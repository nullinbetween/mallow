"""
Voice spike acceptance.

A-B (real Chrome / Safari against Gemini on Google Cloud) cannot run here: this container has no
Google Cloud credentials and no browser. Those two are reported as unverified
rather than simulated. Everything else is exercised for real.
"""
import io
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from contract import (CandidateRejected, RECORD_FIELDS, REVIEW_STATUS,   # noqa: E402
                      build_record, validate)
from gemini import ACCEPTED_MIME, ModelUnavailable, understand   # noqa: E402
from policy import decide                                    # noqa: E402
import app as spike                                          # noqa: E402

FIX = json.loads((HERE / "fixtures" / "utterances.json").read_text())


@pytest.fixture()
def client(monkeypatch):
    spike.RECORDS.clear(); spike.CAPTURES.clear()
    spike.app.config.update(TESTING=True)
    return spike.app.test_client()


def fake_model(monkeypatch, payload):
    monkeypatch.setattr(spike, "understand", lambda audio, mime: payload)


# 🔴 Big enough to clear MIN_AUDIO_BYTES. Two bytes used to be the default,
# which was fine while nothing looked at the size — but a payload no real
# recording could ever be is a poor stand-in for one, and every test that used
# it started exercising the too-short refusal instead of the pipeline. The
# leading bytes are the WebM/Matroska magic; the rest is padding.
SOME_AUDIO = b"\x1aE\xdf\xa3" + bytes(4096)


def post(client, payload_id="c1", data=SOME_AUDIO, mime="audio/webm"):
    return client.post("/voice", data={
        "audio": (io.BytesIO(data), "note", mime),
        "capture_id": payload_id, "mime_type": mime})


# ------------------------------------------------------------------ A / B ---
def test_browser_containers_are_accepted_without_conversion():
    """DECISION 3: webm and mp4 go as recorded. No encoder exists in this path."""
    assert "audio/webm" in ACCEPTED_MIME and "audio/mp4" in ACCEPTED_MIME
    # Prose may mention ffmpeg to say it is absent; imports and calls may not.
    import ast
    tree = ast.parse((HERE / "gemini.py").read_text())
    imported = {n.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for n in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    for banned in {"wave", "pydub", "audioop", "subprocess", "ffmpeg"}:
        assert banned not in imported, f"{banned} must not be imported in the audio path"


def test_an_unsupported_container_is_refused_not_converted():
    with pytest.raises(ModelUnavailable):
        understand(b"x", "audio/x-caf")


def test_no_offline_substitute_for_the_model(monkeypatch):
    import gemini
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(ModelUnavailable):
        gemini.understand(b"x", "audio/webm")
    with pytest.raises(ModelUnavailable):
        gemini.understand_text("一直記著後天要交回條")


# ---------------------------------------------------------------------- C ---
def test_one_sentence_one_event(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": FIX["single_event"][0]["said"], "events": [
        {"activity_text": "labelling clothes", "source_text": "全部寫上名字，弄了三十五分鐘",
         "activity_domain": "clothing_laundry",
         "labour_kind": "invisible_chore", "duration_minutes": 35, "occurred_at": None}]})
    d = post(client).get_json()
    assert len(d["items"]) == 1
    assert d["items"][0]["food"] == "grass"
    assert d["items"][0]["duration_minutes"] == 35


# ---------------------------------------------------------------------- D ---
def test_one_sentence_many_events(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": FIX["multi_event"][0]["said"], "events": [
        {"activity_text": "putting things back", "source_text": "把櫃子裡的東西歸位",
         "activity_domain": "household_upkeep",
         "labour_kind": "invisible_chore", "duration_minutes": None, "occurred_at": "today"},
        {"activity_text": "restocking", "source_text": "補了衛生紙",
         "activity_domain": "shopping_restocking",
         "labour_kind": "invisible_chore", "duration_minutes": None, "occurred_at": "today"},
        {"activity_text": "holding a deadline", "source_text": "記著後天要交回條",
         "activity_domain": "school_community",
         "labour_kind": "mental_load", "duration_minutes": None, "occurred_at": None}]})
    d = post(client).get_json()
    assert [i["food"] for i in d["items"]] == ["grass", "grass", "carrot"]


