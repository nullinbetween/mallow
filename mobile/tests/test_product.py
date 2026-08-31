"""
Tests for the path a person actually walks.

The voice slice already has its own 56 tests. These cover what the product adds
on top of it and, more importantly, the places where the two are joined —
because a passing test on an unmounted class is exactly the failure this project
has already made once.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MOBILE = HERE.parent
ROOT = MOBILE.parent
sys.path[:0] = [str(MOBILE), str(ROOT / "spike" / "voice")]


def fresh(tmp_path=None, *, fake=True, ephemeral=True):
    """A server module with its own store, reloaded so state never leaks."""
    os.environ["MALLOW_FAKE_MODEL"] = "1" if fake else "0"
    # The local task credential. In production this is unset and the OIDC
    # service-account path is the only way in; see tasks.py.
    if "MALLOW_TASK_KEY" not in os.environ:
        os.environ["MALLOW_TASK_KEY"] = "test-task-key"
    # Signs the navigation session. Required whenever a Firebase project is
    # configured, because a per-process secret would log people out whenever
    # Cloud Run answered from a different instance.
    os.environ.setdefault("MALLOW_SESSION_SECRET", "test-session-secret")
    os.environ["MALLOW_EPHEMERAL"] = "1" if ephemeral else "0"
    if tmp_path:
        os.environ["MALLOW_DATA_DIR"] = str(tmp_path)
    # `app` is popped too: it holds the model functions the server swaps, and a
    # cached copy would carry a previous test's substitution into this one.
    for m in ("server", "ledger", "fake_model", "app",
              "identity", "workspaces", "export", "i18n", "reflection",
              "reflection_schedule", "tasks", "firestore_store"):
        sys.modules.pop(m, None)
    return importlib.import_module("server")


def visible(client, path="/"):
    """
    Page source with comments removed — a banned word in a note explaining why
    it is banned is not the word appearing in the product.

    Both kinds: HTML comments, and the `/* … */` blocks in the inline style and
    script, which is where most of this file's reasoning lives.
    """
    html = client.get(path).get_data(as_text=True)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return re.sub(r"/\*.*?\*/", "", html, flags=re.S)


@pytest.fixture()
def srv():
    s = fresh()
    return s, s.app.test_client()


def textwrap_dedent(text: str) -> str:
    import textwrap
    return textwrap.dedent(text)


def c_get(srv):
    """The rendered meadow, as the server actually serves it."""
    return srv[1].get("/").get_data(as_text=True)


def file_note(client, note, capture="c1"):
    return client.post("/voice/text", json={"capture_id": capture, "note": note}).get_json()


# --------------------------------------------------------------- fixtures --
def as_google(s, monkeypatch, uid="g1"):
    """Answer every request as a signed-in Google account."""
    ident = s.identity
    monkeypatch.setattr(ident, "resolve",
                        lambda: ident.Identity(uid=uid, provider="google",
                                               mode="firebase"))


def firebase_mode(monkeypatch, provider="google.com", uid="fb-user-1"):
    """
    A server configured exactly as production is, with token verification
    replaced. Everything else — the session cookie, the GET/POST split, the
    provider allow-list — is the real code path.
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("REQUIRE_FIREBASE_AUTH", "1")
    monkeypatch.setenv("MALLOW_SESSION_SECRET", "test-session-secret")
    s = fresh()

    def fake_verify(token):
        if token != "good-token":
            raise s.identity.Unauthenticated("bad token")
        raw = (s.identity.PROVIDERS.get(provider))
        if raw is None:
            raise s.identity.Unauthenticated(f"provider {provider!r} refused")
        return s.identity.Identity(uid=uid, provider=raw, mode="firebase")

    monkeypatch.setattr(s.identity, "_verify", fake_verify)
    return s, s.app.test_client()


def a_record(rid, when, *, food="grass", kind="invisible_chore",
             minutes=None, text="wrote names on everything"):
    """One row in the shape the ledger actually holds."""
    return {"record_id": rid, "capture_id": "seed",
            "recorded_at": when.isoformat(timespec="seconds"),
            "occurred_at": None, "duration_minutes": minutes,
            "transcript": text, "activity_text": "labelling and preparing ahead",
            "source_text": text, "activity_domain": "clothing_laundry",
            "labour_kind": kind,
            "model_version": "test", "prompt_version": "test",
            "policy_result": food, "policy_version": "test",
            "review_status": "active", "supersedes": None}


def seed(srv, *, day_offsets=(1, 2, 3, 4, 5), minutes=None, food="grass"):
    """
    Put a week of records into the caller's own workspace and hand it back.

    Written straight into the store rather than through the route, because the
    route stamps `recorded_at` with the clock and a rolling-window rule needs
    records on chosen days. The rows are the same shape either way.
    """
    from datetime import timedelta
    s, c = srv
    uid = c.get("/whoami").get_json()["uid"]
    ws = s.workspaces.for_uid(uid)
    now = s.reflection.now_jst()
    pref = s.reflection.schedule.make("weekly", "23:00", "Asia/Tokyo", now=now)
    pref["period_start_at"] = (now - timedelta(days=7)).isoformat(timespec="seconds")
    pref["next_reflection_at"] = (now - timedelta(seconds=1)).isoformat(timespec="seconds")
    ws.preferences.write(pref)
    for i, off in enumerate(day_offsets):
        ws.ledger[f"r{i}"] = a_record(f"r{i}", now - timedelta(days=off),
                                      minutes=minutes, food=food)
    return ws


def run_task(c, key="test-task-key"):
    return c.post("/tasks/weekly-reflection", headers={"X-Mallow-Task-Key": key})


def seed_week(srv):
    """A qualifying week, reflected on by the scheduled task. Returns the client."""
    s, c = srv
    seed(srv)
    assert run_task(c).status_code == 200
    return c


# --------------------------------------------------------------- the page --
def test_meadow_renders_with_the_locked_registration(srv):
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert c.get("/").status_code == 200
    # The numbers QA measured and PRODUCT_DECISIONS 25 locked.
    assert "--ground:76%" in html
    assert "width:28.294%" in html          # idle at 26% of stage height
    assert "width:17.272%" in html          # basket at 13%
    assert "aspect-ratio:1/1" in html       # the shared-unit anchor


def test_idle_screen_shows_no_dashboard(srv):
    """No counter, no score, no progress bar, no chat log on the meadow."""
    _, c = srv
    html = visible(c)
    for banned in ("progress", "score", "總計", "累計", "streak", "badge", "history"):
        assert banned not in html.lower()


def test_bubble_uses_a_real_textarea_and_carries_no_name_tag(srv):
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert "<textarea" in html and "canvas" not in html.lower()
    assert 'id="note"' in html
    # PRODUCT_DECISIONS 30: neither bubble is labelled.
    assert ">你</" not in html and "class=\"who\"" not in html


def test_hidden_controls_cannot_be_revived_by_shared_row_styles(srv):
    """Receipt actions stay hidden until a filed capture can use them."""
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert "[hidden]{display:none !important}" in html
    assert 'id="confirmRow" hidden' in html


def test_only_mallows_bubble_has_a_tail(srv):
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert ".bubble.mallow::after" in html
    assert ".bubble.you::after" not in html


def test_reduced_motion_and_breathing_is_on_the_wrapper(srv):
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert "prefers-reduced-motion" in html
    assert ".breathe{animation:none}" in html
    # Breathing must never sit on a sprite, or the crossfade drifts.
    assert re.search(r"\.sp\{[^}]*animation", html) is None


def test_dvh_not_vh_so_the_keyboard_cannot_push_the_rabbit_away(srv):
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert "100dvh" in html
    assert re.search(r"height:\s*100vh", html) is None


# ------------------------------------------------------------ what it says --
def test_the_two_locked_examples_reproduce_exactly(srv):
    s, c = srv
    said = c.post("/say?lang=zh-Hant", json=file_note(c, "幫全部衣服寫名字，35分鐘")).get_json()
    assert said["line"] == "收好了。今天有一把草。"
    assert said["food"] == "grass" and said["unsure"] is False

    said = c.post("/say?lang=zh-Hant", json=file_note(c, "整理了孩子明天要帶的東西", "c2")).get_json()
    assert said["line"] == "收好了。沒有提供時長也沒關係。"


def test_it_never_says_a_total_or_a_score(srv):
    s, c = srv
    for i, note in enumerate(["幫衣服寫名字，10分鐘", "整理了東西", "決定了要去哪間診所"]):
        said = c.post("/say", json=file_note(c, note, f"n{i}")).get_json()
        assert not re.search(r"\d", said["line"]), said["line"]
        for banned in ("總", "分數", "排名", "累計", "餘額", "level"):
            assert banned not in said["line"]


def test_uncertainty_is_saved_but_never_demands_a_second_confirmation(srv):
    s, c = srv
    said = c.post("/say?lang=zh-Hant", json=file_note(c, "然後那個東西弄完了")).get_json()
    assert said["unsure"] is True
    assert "收好了" in said["line"]
    assert "不要這筆" in said["line"] and "再說一次" in said["line"]
    assert "可以嗎" not in said["line"], "a receipt became a confirmation request again"


def test_nothing_heard_is_not_an_error(srv):
    """
    No transcript. Mallow says what was measured, and blames nobody.

    🔴 2026-08-25: the wording moved from 「我沒有聽清楚」 to 「這次我沒有收到
    聲音」. The old line kept the blame off the person, which is right, but it
    was not true of the commonest case — somebody who deliberately said
    nothing. "Not clearly" implies there was something. The app cannot tell
    that case from a microphone that missed them, so the one sentence covering
    both has to be true of both.
    """
    s, c = srv
    assert c.post("/say?lang=zh-Hant", json={"items": [], "withheld_fragments": 0}).get_json() == {
        "line": "這次我沒有收到聲音。可以再說一次，或改用打字告訴我。",
        "unsure": False, "food": None}


def test_a_transcript_with_no_activity_is_answered_by_being_heard(srv):
    """
    🔴 The case this product cannot afford to get wrong.

    Someone says "我覺得很累" and nothing in it maps to an activity. Mallow
    used to answer both this and the no-transcript case with "這個我沒聽出什麼
    要記的" — which tells a person that what they said did not count. Mallow
    exists because that already happens to this work everywhere else.

    Nothing is filed and no food is issued: that part was correct and stays.
    What changes is that having heard someone is stated separately from having
    filed something, because the two are separate facts.
    """
    s, c = srv
    out = c.post("/say?lang=zh-Hant", json={"items": [], "withheld_fragments": 0,
                               "heard": "我覺得很累"}).get_json()
    assert out == {"line": "我聽到了。這次沒有新增活動紀錄——不過，我聽到了。",
                   "unsure": False, "food": None}

    en = c.post("/say?lang=en", json={"items": [], "withheld_fragments": 0,
                                      "heard": "I am so tired"}).get_json()
    assert en["line"] == ("I heard you. No activity was added this time—but "
                          "I heard you.")
    assert en["food"] is None


def test_the_rabbit_never_says_there_was_nothing_worth_recording():
    """
    A ban on a shape of sentence, not on one string.

    Ruled out by the Owner and the Strategic Officer on 2026-08-23. The wording
    is banned in both languages and everywhere in the table, so it cannot come
    back through a different key.
    """
    import i18n

    banned = ("沒有什麼要記的", "沒聽出", "不值得", "值得記錄",
              "nothing worth", "nothing to record", "nothing to note",
              "anything to note")
    for key, row in i18n.STRINGS.items():
        for lang, text in row.items():
            low = text.lower()
            for phrase in banned:
                assert phrase.lower() not in low, (
                    f"{key}[{lang}] says {phrase!r}. Mallow does not tell a "
                    f"person that what they said was not worth recording.")


def test_the_carrot_line_can_actually_be_reached(srv):
    """
    `line_carrot` must win over generic missing-duration reassurance. Mental
    load is a semantic class and may or may not have a stated duration; neither
    case should make the visible carrot go unnamed.

    The grass side of the same branch is asserted too, because narrowing the
    branch to the carrot is the whole point: a chore with no minutes still gets
    the reassurance, exactly as the locked example says.
    """
    _, c = srv

    carrot = c.post("/say?lang=zh-Hant", json={
        "items": [{"food": "carrot", "duration_minutes": None, "source": "x"}],
        "withheld_fragments": 0, "heard": "x"}).get_json()
    assert carrot["food"] == "carrot"
    assert carrot["line"] == "收好了。有一根蘿蔔。"

    mixed = c.post("/say?lang=zh-Hant", json={
        "items": [{"food": "none", "duration_minutes": None, "source": "a"},
                  {"food": "carrot", "duration_minutes": None, "source": "b"}],
        "withheld_fragments": 0, "heard": "ab"}).get_json()
    assert mixed["line"] == "收好了。有一根蘿蔔。"

    # Grass with no duration keeps the locked reassurance, not the food line.
    grass = c.post("/say?lang=zh-Hant", json={
        "items": [{"food": "grass", "duration_minutes": None, "source": "x"}],
        "withheld_fragments": 0, "heard": "x"}).get_json()
    assert grass["line"] == "收好了。沒有提供時長也沒關係。"


# ======================= the person's own words, traceably ==================
# 🔴 Ordered by the Owner and the Strategic Officer, 2026-08-23, after a
# deployed recording of silence came back as a fluent French sentence and was
# filed as something the person had said.
#
# The contract these four tests hold:
#
#   transcript   everything the person said, verbatim, never rewritten
#   source_text  the stretch of that sentence one event rests on
#
# and `source_text` must be a real, contiguous piece of `transcript`. That is
# what makes a record traceable back to a person's own words instead of to a
# model's summary of them. An earlier ruling asked for the full sentence to
# live in `source_text`; it was withdrawn once the CSV from the live app showed
# that `source_text` is a span by design and `transcript` is the field that
# holds the whole utterance. Both are stored on every record, so nothing is
# lost either way — but only one of them is the canonical original.

def test_the_whole_sentence_is_kept_verbatim_in_the_transcript(srv):
    """
    A sentence that mixes a feeling with a piece of work. V1 files the work and
    does not file the feeling — but it must not lose the words.
    """
    _, c = srv
    said = "我覺得很累，哄睡了兩小時"
    out = file_note(c, said, "tr1")

    assert out["heard"] == said, "the receipt shows the whole sentence back"

    rows = c.get("/voice/records").get_json()
    assert rows and all(r["transcript"] == said for r in rows), \
        "every record carries the whole utterance, feeling included"


def test_every_source_text_is_a_span_of_its_transcript(srv):
    """The traceability check itself, over a sentence that becomes two rows."""
    _, c = srv
    said = "補了衛生紙，一直記著要預約牙醫"
    file_note(c, said, "tr2")

    rows = c.get("/voice/records").get_json()
    assert len(rows) >= 2, "one sentence, more than one piece of work"
    for r in rows:
        assert r["source_text"] in r["transcript"], (
            f"{r['source_text']!r} is not a piece of {r['transcript']!r}")


def test_each_event_points_at_its_own_span_not_the_whole_sentence(srv):
    """
    Three records that all quote the entire sentence would be traceable to
    nothing: you could not tell which row came from which part.
    """
    _, c = srv
    said = "補了衛生紙，一直記著要預約牙醫"
    file_note(c, said, "tr3")

    rows = c.get("/voice/records").get_json()
    spans = [r["source_text"] for r in rows]
    assert len(set(spans)) == len(spans), "each row quotes a different stretch"
    assert not all(sp == said for sp in spans), \
        "a span is a piece of the sentence, not a copy of it"


def test_a_rewritten_or_invented_span_is_refused_not_repaired():
    """
    Fail closed. A model that paraphrases, translates, or invents the words it
    attributes to a person must not have those words filed as that person's.

    The rejected fragment stays visible: the person is entitled to see that
    part of what they said could not be used, rather than have it vanish.
    """
    import contract

    said = "補了衛生紙，一直記著要預約牙醫"

    def one(source_text):
        return contract.validate({
            "transcript": said,
            "events": [{"activity_text": "a thing", "source_text": source_text,
                        "activity_domain": "other",
                        "labour_kind": "invisible_chore",
                        "duration_minutes": None, "occurred_at": None}]})

    kept = one("補了衛生紙")
    assert len(kept.events) == 1 and not kept.rejected

    for wrong, why in (
            ("restocked the toilet paper", "translated"),
            ("補充了衛生紙", "paraphrased — one character changed"),
            ("幫全部衣服寫名字", "a sentence from somebody else's day"),
            ("補了衛生紙，然後去接小孩", "half quoted, half invented")):
        out = one(wrong)
        assert not out.events, f"{why}: this must not be filed"
        assert len(out.rejected) == 1, f"{why}: and it must stay visible"
        assert "span" in out.rejected[0]["reason"]


def test_say_rejects_a_malformed_receipt(srv):
    _, c = srv
    assert c.post("/say", json={"items": "not a list"}).status_code == 400


# ------------------------------------------------------- optional duration --
def test_a_missing_duration_is_recorded_and_never_invented(srv):
    s, c = srv
    file_note(c, "整理了孩子明天要帶的東西")
    row = c.get("/export.json").get_json()["records"][0]
    assert row["duration_minutes"] is None
    assert row["policy_result"] == "grass"      # still food; absence is not a penalty
    assert row["review_status"] == "active"


def test_a_stated_duration_is_marked_as_the_persons_own_claim(srv):
    s, c = srv
    file_note(c, "幫全部衣服寫名字，35分鐘")
    row = c.get("/export.json").get_json()["records"][0]
    assert row["duration_minutes"] == 35
    assert row["provenance"] == "asserted"


def _candidate_with_time(said="0740 出發搭巴士", occurred_at="0740"):
    return {"transcript": said, "events": [{
        "activity_text": "School run by bus",
        "source_text": said,
        "activity_domain": "transport_errands",
        "labour_kind": "recognised_work",
        "duration_minutes": None,
        "occurred_at": occurred_at,
    }]}


def test_q28_clock_time_survives_the_whole_capture_path(srv, monkeypatch):
    s, c = srv
    monkeypatch.setattr(s.slice_, "understand_text",
                        lambda note: _candidate_with_time(note, "0740"))

    receipt = c.post("/voice/text", json={
        "capture_id": "q28-clock", "note": "0740 出發搭巴士",
    }).get_json()

    assert receipt["items"][0]["occurred_at"] == "07:40"
    row = c.get("/export.json").get_json()["records"][0]
    assert row["occurred_at"] == "07:40"
    assert row["duration_minutes"] is None
    assert row["recorded_at"] != row["occurred_at"]


def test_q28_a_clock_with_no_duration_is_still_the_persons_own_claim(srv, monkeypatch):
    """
    🔴 The branch nothing was watching.

    `provenance()` reads "asserted" when the row carries a duration **or** an
    occurrence time — the person said one of them. Deleting the
    `occurred_at` half left all 294 Python tests green, which means the label
    on a clock-only row was free to flip to "inferred" without anyone noticing.

    That label is not decoration. It is what the record page uses to tell
    somebody whether a number came out of their own mouth or out of a model,
    and a clock the person actually said being filed as Mallow's reading is
    exactly the confusion Q-28 exists to end.

    Found by mutation on 2026-08-26, after the Q-28 gate was already green.
    """
    s, c = srv
    monkeypatch.setattr(s.slice_, "understand_text",
                        lambda note: _candidate_with_time(note, "0740"))
    c.post("/voice/text", json={"capture_id": "q28-prov", "note": "0740 出發搭巴士"})

    row = c.get("/export.json").get_json()["records"][0]
    assert row["duration_minutes"] is None, "the point of the row: no duration"
    assert row["occurred_at"] == "07:40"
    assert row["provenance"] == "asserted", \
        "the person said the time; it is not Mallow's reading"


def test_q28_receipt_acknowledges_occurrence_not_missing_duration(srv):
    s, _ = srv
    receipt = {"items": [{"food": "none", "duration_minutes": None,
                           "occurred_at": "0740"}],
               "withheld_fragments": 0}
    zh = s.rabbit_line(receipt, "zh-Hant")["line"]
    en = s.rabbit_line(receipt, "en")["line"]
    assert zh == "收好了，07:40 也記下了。"
    assert en == "Kept. I noted 07:40 too."
    assert "沒有時間" not in zh


def test_missing_duration_copy_names_duration_not_time(srv):
    s, _ = srv
    receipt = {"items": [{"food": "grass", "duration_minutes": None,
                           "occurred_at": None}],
               "withheld_fragments": 0}
    assert s.rabbit_line(receipt, "zh-Hant")["line"] == \
        "收好了。沒有提供時長也沒關係。"


def test_q28_records_page_separates_occurred_duration_and_recorded(srv, monkeypatch):
    s, c = srv
    monkeypatch.setattr(s.slice_, "understand_text",
                        lambda note: _candidate_with_time(note, "0900"))
    c.post("/voice/text?lang=zh-Hant", json={"capture_id": "q28-page",
                                 "note": "0900 drop in孩子"})
    html = visible(c, "/records?lang=zh-Hant")
    assert "發生時間：09:00" in html
    assert "未提供時長" in html
    assert "記錄時間：" in html
    assert "沒有時間（不需要補）" not in html


def test_q28_legacy_compact_clock_is_normalised_only_for_reading(srv):
    """Append-only history stays 0900 on disk while the page says 09:00."""
    s, c = srv
    uid = c.get("/whoami?lang=zh-Hant").get_json()["uid"]
    import workspaces
    row = a_record("legacy-time", __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc))
    row["occurred_at"] = "0900"
    workspaces.REGISTRY.get(uid).ledger["legacy-time"] = row
    assert "發生時間：09:00" in visible(c, "/records?lang=zh-Hant")
    stored = c.get("/export.json?lang=zh-Hant").get_json()["records"][0]
    assert stored["occurred_at"] == "0900", "history was rewritten"


def test_q28_pdf_labels_both_kinds_of_time(srv, monkeypatch):
    import io
    from pdfminer.high_level import extract_text

    s, c = srv
    monkeypatch.setattr(s.slice_, "understand_text",
                        lambda note: _candidate_with_time(note, "0740"))
    c.post("/voice/text?lang=zh-Hant", json={"capture_id": "q28-pdf",
                                 "note": "0740 出發搭巴士"})
    response = c.get("/export.pdf?lang=zh-Hant")
    assert response.data.startswith(b"%PDF")
    text = extract_text(io.BytesIO(response.data))
    assert "發生時間：07:40" in text
    assert "未提供時長" in text
    assert "記錄時間：" in text


def test_q29_human_timestamp_is_clean_while_machine_exports_stay_iso(srv):
    """The page may be readable; CSV and JSON remain stable machine data."""
    from datetime import datetime
    import io
    import export as exporter
    import workspaces
    from pdfminer.high_level import extract_text

    s, c = srv
    uid = c.get("/whoami?lang=zh-Hant").get_json()["uid"]
    ws = workspaces.REGISTRY.get(uid)
    raw_stamp = "2026-08-26T10:03:12+09:00"
    row = a_record("q29-display", datetime.fromisoformat(raw_stamp))
    row["occurred_at"] = "07:40"
    ws.ledger["q29-display"] = row
    ws.preferences.write({"timezone": "UTC"})

    html = visible(c, "/records?lang=zh-Hant")
    assert "發生時間：07:40" in html
    assert "記錄時間：2026-08-26 01:03 UTC" in html
    assert raw_stamp not in html

    exported = c.get("/export.json?lang=zh-Hant").get_json()["records"][0]
    assert exported["recorded_at"] == raw_stamp
    assert raw_stamp in exporter.to_csv([row])

    pdf_text = extract_text(io.BytesIO(exporter.to_pdf(
        [row], lang="zh-Hant", timezone_name="UTC")))
    assert "記錄時間：2026-08-26 01:03 UTC" in pdf_text
    assert raw_stamp not in pdf_text


def test_q30_display_conversion_uses_the_saved_iana_timezone():
    import export as exporter

    raw = "2026-08-26T10:03:12+09:00"
    assert exporter.display_timestamp(raw, "zh-Hant", "Asia/Tokyo") == \
        "2026-08-26 10:03 JST"
    assert exporter.display_timestamp(raw, "en", "UTC") == \
        "26 Aug 2026, 01:03 UTC"
    assert exporter.display_timestamp(raw, "en", "America/New_York") == \
        "25 Aug 2026, 21:03 EDT"


def test_an_unclassified_row_is_marked_inferred_and_gets_no_food(srv):
    s, c = srv
    file_note(c, "然後那個東西弄完了")
    row = c.get("/export.json").get_json()["records"][0]
    assert row["policy_result"] == "withheld"
    assert row["review_status"] == "unclassified"
    assert row["provenance"].startswith("inferred")


# ------------------------------------------------------- store and replay --
def test_the_same_capture_is_only_ever_filed_once(srv):
    s, c = srv
    first = file_note(c, "幫衣服寫名字，20分鐘", "same")
    again = c.post("/voice/text", json={"capture_id": "same",
                                        "note": "幫衣服寫名字，20分鐘"}).get_json()
    assert again["replay"] is True
    assert again["items"] == first["items"]
    assert c.get("/voice/state").get_json()["records_total"] == len(first["items"])


def test_a_correction_appends_and_never_deducts(srv):
    """
    🔴 Rewritten 2026-08-29, and the rewrite is the point.

    It used to prove "history grew" by counting rows in `/export.json`, which
    only worked because the export handed back everything including the rows a
    person had cancelled. Owner's ruling that day: cancelling means the content
    does not come back, in any file they are offered.

    So the same two facts are still asserted, from the two places that now own
    them: history grows in the store, and the person's own download does not
    show them what they cancelled.
    """
    s, c = srv
    file_note(c, "幫衣服寫名字，20分鐘", "cap")
    before = len(c.get("/voice/records").get_json())
    out = c.post("/voice/cancel", json={"capture_id": "cap"}).get_json()
    assert out["food_deducted"] is False and out["negative_food_created"] is False

    history = c.get("/voice/records").get_json()
    assert len(history) > before                      # nothing was deducted
    assert any(r["review_status"] == "cancelled" for r in history)
    assert any(r.get("supersedes") for r in history)
    assert c.get("/voice/state").get_json()["records_in_rollup"] == 0

    given_back = c.get("/export.json").get_json()["records"]
    assert given_back == [], "the export handed back a cancelled row"


def test_history_cannot_be_rewritten(srv):
    s, c = srv
    from ledger import HistoryRewrite
    with s.app.test_request_context("/"):
        import workspaces
        led = workspaces.current().ledger
        led["r1"] = {"review_status": "active", "source_text": "原句",
                     "recorded_at": "x"}
        with pytest.raises(HistoryRewrite):
            led["r1"] = {"review_status": "active", "source_text": "改過的句子",
                         "recorded_at": "x"}
        with pytest.raises(HistoryRewrite):
            del led["r1"]
        # The one permitted move.
        led["r1"] = {"review_status": "cancelled", "source_text": "原句",
                     "recorded_at": "x"}
        assert led["r1"]["source_text"] == "原句"


