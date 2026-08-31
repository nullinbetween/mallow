"""
The silence gate, run in a real engine against the real page.

🔴 Why this file exists.

On 2026-08-23, on the deployed app, someone held the rabbit and said nothing.
The recording that produced — several kilobytes of a quiet room, a perfectly
well-formed container — went to Gemini, which returned a fluent French sentence
about spending ten minutes sewing a button onto Thomas's coat. Mallow filed it,
issued grass for it, and wrote it into an append-only ledger as something that
person had said.

Every test in the repository was green. They were green because they all hand
the pipeline a model response; none of them can hold a microphone.

So the check is in the browser, where the samples are, and this file drives it
in a browser: a real Chromium, the real `index.html` the server renders, the
real page script. Only the four things the page cannot have in a test — the
microphone, MediaRecorder, the audio clock, and the network — are stubbed, and
they are stubbed to behave like a silent room, a fumbled tap, and an ordinary
sentence in turn.

The assertion that matters is not what appears on screen. It is that for
silence **no request is made at all**: a gate that lets the request through and
throws the answer away is not a gate.

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
    """The meadow exactly as the server renders it, script and all."""
    os.environ["MALLOW_FAKE_MODEL"] = "1"
    os.environ["MALLOW_EPHEMERAL"] = "1"
    os.environ.setdefault("MALLOW_SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("MALLOW_TASK_KEY", "test-task-key")
    for m in ("server", "ledger", "fake_model", "app", "identity",
              "workspaces", "export", "i18n", "reflection", "tasks",
              "firestore_store"):
        sys.modules.pop(m, None)
    server = importlib.import_module("server")
    html = server.app.test_client().get("/?lang=zh-Hant").get_data(as_text=True)
    # auth.js belongs to sign-in and needs Firebase; this file is about the
    # microphone. `window.Mallow` is provided by the harness instead.
    return re.sub(r'<script src="/static/auth\.js"></script>', "", html)


# Everything the page reaches for that a test cannot have: the microphone, the
# recorder, the analyser, and the network. Each is a stand-in, and each is
# honest about which situation it is standing in for.
HARNESS = """
window.__requests = [];
window.Mallow = {
  mallowFetch(url, opts) {
    window.__requests.push(String(url));
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(window.__reply || {
        state: "receipt", capture_id: "c", heard: "補了衛生紙",
        items: [{record_id: "r1", food: "grass", duration_minutes: null,
                 source: "補了衛生紙"}],
        withheld_fragments: 0}),
    });
  },
};

// How loud the imaginary room is. 0 is silence.
window.__level = 0;

class FakeAnalyser {
  constructor(ctx) { this.fftSize = 1024; this.ctx = ctx; }
  disconnect() {}
  getFloatTimeDomainData(buf) {
    // A suspended context hands back zeros however loud the room is.
    const v = this.ctx.state === "running" ? window.__level : 0;
    for (let i = 0; i < buf.length; i++) buf[i] = v;
  }
}
// 🔴 Modelled on iOS Safari: a context created outside a user gesture starts
// `suspended`, and a suspended analyser reports every sample as zero without
// saying so. `window.__gestureOpen` is true only while a gesture handler is on
// the stack, which is what the real platform checks.
window.__contexts = 0;
window.__gestureOpen = false;
window.AudioContext = class {
  constructor() {
    window.__contexts++;
    this.state = window.__gestureOpen ? "running" : "suspended";
    window.__lastCtx = this;
  }
  resume() {
    if (window.__gestureOpen) this.state = "running";
    return Promise.resolve();
  }
  createMediaStreamSource() { return {connect() {}}; }
  createAnalyser() { return new FakeAnalyser(this); }
  close() { return Promise.resolve(); }
};

// 🔴 `navigator.mediaDevices` is a read-only accessor on a secure origin, so a
// plain assignment is silently ignored and the page reaches the real
// microphone — which is refused, which looks exactly like the gate working.
// The first draft of this file did that and its silence test "passed" for the
// wrong reason. defineProperty is what actually replaces it.
// `window.__micDelay` stands in for the permission sheet: the milliseconds
// between asking for the microphone and getting it.
window.__micDelay = 0;
Object.defineProperty(navigator, "mediaDevices", {
  configurable: true,
  value: {
    getUserMedia: () => new Promise(r => setTimeout(
      () => r({getTracks: () => [{stop() {}}]}), window.__micDelay)),
  },
});