# ---------------------------------------------------------------------- E ---
def test_unspoken_time_stays_null(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": FIX["no_duration"][0]["said"], "events": [
        {"activity_text": "holding a deadline", "source_text": "孩子學校那張表什麼時候到期",
         "activity_domain": "school_community",
         "labour_kind": "mental_load", "duration_minutes": None, "occurred_at": None}]})
    it = post(client).get_json()["items"][0]
    assert it["duration_minutes"] is None and it["occurred_at"] is None


@pytest.mark.parametrize(("raw", "canonical"), (
    ("0740", "07:40"),
    ("0900", "09:00"),
    ("7:40", "07:40"),
    ("23:59", "23:59"),
))
def test_an_extracted_exact_clock_is_canonicalised(raw, canonical):
    """Q-28: normalise the field Gemini chose, never mine the transcript."""
    payload = only_event("recognised_work", said="0740 出發搭巴士")
    payload["events"][0]["source_text"] = "0740 出發搭巴士"
    payload["events"][0]["occurred_at"] = raw
    got = validate(payload)
    assert not got.rejected
    assert got.events[0].occurred_at == canonical


def test_a_non_clock_temporal_description_is_preserved_not_rejected():
    payload = only_event("mental_load", said="明天要交回條")
    payload["events"][0]["source_text"] = "明天要交回條"
    payload["events"][0]["occurred_at"] = "tomorrow"
    got = validate(payload)
    assert not got.rejected
    assert got.events[0].occurred_at == "tomorrow"


def test_digits_in_the_transcript_are_never_promoted_by_validation():
    """A reference number is not a clock merely because it has four digits."""
    payload = only_event("mental_load", said="reference number 0740")
    payload["events"][0]["source_text"] = "reference number 0740"
    payload["events"][0]["occurred_at"] = None
    got = validate(payload)
    assert got.events[0].occurred_at is None


# ---------------------------------------------------------------------- F ---
def test_unknown_receives_no_food(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": "嗯……那個……算了。", "events": [
        {"activity_text": "unclear", "source_text": "算了",
         "activity_domain": "other",
         "labour_kind": "unknown", "duration_minutes": None, "occurred_at": None}]})
    it = post(client).get_json()["items"][0]
    assert it["food"] == "withheld" and "no food" in it["why"]


def test_no_labour_content_produces_no_event(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": "今天天氣很好。", "events": []})
    d = post(client).get_json()
    assert d["items"] == [] and d["note"]


def test_recognised_work_is_recorded_without_food(client, monkeypatch):
    """
    🔴 Locked, and the citation is here so it stops being re-litigated.

        PRODUCT_DECISIONS.md §2 「獎勵機制（2026-08-22 定案）」
        「可見的、世界已經算成工作的勞動 → 不產出食物。那些本來就被看見了。」

        HANDOFF.md 「世界已經算成工作的（煮飯、打掃）→ 只記錄，不產食物。」

    It reads like a gap — most of what a person says lands here and earns
    nothing — and it is the product's whole argument. Grass is for hands-on
    work that often goes unseen; a carrot is for planning, remembering,
    deciding or coordinating. If cooking dinner also earned grass, the foods would stop meaning
    "this went unseen" and start meaning "this happened", and the meadow would
    be a activity log with a rabbit on it.

    Queried on 2026-08-24 after a QA export showed most rows at `none`. The
    finding was real and the cause is one layer up: `哄睡` — settling a child to
    sleep — was classified `recognised_work`, and care work is the canonical
    example of labour the world does *not* count. That is the taxonomy in the
    extraction prompt, not this policy, and it is logged separately.
    """
    fake_model(monkeypatch, {"transcript": "煮了晚飯。", "events": [
        {"activity_text": "cooking dinner", "source_text": "煮了晚飯",
         "activity_domain": "food_preparation",
         "labour_kind": "recognised_work", "duration_minutes": None, "occurred_at": None}]})
    assert post(client).get_json()["items"][0]["food"] == "none"


def test_the_four_kinds_map_to_exactly_these_four_outcomes():
    """The whole nourishment contract, on one screen, so a drift is visible."""
    assert {k: decide(k).outcome for k in
            ("invisible_chore", "mental_load", "recognised_work", "unknown")} == {
        "invisible_chore": "grass",     # hands-on work that often goes unseen
        "mental_load": "carrot",        # planning, remembering or coordinating
        "recognised_work": "none",      # already counted by the world
        "unknown": "withheld",          # heard, not classified, nothing issued
    }


# ---------------------------------------------------------------------- G ---
def test_replayed_capture_issues_nothing_twice(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": "補了衛生紙。", "events": [
        {"activity_text": "restocking", "source_text": "補了衛生紙",
         "activity_domain": "shopping_restocking",
         "labour_kind": "invisible_chore", "duration_minutes": None, "occurred_at": None}]})
    first = post(client, "same-capture").get_json()
    second = post(client, "same-capture").get_json()
    assert second.get("replay") is True
    assert second["items"] == first["items"]
    assert len(spike.CAPTURES) == 1


