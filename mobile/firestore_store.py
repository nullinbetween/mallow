"""
Firestore, behind the same shape as the file journal.

    users/{uid}
    users/{uid}/records/{record_id}
    users/{uid}/captures/{capture_id}
    users/{uid}/summaries/{summary_id}
    users/{uid}/garden/state
    users/{uid}/settings/reflection

Three things this module exists to get right.

**The rules do not move with the backend.** `guard_record`, `guard_capture` and
`guard_summary` live in `ledger.py` and are called from here. A record may move
its status to cancelled or superseded and may not otherwise change, on either
store, because there is one copy of that sentence in the codebase.

**A capture and its records are one write.** `commit()` runs inside a Firestore
transaction: the capture id is read first, and if it is already there nothing at
all is written. A retried upload, a double-tapped button and a Cloud Run retry
all land on the same branch, so the rabbit cannot be fed twice for one sentence.

**It fails closed.** Standing this up is a cloud resource, and the deployment
that names `firestore` and does not get one must stop rather than serve a file
journal behind a page that promises cross-device recovery.

🔴 Honest status: the adapter is written and is exercised by tests against an
in-memory double that implements the same client protocol. The double does not
simulate Firestore contention or automatic callback retries. Those behaviours,
and serialisation of unusual types, require verification against the real
service.
"""
from __future__ import annotations

import threading
from collections.abc import MutableMapping
from typing import Any, Callable, Iterator, Optional

from ledger import (discard_tombstone, guard_capture, guard_record, guard_summary,
                    plan_discard, restored_id,
                    merge_garden_leaf, put_away_garden_leaf)

USERS = "users"
WORKSPACE_MARKER = {"kind": "mallow-workspace", "schema_version": 1}


# --------------------------------------------------------------- the client --
# A four-method seam. The real adapter wraps google-cloud-firestore; the test
# double implements the same four methods in memory. The store above them does
# not know which one it has, which is the point: the transaction logic under
# test is the transaction logic that ships.
class FirestoreUnavailable(RuntimeError):
    """Firestore was named and could not be reached. Never downgraded."""


class ReadAfterWrite(RuntimeError):
    """A transaction read a document after it had already written one.

    Real Firestore rejects this. It is raised by the in-memory double so the
    same code path fails locally instead of only in production."""


class _Txn:
    """The double's transaction handle. Holds the buffered writes."""

    def __init__(self):
        self.writes: list[tuple[str, dict]] = []


class GoogleFirestore:
    """The real one. Thin on purpose — everything interesting is above it."""

    def __init__(self, project: Optional[str] = None, database: Optional[str] = None):
        try:
            from google.cloud import firestore
        except ImportError as e:                                  # noqa: BLE001
            raise FirestoreUnavailable(
                "google-cloud-firestore is not installed") from e
        self._firestore = firestore
        kwargs: dict[str, Any] = {}
        if project:
            kwargs["project"] = project
        if database:
            kwargs["database"] = database
        try:
            self._db = firestore.Client(**kwargs)
        except Exception as e:                                    # noqa: BLE001
            raise FirestoreUnavailable(
                f"could not open Firestore: {type(e).__name__}") from e

    def _ref(self, path: str):
        parts = path.split("/")
        ref = self._db.collection(parts[0])
        for i, part in enumerate(parts[1:], start=1):
            ref = ref.document(part) if i % 2 else ref.collection(part)
        return ref

    def get(self, path: str, txn=None) -> Optional[dict]:
        snap = self._ref(path).get(transaction=txn)
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, value: dict, txn=None) -> None:
        ref = self._ref(path)
        if txn is not None:
            txn.set(ref, value)
        else:
            ref.set(value)

    def list_ids(self, collection_path: str) -> list[str]:
        return sorted(d.id for d in self._ref(collection_path).list_documents())

    def documents(self, collection_path: str) -> list[dict]:
        return [s.to_dict() for s in self._ref(collection_path).stream()]

    def atomic(self, fn: Callable[[Any], Any]) -> Any:
        """Run `fn(txn)` in one Firestore transaction."""
        transactional = self._firestore.transactional

        @transactional
        def _run(txn):
            return fn(txn)

        return _run(self._db.transaction())


