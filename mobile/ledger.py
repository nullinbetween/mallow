"""
The one store.

There were two: this project's earlier `core/ledger.py`, written for a scoring
model that has since been retired (mli, tier, coins, replacement cost), and the
plain dict the voice slice kept in memory. Keeping both would mean keeping two
different ideas of what a record is, so the scoring ledger is retired and this
is the store the product actually runs on.

Two rules, enforced here rather than by convention:

  1. History is append-only. Every write is appended to a journal on disk before
     it is visible, and the journal is never rewritten. A correction is a new
     row naming the one it supersedes.
  2. A row may change in exactly one way: its `review_status` may move to
     `cancelled` or `superseded`. Any other edit raises. That keeps the
     correction flow the voice slice already implements — which marks the old
     row and appends a superseding one — without letting anything quietly
     rewrite what a person said.

Replay safety lives next door in `CaptureLog`: the same capture_id can be
submitted any number of times and is only ever filed once.

The claim this supports, exactly, and no stronger:

    Append-only by application policy, with traceable corrections.

Not immutable. Not tamper-proof. A person with the disk can edit the file; what
they cannot do is get this application to rewrite a row for them.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Iterator, Optional

TERMINAL = ("cancelled", "superseded")
MAX_VISIBLE_LEAVES = 5


def visible_leaves(state: dict) -> list[dict]:
    """Return the visible leaf queue, including a safe legacy migration.

    Older deployments stored one `leaf` plus `seen_at`.  An unseen legacy leaf
    becomes the first queue item; a leaf already opened under the old contract
    stays put away.  The immutable summary remains in `summaries` either way.
    """
    raw = state.get("leaves")
    if isinstance(raw, list):
        leaves = [dict(item) for item in raw if isinstance(item, dict)
                  and item.get("summary_id")]
    else:
        legacy = state.get("leaf")
        leaves = ([dict(legacy)] if isinstance(legacy, dict)
                  and legacy.get("summary_id") and not state.get("seen_at") else [])
    return leaves[-MAX_VISIBLE_LEAVES:]


def merge_garden_leaf(current: dict, proposed: dict) -> dict:
    """Atomically add the proposed leaf without erasing existing tokens."""
    leaves = visible_leaves(current)
    incoming = proposed.get("leaf")
    if isinstance(incoming, dict) and incoming.get("summary_id"):
        sid = incoming["summary_id"]
        leaves = [leaf for leaf in leaves if leaf.get("summary_id") != sid]
        leaves.append(dict(incoming))
    leaves = leaves[-MAX_VISIBLE_LEAVES:]
    return {
        **current,
        **{k: v for k, v in proposed.items() if k not in ("leaf", "leaves", "seen_at")},
        "leaves": leaves,
        # Compatibility for older clients and operational inspection.  New UI
        # reads `leaves`; this pointer is never the source of the collection.
        "leaf": dict(leaves[-1]) if leaves else None,
        "seen_at": None,
    }


def put_away_garden_leaf(current: dict, summary_id: str, when: str) -> dict:
    """Remove one meadow token while preserving the immutable reflection."""
    leaves = [leaf for leaf in visible_leaves(current)
              if leaf.get("summary_id") != summary_id]
    return {
        **current,
        "leaves": leaves,
        "leaf": dict(leaves[-1]) if leaves else None,
        "seen_at": when,
        "last_put_away_summary_id": summary_id,
    }


class HistoryRewrite(RuntimeError):
    """An attempt to change a written row in a way that is not a status move."""


# --------------------------------------------------------------- the rules --
# The two guards live here, as functions, because they are the rules and not the
# storage. The file journal below and the Firestore adapter next door both call
# these: a backend swap must not be able to change what a record is allowed to
# do, and the only way to be sure of that is for there to be one copy.
def guard_record(old: dict, new: dict) -> None:
    """A written record may move its status to cancelled/superseded. Nothing else."""
    was, now = old.get("review_status"), new.get("review_status")
    if now not in TERMINAL:
        raise HistoryRewrite(
            f"review_status may only move to {TERMINAL}, not {now!r}")
    if was == now:
        # 🔴 This branch used to `return` unconditionally, which was a hole in
        # the middle of the append-only claim: a row already sitting at
        # `cancelled` could be written again with the same status and a
        # different `source_text`, and the guard waved it through. A person's
        # own words were editable, as long as you edited them twice.
        #
        # Writing the same terminal status again has exactly one legitimate
        # cause — a replay of the identical write (a retry, a redelivered
        # task). So that is the only thing allowed: byte-for-byte the same
        # document. Any content drift is an edit wearing a status change's
        # clothes.
        if old != new:
            drifted = sorted({k for k in set(old) | set(new)
                              if old.get(k) != new.get(k)})
            raise HistoryRewrite(
                f"a record already at {now!r} may only be rewritten identically; "
                f"these changed: {drifted}")
        return
    # 🔴 The allow-list is what a status change is permitted to say about
    # itself: when it happened, and — for a replacement — what replaced it.
    # Everything the person said stays out of it, which is the whole point of
    # the guard. Widened on 2026-08-24 for `superseded_at` / `superseded_by`,
    # because a row that goes terminal because something replaced it should be
    # able to name what did; the alternative was a status change with no
    # traceable cause, which is worse than a slightly longer list.
    STAMPS = ("review_status", "cancelled_at", "superseded_at", "superseded_by")
    drift = {k for k in set(old) | set(new)
             if k not in STAMPS and old.get(k) != new.get(k)}
    if drift:
        raise HistoryRewrite(f"a status change may not also edit {sorted(drift)}")


# ------------------------------------------------------- discarding a capture --
# 🔴 The rule for "I pressed cancel while it was thinking", written once and
# called by both backends, for the same reason the two guards above are.
#
# There are two orderings and they need different answers, which is the whole
# difficulty. The first design in this project was first-writer-wins, and the
# strategist was right to refuse it: the person's meaning is "this one does not
# count", not "cancel is in a race with the network and may lose".
#
#   discard arrives first   the capture id is free, so the tombstone takes it.
#                           The late `/voice` commit then hits the ordinary
#                           replay branch and writes nothing at all. Cheapest
#                           and cleanest: nothing is ever created.
#
#   commit arrives first    the rows exist. They cannot be deleted - this is an
#                           append-only ledger and the guard above enforces it -
#                           so they are moved to `cancelled`, which every
#                           product read already excludes: the rollup, the
#                           reflection fact pack, the records page. The row
#                           stays in history, labelled; it stops being one of
#                           this person's activities.
#
# 🔴 And the case that is easy to miss: a capture that REPLACED something. If a
# correction is cancelled after it landed, the rows it superseded would be left
# retired with nothing active in their place - the person would lose the
# original work by cancelling its replacement. Going back to `active` is
# forbidden and should be, so the original content is appended again as a new
# active row naming what it restores. History gains a row; the person gets
# their record back.
DISCARDED = "discarded"


def restored_id(capture_id: str, old_record_id: str) -> str:
    """
    The id a restored row will have. Derived, never random.

    🔴 This is what makes cancelling twice safe. A random id would append a
    second copy of the same restored afternoon on the second call — the person
    would end up with the work counted twice — and neither the guard nor the
    status filter would notice, because both rows are perfectly legitimate
    `active` records. Deriving the id means the second call can look for it,
    find it, and do nothing.

    It is also what makes a Firestore transaction retry safe: the callback runs
    again from fresh reads and produces byte-identical intentions.
    """
    seed = f"{capture_id}:{old_record_id}".encode()
    return "rs" + hashlib.sha256(seed).hexdigest()[:14]


def discard_tombstone(capture_id: str, when: str) -> dict:
    """
    What is written when cancel wins the race. It carries no content.

    It is shaped like a receipt because it occupies a receipt's place: the
    capture log is the replay guard, and this is the thing a late commit finds
    there and stops on.
    """
    return {"capture_id": capture_id, "state": DISCARDED, "heard": "", "items": [],
            "withheld_fragments": 0, "audio_persisted": False,
            "note": "Discarded by the person before it was filed.",
            "discarded_at": when, "at": when}


def plan_discard(existing: Optional[dict], rows: dict[str, Optional[dict]],
                 capture_id: str, when: str) -> tuple[str, dict]:
    """
    Given what is already in the store, decide what the discard must write.

    Pure: it reads nothing and writes nothing, so both backends can call it
    after their own reads and before their own writes — which is not a style
    preference on Firestore, where a read after the first write in a
    transaction is an error.

    Returns `(outcome, staged)`.
    """
    if existing is None:
        return "blocked", {}                     # caller writes the tombstone
    if existing.get("state") == DISCARDED:
        return "already_discarded", {}

    # 🔴 Only the rows THIS capture created, named by its own receipt.
    #
    # The first version took every row the caller had read and cancelled any of
    # them that were active. That read set also contains the rows a previous
    # cancel restored — so cancelling twice cancelled the restoration, and the
    # person's original work disappeared on the second press. Two of the tests
    # below exist because of exactly that.
    receipt_ids = {i.get("record_id") for i in (existing.get("items") or [])
                   if i.get("record_id")}
    # A cancelled correction restores the old content as a new active row. Its
    # id cannot be added to the old capture receipt — receipts are immutable —
    # so a later withdrawal from the records page supplies that visible id.
    # It is accepted only when the stored row itself names this capture. An id
    # from another capture in the same workspace therefore cannot be smuggled
    # into a discard request.
    mine = {rid for rid, rec in rows.items()
            if rid in receipt_ids
            or (rec is not None and rec.get("capture_id") == capture_id)}
    live = {rid: rows[rid] for rid in mine
            if rows.get(rid) is not None
            and rows[rid].get("review_status") in ("active", "unclassified")}
    staged: dict[str, dict] = {}
    for rid, rec in live.items():
        staged[rid] = {**rec, "review_status": "cancelled", "cancelled_at": when}

    # Anything this capture retired goes back, as a new row that says so.
    for old_rid, old in rows.items():
        if old is None or old_rid in live:
            continue
        if old.get("review_status") != "superseded":
            continue
        if old.get("superseded_by") != capture_id:
            continue
        rid = restored_id(capture_id, old_rid)
        # 🔴 Already put back. A second cancel of the same capture — a retried
        # request, a double tap, a Firestore transaction re-running its
        # callback — must not append a second copy of the same afternoon.
        if rows.get(rid) is not None:
            continue
        staged[rid] = {**old, "record_id": rid, "recorded_at": when,
                       "review_status": "active", "restores": old_rid,
                       "restored_at": when}
        staged[rid].pop("superseded_at", None)
        staged[rid].pop("superseded_by", None)

    if not staged:
        return "nothing_to_cancel", {}
    return "compensated", staged


def guard_capture(old: dict, new: dict) -> None:
    """A capture receipt is written once. A replay returns the first one."""
    if old != new:
        raise HistoryRewrite("a capture receipt is written once")


def guard_summary(old: dict, new: dict) -> None:
    """
    A scheduled reflection is written once.

    It is derived from records that cannot change, so rewriting one would mean
    the same period had two different accounts of itself.
    """
    if old != new:
        raise HistoryRewrite("a summary is written once")


class _Journalled(MutableMapping):
    """A dict whose every write is appended to a JSONL journal first."""

    def __init__(self, path: Optional[Path], kind: str):
        self._rows: dict[str, dict[str, Any]] = {}
        self._path = Path(path) if path else None
        self._kind = kind
        self._lock = threading.Lock()
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._replay()

    # ---------------------------------------------------------------- disk --
    def _replay(self) -> None:
        if not self._path or not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue          # a torn final write is skipped, never repaired
                self._rows[row["_key"]] = row["_value"]

    def _append(self, key: str, value: dict) -> None:
        if not self._path:
            return
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"_kind": self._kind, "_key": key, "_value": value},
                                ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ------------------------------------------------------------- mapping --
    def __getitem__(self, key: str) -> dict:
        return self._rows[key]

    def __iter__(self) -> Iterator[str]:
        return iter(dict(self._rows))

    def __len__(self) -> int:
        return len(self._rows)

    def __delitem__(self, key: str) -> None:
        raise HistoryRewrite("records are never deleted")

    def __setitem__(self, key: str, value: dict) -> None:
        with self._lock:
            old = self._rows.get(key)
            if old is not None:
                self._guard(old, value)
            self._append(key, value)
            self._rows[key] = value

    def _guard(self, old: dict, new: dict) -> None:      # overridden below
        raise HistoryRewrite("this store does not accept updates")


class Ledger(_Journalled):
    """Records. Append, or move a status to cancelled/superseded. Nothing else."""

    def __init__(self, path: Optional[Path] = None):
        super().__init__(path, "record")

    def _guard(self, old: dict, new: dict) -> None:
        guard_record(old, new)

    # ------------------------------------------------------------- reading --
    def active(self) -> list[dict]:
        return [r for r in self._rows.values()
                if r.get("review_status") in ("active", "unclassified")]

    def ordered(self) -> list[dict]:
        return sorted(self._rows.values(), key=lambda r: r.get("recorded_at", ""))


class CaptureLog(_Journalled):
    """
    Receipts, keyed by capture_id. This is the replay guard.

    A capture is written once. A second submission of the same id returns the
    first receipt rather than filing anything again, so a retried upload or a
    double-tapped button cannot feed the rabbit twice.
    """

    def __init__(self, path: Optional[Path] = None):
        super().__init__(path, "capture")

    def _guard(self, old: dict, new: dict) -> None:
        guard_capture(old, new)


class SummaryLog(_Journalled):
    """
    Scheduled reflections, keyed by summary_id.

    Written once, like a capture, and for the same reason: a summary rests on
    records that cannot change, so it does not get to change either.
    """

    def __init__(self, path: Optional[Path] = None):
        super().__init__(path, "summary")

    def _guard(self, old: dict, new: dict) -> None:
        guard_summary(old, new)

    def ordered(self) -> list[dict]:
        return sorted(self._rows.values(), key=lambda s: s.get("created_at", ""))

    def latest(self) -> Optional[dict]:
        rows = self.ordered()
        return rows[-1] if rows else None


class GardenState(_Journalled):
    """
    One document per person: up to five leaves currently resting in the meadow.

    This is the one thing in the store that is state rather than history — a
    small queue of pointers to immutable summaries. Every change
    is still journalled, so the file keeps the whole sequence; only the current
    value is read back. It holds no counts and no scores: the leaf is a title
    and a few sentences, and the meadow shows it or does not.
    """

    KEY = "state"

    def __init__(self, path: Optional[Path] = None):
        super().__init__(path, "garden")

    def _guard(self, old: dict, new: dict) -> None:
        return                    # derived state, journalled; last write wins

    def read(self) -> dict:
        return dict(self._rows.get(self.KEY) or {})

    def write(self, value: dict) -> None:
        self[self.KEY] = dict(value)


class ReflectionPreferences(GardenState):
    """The person's current reflection schedule, kept apart from the leaf.

    Preferences are mutable state, not an activity record.  Keeping them in a
    separate document prevents a settings save and a scheduled leaf write from
    replacing one another's fields.
    """

    def __init__(self, path: Optional[Path] = None):
        _Journalled.__init__(self, path, "reflection-preferences")
