"""
The front door, and every way it can fail.

🔴 Why this file exists.

For a week the Google button did nothing. Not "showed an error nobody
understood" — nothing at all. The handler behind it was a promise chain with no
`catch`, so `auth/unauthorized-domain`, a blocked popup and a dead network all
produced the identical result: the gate, unchanged, exactly as before the press.
The only report anybody could make was "I pressed it and nothing happened", and
the root cause had to be measured from outside the product with an admin API,
because the product itself could not say one word about its own failure.

So these tests are not about sign-in succeeding. They are about sign-in
*failing out loud*. Every one of them injects a failure and asserts that the
person is told something true and is left with something to press.

🔴 And they hold the two layers apart, which is the other half of the ruling:

    Firebase identity   who Google says this is
    Mallow session      whether this app has a cookie for that person

Google can succeed and `/auth/session` still fail. At that moment the identity
has already changed — and on the anonymous path the link is permanent and
cannot be rolled back. Saying "sign-in failed" there would be false, and
re-opening the popup would be asking her to authorise something she already
authorised. So that state gets its own sentence and a retry that retries the
session and nothing else.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

from playwright.sync_api import sync_playwright                    # noqa: E402

MOBILE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(MOBILE), str(MOBILE.parent / "spike" / "voice")]

ORIGIN = "https://mallow.test/"


def _page_html(path="/?lang=en") -> str:
    """The meadow exactly as the server renders it, script and all."""
    os.environ["MALLOW_FAKE_MODEL"] = "1"
    os.environ["MALLOW_EPHEMERAL"] = "1"
    os.environ.setdefault("MALLOW_SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("MALLOW_TASK_KEY", "test-task-key")
    for m in ("server", "ledger", "fake_model", "app", "identity",
              "workspaces", "export", "i18n", "reflection", "tasks",
              "firestore_store", "reflection_schedule"):
        sys.modules.pop(m, None)
    server = importlib.import_module("server")
    html = server.app.test_client().get(path).get_data(as_text=True)
    # The real auth.js needs Firebase. `window.Mallow` comes from the harness
    # instead, so this file can decide exactly which step fails.
    return re.sub(r'<script src="/static/auth\.js"></script>', "", html)


# 🔴 Counters, not just outcomes. "It failed" and "it failed and then quietly
# opened a second popup" look the same on screen and are completely different
# products — the second one asks a person to authorise Google twice for one
# sign-in, after she already did.
HARNESS = """
window.__popupError = null;      // {code} to reject signInWithGoogle with
window.__linkError = null;       // {code} to reject linkWithGoogle with
window.__sessionOk = true;       // whether /auth/session succeeds
window.__sessionThrows = false;  // or fails by throwing, which is different
window.__signOutError = false;   // Firebase or cookie clearing did not finish

/* 🔴 The counters live in sessionStorage, and that is not a style choice.
 *
 * The last step of a successful sign-in is `location.reload()`, and `reload`
 * is unforgeable — it cannot be replaced, so the page really does reload and
 * anything held in a JS variable is wiped. A counter in memory therefore reads
 * zero for every flow that *worked*, which is exactly backwards. Storage
 * survives a same-origin reload; the reload itself is counted on the Python
 * side, by the route that serves the page. */
function __bump(k) {
  try {
    const c = JSON.parse(sessionStorage.getItem("__calls") || "{}");
    c[k] = (c[k] || 0) + 1;
    sessionStorage.setItem("__calls", JSON.stringify(c));
  } catch (e) {}
}
window.__callsNow = () => {
  const c = {popup: 0, link: 0, anon: 0, session: 0, signOut: 0};
  try { Object.assign(c, JSON.parse(sessionStorage.getItem("__calls") || "{}")); }
  catch (e) {}
  return c;
};

function __fail(code) { const e = new Error(code); e.code = code; return e; }