class InMemoryFirestore:
    """
    The double the tests run against.

    Not a mock — it stores documents and applies a transaction's buffered
    writes together. It deliberately does not claim to simulate Firestore's
    automatic callback retries or contention behaviour.
    """

    def __init__(self):
        self._docs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self.transactions = 0

    def get(self, path: str, txn=None) -> Optional[dict]:
        # 🔴 Firestore forbids a read after the first write inside the same
        # transaction. The earlier version of this double did not, so a commit
        # that interleaved reads and writes passed here and would have raised
        # against the real service — a green test for code that could not run.
        # A test double that is more permissive than the thing it stands in for
        # is not a test double, it is a second implementation of the bug.
        if txn is not None and txn.writes:
            raise ReadAfterWrite(
                "Firestore transactions require all reads before any write; "
                f"tried to read {path!r} after {len(txn.writes)} buffered write(s)")
        with self._lock:
            doc = self._docs.get(path)
            return dict(doc) if doc is not None else None

    def set(self, path: str, value: dict, txn=None) -> None:
        with self._lock:
            if txn is not None:
                txn.writes.append((path, dict(value)))
            else:
                self._docs[path] = dict(value)

    def list_ids(self, collection_path: str) -> list[str]:
        prefix = collection_path.rstrip("/") + "/"
        with self._lock:
            # Firestore queries/list snapshots do not surface a non-existent
            # parent merely because it owns a subcollection. Only real direct
            # documents in this collection are listable.
            return sorted(k[len(prefix):] for k in self._docs
                          if k.startswith(prefix)
                          and "/" not in k[len(prefix):])

    def documents(self, collection_path: str) -> list[dict]:
        prefix = collection_path.rstrip("/") + "/"
        with self._lock:
            return [dict(v) for k, v in self._docs.items()
                    if k.startswith(prefix) and "/" not in k[len(prefix):]]

    def atomic(self, fn):
        """Buffer the writes, apply them together, or apply none of them."""
        self.transactions += 1
        txn = _Txn()
        result = fn(txn)
        with self._lock:
            for path, value in txn.writes:
                self._docs[path] = value
        return result


# ---------------------------------------------------------------- documents --
class _Collection(MutableMapping):
    """
    One Firestore subcollection, read through on every access.

    Nothing is cached between requests. A cache would be a second copy of the
    truth in a process that may be one of several, and stale isolation is not
    isolation.
    """

    def __init__(self, client, base: str, guard):
        self._c = client
        self._base = base
        self._guard = guard

    def _path(self, key: str) -> str:
        return f"{self._base}/{key}"

    def __getitem__(self, key: str) -> dict:
        doc = self._c.get(self._path(key))
        if doc is None:
            raise KeyError(key)
        return doc

    def __setitem__(self, key: str, value: dict) -> None:
        path, guard = self._path(key), self._guard

        def write(txn):
            old = self._c.get(path, txn=txn)
            if old is not None:
                guard(old, value)
            self._c.set(path, dict(value), txn=txn)

        self._c.atomic(write)

    def __delitem__(self, key: str) -> None:
        from ledger import HistoryRewrite
        raise HistoryRewrite("records are never deleted")

    def __iter__(self) -> Iterator[str]:
        return iter(self._c.list_ids(self._base))

    def __len__(self) -> int:
        return len(self._c.list_ids(self._base))

    def values(self) -> list[dict]:                     # type: ignore[override]
        return self._c.documents(self._base)


class _Records(_Collection):
    def active(self) -> list[dict]:
        return [r for r in self.values()
                if r.get("review_status") in ("active", "unclassified")]

    def ordered(self) -> list[dict]:
        return sorted(self.values(), key=lambda r: r.get("recorded_at", ""))


class _Summaries(_Collection):
    def ordered(self) -> list[dict]:
        return sorted(self.values(), key=lambda s: s.get("created_at", ""))

    def latest(self) -> Optional[dict]:
        rows = self.ordered()
        return rows[-1] if rows else None


