"""
One workspace per uid, and nothing that can reach across.

Records used to live in two module-level dicts, which was fine while exactly one
person could ever reach the process. The moment a second person can, a shared
journal is not a design — it is a leak. So the store is keyed by the verified
uid and there is no route, parameter or query that can name a different one:
every read and every write goes through `current()`, which asks `identity` and
nothing else.

The voice slice still refers to its `RECORDS` and `CAPTURES` module globals. It
does not have to change: those names are rebound to the proxies below, which
resolve to the calling request's workspace at the moment of each operation. One
line of indirection buys per-user isolation without touching tested code, and
being resolved per request rather than per process it is safe under threads.

Two backends, one shape:

    file        a journalled JSONL directory per uid. Real isolation, real
                append-only, and it lives on whichever machine is running —
                so it does not survive a Cloud Run revision and does not
                follow anyone to another device.
    firestore   users/{uid}/… . The decided production home. A capture and the
                records it produced are written in one transaction, so a retry
                cannot half-file a note.

🔴 Which one is in use is read off the object that is actually serving requests
(`backend()` / `cross_device()`), never off an environment variable. A variable
can say `firestore` while a file journal quietly serves every request, and the
page would then promise cross-device recovery that does not exist. That failure
has a name in this project — it is the same shape as the model region in V2 —
and the only defence is that the claim and the code have one source.
"""
from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import MutableMapping
from pathlib import Path
from typing import Iterator, Optional

from flask import g

from ledger import (CaptureLog, GardenState, Ledger, ReflectionPreferences,
                    SummaryLog, discard_tombstone, merge_garden_leaf,
                    plan_discard, put_away_garden_leaf, restored_id)

SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BackendUnavailable(RuntimeError):
    """A backend was named and could not be stood up. Never silently downgraded."""


def folder_for(uid: str) -> str:
    """
    A uid becomes a directory name, and never by concatenation.

    Firebase uids are already safe, but a uid arrives from outside this process,
    so anything that is not plainly safe is hashed rather than trusted. There is
    no path here a caller can steer.
    """
    return uid if SAFE.match(uid) else "u_" + hashlib.sha256(uid.encode()).hexdigest()[:32]


# ------------------------------------------------------------- file backend --
class FileWorkspace:
    """One directory per uid. Activity history plus two mutable state journals."""

    backend = "local-file"
    cross_device = False

    def __init__(self, uid: str, root: Optional[Path], suffix: str = ""):
        self.uid = uid
        if root is None:                       # tests: nothing touches disk
            self.path = None
            self.ledger, self.captures = Ledger(None), CaptureLog(None)
            self.summaries, self.garden = SummaryLog(None), GardenState(None)
            self.preferences = ReflectionPreferences(None)
        else:
            self.path = root / folder_for(uid)
            self.ledger = Ledger(self.path / f"records{suffix}.jsonl")
            self.captures = CaptureLog(self.path / f"captures{suffix}.jsonl")
            self.summaries = SummaryLog(self.path / f"summaries{suffix}.jsonl")
            self.garden = GardenState(self.path / f"garden{suffix}.jsonl")
            self.preferences = ReflectionPreferences(
                self.path / f"reflection-preferences{suffix}.jsonl")
        self._lock = threading.Lock()

    def commit(self, capture_id: Optional[str], receipt: Optional[dict],
               records: dict[str, dict]) -> Optional[dict]:
        """
        File a capture and the records it produced, together.

        On this backend "together" is a process lock rather than a database
        transaction — honest, and enough for one container. The Firestore
        adapter does the same thing with a real transaction, and both take the
        capture first so a replay is refused before anything is written twice.

        Returns the **canonical** receipt: on a replay, the one already in the
        store rather than the one this call just built. Two submissions of the
        same recording each mint their own record ids, and giving the loser its
        own receipt back would show the rabbit eating something that was never
        filed.
        """
        with self._lock:
            if capture_id is not None:
                already = self.captures.get(capture_id)
                if already is not None:
                    return already                  # replay: already filed
            for rid, rec in records.items():
                self.ledger[rid] = rec
            if capture_id is not None and receipt is not None:
                self.captures[capture_id] = receipt
            return receipt

    def discard(self, capture_id: str, *, when: str,
                record_ids: tuple[str, ...] = ()) -> dict:
        """
        Cancel one capture, whichever request got here first.

        🔴 The whole point is that the answer does not depend on the ordering.
        Under the same lock the commit path uses: if the id is free the
        tombstone claims it and the late commit will write nothing; if the rows
        are already there they are moved to `cancelled` and anything this
        capture superseded is appended back as active.
        """
        with self._lock:
            existing = self.captures.get(capture_id)
            rows: dict[str, dict | None] = {}
            if existing is not None:
                for rid in [i.get("record_id") for i in (existing.get("items") or [])]:
                    if rid:
                        rows[rid] = self.ledger.get(rid)
                # The records page sends the active rows it is actually
                # showing. This matters after a cancelled correction restored
                # an immutable old receipt under a derived record id.
                for rid in record_ids:
                    rows.setdefault(rid, self.ledger.get(rid))
                for rid in (existing.get("superseded") or []):
                    rows.setdefault(rid, self.ledger.get(rid))
                    # 🔴 And the row that a previous cancel would have written
                    # to put it back. Without this read the second cancel
                    # cannot see the first one's work and appends it again.
                    back = restored_id(capture_id, rid)
                    rows.setdefault(back, self.ledger.get(back))
            outcome, staged = plan_discard(existing, rows, capture_id, when)
            if outcome == "blocked":
                self.captures[capture_id] = discard_tombstone(capture_id, when)
            for rid, rec in staged.items():
                self.ledger[rid] = rec
            return {"outcome": outcome,
                    "cancelled": sorted(r for r, v in staged.items()
                                        if v.get("review_status") == "cancelled"),
                    "restored": sorted(r for r, v in staged.items()
                                       if v.get("restores"))}

    def commit_reflection(self, summary_id: str, summary: dict,
                          garden_state: dict,
                          preferences_state: Optional[dict] = None,
                          expected_preferences: Optional[dict] = None) -> bool:
        """
        The scheduled summary, its leaf, and its next boundary, together.

        Written separately, a failure between the two left the summary stored
        and the meadow empty — and every later run exited early because the
        summary already existed, so that leaf could never appear.

        The preference comparison prevents a late task from restoring a stale
        cadence after the person changed it while the model was writing.
        """
        with self._lock:
            if expected_preferences is not None \
                    and self.preferences.read() != expected_preferences:
                return False
            self.summaries[summary_id] = summary
            self.garden.write(merge_garden_leaf(self.garden.read(), garden_state))
            if preferences_state is not None:
                self.preferences.write(preferences_state)
            return True

    def put_away_leaf(self, summary_id: str, when: str) -> dict:
        """Put away exactly one visible leaf under the workspace lock."""
        with self._lock:
            state = put_away_garden_leaf(self.garden.read(), summary_id, when)
            self.garden.write(state)
            return state

    def advance_reflection(self, expected: dict, next_state: dict) -> bool:
        """Advance a silent check without overwriting a newer user choice."""
        with self._lock:
            if self.preferences.read() != expected:
                return False
            self.preferences.write(next_state)
            return True