// Emits one blob big enough to clear the byte floor, so that the byte floor is
// never what these tests are measuring.
window.MediaRecorder = class {
  constructor() { this.state = "recording"; }
  start() {}
  stop() {
    this.state = "inactive";
    const bytes = new Uint8Array(window.__blobBytes === undefined
                                 ? 5000 : window.__blobBytes);
    if (this.ondataavailable) {
      this.ondataavailable({data: new Blob([bytes], {type: "audio/webm"})});
    }
    if (this.onstop) this.onstop();
  }
};
"""


@pytest.fixture(scope="module")
def html():
    return _page_html()


@pytest.fixture()
def page(html):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.route("**/", lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=html))
        pg.add_init_script(HARNESS)
        pg.goto(ORIGIN)
        yield pg
        browser.close()


def hold(pg, ms):
    """
    Press the rabbit, wait, let go — through the page's own listeners.

    `__gestureOpen` is true only while the pointerdown handler is running,
    which is what iOS Safari actually requires of an AudioContext. A page that
    opens its context after the first `await` misses this window.
    """
    pg.eval_on_selector("#hold", """el => {
        window.__gestureOpen = true;
        el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, pointerId: 1}));
        window.__gestureOpen = false;
    }""")
    pg.wait_for_timeout(ms)
    pg.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerup", {bubbles: true, pointerId: 1}));
    }""")
    pg.wait_for_timeout(250)


# ------------------------------------------------------- the decision itself --
def test_the_thresholds_the_page_uses_are_the_ones_under_test(page):
    """
    There is one copy of the gate and the test drives that copy.

    A test with its own thresholds would pass while the page shipped different
    ones, which is the failure mode this whole file exists to answer.
    """
    gate = page.evaluate("window.__mallowGate.GATE")
    assert gate == {"MIN_MS": 700, "RMS": 0.02, "MIN_FRAMES": 5}


@pytest.mark.parametrize("measurement,expected", [
    ({"measured": True, "ms": 3000, "loudFrames": 40}, "send"),
    ({"measured": True, "ms": 3000, "loudFrames": 0},  "too_quiet"),
    ({"measured": True, "ms": 3000, "loudFrames": 4},  "too_quiet"),
    ({"measured": True, "ms": 200,  "loudFrames": 40}, "too_short"),
    ({"measured": False, "ms": 3000, "loudFrames": 40}, "unmeasured"),
])
def test_the_verdicts(page, measurement, expected):
    """Including the one that matters most: no measurement is not a pass."""
    assert page.evaluate("m => window.__mallowGate.gateVerdict(m)",
                         measurement) == expected


# ------------------------------------------------- through the whole page ----
def test_a_silent_recording_never_reaches_the_model(page):
    """
    🔴 The regression. Held for a second and a half, in a silent room.

    No request. Not a request whose answer is discarded — none made. Nothing
    can be filed from a call that was never placed.
    """
    page.evaluate("window.__level = 0")
    hold(page, 1500)

    assert page.evaluate("window.__requests") == []
    assert page.eval_on_selector("#mallowText", "el => el.textContent") == \
        "這次我沒有收到聲音。可以再說一次，或改用打字告訴我。"


def test_the_sentence_it_shows_never_blames_the_person(page):
    page.evaluate("window.__level = 0")
    hold(page, 1500)
    said = page.eval_on_selector("#mallowText", "el => el.textContent")
    for banned in ("沒有什麼要記的", "沒聽出", "不值得"):
        assert banned not in said


def test_a_fumbled_tap_never_reaches_the_model(page):
    """Too short to be a sentence, however loud the room."""
    page.evaluate("window.__level = 0.5")
    hold(page, 120)
    assert page.evaluate("window.__requests") == []


def test_an_ordinary_sentence_still_goes_through(page):
    """
    A gate that refused real recordings would be worse than none. This is the
    control, and it is what stops the threshold being quietly raised.
    """
    page.evaluate("window.__level = 0.5")
    hold(page, 1200)
    urls = page.evaluate("window.__requests")
    assert [u for u in urls if u == "/voice"] == ["/voice"]
    assert any(u.startswith("/say") for u in urls), "and a receipt came back"


def test_without_a_way_to_measure_the_microphone_is_not_offered(page):
    """
    Fail closed.

    With no AudioContext there is no way to tell a sentence from a silent room,
    and the room is what produced an invented record. Voice is withdrawn and
    the text box opens: asking someone to type is a smaller harm than filing
    words they never said.
    """
    page.evaluate("window.AudioContext = undefined; window.webkitAudioContext = undefined")
    page.evaluate("window.__level = 0.5")
    hold(page, 1200)

    assert page.evaluate("window.__requests") == []
    assert page.eval_on_selector("#youBubble", "el => el.classList.contains('on')")