def test_a_status_change_may_not_smuggle_an_edit(srv):
    s, _ = srv
    from ledger import HistoryRewrite
    with s.app.test_request_context("/"):
        import workspaces
        led = workspaces.current().ledger
        led["r2"] = {"review_status": "active", "source_text": "原句"}
        with pytest.raises(HistoryRewrite):
            led["r2"] = {"review_status": "cancelled", "source_text": "別的句子"}


def test_the_journal_survives_a_restart(tmp_path, monkeypatch):
    """A workspace opened again after a restart still holds its rows."""
    monkeypatch.setenv("MALLOW_LOCAL_SECRET", "fixed-for-this-test")
    s = fresh(tmp_path, ephemeral=False)
    c = s.app.test_client()
    c.post("/voice/text", json={"capture_id": "keep", "note": "幫衣服寫名字，5分鐘"})
    uid = c.get("/whoami").get_json()["uid"]
    assert (tmp_path / uid / "records-demo.jsonl").exists()

    again = fresh(tmp_path, ephemeral=False)
    with again.app.test_request_context("/"):
        import workspaces
        rows = workspaces.REGISTRY.get(uid).ledger.ordered()
    assert rows and rows[0]["source_text"]


def test_demo_rows_are_stored_apart_from_real_ones(tmp_path):
    store = tmp_path / "store"
    demo = fresh(store, fake=True, ephemeral=False)
    with demo.app.test_request_context("/"):
        import workspaces
        d = workspaces.current()
    real = fresh(store, fake=False, ephemeral=False)
    with real.app.test_request_context("/"):
        import workspaces as w2
        r = w2.current()
    assert d.ledger._path.name == "records-demo.jsonl"
    assert r.ledger._path.name == "records.jsonl"
    assert d.captures._path != r.captures._path


# ------------------------------------------------------------- extraction --
def test_the_fake_model_is_never_a_silent_fallback():
    """Without MALLOW_FAKE_MODEL the real adapter is used, failures and all."""
    s = fresh(fake=False)
    import gemini
    assert s.slice_.understand is gemini.understand
    assert s.slice_.understand_text is gemini.understand_text


def test_the_fake_model_refuses_to_pretend_it_can_hear():
    import fake_model
    from gemini import AudioUnreadable
    with pytest.raises(AudioUnreadable):
        fake_model.understand(b"\x00" * 100, "audio/webm")


def test_the_fake_model_never_invents_a_number():
    """The double exercises downstream code; it is not a Gemini time eval."""
    import fake_model
    out = fake_model.understand_text("整理了孩子明天要帶的東西")
    assert all(e["duration_minutes"] is None for e in out["events"])
    assert all(e["occurred_at"] is None for e in out["events"])


def test_extraction_quotes_the_person_rather_than_paraphrasing(srv):
    s, c = srv
    file_note(c, "幫全部衣服寫名字，35分鐘")
    row = c.get("/export.json").get_json()["records"][0]
    assert "幫全部衣服寫名字" in row["source_text"]
    assert row["transcript"] == "幫全部衣服寫名字，35分鐘"


def test_an_invalid_candidate_writes_nothing_and_feeds_nobody(srv, monkeypatch):
    s, c = srv
    monkeypatch.setattr(s.slice_, "understand_text",
                        lambda note: {"transcript": "x", "events": "not an array"})
    out = c.post("/voice/text", json={"capture_id": "bad", "note": "x"}).get_json()
    assert out["items"] == []
    assert c.get("/voice/state").get_json()["records_total"] == 0


def test_a_model_outage_offers_the_text_box_and_never_a_guess(srv, monkeypatch):
    s, c = srv
    from gemini import ModelUnavailable

    def boom(*a):
        raise ModelUnavailable("down")

    monkeypatch.setattr(s.slice_, "understand", boom)
    r = c.post("/voice", data={"capture_id": "v1", "audio": (open(__file__, "rb"), "a.webm")},
               content_type="multipart/form-data")
    assert r.status_code == 503
    assert r.get_json()["state"] == "fallback_text"
    assert c.get("/voice/state").get_json()["records_total"] == 0


# --------------------------------------------------------- basket and leaf --
def test_a_meadow_with_nothing_in_it_shows_no_leaf(srv):
    """The rule exists now; silence is still the normal answer."""
    import reflection
    _, c = srv
    g = c.get("/garden").get_json()
    assert g["leaf"] is None and g["leaf_rule"] == reflection.RULE_ID


def test_no_route_can_make_a_leaf(srv):
    """
    A leaf is evidence that the agent acted on its own. A leaf a person could
    conjure would be evidence of nothing, so there is no way to ask for one:
    the old `?leaf=1` preview is gone and nothing replaced it.
    """
    _, c = srv
    for attempt in ("/garden?leaf=1", "/garden?leaf=true", "/garden?preview=1"):
        assert c.get(attempt).get_json()["leaf"] is None, attempt
    assert c.post("/garden").status_code == 405
    html = (MOBILE / "templates" / "index.html").read_text()
    assert "leaf=1" not in html


def test_leaf_tokens_are_reachable_and_sit_above_the_rabbit_hit_area(srv):
    _, c = srv
    html = c.get("/?lang=zh-Hant").get_data(as_text=True)
    assert 'id="leafTray"' in html and 'data-label="打開摺好的葉子"' in html
    assert ".leaf-token{position:absolute;width:44px;height:44px" in html
    assert "z-index:9" in html and "#hold" in html and "z-index:4" in html
    assert "background:radial-gradient(circle" in html
    assert "rgba(253,248,226,.27)" in html
    assert "opacity:1;transform:rotate(var(--leaf-turn" in html
    assert "@keyframes leaf-float" in html
    assert ".dialogue{position:absolute" in html and "z-index:11" in html
    assert "#ambient{position:absolute" in html and "pointer-events:none" in html
    assert 'id="leafHit"' not in html
    assert 'class="sp leaf"' not in html


def test_the_basket_carries_no_badge_or_count(srv):
    _, c = srv
    html = visible(c)
    for banned in ("badge", "unread", "未讀", "紅點", "notification"):
        assert banned not in html.lower()


def test_leaf_refresh_is_bounded_and_wakes_when_the_page_returns(srv):
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert "const LEAF_BACKOFF = [30000, 90000, 180000, 360000, 600000, 960000]" in html
    assert 'document.addEventListener("visibilitychange"' in html
    assert 'api("/garden?lang=" + encodeURIComponent(LANG), {cache:"no-store"})' in html
    assert "setInterval(leafCheck" not in html


def test_basket_and_glow_are_not_click_targets(srv):
    _, c = srv
    html = c.get("/").get_data(as_text=True)
    assert '<img class="sp basket on"' in html
    assert "#warm{" in html and "pointer-events:none" in html
    assert 'id="basketHit"' not in html
    assert "width:31%;height:30%" in html


def test_the_garden_never_returns_a_count(srv):
    """
    Words, never counts. The rule id carries digits and is not a number about
    anybody, so the guard is on what a person actually reads: the leaf.
    """
    seeded = seed_week(srv)
    g = seeded.get("/garden").get_json()
    assert g["leaf"] is not None
    readable = g["leaf"]["title"] + " " + g["leaf"]["body"]
    assert not re.search(r"\d+\s*(hours?|minutes?|mins?|%)", readable, re.I)
    for counted in ("total", "score", "streak", "level", "count", "分數", "總共"):
        assert counted not in json.dumps(g, ensure_ascii=False).lower()


# ------------------------------------------------------------ record layer --
def test_the_record_page_shows_the_persons_words_and_the_provenance(srv):
    s, c = srv
    file_note(c, "幫全部衣服寫名字，35分鐘")
    html = c.get("/records?lang=zh-Hant").get_data(as_text=True)
    assert "幫全部衣服寫名字" in html
    assert "asserted" in html
    assert "35 分鐘（你說的）" in html


def test_records_show_effective_food_and_all_reflection_totals(srv):
    from datetime import timedelta
    s, c = srv
    uid = c.get("/whoami?lang=zh-Hant").get_json()["uid"]
    ws = s.workspaces.for_uid(uid)
    now = s.reflection.now_jst()
    ws.ledger["grass-active"] = a_record("grass-active", now, food="grass")
    cancelled = a_record("grass-cancelled", now + timedelta(seconds=1), food="grass")
    cancelled["review_status"] = "cancelled"
    ws.ledger["grass-cancelled"] = cancelled
    ws.ledger["carrot-active"] = a_record(
        "carrot-active", now + timedelta(seconds=2), food="carrot", kind="mental_load")
    for sid in ("leaf-a", "leaf-b"):
        ws.summaries[sid] = {"summary_id": sid, "created_at": now.isoformat(),
                             "reflection": sid, "reflection_zh": sid}
    html = c.get("/records?lang=zh-Hant").get_data(as_text=True)
    assert "草 1" in html and "蘿蔔 1" in html and "葉子 2" in html
    assert "不是目標或分數" in html


def test_export_is_described_as_an_ordinary_export(srv):
    s, c = srv
    html = visible(c, "/records?lang=zh-Hant")
    for banned in ("證據", "求助", "法律", "evidence", "legal"):
        assert banned not in html
    assert "不會寄給任何人" in html


def test_export_carries_the_exact_storage_claim_and_no_stronger_one(srv):
    s, c = srv
    payload = c.get("/export.json").get_json()
    assert payload["storage_claim"] == \
        "Append-only by application policy, with traceable corrections."
    assert payload["audio_persisted"] is False
    blob = json.dumps(payload) + visible(c, "/records")
    for overclaim in ("immutable", "tamper", "WORM", "audit-grade", "secure deletion"):
        assert overclaim.lower() not in blob.lower()


def test_csv_export_downloads_with_every_column(srv):
    s, c = srv
    file_note(c, "幫衣服寫名字，5分鐘")
    r = c.get("/export.csv")
    assert "attachment" in r.headers["Content-Disposition"]
    import export as exporter
    head = r.get_data(as_text=True).splitlines()[0].split(",")
    assert head == list(exporter.COLUMNS)


def test_no_automatic_outbound_path(srv):
    """
    No automatic outbound path. A person can download an export in their
    browser, but Mallow does not send it to any external destination.
    """
    s, c = srv
    source = (MOBILE / "server.py").read_text() + \
             (MOBILE / "templates" / "index.html").read_text()
    for outbound in ("smtplib", "sendgrid", "mailto:", "requests.post", "urllib.request"):
        assert outbound not in source


# ------------------------------------------------------------------- pwa ---
def test_the_app_is_installable(srv):
    _, c = srv
    m = c.get("/manifest.webmanifest").get_json()
    assert m["display"] == "standalone" and m["start_url"] == "/"
    assert any(i["sizes"] == "512x512" for i in m["icons"])
    assert c.get("/sw.js").status_code == 200


def test_the_service_worker_never_caches_or_queues_a_capture(srv):
    _, c = srv
    sw = c.get("/sw.js").get_data(as_text=True)
    assert 'e.request.method !== "GET"' in sw
    assert "/voice" in sw and "/say" in sw       # both excluded from the cache
    assert "sync" not in sw and "IndexedDB" not in sw


def test_the_artwork_is_served(srv):
    _, c = srv
    for name in ("background_day.webp", "background_night.webp", "rabbit_idle.webp",
                 "rabbit_listening.webp", "rabbit_grass.webp", "rabbit_carrot.webp",
                 "rabbit_sleeping.webp", "basket.webp", "leaf.webp"):
        assert c.get(f"/art/{name}").status_code == 200, name


def test_health_reports_whether_a_model_is_configured(srv):
    _, c = srv
    assert c.get("/healthz").get_json()["ok"] is True


def test_health_is_also_served_on_a_path_cloud_run_does_not_intercept(srv):
    """
    🔴 `/healthz` is unreachable on Cloud Run.

    Google's frontend answers that exact path with its own 404 page before the
    request is proxied to the container. Observed on the deployed service on
    2026-08-23: every sibling route registered in the same loop answered 401,
    `/healthz` answered a Google error page on both `run.app` URL formats, and
    no such request appeared in the service log. Locally it has always worked,
    which is why it survived every green run right up to deployment.

    The same view is therefore mounted at `/health`, and that is the path the
    runbook uses. This test exists so the second mount cannot be tidied away by
    someone who only ever runs the suite on a laptop.
    """
    _, c = srv
    assert c.get("/health").get_json()["ok"] is True
    assert c.get("/health").get_json() == c.get("/healthz").get_json()


# ============================ identity and isolation ========================
# The acceptance list CodeX set for MALLOW-AUTH-STORAGE-DRIVE. These are the
# tests that matter most in this file: everything else protects a feature, and
# these protect a person from another person.

def test_two_browsers_cannot_see_each_other(srv):
    s, a = srv
    b = s.app.test_client()
    file_note(a, "幫全部衣服寫名字，35分鐘", "a1")
    assert len(a.get("/export.json").get_json()["records"]) == 1
    assert b.get("/export.json").get_json()["records"] == []
    file_note(b, "整理了孩子明天要帶的東西", "b1")
    assert len(a.get("/export.json").get_json()["records"]) == 1
    assert len(b.get("/export.json").get_json()["records"]) == 1
    assert a.get("/whoami").get_json()["uid"] != b.get("/whoami").get_json()["uid"]


def test_a_local_workspace_survives_across_requests_in_one_browser(srv):
    s, c = srv
    first = c.get("/whoami").get_json()["uid"]
    file_note(c, "幫衣服寫名字，5分鐘", "k1")
    assert c.get("/whoami").get_json()["uid"] == first
    assert len(c.get("/export.json").get_json()["records"]) == 1


def test_a_uid_supplied_by_the_client_is_ignored(srv):
    s, a = srv
    b = s.app.test_client()
    file_note(a, "幫全部衣服寫名字，35分鐘", "a1")
    victim = a.get("/whoami").get_json()["uid"]

    # Every way a caller might try to name someone else's workspace.
    assert b.get(f"/export.json?uid={victim}").get_json()["records"] == []
    assert b.get("/export.json", headers={"X-Uid": victim}).get_json()["records"] == []
    assert b.get(f"/records?uid={victim}").status_code == 200
    assert victim not in b.get(f"/records?uid={victim}").get_data(as_text=True)


def test_a_forged_local_cookie_is_refused(srv):
    s, a = srv
    file_note(a, "幫全部衣服寫名字，35分鐘", "a1")
    victim = a.get("/whoami").get_json()["uid"]
    b = s.app.test_client()
    import identity
    b.set_cookie(identity.COOKIE, victim, domain="localhost")     # unsigned
    assert b.get("/whoami").get_json()["uid"] != victim
    assert b.get("/export.json").get_json()["records"] == []


