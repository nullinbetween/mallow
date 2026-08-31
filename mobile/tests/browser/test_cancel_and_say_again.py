"""
Cancelling a wait, and being given the microphone back.

🔴 Why this file exists.

Two of the assertions the strategist asked for on 2026-08-29 cannot be made
anywhere but a browser, because both are about what happens on the screen after
the moment a request was abandoned:

  * a model answer that arrives AFTER cancel must change nothing — not the
    rabbit, not the bubble, not the food. The server half of that is a
    transaction and is tested next door; this is the half where a promise that
    was already in the air resolves into a page that has moved on.
  * `Say it again` must take the keyboard away. "The textarea is hidden" is not
    the same claim as "the field is no longer focused", and on a phone only the
    second one puts the keyboard down.

So the page runs in a real engine, with only the network replaced — and
replaced by something the test can hold open, because the whole subject is
what happens in the gap.

    pip install playwright && playwright install chromium
    python3 -m pytest mobile/tests/browser -q
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import pytest

from playwright.sync_api import sync_playwright                    # noqa: E402

MOBILE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(MOBILE), str(MOBILE.parent / "spike" / "voice")]

ORIGIN = "https://mallow.test/"


def _page_html() -> str:
    os.environ["MALLOW_FAKE_MODEL"] = "1"
    os.environ["MALLOW_EPHEMERAL"] = "1"
    os.environ.setdefault("MALLOW_SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("MALLOW_TASK_KEY", "test-task-key")
    for m in ("server", "ledger", "fake_model", "app", "identity",
              "workspaces", "export", "i18n", "reflection", "tasks",
              "firestore_store", "reflection_schedule"):
        sys.modules.pop(m, None)
    server = importlib.import_module("server")
    html = server.app.test_client().get("/?lang=en").get_data(as_text=True)
    return re.sub(r'<script src="/static/auth\.js"></script>', "", html)


# 🔴 `/voice/text` is deliberately left hanging. The test decides when the model
# answers, which is the only way to be standing inside the wait when cancel is
# pressed — the situation the whole feature is about.
HARNESS = """
window.__requests = [];
window.__release = null;             // call to let the model finally answer
window.__reply = {
  state: "receipt", capture_id: "c1", heard: "Folded the laundry",
  items: [{record_id: "r1", food: "grass", duration_minutes: 20,
           source: "Folded the laundry"}],
  withheld_fragments: 0};