# ------------------------------------------------------------ the mic hint ---
def test_the_page_does_not_say_it_is_listening_while_permission_is_pending(page):
    """
    🔴 The window this has to be checked in is the one where it could lie.

    Asserting the hint after the microphone opened proves nothing: it is on by
    then either way. The only interval that distinguishes "shown when the
    recorder starts" from "shown when the button is pressed" is while the
    permission sheet is still up — so this holds getUserMedia open and looks
    during it.
    """
    page.evaluate("window.__micDelay = 600; window.__level = 0.5")
    page.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, pointerId: 1}));
    }""")

    page.wait_for_timeout(250)            # asked for, not yet granted
    # ⚠️ 2026-08-24, Q-20: this used to assert the hint was *off* here, using
    # "nothing on screen" as a stand-in for "not claiming to listen". Those are
    # not the same claim, and the gap between them was the bug — nothing on
    # screen is exactly what made the Owner let go mid-permission on an iPhone.
    # The page now answers the press immediately. It just may not answer it
    # with this sentence, and the mic mark still belongs to a running recorder.
    assert page.eval_on_selector("#micHint", "el => el.textContent") != \
        page.evaluate("() => S.listening_hint"), \
        "the page claimed to be listening before the microphone was open"
    assert not page.eval_on_selector("#recDot", "el => el.classList.contains('on')"), \
        "and showed the microphone mark before the microphone was open"

    page.wait_for_timeout(600)            # granted, recorder started
    assert page.eval_on_selector("#micHint", "el => el.classList.contains('on')")

    page.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerup", {bubbles: true, pointerId: 1}));
    }""")
    page.wait_for_timeout(250)
    assert not page.eval_on_selector("#micHint", "el => el.classList.contains('on')")


def test_the_page_only_says_it_is_listening_once_it_is(page):
    """The wording, and that it clears again when the press ends."""
    assert not page.eval_on_selector("#micHint", "el => el.classList.contains('on')")

    page.evaluate("window.__level = 0.5")
    page.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, pointerId: 1}));
    }""")
    page.wait_for_timeout(200)
    assert page.eval_on_selector("#micHint", "el => el.classList.contains('on')")
    assert "在聽了" in page.eval_on_selector("#micHint", "el => el.textContent")

    page.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerup", {bubbles: true, pointerId: 1}));
    }""")
    page.wait_for_timeout(250)
    assert not page.eval_on_selector("#micHint", "el => el.classList.contains('on')")


# ----------------------------------------------------- one capture, one row --
def test_a_recording_is_filed_under_one_capture_id_however_often_it_stops(page):
    """
    🔴 The duplicate. Two identical rows, two seconds apart, on the deployed
    app — in a ledger that cannot take a row back.

    The id used to be minted inside the send function, so a second send was a
    second capture and the server's replay guard never saw a repeat. It now
    belongs to the recording.
    """
    # 🔴 Wrapped in an arrow on purpose. Playwright calls an evaluate string
    # that parses as a function, and a script ending in `x = (a) => {…}` does —
    # so an unwrapped version of this ran the replacement once with `null`
    # before the page had done anything, and the recorded list began with a
    # request nobody made.
    page.evaluate("""() => {
        window.__ids = [];
        const real = window.Mallow.mallowFetch;
        window.Mallow.mallowFetch = (url, opts) => {
            if (opts && opts.body && opts.body.get) {
                window.__ids.push(opts.body.get("capture_id"));
            }
            return real(url, opts);
        };
    }""")
    page.evaluate("window.__level = 0.5")

    hold(page, 1200)
    hold(page, 1200)

    ids = page.evaluate("window.__ids")
    assert len(ids) == 2, "two recordings are two things to file"
    assert ids[0] != ids[1], "and they are different things"
    assert all(ids), "every submission carries an id"


def test_a_second_press_is_ignored_while_the_first_is_still_out(page):
    """
    In-flight guard. Two requests racing produce two capture ids and two rows,
    and the ledger keeps both.
    """
    page.evaluate("""() => {
        window.__requests = [];
        window.Mallow.mallowFetch = (url) => {
            window.__requests.push(String(url));
            return new Promise(r => setTimeout(() => r({
                ok: true,
                json: () => Promise.resolve({state: "receipt", capture_id: "c",
                                             heard: "x", items: [],
                                             withheld_fragments: 0}),
            }), 4000));
        };
    }""")
    page.evaluate("window.__level = 0.5")

    hold(page, 1000)                      # the answer is still four seconds away
    hold(page, 1000)                      # so this one must not start a second
    page.wait_for_timeout(400)

    assert page.evaluate("window.__requests") == ["/voice"], \
        "the second press had to wait for the first answer"