def test_a_deployment_can_refuse_local_identity_outright(monkeypatch):
    """Fail closed: REQUIRE_FIREBASE_AUTH must not degrade to laptop mode."""
    monkeypatch.setenv("REQUIRE_FIREBASE_AUTH", "1")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    s = fresh()
    c = s.app.test_client()
    assert c.get("/whoami").status_code == 401
    assert c.get("/export.json").status_code == 401
    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_a_token_without_a_configured_project_is_not_accepted(srv):
    _, c = srv
    r = c.get("/whoami", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_the_workspace_path_cannot_be_steered_by_a_uid(srv):
    import workspaces
    assert workspaces.folder_for("abc_123-XYZ") == "abc_123-XYZ"
    for nasty in ("../../etc/passwd", "a/b", "..", "", "x" * 200, "a\x00b"):
        got = workspaces.folder_for(nasty)
        assert got.startswith("u_")
        assert "/" not in got and ".." not in got and "\x00" not in got


def test_an_anonymous_workspace_says_it_is_temporary(srv):
    _, c = srv
    assert c.get("/whoami?lang=zh-Hant").get_json()["temporary"] is True
    assert "暫時試玩空間" in c.get("/?lang=zh-Hant").get_data(as_text=True)
    page = c.get("/records?lang=zh-Hant").get_data(as_text=True)
    assert "退出匿名模式" in page
    assert "用 Google 登入" not in page


def test_auth_config_serves_no_secret(srv):
    _, c = srv
    cfg = c.get("/auth/config").get_json()
    blob = json.dumps(cfg).lower()
    for secret in ("client_secret", "private_key", "refresh_token", "password"):
        assert secret not in blob
    assert "drive_scope" not in cfg
    assert "drive_configured" not in cfg


# ================================== export ==================================
def test_the_pdf_reads_as_a_self_reported_record(srv):
    s, c = srv
    file_note(c, "幫全部衣服寫名字，35分鐘")
    r = c.get("/export.pdf")
    assert r.status_code == 200
    assert r.data[:5] == b"%PDF-"
    assert "attachment" in r.headers["Content-Disposition"]


def test_export_wording_avoids_the_forbidden_claims(srv):
    s, c = srv
    file_note(c, "幫衣服寫名字，5分鐘")
    blob = (json.dumps(c.get("/export.json").get_json(), ensure_ascii=False)
            + visible(c, "/records"))
    for banned in ("medical record", "medical history", "objective evidence",
                   "audit-grade", "labour exposure", "absolute privacy",
                   "客觀證據", "呈堂證供", "病歷", "客觀證明"):
        assert banned.lower() not in blob.lower(), banned
    assert "使用者自行陳述的活動紀錄" in blob or "self-reported activity record" in blob


def test_the_record_page_does_not_promise_that_nobody_else_can_ever_look(srv):
    s, c = srv
    page = visible(c, "/records?lang=zh-Hant")
    assert "只有你" not in page.replace(" ", "")
    assert "永遠" not in page
    # The honest caveat is present, phrased for whichever store is actually wired.
    assert "在技術上仍可能存取" in page


def test_the_shipped_client_has_no_drive_authorisation_path(srv):
    _, c = srv
    js = c.get("/static/auth.js").get_data(as_text=True)
    assert "requestDriveAccess" not in js
    assert "reauthenticateWithPopup" not in js
    assert "reauthenticateWithRedirect" not in js
    assert "mallow_drive_redirect" not in js
    for template in ("index.html", "records.html"):
        page = (MOBILE / "templates" / template).read_text()
        assert "/export/drive" not in page
        assert 'id="drive"' not in page


def test_the_retired_drive_feature_has_no_server_or_exporter_path(srv):
    import export as exporter
    _, c = srv
    assert c.post("/export/drive").status_code == 404
    for retired in ("save_to_drive", "drive_configured", "DriveUnavailable",
                    "DRIVE_SCOPE", "DRIVE_UPLOAD", "build_multipart"):
        assert not hasattr(exporter, retired), retired
    src = (MOBILE / "server.py").read_text() + (MOBILE / "export.py").read_text()
    for retired in ("/export/drive", "MALLOW_DRIVE_ENABLED",
                    "googleapis.com/upload/drive", "drive.file"):
        assert retired not in src


def test_nothing_leaves_without_being_asked(srv):
    """
    There is a scheduled task now, and it still sends nothing anywhere.

    It writes into the person's own workspace and stops. No mail, no webhook,
    no recipient and no third-party export destination.
    """
    src = "".join((MOBILE / f).read_text() for f in
                  ("server.py", "export.py", "identity.py", "workspaces.py",
                   "reflection.py", "tasks.py"))
    for outbound in ("smtplib", "sendgrid", "mailto:", "APScheduler",
                     "schedule.every", "BackgroundTasks", "webhook"):
        assert outbound not in src
    assert (MOBILE / "reflection.py").read_text().count("import requests") == 0


# ============================ public bootstrap ==============================
# A deployment that requires a token must still let a stranger reach the front
# door, or the sign-in button can never appear — a lock with the key inside.

def test_the_front_door_and_the_auth_config_stay_public(monkeypatch):
    monkeypatch.setenv("REQUIRE_FIREBASE_AUTH", "1")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    s = fresh(); c = s.app.test_client()
    assert c.get("/").status_code == 200
    cfg = c.get("/auth/config")
    assert cfg.status_code == 200
    assert cfg.get_json()["signed_in"] is False
    assert cfg.get_json()["auth_required"] is True
    for public in ("/manifest.webmanifest", "/sw.js", "/static/auth.js",
                   "/art/rabbit_idle.webp"):
        assert c.get(public).status_code == 200, public
    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_everything_private_still_refuses(monkeypatch):
    monkeypatch.setenv("REQUIRE_FIREBASE_AUTH", "1")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    s = fresh(); c = s.app.test_client()
    records = c.get("/records")
    assert records.status_code == 302
    assert "next=records" in records.headers["Location"]
    for private in ("/whoami", "/export.json", "/export.csv",
                    "/export.pdf", "/garden", "/voice/state", "/voice/records"):
        assert c.get(private).status_code == 401, private
    assert c.post("/voice/text", json={"capture_id": "x", "note": "y"}).status_code == 401
    assert c.post("/garden/seen").status_code == 401
    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_a_signed_out_visitor_is_offered_both_ways_in(monkeypatch):
    monkeypatch.setenv("REQUIRE_FIREBASE_AUTH", "1")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    s = fresh(); c = s.app.test_client()
    html = c.get("/?lang=zh-Hant").get_data(as_text=True)
    assert 'id="gate"' in html and "用 Google 登入" in html and "先看看就好" in html
    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


# ============================== storage truth ===============================
def test_the_page_does_not_promise_cross_device_until_firestore_is_wired(srv):
    s, c = srv
    assert s.storage() == "local-file" and s.cross_device() is False
    page = visible(c, "/records?lang=zh-Hant")
    assert "可以在其他裝置取回紀錄" not in page
    assert "Firestore" in page and "尚未接上 Firestore" in page


def test_the_backend_name_comes_from_the_store_not_from_a_variable(srv, monkeypatch):
    """
    The failure this guards against: a variable saying firestore while a file
    journal serves every request, and the page promising cross-device recovery
    that does not exist. Same shape as the model region caught in V2.
    """
    s, c = srv
    assert s.storage() == "local-file"
    assert c.get("/auth/config?lang=zh-Hant").get_json()["cross_device"] is False
    assert "可以在其他裝置取回紀錄" not in visible(c, "/records?lang=zh-Hant")

    # Naming firestore and not getting one must stop the process, not downgrade.
    monkeypatch.setenv("MALLOW_FIRESTORE", "1")
    with pytest.raises(Exception):
        fresh()
    monkeypatch.delenv("MALLOW_FIRESTORE")


def test_no_records_page_advertises_drive(srv):
    _, c = srv
    page = c.get("/records?lang=zh-Hant").get_data(as_text=True)
    assert 'id="drive"' not in page
    assert "存到 Google Drive" not in page
    assert "登入 Google 後可儲存至 Drive" not in page


def test_pdf_csv_and_json_remain_after_drive_is_retired(srv):
    _, c = srv
    page = c.get("/records?lang=en").get_data(as_text=True)
    for target in ("/export.pdf?lang=en", "/export.csv", "/export.json"):
        assert target in page


# ============================== browser-level ===============================
def test_firebase_is_initialised_at_most_once(srv):
    """
    Boot and a sign-in press share one Firebase app. Executed in a real browser
    engine, because a Python test cannot catch duplicate-app.
    """
    js = (MOBILE / "static" / "auth.js").read_text()
    assert "getApps()" in js and js.count("initializeApp") <= 2
    assert "sdk = null" in js                       # a failure must not stick


def test_signing_in_never_falls_back_to_the_redirect(srv):
    """
    🔴 This test used to require the opposite, and requiring it was the defect.

    The sign-in redirect cannot work here: Mallow is served from a Cloud Run
    origin while `authDomain` is `<project>.firebaseapp.com`, and a browser that
    blocks third-party storage — Safari 16.1+ — cannot recover the redirect
    helper's state on the way back. Owner saw exactly that: Google authorised,
    the browser came back, and the gate was still on screen.

    Falling back to a path known to be broken is not a fallback. It is a slower
    way to fail, and it fails *after* the person has already given Google their
    consent. So sign-in is a popup on every browser, and this asserts the
    redirect cannot creep back into that path.

    The anonymous workspace has no in-place linking path, and the retired
    Drive feature has no reauthentication redirect of its own.
    """
    js = (MOBILE / "static" / "auth.js").read_text()
    assert "signInWithRedirect" not in js
    assert "signInWithPopup" in js
    assert "linkWithPopup" not in js
    assert "reauthenticateWithRedirect" not in js
    assert "getRedirectResult" not in js


# ================================ exports ===================================
def test_the_export_font_covers_both_alphabets(srv):
    """
    A record is Chinese next to English labels and digits, drawn in one font.
    The first font tried here rendered the Chinese and dropped every Latin
    character, which a render check caught and this test now prevents.
    """
    import export as exporter
    from reportlab.pdfbase.ttfonts import TTFont
    assert exporter.register_cjk_font()
    latin_only = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(latin_only):
        assert not exporter.covers(TTFont("probe-latin", latin_only))


def test_the_export_refuses_rather_than_dropping_text(srv, monkeypatch):
    import export as exporter
    from reportlab.pdfbase import pdfmetrics
    monkeypatch.setattr(exporter, "CJK_FONTS", ())
    monkeypatch.setattr(pdfmetrics, "getRegisteredFontNames", lambda: [])
    with pytest.raises(exporter.FontMissing):
        exporter.register_cjk_font()


def test_the_pdf_embeds_a_real_face(srv):
    import export as exporter
    data = exporter.to_pdf([{"source_text": "幫全部衣服寫名字，35分鐘",
                             "activity_text": "labelling, tidying or preparing ahead",
                             "policy_result": "grass", "duration_minutes": 35,
                             "recorded_at": "2026-08-22T23:10:04+09:00",
                             "provenance": "asserted", "review_status": "active"}])
    assert data[:5] == b"%PDF-"
    assert len(data) > 5000          # a subset of a real font is in the file


def test_the_pdf_carries_no_emoji(srv):
    """STSong-Light is a CID font with no pictographs: an emoji would be a box."""
    import export as exporter
    src = (MOBILE / "export.py").read_text()
    body = src.split("def to_pdf")[1].split("def ")[0]
    assert not any(ord(ch) > 0x1F000 for ch in body)


def test_pdf_escapes_every_field_not_only_the_persons_words(srv):
    import export as exporter
    rows = [{"source_text": "<b>粗體</b> & 「引號」",
             "activity_text": "<i>from a model</i>",
             "policy_result": "grass", "duration_minutes": 35,
             "recorded_at": "2026-08-22T00:00:00+09:00",
             "provenance": "asserted & checked", "review_status": "active"}]
    data = exporter.to_pdf(rows)
    assert data[:5] == b"%PDF-"
    assert exporter.esc("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"


def test_csv_does_not_hand_a_spreadsheet_a_formula(srv):
    import export as exporter
    rows = [{"source_text": "=1+1", "activity_text": "+SUM(A1)",
             "transcript": "@cmd", "labour_kind": "-danger"}]
    out = exporter.to_csv(rows)
    for cell in ("'=1+1", "'+SUM(A1)", "'@cmd", "'-danger"):
        assert cell in out
    assert exporter.defuse("ordinary text") == "ordinary text"


# ====================== scheduled reflections ===============================
# The only thing in this product that happens with nobody watching, and so the
# only thing whose correctness cannot be checked by using the app. Everything
# below is about one question: does it stay quiet when it should?

def test_a_due_period_with_no_new_records_gets_no_leaf(srv):
    """A chosen time is not permission to invent content."""
    s, c = srv
    ws = seed(srv, day_offsets=())
    assert run_task(c).get_json()["written"] == 0
    assert c.get("/garden").get_json()["leaf"] is None
    assert s.reflection._parse(ws.preferences.read()["next_reflection_at"]) \
        > s.reflection.now_jst()


def test_one_new_record_is_enough_when_the_period_is_due(srv):
    """Cadence and content are separate: one honest record is still content."""
    s, c = srv
    seed(srv, day_offsets=(1,))
    assert run_task(c).get_json()["written"] == 1
    assert c.get("/garden").get_json()["leaf"] is not None


def test_several_notes_from_one_day_are_still_eligible_content(srv):
    s, c = srv
    seed(srv, day_offsets=(1, 1, 1, 1, 1))
    assert run_task(c).get_json()["written"] == 1
    assert c.get("/garden").get_json()["leaf"] is not None


def test_records_older_than_the_window_do_not_count(srv):
    s, c = srv
    seed(srv, day_offsets=(9, 10, 11, 12, 13))     # all outside seven days
    assert run_task(c).get_json()["written"] == 0


def test_a_week_of_pure_mental_load_still_gets_a_leaf(srv):
    """
    Duration is optional and classification is semantic. Eligibility never
    treats a missing duration as a negative signal.
    """
    s, c = srv
    seed(srv, minutes=None, food="carrot")
    for r in s.workspaces.for_uid(c.get("/whoami").get_json()["uid"]).ledger.ordered():
        assert r["duration_minutes"] is None
    assert run_task(c).get_json()["written"] == 1
    assert c.get("/garden").get_json()["leaf"] is not None


def test_the_meadow_keeps_only_the_five_newest_leaf_tokens(srv):
    s, c = srv
    ws = s.workspaces.for_uid(c.get("/whoami").get_json()["uid"])
    for i in range(6):
        sid = f"s{i}"
        summary = {"summary_id": sid, "created_at": f"2026-08-0{i + 1}T09:00:00+09:00",
                   "reflection": f"reflection {i}", "reflection_zh": f"回顧 {i}"}
        leaf = {"leaf": {"summary_id": sid, "body": f"reflection {i}",
                           "body_zh": f"回顧 {i}"},
                "last_summary_at": summary["created_at"]}
        assert ws.commit_reflection(sid, summary, leaf)
    garden = c.get("/garden").get_json()
    assert [leaf["summary_id"] for leaf in garden["leaves"]] == [
        "s1", "s2", "s3", "s4", "s5"]
    assert len(ws.summaries) == 6       # the sixth token never deletes history


def test_putting_away_one_leaf_keeps_the_other_tokens_and_the_summary(srv):
    s, c = srv
    ws = s.workspaces.for_uid(c.get("/whoami").get_json()["uid"])
    for sid in ("s1", "s2"):
        summary = {"summary_id": sid,
                   "created_at": f"2026-08-25T0{sid[-1]}:00:00+09:00",
                   "reflection": sid, "reflection_zh": sid}
        ws.commit_reflection(sid, summary, {
            "leaf": {"summary_id": sid, "body": sid, "body_zh": sid},
            "last_summary_at": summary["created_at"]})

    out = c.post("/garden/seen", json={"summary_id": "s1"})
    assert out.status_code == 200 and out.get_json()["put_away"] is True
    assert [leaf["summary_id"] for leaf in c.get("/garden").get_json()["leaves"]] == ["s2"]
    assert set(ws.summaries) == {"s1", "s2"}


def test_a_second_leaf_waits_for_the_next_chosen_period(srv):
    from datetime import timedelta
    s, c = srv
    seed(srv)
    assert run_task(c).get_json()["written"] == 1
    assert c.post("/garden/seen").status_code == 200

    ws = s.workspaces.for_uid(c.get("/whoami").get_json()["uid"])
    assert ws.garden.read()["seen_at"]
    assert ws.garden.read()["leaves"] == []
    assert run_task(c).get_json()["written"] == 0, "read, but not due again"

    # Move the clock past the gap and it is allowed again — with new records,
    # because the old ones have aged out of the window by then.
    later = s.reflection.now_jst() + timedelta(days=8)
    for i in range(5):
        ws.ledger[f"n{i}"] = a_record(f"n{i}", later - timedelta(days=i + 1))
    assert s.reflection.run_for(ws, now=later,
                                writer=s.reflection.deterministic) is not None


def test_the_leaf_is_a_description_and_never_a_finding(srv):
    """
    The whole risk of this feature in one test. A weekly note that told somebody
    they were overloaded would be a diagnosis from a keyword table, and this
    product does not get to make one.
    """
    s, c = srv
    seed_week(srv)
    body = c.get("/garden").get_json()["leaf"]["body"]
    for claim in s.reflection.FORBIDDEN:
        assert claim.lower() not in body.lower(), claim


def test_a_summary_citing_a_record_that_does_not_exist_is_thrown_away(srv):
    """
    A note naming a record this person does not have is a hallucination or
    somebody else's data. There is no version of either that is safe to keep,
    so it is discarded rather than trimmed to the ids that did match.
    """
    s, c = srv
    ws = seed(srv)

    # 🔴 2026-08-25: `reflection_zh` was missing here, so this test passed by
    # being rejected for the wrong field — validate() checks both texts before
    # it ever looks at the ids. Deleting the citation check outright left this
    # test green. Fourth fake test found in this repository, and it was
    # standing in front of the most important rule on this path.
    def liar(pack):
        return {"reflection": "A description of the week.",
                "reflection_zh": "這一週的描述。",
                "cited_record_ids": list(pack["record_ids"]) + ["not-a-real-id"]}

    with pytest.raises(s.reflection.ReflectionRejected):
        s.reflection.run_for(ws, writer=liar)
    assert len(ws.summaries) == 0
    assert ws.garden.read().get("leaf") is None


def test_a_summary_that_diagnoses_is_thrown_away(srv):
    s, c = srv
    ws = seed(srv)

    def overreacher(pack):
        return {"reflection": "This describes the period. You are exhausted and should see a doctor.",
                "reflection_zh": "這是一段普通描述。這裡只保留紀錄。",
                "cited_record_ids": list(pack["record_ids"])}

    with pytest.raises(s.reflection.ReflectionRejected) as caught:
        s.reflection.run_for(ws, writer=overreacher)
    assert "forbidden wording" in str(caught.value)
    assert len(ws.summaries) == 0


def test_a_summary_that_quantifies_the_week_is_thrown_away(srv):
    s, c = srv
    ws = seed(srv)

    def counter(pack):
        return {"reflection": "This describes the period. You spent 14 hours on this.",
                "reflection_zh": "這是一段普通描述。這裡只保留紀錄。",
                "cited_record_ids": list(pack["record_ids"])}

    with pytest.raises(s.reflection.ReflectionRejected) as caught:
        s.reflection.run_for(ws, writer=counter)
    assert "quantified" in str(caught.value)


def test_the_model_never_sees_the_persons_own_sentences(srv):
    """
    The pack carries counts and the labels Mallow wrote. Not the words. A
    weekly note is a place someone might show a professional, and quoting a
    sentence said in a bad moment into it is not a decision a model gets.
    """
    s, c = srv
    ws = seed(srv)
    pack = s.reflection.eligible(ws).pack
    blob = json.dumps(pack, ensure_ascii=False)
    assert "wrote names on everything" not in blob
    assert "source_text" not in blob and "transcript" not in blob
    assert pack["counts_by_domain"] == {"clothing_laundry": len(pack["record_ids"])}


def test_the_summary_is_labelled_as_the_agents_reading_and_not_a_fact(srv):
    s, c = srv
    seed_week(srv)
    ws = s.workspaces.for_uid(c.get("/whoami").get_json()["uid"])
    summary = ws.summaries.latest()
    assert summary["provenance"] == "inferred"
    assert summary["generated_by"] == "scheduled-task"
    assert summary["writer"] == "deterministic"      # honest about who wrote it
    assert set(summary["cited_record_ids"]) <= set(r["record_id"]
                                                   for r in ws.ledger.ordered())


def test_a_summary_is_written_once(srv):
    s, c = srv
    ws = seed(srv)
    first = s.reflection.run_for(ws, writer=s.reflection.deterministic)
    with pytest.raises(Exception):
        ws.summaries[first["summary_id"]] = {**first, "reflection": "rewritten"}


# ============================ the task door =================================
def test_a_person_cannot_run_the_scheduled_task(srv):
    """
    🔒 The one that matters. A Firebase ID token is a real credential for
    reading your own records; it must be no credential at all here, because
    this endpoint walks every workspace in the deployment.
    """
    s, c = srv
    assert c.post("/tasks/weekly-reflection").status_code == 403
    assert c.post("/tasks/weekly-reflection",
                  headers={"X-Mallow-Task-Key": "guessed"}).status_code == 403
    assert c.post("/tasks/weekly-reflection",
                  headers={"Authorization": "Bearer a-users-id-token"}).status_code == 403


def test_the_task_door_does_not_consult_identity(srv):
    """
    Two doors, two keys, no shared hallway — asserted in the source, because
    the day somebody reaches for `identity.current()` in here to "reuse the
    verification" is the day a user token starts working on this endpoint.
    """
    code = [l for l in (MOBILE / "tasks.py").read_text().splitlines()
            if not l.lstrip().startswith("#")]
    body = "\n".join(code).split('"""')[-1]
    assert "import identity" not in body
    assert "identity." not in body


def test_the_shared_key_stops_working_once_a_service_account_is_named(monkeypatch):
    """
    A local convenience must not survive into production as a second way in.
    Naming the service account turns the key off, rather than adding to it.
    """
    monkeypatch.setenv("TASKS_SERVICE_ACCOUNT", "mallow-tasks@example.iam.gserviceaccount.com")
    monkeypatch.setenv("TASKS_AUDIENCE", "https://mallow.example.run.app")
    s = fresh(); c = s.app.test_client()
    r = c.post("/tasks/weekly-reflection", headers={"X-Mallow-Task-Key": "test-task-key"})
    assert r.status_code == 403
    monkeypatch.delenv("TASKS_SERVICE_ACCOUNT")
    monkeypatch.delenv("TASKS_AUDIENCE")


def test_the_task_endpoint_is_off_rather_than_open_when_unconfigured(monkeypatch):
    """Neither credential configured: the door is shut, not left ajar."""
    monkeypatch.setenv("MALLOW_TASK_KEY", "")
    s = fresh(); c = s.app.test_client()
    assert c.post("/tasks/weekly-reflection").status_code == 503
    monkeypatch.setenv("MALLOW_TASK_KEY", "test-task-key")


def test_one_broken_workspace_does_not_stop_the_run(srv):
    s, c = srv
    seed(srv)

    def explode(pack):
        raise RuntimeError("the model fell over")

    import reflection as R
    original = R.ask_gemini
    R.ask_gemini = explode
    try:
        out = run_task(c).get_json()           # not demo-writer: uses ask_gemini
    finally:
        R.ask_gemini = original
    assert out["considered"] >= 1


def test_the_app_offers_no_way_to_ask_for_a_leaf(srv):
    """
    Judges are being shown autonomy. A button that makes a leaf would make the
    demonstration worthless, so there is not one — in the page or the routes.
    """
    _, c = srv
    for page in ("/", "/records"):
        html = visible(c, page).lower()
        for control in ("generate", "產生葉子", "make a leaf", "refresh leaf"):
            assert control not in html
    assert c.post("/garden").status_code == 405


# ========================= firestore, against a double ======================
def test_the_firestore_adapter_keeps_the_same_rules(srv):
    import firestore_store as fs
    from ledger import HistoryRewrite
    ws = fs.FirestoreWorkspace("u1", fs.InMemoryFirestore())

    row = a_record("r1", __import__("datetime").datetime.now())
    ws.ledger["r1"] = row
    assert ws.ledger["r1"]["source_text"] == row["source_text"]

    with pytest.raises(HistoryRewrite):
        ws.ledger["r1"] = {**row, "source_text": "something else"}
    ws.ledger["r1"] = {**row, "review_status": "cancelled"}       # allowed
    with pytest.raises(HistoryRewrite):
        del ws.ledger["r1"]


def test_the_firestore_adapter_files_a_capture_once_however_many_times_it_arrives(srv):
    """
    Transaction replay. The capture id is read inside the transaction and
    before any write, so a retried upload writes nothing the second time —
    rather than a second set of records under a second set of ids.
    """
    import firestore_store as fs
    from datetime import datetime
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("u1", client)

    rows = {"r1": a_record("r1", datetime.now())}
    receipt = {"capture_id": "cap1", "items": [{"record_id": "r1"}]}

    ws.commit("cap1", receipt, rows)
    ws.commit("cap1", receipt, rows)                      # the retry
    ws.commit("cap1", {**receipt, "items": []}, {"r2": a_record("r2", datetime.now())})

    assert sorted(ws.ledger.keys()) == ["r1"], "the retry filed a second record"
    assert len(ws.captures) == 1
    assert client.transactions == 3, "every commit went through a transaction"


def test_two_uids_cannot_see_each_other_on_firestore(srv):
    import firestore_store as fs
    from datetime import datetime
    client = fs.InMemoryFirestore()
    a = fs.FirestoreWorkspace("uid-a", client)
    b = fs.FirestoreWorkspace("uid-b", client)
    a.ledger["r1"] = a_record("r1", datetime.now(), text="a's note")
    assert list(b.ledger) == []
    # Both workspaces have content-free parent manifests so the Scheduler can
    # discover them. Their record subcollections remain isolated.
    assert client.list_ids("users") == ["uid-a", "uid-b"]


def test_firestore_reports_itself_and_demo_never_runs_against_it():
    import firestore_store as fs
    reg = fs.FirestoreRegistry(client=fs.InMemoryFirestore())
    assert reg.backend == "firestore" and reg.cross_device is True
    with pytest.raises(fs.FirestoreUnavailable):
        fs.FirestoreRegistry(client=fs.InMemoryFirestore(), suffix="-demo")


# ================================ two languages =============================
def test_english_and_chinese_are_the_same_product(srv):
    """
    One template, one pipeline. The furniture changes; the record does not.
    If these ever diverge it will be because somebody copied a template.
    """
    s, c = srv
    zh = c.post("/voice/text", json={"capture_id": "z1", "note": "幫衣服寫名字，5分鐘"}).get_json()
    en = c.post("/voice/text?lang=en",
                json={"capture_id": "e1",
                      "note": "labelled all the clothes, 5 minutes"}).get_json()
    for side in (zh, en):
        assert side["items"] and side["items"][0]["food"] == "grass"
        assert side["items"][0]["duration_minutes"] == 5
        assert side["audio_persisted"] is False


def test_the_rabbit_answers_in_the_language_of_the_page(srv):
    _, c = srv
    receipt = {"items": [{"food": "grass", "duration_minutes": 5, "source": "x"}],
               "withheld_fragments": 0}
    assert "收好了" in c.post("/say?lang=zh-Hant", json=receipt).get_json()["line"]
    assert "Kept" in c.post("/say?lang=en", json=receipt).get_json()["line"]


def test_the_english_surface_is_actually_english(srv):
    _, c = srv
    html = visible(c, "/?lang=en")
    assert 'lang="en"' in html
    for phrase in ("Say something", "Records", "Sign in with Google", "Just look around"):
        assert phrase in html, phrase
    # No stray Chinese furniture left behind in the English page.
    body = html.split("<body")[1]
    for leftover in ("說點什麼", "紀錄", "用 Google 登入", "先看看就好"):
        assert leftover not in body, leftover


def test_the_english_record_page_is_actually_english(srv):
    _, c = srv
    file_note(c, "幫衣服寫名字，5分鐘")
    page = visible(c, "/records?lang=en")
    assert "Your records" in page and "Download PDF" in page
    assert "self-reported activity record" in page
    # 🔴 The person's own sentence is data, and is printed exactly as it was
    # said — in the English page too.
    assert "幫衣服寫名字，5分鐘" in page


def test_the_language_choice_is_remembered(srv):
    _, c = srv
    c.get("/?lang=en")
    assert "Say something" in visible(c, "/"), "the choice did not survive a click"


def test_both_languages_are_reachable_from_the_page(srv):
    _, c = srv
    assert 'id="langSwitch"' in c.get("/").get_data(as_text=True)
    assert "lang=en" in c.get("/").get_data(as_text=True)
    assert "lang=zh-Hant" in c.get("/?lang=en").get_data(as_text=True)


def test_a_missing_string_fails_here_rather_than_in_front_of_a_judge(srv):
    import i18n
    with pytest.raises(KeyError):
        i18n.t("no_such_string", "en")
    # Every key carries both languages, so neither page can fall back silently.
    for key, entry in i18n.STRINGS.items():
        assert set(entry) == {"zh-Hant", "en"}, key
        assert entry["en"].strip() and entry["zh-Hant"].strip(), key


def test_the_english_pdf_keeps_the_persons_words_and_the_exact_claim(srv):
    s, c = srv
    file_note(c, "幫衣服寫名字，5分鐘")
    assert c.get("/export.pdf?lang=en").status_code == 200
    import export as exporter
    assert exporter.CLAIM == ("Append-only by application policy, "
                              "with traceable corrections.")


# ====================== the surface says nothing it cannot ==================
def test_no_diagnosis_or_alert_vocabulary_reaches_the_product_surface(srv):
    """
    The retired threat model, and the new risk that replaced it. A weekly note
    is one careless adjective away from reading as a medical claim.
    """
    s, c = srv
    seed_week(srv)
    surface = ""
    for page in ("/", "/records", "/?lang=en", "/records?lang=en"):
        surface += visible(c, page)
    surface += json.dumps(c.get("/garden").get_json(), ensure_ascii=False)
    surface += json.dumps(c.get("/garden?lang=en").get_json(), ensure_ascii=False)
    surface += json.dumps(c.get("/export.json").get_json(), ensure_ascii=False)

    for banned in ("support brief", "help document", "threat model", "attacker",
                   "coercive", "surveillance", "burnout", "overloaded",
                   "high workload", "at risk", "alert", "warning", "diagnos",
                   "求助文件", "訴訟", "過勞", "警告", "異常", "診斷"):
        assert banned.lower() not in surface.lower(), banned


def test_the_release_gate_cannot_report_green_while_a_suite_did_not_run(srv):
    """
    🔴 Two separate ways this used to lie, both closed here.

    The browser suite sat behind `pytest.importorskip`, so a machine with no
    engine skipped it — and a skip reads as a pass in the summary line anybody
    actually looks at. And the gate ended that line with `|| echo`, so even a
    real failure exited 0.

    A gate that treats "did not run" as "passed" is worse than no gate, because
    people trust it.
    """
    sh = (MOBILE.parent / "run.sh").read_text()
    gate = sh.split("  test)")[1].split("  seed-demo)")[0]
    code = "\n".join(l for l in gate.splitlines() if not l.lstrip().startswith("#"))

    assert "mobile/tests/browser" in code
    # `||` is fine on a probe (`have_browser || { … exit 1 }`). It is not fine
    # on the line that runs a suite, which is where it hid a red result before.
    for line in code.splitlines():
        if "pytest" in line:
            assert "||" not in line, f"a suite's result is swallowed: {line.strip()}"
    assert "have_browser" in code and "exit 1" in code, \
        "the gate must probe for the engine and stop, not narrow itself"

    browser_src = (MOBILE / "tests" / "browser" / "test_auth_js.py").read_text()
    browser_code = "\n".join(l for l in browser_src.splitlines()
                             if not l.lstrip().startswith("#"))
    assert "importorskip" not in browser_code, \
        "a missing engine must fail collection, not skip"
    assert "from playwright.sync_api import" in browser_code

    # And the honest partial run has to say what it left out.
    partial = sh.split("  test-python)")[1].split("  test)")[0]
    assert "NOT RUN" in partial


def test_the_gate_stops_when_the_browser_engine_is_missing(srv, tmp_path):
    """
    Not asserted by reading the script — actually run it with the engine
    hidden, because the claim is about an exit code.
    """
    import subprocess
    shim = tmp_path / "playwright"
    shim.mkdir()
    (shim / "__init__.py").write_text("raise ImportError('hidden for this test')")

    out = subprocess.run(["bash", "run.sh", "test"], cwd=MOBILE.parent,
                         env={**os.environ, "PYTHONPATH": str(tmp_path)},
                         capture_output=True, text=True, timeout=300)
    assert out.returncode != 0, "the gate reported success without the browser suite"
    assert "playwright" in (out.stdout + out.stderr).lower()


def test_the_leaf_is_written_in_both_languages_at_once(srv):
    """
    A scheduled job has no reader to ask. Both versions are produced in the
    same model call and checked by the same guard, so neither page shows a leaf
    in a language the person holding the phone did not choose.
    """
    s, c = srv
    seed_week(srv)
    zh = c.get("/garden?lang=zh-Hant").get_json()["leaf"]
    en = c.get("/garden?lang=en").get_json()["leaf"]
    assert zh["title"] == "一片摺好的葉子" and en["title"] == "A folded leaf"
    assert zh["body"] != en["body"]
    assert re.search(r"[一-鿿]", zh["body"]), "the Chinese leaf is not Chinese"
    assert not re.search(r"[一-鿿]", en["body"]), "the English leaf is not English"


def test_a_summary_missing_one_language_is_thrown_away(srv):
    s, c = srv
    ws = seed(srv)

    def half(pack):
        return {"reflection": "A description of the week.",
                "cited_record_ids": list(pack["record_ids"])}

    with pytest.raises(s.reflection.ReflectionRejected):
        s.reflection.run_for(ws, writer=half)
    assert len(ws.summaries) == 0


def test_the_vocabulary_guard_applies_to_the_chinese_half_too(srv):
    """
    A guard that only reads the English half is a guard with a door in it —
    and the person most likely to read the Chinese one is the owner.
    """
    s, c = srv
    ws = seed(srv)

    def sneaky(pack):
        return {"reflection": "A quiet description of the week.",
                "reflection_zh": "你這週明顯過勞了，建議去看醫生。",
                "cited_record_ids": list(pack["record_ids"])}

    with pytest.raises(s.reflection.ReflectionRejected):
        s.reflection.run_for(ws, writer=sneaky)
    assert len(ws.summaries) == 0
    assert ws.garden.read().get("leaf") is None


# ═══════════════════ the production boundary (CodeX review, 2026-08-23) ══════
# Everything below fails on the version reviewed that morning. The suite was
# 180 green and the app was unusable signed in, because every test ran in local
# mode — where a cookie already existed and the boundary was never crossed.

# ---------------------------------------------------- P0-1 · navigation ------
def test_a_signed_in_person_can_reach_their_own_records_by_clicking(monkeypatch):
    """
    🔴 The one that made the product unusable in production.

    A Firebase ID token can only be attached by script, to a fetch. Clicking
    "records" is a navigation: no script, no header, no token. Before the
    session cookie this returned 401 — and no test noticed, because the tests
    ran in local mode where a cookie was already there.
    """
    s, c = firebase_mode(monkeypatch)
    auth = {"Authorization": "Bearer good-token"}

    # Signed out, a page navigation returns to the public gate instead of
    # printing an API-shaped 401 in the browser. No private data is rendered.
    signed_out = c.get("/records?lang=en")
    assert signed_out.status_code == 302
    assert signed_out.headers["Location"].endswith("/?lang=en&next=records")

    # Sign in the way the page does, then click things the way a person does.
    assert c.post("/auth/session", headers=auth).status_code == 200
    for clicked in ("/records", "/export.pdf", "/export.csv", "/export.json",
                    "/garden", "/whoami"):
        assert c.get(clicked).status_code == 200, clicked

    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_the_navigation_session_cannot_be_used_to_write(monkeypatch):
    """
    The cookie buys reading, and only reading.

    A cookie that also authorised writes would be a CSRF hole: another site
    cannot read a bearer token out of this page, but it can make a browser
    send a cookie. So writes keep needing the token the page holds in memory.
    """
    s, c = firebase_mode(monkeypatch)
    auth = {"Authorization": "Bearer good-token"}
    c.post("/auth/session", headers=auth)

    assert c.get("/records").status_code == 200            # reading: yes
    for written in ("/garden/seen",):
        assert c.post(written).status_code == 401, written  # writing: no
    assert c.post("/voice/text",
                  json={"capture_id": "x", "note": "y"}).status_code == 401

    # The same writes work with the token the page actually has.
    assert c.post("/garden/seen", headers=auth).status_code == 200

    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_a_forged_session_cookie_is_refused(monkeypatch):
    s, c = firebase_mode(monkeypatch)
    c.set_cookie("mallow_session", "somebody-elses-uid")
    records = c.get("/records")
    assert records.status_code == 302
    assert "next=records" in records.headers["Location"]
    # API/export boundaries do not become redirects or readable data.
    assert c.get("/export.json").status_code == 401
    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_records_gate_returns_to_records_after_sign_in(monkeypatch):
    s, c = firebase_mode(monkeypatch)
    page = c.get("/?lang=zh-Hant&next=records").get_data(as_text=True)
    assert 'const AUTH_RETURN_TO = "/records?lang=zh-Hant";' in page
    assert "location.assign(AUTH_RETURN_TO)" in page
    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_signed_in_account_copy_can_name_the_actual_email(srv):
    s, _ = srv
    for lang in ("en", "zh-Hant"):
        text = s.i18n.STRINGS["account_signed_as"][lang]
        assert "{email}" in text
        assert "account_signed_as" in s.i18n.SCRIPT_KEYS


def test_a_session_secret_is_required_once_firebase_is_configured(monkeypatch):
    """
    Cloud Run runs more than one instance. A per-process random secret would
    mean a cookie minted by one and rejected by the next — people signed out at
    random, looking like a product bug rather than a configuration one.
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("MALLOW_SESSION_SECRET", "")
    with pytest.raises(RuntimeError, match="MALLOW_SESSION_SECRET"):
        fresh()
    monkeypatch.delenv("FIREBASE_PROJECT_ID")
    os.environ["MALLOW_SESSION_SECRET"] = "test-session-secret"


def test_the_page_exchanges_its_token_for_a_session(srv):
    """The browser side of the same fix, asserted in the source."""
    js = (MOBILE / "static" / "auth.js").read_text()
    assert "/auth/session" in js
    for page in ("index.html", "records.html"):
        assert "startSession" in (MOBILE / "templates" / page).read_text(), page


def test_anonymous_records_exits_instead_of_linking_an_account(srv):
    page = (MOBILE / "templates" / "records.html").read_text()
    handler = page.split('if ($("#leaveAnonymous"))', 1)[1]
    handler = handler.split('if ($("#signout"))', 1)[0]
    assert "await window.Mallow.signOut()" in handler
    assert "linkWithGoogle" not in page
    assert "signInWithGoogle" not in handler


def test_anonymous_records_only_offers_the_exit(srv):
    _, c = srv
    english = visible(c, "/records?lang=en").split('<div class="who">', 1)[1].split("</div>", 1)[0]
    chinese = visible(c, "/records?lang=zh-Hant").split('<div class="who">', 1)[1].split("</div>", 1)[0]

    assert "Exit anonymous mode" in english
    assert "Sign in with Google" not in english
    assert "temporary workspace" not in english.lower()
    assert "退出匿名模式" in chinese
    assert "用 Google 登入" not in chinese
    assert "暫時空間" not in chinese


def test_signout_failure_copy_exists_in_both_languages():
    import i18n
    for lang in ("zh-Hant", "en"):
        assert i18n.STRINGS["auth_signout_failed"][lang].strip()
    assert "auth_signout_failed" in i18n.SCRIPT_KEYS


def test_restored_identity_does_not_discard_the_session_result():
    auth = (MOBILE / "static" / "auth.js").read_text()
    meadow = (MOBILE / "templates" / "index.html").read_text()
    assert "const sessionReady = await startSession()" in auth
    assert "return {...state, sessionReady}" in auth
    assert 'state.sessionReady === false' in meadow
    assert "authRetryable = true" in meadow
    assert "if(sessionFailed) await openSettings()" in meadow


def test_signout_cannot_reload_before_the_navigation_cookie_is_cleared():
    auth = (MOBILE / "static" / "auth.js").read_text()
    assert "sessionCleared = r.ok" in auth
    assert "if (!sessionCleared) throw" in auth
    for name in ("index.html", "records.html"):
        page = (MOBILE / "templates" / name).read_text()
        assert "auth_signout_failed" in page
        assert "await window.Mallow.signOut()" in page


# ------------------------------------------- P0-2 · Firestore transaction ----
def test_the_double_refuses_a_read_after_a_write(srv):
    """
    The double is what made the old commit look correct. Firestore requires
    every read before any write; the double allowed interleaving, so a method
    that could never run against the real service had a green test.

    This asserts the double now enforces the constraint — without it, the test
    below proves nothing.
    """
    import firestore_store as fs
    client = fs.InMemoryFirestore()

    def bad(txn):
        client.set("users/u/records/a", {"x": 1}, txn=txn)
        client.get("users/u/records/b", txn=txn)          # too late

    with pytest.raises(fs.ReadAfterWrite):
        client.atomic(bad)


def test_commit_reads_everything_before_it_writes_anything(srv):
    """
    Two records in one capture. The old loop read A, wrote A, read B — which
    real Firestore rejects. With the double enforcing the rule, that shape
    cannot pass here either.
    """
    import firestore_store as fs
    from datetime import datetime
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("u1", client)
    now = datetime.now()

    ws.commit("cap1", {"capture_id": "cap1"},
              {"r1": a_record("r1", now), "r2": a_record("r2", now),
               "r3": a_record("r3", now)})

    assert sorted(ws.ledger.keys()) == ["r1", "r2", "r3"]
    assert client.transactions == 1


def test_a_replay_gets_the_receipt_that_was_actually_filed(srv):
    """
    Two submissions of one recording each mint their own record ids. The loser
    used to be handed its own receipt — ids that are in no store — so the
    rabbit ate something that had never been filed.
    """
    import firestore_store as fs
    from datetime import datetime
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("u1", client)
    now = datetime.now()

    first = ws.commit("cap1", {"capture_id": "cap1", "items": [{"record_id": "r1"}]},
                      {"r1": a_record("r1", now)})
    second = ws.commit("cap1", {"capture_id": "cap1", "items": [{"record_id": "r9"}]},
                       {"r9": a_record("r9", now)})

    assert second == first, "the replay was given its own unfiled receipt"
    assert list(ws.ledger) == ["r1"]


def test_the_file_store_returns_the_canonical_receipt_too(srv):
    """Both backends, one behaviour. A rule that only holds on one is not a rule."""
    s, c = srv
    first = file_note(c, "幫衣服寫名字，5分鐘", capture="same-id")
    again = file_note(c, "完全不一樣的一句話", capture="same-id")
    assert again["items"] == first["items"]
    assert again["heard"] == first["heard"]


# --------------------------------------------- P0-4 · append-only loophole ---
def test_a_cancelled_record_cannot_have_its_words_edited(srv):
    """
    🔴 The hole in the middle of the append-only claim. `guard_record` returned
    early when the status was unchanged, so a row already at `cancelled` could
    be written again — same status, different `source_text`. A person's own
    words were editable, as long as you edited them twice.
    """
    from ledger import HistoryRewrite, guard_record
    cancelled = {"review_status": "cancelled", "source_text": "what they said"}

    guard_record(cancelled, dict(cancelled))              # an identical replay: fine
    with pytest.raises(HistoryRewrite):
        guard_record(cancelled, {**cancelled, "source_text": "something else"})
    with pytest.raises(HistoryRewrite):
        guard_record({"review_status": "superseded", "duration_minutes": 5},
                     {"review_status": "superseded", "duration_minutes": 500})


def test_the_loophole_is_closed_through_the_running_store_as_well(srv):
    """Not just the function — the path a request actually takes."""
    from ledger import HistoryRewrite
    s, c = srv
    receipt = file_note(c, "幫衣服寫名字，5分鐘")
    c.post("/voice/cancel", json={"capture_id": "c1"})

    rid = receipt["items"][0]["record_id"]
    ws = s.workspaces.for_uid(c.get("/whoami").get_json()["uid"])
    row = ws.ledger[rid]
    assert row["review_status"] == "cancelled"
    with pytest.raises(HistoryRewrite):
        ws.ledger[rid] = {**row, "source_text": "rewritten after the fact"}


# --------------------------------------------------- provider allow-list -----
def test_only_google_and_anonymous_are_accepted(monkeypatch):
    """
    `password`, `github.com`, a phone number, a custom token — all used to be
    relabelled "google". The allow-list must describe the real provider.
    """
    import importlib, sys as _s
    for provider in ("password", "github.com", "phone", "custom", ""):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
        monkeypatch.setenv("MALLOW_SESSION_SECRET", "test-session-secret")
        s = fresh()
        claims = {"user_id": "u1", "firebase": {"sign_in_provider": provider}}
        monkeypatch.setattr(
            "google.oauth2.id_token.verify_firebase_token",
            lambda *a, **k: claims)
        with pytest.raises(s.identity.Unauthenticated):
            s.identity._verify("any-token")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_google_and_anonymous_still_resolve(monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("MALLOW_SESSION_SECRET", "test-session-secret")
    s = fresh()
    for raw, expected in (("google.com", "google"), ("anonymous", "anonymous")):
        monkeypatch.setattr(
            "google.oauth2.id_token.verify_firebase_token",
            lambda *a, _r=raw, **k: {"user_id": "u1",
                                     "firebase": {"sign_in_provider": _r}})
        assert s.identity._verify("t").provider == expected
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


# ------------------------------------------- summary / garden atomicity ------
def test_the_summary_and_the_leaf_are_one_write(srv):
    """
    They used to be two. A failure between them stored the summary and left the
    meadow empty — and the next run exited early because the summary existed,
    so that leaf could never appear at all.
    """
    import firestore_store as fs
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("u1", client)
    before = client.transactions
    ws.commit_reflection("s1", {"summary_id": "s1", "reflection": "a week"},
                         {"leaf": {"summary_id": "s1"}, "seen_at": None})
    assert client.transactions == before + 1
    assert ws.summaries["s1"]["summary_id"] == "s1"
    assert ws.garden.read()["leaf"]["summary_id"] == "s1"


def test_firestore_keeps_five_leaf_tokens_and_puts_away_only_one(srv):
    """The deployed backend has the same queue and atomic put-away as files."""
    import firestore_store as fs
    from ledger import visible_leaves
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("u1", client)

    for i in range(6):
        sid = f"s{i}"
        made = f"2026-08-{i + 1:02d}T09:00:00+09:00"
        assert ws.commit_reflection(
            sid,
            {"summary_id": sid, "created_at": made, "reflection": sid},
            {"leaf": {"summary_id": sid, "body": sid},
             "last_summary_at": made},
        )

    assert [leaf["summary_id"] for leaf in visible_leaves(ws.garden.read())] == [
        "s1", "s2", "s3", "s4", "s5"]
    before = client.transactions
    ws.put_away_leaf("s3", "2026-08-26T09:00:00+09:00")
    assert client.transactions == before + 1
    assert [leaf["summary_id"] for leaf in visible_leaves(ws.garden.read())] == [
        "s1", "s2", "s4", "s5"]
    assert sorted(ws.summaries.keys()) == ["s0", "s1", "s2", "s3", "s4", "s5"]


def test_a_stranded_summary_gets_its_leaf_back(srv):
    """The repair path, for a split that a shipped build could already have left."""
    s, c = srv
    ws = seed(srv)
    made = s.reflection.run_for(ws, writer=s.reflection.deterministic)
    assert made is not None

    ws.garden.write({})                                   # the garden write is lost
    assert ws.garden.read().get("leaf") is None
    assert s.reflection.reconcile(ws) is not None
    assert ws.garden.read()["leaf"]["summary_id"] == made["summary_id"]


def test_reconcile_does_not_resurrect_a_leaf_the_garden_moved_past(srv):
    """Repairing a dropped write is not the same as inventing an event."""
    s, c = srv
    ws = seed(srv)
    s.reflection.run_for(ws, writer=s.reflection.deterministic)
    state = ws.garden.read()
    ws.garden.write({**state, "leaf": None, "seen_at": "2026-08-23T00:00:00+09:00"})
    assert s.reflection.reconcile(ws) is None


# ------------------------------------------------ the scheduler's audience ---
def test_a_service_account_without_an_audience_refuses_to_start(monkeypatch):
    """
    `verify_oauth2_token(audience=None)` skips the audience claim, so a token
    minted by the same service account for a different service would pass.
    Naming the caller without naming the callee is a weaker check, not a
    smaller one.
    """
    monkeypatch.setenv("TASKS_SERVICE_ACCOUNT", "mallow-tasks@example.iam.gserviceaccount.com")
    monkeypatch.delenv("TASKS_AUDIENCE", raising=False)
    with pytest.raises(RuntimeError, match="TASKS_AUDIENCE"):
        fresh()
    monkeypatch.delenv("TASKS_SERVICE_ACCOUNT")


# ------------------------------------------------ the transcript is shown ----
def test_what_mallow_heard_is_always_shown_and_always_correctable(srv):
    """
    The locked UX: the person must see what Mallow heard, and must always be
    able to change or cancel it. The receipt used to show the correction
    buttons only when the model said it was unsure — so a confident
    mis-hearing was filed with nothing on screen to argue with.
    """
    _, c = srv
    page = visible(c, "/")
    assert 'id="heard"' in page, "the transcript has no place on screen"
    # The confirm row is no longer hidden behind the model's own confidence.
    assert "confirmRow" in page
    assert "el.confirm.hidden = !unsure" not in page


# --------------------------------------------------------- the SDK version ---
def test_the_genai_sdk_is_not_the_one_from_launch_week(srv):
    req = (MOBILE.parent / "requirements.txt").read_text()
    assert "google-genai==1.0.0" not in req


def test_the_deploy_doc_does_not_claim_rules_enforce_isolation(srv):
    """
    🔴 A false security claim, in the one document somebody follows while
    holding admin rights.

    Firestore Security Rules govern the client SDKs. This server uses the
    Python *server* SDK, which authorises through IAM and bypasses Rules
    entirely — so a per-uid Rule protected a door nobody uses, while the
    sentence beside it promised a second layer that did not exist.
    """
    doc = (MOBILE.parent / "deploy" / "DEPLOY.md").read_text()
    assert "allow read, write: if false" in doc
    assert "bypasses Rules" in doc or "bypass Rules" in doc
    assert "roles/datastore.user" in doc, "the app cannot write Firestore without it"

    # The rule that used to be published as a live isolation layer must not be
    # anywhere in the executable runbook.  An older test split on a retired
    # heading ("What to publish instead"), so a correct rewrite failed merely
    # because it no longer carried that prose section.
    assert "request.auth.uid == uid" not in doc
    rules = (MOBILE.parent / "deploy" / "firestore.rules").read_text()
    assert "request.auth.uid == uid" not in rules


# ═══════════ second review round (CodeX, 2026-08-23) ═════════════════════════

def test_a_bad_token_is_401_and_never_500(monkeypatch):
    """
    🔴 `verify_firebase_token` raises for most of the ways a token is bad —
    expired, malformed, wrong signature — and for things that are not the
    caller's fault at all, like being unable to fetch Google's public keys.
    Uncaught, every one of those became a server error. An expired token is the
    most ordinary thing that happens to a token, and it read as "Mallow is
    broken" rather than "sign in again".
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("REQUIRE_FIREBASE_AUTH", "1")
    monkeypatch.setenv("MALLOW_SESSION_SECRET", "test-session-secret")
    s = fresh()
    c = s.app.test_client()

    class KeysUnreachable(Exception):
        pass

    for boom in (ValueError("Token expired"),
                 ValueError("Could not verify token signature"),
                 KeysUnreachable("certs endpoint down"),
                 Exception("something the library did not document")):
        monkeypatch.setattr("google.oauth2.id_token.verify_firebase_token",
                            lambda *a, _e=boom, **k: (_ for _ in ()).throw(_e))
        for path in ("/whoami", "/records", "/export.json"):
            r = c.get(path, headers={"Authorization": "Bearer whatever"})
            assert r.status_code == 401, f"{type(boom).__name__} on {path} → {r.status_code}"

    # A verifier that returns nothing is still 401, as before.
    monkeypatch.setattr("google.oauth2.id_token.verify_firebase_token",
                        lambda *a, **k: None)
    assert c.get("/whoami", headers={"Authorization": "Bearer x"}).status_code == 401

    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_a_rejected_token_is_never_written_to_a_log(monkeypatch, caplog):
    """The exception type is useful. The token is a live credential."""
    import logging
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("MALLOW_SESSION_SECRET", "test-session-secret")
    s = fresh()
    monkeypatch.setattr("google.oauth2.id_token.verify_firebase_token",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("expired")))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(s.identity.Unauthenticated):
            s.identity._verify("super-secret-token-value")
    assert "super-secret-token-value" not in caplog.text
    assert "ValueError" in caplog.text
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_a_deployment_always_marks_its_cookies_secure(monkeypatch):
    """
    🔴 `request.is_secure` describes the hop this process can see. Behind Cloud
    Run's proxy that is the internal one unless the forwarded scheme is
    trusted — so a cookie that authorises reading somebody's private records
    would have had its transport protection decided by a header that may not
    have survived. Deployed means Secure, and it is not read off the request.
    """
    s, c = firebase_mode(monkeypatch)                    # http test client
    r = c.post("/auth/session", headers={"Authorization": "Bearer good-token"})
    assert r.status_code == 200
    cookie = r.headers["Set-Cookie"]
    assert "Secure" in cookie, cookie
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    monkeypatch.delenv("REQUIRE_FIREBASE_AUTH")
    monkeypatch.delenv("FIREBASE_PROJECT_ID")


def test_a_laptop_on_plain_http_still_gets_a_usable_cookie(srv):
    """The one case that may be insecure, because otherwise it cannot work."""
    s, c = srv
    assert s.identity.DEPLOYED is False
    c.get("/")                                            # mints a local identity
    assert c.get("/whoami").status_code == 200


def test_cloud_run_forwarded_scheme_is_trusted(srv):
    """Redirects and logs should describe what the browser did, not the hop."""
    src = (MOBILE / "server.py").read_text()
    assert "ProxyFix" in src and "K_SERVICE" in src


def test_the_deploy_doc_is_executable_in_dependency_order(srv):
    """
    🔴 The runbook used to deploy private and then verify with plain curl.
    Cloud Run IAM answers first on a private service, so those 401s and 403s
    could not be observed at all — the response is about invocation permission,
    not about anything the app decided.
    """
    doc = (MOBILE.parent / "deploy" / "DEPLOY.md").read_text()
    for stage in range(1, 7):
        assert f"Stage {stage}" in doc

    deploy = doc.index("gcloud run deploy")
    service_binding = doc.index("gcloud run services add-iam-policy-binding")
    scheduler_job = doc.index("gcloud scheduler jobs create")
    opened = doc.index("--member=allUsers")
    verified = doc.index("curl -s \"$URL/health\"")
    assert deploy < service_binding < scheduler_job < opened < verified

    assert "gcloud firestore databases create" in doc
    assert "firebase-tools deploy" in doc and "firestore:rules" in doc
    assert "TASKS_AUDIENCE=${URL}" in doc
    assert "expect 401" in doc, "the bad-token case has to be on the runbook"


def test_the_deploy_doc_does_not_teach_two_commands_that_fail_on_cloud_run(srv):
    """
    Both of these were in the runbook and both were caught by running it, not
    by reading it. They are asserted here because the next person to touch this
    document will not have a live service in front of them.

      1. `--allow-unauthenticated` is a flag on `gcloud run deploy`. On
         `gcloud run services update` it is an `unrecognized arguments` error,
         so a service deployed private could never be opened by following the
         runbook. Opening an existing service is an IAM binding for allUsers.

      2. `/healthz` never reaches a Cloud Run container — Google's frontend
         answers it. A reader who curls it sees a 404 and concludes the deploy
         failed. `/health` is the same view on a path that is not intercepted.
    """
    doc = (MOBILE.parent / "deploy" / "DEPLOY.md").read_text()

    # Only the commands are asserted. The prose around them names both wrong
    # commands on purpose, so that a reader who already ran one recognises it;
    # a test that searched the whole document could not tell the warning from
    # the mistake.
    runnable = "\n".join(doc.split("```bash")[i].split("```")[0]
                         for i in range(1, doc.count("```bash") + 1))

    # `--no-allow-unauthenticated` on the first deploy is correct and stays.
    assert "--allow-unauthenticated" not in runnable.replace(
        "--no-allow-unauthenticated", ""), \
        "services update does not accept --allow-unauthenticated"

    assert 'curl -s "$URL/healthz"' not in runnable, \
        "/healthz is intercepted by Google's frontend on Cloud Run"
    assert 'curl -s "$URL/health"' in runnable


# ===================== the schemas the real SDK has to accept ===============
# 🔴 The gap these two tests close.
#
# Every other test in this repository runs against the deterministic model, so
# the response schemas were never once handed to the GenAI SDK. `CANDIDATE_SCHEMA`
# wrote "may be absent" as the JSON-Schema union `"type": ["integer", "null"]`,
# which `types.Schema` rejects with a pydantic ValidationError before any request
# is built. On the deployed service every single capture returned 503 — at 17ms
# per attempt, because nothing was ever sent. 215 tests were green.
#
# These need no credentials and no network: the SDK validates the dict locally,
# which is precisely where the failure was.

def _schema_types(node, path="$"):
    """Every `type` value in a schema tree, with where it was found."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "type":
                yield f"{path}.type", value
            else:
                yield from _schema_types(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _schema_types(value, f"{path}[{i}]")


def test_the_response_schemas_are_valid_genai_schemas():
    """
    `types.Schema.model_validate` — the exact call that raised in production.

    🔴 Building `GenerateContentConfig(response_schema=...)` is NOT this check.
    The config accepts a bare dict and holds it as-is; it is only converted into
    a `Schema` inside `generate_content`, at request-assembly time. The first
    version of this test constructed the config, passed against the schema that
    was live-broken at the time, and would have let the outage ship twice.
    """
    from google.genai import types

    import contract
    import reflection

    for name, schema in (("CANDIDATE_SCHEMA", contract.CANDIDATE_SCHEMA),
                         ("REFLECTION_SCHEMA", reflection.REFLECTION_SCHEMA)):
        types.Schema.model_validate(schema)


def test_no_schema_says_nullable_with_a_json_schema_union():
    """
    The specific shape that broke, named so the next person recognises it.

    Plain JSON Schema spells optionality `"type": ["string", "null"]`. A GenAI
    `Schema` spells it `"type": "string", "nullable": True`. The two look
    interchangeable and are not.
    """
    import contract
    import reflection

    for name, schema in (("CANDIDATE_SCHEMA", contract.CANDIDATE_SCHEMA),
                         ("REFLECTION_SCHEMA", reflection.REFLECTION_SCHEMA)):
        for where, value in _schema_types(schema, name):
            assert isinstance(value, str), (
                f"{where} is {value!r}. A GenAI Schema takes one type plus "
                f'"nullable": True, never a JSON-Schema union list.')


def test_the_versioned_firestore_rule_denies_every_client(srv):
    root = MOBILE.parent
    config = json.loads((root / "firebase.json").read_text())
    assert config["firestore"]["rules"] == "deploy/firestore.rules"
    rules = (root / "deploy" / "firestore.rules").read_text()
    assert "allow read, write: if false" in rules


def test_the_gate_checks_that_requirements_resolve_not_just_that_it_works_here(srv):
    """
    🔴 The pin that would have failed the Docker build, and passed every test.

    `google-auth` was pinned at 2.35.0 while `google-genai` 2.19.0 requires
    >=2.56.0. Nothing local noticed: this machine already had a compatible
    google-auth installed from earlier, so pip never had to resolve the
    conflict. A clean install — the only kind a container does — stops at
    dependency resolution, before the first line of the app.

    "Importable on this machine" and "this file resolves" are different
    questions, and only the second one is the one the deploy asks.
    """
    sh = (MOBILE.parent / "run.sh").read_text()
    assert "requirements_resolve" in sh
    assert "--dry-run" in sh
    assert "--ignore-installed" in sh, \
        "without it pip can pass a pin this machine already satisfies"
    gate = sh.split("  test)")[1].split("  seed-demo)")[0]
    assert "requirements_resolve" in gate, "the gate does not check it"
    assert "doctor)" in sh and "requirements_resolve" in sh.split("doctor)")[1][:600]


def test_the_pins_actually_agree_with_each_other(srv):
    """
    Not a proxy for the real check — the real check, run here.

    `pip install --dry-run` resolves the file the container will install. It is
    a few seconds and it is the difference between finding this now and finding
    it while somebody is watching a build fail.
    """
    import subprocess
    req = MOBILE.parent / "requirements.txt"
    out = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
         "--quiet", "--report", os.devnull, "-r", str(req)],
        capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, (
        "requirements.txt does not resolve; the Docker build would stop here:\n"
        + (out.stderr or out.stdout)[-1500:])


def test_the_firestore_rules_are_versioned_and_deny_all(srv):
    """
    The rules are a file in the repo rather than something typed into a console
    once, so what is deployed can be reviewed and re-deployed.
    """
    import json
    root = MOBILE.parent
    cfg = json.loads((root / "firebase.json").read_text())
    rules_path = root / cfg["firestore"]["rules"]
    assert rules_path.exists()
    rules = rules_path.read_text()
    assert "allow read, write: if false" in rules
    assert "request.auth.uid == uid" not in rules
    assert "firestore:rules" in (root / "deploy" / "DEPLOY.md").read_text()


# ================= a correction may not cost you the original ===============
# 🔴 Q-09, found in the QA export of 2026-08-24: a kitchen-counter record
# `cancelled`, its tombstone `superseded`, and no active version of that work
# anywhere. Nothing had replaced it — pressing "say it again" retired the old
# capture on the spot, before a replacement existed.
#
# The rule now: the old rows are retired by the same write that files the new
# ones, and only if there are new ones. A failure, a silence, a cancel or a
# closed tab leaves the original exactly as it was.

def _active(client):
    return [r for r in client.get("/voice/records").get_json()
            if r["review_status"] in ("active", "unclassified")]


def test_a_replacement_retires_the_original_only_when_it_lands(srv):
    _, c = srv
    c.post("/voice/text", json={"capture_id": "one",
                                "note": "擦了廚房檯面二十分鐘"})
    assert len(_active(c)) == 1

    c.post("/voice/text", json={"capture_id": "two",
                                "note": "擦了廚房檯面三十分鐘",
                                "replaces": "one"})
    rows = c.get("/voice/records").get_json()
    old = [r for r in rows if r["source_text"] == "擦了廚房檯面二十分鐘"]
    new = [r for r in rows if r["source_text"] == "擦了廚房檯面三十分鐘"]

    assert len(old) == 1 and old[0]["review_status"] == "superseded"
    assert old[0]["superseded_by"] == "two"
    assert len(new) == 1 and new[0]["review_status"] in ("active", "unclassified")
    assert new[0]["supersedes"] == old[0]["record_id"], \
        "a one-for-one correction names the row it replaced"


def test_a_replacement_that_files_nothing_leaves_the_original_alone(srv):
    """
    🔴 The data-loss case itself.

    The person pressed "say it again", and what they said next produced no
    event. The work they had already recorded must still be there.
    """
    _, c = srv
    c.post("/voice/text", json={"capture_id": "one",
                                "note": "擦了廚房檯面二十分鐘"})
    before = _active(c)

    out = c.post("/voice/text", json={"capture_id": "two", "note": "嗯",
                                      "replaces": "one"}).get_json()
    assert out["items"] == [], "nothing was filed from that"

    after = _active(c)
    assert after == before, "and so nothing was retired either"


def test_a_failed_replacement_leaves_the_original_alone(srv, monkeypatch):
    """The model was unreachable. That is not a reason to lose the record."""
    s, c = srv
    c.post("/voice/text", json={"capture_id": "one",
                                "note": "擦了廚房檯面二十分鐘"})
    before = _active(c)

    def unreachable(note):
        raise ConnectionError("no route to the model")

    monkeypatch.setattr(s.slice_, "understand_text", unreachable)
    r = c.post("/voice/text", json={"capture_id": "two",
                                    "note": "擦了廚房檯面三十分鐘",
                                    "replaces": "one"})
    assert r.status_code == 503
    assert _active(c) == before


def test_abandoning_a_correction_costs_nothing(srv):
    """
    The person pressed "say it again" and then closed the tab.

    Nothing was sent, so nothing can have been retired. This is the case the
    old flow got wrong: it retired on the button press, and the button press is
    not a decision to delete anything.
    """
    _, c = srv
    c.post("/voice/text", json={"capture_id": "one",
                                "note": "擦了廚房檯面二十分鐘"})
    before = _active(c)
    # …and nothing else happens at all.
    assert _active(c) == before


def test_a_correction_that_becomes_several_records_says_so_by_capture(srv):
    """
    One sentence can be corrected into two pieces of work. There is then no
    single row to point at, so the events say nothing rather than picking one
    arbitrarily, and the link is carried by the capture instead.
    """
    _, c = srv
    c.post("/voice/text", json={"capture_id": "one", "note": "補了衛生紙"})
    c.post("/voice/text", json={"capture_id": "two",
                                "note": "補了衛生紙，一直記著要預約牙醫",
                                "replaces": "one"})
    rows = c.get("/voice/records").get_json()
    old = [r for r in rows if r["capture_id"] == "one"]
    new = [r for r in rows if r["capture_id"] == "two"]

    assert len(old) == 1 and old[0]["review_status"] == "superseded"
    assert old[0]["superseded_by"] == "two"
    assert len(new) >= 2 and all(
        r["review_status"] in ("active", "unclassified") for r in new)


def test_replacing_a_capture_twice_does_not_retire_it_twice(srv):
    """Already superseded is already superseded; append-only forbids the rest."""
    _, c = srv
    c.post("/voice/text", json={"capture_id": "one", "note": "擦了廚房檯面二十分鐘"})
    c.post("/voice/text", json={"capture_id": "two", "note": "擦了廚房檯面三十分鐘",
                                "replaces": "one"})
    r = c.post("/voice/text", json={"capture_id": "three",
                                    "note": "擦了廚房檯面四十分鐘",
                                    "replaces": "one"})
    assert r.status_code == 200
    rows = c.get("/voice/records").get_json()
    old = [x for x in rows if x["capture_id"] == "one"]
    assert len(old) == 1 and old[0]["superseded_by"] == "two"


# ============ what a person reads back is what a person said ================
# 🔴 Q-10. The record page and the PDF printed `source_text` — the span one
# event rests on — so "我覺得很累 哄睡了兩小時" came back as "哄睡了兩小時"
# days later. The tiredness was in `transcript` the whole time and was simply
# never shown. V1 does not file a feeling as its own record; that is a limit.
# Showing someone two thirds of their own sentence is not a limit, it is a loss.

def test_the_record_page_shows_the_whole_sentence(srv):
    _, c = srv
    said = "我覺得很累，哄睡了兩小時"
    file_note(c, said, "cap1")

    html = visible(c, "/records")
    assert said in html, "the person's whole sentence, as they said it"
    assert html.count(f"<q>{said}</q>") == 1, "and once, not once per event"


def test_the_record_page_still_shows_which_span_each_row_rests_on(srv):
    """The trace does not disappear because the sentence is now shown."""
    _, c = srv
    file_note(c, "補了衛生紙，一直記著要預約牙醫", "cap1")
    html = visible(c, "/records")
    assert 'class="span"' in html
    for span in ("補了衛生紙", "一直記著要預約牙醫"):
        assert span in html


def test_the_pdf_shows_the_whole_sentence(srv):
    """
    A PDF is the copy a person might hand to somebody else. It has to be their
    sentence, not the part a model found a category for.
    """
    s, c = srv
    said = "我覺得很累，哄睡了兩小時"
    file_note(c, said, "cap1")

    pdf = c.get("/export.pdf").data
    assert pdf[:4] == b"%PDF"

    from pdfminer.high_level import extract_text
    import io
    text = extract_text(io.BytesIO(pdf))
    assert said in text
    assert "我覺得很累" in text


def test_the_csv_keeps_both_columns(srv):
    """Machine-readable export loses nothing: the utterance and every span."""
    _, c = srv
    file_note(c, "我覺得很累，哄睡了兩小時", "cap1")
    csv = c.get("/export.csv").get_data(as_text=True)
    header = csv.splitlines()[0]
    assert "transcript" in header and "source_text" in header
    assert "我覺得很累，哄睡了兩小時" in csv


# ===================== care labour is invisible labour ======================
# 🔴 Q-12, ruled 2026-08-24 by the Owner and the Strategic Officer.
#
# The QA export showed 「哄睡了兩小時」 filed as `recognised_work`, which earns
# no food. Two hours of settling a child returned nothing, in a product whose
# subject is work nobody counts. Care is the canonical example of that work; it
# is not in the same class as cooking and cleaning.
#
# 🔴 What these tests can and cannot prove, stated plainly.
#
# The classification is the model's, and no test here can call the model. What
# is asserted below is everything *around* it that the ruling depends on: that
# the rule is in the prompt and cannot be silently dropped, that the prompt
# version moved so old records stay attributable to the taxonomy that produced
# them, that the boundary between giving care and arranging it is written down,
# and that the deterministic double follows the same taxonomy so demo mode and
# production do not disagree about what a week looked like.
#
# The ruling's five acceptance cases are about the real model. They are run
# against Gemini on Google Cloud by `demo/verify_care_taxonomy.py`, and until that has been run
# they are unverified — not passed.

def _prompt():
    import gemini
    return gemini.INSTRUCTION


def _kind_block(name: str) -> str:
    """
    One class's definition, from the `labour_kind:` block.

    🔴 Anchored on the *definition line*, not on the first time the word shows
    up. This helper has been wrong twice now, and each time in the same way.

    First it sliced on the first occurrence anywhere, which caught the one-line
    field list at the top and returned fourteen characters — two of these tests
    passed on nothing.

    Then it sliced from one class name to the next, which only works while no
    class is ever named inside another class's prose. That stopped being true
    on 2026-08-27: `invisible_chore` now says "not recognised_work" and "Not
    unknown - no event", both deliberately, because a definition that points at
    its neighbour is clearer than one that pretends the neighbour is not there.
    The slice then ran off the end and `min()` was handed an empty sequence.

    A definition line is `  name` at the start of a line, two spaces in.
    A mention inside prose never is. That is the whole difference, so match on
    exactly that and let the prose say whatever it needs to.
    """
    import re as _re
    text = _prompt()
    kinds = text[text.index("labour_kind:"):]
    order = ["invisible_chore", "mental_load", "recognised_work", "unknown"]
    starts = {}
    for candidate in order:
        found = _re.search(rf"^  {candidate}\s", kinds, _re.M)
        assert found, f"no definition line for {candidate!r} in the labour_kind block"
        starts[candidate] = found.start()
    begin = starts[name]
    later = [pos for key, pos in starts.items() if pos > begin]
    return kinds[begin:min(later) if later else len(kinds)]


def test_the_prompt_puts_hands_on_care_under_invisible_chore():
    chore = _kind_block("invisible_chore").lower()

    for phrase in ("care", "sleep", "feeding", "bathing"):
        assert phrase in chore, (
            f"{phrase!r} is not in the invisible_chore definition. Care labour "
            f"was ruled into this class on 2026-08-24; if that rule is being "
            f"removed it needs a decision, not an edit.")


def test_the_prompt_keeps_arranging_care_separate_from_giving_it():
    """
    The boundary the ruling drew, and the one most likely to be smudged.

    Making the appointment is mental load. Sitting with the child is a chore.
    Collapsing them would turn every remembered task into two hours of care.
    """
    load = _kind_block("mental_load").lower()
    assert "care" in load and ("arrang" in load or "booking" in load)


def test_cooking_and_cleaning_are_untouched_by_the_care_rule():
    """
    Q-11 stays locked. The care ruling widened one class; it did not reopen the
    reward policy, and recognised_work still means what §2 says it means.
    """
    rec = _kind_block("recognised_work")
    assert "cooking" in rec and "cleaning" in rec


def test_the_prompt_version_moved_with_the_taxonomy():
    """A prompt change that does not move the version is invisible in the ledger."""
    import gemini
    assert gemini.PROMPT_VERSION == "voice-extract-v6-care-context-r5"


def test_a_duration_in_hours_is_converted_rather_than_dropped():
    """
    「哄睡了兩小時」 came back with no duration at all — not because the person
    withheld it, but because only 分鐘 was being read. A unit someone used is
    theirs; only a number they never gave would be an invention.
    """
    assert "hours" in _prompt() and "120" in _prompt()

    import fake_model
    assert fake_model.minutes_in("哄睡了兩小時") == 120
    assert fake_model.minutes_in("2 hours") == 120
    assert fake_model.minutes_in("陪睡了半小時") == 30
    assert fake_model.minutes_in("45 minutes") == 45
    assert fake_model.minutes_in("哄睡了一下") is None, "no number, no duration"
    assert fake_model.minutes_in("打掃了三十分鐘") == 30, \
        "compound Chinese minutes are the speaker's own number, not a guess"
    assert fake_model.minutes_in("哄睡了二十小時") is None, \
        "a numeral this table cannot read is not read, rather than guessed"


@pytest.mark.parametrize("note,kind,minutes", [
    ("哄睡了兩小時", "invisible_chore", 120),
    ("I spent 45 minutes settling my child to sleep.", "invisible_chore", 45),
    ("餵奶", "invisible_chore", None),
    ("記得明天要替孩子預約診所", "mental_load", None),
    ("remember to book the clinic for my child tomorrow", "mental_load", None),
    ("煮了晚飯", "recognised_work", None),
    # 🔴 Was `None` until 2026-08-31, and that was the double's limitation, not
    # the rule: the speaker said 三十分鐘. Compound Chinese minutes are read now.
    ("打掃了三十分鐘", "recognised_work", 30),
])
def test_the_double_follows_the_same_taxonomy(note, kind, minutes):
    """
    The demonstration model, not the real one.

    This does not prove Gemini classifies these correctly — nothing offline
    can. It proves that `./run.sh demo`, the seeded week, and every test that
    runs against the double agree with the ruling, so demo mode cannot quietly
    tell a different story about the same sentence.
    """
    import fake_model
    events = fake_model.understand_text(note)["events"]
    assert len(events) == 1
    assert events[0]["labour_kind"] == kind
    assert events[0]["duration_minutes"] == minutes


def test_two_hours_of_settling_a_child_earns_grass(srv):
    """The end of the chain, which is the point of the ruling."""
    _, c = srv
    out = file_note(c, "哄睡了兩小時", "care1")
    assert out["items"][0]["food"] == "grass"
    assert out["items"][0]["duration_minutes"] == 120

    said = c.post("/say", json=out).get_json()
    assert said["food"] == "grass"


# ---------------------------------------------- the leaf that was thrown away --
# 🔴 2026-08-25, on the deployed app. Cloud Scheduler fired, the OIDC door
# opened, Gemini answered, and the first real leaf was discarded — by nine
# characters.
#
#     reflection      329 chars   three plain English sentences
#     reflection_zh    94 chars   the same three sentences
#     MAX_REFLECTION_CHARS = 320  one number for both
#
# The note was correct in every way the rules care about: describing, not
# evaluating; no durations; every id cited from the pack. A single character
# budget shared by two scripts is not one rule applied twice, it is two
# different rules wearing the same number.

PROD_EN = ("You recorded practical household tasks such as putting winter items "
           "away, restocking sunscreen, and cooking dinner. Your log also included "
           "scheduling and coordination details, like picking swimming classes and "
           "following up on a class list. Other entries involved keeping "
           "appointments in mind and setting things out ahead of time.")
PROD_ZH = ("你記錄了整理冬裝入箱、補買防曬乳以及煮晚餐等日常家務。記錄中也包含排程與聯繫事宜，"
           "例如挑選合適的游泳課和追蹤班級名單的回覆。其餘事項則涉及記住看診提醒，"
           "以及在前一晚備妥隔天早晨所需的物品。")


def test_the_note_that_was_actually_rejected_in_production_is_accepted_now(srv):
    """
    The regression, in the model's own words.

    This is not a note shaped like the one that failed — it is that note, byte
    for byte, off the deployed service on 2026-08-25.
    """
    s, c = srv
    ws = seed(srv)
    assert len(PROD_EN) == 329, "the exact text matters; this one lost by nine"

    def real(pack):
        return {"reflection": PROD_EN, "reflection_zh": PROD_ZH,
                "cited_record_ids": list(pack["record_ids"])}

    made = s.reflection.run_for(ws, writer=real, writer_name="test")
    assert made is not None, "the leaf that production threw away"
    assert len(ws.summaries) == 1
    assert ws.garden.read().get("leaf")


def test_the_two_budgets_are_different_because_the_scripts_are(srv):
    """
    🔴 The fix is not "a bigger number". It is two numbers.

    The same three sentences measured 329 and 94 — a factor of three and a
    half. One cap across both fields silently asks the two languages for
    different amounts of content, while requiring them to say the same thing.
    """
    s, c = srv
    caps = s.reflection.MAX_REFLECTION_CHARS
    assert set(caps) == {"reflection", "reflection_zh"}
    assert caps["reflection"] > caps["reflection_zh"], \
        "English needs more characters to say the same thing"
    assert caps["reflection"] >= len(PROD_EN)
    assert caps["reflection_zh"] >= len(PROD_ZH)


@pytest.mark.parametrize("field,filler", [
    ("reflection", "words and more words. "),
    ("reflection_zh", "字詞又字詞。"),
])
def test_the_cap_still_refuses_an_essay_in_either_language(srv, field, filler):
    """The budget went up. It did not go away."""
    s, c = srv
    ws = seed(srv)
    caps = s.reflection.MAX_REFLECTION_CHARS

    note = {"reflection": PROD_EN, "reflection_zh": PROD_ZH}
    note[field] = filler * (caps[field] // len(filler) + 2)
    assert len(note[field]) > caps[field]

    def writer(pack):
        return {**note, "cited_record_ids": list(pack["record_ids"])}

    with pytest.raises(s.reflection.ReflectionRejected):
        s.reflection.run_for(ws, writer=writer)
    assert len(ws.summaries) == 0
    assert ws.garden.read().get("leaf") is None


def test_the_rejection_says_which_field_and_how_long(srv):
    """
    🔴 The message is what a person reads at 2am off a Cloud Run log.

    "too long" is eight faults wearing one name. The field and the measured
    length are what turn a redeploy into a one-line fix.
    """
    s, c = srv
    ws = seed(srv)
    long_zh = "字" * (s.reflection.MAX_REFLECTION_CHARS["reflection_zh"] + 40)

    def writer(pack):
        return {"reflection": PROD_EN, "reflection_zh": long_zh,
                "cited_record_ids": list(pack["record_ids"])}

    with pytest.raises(s.reflection.ReflectionRejected) as caught:
        s.reflection.run_for(ws, writer=writer)
    msg = str(caught.value)
    assert "reflection_zh" in msg
    assert str(len(long_zh)) in msg, "say how long it actually was"


def test_the_prompt_promises_the_same_numbers_the_validator_enforces(srv):
    """
    🔴 Drift between a prompt and the code that judges its output is invisible
    until the model obeys the prompt and the validator throws it away anyway.

    The model is now told the budgets. If someone raises a cap and leaves the
    instruction saying the old number, every note near the limit is a coin
    toss that the model cannot win on purpose.
    """
    s, c = srv
    caps = s.reflection.MAX_REFLECTION_CHARS
    text = s.reflection.INSTRUCTION
    for field, cap in caps.items():
        assert str(cap) in text, \
            f"the instruction never tells the model the {field} budget ({cap})"


def test_a_rejected_reflection_says_why_in_the_log(srv, caplog):
    """
    🔴 2026-08-25 this cost a deploy cycle: the log said `ReflectionRejected`
    and nothing else, which is eight faults with eight different fixes.

    Every one of those messages is a fixed string written in reflection.py — a
    word from FORBIDDEN, a field name, a count. None can carry a transcript,
    so withholding them protected nothing and hid everything.
    """
    import logging
    s, c = srv
    seed(srv)
    long_zh = "字" * (s.reflection.MAX_REFLECTION_CHARS["reflection_zh"] + 40)

    # The task endpoint picks its writer at request time; under MALLOW_FAKE_MODEL
    # that is `reflection.deterministic`, so this is the one to replace.
    s.reflection.deterministic = lambda pack: {                   # noqa: ARG005
        "reflection": PROD_EN, "reflection_zh": long_zh,
        "cited_record_ids": list(pack["record_ids"])}

    with caplog.at_level(logging.WARNING):
        r = c.post("/tasks/weekly-reflection",
                   headers={"X-Mallow-Task-Key": os.environ["MALLOW_TASK_KEY"]})
    assert r.status_code == 200
    assert r.get_json()["skipped_with_error"] >= 1

    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "ReflectionRejected" in logged
    assert "reflection_zh" in logged, "the reason, not just the class name"


def test_an_unexpected_failure_still_logs_only_its_type(srv, caplog):
    """
    🔴 The other half of the same decision, and the reason it is not simply
    "log everything".

    An arbitrary exception can carry the data that caused it — a KeyError
    naming a field, a driver echoing a row. Those messages stay out of the log.
    Only `ReflectionRejected`, whose messages this repository writes itself,
    is quoted.
    """
    import logging
    s, c = srv
    seed(srv)
    secret = "a-sentence-somebody-said"

    def explode(pack):                                            # noqa: ARG001
        raise RuntimeError(secret)

    s.reflection.deterministic = explode

    with caplog.at_level(logging.WARNING):
        c.post("/tasks/weekly-reflection",
               headers={"X-Mallow-Task-Key": os.environ["MALLOW_TASK_KEY"]})

    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "RuntimeError" in logged
    assert secret not in logged, "an arbitrary exception message stays out"


# ====================== configurable reflection cadence =====================
def test_reflection_settings_offer_every_owner_decided_cadence(srv):
    s, c = srv
    got = c.get("/settings/reflection")
    assert got.status_code == 200
    assert got.get_json()["cadence"] == "weekly", "old workspaces remain weekly"
    html = c.get("/").get_data(as_text=True)
    for value in ("off", "daily", "weekly", "biweekly", "monthly"):
        assert f'value="{value}"' in html


@pytest.mark.parametrize("cadence", ["off", "daily", "weekly", "biweekly", "monthly"])
def test_each_reflection_cadence_can_be_saved(srv, cadence):
    _, c = srv
    r = c.post("/settings/reflection", json={
        "cadence": cadence, "time_local": "23:00", "timezone": "Asia/Tokyo"})
    assert r.status_code == 200
    assert r.get_json()["cadence"] == cadence
    assert c.get("/settings/reflection").get_json()["cadence"] == cadence


def test_invalid_reflection_preferences_fail_closed(srv):
    _, c = srv
    for body in (
        {"cadence": "whenever", "time_local": "23:00", "timezone": "Asia/Tokyo"},
        {"cadence": "daily", "time_local": "25:00", "timezone": "Asia/Tokyo"},
        {"cadence": "daily", "time_local": "23:00", "timezone": "Moon/Base"},
    ):
        assert c.post("/settings/reflection", json=body).status_code == 400


def test_cadence_setting_is_isolated_by_workspace(tmp_path):
    from datetime import datetime, timezone
    import workspaces
    reg = workspaces.FileRegistry(tmp_path)
    first, second = reg.get("first"), reg.get("second")
    now = datetime.now(timezone.utc)
    import reflection_schedule as rs
    rs.save(first, "daily", "21:30", "Asia/Tokyo", now=now)
    rs.save(second, "monthly", "09:15", "Asia/Tokyo", now=now)
    assert first.preferences.read()["cadence"] == "daily"
    assert second.preferences.read()["cadence"] == "monthly"


def test_off_means_the_scheduler_cannot_make_a_leaf(srv):
    s, c = srv
    ws = seed(srv, day_offsets=(1,))
    off = s.reflection.schedule.make("off", "23:00", "Asia/Tokyo",
                                     now=s.reflection.now_jst())
    ws.preferences.write(off)
    assert run_task(c).get_json()["written"] == 0
    assert c.get("/garden").get_json()["leaf"] is None


def test_a_future_due_time_does_not_run_early(srv):
    s, c = srv
    ws = seed(srv, day_offsets=(1,))
    pref = ws.preferences.read()
    pref["next_reflection_at"] = (
        s.reflection.now_jst() + __import__("datetime").timedelta(hours=1)
    ).isoformat(timespec="seconds")
    ws.preferences.write(pref)
    assert run_task(c).get_json()["written"] == 0


def test_summary_leaf_and_next_due_are_one_firestore_transaction(srv):
    import firestore_store as fs
    s, _ = srv
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("scheduled", client)
    now = s.reflection.now_jst()
    pref = s.reflection.schedule.make("daily", "23:00", "Asia/Tokyo", now=now)
    pref["period_start_at"] = (now - __import__("datetime").timedelta(days=1)).isoformat()
    pref["next_reflection_at"] = (now - __import__("datetime").timedelta(seconds=1)).isoformat()
    ws.preferences.write(pref)
    ws.ledger["r1"] = a_record("r1", now - __import__("datetime").timedelta(hours=1))
    before = client.transactions
    assert s.reflection.run_for(ws, now=now,
                                writer=s.reflection.deterministic) is not None
    assert client.transactions == before + 1
    assert ws.garden.read()["leaf"]
    assert s.reflection._parse(ws.preferences.read()["next_reflection_at"]) > now


def test_reflection_and_capture_limits_match_owner_decision(srv):
    s, c = srv
    assert s.reflection.MAX_REFLECTION_CHARS == {
        "reflection": 2000, "reflection_zh": 1000}
    assert s.slice_.MAX_NOTE_CHARS == 2000
    assert 'maxlength="2000"' in c.get("/").get_data(as_text=True)


def test_service_worker_never_cache_firsts_private_state():
    import re
    sw = (MOBILE / "static" / "sw.js").read_text()
    # A previous test asserted the exact old namespace, so it stayed green when
    # mutable auth.js changed without a bump and turned red when somebody made
    # the correct fix. Future versions may advance; they may not fall back to
    # the namespace known to contain stale authentication code.
    version = re.search(r'const CACHE = "mallow-shell-v(\d+)";', sw)
    assert version and int(version.group(1)) >= 4
    assert 'e.request.mode === "navigate"' in sw
    for path in ("/auth", "/garden", "/records", "/voice", "/export", "/settings"):
        assert f'"{path}"' in sw

    # Mutable authentication code is explicitly network-first. Merely keeping
    # auth.js in SHELL is not enough: the installed copy is the offline fallback,
    # while a connected returning browser must ask the network before cache.
    branch = re.search(
        r'if \(url\.pathname === "/static/auth\.js".*?\n  }', sw, re.S)
    assert branch
    code = branch.group(0)
    assert code.index("fetch(e.request)") < code.index("caches.match(e.request)")
    assert 'url.pathname.startsWith("/static/")' not in sw


def test_capture_prompt_is_child_care_scoped_and_duration_independent():
    """
    The one structural test over `INSTRUCTION` (戰略官, 2026-08-30): four labour
    kinds, the current version, product scope, and duration independence.

    🔴 It asserts that the rules are *present*. It cannot assert that Gemini
    obeys them — that is `demo/verify_care_taxonomy.py`, PRODUCT_DECISIONS §41 C.
    Everything this file used to assert about the prompt's exact prose has been
    removed: those tests froze wording, not behaviour, and each reflow turned
    them red for reasons that had nothing to do with the product.
    """
    import re
    import gemini
    prompt = gemini.INSTRUCTION.lower()
    flat = re.sub(r"\s+", " ", prompt)

    assert gemini.PROMPT_VERSION == "voice-extract-v6-care-context-r5"

    for kind in ("invisible_chore", "mental_load", "recognised_work", "unknown"):
        assert re.search(rf"^  {kind}\s", gemini.INSTRUCTION, re.M), \
            f"no definition line for {kind}"

    # scope
    assert "parent or\nguardian" in prompt
    assert "dependent child" in prompt
    assert "elderly" not in prompt and "ill person" not in prompt

    # duration never decides, stated once
    assert "not on whether a duration was stated" in flat
    assert flat.count("missing duration does not turn a chore into mental load") == 1

    # the speaker's own words are never rewritten
    assert "preserve code-mixed speech exactly" in prompt


def test_the_instruction_carries_no_history_or_navigation():
    """
    🔴 PRODUCT_DECISIONS §44 · MALLOW-HYGIENE-001. Owner, 2026-08-30:
    「我們不要 spagetti code。也不要垃圾 prompt」「對空氣揮拳」「浪費 tokens api」.

    Every character here is sent to Gemini on every capture. A sentence written
    for a human reader is not an instruction — and one that narrates a retired
    rule puts a second, competing statement next to the live one.

    The test: is this something the model needs in order to do the task?
    """
    import gemini
    text = gemini.INSTRUCTION

    for marker in ("listed here once", "error this version", "note above",
                   "unchanged by", "🔴", "🚫", "2026-", "戰略官", "Owner",
                   "this version exists to correct"):
        assert marker not in text, \
            f"{marker!r} is addressed to a person, not to the model"

    # It also may not argue with an imagined objection instead of stating a rule.
    for motive in ("does not make it work the world already counts",
                   "never because you decided against it"):
        assert motive not in text, f"motive rather than output contract: {motive!r}"


def test_q32_not_labour_is_not_stretched_into_recognised_work():
    """
    🔴 The bucket that did not exist.

    On 2026-08-26 the Owner said 「0740 出發搭巴士」 and got back
    「已被算作工作」. Catching a bus alone is not work the world already counts;
    it is not work at all. Owner's ruling: tighten the prompt rather than add a
    fourth labour kind.

    `unknown` is scope-bound — in scope, kind undetermined. Out of scope
    produces no event at all. These assertions hold that line from both ends;
    they survived the 2026-08-30 hygiene pass because they name a contract, not
    a paragraph.
    """
    import re
    import gemini
    low = re.sub(r"\s+", " ", gemini.INSTRUCTION).lower()

    assert "must still be household or child-care work" in low
    assert "personal travel, exercise, hobbies, rest and paid employment return an empty events array" in low

    assert "use unknown only inside this product scope" in low
    assert "do not stretch one of the three labour kinds" in low
    assert "returns an empty events array" in low
    assert "do not force it into unknown" in low

    # 🚫 The retired wording must not come back. Re-widening `unknown` would
    # restore the 2026-08-27 contradiction silently.
    assert "not household or dependent-child care work at all" not in low


def test_demo_seed_has_no_force_path_and_marks_the_workspace():
    source = (ROOT / "demo" / "seed_firestore.py").read_text()
    assert 'add_argument("--force"' not in source
    assert "MALLOW_DEMO_UID" in source
    assert "synthetic_demo_workspace" in source
    assert "batch.commit()" in source


def test_navigation_session_lasts_one_demo_day(srv):
    s, _ = srv
    assert s.identity.SESSION_MAX_AGE == 60 * 60 * 24


def test_weekday_and_month_day_are_saved_not_merely_shown(srv):
    _, c = srv
    weekly = c.post("/settings/reflection", json={
        "cadence": "weekly", "time_local": "21:15",
        "timezone": "Asia/Tokyo", "weekday": 6, "day_of_month": 9})
    assert weekly.status_code == 200
    assert weekly.get_json()["weekday"] == 6

    monthly = c.post("/settings/reflection", json={
        "cadence": "monthly", "time_local": "08:30",
        "timezone": "Asia/Tokyo", "weekday": 0, "day_of_month": 31})
    assert monthly.status_code == 200
    assert monthly.get_json()["day_of_month"] == 31


def test_monthly_day_31_clamps_without_forgetting_the_preference():
    from datetime import datetime, timezone
    import reflection_schedule as rs
    now = datetime(2027, 1, 30, 0, 0, tzinfo=timezone.utc)
    pref = rs.make("monthly", "23:00", "Asia/Tokyo", now=now,
                   day_of_month=31)
    first = rs.parse_stamp(pref["next_reflection_at"])
    assert first.day == 31
    following = rs.completed(pref, first, now=first)
    next_due = rs.parse_stamp(following["next_reflection_at"])
    assert next_due.month == 2 and next_due.day == 28
    assert following["day_of_month"] == 31


def test_a_changed_schedule_cancels_an_inflight_file_reflection(srv):
    s, _ = srv
    ws = seed(srv, day_offsets=(1,))
    now = s.reflection.now_jst()

    def person_changes_to_off(pack):
        s.reflection.schedule.save(ws, "off", "23:00", "Asia/Tokyo", now=now)
        return s.reflection.deterministic(pack)

    assert s.reflection.run_for(ws, now=now, writer=person_changes_to_off) is None
    assert ws.preferences.read()["cadence"] == "off"
    assert ws.summaries.latest() is None
    assert not ws.garden.read().get("leaf")


def test_a_changed_schedule_cancels_an_inflight_firestore_reflection(srv):
    import firestore_store as fs
    s, _ = srv
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("preference-race", client)
    now = s.reflection.now_jst()
    due = s.reflection.schedule.make("daily", "23:00", "Asia/Tokyo", now=now)
    due["period_start_at"] = (
        now - __import__("datetime").timedelta(days=1)).isoformat()
    due["next_reflection_at"] = (
        now - __import__("datetime").timedelta(seconds=1)).isoformat()
    ws.preferences.write(due)
    ws.ledger["r1"] = a_record(
        "r1", now - __import__("datetime").timedelta(hours=1))

    def person_changes_to_off(pack):
        s.reflection.schedule.save(ws, "off", "23:00", "Asia/Tokyo", now=now)
        return s.reflection.deterministic(pack)

    assert s.reflection.run_for(ws, now=now, writer=person_changes_to_off) is None
    assert ws.preferences.read()["cadence"] == "off"
    assert ws.summaries.latest() is None
    assert not ws.garden.read().get("leaf")


def test_synthetic_demo_marker_is_visible_in_ui_state_and_json_export(srv):
    s, c = srv
    uid = c.get("/whoami").get_json()["uid"]
    ws = s.workspaces.for_uid(uid)
    pref = s.reflection.schedule.read(
        ws, now=s.reflection.now_jst(), persist_default=True)
    ws.preferences.write({
        **pref, "synthetic_demo_workspace": True})
    assert c.get("/garden").get_json()["demo"] is True
    assert c.get("/export.json").get_json()["demo_data"] is True


def test_firestore_demo_seed_rows_follow_the_current_capture_schema():
    import importlib.util
    from datetime import datetime, timezone
    from contract import ACTIVITY_DOMAINS, RECORD_FIELDS
    path = ROOT / "demo" / "seed_firestore.py"
    spec = importlib.util.spec_from_file_location("seed_firestore_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    rows = module.build(datetime.now(timezone.utc))
    assert rows
    for row in rows:
        assert not [field for field in RECORD_FIELDS if field not in row]
        assert row["activity_domain"] in ACTIVITY_DOMAINS


def test_firestore_workspace_has_a_real_listable_parent_document():
    import firestore_store as fs
    client = fs.InMemoryFirestore()

    # A child alone must not make a missing parent look queryable in the double.
    client.set("users/ghost/records/r1", {"record_id": "r1"})
    assert client.list_ids("users") == []

    registry = fs.FirestoreRegistry(client=client)
    registry.get("visible")
    assert registry.all_uids() == ["visible"]
    assert client.get("users/visible") == fs.WORKSPACE_MARKER


def test_firestore_demo_seed_writes_the_parent_manifest_in_the_same_batch():
    source = (ROOT / "demo" / "seed_firestore.py").read_text()
    assert 'batch.set(user, {"kind": "mallow-workspace"' in source
    assert source.index("batch.set(user") < source.index("batch.commit()")


def test_full_resolution_art_masters_never_ship_or_enter_the_repo():
    rule = "assets/art/Original/"
    for name in (".gitignore", ".gcloudignore", ".dockerignore"):
        lines = [(line.split("#", 1)[0]).strip()
                 for line in (ROOT / name).read_text().splitlines()]
        assert rule in lines, f"{name} would include local art masters"


# =============== Q-34 · whose clock a record is printed against =============
def prefs(c):
    """The stored preference, read back the way the page reads it."""
    return c.get("/settings/reflection").get_json()


def test_q34_a_person_who_never_opened_settings_reads_their_own_day(srv):
    """
    🔴 The defect, stated as the person sees it.

    `timezone` was written only when somebody opened the reflection panel and
    pressed save. Nobody has to do that to use Mallow, so anybody who did not
    read their own records in Tokyo time — correct data, printed against a
    city they have never been to.
    """
    _, c = srv
    file_note(c, "哄睡了兩小時")
    assert "JST" in c.get("/records").get_data(as_text=True), "the old behaviour"

    assert c.post("/settings/display-timezone",
                  json={"timezone": "America/Los_Angeles"}).status_code == 200

    page = c.get("/records").get_data(as_text=True)
    assert "PDT" in page or "PST" in page
    assert "JST" not in page, "🔴 a Los Angeles reader is still being shown Tokyo"


def test_q34_the_device_zone_outranks_the_schedule_zone_for_printing(srv):
    """
    Two different questions that used to share one answer.

    Where the reflection *runs* is a schedule. Which clock a timestamp is
    *printed* against is presentation. Somebody who set a schedule in Tokyo and
    is now reading in Hong Kong wants the second one to follow them.
    """
    _, c = srv
    file_note(c, "哄睡了兩小時")
    c.post("/settings/reflection", json={"cadence": "weekly",
                                         "time_local": "23:00",
                                         "timezone": "Asia/Tokyo"})
    assert "JST" in c.get("/records").get_data(as_text=True), \
        "no device zone yet: the schedule answers"

    c.post("/settings/display-timezone", json={"timezone": "Asia/Hong_Kong"})
    assert "HKT" in c.get("/records").get_data(as_text=True)
    assert prefs(c)["timezone"] == "Asia/Tokyo", \
        "🔴 the schedule's own zone is not what was being set"


def test_q34_recording_a_device_zone_never_disturbs_the_schedule(srv):
    """
    🔴 The one that protects the leaf.

    The scheduler's memory of what has already run lives in these fields. If
    opening the page in another country could rewrite them, a reflection that
    has run could run again — and the autonomous loop is the whole product.
    """
    _, c = srv
    c.post("/settings/reflection", json={"cadence": "daily",
                                         "time_local": "21:30",
                                         "timezone": "Asia/Tokyo"})
    before = prefs(c)

    c.post("/settings/display-timezone", json={"timezone": "Europe/Berlin"})
    after = prefs(c)

    for field in ("cadence", "time_local", "timezone", "weekday",
                  "day_of_month", "period_start_at", "next_reflection_at"):
        assert after.get(field) == before.get(field), f"{field} moved"
    assert after["display_timezone"] == "Europe/Berlin"


def test_q34_a_first_workspace_starts_in_the_zone_of_the_device_that_made_it(srv):
    """
    Nothing to preserve here, so the device's zone is the better first guess.
    A person in Hong Kong should not be handed a schedule that runs on Tokyo
    midnight merely because that is this module's default.
    """
    _, c = srv
    c.post("/settings/display-timezone", json={"timezone": "Asia/Hong_Kong"})
    pref = prefs(c)
    assert pref["timezone"] == "Asia/Hong_Kong"
    assert pref["cadence"] == "weekly", "the default cadence is untouched"


def test_q34_an_unrecognised_zone_is_refused_rather_than_stored(srv):
    """
    🔴 This test is written against a workspace that already has preferences.

    An empty one proves nothing: that path builds a whole schedule, so the
    schedule builder rejects the junk on the way past and the endpoint returns
    400 even with no validation of its own. The merge path is the one with no
    other guard in front of it, and it is the path every returning person
    takes. Verified by mutation: delete the check in `valid_timezone` and this
    goes red — with `file_note` alone in the setup, it did not.
    """
    _, c = srv
    file_note(c, "哄睡了兩小時")
    c.post("/settings/reflection", json={"cadence": "weekly",
                                         "time_local": "23:00",
                                         "timezone": "Asia/Tokyo"})
    for junk in ("Moon/Base", "", "   ", "UTC+9", "'; drop"):
        assert c.post("/settings/display-timezone",
                      json={"timezone": junk}).status_code == 400, junk
        assert "display_timezone" not in prefs(c), f"{junk!r} reached the store"
    assert "JST" in c.get("/records").get_data(as_text=True), "fell back, did not break"


def test_q34_the_page_reports_its_zone_without_being_asked(srv):
    """
    The fix only works if it happens to people who never open anything.
    """
    _, c = srv
    page = visible(c, "/")
    assert "/settings/display-timezone" in page
    assert "resolvedOptions().timeZone" in page.replace(" ", "")
    assert "tellServerOurTimezone();" in page, "🔴 defined but never called at startup"


def test_q34_the_stored_instant_is_never_rewritten(srv):
    """
    🚫 Storage is not what was wrong. `recorded_at` carries its own offset, so
    printing it elsewhere is a conversion; migrating it would be a rewrite of
    somebody's history to fix a rendering choice.
    """
    _, c = srv
    file_note(c, "哄睡了兩小時")
    before = c.get("/export.json").get_json()["records"][0]["recorded_at"]
    c.post("/settings/display-timezone", json={"timezone": "America/New_York"})
    after = c.get("/export.json").get_json()["records"][0]["recorded_at"]
    assert after == before
    assert before.endswith("+09:00"), "still written as a JST-offset instant"


# ============ Q-35 · the meadow explains itself, and the back arrow ==========
def test_q35_the_back_button_draws_exactly_one_arrow(srv):
    """
    🔴 Real device QA, 2026-08-27: the button read "← ← 回草原".

    Q-33 moved the link and gave it an SVG arrow. The translated string already
    carried a "←" from when it was a plain text link, and nothing put the two
    together until somebody looked at a phone.
    """
    _, c = srv
    import i18n
    for language in ("zh-Hant", "en"):
        assert "←" not in i18n.t("back", language), language
    page = c.get("/records").get_data(as_text=True)
    assert page.count("←") == 0, "🔴 a second arrow is back in the markup"
    assert 'class="back"' in page and "<svg" in page.split('class="back"')[1][:400]


def test_q35_the_three_foods_can_be_explained_without_leaving_the_page(srv):
    """
    Owner, 2026-08-27: 「大家都不知道草和蘿蔔和葉子是什麼」.

    The names are this product's own vocabulary. Nobody arrives knowing them,
    and a judge opening the page for the first time is exactly that person.
    """
    _, c = srv
    page = c.get("/records").get_data(as_text=True)
    assert 'id="whatOpen"' in page
    assert 'aria-controls="legend"' in page
    assert 'id="legend"' in page and 'role="dialog"' in page
    for word in ("🌿", "🥕", "🍃"):
        assert word in page.split('id="legend"')[1][:1400], word


def test_q35_the_explanation_says_it_is_not_a_score(srv):
    """
    🔴 Three numbers in a row is the shape of a scoreboard.

    `ROADMAP_FUTURE` forbids this product turning into one, and the panel that
    finally explains the three is the only place that can say so out loud. A
    legend that lists them without that sentence teaches the wrong lesson.
    """
    _, c = srv
    import i18n
    for language in ("zh-Hant", "en"):
        note = i18n.t("legend_note", language)
        assert note and "🚫" in note, language
    page = c.get("/records?lang=zh-Hant").get_data(as_text=True)
    assert i18n.t("legend_note", "zh-Hant") in page


def test_q35_every_legend_string_exists_in_both_languages(srv):
    """The same page in both languages, or the second one silently loses a row."""
    import i18n
    keys = ("legend_open", "legend_title", "legend_grass", "legend_carrot",
            "legend_leaf", "legend_note", "legend_close")
    for key in keys:
        for language in ("zh-Hant", "en"):
            value = i18n.t(key, language)
            assert value and not value.startswith("legend_"), f"{key}/{language}"


def test_q35_the_panel_starts_closed(srv):
    """
    It explains something. It must not be the first thing in the way.
    """
    _, c = srv
    page = c.get("/records").get_data(as_text=True)
    block = page.split('id="legend"')[1][:120]
    assert "hidden" in block, "🔴 the legend is open on arrival"
    assert 'aria-expanded="false"' in page


# ============= Q-37 · what the first frame is allowed to ask for =============
EAGER = ("background_day.webp", "rabbit_idle.webp", "basket.webp",
         "rabbit_listening.webp")
DEFERRED = ("background_night.webp", "rabbit_grass.webp", "rabbit_carrot.webp",
            "rabbit_sleeping.webp")


def test_q37_the_first_frame_only_fetches_what_it_can_show(srv):
    """
    Owner, 2026-08-27: 「mallow 剛進入的時候全部都很慢，慢到不合理」.

    Eight `<img src>` tags, 3.64MB, every one of them requested at once. More
    than half was artwork for states the person had not reached yet — the night
    sky at eleven in the morning, the sleeping rabbit sixty seconds early — and
    it was competing for the same phone connection as the three pictures the
    first frame could not be drawn without.

    🔴 A judge opens this once. The first second is the whole impression.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    # 🔴 The leading space matters: `data-src="…"` contains `src="…"`, so a
    # bare substring check passes for a picture that was correctly deferred.
    for name in EAGER:
        assert f' src="/art/{name}"' in page, f"{name} must load immediately"
    for name in DEFERRED:
        assert f' src="/art/{name}"' not in page, f"🔴 {name} is racing the first frame"
        assert f'data-src="/art/{name}"' in page, f"{name} must still exist, deferred"


def test_q37_the_rabbit_cannot_vanish_under_a_thumb(srv):
    """
    🔴 `rabbit_listening` is eager on purpose, and that is not an oversight.

    It is the very next sprite a finger can ask for. Deferring it would trade a
    slow first paint for a rabbit that disappears the moment somebody presses
    it, which is a worse thing to ship than the problem being fixed.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    assert ' src="/art/rabbit_listening.webp"' in page
    assert 'data-src="/art/rabbit_listening.webp"' not in page


def test_q37_the_first_frame_images_are_announced_early(srv):
    """A preload in the head beats a discovery halfway down the body."""
    _, c = srv
    head = c.get("/").get_data(as_text=True).split("<title>")[0]
    for name in EAGER:
        assert f'rel="preload" as="image" href="/art/{name}"' in head, name


def test_q37_the_deferred_art_is_actually_requested_later(srv):
    """
    Deferring is only half of it. Something has to go and get them, or the
    sleeping rabbit never arrives at all.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    assert "function loadTheRest()" in page
    assert "loadTheRest();" in page, "🔴 defined but never called"
    assert 'querySelectorAll("[data-src]")' in page


def test_q37_the_ready_class_is_no_longer_decoration(srv):
    """
    🔴 `body.classList.add("ready")` was set by startup and read by nothing.

    Not one CSS rule, not one branch. The comment beside it said it existed to
    stop the first state change flashing on a cold cache; nothing implemented
    that. A class that nothing reads is a promise the file makes and does not
    keep, and it read as a protection that was already in place.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    assert 'body.classList.add("ready")' in page
    assert "body:not(.ready)" in page, "🔴 set by startup, read by nothing"


# ========== Q-36 Stage 1 · the school run is not work already counted ========
def _flat_prompt():
    """
    The instruction with its hand-wrapping collapsed and lowercased.

    🔴 Deliberately NOT called `_prompt`. It was, for about an hour on
    2026-08-27, and being defined lower in the file it silently shadowed the
    real `_prompt()` at the top — so `_kind_block()` started slicing collapsed,
    lowercased text without anything failing.

    Three older tests kept passing, because "care", "sleep" and "cooking" are
    substrings that survive being flattened. They were no longer testing the
    thing they were written to test, and nothing said so. A test that changes
    what it inspects without changing colour is the failure mode this whole
    suite exists to catch.
    """
    import re as _re
    import gemini
    return _re.sub(r"\s+", " ", gemini.INSTRUCTION).lower()


def test_q36_the_school_run_left_recognised_work():
    """
    🔴 Owner, fifth-round real-device QA: 「為什麼沒草？」

    Four phrasings of taking a child to and from school, four times
    `recognised_work`, four times no food. The model was not wrong — the prompt
    listed `the school run` under work the world already counts, and it obeyed.

    A person doing that twice a day was being told, by the one app built on the
    premise that this labour is unseen, that the world already counts it.
    """
    low = _flat_prompt()
    assert "cooking, cleaning, the school run" not in low
    head = low.split("recognised_work")[1][:400]
    assert "school run" not in head, \
        "🔴 the school run is back under work the world already counts"


def test_q36_child_transport_is_named_as_accompaniment(srv):
    """
    🔴 Owner, fifth-round real-device QA: 「為什麼沒草？」 Four phrasings of
    taking a child to school, four times `recognised_work`, four times no food.

    The rule is stated positively in `invisible_chore` and fenced out of
    `recognised_work`. 🔴 Frequency is asserted as *behaviour* here rather than
    as the sentence "however many times a week it happens", which the 2026-08-30
    hygiene pass replaced with a compact clause.
    """
    import re
    import fake_model
    import gemini
    low = re.sub(r"\s+", " ", gemini.INSTRUCTION).lower()

    assert "child transport is invisible_chore" in low
    assert "to or from school" in low
    assert "child transport described under invisible_chore is not recognised_work" in low
    assert "duration, frequency and the speaker's share of caregiving" in low

    # behaviour, not prose: doing it twice does not promote it
    for said in ("接送孩子放學", "接送孩子放學，一天兩次"):
        events = fake_model.understand_text(said)["events"]
        assert events and events[0]["labour_kind"] == "invisible_chore", said


def test_q36_the_speakers_own_journey_is_still_excluded():
    """
    🔴 The fence widened on purpose. It must not have widened past the child.

    Q-32 exists because 「0740 出發搭巴士」 was called work. Making child
    transport count is one edit away from making every bus ride count, and both
    rules have to hold at once.
    """
    import re
    import fake_model
    import gemini
    low = re.sub(r"\s+", " ", gemini.INSTRUCTION).lower()

    assert "the utterance itself must supply the child-care purpose" in low
    assert "a bare personal journey, commute or errand" in low
    assert "returns no event" in low

    # behaviour: a journey with no child named earns nothing
    for said in ("0740 出發搭巴士", "went home by bus"):
        events = fake_model.understand_text(said)["events"]
        assert not any(e.get("labour_kind") == "invisible_chore" for e in events), said


def test_q36_care_has_no_duration_or_role_share_threshold():
    """
    The product is for any parent or guardian doing this work, whether it took
    five minutes or five hundred. `took real hours` contradicted both the
    Owner's ruling and the later sentence saying even short care still counts.
    """
    low = _flat_prompt()
    assert "took real hours" not in low
    assert "took real time" in low
    assert "no minimum duration" in low
    assert "no minimum share of caregiving" in low


def test_q36_unknown_is_only_for_unclear_work_inside_product_scope():
    """
    The instruction used to define dancing and a personal commute as `unknown`
    and later demand an empty event list for those same inputs. The model cannot
    reliably obey two opposite output contracts.
    """
    low = _flat_prompt()
    unknown = low.split("unknown you can tell", 1)[1].split("activity_domain", 1)[0]
    assert "only inside this product scope" in unknown
    assert "outside this product scope returns an empty events array" in unknown
    for phrase in ("dancing", "travelling alone", "their own paid job"):
        assert phrase not in unknown


def test_q36_fake_model_does_not_turn_any_pickup_into_child_care():
    """A bare `接送` regex classified colleagues and other adults as children."""
    import fake_model

    child = fake_model.understand_text("接送孩子放學")["events"]
    colleague = fake_model.understand_text("接送同事去車站")["events"]
    elder = fake_model.understand_text("陪奶奶去看醫生")["events"]

    assert child[0]["labour_kind"] == "invisible_chore"
    assert colleague[0]["labour_kind"] != "invisible_chore"
    assert elder[0]["labour_kind"] != "invisible_chore"


def test_q36_real_model_corpus_actually_asserts_the_time_blockers():
    """
    The deploy note named `reference number 0740` as a hard blocker, but the
    taxonomy script neither contained that note nor compared `occurred_at`.
    A release check must fail when the model turns an identifier into 07:40.
    """
    from pathlib import Path

    script = (Path(__file__).parents[2] / "demo" /
              "verify_care_taxonomy.py").read_text(encoding="utf-8")
    assert "reference number 是 0740" in script
    assert "OCCURRED_AT_EXPECTED" in script
    assert '"0900 drop off 孩子": ["09:00"]' in script
    assert '"0740 出發搭巴士送孩子去學校": ["07:40"]' in script
    assert '"學校回條的 reference number 是 0740，我記得要交回去": [None]' in script
    assert "SOURCE_TEXT_EXPECTED" in script
    assert 'Counter(["餵奶", "換了尿布"])' in script


def test_q36_stage_one_is_marked_as_temporary_in_the_source():
    """
    Stage 1 fixes the answer using the mechanism Stage 2 removes: the model still
    chooses `labour_kind`. A stopgap that does not say it is one becomes the
    architecture by default.

    🔴 2026-08-30 (戰略官): the successor must still be named in the source, but
    the deployed version string is no longer required to contain `hotfix` —
    keeping a word in a shipped identifier only to satisfy a test is the tail
    wagging the dog.
    """
    import inspect
    import gemini
    src = inspect.getsource(gemini)
    assert "voice-extract-v7-semantic-gate" in src, \
        "🔴 the successor must be named here, or this stops looking temporary"


def test_q45_maintaining_a_childs_things_is_care_infrastructure():
    """
    🔴 Q-45. Owner, live on mallow-00019-kj2, 2026-08-30:

        「幫咗佢洗玩具用咗十五分鐘」 → recognised_work, no food
        「買咗新玩具畀佢」           → invisible_chore, grass

    Both obeyed the prompt of the day: cleaning was recognised_work, buying
    matched "preparing what someone else will need". Fifteen minutes of hands-on
    work for a child earned nothing; a purchase earned grass. Third instance of
    one shape after Q-12 and Q-36 — child-specific work falling back into the
    general household bucket.

    🔴 The rule is about the child's *things*. 戰略官 2026-08-31 found the first
    draft of the double treating `baby` and `child` as belongings, so cleaning
    the kitchen near a sleeping baby became care. A person is not a belonging,
    and general cleaning stays recognised_work even when it serves someone in
    the speaker's care — the prompt says so in as many words.
    """
    import re
    import fake_model
    import gemini
    low = re.sub(r"\s+", " ", gemini.INSTRUCTION).lower()

    assert "care infrastructure" in low
    assert "cooking and cleaning remain recognised_work" in low

    for said in ("幫佢洗玩具", "I sanitised my baby's bottles.",
                 "I washed my child's toys.",
                 "I washed the toys for my child."):
        events = fake_model.understand_text(said)["events"]
        assert events and events[0]["labour_kind"] == "invisible_chore", said

    # 🔴 The negatives are the half that catches over-reach. Two of them carry a
    # child word and must still be recognised_work; a test using only
    # 「打掃廚房」 cannot detect the regression, because it names no child at all.
    for said in ("打掃廚房", "煮了晚飯",
                 "I cleaned the kitchen while my baby slept.",
                 "I cleaned the kitchen for my child.",
                 "I washed my uniform.",
                 "I cleaned a bottle."):
        events = fake_model.understand_text(said)["events"]
        assert events and events[0]["labour_kind"] == "recognised_work", said

    # A pet's belongings are outside this child-care rule. The deterministic
    # double may retain the utterance as ordinary washing, but it must never
    # issue grass merely because the object is a toy.
    events = fake_model.understand_text("I washed the dog's toys.")["events"]
    assert all(event["labour_kind"] != "invisible_chore" for event in events)


def test_the_double_reads_compound_chinese_minutes_and_still_refuses_hours():
    """
    「用咗十五分鐘」 is a number and a unit the speaker gave. Reading it is not
    estimating; dropping it loses something they said.

    🔴 The conservative rule stays exactly where it was. `CN_NUM` refuses
    compound Chinese on the HOURS path — 「二十小時」 is a different order of
    magnitude and the ruling not to guess there is untouched by this.
    """
    import fake_model
    assert fake_model.minutes_in("十五分鐘") == 15
    assert fake_model.minutes_in("二十分鐘") == 20
    assert fake_model.minutes_in("三十五分鐘") == 35
    assert fake_model.minutes_in("二十小時") is None
    assert fake_model.minutes_in("兩小時") == 120
    assert fake_model.minutes_in("剛剛整理了一下") is None


def test_q46_the_runbook_schedules_the_scan_every_minute():
    """
    🔴 Q-46. The Settings UI accepts any minute; the global job resolved only
    quarter-hours. Owner set 21:33, the scan ran at 21:45, and the meadow spent
    twelve minutes looking broken to somebody watching it.

    戰略官 ruling 2026-08-30, timing-review option 1: align the clocks at the
    scheduler. `*/15` cannot appear in a runbook that a person will paste.

    This asserts the documented command only. Changing the live job is an
    Owner-controlled rollout step and is deliberately not automated here.
    """
    from pathlib import Path
    runbook = (Path(__file__).parents[2] / "deploy" /
               "DEPLOY.md").read_text(encoding="utf-8")
    assert '--schedule "* * * * *"' in runbook
    assert "*/15" not in runbook, \
        "🔴 a quarter-hour scan cannot serve a UI that accepts any minute"


def test_q45_cases_are_in_the_real_model_corpus():
    """
    🔴 §41 C. The deterministic double proves the pipeline; only the real model
    can prove the semantics. A taxonomy ruling that exists solely in the double
    is a ruling nobody has checked.
    """
    from pathlib import Path
    script = (Path(__file__).parents[2] / "demo" /
              "verify_care_taxonomy.py").read_text(encoding="utf-8")
    for case in ("幫佢洗玩具用咗十五分鐘", "I sanitised my baby's bottles.",
                 "I washed the toys for my child.",
                 "I washed my work uniform.", "I cleaned a bottle.",
                 "打掃廚房十五分鐘",
                 "I cleaned the kitchen while my baby slept."):
        assert case in script, case


def test_q35_the_legend_uses_the_approved_definitions(srv):
    """
    🔴 The 戰略官 approved the anti-score sentence on condition that the panel
    define all three, and supplied the wording (reply of 2026-08-27 §7).

    Approved copy is not a suggestion the next edit may quietly drift away
    from, so the load-bearing phrase of each definition is asserted here.
    """
    import i18n
    for key, phrase in (("legend_grass",  "卻常沒有被算進去的照顧與準備"),
                        ("legend_carrot", "記住、安排、追蹤與協調等 mental load"),
                        ("legend_leaf",   "根據近期紀錄自動準備的私人小回顧"),
                        ("legend_note",   "這些是紀錄，不是分數或目標")):
        assert phrase in i18n.t(key, "zh-Hant"), key


def test_q36_a_bare_journey_earns_nothing():
    """
    🔴 Not `unknown` — no event. An `unknown` here would put a non-labour thing
    into the labour ledger, which is the opposite of what this product claims.

    The prose that used to be asserted here ("that the people who use this look
    after children is not evidence", "gets no event at all. not unknown - no
    event") was replaced on 2026-08-30 by a compact clause. The contract is
    unchanged and is now asserted as behaviour as well.
    """
    import re
    import fake_model
    import gemini
    low = re.sub(r"\s+", " ", gemini.INSTRUCTION).lower()

    assert "the product's audience is not evidence" in low
    assert "do not invent a companion" in low

    events = fake_model.understand_text("caught the train at 8")["events"]
    assert not any(e.get("labour_kind") == "invisible_chore" for e in events)


def test_q36_child_care_shorthand_is_evidence_without_the_word_child():
    """
    「接送」, "the school run", "school pickup" name child transport without
    using the word child. Requiring the literal word would fail the phrasings
    people actually use.
    """
    import re
    import gemini
    low = re.sub(r"\s+", " ", gemini.INSTRUCTION).lower()

    for phrase in ("the school run", "school pickup"):
        assert phrase in low, f"{phrase!r} must count as evidence"
    assert "even when the word child is omitted" in low


def test_q36_the_corpus_tests_both_directions_in_both_languages():
    """
    A prompt saying the right thing is not the same as a corpus able to notice
    when it stops. Shorthand that must survive and bare journeys that must not,
    in English as well as Chinese, in one run.
    """
    import pathlib
    corpus = (pathlib.Path(__file__).resolve().parents[2]
              / "demo" / "verify_care_taxonomy.py")
    text = corpus.read_text(encoding="utf-8")
    # 🔴 Every one of these names a completed act. "School pickup at 3." was
    # here until the 戰略官 pointed out it reads as a reminder just as easily,
    # which would make `mental_load` a fair answer — and a case with two fair
    # answers tests the corpus rather than the model.
    for shorthand in ("Did the school run at 8.", "Did school pickup at 3.",
                      "Went to daycare for drop-off."):
        assert shorthand in text, f"missing shorthand positive: {shorthand}"
    for bare in ("0740 出發搭巴士", "我自己搭巴士去上班", "Caught the train at 8."):
        assert bare in text, f"missing bare-journey negative: {bare}"


# ===== Q-36 · the widened domain check must still be able to fail ===========
def _corpus_module():
    """The real-model harness, imported without running it."""
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parents[2]
            / "demo" / "verify_care_taxonomy.py")
    spec = importlib.util.spec_from_file_location("verify_care_taxonomy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_q36_stay_and_play_accepts_both_rulings_for_the_first_domain():
    """
    🔴 戰略官, 2026-08-28: run 3 is semantically 25/25. The one red was the
    harness being narrower than the ruling.

    Staying at a child's school session is a school thing and a child thing at
    the same time. Three runs of the identical sentence with the identical
    prompt answered:

        run 1  school_community      run 2  care_child      run 3  care_child

    `labour_kind` was `invisible_chore` all three times and the durations were
    120 and 60 all three times. The field that decides food does not move. The
    field that only describes the activity does.
    """
    check = _corpus_module().context_matches
    for domain in ("school_community", "care_child"):
        assert check([(domain, "invisible_chore", 120),
                      ("care_child", "invisible_chore", 60)]), domain


def test_q36_the_widened_domain_check_still_rejects_a_real_regression():
    """
    🚫 Widened, not removed.

    An assertion that accepts anything is the same as no assertion, and that is
    exactly how a corpus stops being able to fail while still printing ✅. The
    ruling named two domains; a third is a regression and must go red.
    """
    check = _corpus_module().context_matches
    for wrong in ("transport_errands", "household_upkeep", "other"):
        assert not check([(wrong, "invisible_chore", 120),
                          ("care_child", "invisible_chore", 60)]), wrong


def test_q36_only_the_domain_was_widened(): 
    """
    The kind, the durations and the event count stay exact. Loosening the
    domain must not have loosened its neighbours by accident.
    """
    check = _corpus_module().context_matches
    assert not check([("care_child", "recognised_work", 120),
                      ("care_child", "invisible_chore", 60)]), "kind"
    assert not check([("care_child", "invisible_chore", 90),
                      ("care_child", "invisible_chore", 60)]), "duration"
    assert not check([("care_child", "invisible_chore", 120)]), "event count"


def test_q36_the_second_event_is_not_widened_at_all():
    """
    Sitting with a child through a meal is child care and nothing else. Only
    the first event carried a genuine ambiguity, so only it was widened.
    """
    module = _corpus_module()
    assert module.CONTEXT_EXPECTED[1][0] == {"care_child"}
    assert module.CONTEXT_EXPECTED[0][0] == {"school_community", "care_child"}


# ============ Q-38 · the corner clock, and whose clock it is =================
def test_q38_the_clock_exists_and_shows_a_date(srv):
    """
    Owner, 2026-08-25: 「兔子天空右上角應該加一個 日期＋時鐘⋯⋯我覺得有時鐘比較
    方便，retention rate 也比較高」.

    A record-only app is not opened on a day with nothing to record. One that
    also tells the time gets left somewhere it can be seen.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    assert 'id="clock"' in page
    assert 'id="clockTime"' in page and 'id="clockDate"' in page


def test_q38_the_clock_reads_the_same_zone_as_every_timestamp(srv):
    """
    🔴 `PRODUCT_DECISIONS 42 D-2`. The failure this prevents is 18:00 in the
    sky above a record that says 10:00.

    The clock does not call `new Date()` and hope. It calls the identical
    expression that `tellServerOurTimezone()` sends to the server — the value
    that becomes `display_timezone` and prints every timestamp on the records
    page. One source, one hop apart. They cannot disagree.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    flat = page.replace(" ", "").replace("\n", "")
    assert flat.count("Intl.DateTimeFormat().resolvedOptions().timeZone") >= 1
    assert "function deviceZone()" in page
    assert "timeZone:zone" in flat, "🔴 the clock must be formatted in that zone"


def test_q38_the_clock_says_it_is_a_clock(srv):
    """
    🚫 A number in a corner of a page about recorded time could be read as the
    time of something that was recorded. `occurred_at` and `recorded_at` are
    already two different things; this is a third and it is written down
    nowhere.
    """
    import i18n
    for language in ("zh-Hant", "en"):
        label = i18n.t("clock_label", language)
        assert "{time}" in label, language
    assert "clock_label" in i18n.SCRIPT_KEYS
    _, c = srv
    assert "S.clock_label" in c.get("/").get_data(as_text=True)


def test_q38_the_clock_can_be_put_away(srv):
    """Owner asked for it to be hideable, and for it to stay hidden."""
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    assert "clock-off" in page
    assert 'localStorage.setItem("mallow.clock"' in page.replace(" ", "")
    assert "body.clock-off #clock" in page, "hidden state needs a style"


# ============ Q-39 · ambient motion that can actually be seen ================
def test_q39_the_meadow_has_fireflies_at_night(srv):
    """
    🔴 Owner, 2026-08-29: 「除了睡覺外我看不到別的東西」.

    What was there: three 18x30px blades at 18% opacity turning 0.7 degrees in
    total, and a sky veil at 13% opacity moving ten pixels over eighteen
    seconds. All of it in the stylesheet, none of it on the screen. The deputy
    had read that CSS and called the feature seventy per cent done.

    Three screenshots later the blades were abandoned rather than enlarged: the
    meadow is painted grass, white daisies and small orange flowers at high
    density, and anything in the same palette is simply one more flower.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    ambient = page.split('id="ambient"')[1][:200]
    assert ambient.count("<i>") >= 6
    assert "body.night #ambient i:nth-child(6)" in page, "each mote needs its own timing"
    assert "@keyframes firefly" in page


def test_q39_the_particles_do_not_run_in_daylight(srv):
    """
    🔴 A finding, not a shortcut.

    The daytime painting is high-key everywhere — pale sky, pale mountains,
    bright meadow — so a light mote has no dark ground anywhere on it, and a
    dark one reads as dust on the screen. Particles do not work by day, so they
    do not run by day: the meadow is quiet, and at dusk the fireflies come out.

    🚫 If this ever becomes `#ambient i{display:block}` again, six elements are
    being animated where nobody can see them.
    """
    _, c = srv
    flat = c.get("/").get_data(as_text=True).replace(" ", "").replace("\n", "")
    assert "#ambienti{display:none}" in flat
    assert "body.night#ambienti{" in flat


def test_q39_the_daytime_sky_actually_moves(srv):
    """
    🔴 Rewritten 2026-08-29 to assert a THRESHOLD rather than a value.

    The old version pinned `air-drift` to exact pixels and an exact veil
    opacity, so it stayed green for a veil that was measured, in steady state,
    at a maximum of four luminance levels out of 255 - which is to say invisible
    at any brightness on any phone. A test that green-lights an animation nobody
    can see is worse than no test.

    So this asks the only question that matters: does the sky move fast enough
    to be noticed in the ten seconds after somebody opens the app? Any speed
    above the floor passes; tuning the numbers will not turn this red, and
    quietly deleting the motion will.
    """
    import re
    _, c = srv
    flat = c.get("/").get_data(as_text=True).replace(" ", "").replace("\n", "")

    assert 'id="skydrift"' in c.get("/").get_data(as_text=True), \
        "the daytime sky needs a layer that moves"

    span = re.search(r"@keyframesskydrift\{from\{transform:translateX\((-?[\d.]+)px\)\}"
                     r"to\{transform:translateX\((-?[\d.]+)px\)\}\}", flat)
    assert span, "skydrift keyframes must translate the sky"
    travel = abs(float(span.group(2)) - float(span.group(1)))

    # The page is whitespace-stripped here, so the timing function has to be
    # matched by name rather than by "the next word".
    rule = re.search(r"#skydrift\{.*?animation:skydrift([\d.]+)s"
                     r"(cubic-bezier\([^)]*\)|steps\([^)]*\)|linear|"
                     r"ease-in-out|ease-in|ease-out|ease)", flat)
    assert rule, "skydrift needs an animation with an explicit timing function"
    seconds, timing = float(rule.group(1)), rule.group(2)

    speed = travel / seconds
    assert speed >= 1.5, (
        f"the sky moves {speed:.2f}px/s; that is a wallpaper, not weather")
    assert speed <= 4.0, (
        f"the sky moves {speed:.2f}px/s; above this it reads as sliding")

    # 🔴 Not a style preference. `ease-in-out` spends the beginning of every
    # cycle almost stationary, and the beginning of the cycle is exactly the
    # window somebody looks at when the app opens: at 110s ease-in-out the
    # measured movement over the first nine seconds was 2.2px.
    assert timing == "linear", (
        f"skydrift is {timing}; easing stalls the ten seconds people watch")


def test_q39_breathing_is_a_chest_and_is_big_enough_to_see(srv):
    """
    🔴 Rewritten 2026-08-29. The old version asserted `scaleY(1.011)` exactly,
    which is the amplitude the Owner reported she could not see at all - so the
    test was pinning the defect in place.

    Two properties, both thresholds. It has to be big enough to notice: on the
    226px rabbit, 1.011 put the top edge through 2.46px over 5.5 seconds, about
    0.45px a second, and 1.018 puts it through 4.03px. And it has to be a chest
    rather than a zoom - a chest rises more than it widens, and the origin is
    the ground line so the feet stay planted.
    """
    import re
    _, c = srv
    flat = c.get("/").get_data(as_text=True).replace(" ", "").replace("\n", "")

    peak = re.search(r"@keyframesbreathe\{0%,100%\{transform:scaleY\(1\)scaleX\(1\)\}"
                     r"[\d%,]+\{transform:scaleY\(([\d.]+)\)scaleX\(([\d.]+)\)", flat)
    assert peak, "breathing must scale the two axes separately"
    sy, sx = float(peak.group(1)), float(peak.group(2))

    assert sy >= 1.015, f"scaleY {sy} moves the head about {(sy-1)*226:.1f}px; too little to see"
    assert sx < sy, "a chest rises more than it widens; equal axes read as a zoom"
    assert sx > 1.0, "it should not be a pure vertical stretch either"
    assert "transform-origin:50%var(--ground)" in flat, "it lifts from the feet"

    # 🔴 The Owner refused a version that froze the breath while the rabbit was
    # being held: being listened to is not the same as being held still.
    assert "body.pressing.breathe{animation-play-state:paused}" not in flat, \
        "the rabbit keeps breathing while somebody is talking to it"


def test_q39_a_person_who_asked_for_less_motion_gets_less(srv):
    """
    🔴 Somebody who has told their phone to reduce motion has asked for a
    reason, and this product exists for people already carrying too much. The
    rabbit still changes state; it stops breathing and the sky stands still.

    🔴 Rewritten 2026-08-29. The old version read the FIRST reduced-motion block
    and asserted against its first 120 characters, so the moment the clock got a
    reduced-motion rule of its own the test was inspecting the wrong block and
    went red for a reason that had nothing to do with ambient motion. Position
    in the file is not a property worth testing. This asks whether each moving
    thing is stopped by SOME reduced-motion rule, wherever that rule lives.
    """
    import re
    _, c = srv
    flat = c.get("/").get_data(as_text=True).replace(" ", "").replace("\n", "")

    blocks = re.findall(r"@media\(prefers-reduced-motion:reduce\)\{(.*?)\}\}", flat)
    assert blocks, "there must be a reduced-motion block"
    silenced = "".join(b for b in blocks if "animation:none" in b)

    for selector in (".breathe", "#skydrift", "#ambienti"):
        assert selector in silenced, f"{selector} still animates for a person who asked it not to"


# ============ Q-41 · a wait that can really be cancelled ====================
#
# 🔴 The rule this section exists to hold.
#
# Pressing cancel while Mallow is thinking means "this one does not count". It
# does not mean "cancel is racing the server and may lose". The first design
# here was first-writer-wins, and the strategist refused it on 2026-08-29:
# under that design a commit landing ten milliseconds early left the person
# with a record, food and a line in their rollup for something they had just
# cancelled.
#
# So the tests below drive both orderings explicitly, by calling the store's
# own operations in the order under test rather than hoping a race happens.
def _one_record(srv, capture="cap-1", note="Folded the laundry for 20 minutes"):
    """File one note the ordinary way and hand back its receipt."""
    return file_note(srv[1], note, capture=capture)


# 🔴 Read through the client, not through `workspaces.current()` in a fresh
# request context. In local mode a request without the workspace cookie mints a
# NEW uid, so a helper that opens its own context reads an empty store and every
# assertion below it passes for the wrong reason. The client keeps the cookie,
# so it is the same person who filed the note.
# 🔴 Named `q41_` on purpose. A helper called `_active` already exists further
# up this file and takes a client rather than the `srv` pair; defining a second
# one with the same name silently rebound it for every test below, and four
# unrelated correction tests went red with a TypeError. Shadowing at module
# scope is the same defect as the `_prompt()` collision in Q-36 and it is
# caught the same way: give the new one a name nothing else can want.
def q41_rows(srv):
    return srv[1].get("/voice/records").get_json()


def q41_active(srv):
    return [r for r in q41_rows(srv)
            if r.get("review_status") in ("active", "unclassified")]


def test_q41_cancel_before_the_commit_means_nothing_is_ever_written(srv):
    """
    Ordering one: the cancel wins the race.

    The tombstone takes the capture id, so the model's answer arrives at a
    capture that is already spoken for and the ordinary replay branch writes
    nothing. Cheapest of the two, and the only one where no row is created.
    """
    s, c = srv
    r = c.post("/voice/discard", json={"capture_id": "cap-race"}).get_json()
    assert r["outcome"] == "blocked"

    filed = file_note(c, "Folded the laundry for 20 minutes", capture="cap-race")
    assert filed["state"] == "discarded", filed
    assert filed["items"] == []
    assert q41_rows(srv) == [], "a cancelled capture must not create rows at all"


def test_q41_cancel_after_the_commit_still_leaves_nothing_active(srv):
    """
    🔴 Ordering two, and the one the first design got wrong.

    The rows are already on disk. They cannot be deleted — this is an
    append-only ledger — so they move to `cancelled`, which every product read
    already excludes. The row survives in history; the activity does not.
    """
    s, c = srv
    filed = _one_record(srv, capture="cap-late")
    assert filed["items"], "the fixture has to have filed something"
    assert len(q41_active(srv)) == len(filed["items"])

    r = c.post("/voice/discard", json={"capture_id": "cap-late"}).get_json()
    assert r["outcome"] == "compensated"
    assert sorted(r["cancelled_records"]) == sorted(i["record_id"] for i in filed["items"])

    assert q41_active(srv) == [], "cancelling after the commit left an active record"
    assert all(row["review_status"] == "cancelled" for row in q41_rows(srv))


def test_q41_a_cancelled_capture_is_out_of_every_product_read(srv):
    """
    Not just out of the ledger's own filter. Out of the records page, the CSV,
    the reflection fact pack — the places a person would actually meet it.
    """
    s, c = srv
    _one_record(srv, capture="cap-reads")
    c.post("/voice/discard", json={"capture_id": "cap-reads"})

    # 🔴 Owner's ruling, 2026-08-29: cancelling means the content does not come
    # back. A file that contains what somebody cancelled, with the word
    # "cancelled" beside it, is still handing it back to them.
    for path in ("/export.csv", "/export.json"):
        body = c.get(path).get_data(as_text=True)
        assert "laundry" not in body.lower(), f"{path} handed back a cancelled note"
        assert "cancelled" not in body, f"{path} still names the cancelled row"
    pdf = c.get("/export.pdf").get_data()
    assert pdf[:4] == b"%PDF"
    assert q41_active(srv) == []
    assert c.get("/records").status_code == 200


def test_q41_cancelling_a_correction_twice_restores_it_once(srv):
    """
    🔴 The bug the derived id exists for, and it was real.

    With a random id for the restored row, the second cancel could not see the
    first one's work: it read the retired original, saw nothing active in its
    place, and appended the same afternoon a second time. Two perfectly
    legitimate `active` rows, identical content — nothing in the guard or the
    status filter would ever have objected, and every total that person saw
    afterwards would have counted that work twice.
    """
    s, c = srv
    first = file_note(c, "Folded the laundry for 20 minutes", capture="cap-a")
    old_ids = [i["record_id"] for i in first["items"]]
    c.post("/voice/text", json={"capture_id": "cap-b",
                                "note": "Folded the laundry for 40 minutes",
                                "replaces": "cap-a"})

    one = c.post("/voice/discard", json={"capture_id": "cap-b"}).get_json()
    after_one = q41_active(srv)
    two = c.post("/voice/discard", json={"capture_id": "cap-b"}).get_json()
    after_two = q41_active(srv)

    assert one["restored_records"], one
    assert two["restored_records"] == [], "the second cancel restored it again"
    assert two["outcome"] == "nothing_to_cancel"
    assert [r["record_id"] for r in after_one] == [r["record_id"] for r in after_two]
    assert len(after_two) == len(old_ids), "one active copy, not two"

    # 🔴 And nothing is counted twice: one active row per original, carrying
    # the original's own minutes, not two rows adding up to double.
    assert sum(r.get("duration_minutes") or 0 for r in after_two) == 20
    assert all(r["restores"] in old_ids for r in after_two)


def test_q41_the_restored_row_can_be_traced_back(srv):
    """
    Provenance both ways: the restored row names what it restores, and the row
    it restores still names the capture that retired it. Neither end is guessed.
    """
    s, c = srv
    first = file_note(c, "Folded the laundry for 20 minutes", capture="cap-p")
    old_ids = [i["record_id"] for i in first["items"]]
    c.post("/voice/text", json={"capture_id": "cap-q",
                                "note": "Folded the laundry for 40 minutes",
                                "replaces": "cap-p"})
    c.post("/voice/discard", json={"capture_id": "cap-q"})

    rows = {r["record_id"]: r for r in q41_rows(srv)}
    restored = [r for r in rows.values() if r.get("restores")]
    assert restored
    for r in restored:
        original = rows[r["restores"]]
        assert original["record_id"] in old_ids
        assert original["review_status"] == "superseded"
        assert original["superseded_by"] == "cap-q"
        assert r["source_text"] == original["source_text"]
        assert r["review_status"] == "active"
        assert r["restored_at"]


def test_q41_the_rollup_counts_a_restored_row_once(srv):
    """
    The rabbit's own view. `records_in_rollup` is what feeds food and the
    reflection, and after cancelling a correction it has to be back to exactly
    what it was before the correction — not one more, not zero.
    """
    s, c = srv
    file_note(c, "Folded the laundry for 20 minutes", capture="cap-r1")
    before = c.get("/voice/state").get_json()["records_in_rollup"]
    assert before >= 1

    c.post("/voice/text", json={"capture_id": "cap-r2",
                                "note": "Folded the laundry for 40 minutes",
                                "replaces": "cap-r1"})
    assert c.get("/voice/state").get_json()["records_in_rollup"] == before

    c.post("/voice/discard", json={"capture_id": "cap-r2"})
    assert c.get("/voice/state").get_json()["records_in_rollup"] == before
    c.post("/voice/discard", json={"capture_id": "cap-r2"})
    assert c.get("/voice/state").get_json()["records_in_rollup"] == before


def test_q41_a_commit_and_a_cancel_arriving_together(tmp_path):
    """
    🔴 Ordering three, and it is a test rather than an argument.

    Orderings one and two prove the two outcomes the store is allowed to reach.
    They do not prove that two requests actually arriving at once reach one of
    them: that is where a lock held over the wrong span, a read that escapes
    the transaction, or a restore written twice would show up. Nobody can test
    all of concurrency, and this does not claim to. What it claims is that
    across many real overlapping attempts, the invariant never broke and
    nothing deadlocked.

    🔴 No randomness, and that is deliberate twice over.

    The first version released both sides from a plain barrier. A probe of two
    hundred rounds showed the cancel path — much the shorter of the two — won
    every single one: the test was running ordering one, fifty times, while its
    name claimed otherwise. The second version fixed that with a random head
    start and an assertion that both outcomes had been seen.

    That assertion was itself a hazard: a slower runner could produce fifty of
    one kind and fail the release gate for a reason that has nothing to do with
    the code. Which outcome happens is not this test's job — orderings one and
    two are each pinned by their own deterministic test next door. This one
    exists to say that when the two really overlap, the invariants hold.

    So the head start alternates by round number rather than by chance: every
    even round leans one way, every odd round the other, and both directions
    are exercised by construction rather than by luck. Nothing here can flake.

    Fifty rounds, each in its own workspace.
    """
    import threading
    import time

    s = fresh(tmp_path, ephemeral=False)
    failures, dead = [], []
    outcomes = {"nothing was ever written": 0, "written then cancelled": 0}

    for round_no in range(50):
        uid = f"race-{round_no}"
        capture = f"cap-{round_no}"
        gate = threading.Barrier(2, timeout=10)
        head_start = "commit" if round_no % 2 == 0 else "cancel"
        pause = 0.003

        def commit_side():
            try:
                gate.wait()
                if head_start == "cancel":
                    time.sleep(pause)
                with s.app.test_request_context("/"):
                    import identity
                    g_ = identity.g
                    g_._identity = identity.Identity(uid=uid, provider="local", mode="local")
                    ws = s.workspaces.for_uid(uid)
                    ws.commit(capture,
                              {"capture_id": capture, "state": "receipt", "items":
                               [{"record_id": f"{capture}-r", "food": "grass"}],
                               "superseded": []},
                              {f"{capture}-r": {"record_id": f"{capture}-r",
                                                "capture_id": capture,
                                                "review_status": "active",
                                                "source_text": "folded the laundry",
                                                "duration_minutes": 20,
                                                "policy_result": "grass",
                                                "recorded_at": "2026-08-29T10:00:00+09:00"}})
            except Exception as e:                                    # noqa: BLE001
                failures.append(("commit", round_no, repr(e)))

        def cancel_side():
            try:
                gate.wait()
                if head_start == "commit":
                    time.sleep(pause)
                s.workspaces.for_uid(uid).discard(capture, when="2026-08-29T10:00:01+09:00")
            except Exception as e:                                    # noqa: BLE001
                failures.append(("cancel", round_no, repr(e)))

        threads = [threading.Thread(target=commit_side),
                   threading.Thread(target=cancel_side)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            if t.is_alive():
                dead.append(round_no)

        ws = s.workspaces.for_uid(uid)
        rows = list(ws.ledger.values())
        active = [r for r in rows if r.get("review_status") in ("active", "unclassified")]
        restored = [r for r in rows if r.get("restores")]
        if active:
            failures.append(("active row survived", round_no, active))
        if len(restored) > 1:
            failures.append(("restored twice", round_no, restored))
        if len(rows) > 1:
            failures.append(("more rows than the one capture could make", round_no, rows))
        if not rows:
            outcomes["nothing was ever written"] += 1
        elif all(r.get("review_status") == "cancelled" for r in rows):
            outcomes["written then cancelled"] += 1

    assert not dead, f"a round did not finish: {dead}"
    assert not failures, failures[:4]
    # 🔴 `outcomes` is counted and deliberately NOT asserted on. Which side wins
    # a real overlap is a property of the machine, not of the code, and turning
    # it into a gate condition makes the release gate flake on a slow runner.
    # Orderings one and two are pinned by their own deterministic tests; this
    # one is about the invariants holding when they actually collide.
    assert sum(outcomes.values()) == 50, outcomes


def test_the_concurrency_test_does_not_depend_on_chance():
    """
    🔴 A guard on a test, which is unusual and is earned.

    The overlap test went through two wrong versions. The first released both
    sides from a bare barrier and the cancel path won all two hundred probe
    rounds, so it ran one ordering fifty times under a name that claimed
    otherwise. The second used a random head start plus an assertion that both
    outcomes had been seen — which makes the release gate depend on a coin
    landing both ways inside fifty tries, on whatever machine happens to run
    it. A gate that can go red for that reason teaches people to re-run it,
    and a gate people re-run is not a gate.

    Removing the randomness fixed it. Nothing stopped it coming back — a
    mutation that restored `random.choice` stayed green — so this is what
    stops it. The head start has to be a function of the round number, and the
    round has to be reproducible.
    """
    import ast
    import inspect
    src = inspect.getsource(test_q41_a_commit_and_a_cancel_arriving_together)

    # 🔴 The docstring explains at length why there is no randomness, so a
    # plain substring search finds the word "random" in its own reasoning and
    # fails. Scan the code, not the prose — the first version of this guard did
    # exactly that and went red on itself.
    tree = ast.parse(textwrap_dedent(src))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]                       # drop the docstring
    code = ast.unparse(ast.Module(body=fn.body, type_ignores=[]))

    for token in ("random", "shuffle", "uniform", "choice"):
        assert token not in code, \
            f"the overlap test reaches for {token!r}; the gate must not depend on luck"
    assert "round_no % 2" in code, "both directions have to be exercised by construction"


def test_q41_the_same_invariant_holds_when_a_transaction_has_to_run_twice():
    """
    🔴 Firestore's half of ordering three.

    A real Firestore transaction retries its callback on contention: the
    function runs again, from fresh reads, and only the last attempt's writes
    land. So the callback has to be safe to re-execute — and the thing that
    makes it safe is that a restored row's id is derived rather than random.
    With a random id, a retry would have produced a different id each time and
    the invariant would have depended on which attempt happened to win.

    The in-memory double says plainly that it does not simulate retries, so
    this drives the retry itself: the same callback, run twice, writes applied
    once, and the result compared.
    """
    import firestore_store as fs

    class Retrying(fs.InMemoryFirestore):
        """Runs every callback twice, keeping only the second set of writes."""

        def atomic(self, fn):
            self.transactions += 1
            first = fs._Txn()
            fn(first)                       # attempt one: discarded, as on contention
            second = fs._Txn()
            result = fn(second)
            with self._lock:
                for path, value in second.writes:
                    self._docs[path] = value
            return result

    for client in (fs.InMemoryFirestore(), Retrying()):
        ws = fs.FirestoreWorkspace("u-retry", client)
        ws.commit("cap-1",
                  {"capture_id": "cap-1", "state": "receipt",
                   "items": [{"record_id": "new-1"}], "superseded": ["old-1"]},
                  {"old-1": {"record_id": "old-1", "review_status": "superseded",
                             "superseded_by": "cap-1", "source_text": "the original",
                             "duration_minutes": 20,
                             "recorded_at": "2026-08-29T09:00:00+09:00"},
                   "new-1": {"record_id": "new-1", "review_status": "active",
                             "capture_id": "cap-1", "source_text": "the correction",
                             "duration_minutes": 40,
                             "recorded_at": "2026-08-29T10:00:00+09:00"}})

        ws.discard("cap-1", when="2026-08-29T10:00:01+09:00")
        ws.discard("cap-1", when="2026-08-29T10:00:02+09:00")

        rows = ws.ledger.values()
        active = [r for r in rows if r.get("review_status") in ("active", "unclassified")]
        restored = [r for r in rows if r.get("restores")]
        assert len(restored) == 1, (type(client).__name__, restored)
        assert len(active) == 1, (type(client).__name__, active)
        assert active[0]["source_text"] == "the original"
        assert sum(r.get("duration_minutes") or 0 for r in active) == 20


def test_q41_the_definitive_sentence_waits_for_the_server(srv):
    """
    🔴 The page may leave the waiting screen at once. It may not say what it
    does not know.

    The first version showed "Nothing was recorded" the instant the button was
    pressed and swallowed every error from the discard request — so a dropped
    connection produced a screen claiming the capture was cancelled while the
    server went on and committed it. That is the failure this whole feature
    exists to remove, reintroduced in its own last line.
    """
    page = c_get(srv)
    body = page.split("async function cancelWait(){", 1)[1].split("\n}", 1)[0]
    flat = body.replace(" ", "").replace("\n", "")

    assert "showMallow(S.stopped_waiting" in flat, \
        "the immediate line may only say the wait is over"
    assert "S.discarded_note:S.discard_unconfirmed" in flat, \
        "the definitive line and the honest one must both be reachable"
    # 🔴 The definitive sentence must come after the answer, not before it.
    assert flat.index("awaittold") < flat.index("S.discarded_note"), \
        "it still claims success before the server has said so"
    assert "catch(e){}" not in flat.split("awaittold")[0].split("showMallow(S.stopped_waiting")[-1]


def test_q41_a_cancel_that_could_not_be_delivered_is_retried_then_admitted(srv):
    """
    The endpoint is idempotent by construction, so a bounded retry is safe and
    is what carries an ordinary few seconds of no signal. What is not allowed
    is retrying for ever, or giving up quietly.
    """
    page = c_get(srv)
    body = page.split("async function tellServerToDiscard(", 1)[1].split("\n}", 1)[0]
    flat = body.replace(" ", "").replace("\n", "")
    assert "keepalive:true" in flat, "the request has to survive the page going away"
    assert "attempt<DISCARD_TRIES" in flat, "it must be bounded"
    assert 'data.state==="discarded"' in flat, \
        "only the server's own word counts as confirmation"
    assert "returnfalse" in flat, "and it has to be able to say it failed"
    assert "constDISCARD_TRIES=3" in page.replace(" ", "").replace("\n", "")


def test_the_two_languages_make_the_same_claim():
    """
    🔴 This test exists because of a specific mistake.

    The English `claim_note` was rewritten and the Chinese was not: the edit
    that changed it was written and never saved, and nothing in the suite
    compared the two. For a week the app could have told a Chinese reader to
    look for a row that no read face shows, while telling an English reader the
    opposite.

    Comparing translations word for word is impossible and pointless. What can
    be checked is that neither version still promises the thing that is no
    longer true.
    """
    import i18n
    note = i18n.STRINGS["claim_note"]
    assert set(note) == {"zh-Hant", "en"}
    for lang, text in note.items():
        assert "cancelled" not in text.lower(), \
            f"{lang} still points the reader at a row no default view shows"
    assert "覆寫歷史" in note["zh-Hant"] and "匯出" in note["zh-Hant"]
    assert "overwrites history" in note["en"] and "export" in note["en"]


def test_every_sentence_the_page_needs_exists_in_both_languages():
    """
    A missing translation falls back to English mid-sentence, which reads as a
    bug and is one. `SCRIPT_KEYS` is what the page actually asks for.
    """
    import i18n
    missing = [(k, lang) for k in i18n.SCRIPT_KEYS
               for lang in ("zh-Hant", "en")
               if not (i18n.STRINGS.get(k) or {}).get(lang)]
    assert not missing, missing


def test_q41_discarding_twice_says_the_same_thing(srv):
    """A retried cancel is a cancel. It must not cancel something else."""
    s, c = srv
    _one_record(srv, capture="cap-twice")
    first = c.post("/voice/discard", json={"capture_id": "cap-twice"}).get_json()
    second = c.post("/voice/discard", json={"capture_id": "cap-twice"}).get_json()
    assert first["outcome"] == "compensated"
    assert second["outcome"] == "nothing_to_cancel"
    assert q41_active(srv) == []

    blocked = c.post("/voice/discard", json={"capture_id": "cap-never"}).get_json()
    again = c.post("/voice/discard", json={"capture_id": "cap-never"}).get_json()
    assert blocked["outcome"] == "blocked"
    assert again["outcome"] == "already_discarded"


def test_q41_cancelling_a_correction_gives_the_original_back(srv):
    """
    🔴 The case that is easy to miss, and the expensive one to get wrong.

    A correction that lands and is then cancelled would otherwise leave the row
    it replaced retired with nothing active in its place — the person would
    lose the original by cancelling its replacement. That is the shape of Q-09.

    Going back to `active` is forbidden and should be, so the original content
    is appended again as a new active row that names what it restores.
    """
    s, c = srv
    first = _one_record(srv, capture="cap-orig", note="Folded the laundry for 20 minutes")
    old_ids = [i["record_id"] for i in first["items"]]
    assert old_ids

    fixed = c.post("/voice/text", json={"capture_id": "cap-fix",
                                        "note": "Folded the laundry for 40 minutes",
                                        "replaces": "cap-orig"}).get_json()
    assert fixed["items"]
    assert [r["record_id"] for r in q41_active(srv)] == [i["record_id"] for i in fixed["items"]]

    c.post("/voice/discard", json={"capture_id": "cap-fix"})

    active = q41_active(srv)
    assert active, "cancelling the correction must not leave the person with nothing"
    assert all(r.get("restores") in old_ids for r in active), active
    assert all(r["source_text"] == "Folded the laundry for 20 minutes" for r in active)


def test_q41_one_persons_cancel_cannot_reach_anothers_capture(srv, monkeypatch):
    """
    A capture id is only cancellable inside the workspace it belongs to. There
    is no uid on this endpoint and there must never be one.
    """
    s, c = srv
    as_google(s, monkeypatch, uid="owner")
    _one_record(srv, capture="cap-mine")
    assert len(q41_active(srv)) >= 1

    as_google(s, monkeypatch, uid="stranger")
    r = c.post("/voice/discard", json={"capture_id": "cap-mine"}).get_json()
    assert r["outcome"] == "blocked", "the stranger only wrote a tombstone in their own store"

    as_google(s, monkeypatch, uid="owner")
    assert len(q41_active(srv)) >= 1, "somebody else cancelled this person's record"


def test_q41_cancelling_needs_a_write_credential(srv):
    """
    POST, so the navigation session cookie cannot authorise it — that cookie is
    good for GET and HEAD only. Cancelling from another site is exactly what
    that rule exists to stop.
    """
    s, _ = srv
    rules = {r.rule: sorted(r.methods - {"HEAD", "OPTIONS"})
             for r in s.app.url_map.iter_rules()}
    assert rules["/voice/discard"] == ["POST"]


def test_q41_the_way_out_is_only_there_while_it_is_thinking(srv):
    """
    The cancel row is shown and hidden by `thinking()` and by nothing else, so
    it cannot outlive its own wait and cancel whatever came after it.
    """
    page = c_get(srv)
    row = page.split('id="waitRow"')[1].split(">")[0]
    assert "hidden" in row, "it must start hidden"
    flat = page.replace(" ", "").replace("\n", "")
    assert "el.waitRow.hidden=!on" in flat, "only thinking() may reveal it"


def test_q41_a_late_answer_cannot_repaint_a_cancelled_wait(srv):
    """
    Aborting a fetch is for the screen; the discard is for the data. But a
    response can still arrive — from a slow connection, or from the abort
    losing to the parse — so every wait carries a generation and anything
    holding an old one is dropped before it touches the rabbit.
    """
    page = c_get(srv)
    flat = page.replace(" ", "").replace("\n", "")
    assert "letwaitGen=0,waiting=null" in flat
    assert "newAbortController()" in flat

    # 🔴 In BOTH send paths, and in both halves of each: the answer that
    # arrives, and the rejection that arrives. Asserting the guard exists
    # "somewhere in the page" passes with one of the four deleted, which is how
    # a mutation of exactly that survived the first version of this test.
    for name in ("async function send(){", "async function sendText(){"):
        body = page.split(name, 1)[1].split("\n}", 1)[0].replace(" ", "").replace("\n", "")
        assert body.count("if(!isCurrent(mine))return") >= 2, name
        assert "isCurrent(mine)" in body.split("finally", 1)[-1], name


# ============ Q-42 · say it again gives back the microphone =================
def test_q42_say_it_again_goes_back_the_way_she_was_already_talking(srv):
    """
    🔴 This has now been wrong in both directions, and that is the point.

    Originally the label said "say it again" and the action opened the
    keyboard: the worst possible swap for the person this is built for, who is
    using her voice because her hands are full. Q-42 fixed it by always giving
    back the rabbit — and so broke the other half. Somebody who typed, quite
    possibly because she cannot speak where she is, was told to hold the rabbit.

    Neither default is right, so there is no default: the correction happens in
    whichever way she was already talking. What is asserted here is the branch,
    and that the microphone still needs a gesture in both of them.
    """
    page = c_get(srv)
    body = page.split("async function correct(){", 1)[1].split("\n}\n", 1)[0]
    flat = body.replace(" ", "").replace("\n", "")

    assert 'captureOrigin==="text"' in flat, "the branch is on how she said it"
    typed, spoken = flat.split("}else{", 1)

    # She typed: the text box comes back, carrying what she wrote.
    assert "showYou(original)" in typed, "her sentence has to come back with it"
    assert "el.note.focus(" in typed, "and the caret belongs in it"

    # She spoke: no keyboard, no draft, and the rabbit is waiting.
    assert 'el.note.value=""' in spoken, "no draft may be left in edit mode"
    assert "el.note.blur()" in spoken, "the phone keyboard has to come down"
    assert 'micHint("again")' in spoken, "say what to do next"
    assert "el.hold.focus(" in spoken

    assert 'setSprite("rabbit_idle")' in flat
    # 🚫 Neither branch may start recording. It stays a gesture.
    assert "startRec" not in flat, "the microphone still needs a gesture"


def test_q42_saying_it_again_retires_nothing_until_something_replaces_it(srv):
    """
    🔴 Q-09 again. Pressing the button used to retire the original at once, so
    a person who then said nothing, lost the connection or closed the tab lost
    the record too. Only the intention is kept here; the server retires the old
    rows in the same write that files the new ones, and only if there are any.
    """
    s, c = srv
    first = _one_record(srv, capture="cap-keep")
    before = [r["record_id"] for r in q41_active(srv)]
    assert before

    page = c_get(srv)
    body = page.split("async function correct(){", 1)[1].split("\n}", 1)[0]
    assert "replacing = capture" in body
    for verb in ("/voice/cancel", "/voice/discard", "review_status"):
        assert verb not in body, f"correct() must not touch stored rows ({verb})"

    # And nothing on the server retires anything for a replacement that filed
    # no events: an empty correction must not cost somebody the original.
    empty = c.post("/voice/text", json={"capture_id": "cap-empty", "note": "hmm",
                                        "replaces": "cap-keep"}).get_json()
    if not empty["items"]:
        assert [r["record_id"] for r in q41_active(srv)] == before


# ============ Q-43 · a receipt is optional, withdrawal remains =============
def test_q43_the_receipt_is_not_a_second_confirmation(srv):
    page = c_get(srv)
    assert 'id="okBtn"' not in page
    assert "That's right" not in page
    assert 'id="discardBtn"' in page and 'id="editBtn"' in page
    assert "RECEIPT_MS = 8000" in page
    assert 'id="receiptLife"' not in page
    assert "receipt-life" not in page
    assert "offsetWidth" not in page


def test_q43_the_receipt_really_dismisses_and_pauses_without_a_countdown(srv):
    """The receipt quietly makes room; it does not turn into a deadline."""
    page = c_get(srv)
    start = page.split("function startReceiptDismiss(){", 1)[1].split("\n}", 1)[0]
    pause = page.split("function pauseReceiptDismiss(){", 1)[1].split("\n}", 1)[0]
    resume = page.split("function resumeReceiptDismiss(){", 1)[1].split("\n}", 1)[0]
    assert "setTimeout(quiet, receiptRemaining)" in start
    assert "clearTimeout(receiptTimer)" in pause
    assert "receiptRemaining" in pause
    assert "setTimeout(quiet, receiptRemaining)" in resume


def test_q43_receipt_withdrawal_waits_for_the_servers_answer(srv):
    page = c_get(srv)
    body = page.split("async function discardReceipt(){", 1)[1].split("\n}", 1)[0]
    assert "await tellServerToDiscard(target)" in body
    assert "if(confirmed) capture = null" in body
    assert body.index("await tellServerToDiscard(target)") < body.index("S.discarded_note")


def test_q43_a_wording_failure_still_says_the_capture_was_saved(srv):
    page = c_get(srv)
    body = page.split("async function receipt(data, origin){", 1)[1].split("\n}", 1)[0]
    fallback = body.split("catch(e){", 1)[1]
    assert "S.receipt_saved_reply_failed" in fallback
    assert "S.cannot_record" not in fallback


def test_q43_capture_groups_are_the_unit_of_removal(srv):
    _, c = srv
    filed = _one_record(srv, capture="cap-group")
    groups = __import__("export").by_capture(q41_active(srv))
    assert len(groups) == 1
    assert groups[0]["capture_id"] == "cap-group"
    assert groups[0]["record_ids"] == [i["record_id"] for i in filed["items"]]
    page = c.get("/records").get_data(as_text=True)
    assert 'class="remove-entry"' in page
    assert 'data-capture-id="cap-group"' in page
    assert "Remove this entry" in page


def test_q43_record_removal_waits_for_discarded_before_reloading(srv):
    page = srv[1].get("/records?lang=en").get_data(as_text=True)
    request = page.split("async function requestRemoval(payload){", 1)[1]
    request = request.split("\n}", 1)[0]
    handler = page.split('removeConfirm.addEventListener("click", async () => {', 1)[1]
    handler = handler.split("\n});", 1)[0]
    assert 'out.state === "discarded"' in request
    assert "attempt < REMOVE_TRIES" in request
    assert "controller.abort()" in request
    assert "if(!removed)" in handler
    assert handler.index("if(!removed)") < handler.index("location.reload()")
    assert "removeError.textContent = S.remove_failed" in handler


def test_q43_every_removal_sentence_exists_in_both_languages():
    import i18n
    keys = ("remove_capture", "remove_title", "remove_body", "remove_keep",
            "remove_confirm", "remove_working", "remove_failed")
    missing = [(key, lang) for key in keys for lang in ("zh-Hant", "en")
               if not (i18n.STRINGS.get(key) or {}).get(lang)]
    assert not missing, missing


def test_q43_a_restored_capture_can_later_be_removed_from_records(srv):
    """The immutable old receipt does not know the derived restoration id."""
    _, c = srv
    _one_record(srv, capture="cap-original")
    c.post("/voice/text", json={"capture_id": "cap-correction",
                                 "note": "Folded the laundry for 40 minutes",
                                 "replaces": "cap-original"})
    c.post("/voice/discard", json={"capture_id": "cap-correction"})
    restored = [r for r in q41_active(srv) if r.get("restores")]
    assert restored, "the fixture did not restore the original"

    out = c.post("/voice/discard", json={
        "capture_id": "cap-original",
        "record_ids": [r["record_id"] for r in restored],
    }).get_json()
    assert out["state"] == "discarded"
    assert sorted(out["cancelled_records"]) == sorted(r["record_id"] for r in restored)
    assert q41_active(srv) == []


def test_q43_record_ids_cannot_cancel_another_capture(srv):
    _, c = srv
    target = _one_record(srv, capture="cap-target", note="Folded the laundry for 20 minutes")
    other = _one_record(srv, capture="cap-other", note="Labelled all the clothes for 10 minutes")
    c.post("/voice/discard", json={
        "capture_id": "cap-target",
        "record_ids": [other["items"][0]["record_id"]],
    })
    active_ids = {r["record_id"] for r in q41_active(srv)}
    assert other["items"][0]["record_id"] in active_ids
    assert target["items"][0]["record_id"] not in active_ids


@pytest.mark.parametrize("bad", ["not-a-list", [""], [3], ["x" * 161]])
def test_q43_record_removal_rejects_malformed_record_ids(srv, bad):
    r = srv[1].post("/voice/discard", json={"capture_id": "cap-x", "record_ids": bad})
    assert r.status_code == 400


def test_q43_firestore_removes_a_derived_restoration_id_too():
    import firestore_store as fs
    client = fs.InMemoryFirestore()
    ws = fs.FirestoreWorkspace("u-remove-restored", client)
    old = {"record_id": "old-1", "capture_id": "cap-original",
           "review_status": "active", "source_text": "the original",
           "duration_minutes": 20, "recorded_at": "2026-08-29T09:00:00+09:00"}
    ws.commit("cap-original", {"capture_id": "cap-original", "state": "receipt",
                                "items": [{"record_id": "old-1"}], "superseded": []},
              {"old-1": old})
    retired = {**old, "review_status": "superseded",
               "superseded_at": "2026-08-29T10:00:00+09:00",
               "superseded_by": "cap-correction"}
    new = {"record_id": "new-1", "capture_id": "cap-correction",
           "review_status": "active", "source_text": "the correction",
           "duration_minutes": 40, "recorded_at": "2026-08-29T10:00:00+09:00"}
    ws.commit("cap-correction",
              {"capture_id": "cap-correction", "state": "receipt",
               "items": [{"record_id": "new-1"}], "superseded": ["old-1"]},
              {"old-1": retired, "new-1": new})
    ws.discard("cap-correction", when="2026-08-29T10:01:00+09:00")
    restored = [r for r in ws.ledger.active() if r.get("restores")]
    assert len(restored) == 1

    out = ws.discard("cap-original", when="2026-08-29T10:02:00+09:00",
                     record_ids=(restored[0]["record_id"],))
    assert out["cancelled"] == [restored[0]["record_id"]]
    assert ws.ledger.active() == []


# ============ Q-37 r2 · the first frame, and what follows it ================
def test_q37r2_no_runtime_reference_asks_for_a_png(srv):
    """
    🔴 The artwork is WebP everywhere or it is broken somewhere.

    3.70MB of PNG became 1.20MB, and the four pictures the first frame cannot
    be drawn without went from 1,677KB to 590KB. The failure mode of a partial
    conversion is silent: one stale `.png` reference still resolves on a
    machine that has the source art beside it, and 404s in the container, where
    `.dockerignore` keeps the PNGs out.
    """
    import re
    page = c_get(srv)
    sw = (MOBILE / "static" / "sw.js").read_text()
    for name, text in (("index.html", page), ("sw.js", sw)):
        assert not re.search(r'/art/[a-z_]+\.png', text), name
        assert re.search(r'/art/[a-z_]+\.webp', text), name


def test_q37r2_the_pngs_do_not_travel_with_the_image():
    """
    They stay in the repository as the source paintings; they are not in the
    thing that gets deployed. Both ignore files matter: `.gcloudignore` governs
    what `gcloud run deploy --source .` uploads, `.dockerignore` what the build
    context sees, and neither inherits the other.
    """
    root = MOBILE.parent
    for name in (".dockerignore", ".gcloudignore"):
        lines = [l.strip() for l in (root / name).read_text().splitlines()]
        assert "assets/art/*.png" in lines, name


def test_q37r2_the_deferred_pictures_are_not_a_queue(srv):
    """
    🔴 They used to be a chain: one picture, and on its load event the next.
    Four images in series is four round trips laid end to end, which is the
    progress bar still crawling after the meadow is already on screen.

    Nothing was bought by the queue: by the time this runs the first frame has
    painted, so these are not competing with anything the person is waiting for.
    """
    page = c_get(srv)
    body = page.split("function loadTheRest(){", 1)[1].split("\n}", 1)[0]
    flat = body.replace(" ", "").replace("\n", "")
    # 🔴 The property, not one spelling of it: nothing in here may wait for a
    # picture to arrive before asking for the next one. `onload`, `onerror`,
    # `decode()` and `addEventListener` are all ways to write the same queue.
    for chained in ("onload", "onerror", "decode(", "addEventListener"):
        assert chained not in flat, f"the deferred pictures are a queue again ({chained})"
    assert "forEach" in flat, "they have to be started together"
    assert 'el.fetchPriority="low"' in flat, "they must be allowed to yield"


def test_q37r2_the_sign_in_host_is_connected_to_early(srv):
    """
    Firebase arrives through a dynamic `import()` inside auth.js, so the browser
    cannot discover the host while parsing the document — it learns it only once
    the script has run, and then pays DNS, TCP and TLS to a new origin before
    the first byte. `crossorigin` is not optional: a module import is a CORS
    request and a preconnect opened without it is a different connection.
    """
    head = c_get(srv).split("<title>")[0]
    assert 'rel="preconnect" href="https://www.gstatic.com" crossorigin' in head


def test_q37r2_the_settings_button_is_not_hidden_at_first_paint(srv):
    """
    🔴 It used to carry `hidden` and be revealed by one line inside the success
    branch of `boot()`. Any failure in there removed it permanently for
    somebody who was otherwise using the app perfectly well.
    """
    page = c_get(srv)
    button = page.split('id="settingsOpen"')[1].split(">")[0]
    assert "hidden" not in button, button


# ============ Q-40 · what somebody gets when nothing says otherwise =========
def test_q40_a_visitor_with_no_preference_is_answered_in_english(srv):
    """
    🔴 Owner, 2026-08-29: 「default 介面希望是 EN 而不是 CN」.

    `from_request()` is a chain: an explicit `?lang=`, then the remembered
    cookie, then Accept-Language, and only then the default. So this is not
    "the app's language" — it is what somebody gets when **nothing about them
    says otherwise**, and a judge on a Japanese or German browser matched none
    of the earlier rules and was handed Traditional Chinese.

    The rules also require the application to support English at a minimum.
    Both point the same way.
    """
    _, c = srv
    page = c.get("/").get_data(as_text=True)
    assert 'lang="en"' in page
    assert "Say something" in page
    assert "說點什麼" not in page.split("<body")[1]


def test_q40_chinese_is_still_one_tap_away(srv):
    """
    🚫 The default moved. Chinese did not go anywhere.

    Three routes still reach it, and this product was written in it.
    """
    _, c = srv
    assert "說點什麼" in c.get("/?lang=zh-Hant").get_data(as_text=True)
    assert "說點什麼" in c.get(
        "/", headers={"Accept-Language": "zh-Hant,zh;q=0.9"}).get_data(as_text=True)
    english = c.get("/?lang=en").get_data(as_text=True)
    assert 'href="/?lang=zh-Hant"' in english or "lang=zh-Hant" in english, \
        "the switch on the page must still offer it"


def test_q40_an_explicit_choice_still_beats_the_default(srv):
    """The chain is the feature; the default is only its last link."""
    _, c = srv
    assert "說點什麼" in c.get("/?lang=zh-Hant").get_data(as_text=True)
    assert "Say something" in c.get(
        "/?lang=en", headers={"Accept-Language": "zh-Hant"}).get_data(as_text=True)


def test_q40_every_string_has_english_or_the_fallback_raises(srv):
    """
    🔴 `t()` falls back to `STRINGS[key][DEFAULT]` when a language is missing.

    While the default was Chinese, an entry with no English rendered Chinese to
    an English reader — visible, but harmless. Now the fallback is English, so
    an entry with no English would raise `KeyError` and take the page down.
    The guarantee has to be checked, not assumed.
    """
    import i18n
    missing = sorted(k for k, v in i18n.STRINGS.items() if "en" not in v)
    assert not missing, f"no English for: {missing}"
    assert i18n.DEFAULT == "en"
    assert i18n.DEFAULT in i18n.HTML_LANG