window.Mallow = {
  mallowFetch(url, opts) {
    opts = opts || {};
    const method = (opts.method || "GET").toUpperCase();
    window.__requests.push({url: String(url), method: method,
                            body: opts.body || null});

    if (String(url).indexOf("/voice/text") === 0 || String(url).indexOf("/voice") === 0
        && method === "POST" && String(url) === "/voice") {
      // Answer only when the test says so, and reject on abort the way fetch does.
      return new Promise((resolve, reject) => {
        window.__release = () => resolve({ok: true,
                                          json: () => Promise.resolve(window.__reply)});
        const sig = opts.signal;
        // 🔴 `__ignoreAbort` models the case the abort does NOT cover: a
        // response that had already arrived and been parsed when cancel was
        // pressed. `abort()` cannot unring that bell, so the only thing
        // standing between it and the rabbit is the generation guard — which
        // is precisely what has to be tested, and cannot be if every abort
        // conveniently rejects.
        if (sig) sig.addEventListener("abort", () => {
          if (window.__ignoreAbort) return;
          const e = new Error("aborted"); e.name = "AbortError"; reject(e);
        });
      });
    }
    // 🔴 The discard is what decides which sentence the page is allowed to
    // show, so the test has to be able to make it fail in each of the ways a
    // phone actually fails: no connection, a server error, and a first attempt
    // that fails followed by one that works.
    if (String(url).indexOf("/voice/discard") === 0) {
      window.__discardCalls = (window.__discardCalls || 0) + 1;
      const mode = window.__discardMode || "ok";
      if (mode === "reject") return Promise.reject(new TypeError("Failed to fetch"));
      if (mode === "500") return Promise.resolve({ok: false, status: 500,
                                                  json: () => Promise.resolve({})});
      if (mode === "fail-then-ok" && window.__discardCalls === 1) {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      const answer = {ok: true,
        json: () => Promise.resolve({state: "discarded", outcome: "blocked"})};
      // 🔴 `__holdDiscard` keeps the answer in the air so a test can stand
      // between the two sentences. Without it the round trip resolves in the
      // same tick and the provisional line is never observable — which is the
      // only reason it looked like the page skipped it.
      if (window.__holdDiscard) {
        return new Promise(resolve => { window.__releaseDiscard = () => resolve(answer); });
      }
      return Promise.resolve(answer);
    }
    if (String(url).indexOf("/say") === 0) {
      if (window.__sayMode === "500") {
        return Promise.resolve({ok: false, status: 500,
                                json: () => Promise.resolve({})});
      }
      return Promise.resolve({ok: true, json: () => Promise.resolve(
        {line: "I heard you.", unsure: false, food: "grass"})});
    }
    return Promise.resolve({ok: true, json: () => Promise.resolve({})});
  },
  config: () => Promise.resolve({auth_required: false}),
  startSession: () => Promise.resolve(),
  boot: () => Promise.resolve({signedIn: true, temporary: true, mode: "anon"}),
};
"""

DISCARDS = 'window.__requests.filter(r => r.url.indexOf("/voice/discard") === 0)'


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def html():
    return _page_html()


@pytest.fixture()
def page(browser, html):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    pg.route("**/", lambda route: route.fulfill(
        status=200, content_type="text/html; charset=utf-8", body=html))
    pg.add_init_script(HARNESS)
    pg.goto(ORIGIN)
    pg.wait_for_timeout(500)
    yield pg
    ctx.close()


def start_a_wait(pg, note="Folded the laundry for 20 minutes"):
    """Type a note and send it, leaving the page inside `Taking it in…`."""
    pg.click("#entry")
    pg.fill("#note", note)
    pg.click("#sendBtn")
    pg.wait_for_timeout(300)


# --------------------------------------------------------------- cancelling --
def test_the_way_out_appears_only_while_it_is_thinking(page):
    assert page.eval_on_selector("#waitRow", "el => el.hidden") is True
    start_a_wait(page)
    assert page.eval_on_selector("#waitRow", "el => el.hidden") is False
    assert page.is_visible("#stopBtn")


def test_cancelling_tells_the_server_and_lets_the_rabbit_go(page):
    start_a_wait(page)
    page.click("#stopBtn")
    page.wait_for_timeout(400)

    sent = page.evaluate(DISCARDS)
    assert len(sent) == 1, sent
    assert sent[0]["method"] == "POST"
    assert "capture_id" in sent[0]["body"]
    assert page.eval_on_selector("body", "el => el.dataset.rabbit") == "rabbit_idle"
    assert page.eval_on_selector("#waitRow", "el => el.hidden") is True


@pytest.mark.parametrize("abort_lands", [True, False])
def test_an_answer_that_arrives_after_cancel_changes_nothing(page, abort_lands):
    """
    🔴 The regression this file exists for, in both of its shapes.

    `abort_lands=True`  the ordinary one: `abort()` rejects the fetch and the
                        rejection is what has to be recognised as deliberate
                        rather than shown as a failure.
    `abort_lands=False` 🔴 the one that is easy to miss and impossible to
                        prevent: the response had already arrived when cancel
                        was pressed. Aborting cannot unring that bell. Nothing
                        stands between that answer and the rabbit except the
                        generation the wait was given, so this case is the only
                        thing that actually tests it.
    """
    page.evaluate("window.__ignoreAbort = %s" % ("false" if abort_lands else "true"))
    start_a_wait(page)
    page.click("#stopBtn")
    page.wait_for_timeout(300)

    page.evaluate("window.__release && window.__release()")   # the model answers, late
    page.wait_for_timeout(600)

    assert page.eval_on_selector("body", "el => el.dataset.rabbit") == "rabbit_idle", \
        "a late answer fed the rabbit for a cancelled capture"
    assert page.eval_on_selector("#waitRow", "el => el.hidden") is True
    assert page.eval_on_selector("#confirmRow", "el => el.hidden") is True

    # 🔴 And what it says matters. Without the guard in the `catch`, an abort
    # reads as a failed request and the person is told Mallow could not record
    # that — a lie about something they chose. The line has to stay the one
    # cancel put there.
    import i18n
    text = page.eval_on_selector("#mallowText", "el => el.textContent").strip()
    assert text == i18n.t("discarded_note", "en"), text


def _mallow_text(pg):
    return pg.eval_on_selector("#mallowText", "el => el.textContent").strip()


@pytest.mark.parametrize("mode", ["reject", "500"])
def test_a_cancel_that_could_not_be_delivered_does_not_claim_success(page, mode):
    """
    🔴 The regression CodeX caught on 2026-08-29.

    The page used to say "Nothing was recorded" the instant the button was
    pressed, and swallow every error from the discard. A dropped connection or
    a 500 therefore produced a screen claiming the capture was cancelled while
    the server went on and committed it — the exact failure this feature exists
    to remove, reintroduced in its own last line.
    """
    import i18n
    page.evaluate("window.__discardMode = %r" % mode)
    start_a_wait(page)
    page.click("#stopBtn")

    # The screen is freed at once and says only that the wait is over.
    page.wait_for_timeout(200)
    assert _mallow_text(page) == i18n.t("stopped_waiting", "en")
    assert page.eval_on_selector("body", "el => el.dataset.rabbit") == "rabbit_idle"

    # Three bounded attempts, 400ms and 800ms apart, then the honest ending.
    page.wait_for_timeout(3000)
    assert _mallow_text(page) == i18n.t("discard_unconfirmed", "en")
    assert _mallow_text(page) != i18n.t("discarded_note", "en")
    assert page.evaluate("window.__discardCalls") == 3, "it must be bounded, and it must try"


def test_a_cancel_that_succeeds_on_the_second_try_is_still_a_cancel(page):
    """The ordinary phone case: a few seconds of no signal, then it goes."""
    import i18n
    page.evaluate("window.__discardMode = 'fail-then-ok'")
    start_a_wait(page)
    page.click("#stopBtn")
    page.wait_for_timeout(2500)
    assert _mallow_text(page) == i18n.t("discarded_note", "en")
    assert page.evaluate("window.__discardCalls") == 2


def test_the_definitive_sentence_only_appears_after_the_server_says_so(page):
    """
    Even on the happy path the order matters: provisional first, definitive
    only once the server has answered.
    """
    import i18n
    page.evaluate("window.__discardMode = 'ok'; window.__holdDiscard = true;")
    start_a_wait(page)
    page.click("#stopBtn")
    page.wait_for_timeout(400)

    # The server has not answered yet, and will not until this test says so.
    seen_first = _mallow_text(page)
    assert seen_first == i18n.t("stopped_waiting", "en"), seen_first
    assert page.eval_on_selector("body", "el => el.dataset.rabbit") == "rabbit_idle", \
        "the person is not held in the wait while this is unresolved"

    page.evaluate("window.__releaseDiscard()")
    page.wait_for_timeout(400)
    assert _mallow_text(page) == i18n.t("discarded_note", "en")


def test_cancelling_one_wait_cannot_cancel_the_next_one(page):
    """
    The cancel belongs to the wait it was pressed in. Once the page is back at
    the rabbit there is nothing to cancel, and a stale handler must not reach
    forward into whatever the person does next.
    """
    start_a_wait(page)
    page.click("#stopBtn")
    page.wait_for_timeout(300)
    first = len(page.evaluate(DISCARDS))

    page.evaluate("window.cancelWait && window.cancelWait()")   # a stale press
    page.wait_for_timeout(200)
    assert len(page.evaluate(DISCARDS)) == first, "a second cancel had nothing to cancel"


# ------------------------------------------------------------ say it again --
def _receipt_on_screen(pg):
    start_a_wait(pg)
    pg.evaluate("window.__release()")
    pg.wait_for_timeout(600)


def test_a_receipt_is_optional_and_has_only_the_two_exception_actions(page):
    _receipt_on_screen(page)
    assert page.locator("#okBtn").count() == 0
    assert page.is_visible("#discardBtn")
    assert page.is_visible("#editBtn")
    assert page.locator("#receiptLife").count() == 0


def test_a_receipt_makes_room_by_itself_without_changing_the_record(page):
    _receipt_on_screen(page)
    assert page.eval_on_selector("#mallowBubble", "el => el.classList.contains('on')")
    # Keep the production contract at eight seconds, but shorten this one
    # already-running timer so the suite does not spend eight seconds proving a
    # setTimeout can fire.
    page.evaluate("""
      clearTimeout(receiptTimer);
      receiptRemaining = 80;
      receiptStarted = performance.now();
      receiptTimer = setTimeout(quiet, receiptRemaining);
    """)
    page.wait_for_timeout(180)
    assert page.eval_on_selector("#mallowBubble", "el => el.classList.contains('on')") is False
    # Auto-dismiss is only presentation: no discard request was made.
    assert page.evaluate(f"{DISCARDS}.length") == 0


def test_touching_the_receipt_pauses_its_quiet_timer(page):
    _receipt_on_screen(page)
    page.dispatch_event("#mallowBubble", "pointerdown", {"pointerType": "touch"})
    page.evaluate("receiptRemaining = 80")
    page.wait_for_timeout(150)
    assert page.eval_on_selector("#mallowBubble", "el => el.classList.contains('on')")
    page.dispatch_event("#mallowBubble", "pointerup", {"pointerType": "touch"})
    page.wait_for_timeout(180)
    assert page.eval_on_selector("#mallowBubble", "el => el.classList.contains('on')") is False


def test_dont_keep_this_waits_for_a_confirmed_discard(page):
    import i18n
    _receipt_on_screen(page)
    page.evaluate("window.__holdDiscard = true")
    page.click("#discardBtn")
    page.wait_for_timeout(150)
    assert _mallow_text(page) == i18n.t("discarding_note", "en")
    assert page.eval_on_selector("#confirmRow", "el => el.hidden") is True
    page.evaluate("window.__releaseDiscard()")
    page.wait_for_timeout(300)
    assert _mallow_text(page) == i18n.t("discarded_note", "en")


def test_a_failed_receipt_withdrawal_does_not_claim_it_was_removed(page):
    import i18n
    page.evaluate("window.__discardMode = '500'")
    _receipt_on_screen(page)
    page.click("#discardBtn")
    page.wait_for_timeout(3000)
    assert _mallow_text(page) == i18n.t("discard_unconfirmed", "en")
    assert _mallow_text(page) != i18n.t("discarded_note", "en")


def test_a_wording_failure_does_not_turn_a_saved_capture_into_cannot_record(page):
    import i18n
    page.evaluate("window.__sayMode = '500'")
    _receipt_on_screen(page)
    assert _mallow_text(page) == i18n.t("receipt_saved_reply_failed", "en")
    assert _mallow_text(page) != i18n.t("cannot_record", "en")
    assert page.is_visible("#discardBtn"), "the saved capture lost its way to withdraw"


def test_say_it_again_after_typing_gives_the_text_box_back(page):
    """
    🔴 This assertion is the inverse of the one that used to be here, and the
    inversion is the fix.

    `Say it again` was originally a keyboard for someone who had spoken — the
    worst possible swap for the person this is built for, whose hands are full.
    Q-42 corrected that by always returning the rabbit, and so broke the other
    half: someone who typed, quite possibly because she cannot speak where she
    is, was handed a rabbit to hold.

    The harness drives the typed path, so this is the typed case: the box comes
    back, carrying what she wrote, with the caret in it. Retyping the sentence
    is exactly the tax Q-42 set out to remove.
    """
    _receipt_on_screen(page)
    assert page.eval_on_selector("#confirmRow", "el => el.hidden") is False

    page.click("#editBtn")
    page.wait_for_timeout(300)

    assert page.eval_on_selector("#youBubble", "el => el.classList.contains('on')")
    assert page.eval_on_selector("#note", "el => el.value") == "Folded the laundry", \
        "her sentence has to come back with the box"
    assert page.evaluate(
        "document.activeElement === document.getElementById('note')"), \
        "the caret belongs where she is going to type"
    assert page.eval_on_selector("#confirmRow", "el => el.hidden") is True


def test_say_it_again_after_speaking_puts_the_keyboard_down(page):
    """
    The other branch, driven through the real `receipt()` with the origin the
    voice path passes.

    🔴 "The textarea is hidden" and "the field is not focused" are different
    claims, and on a phone only the second one closes the keyboard.
    """
    _receipt_on_screen(page)
    page.evaluate("async () => { await receipt(window.__reply, 'voice'); }")
    page.wait_for_timeout(200)

    page.click("#editBtn")
    page.wait_for_timeout(300)

    assert page.eval_on_selector("#youBubble", "el => el.classList.contains('on')") is False
    assert page.eval_on_selector("#note", "el => el.value") == "", "a draft was left behind"
    # Nothing inside the text bubble holds the focus, so there is nothing for a
    # phone to keep a keyboard open for.
    assert page.evaluate(
        "!document.getElementById('youBubble').contains(document.activeElement)")
    assert page.eval_on_selector("body", "el => el.dataset.rabbit") == "rabbit_idle"
    assert page.eval_on_selector("#confirmRow", "el => el.hidden") is True


def test_say_it_again_does_not_open_the_microphone_by_itself(page):
    """It stays a gesture. Nothing is recording until a finger lands."""
    _receipt_on_screen(page)
    page.click("#editBtn")
    page.wait_for_timeout(300)
    assert page.eval_on_selector("body", "el => el.classList.contains('pressing')") is False
    assert page.eval_on_selector("#recDot", "el => el.classList.contains('on')") is False


def test_say_it_again_says_what_to_do_next(page):
    """
    🔴 Only the spoken branch needs telling. The typed branch answers the same
    question by simply being a text box with a caret in it — a line saying
    "hold the rabbit" there would be the instruction pointing away from the
    thing that is actually waiting for her.
    """
    _receipt_on_screen(page)
    page.evaluate("async () => { await receipt(window.__reply, 'voice'); }")
    page.wait_for_timeout(200)
    page.click("#editBtn")
    page.wait_for_timeout(300)
    hint = page.eval_on_selector("#micHint", "el => el.textContent")
    assert hint.strip(), "somebody who pressed it has to be told the rabbit is waiting"