# =============== the typed path, which is where the duplicate happened =======
# 🔴 The two rows in the live ledger came from the typed box, not the
# microphone: "哄睡了兩小時" filed at 04:12:04 and again at 04:12:06, both
# active, in a journal that cannot take a row back.
#
# The id used to be minted inside the send function. Two presses were therefore
# two capture ids, the server's replay guard — keyed on the id, and correct —
# saw two different captures, and filed both. So the id now belongs to the note
# rather than to the press: pressing again after a failure is the same thing
# retried, and editing the note is a different thing to file.

def _type(pg, text):
    pg.eval_on_selector("#entry", "el => el.click()")
    pg.wait_for_timeout(80)
    pg.eval_on_selector("#note", """(el, t) => {
        el.value = t;
        el.dispatchEvent(new Event("input", {bubbles: true}));
    }""", text)


def _press_send(pg):
    pg.eval_on_selector("#sendBtn", "el => el.click()")


def test_retrying_the_same_note_reuses_its_capture_id(page):
    """
    A failure and a second press are one thing filed once, not two.

    Without this the retry is a new capture, the server has no way to know it
    has seen these words before, and the person who pressed twice because
    nothing happened the first time ends up with two rows.
    """
    page.evaluate("""() => {
        window.__ids = [];
        window.__fail = true;
        window.Mallow.mallowFetch = (url, opts) => {
            const body = JSON.parse(opts.body);
            // /say carries the same capture_id back; only the capture counts.
            if (String(url) === "/voice/text") window.__ids.push(body.capture_id);
            if (window.__fail) {
                return Promise.resolve({ok: false, json: () => Promise.resolve(
                    {state: "failure", capture_id: body.capture_id})});
            }
            return Promise.resolve({ok: true, json: () => Promise.resolve(
                {state: "receipt", capture_id: body.capture_id, heard: body.note,
                 items: [], withheld_fragments: 0})});
        };
    }""")

    _type(page, "哄睡了兩小時")
    _press_send(page)
    page.wait_for_timeout(250)

    page.evaluate("window.__fail = false")
    _press_send(page)
    page.wait_for_timeout(250)

    ids = page.evaluate("window.__ids")
    assert len(ids) == 2, "it was sent twice"
    assert ids[0] == ids[1], "and both times it was the same thing being filed"


def test_a_second_press_of_the_same_note_does_not_start_a_second_request(page):
    """The impatient double-tap: two presses, one request in flight, one row."""
    page.evaluate("""() => {
        window.__ids = [];
        window.Mallow.mallowFetch = (url, opts) => {
            if (String(url) === "/voice/text") {
                window.__ids.push(JSON.parse(opts.body).capture_id);
            }
            return new Promise(r => setTimeout(() => r({
                ok: true,
                json: () => Promise.resolve({state: "receipt", capture_id: "c",
                                             heard: "x", items: [],
                                             withheld_fragments: 0}),
            }), 4000));
        };
    }""")

    _type(page, "哄睡了兩小時")
    _press_send(page)
    page.wait_for_timeout(60)
    _press_send(page)
    _press_send(page)
    page.wait_for_timeout(300)

    assert len(page.evaluate("window.__ids")) == 1


def test_editing_the_note_makes_it_a_different_thing_to_file(page):
    """
    The other half of the rule, and the one that stops the fix going too far.

    Reusing an id forever would mean the server refused the second note as a
    replay, and someone who really did two things would keep only the first.
    """
    page.evaluate("""() => {
        window.__ids = [];
        window.Mallow.mallowFetch = (url, opts) => {
            const body = JSON.parse(opts.body);
            if (String(url) === "/voice/text") window.__ids.push(body.capture_id);
            return Promise.resolve({ok: false, json: () => Promise.resolve(
                {state: "failure", capture_id: body.capture_id})});
        };
    }""")

    _type(page, "哄睡了兩小時")
    _press_send(page)
    page.wait_for_timeout(200)

    _type(page, "摺了三十分鐘的衣服")
    _press_send(page)
    page.wait_for_timeout(200)

    ids = page.evaluate("window.__ids")
    assert len(ids) == 2 and ids[0] != ids[1]