window.Mallow = {
  mallowFetch: (url, opts) => Promise.resolve({ok: true, json: () => Promise.resolve({})}),
  config: () => Promise.resolve({auth_required: true}),
  ready: () => true,
  prepare: () => Promise.resolve(true),
  boot: () => window.__boot === null
    ? Promise.reject(new Error("no identity"))
    : Promise.resolve(window.__boot),
  signInWithGoogle() {
    __bump("popup");
    return window.__popupError ? Promise.reject(__fail(window.__popupError))
                               : Promise.resolve({uid: "u1"});
  },
  linkWithGoogle() {
    __bump("link");
    return window.__linkError ? Promise.reject(__fail(window.__linkError))
                              : Promise.resolve({uid: "anon1"});
  },
  browseAnonymously() { __bump("anon"); return Promise.resolve({uid: "anon1"}); },
  startSession() {
    __bump("session");
    if (window.__sessionThrows) return Promise.reject(new Error("network"));
    return Promise.resolve(window.__sessionOk);
  },
  signOut() { __bump("signOut");
              return window.__signOutError
                ? Promise.reject(new Error("not cleared"))
                : Promise.resolve(); },
};
"""

GATE_STATE = {"mode": "firebase", "signedIn": False, "temporary": True,
              "anonymous": False, "email": None}
ANON_STATE = {"mode": "firebase", "signedIn": True, "temporary": True,
              "anonymous": True, "email": None}
GOOGLE_STATE = {"mode": "firebase", "signedIn": True, "temporary": False,
                "anonymous": False, "email": "her@example.com"}
GOOGLE_SESSION_FAILED = {**GOOGLE_STATE, "sessionReady": False}


class Meadow:
    def __init__(self, browser, html):
        self._browser, self._html = browser, html
        self._open = []

    def load(self, boot, *, html=None, destination=False, **flags):
        ctx = self._browser.new_context()
        self._open.append(ctx)
        pg = ctx.new_page()
        loads = []
        def serve(route):
            loads.append(1)
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=html or self._html)
        pg.route("**/", serve)
        if destination:
            pg.route("**/records?lang=en", lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8",
                body="<!doctype html><title>Records destination</title>"))
        # One load is the visit; every one after it is a reload the page asked for.
        pg.reloads = lambda: max(0, len(loads) - 1)
        pg.add_init_script(HARNESS)
        pg.add_init_script("window.__boot = %s;" % json.dumps(boot))
        for name, value in flags.items():
            pg.add_init_script("window.__%s = %s;" % (name, json.dumps(value)))
        pg.goto(ORIGIN)
        pg.wait_for_timeout(400)
        return pg

    def close(self):
        for ctx in self._open:
            ctx.close()


@pytest.fixture(scope="module")
def meadow():
    with sync_playwright() as p:
        b = p.chromium.launch()
        m = Meadow(b, _page_html())
        yield m
        m.close()
        b.close()


def _msg(pg):
    return pg.eval_on_selector("#authMsg", "el => el.hidden ? '' : el.textContent").strip()


# ======================= the gate says what went wrong =======================
def test_a_blocked_popup_is_named_and_the_gate_stays_usable(meadow):
    pg = meadow.load(GATE_STATE, popupError="auth/popup-blocked")
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)

    assert "blocked" in _msg(pg).lower(), "the person has to be told which failure this was"
    assert pg.eval_on_selector("#gGoogle", "el => el.disabled") is False, \
        "she has to be able to try again"
    assert pg.eval_on_selector("#gAnon", "el => el.disabled") is False, \
        "and the other way in must still be open"
    assert pg.reloads() == 0, "nothing succeeded, so nothing reloads"
    # 🚫 A popup failure is not a session failure. Offering "try connecting
    # again" here would point at the wrong thing.
    assert pg.eval_on_selector("#authRetry", "el => el.hidden") is True


def test_closing_the_popup_is_told_apart_from_the_browser_refusing_it(meadow):
    pg = meadow.load(GATE_STATE, popupError="auth/popup-closed-by-user")
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)
    said = _msg(pg).lower()
    assert said and "blocked" not in said, \
        "changing your mind and being refused are different, and read differently"


def test_an_unknown_failure_still_gets_a_sentence(meadow):
    """
    🔴 The specific defect: an unrecognised code must not fall through to
    silence. `auth/unauthorized-domain` — the real root cause — was exactly
    this shape, and it produced nothing for a week.
    """
    pg = meadow.load(GATE_STATE, popupError="auth/unauthorized-domain")
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)
    assert _msg(pg), "an unknown failure is still a failure the person can see"


# ================= Google succeeded, Mallow did not ==========================
def test_a_failed_session_does_not_claim_the_sign_in_failed(meadow):
    pg = meadow.load(GATE_STATE, sessionOk=False)
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)

    said = _msg(pg)
    assert said, "silence here is the original defect"
    assert "google" in said.lower(), \
        "it must say Google is done — the identity really has changed"
    assert pg.reloads() == 0, \
        "🚫 a reload here would show a signed-out page to a signed-in person"
    assert pg.eval_on_selector("#authRetry", "el => el.hidden") is False, \
        "the one action that can still help has to be on screen"


def test_the_retry_retries_the_session_and_never_the_popup(meadow):
    """
    🔴 The heart of the ruling. By now Firebase has already accepted her. A
    second popup would ask her to authorise Google again for a sign-in she
    completed, and on the anonymous path the link is already permanent — there
    is nothing left to authorise.
    """
    pg = meadow.load(GATE_STATE, sessionOk=False)
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)
    assert pg.evaluate("window.__callsNow().popup") == 1

    pg.click("#authRetry")
    pg.wait_for_timeout(300)
    calls = pg.evaluate("window.__callsNow()")
    assert calls["session"] == 2, "the retry has to actually retry the session"
    assert calls["popup"] == 1, "🚫 and must not open a second popup"
    assert calls["link"] == 0 and calls["anon"] == 0


def test_a_retry_that_works_finishes_the_sign_in(meadow):
    pg = meadow.load(GATE_STATE, sessionOk=False)
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)
    pg.evaluate("window.__sessionOk = true;")
    pg.click("#authRetry")
    pg.wait_for_timeout(300)
    assert pg.reloads() == 1
    assert pg.evaluate("window.__callsNow().popup") == 1


def test_a_session_that_throws_is_treated_like_one_that_refuses(meadow):
    """A dropped connection and a 500 are the same fact to the person."""
    pg = meadow.load(GATE_STATE, sessionThrows=True)
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)
    assert _msg(pg)
    assert pg.reloads() == 0
    assert pg.eval_on_selector("#authRetry", "el => el.hidden") is False


def test_a_signed_in_person_is_only_reloaded_once_the_cookie_exists(meadow):
    pg = meadow.load(GATE_STATE)
    pg.click("#gGoogle")
    pg.wait_for_timeout(300)
    assert pg.evaluate("window.__callsNow().session") == 1
    assert pg.reloads() == 1


def test_a_records_sign_in_returns_to_records_after_the_session_exists(meadow):
    """
    The server can carry `next=records` and the source can contain
    `location.assign`, while the branch that calls it is dead. Exercise the
    navigation: this is the last hop a string scan cannot prove.
    """
    pg = meadow.load(
        GATE_STATE,
        html=_page_html("/?lang=en&next=records"),
        destination=True,
    )
    pg.click("#gGoogle")
    pg.wait_for_timeout(500)
    assert pg.url == "https://mallow.test/records?lang=en"


# ==================== anonymous mode has one clear exit ======================
def test_an_anonymous_person_is_offered_only_the_exit(meadow):
    pg = meadow.load(ANON_STATE)
    assert pg.eval_on_selector("#gate", "el => el.hidden") is True
    pg.click("#settingsOpen")
    pg.wait_for_timeout(300)

    assert pg.eval_on_selector("#accountBox", "el => el.hidden") is False
    assert pg.eval_on_selector("#accountAnon", "el => el.hidden") is False
    assert pg.eval_on_selector("#accountGoogle", "el => el.hidden") is True
    assert pg.locator("#accountLink").count() == 0
    assert pg.eval_on_selector("#accountBody", "el => el.hidden") is True
    assert pg.text_content("#accountLeave").strip() == "Exit anonymous mode"


def test_a_google_account_is_not_offered_the_anonymous_actions(meadow):
    pg = meadow.load(GOOGLE_STATE)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(300)
    assert pg.eval_on_selector("#accountAnon", "el => el.hidden") is True
    assert pg.eval_on_selector("#accountGoogle", "el => el.hidden") is False
    # 🔴 And the temporary badge is a claim about her that is no longer true.
    assert pg.eval_on_selector("#tempPill", "el => el.hidden") is True


def test_the_temporary_badge_is_a_status_and_not_a_button(meadow):
    """
    Owner: 「我一直想吐槽 Lang 和 Settings 旁邊的 Temporary workspace pill
    是幹嘛的。」 It sat in the row of controls, dressed exactly like them, and
    did nothing when pressed. The information is worth keeping; the affordance
    was a lie.
    """
    pg = meadow.load(ANON_STATE)
    assert pg.eval_on_selector("#tempPill", "el => el.hidden") is False
    assert pg.eval_on_selector("#tempPill", "el => el.tagName") == "SPAN"
    assert pg.eval_on_selector("#tempPill", "el => el.getAttribute('role')") == "note"
    # The suite can run before sunrise. Force each visual state instead of
    # letting wall-clock time decide which half of this assertion is sampled.
    pg.evaluate("document.body.classList.remove('night')")
    style = pg.eval_on_selector(
        "#tempPill",
        "el => { const s = getComputedStyle(el);"
        " return {bg: s.backgroundColor, border: s.borderTopWidth,"
        "         cursor: s.cursor, size: parseFloat(s.fontSize),"
        "         color: s.color}; }")
    assert style["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), "no button fill"
    assert style["border"] == "0px", "no button edge"
    assert style["cursor"] != "pointer", "it does not offer to be pressed"
    lang = pg.eval_on_selector("#langSwitch", "el => parseFloat(getComputedStyle(el).fontSize)")
    assert style["size"] < lang, "quieter than the things that are actually pressable"
    day_rgb = [int(x) for x in re.findall(r"\d+", style["color"])[:3]]
    assert max(day_rgb) < 100, "daytime status must survive the white cloud"
    pg.evaluate("document.body.classList.add('night')")
    night = pg.eval_on_selector("#tempPill", "el => getComputedStyle(el).color")
    night_rgb = [int(x) for x in re.findall(r"\d+", night)[:3]]
    assert min(night_rgb) > 200, "night status must remain light on the dark sky"


def test_exiting_anonymous_mode_returns_to_the_front_door_without_google(meadow):
    pg = meadow.load(ANON_STATE)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(200)
    pg.click("#accountLeave")
    pg.wait_for_timeout(300)
    calls = pg.evaluate("window.__callsNow()")
    assert calls["signOut"] == 1
    assert calls["link"] == 0 and calls["popup"] == 0
    assert pg.reloads() == 1


def test_anonymous_exit_failure_stays_put_and_can_be_retried(meadow):
    pg = meadow.load(ANON_STATE, signOutError=True)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(200)
    pg.click("#accountLeave")
    pg.wait_for_timeout(250)

    said = pg.eval_on_selector("#accountMsg", "el => el.hidden ? '' : el.textContent")
    assert said.strip()
    assert pg.eval_on_selector("#accountLeave", "el => el.disabled") is False
    assert pg.reloads() == 0
    assert pg.evaluate("window.__callsNow().signOut") == 1


# =========================== resolving and failure ===========================
def test_the_buttons_do_not_offer_themselves_before_the_sdk_is_in(meadow):
    """
    The press must not have to await a dynamic import: iOS Safari will not open
    a popup asked for after network I/O. So the button waits instead of the
    person waiting mid-gesture.
    """
    pg = meadow.load(GATE_STATE, boot_unused=0)
    pg.evaluate("window.Mallow.ready = () => false; renderAuth();")
    assert pg.eval_on_selector("#gGoogle", "el => el.disabled") is True
    pg.evaluate("window.Mallow.ready = () => true; renderAuth();")
    assert pg.eval_on_selector("#gGoogle", "el => el.disabled") is False


def test_a_boot_failure_is_not_silence_either(meadow):
    """
    🔴 Distinct from "there is nobody here". Firebase could not be reached, so
    whether she has an identity is unknown — and the page says so rather than
    presenting an unexplained gate.
    """
    pg = meadow.load(None)
    assert pg.eval_on_selector("#gate", "el => el.hidden") is False
    assert _msg(pg), "a boot failure has to say something too"
    assert pg.eval_on_selector("#accountBox", "el => el.hidden") is True, \
        "🚫 no account actions when we do not know whose account it is"


def test_a_restored_identity_with_no_navigation_session_opens_the_recovery(meadow):
    pg = meadow.load(GOOGLE_SESSION_FAILED)
    assert pg.eval_on_selector("#settingsPanel", "el => el.classList.contains('on')") is True
    assert pg.eval_on_selector("#accountRetry", "el => el.hidden") is False
    said = pg.eval_on_selector("#accountMsg", "el => el.hidden ? '' : el.textContent")
    assert said.strip()


def test_signout_failure_stays_on_the_page_and_says_so(meadow):
    pg = meadow.load(GOOGLE_STATE, signOutError=True)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(200)
    pg.click("#accountSignOut")
    pg.wait_for_timeout(250)
    assert pg.reloads() == 0
    assert pg.eval_on_selector("#accountSignOut", "el => el.disabled") is False
    said = pg.eval_on_selector("#accountMsg", "el => el.hidden ? '' : el.textContent")
    assert said.strip()


def test_google_account_is_named_in_settings(meadow):
    pg = meadow.load(GOOGLE_STATE)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(200)
    words = pg.eval_on_selector("#accountBody", "el => el.textContent")
    assert "her@example.com" in words