class _Garden:
    """`users/{uid}/garden/state`. A pointer, not a history."""

    def __init__(self, client, base: str):
        self._c = client
        self._path = f"{base}/garden/state"

    def read(self) -> dict:
        return self._c.get(self._path) or {}

    def write(self, value: dict) -> None:
        self._c.set(self._path, dict(value))


class _Preferences(_Garden):
    """`users/{uid}/settings/reflection`, separate from garden state."""

    def __init__(self, client, base: str):
        self._c = client
        self._path = f"{base}/settings/reflection"


# --------------------------------------------------------------- workspaces --
class FirestoreWorkspace:
    backend = "firestore"
    cross_device = True

    def __init__(self, uid: str, client):
        self.uid = uid
        self._c = client
        self.path = None
        base = f"{USERS}/{uid}"
        self._base = base
        self.ledger = _Records(client, f"{base}/records", guard_record)
        self.captures = _Collection(client, f"{base}/captures", guard_capture)
        self.summaries = _Summaries(client, f"{base}/summaries", guard_summary)
        self.garden = _Garden(client, base)
        self.preferences = _Preferences(client, base)
        # A subcollection document does not create its parent document in
        # Firestore, and non-existent parents do not appear in queries or
        # snapshots. The global Scheduler enumerates `users`, so this small,
        # content-free manifest is what makes a workspace discoverable.
        if client.get(base) is None:
            client.set(base, WORKSPACE_MARKER)

    def commit(self, capture_id: Optional[str], receipt: Optional[dict],
               records: dict[str, dict]) -> Optional[dict]:
        """
        One transaction: the capture is claimed, or nothing happens.

        Returns the **canonical** receipt — the one that is actually in the
        store. On a replay that is the receipt written first, not the one this
        request just built: two concurrent submissions of the same recording
        each construct their own record ids, and handing the loser its own
        receipt back would show the rabbit eating something that was never
        filed.
        """
        base = self._base

        def write(txn):
            # ── every read, first ───────────────────────────────────────────
            # 🔴 Not a style preference. Firestore rejects a read that happens
            # after the first write in the same transaction, and the previous
            # version of this method interleaved them inside a loop: read A,
            # write A, read B, write B. It passed the tests because the double
            # allowed it, and it would have raised the first time it met the
            # real service.
            existing_capture = (self._c.get(f"{base}/captures/{capture_id}", txn=txn)
                                if capture_id is not None else None)
            if existing_capture is not None:
                return existing_capture             # replay: nothing is written

            olds = {rid: self._c.get(f"{base}/records/{rid}", txn=txn)
                    for rid in records}

            # ── then validate, still having written nothing ─────────────────
            for rid, rec in records.items():
                if olds[rid] is not None:
                    guard_record(olds[rid], rec)

            # ── then every write ────────────────────────────────────────────
            for rid, rec in records.items():
                self._c.set(f"{base}/records/{rid}", dict(rec), txn=txn)
            if capture_id is not None and receipt is not None:
                self._c.set(f"{base}/captures/{capture_id}", dict(receipt), txn=txn)
            return receipt

        return self._c.atomic(write)


    def commit_reflection(self, summary_id: str, summary: dict,
                          garden_state: dict,
                          preferences_state: Optional[dict] = None,
                          expected_preferences: Optional[dict] = None) -> bool:
        """
        The scheduled summary, its leaf, and its next boundary, together.

        Written separately, a failure between them left the summary stored and
        the meadow empty — and the next run would exit early because the
        summary already existed, so the leaf would never appear at all.
        """
        base = self._base

        def write(txn):
            existing = self._c.get(f"{base}/summaries/{summary_id}", txn=txn)
            current_garden = self._c.get(f"{base}/garden/state", txn=txn) or {}
            current_preferences = (
                self._c.get(f"{base}/settings/reflection", txn=txn)
                if expected_preferences is not None else None)
            if expected_preferences is not None \
                    and current_preferences != expected_preferences:
                return False
            if existing is not None and existing != summary:
                guard_summary(existing, summary)
            self._c.set(f"{base}/summaries/{summary_id}", dict(summary), txn=txn)
            self._c.set(f"{base}/garden/state",
                        merge_garden_leaf(current_garden, garden_state), txn=txn)
            if preferences_state is not None:
                self._c.set(f"{base}/settings/reflection",
                            dict(preferences_state), txn=txn)
            return True

        return self._c.atomic(write)

    def discard(self, capture_id: str, *, when: str,
                record_ids: tuple[str, ...] = ()) -> dict:
        """
        Cancel one capture inside one transaction, whichever request won.

        🔴 Every read happens before the first write, and that is not tidiness:
        Firestore rejects a read that follows a write in the same transaction,
        and the record ids to read are only knowable from the capture receipt,
        so the receipt is read first and the rows in one batch after it.

        The decision itself is `ledger.plan_discard`, which the file backend
        calls too. A backend swap must not be able to change what cancelling
        means, and the only way to be sure is for there to be one copy of it.
        """
        base = self._base

        def write(txn):
            # ── every read, first ──────────────────────────────────────────
            existing = self._c.get(f"{base}/captures/{capture_id}", txn=txn)
            rows: dict = {}
            if existing is not None:
                wanted = [i.get("record_id") for i in (existing.get("items") or [])]
                # A restored row has a derived id that the immutable original
                # receipt cannot name. The records page supplies the ids it is
                # visibly grouping under this capture; `plan_discard` still
                # verifies the stored row's capture_id before cancelling it.
                wanted += list(record_ids)
                retired = list(existing.get("superseded") or [])
                wanted += retired
                # 🔴 And the rows a previous cancel would have written to put
                # those back: a retry that cannot see them appends them twice.
                wanted += [restored_id(capture_id, rid) for rid in retired]
                for rid in wanted:
                    if rid and rid not in rows:
                        rows[rid] = self._c.get(f"{base}/records/{rid}", txn=txn)

            outcome, staged = plan_discard(existing, rows, capture_id, when)

            # ── then every write ───────────────────────────────────────────
            if outcome == "blocked":
                self._c.set(f"{base}/captures/{capture_id}",
                            discard_tombstone(capture_id, when), txn=txn)
            for rid, rec in staged.items():
                self._c.set(f"{base}/records/{rid}", dict(rec), txn=txn)
            return {"outcome": outcome,
                    "cancelled": sorted(r for r, v in staged.items()
                                        if v.get("review_status") == "cancelled"),
                    "restored": sorted(r for r, v in staged.items()
                                       if v.get("restores"))}

        return self._c.atomic(write)

    def put_away_leaf(self, summary_id: str, when: str) -> dict:
        """Remove one visible leaf without deleting its summary."""
        path = f"{self._base}/garden/state"

        def write(txn):
            current = self._c.get(path, txn=txn) or {}
            state = put_away_garden_leaf(current, summary_id, when)
            self._c.set(path, state, txn=txn)
            return state

        return self._c.atomic(write)

    def advance_reflection(self, expected: dict, next_state: dict) -> bool:
        """Advance a silent check only if its schedule is still current."""
        path = f"{self._base}/settings/reflection"

        def write(txn):
            if self._c.get(path, txn=txn) != expected:
                return False
            self._c.set(path, dict(next_state), txn=txn)
            return True

        return self._c.atomic(write)


class FirestoreRegistry:
    backend = "firestore"
    cross_device = True

    def __init__(self, client=None, suffix: str = ""):
        # `suffix` is the demo separation the file store gets from a filename.
        # On Firestore it would have to be a different collection root, and a
        # demo deployment writing into the production database is not something
        # to solve with a naming convention — so it is refused outright.
        if suffix:
            raise FirestoreUnavailable(
                "demo mode does not run against Firestore; use the file store")
        self._c = client or GoogleFirestore()
        self._open: dict[str, FirestoreWorkspace] = {}
        self._lock = threading.Lock()

    def get(self, uid: str) -> FirestoreWorkspace:
        with self._lock:
            ws = self._open.get(uid)
            if ws is None:
                ws = FirestoreWorkspace(uid, self._c)
                self._open[uid] = ws
            return ws

    def count(self) -> int:
        return len(self._open)

    def all_uids(self) -> list[str]:
        return self._c.list_ids(USERS)