def test_the_page_says_it_is_thinking_while_the_model_is(page):
    """
    Two to four seconds of a real model call with no feedback reads as a frozen
    app. Reported by the first person to use the deployed version.
    """
    page.evaluate("""() => {
        window.Mallow.mallowFetch = () => new Promise(r => setTimeout(() => r({
            ok: true,
            json: () => Promise.resolve({state: "receipt", capture_id: "c",
                                         heard: "x", items: [],
                                         withheld_fragments: 0}),
        }), 3000));
    }""")

    _type(page, "哄睡了兩小時")
    _press_send(page)
    page.wait_for_timeout(300)

    assert page.eval_on_selector("#thinking",
                                 "el => getComputedStyle(el).display") == "flex"
    assert page.eval_on_selector("#thinkingText", "el => el.textContent") == "聽進去了"
    assert page.eval_on_selector("#thinking .ear svg", "el => !!el")
    assert page.eval_on_selector("#mallowBubble", "el => el.classList.contains('on')")


def test_recording_and_thinking_do_not_say_the_same_thing(page):
    """
    🔴 Two states, two sentences, on purpose.

    While the microphone is open the page says "在聽了" — it really is
    listening. Once the press ends the microphone is closed, and saying
    "listening" there would invite someone to keep talking into a recorder
    that has stopped. The ear icon is for what was already heard.
    """
    recording = page.evaluate("() => window.__S ? null : null")   # noqa: F841
    strings = page.evaluate("() => ({hint: S.listening_hint, take: S.taking_it_in})")
    assert strings["hint"] != strings["take"]
    assert "在聽" in strings["hint"] and "聽進去" in strings["take"]


def test_the_microphone_mark_shows_only_while_recording(page):
    """A white mark above the rabbit, and only while the microphone is open."""
    assert not page.eval_on_selector("#recDot", "el => el.classList.contains('on')")

    page.evaluate("window.__level = 0.5")
    page.eval_on_selector("#hold", """el => {
        window.__gestureOpen = true;
        el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, pointerId: 1}));
        window.__gestureOpen = false;
    }""")
    page.wait_for_timeout(200)
    assert page.eval_on_selector("#recDot", "el => el.classList.contains('on')")

    page.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerup", {bubbles: true, pointerId: 1}));
    }""")
    page.wait_for_timeout(250)
    assert not page.eval_on_selector("#recDot", "el => el.classList.contains('on')")


def test_the_first_press_on_ios_says_it_is_ready_rather_than_nothing(page):
    """
    🔴 Q-16. On iOS the permission sheet covers the page, so the finger comes
    off it and the press ends before the microphone is granted. The guard that
    refuses to record a press nobody is holding then fires — correctly, and
    silently. Every first attempt failed with nothing on screen.
    """
    page.evaluate("window.__micDelay = 400; window.__level = 0.5")
    page.eval_on_selector("#hold", """el => {
        window.__gestureOpen = true;
        el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, pointerId: 1}));
        window.__gestureOpen = false;
    }""")
    page.wait_for_timeout(60)
    # the finger comes off while the sheet is still up
    page.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerup", {bubbles: true, pointerId: 1}));
    }""")
    page.wait_for_timeout(600)                 # permission resolves after that

    assert page.eval_on_selector("#micHint", "el => el.classList.contains('on')"), \
        "the first attempt must not fail silently"
    assert page.eval_on_selector("#micHint", "el => el.textContent") == \
        "好了，再按一次就可以說話"
    assert page.evaluate("window.__requests") == [], "and nothing was filed"


def test_typing_does_not_re_measure_the_box_during_ime_composition(page):
    """
    🔴 Q-18, the half that is ours.

    `height:auto` then reading `scrollHeight` forces a synchronous layout.
    Once per keystroke is tolerable with a Latin keyboard; pinyin fires input
    on every keystroke *and* every candidate change. Composition is skipped
    entirely, and everything else is collapsed to one pass per frame.
    """
    page.eval_on_selector("#entry", "el => el.click()")
    page.wait_for_timeout(80)

    page.eval_on_selector("#note", """el => {
        window.__layouts = 0;
        const d = Object.getOwnPropertyDescriptor(
            Object.getPrototypeOf(el), "scrollHeight")
          || Object.getOwnPropertyDescriptor(Element.prototype, "scrollHeight");
        Object.defineProperty(el, "scrollHeight", {
            configurable: true,
            get() { window.__layouts++; return d.get.call(this); },
        });
    }""")

    # composing: five input events, no measurement at all
    page.eval_on_selector("#note", """el => {
        el.dispatchEvent(new CompositionEvent("compositionstart"));
        for (let i = 0; i < 5; i++) {
            el.value += "h";
            el.dispatchEvent(new Event("input", {bubbles: true}));
        }
    }""")
    page.wait_for_timeout(120)
    assert page.evaluate("window.__layouts") == 0, \
        "the box was re-measured while an IME was still composing"

    # committed: one frame, a bounded number of reads
    page.eval_on_selector("#note", """el => {
        el.value = "哄睡了兩小時";
        el.dispatchEvent(new CompositionEvent("compositionend"));
    }""")
    page.wait_for_timeout(120)
    assert 0 < page.evaluate("window.__layouts") <= 2


