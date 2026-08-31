"""
Voice spike.

Hold the rabbit, say what happened, release.

Scope limits, deliberate:
  - no Firestore, no scheduled reflection, no deployment
  - records live in memory for the length of the process
  - raw audio is processed in memory and is not persisted by Mallow:
    no object store, no database, no temporary file, no request-body log,
    no exception-body log. `audio_persisted: false` is derived by this server,
    never taken from the model.

Run:  python3 -m gunicorn --bind 127.0.0.1:8090 app:app
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contract import CandidateRejected, build_record, validate      # noqa: E402
from gemini import (MODEL, PROMPT_VERSION, AudioUnreadable,         # noqa: E402
                    ModelMisconfigured, ModelUnavailable,
                    understand, understand_text)
from policy import decide                                            # noqa: E402

JST = timezone(timedelta(hours=9))
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_NOTE_CHARS = 2000         # tree-hole capture; still bounded server-side

# 🔴 A floor, and an honest one about what it is.
#
# The browser decides whether a recording carried any sound: it has the samples
# and this does not. What the server can tell is that a payload is too small to
# be a sentence in any container, and that costs one comparison and saves a
# model call on an accidental tap. It is NOT silence detection and must never
# be described as one — a five-second recording of a quiet room is far bigger
# than this and passes.
#
# It exists because the browser gate is the browser's. A request that reaches
# here having skipped it should still not be able to spend a model call on a
# blip.
MIN_AUDIO_BYTES = 1200
GEMINI_ATTEMPTS = 2           # one retry, then the text fallback

app = Flask(__name__)

RECORDS: dict[str, dict] = {}          # record_id -> record
CAPTURES: dict[str, dict] = {}         # capture_id -> receipt (replay guard)


def commit(capture_id, receipt, records: dict[str, dict]):
    """
    Write a capture and the records it produced, and return what is stored.

    The default is what the spike has always done: set the rows, then the
    receipt. The product rebinds this name to a store that does the same thing
    inside one transaction, so a retried upload cannot file half a note. The
    slice keeps working standalone either way — this is one seam, not a
    dependency on the product.

    🔴 The return value is the canonical receipt. A concurrent replay must get
    back the receipt that was actually filed, not the one it built and lost the
    race with — otherwise the screen shows record ids that are in no store.
    """
    if capture_id is not None:
        already = CAPTURES.get(capture_id)
        if already is not None:
            return already
    for rid, rec in records.items():
        RECORDS[rid] = rec
    if capture_id is not None and receipt is not None:
        CAPTURES[capture_id] = receipt
    return receipt


COMMIT = commit


class NoStoreBound(RuntimeError):
    """Cancelling needs a transactional store, and this slice does not have one."""


def discard_capture(capture_id: str, *, when: str,
                    record_ids: tuple[str, ...] = ()) -> dict:
    """
    The default, and it deliberately refuses.

    🔴 Cancelling has to be atomic to be correct at all: the whole difficulty
    is that a commit and a cancel can arrive in either order, and the answer
    must not depend on which. Two module-level dicts cannot promise that. The
    product rebinds this name to `workspaces.discard`, which decides inside the
    same transaction the commit uses.

    There is no half version of this. A best-effort cancel that usually works
    is exactly the thing that leaves somebody with a record they cancelled.
    """
    raise NoStoreBound("cancelling requires a transactional store")


DISCARD = discard_capture


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _call(fn, *args):
    """
    One retry, then the text box.

    Misconfiguration is not retried - a missing project or an unsupported
    container will fail identically the second time, and making the person wait
    for that is worse than offering them the text box immediately.
    Unreadable audio is not retried either: the model heard it and had nothing.
    Everything else - transient API errors, network faults - gets one more go.
    """
    last = None
    for attempt in range(GEMINI_ATTEMPTS):
        try:
            return fn(*args)
        except (ModelMisconfigured, AudioUnreadable):
            raise
        except Exception as e:                                       # noqa: BLE001
            last = e
            app.logger.warning("model call attempt %d failed: %s",
                               attempt + 1, type(e).__name__)   # type only, never the body
    raise ModelUnavailable(f"model call failed {GEMINI_ATTEMPTS} times: {type(last).__name__}")


def _file(capture_id: str, raw, source: str, replaces: str = ""):
    """
    Validate, decide, record. Shared by the voice and text paths.

    🔴 `replaces` names a capture this one is a correction of, and the whole
    point is *when* the old rows are touched.

    Pressing "say it again" used to cancel the old capture immediately, before
    the replacement existed. If the person then gave up, lost the connection,
    said nothing the model could file, or simply closed the tab, the original
    was already out of the rollup and nothing had taken its place. The QA
    export from 2026-08-24 shows one: a kitchen-counter record marked
    `cancelled`, its tombstone marked `superseded`, and no active version of
    that work anywhere. The person had said it, and it was gone.

    So nothing happens to the old rows here until the new ones exist and have
    passed validation, and then both halves go into the store as one write. A
    failure, a silence, a cancel, or a closed tab leaves the original exactly
    as it was.
    """
    cand = validate(raw)
    stamp = now()
    items = []
    staged: dict[str, dict] = {}
    for ev in cand.events:
        d = decide(ev.labour_kind)
        rec = build_record(event=ev, transcript=cand.transcript, decision=d,
                           recorded_at=stamp, model_version=MODEL,
                           prompt_version=PROMPT_VERSION)
        rid = uuid.uuid4().hex[:16]
        staged[rid] = {**rec, "record_id": rid, "capture_id": capture_id,
                       "input_source": source}
        items.append({"record_id": rid, "activity": ev.activity_text,
                      "source": ev.source_text,
                      "activity_domain": ev.activity_domain,
                      "labour_kind": ev.labour_kind,
                      "duration_minutes": ev.duration_minutes,
                      "occurred_at": ev.occurred_at, "food": d.outcome,
                      "why": d.reason, "review_status": rec["review_status"]})

    # 🔴 Read before write, and only if there is something to write.
    #
    # No events means the replacement failed to produce anything, and an empty
    # replacement must not retire the thing it was meant to replace.
    superseded: list[str] = []
    if replaces and staged:
        for rid, rec in list(RECORDS.items()):
            if rec.get("capture_id") != replaces:
                continue
            if rec["review_status"] in ("cancelled", "superseded"):
                continue
            staged[rid] = {**rec, "review_status": "superseded",
                           "superseded_at": stamp, "superseded_by": capture_id}
            superseded.append(rid)
        # A one-for-one correction can name the row it replaced. A correction
        # that turns one sentence into three cannot, and says nothing rather
        # than picking one of them arbitrarily; the capture link carries it.
        if len(superseded) == 1:
            for rid in [r for r in staged if r not in superseded]:
                staged[rid] = {**staged[rid], "supersedes": superseded[0]}
            for it in items:
                it["supersedes"] = superseded[0]

    note = ""
    if not items and not cand.rejected:
        note = "Nothing to file in that one."
    elif cand.rejected:
        note = "Part of that could not be filed. It is shown, not discarded."

    receipt = {"capture_id": capture_id, "state": "receipt", "input_source": source,
               "heard": cand.transcript, "items": items,
               "withheld_fragments": len(cand.rejected), "note": note,
               # 🔴 What this capture retired, by id. Cancelling a correction
               # has to be able to put those rows back, and after the fact the
               # only place that list survives is here.
               "superseded": superseded,
               "at": stamp, "audio_persisted": False}
    # One write. The capture is claimed and the records land together, or
    # neither happens — see `commit` above. What comes back is what is in the
    # store, which on a lost race is somebody else's receipt for the same
    # capture, not this one.
    stored = COMMIT(capture_id, receipt, staged)
    return stored if stored is not None else receipt


# ------------------------------------------------------------------ voice ---
@app.post("/voice")
def voice():
    blob = request.files.get("audio")
    capture_id = (request.form.get("capture_id") or "").strip()
    if not blob or not capture_id:
        return jsonify({"error": "audio and capture_id are both required"}), 400
    if capture_id in CAPTURES:
        return jsonify({**CAPTURES[capture_id], "replay": True}), 200

    audio = blob.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        return jsonify({"error": "recording too long for this spike"}), 413
    if len(audio) < MIN_AUDIO_BYTES:
        # Too small to be a sentence. No model call, no capture, no record —
        # nothing is filed, so nothing has to be unfiled later.
        audio = b""
        return jsonify({"state": "receipt", "capture_id": capture_id, "heard": "",
                        "items": [], "withheld_fragments": 0,
                        "audio_persisted": False,
                        "note": "too short to be a sentence"}), 200
    mime = blob.mimetype or request.form.get("mime_type") or ""

    try:
        raw = _call(understand, audio, mime)
    except AudioUnreadable:
        return jsonify({"state": "receipt", "capture_id": capture_id, "heard": "",
                        "items": [], "withheld_fragments": 0, "audio_persisted": False,
                        "note": "Mallow could not make that out. Say it again?"}), 200
    except ModelUnavailable as e:
        # Never substituted with a guess. The person is offered the text box.
        return jsonify({"state": "fallback_text", "capture_id": capture_id,
                        "reason": str(e),
                        "prompt": "Tell Mallow what happened…"}), 503
    finally:
        audio = b""

    try:
        return jsonify(_file(capture_id, raw, "audio",
                             (request.form.get("replaces") or "").strip())), 200
    except CandidateRejected as e:
        return jsonify({"state": "receipt", "capture_id": capture_id, "heard": "",
                        "items": [], "withheld_fragments": 0, "audio_persisted": False,
                        "note": f"Mallow heard something it could not file ({e})."}), 200


# ------------------------------------------------------- bounded text -------
@app.post("/voice/text")
def text():
    """
    The fallback when the microphone is unavailable or the model failed twice.

    Capture, not conversation: one note in, a receipt out. No reply, no advice,
    no history, no follow-up question. It is the same pipeline with a different
    front door.
    """
    body = request.get_json(silent=True) or {}
    capture_id = (body.get("capture_id") or "").strip()
    note = (body.get("note") or "").strip()
    replaces = (body.get("replaces") or "").strip()
    if not capture_id or not note:
        return jsonify({"error": "capture_id and note are both required"}), 400
    if len(note) > MAX_NOTE_CHARS:
        return jsonify({"error": f"note is bounded to {MAX_NOTE_CHARS} characters"}), 413
    if capture_id in CAPTURES:
        return jsonify({**CAPTURES[capture_id], "replay": True}), 200

    try:
        raw = _call(understand_text, note)
    except (AudioUnreadable, ModelUnavailable) as e:
        return jsonify({"state": "failure", "capture_id": capture_id,
                        "reason": str(e),
                        "note": "Mallow could not file that just now."}), 503
    try:
        return jsonify(_file(capture_id, raw, "text", replaces)), 200
    except CandidateRejected as e:
        return jsonify({"state": "receipt", "capture_id": capture_id, "heard": note,
                        "items": [], "withheld_fragments": 0, "audio_persisted": False,
                        "note": f"Mallow could not file that ({e})."}), 200


# ------------------------------------------------------------- discard ------
@app.post("/voice/discard")
def discard():
    """
    "I pressed cancel while it was still thinking, so this one does not count."

    🔴 Not a race. The first design here was first-writer-wins, and the
    strategist refused it on 2026-08-29 for the right reason: if the commit
    lands a few milliseconds early, a person who pressed cancel would be left
    with a record, food and a line in their rollup. What the button means is an
    outcome, not a bet on the network.

    So both orderings end in the same place, and the deciding happens inside
    one transaction in `workspaces.discard`:

        cancel first    the tombstone takes the capture id, and the late commit
                        stops on the ordinary replay branch having written
                        nothing at all.
        commit first    the rows are moved to `cancelled` — every product read
                        already excludes those — and anything the capture
                        superseded is appended back as active, because a
                        cancelled correction must not cost somebody the record
                        it was correcting.

    🔴 This is a POST, which is what keeps it safe: the session cookie
    authorises GET and HEAD only, so cancelling always needs the bearer token.
    And there is no uid parameter — `workspaces.discard` resolves to the
    caller's own store, so another person's capture id is simply not there.
    """
    body = request.get_json(silent=True) or {}
    capture_id = (body.get("capture_id") or "").strip()
    if not capture_id:
        return jsonify({"error": "capture_id is required"}), 400
    raw_ids = body.get("record_ids") or []
    if not isinstance(raw_ids, list) or len(raw_ids) > 100 \
            or any(not isinstance(rid, str) or not rid.strip() or len(rid) > 160
                   for rid in raw_ids):
        return jsonify({"error": "record_ids must be a short list of ids"}), 400
    record_ids = tuple(dict.fromkeys(rid.strip() for rid in raw_ids))
    try:
        result = DISCARD(capture_id, when=now(), record_ids=record_ids)
    except NoStoreBound as e:
        return jsonify({"error": str(e)}), 503
    return jsonify({"capture_id": capture_id, "state": "discarded",
                    "outcome": result["outcome"],
                    "cancelled_records": result["cancelled"],
                    "restored_records": result["restored"],
                    "audio_persisted": False,
                    "note": "This one does not count."}), 200


# --------------------------------------------------------- correction -------
@app.post("/voice/cancel")
def cancel():
    """
    Cancelling appends. It never deducts.

    The prior records are marked cancelled and drop out of future rollups; the
    food already shown is not taken back and no negative amount is ever created.
    """
    capture_id = ((request.get_json(silent=True) or {}).get("capture_id") or "").strip()
    if capture_id not in CAPTURES:
        return jsonify({"error": "no such capture"}), 404

    cancelled, stamp = [], now()
    staged: dict[str, dict] = {}
    for rid, rec in list(RECORDS.items()):
        already = rec["review_status"] in ("cancelled", "superseded")
        if rec.get("capture_id") == capture_id and not already:
            staged[rid] = {**rec, "review_status": "cancelled", "cancelled_at": stamp}
            marker = uuid.uuid4().hex[:16]
            staged[marker] = {**rec, "record_id": marker, "recorded_at": stamp,
                              "review_status": "superseded", "supersedes": rid,
                              "policy_result": rec["policy_result"]}
            cancelled.append(rid)
    # A correction is one write too: the cancelled row and the row that
    # supersedes it are the same fact, and a store must not be able to hold one
    # of them.
    if staged:
        COMMIT(None, None, staged)

    return jsonify({"capture_id": capture_id, "cancelled_records": cancelled,
                    "food_deducted": False, "negative_food_created": False,
                    "note": "Recorded as cancelled. Nothing was taken back.",
                    "at": stamp}), 200


@app.get("/voice/state")
def state():
    active = [r for r in RECORDS.values() if r["review_status"] in ("active", "unclassified")]
    return jsonify({
        "captures": len(CAPTURES),
        "records_total": len(RECORDS),
        "records_in_rollup": len(active),
        "cancelled": sum(1 for r in RECORDS.values() if r["review_status"] == "cancelled"),
        "audio_objects_retained": 0,
        "storage_claim": "Append-only by application policy, with traceable corrections.",
    })


@app.get("/voice/records")
def records():
    """Developer view. Shows the full record shape including provenance."""
    return jsonify(sorted(RECORDS.values(), key=lambda r: r["recorded_at"]))


@app.get("/")
def home():
    return render_template("hold.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "spike": "voice",
                    "model_configured": bool(os.getenv("GOOGLE_CLOUD_PROJECT"))}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", 8090)))