# ---------------------------------------------------------------------- H ---
def test_receipt_separates_what_was_heard_from_how_it_was_filed(client, monkeypatch):
    said = "把東西歸位。"
    fake_model(monkeypatch, {"transcript": said, "events": [
        {"activity_text": "putting things back", "source_text": "把東西歸位",
         "activity_domain": "household_upkeep",
         "labour_kind": "invisible_chore", "duration_minutes": None, "occurred_at": None}]})
    d = post(client).get_json()
    assert d["heard"] == said                      # the speaker's words
    assert d["items"][0]["activity"] == "putting things back"   # Mallow's filing
    assert d["items"][0]["source"] == "把東西歸位"             # what it rests on
    assert d["heard"] != d["items"][0]["activity"]


def test_partial_success_shows_the_bad_fragment_rather_than_dropping_it(client, monkeypatch):
    fake_model(monkeypatch, next(f for f in FIX["schema_failures"] if f["id"] == "s05")["raw"])
    d = post(client).get_json()
    assert len(d["items"]) == 1 and d["withheld_fragments"] == 1
    assert "not discarded" in d["note"]


# ---------------------------------------------------------------------- I ---
def test_no_audio_object_is_retained(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": "x", "events": []})
    post(client, "c-audio", data=b"\xff" * 4096)
    assert client.get("/voice/state").get_json()["audio_objects_retained"] == 0
    blob = json.dumps(spike.CAPTURES)
    assert "audio" not in blob or '"audio_persisted": false' in blob.replace("False", "false")
    for r in spike.CAPTURES.values():
        assert r["audio_persisted"] is False
        assert not any(isinstance(v, (bytes, bytearray)) for v in r.values())


# ------------------------------------------------------- cancellation D2 ----
def test_cancelling_never_deducts(client, monkeypatch):
    fake_model(monkeypatch, {"transcript": "補了衛生紙。", "events": [
        {"activity_text": "restocking", "source_text": "補了衛生紙",
         "activity_domain": "shopping_restocking",
         "labour_kind": "invisible_chore", "duration_minutes": None, "occurred_at": None}]})
    post(client, "c-cancel")
    d = client.post("/voice/cancel", json={"capture_id": "c-cancel"}).get_json()
    assert d["food_deducted"] is False and d["negative_food_created"] is False
    assert d["cancelled_records"] and "taken back" in d["note"]


def test_cancelling_an_unknown_capture_is_not_silently_accepted(client):
    assert client.post("/voice/cancel", json={"capture_id": "nope"}).status_code == 404


# --------------------------------------------------- contract, fixtures -----
@pytest.mark.parametrize("case", [f for f in FIX["schema_failures"] if f["id"] != "s05"])
def test_every_schema_failure_fixture_is_caught(case):
    try:
        got = validate(case["raw"])
        assert got.rejected, f"{case['id']} ({case['note']}) was accepted"
    except CandidateRejected:
        pass


def test_policy_mapping_is_total():
    from contract import LABOUR_KINDS
    assert {decide(k).outcome for k in LABOUR_KINDS} == {"grass", "carrot", "none", "withheld"}


def test_activity_domain_is_a_closed_child_and_household_enum():
    from contract import ACTIVITY_DOMAINS
    assert ACTIVITY_DOMAINS == (
        "care_child", "household_upkeep", "food_preparation",
        "clothing_laundry", "school_community", "health_admin",
        "household_admin", "shopping_restocking", "transport_errands",
        "social_coordination", "other",
    )
    assert "care_adult_elder" not in ACTIVITY_DOMAINS


def test_missing_or_unknown_activity_domain_is_rejected():
    from contract import validate
    for value in (None, "care_adult_elder", "workplace"):
        payload = only_event("invisible_chore")
        if value is None:
            payload["events"][0].pop("activity_domain")
        else:
            payload["events"][0]["activity_domain"] = value
        got = validate(payload)
        assert not got.events and len(got.rejected) == 1


def test_receipt_keeps_the_canonical_activity_domain(client, monkeypatch):
    payload = only_event("invisible_chore")
    payload["events"][0]["activity_domain"] = "household_upkeep"
    fake_model(monkeypatch, payload)
    item = post(client, "c-domain").get_json()["items"][0]
    assert item["activity_domain"] == "household_upkeep"


def test_fixtures_declare_clean_room_provenance():
    assert "clean-room" in FIX["provenance"]
    assert "No line is transcribed from" in FIX["provenance"]


# ==========================================================================
# Consensus baseline, 2026-08-22
# ==========================================================================

# 🔴 The transcript and the span have to agree. `source_text` is checked
# against the transcript it claims to be quoting (contract._span_of), so a
# helper that pairs a placeholder transcript with unrelated words is not a
# smaller fixture — it is one the real pipeline would reject.
SAID = "把東西歸位花了十分鐘"


def only_event(kind, duration=None, said=SAID, source=None):
    return {"transcript": said, "events": [
        {"activity_text": "a thing", "source_text": source or said,
         "activity_domain": "other",
         "labour_kind": kind,
         "duration_minutes": duration, "occurred_at": None}]}


# --------------------------------------------- record shape is complete ----
def test_every_required_field_is_present_on_every_record(client, monkeypatch):
    fake_model(monkeypatch, only_event("invisible_chore", 20))
    post(client, "c-fields")
    rec = client.get("/voice/records").get_json()[0]
    assert not [f for f in RECORD_FIELDS if f not in rec]
    assert rec["recorded_at"] and rec["model_version"] and rec["prompt_version"]
    assert rec["policy_result"] == "grass" and rec["policy_version"]
    assert rec["review_status"] in REVIEW_STATUS


def test_recorded_at_is_server_generated_not_model_supplied(client, monkeypatch):
    payload = only_event("mental_load")
    payload["events"][0]["recorded_at"] = "1999-01-01T00:00:00+09:00"   # model tries
    fake_model(monkeypatch, payload)
    post(client, "c-stamp")
    assert not client.get("/voice/records").get_json()[0]["recorded_at"].startswith("1999")


# ------------------------- classification is semantic, not duration-based ---
def test_a_chore_with_no_stated_duration_is_still_a_chore(client, monkeypatch):
    fake_model(monkeypatch, only_event("invisible_chore", None))
    assert post(client, "c-sem1").get_json()["items"][0]["food"] == "grass"


def test_mental_load_with_a_stated_duration_is_still_mental_load(client, monkeypatch):
    fake_model(monkeypatch, only_event("mental_load", 20))
    it = post(client, "c-sem2").get_json()["items"][0]
    assert it["food"] == "carrot" and it["duration_minutes"] == 20


def test_the_prompt_says_absence_of_a_number_proves_nothing():
    # 🔴 The prompt is hand-wrapped, so a sentence straddles a newline and an
    # indent. Asserting on the raw text makes this red whenever somebody reflows
    # a paragraph — a failure with nothing to do with the rule. Collapse first.
    import re as _re
    src = _re.sub(r"\s+", " ", (HERE / "gemini.py").read_text())
    assert "not on whether a duration was stated" in src
    assert "missing duration does not turn a chore into mental load" in src


# ------------------------------------------------------- text fallback -----
def test_text_fallback_runs_the_same_pipeline(client, monkeypatch):
    monkeypatch.setattr(spike, "understand_text", lambda note: only_event("mental_load"))
    d = client.post("/voice/text", json={"capture_id": "t1", "note": "一直記著後天要交回條"}).get_json()
    assert d["items"][0]["food"] == "carrot"
    assert d["input_source"] == "text"


def test_text_fallback_is_bounded(client):
    r = client.post("/voice/text", json={"capture_id": "t2", "note": "x" * 2001})
    assert r.status_code == 413


def test_text_fallback_replay_is_also_guarded(client, monkeypatch):
    monkeypatch.setattr(spike, "understand_text", lambda note: only_event("invisible_chore"))
    client.post("/voice/text", json={"capture_id": "t3", "note": "補了衛生紙"})
    again = client.post("/voice/text", json={"capture_id": "t3", "note": "補了衛生紙"}).get_json()
    assert again.get("replay") is True
    assert client.get("/voice/state").get_json()["records_total"] == 1


def test_model_failure_offers_the_text_box_not_a_guess(client, monkeypatch):
    def boom(audio, mime):
        raise spike.ModelUnavailable("no credentials")
    monkeypatch.setattr(spike, "understand", boom)
    r = post(client, "c-down")
    assert r.status_code == 503
    d = r.get_json()
    assert d["state"] == "fallback_text"
    assert d["prompt"] == "Tell Mallow what happened…"
    assert "items" not in d          # nothing invented


def test_a_transient_failure_is_retried_once(client, monkeypatch):
    calls = {"n": 0}
    def flaky(audio, mime):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return only_event("invisible_chore")
    monkeypatch.setattr(spike, "understand", flaky)
    assert post(client, "c-retry").get_json()["items"][0]["food"] == "grass"
    assert calls["n"] == 2


def test_the_text_path_is_capture_not_conversation():
    """No reply, no advice, no history. One note in, one receipt out."""
    import ast, re
    app_src = (HERE / "app.py").read_text()
    html = (HERE / "templates" / "hold.html").read_text()

    # Prose may say "capture, not conversation". Code and markup may not build one.
    code = "\n".join(l for l in app_src.splitlines() if not l.strip().startswith("#"))
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    for banned in ("chat_history", "conversation", "assistant_reply", "messages.append"):
        assert banned not in code, f"app.py builds {banned}"

    body = html[html.index("<body") if "<body" in html else 0:]
    for banned in ("chat", "history", "advice", "assistant"):
        assert banned not in re.sub(r"//.*", "", body).lower(), f"page contains {banned}"


# ------------------------------------------------ correction contract ------
def test_cancelling_marks_records_and_drops_them_from_rollup(client, monkeypatch):
    fake_model(monkeypatch, only_event("invisible_chore"))
    post(client, "c-cx")
    assert client.get("/voice/state").get_json()["records_in_rollup"] == 1

    d = client.post("/voice/cancel", json={"capture_id": "c-cx"}).get_json()
    assert d["food_deducted"] is False and d["negative_food_created"] is False

    st = client.get("/voice/state").get_json()
    assert st["records_in_rollup"] == 0          # excluded from future rollups
    assert st["cancelled"] == 1
    assert st["records_total"] == 2              # history grew, nothing was removed


def test_a_correction_names_what_it_supersedes(client, monkeypatch):
    fake_model(monkeypatch, only_event("mental_load"))
    post(client, "c-sup")
    client.post("/voice/cancel", json={"capture_id": "c-sup"})
    recs = client.get("/voice/records").get_json()
    sup = [r for r in recs if r["review_status"] == "superseded"]
    assert len(sup) == 1 and sup[0]["supersedes"]
    assert sup[0]["supersedes"] in {r["record_id"] for r in recs}


def test_no_record_is_ever_removed(client, monkeypatch):
    fake_model(monkeypatch, only_event("invisible_chore"))
    post(client, "c-keep")
    before = {r["record_id"] for r in client.get("/voice/records").get_json()}
    client.post("/voice/cancel", json={"capture_id": "c-keep"})
    after = {r["record_id"] for r in client.get("/voice/records").get_json()}
    assert before <= after


def test_unknown_is_recorded_as_unclassified(client, monkeypatch):
    fake_model(monkeypatch, only_event("unknown"))
    post(client, "c-unk")
    rec = client.get("/voice/records").get_json()[0]
    assert rec["review_status"] == "unclassified" and rec["policy_result"] == "withheld"


# ------------------------------------------------------- claim wording -----
def test_the_storage_claim_is_the_agreed_wording(client):
    claim = client.get("/voice/state").get_json()["storage_claim"]
    assert claim == "Append-only by application policy, with traceable corrections."
    for overclaim in ("immutable", "audit-grade", "tamper-proof", "WORM"):
        assert overclaim.lower() not in claim.lower()


# ------------------------------------------------------------ SDK shape ----
def test_sdk_usage_matches_the_baseline():
    src = (HERE / "gemini.py").read_text()
    assert "genai.Client(vertexai=True" in src
    assert 'location=GEMINI_LOCATION' in src and 'GEMINI_LOCATION", "global"' in src
    assert "types.Part.from_bytes" in src
    assert "vertexai.init" not in src          # no legacy entry point
    assert "b64encode" not in src              # no manual base64
    assert "function_call" not in src.lower()  # no function calling in the first spike


# ------------------------------------------------------ UI state shell -----
def test_the_ui_declares_the_six_functional_states():
    html = (HERE / "templates" / "hold.html").read_text()
    for state in ("idle", "recording", "processing", "receipt", "nourished", "fallback_text"):
        assert f"'{state}'" in html or f'"{state}"' in html


# ==========================================================================
# P0 gates before final UI integration (2026-08-22)
# ==========================================================================

HTML = (HERE / "templates" / "hold.html").read_text()


# ------------------------------------------------------------------- P0-2 --
def test_a_press_that_ends_during_the_permission_dialog_records_nothing():
    """
    The first press also asks for the microphone. If the finger is up by the
    time permission arrives, no recording may start.
    """
    assert "let holding = false" in HTML
    assert "if (!holding){" in HTML
    assert "stream.getTracks().forEach(t => t.stop())" in HTML
    # and the state returns to idle rather than pretending to listen
    seg = HTML[HTML.index("if (!holding){"):HTML.index("const mimeType = pickMime()")]
    assert "setState('idle')" in seg and "return" in seg


def test_every_way_a_press_can_end_releases_it():
    for ev in ("pointerup", "pointercancel", "lostpointercapture"):
        assert f"addEventListener('{ev}', release)" in HTML
    assert "window.addEventListener('blur', release)" in HTML


def test_pointer_capture_failure_does_not_abort_the_press():
    assert "try { r.setPointerCapture(e.pointerId); } catch (_) {}" in HTML


# ------------------------------------------------------------------- P0-3 --
def test_transient_failures_are_retried_once(client, monkeypatch):
    calls = {"n": 0}
    def flaky(audio, mime):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("socket reset")
        return only_event("invisible_chore")
    monkeypatch.setattr(spike, "understand", flaky)
    assert post(client, "p3-a").get_json()["items"][0]["food"] == "grass"
    assert calls["n"] == 2


def test_two_transient_failures_reach_the_text_box(client, monkeypatch):
    calls = {"n": 0}
    def always(audio, mime):
        calls["n"] += 1
        raise ConnectionError("socket reset")
    monkeypatch.setattr(spike, "understand", always)
    r = post(client, "p3-b")
    assert r.status_code == 503 and r.get_json()["state"] == "fallback_text"
    assert calls["n"] == spike.GEMINI_ATTEMPTS


def test_misconfiguration_is_not_retried(client, monkeypatch):
    """Waiting twice for the same certain failure is worse than offering the box."""
    calls = {"n": 0}
    def misconf(audio, mime):
        calls["n"] += 1
        raise spike.ModelMisconfigured("GOOGLE_CLOUD_PROJECT is unset")
    monkeypatch.setattr(spike, "understand", misconf)
    r = post(client, "p3-c")
    assert r.status_code == 503 and r.get_json()["state"] == "fallback_text"
    assert calls["n"] == 1


def test_unreadable_audio_is_not_retried(client, monkeypatch):
    calls = {"n": 0}
    def deaf(audio, mime):
        calls["n"] += 1
        raise spike.AudioUnreadable("nothing there")
    monkeypatch.setattr(spike, "understand", deaf)
    d = post(client, "p3-d").get_json()
    assert calls["n"] == 1 and d["items"] == [] and "Say it again" in d["note"]


def test_a_failed_attempt_never_logs_the_payload():
    src = (HERE / "app.py").read_text()
    seg = src[src.index("def _call("):src.index("def _file(")]
    assert "type(e).__name__" in seg
    for leak in ("%s\", e)", "str(e)", "repr(e)", "audio", "note"):
        assert f"logger.warning" not in seg or leak not in seg.split("logger.warning")[1][:120]


# ------------------------------------------------------------------- P0-4 --
def test_a_dropped_connection_lands_in_the_text_box_not_the_console():
    assert HTML.count("try {") >= 3
    voice_send = HTML[HTML.index("async function send()"):HTML.index("$('#fallback').addEventListener")]
    assert "catch (err)" in voice_send
    assert "showFallback('Mallow could not be reached" in voice_send


def test_a_failed_text_submission_keeps_what_was_typed():
    seg = HTML[HTML.index("$('#fallback').addEventListener"):HTML.index("const FOOD")]
    assert "catch (err)" in seg
    # the value is only cleared after a successful response
    assert seg.index("catch (err)") < seg.index("$('#note').value = ''")
    assert "your words are still here" in seg.lower()


# ------------------------------------------------------------------- P0-5 --
def test_the_receipt_labels_both_halves():
    assert "<h3>Mallow heard</h3>" in HTML
    assert "<h3>Saved as</h3>" in HTML
    assert HTML.index("Mallow heard") < HTML.index("Saved as")


def test_saved_as_only_appears_when_something_was_filed():
    assert "if ((d.items ?? []).length) html += `<h3>Saved as</h3>`" in HTML


# ------------------------------------------------------------------- P0-6 --
def test_recording_has_a_hard_ceiling():
    assert "const MAX_RECORDING_MS" in HTML
    ms = int(HTML.split("const MAX_RECORDING_MS =")[1].split(";")[0].strip())
    assert 0 < ms <= 120000, "a lost pointerup must not record indefinitely"
    assert "maxTimer = setTimeout(" in HTML
    assert "clearTimeout(maxTimer)" in HTML


# --------------------------------------------------------------- P1-10 -----
def test_no_native_app_switcher_claim_is_made():
    assert "cannot claim native App Switcher protection" in HTML
    for overclaim in ("FLAG_SECURE", "SceneDelegate", "secure deletion", "securely deleted"):
        assert overclaim not in HTML
    assert "Best-effort only" in HTML


# ------------------------------------------------- rebuild boundary ---------
def test_the_spike_page_declares_it_is_not_the_ui_foundation():
    assert "NOT the UI foundation" in HTML


# --------------------------------------------------- the too-short floor ----
def test_a_payload_too_small_to_be_a_sentence_never_reaches_the_model(client,
                                                                      monkeypatch):
    """
    🔴 The server's half of the silence gate, and only its half.

    The browser decides whether a recording carried sound; it has the samples.
    The server can only tell that a payload is too small to be a sentence in
    any container. That is not silence detection and is not claimed to be —
    but it does mean an accidental tap cannot spend a model call, and cannot
    file anything, even from a client that skipped the browser gate.
    """
    called = []

    def explode(*a, **k):
        called.append(a)
        raise AssertionError("the model must not be called for a blip")

    monkeypatch.setattr(spike, "understand", explode)

    d = post(client, "tiny", data=b"\x1aE\xdf\xa3").get_json()
    assert not called
    assert d["items"] == [] and d["heard"] == ""
    assert client.get("/voice/records").get_json() == []


def test_the_floor_is_low_enough_that_a_real_recording_passes(client, monkeypatch):
    """A floor that refused ordinary recordings would be worse than none."""
    fake_model(monkeypatch, only_event("invisible_chore", 10))
    d = post(client, "real", data=SOME_AUDIO).get_json()
    assert d["items"], "a normal recording still goes through"