def test_doing_the_same_thing_twice_is_still_two_things(page):
    """
    🔴 The limit on the duplicate fix, and the reason it is a limit.

    Reusing one id for identical words would make the server treat the second
    send as a replay of the first, and someone who really did put a child to
    bed for two hours on Monday and again on Tuesday would keep only Monday.
    Deduplicating by content is explicitly ruled out for exactly this reason:
    a repeated sentence is not a repeated submission.

    Once something is filed, the next send is a new thing to file — even if it
    reads the same.
    """
    page.evaluate("""() => {
        window.__ids = [];
        window.Mallow.mallowFetch = (url, opts) => {
            const body = JSON.parse(opts.body);
            if (String(url) === "/voice/text") window.__ids.push(body.capture_id);
            return Promise.resolve({ok: true, json: () => Promise.resolve(
                {state: "receipt", capture_id: body.capture_id || "c",
                 heard: "哄睡了兩小時", items: [], withheld_fragments: 0})});
        };
    }""")

    _type(page, "哄睡了兩小時")
    _press_send(page)
    page.wait_for_timeout(300)

    _type(page, "哄睡了兩小時")
    _press_send(page)
    page.wait_for_timeout(300)

    ids = page.evaluate("window.__ids")
    assert len(ids) == 2, "both sends went out"
    assert ids[0] != ids[1], "and the second is a new thing to file, not a replay"



# ================= iOS Safari, which is what this is used on ================
# 🔴 An AudioContext created outside a user gesture starts `suspended` on iOS
# Safari, and a suspended analyser hands back an array of zeros without
# complaining. The meter would read every frame as silence, the gate would
# refuse every recording, and because the gate fails closed the microphone
# would never work at all — not intermittently, never.
#
# `await getUserMedia` is what breaks the gesture chain, so the context is
# opened in the pointerdown handler before that await. These tests hold the
# page to that, because the failure is invisible from a desktop.

def test_the_audio_context_is_opened_inside_the_touch(page):
    """
    Not after the microphone is granted — by then the gesture is over.

    The permission sheet is held open here so the two moments are far apart and
    the test can tell which one the page used.
    """
    page.evaluate("window.__micDelay = 600; window.__level = 0.5")
    page.eval_on_selector("#hold", """el => {
        window.__gestureOpen = true;
        el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, pointerId: 1}));
        window.__gestureOpen = false;
    }""")

    page.wait_for_timeout(80)                      # permission still pending
    assert page.evaluate("window.__contexts") >= 1, \
        "the context must exist before the microphone is granted"
    assert page.evaluate("window.__lastCtx.state") == "running", \
        "and it must be running, not suspended"

    page.eval_on_selector("#hold", """el => {
        el.dispatchEvent(new PointerEvent("pointerup", {bubbles: true, pointerId: 1}));
    }""")


def test_speech_is_measured_on_a_context_opened_in_the_gesture(page):
    """The end of the same story: a real sentence goes through on iOS."""
    page.evaluate("window.__level = 0.5")
    hold(page, 1200)
    assert "/voice" in page.evaluate("window.__requests")


def test_one_context_for_the_whole_page_however_many_recordings(page):
    """iOS caps how many a page may open. Three recordings, one context."""
    page.evaluate("window.__level = 0.5")
    hold(page, 900)
    hold(page, 900)
    hold(page, 900)
    assert page.evaluate("window.__contexts") == 1