class FileRegistry:
    """Lazily opened workspaces, one per uid, for the life of the process."""

    backend = "local-file"
    cross_device = False

    def __init__(self, root: Optional[Path], suffix: str = ""):
        self._root = root
        self._suffix = suffix
        self._open: dict[str, FileWorkspace] = {}
        self._lock = threading.Lock()

    def get(self, uid: str) -> FileWorkspace:
        with self._lock:
            ws = self._open.get(uid)
            if ws is None:
                ws = FileWorkspace(uid, self._root, self._suffix)
                self._open[uid] = ws
            return ws

    def count(self) -> int:
        return len(self._open)

    def all_uids(self) -> list[str]:
        """
        Every workspace this deployment knows about.

        Only the scheduled task uses this, and it never returns it to anybody —
        a list of uids is not something a request can ask for.
        """
        known = set(self._open)
        if self._root and Path(self._root).is_dir():
            known |= {p.name for p in Path(self._root).iterdir() if p.is_dir()}
        return sorted(known)


# --------------------------------------------------------- backend selection --
Registry = FileRegistry            # kept: the file registry is still the default

REGISTRY = None                    # type: ignore[assignment]


def configure(root: Optional[Path], suffix: str = "", *, backend: str = "file",
              client=None):
    """
    Stand up the store this process will actually serve from.

    Fail closed: naming `firestore` and not getting one raises here, at startup,
    rather than leaving a file journal serving requests behind a page that says
    Firestore. There is no downgrade path.
    """
    global REGISTRY
    if backend == "firestore":
        from firestore_store import FirestoreRegistry     # local import: optional dep
        REGISTRY = FirestoreRegistry(client=client, suffix=suffix)
    elif backend == "file":
        REGISTRY = FileRegistry(root, suffix)
    else:
        raise BackendUnavailable(f"unknown storage backend {backend!r}")
    return REGISTRY


def backend() -> str:
    """The name of the store that is serving requests. Read off the instance."""
    return getattr(REGISTRY, "backend", "unconfigured")


def cross_device() -> bool:
    """
    Whether records actually follow a person to another device.

    True only when the live store says so. Nothing else in the product is
    allowed to answer this question.
    """
    return bool(getattr(REGISTRY, "cross_device", False))


def current():
    """The calling request's workspace. Resolved from the verified uid only."""
    if not hasattr(g, "_workspace"):
        import identity
        g._workspace = REGISTRY.get(identity.current().uid)
    return g._workspace


def for_uid(uid: str):
    """
    A workspace by uid, for the scheduled task only.

    Every request path goes through `current()`. This exists because scheduled
    reflections run with no request and no person attached, and it is reachable
    only from the task endpoint, which no user token can pass.
    """
    return REGISTRY.get(uid)


def commit(capture_id: Optional[str], receipt: Optional[dict],
           records: dict[str, dict]) -> Optional[dict]:
    """The slice's write path, resolved to the calling person's own store."""
    return current().commit(capture_id, receipt, records)


def discard(capture_id: str, *, when: str,
            record_ids: tuple[str, ...] = ()) -> dict:
    """The slice's cancel path, resolved to the calling person's own store.

    🔴 There is no uid parameter and there must never be one: a capture id is
    only ever cancellable inside the workspace it belongs to, so another
    person's id resolves to nothing here rather than to their records.
    """
    return current().discard(capture_id, when=when, record_ids=record_ids)


class _Proxy(MutableMapping):
    """Forwards to the calling request's store. Holds no state of its own."""

    def __init__(self, attr: str):
        self._attr = attr

    def _target(self):
        return getattr(current(), self._attr)

    def __getitem__(self, k):        return self._target()[k]
    def __setitem__(self, k, v):     self._target()[k] = v
    def __delitem__(self, k):        del self._target()[k]
    def __iter__(self) -> Iterator:  return iter(self._target())
    def __len__(self) -> int:        return len(self._target())


RECORDS = _Proxy("ledger")
CAPTURES = _Proxy("captures")