def test_a_meter_that_never_ran_is_not_reported_as_silence(page):
    """
    🔴 The distinction the whole fix turns on.

    If the context never reaches `running`, the analyser's zeros mean the meter
    was not measuring — not that the room was quiet. Reporting that as silence
    would be Mallow claiming to have heard nothing when it never listened. It
    is `unmeasured`, the gate fails closed, and the text box opens.
    """
    page.evaluate("""() => {
        // A context that refuses to resume, like one opened after the gesture.
        window.AudioContext = class {
            constructor() { this.state = "suspended"; window.__lastCtx = this; }
            resume() { return Promise.resolve(); }
            createMediaStreamSource() { return {connect() {}}; }
            createAnalyser() { return {fftSize: 1024, disconnect() {},
                getFloatTimeDomainData(b) { for (let i = 0; i < b.length; i++) b[i] = 0; }}; }
            close() { return Promise.resolve(); }
        };
        window.__requests = [];
    }""")
    page.evaluate("window.__level = 0.5")          # a loud room, unheard
    hold(page, 1500)

    assert page.evaluate("window.__requests") == [], "nothing may be filed"
    assert page.eval_on_selector("#youBubble", "el => el.classList.contains('on')"), \
        "and the text box opens, so the person is not simply stuck"


# ------------------------------------------------------------------- Q-20 ----
# 🔴 2026-08-24, on the deployed app, on an iPhone.
#
#   "按兔子按下去沒有一個反饋，所以我不知道螢幕究竟有沒有辨識到我的手指。
#    那 1-2 秒我不知道有沒有按到，然後鬆手，就錯過了錄音的提示。"
#
# `#recDot` cannot honestly appear before MediaRecorder is running, and
# `getUserMedia` takes one to two seconds on a phone. So the press produced
# nothing at all, the finger came off, and the guard that refuses to record a
# press nobody is holding fired — correctly, and silently.
#
# The fix is not a faster microphone. It is an answer that does not wait for
# one. Every test below therefore holds the microphone open (`__micDelay`) and
# asserts what is on screen *while it is still pending*.

def _press(pg):
    """pointerdown and read the page back in the same synchronous turn.

    🔴 No `await`, no timeout, no animation frame between the event and the
    assertions. If the answer needed any of those, this returns it missing —
    which is the bug Q-20 is about.
    """
    return pg.evaluate("""() => {
        const el = document.querySelector("#hold");
        window.__gestureOpen = true;
        el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, pointerId: 1}));
        window.__gestureOpen = false;
        return {
            pressing: document.body.classList.contains("pressing"),
            pressed:  el.classList.contains("pressed"),
            hintOn:   document.querySelector("#micHint").classList.contains("on"),
            hint:     document.querySelector("#micHint").textContent,
            recDot:   document.querySelector("#recDot").classList.contains("on"),
        };
    }""")


def _release(pg):
    pg.eval_on_selector("#hold", """el => el.dispatchEvent(
        new PointerEvent("pointerup", {bubbles: true, pointerId: 1}))""")
    pg.wait_for_timeout(250)


def test_the_press_is_answered_before_the_microphone_is(page):
    """
    The whole of Q-20 in one assertion.

    The microphone is two seconds away and has not been granted. The page must
    already have said something, because the person is deciding *right now*
    whether their finger registered.
    """
    page.evaluate("window.__micDelay = 2000")
    state = _press(page)

    assert state["pressing"], "the body must carry the press in the same turn"
    assert state["hintOn"] and state["hint"], "and it must say something"
    assert state["hint"] == page.evaluate("() => S.hold_on")
    _release(page)


def test_what_it_says_is_keep_holding_not_please_wait(page):
    """
    🔴 The failure was a finger coming off, so the line's job is an
    instruction. A spinner, or "one moment", would describe the situation
    accurately and still lose the recording.
    """
    zh = page.evaluate("() => S.hold_on")
    assert "按著" in zh, f"the zh line must tell the person to keep holding: {zh!r}"


def test_the_press_answer_is_not_a_claim_to_be_listening(page):
    """
    🔴 The one thing this must never do is merge with `listening`.

    The microphone is not open yet. A white mic above the rabbit's head, or
    "在聽了，說吧", would be the app pretending to hear — which is the exact
    dishonesty `mic_ready` and the silence gate were both written to avoid.
    """
    page.evaluate("window.__micDelay = 2000")
    state = _press(page)
    assert not state["recDot"], "the microphone mark belongs to a running recorder"
    assert state["hint"] != page.evaluate("() => S.listening_hint")
    _release(page)


def test_the_answer_is_light_and_the_sprite_still_does_not_move(page):
    """
    🔴 Protects an older decision, not a new one.

    `#hold`'s comment has said since the rebuild: "The sprite never moves on
    press: a picture that jumps under the thumb reads as a bug, not as
    feedback." Q-20 needed feedback *and* that rule. So what arrives is the
    light around the rabbit, and the rabbit itself is still exactly where it
    was — to the pixel.
    """
    page.evaluate("window.__micDelay = 2000")
    # 🔴 Freeze the breathing first. It moves the rabbit by ~0.1px between any
    # two reads, which would make this test fail for a reason that has nothing
    # to do with the press — and, worse, would tempt the next person to add a
    # tolerance wide enough to hide a real jump.
    page.eval_on_selector(".breathe", "el => el.style.animation = 'none'")
    before = page.eval_on_selector(".sp.rabbit_idle", """el => {
        const r = el.getBoundingClientRect();
        return [r.x, r.y, r.width, r.height];
    }""")
    warm_before = page.eval_on_selector(
        "#warm", "el => getComputedStyle(el).opacity")

    _press(page)
    page.wait_for_timeout(200)                      # let the fade finish

    after = page.eval_on_selector(".sp.rabbit_idle", """el => {
        const r = el.getBoundingClientRect();
        return [r.x, r.y, r.width, r.height];
    }""")
    warm_after = page.eval_on_selector(
        "#warm", "el => getComputedStyle(el).opacity")

    assert after == before, "the rabbit may not move under the thumb"
    assert float(warm_before) == 0.0, "and it is dark until pressed"
    assert float(warm_after) > 0.5, "and lit while pressed"
    _release(page)


def test_the_warmth_is_behind_the_rabbit(page):
    """A wash *over* the artwork would change the art. It sits underneath."""
    assert page.eval_on_selector(
        "#warm", "el => el.parentElement.classList.contains('anchor')")
    assert page.eval_on_selector("#warm", """el => {
        const kids = [...el.parentElement.children];
        return kids.indexOf(el) < kids.indexOf(el.parentElement.querySelector('.breathe'));
    }""")
    assert page.eval_on_selector(
        "#warm", "el => getComputedStyle(el).pointerEvents") == "none", \
        "it must never intercept the press it is answering"


def test_letting_go_takes_the_warmth_with_it(page):
    """A glow still sitting under a lifted thumb is the page lying about state."""
    page.evaluate("window.__micDelay = 2000")
    _press(page)
    _release(page)
    assert not page.evaluate("document.body.classList.contains('pressing')")
    assert float(page.eval_on_selector(
        "#warm", "el => getComputedStyle(el).opacity")) == 0.0


def test_the_hint_hands_over_to_listening_once_the_recorder_runs(page):
    """
    Two states, and the second replaces the first rather than joining it.
    """
    page.evaluate("window.__micDelay = 60; window.__level = 0.5")
    _press(page)
    page.wait_for_timeout(400)                      # the microphone arrives

    assert page.eval_on_selector("#micHint", "el => el.textContent") == \
        page.evaluate("() => S.listening_hint")
    assert page.eval_on_selector("#recDot", "el => el.classList.contains('on')")
    _release(page)


def test_a_refused_microphone_stops_saying_keep_holding(page):
    """
    🔴 The corollary the first draft of this fix got wrong.

    The hint is shown before the microphone is asked for. If the request is
    then refused while the finger is still down, "keep holding" becomes an
    instruction to hold for something that will never arrive.
    """
    page.evaluate("""() => {
        Object.defineProperty(navigator, "mediaDevices", {
            configurable: true,
            value: {getUserMedia: () => Promise.reject(new Error("NotAllowedError"))},
        });
    }""")
    state = _press(page)
    assert state["hintOn"], "it still answers the press"

    page.wait_for_timeout(300)                      # the refusal lands
    assert not page.eval_on_selector("#micHint", "el => el.classList.contains('on')"), \
        "and takes the instruction back when there is nothing to hold for"
    _release(page)


def test_the_keyboard_gets_the_same_answer(page):
    """A keyboard cannot hold, but it can still be told what is happening."""
    page.evaluate("window.__micDelay = 2000")
    out = page.evaluate("""() => {
        const el = document.querySelector("#hold");
        window.__gestureOpen = true;
        el.dispatchEvent(new KeyboardEvent("keydown", {key: " ", bubbles: true}));
        window.__gestureOpen = false;
        return {pressing: document.body.classList.contains("pressing"),
                hint: document.querySelector("#micHint").textContent};
    }""")
    assert out["pressing"]
    assert out["hint"] == page.evaluate("() => S.hold_on")
