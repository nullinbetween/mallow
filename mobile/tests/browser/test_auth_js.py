"""
The tests a Python client cannot run.

`auth.js` is the one part of Mallow that only exists inside a browser: module
imports, `initializeApp`, popup and redirect. A green Python suite says nothing
about whether sign-in works, which is exactly the gap that let a duplicate-app
bug sit unnoticed. These run the real file in a real engine.

Firebase itself is stubbed — reaching Google would need a project, and this is
about our code, not theirs. The stub counts calls and records what was asked
for, so the two things that matter can be asserted: the app is initialised once
however many times we ask, and sign-in asks only for identity scopes.

    pip install playwright && playwright install chromium
    python3 -m pytest mobile/tests/browser -q
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

AUTH_JS = Path(__file__).resolve().parents[2] / "static" / "auth.js"

# 🔴 A plain import, deliberately. This used to be `pytest.importorskip`, which
# turned "no browser engine here" into a skip — and a skip reads as a pass in
# every summary line anybody actually looks at. The release gate then reported
# green for a suite that had not run.
#
# If playwright is missing this module fails to collect, `./run.sh test` exits
# non-zero, and the person running it is told what to install. Not running
# these is a legitimate choice; it is made with `./run.sh test-python`, which
# says so out loud.
from playwright.sync_api import sync_playwright                    # noqa: E402

# A stand-in for the two Firebase modules, with counters. It replaces the
# gstatic imports, so nothing here touches the network.
STUB = """
window.__calls = {initializeApp: 0, getAuth: 0, popup: 0, redirect: 0,
                  reauth: 0, reauthRedirect: 0, anon: 0, link: 0,
                  signOut: 0, scopes: []};
window.__redirectResult = null;
window.__apps = [];
window.__firebaseAppModule = {
  initializeApp(cfg) { window.__calls.initializeApp++;
                       const a = {cfg}; window.__apps.push(a); return a; },
  getApps() { return window.__apps; },
};
class FakeProvider { constructor() { this.scopes = []; }
  addScope(s) { this.scopes.push(s); window.__calls.scopes.push(s); } }
FakeProvider.credentialFromResult = () => ({accessToken: "drive-token"});
window.__firebaseAuthModule = {
  getAuth(app) { window.__calls.getAuth++;
                 // A live getter, like the real Auth object: `currentUser`
                 // changes as the session does, and the app holds one instance.
                 const a = {app};
                 Object.defineProperty(a, "currentUser",
                   {get: () => window.__currentUser || null});
                 return a; },
  GoogleAuthProvider: FakeProvider,
  signInWithPopup(auth, p) { window.__calls.popup++;
                             if (window.__popupSignInError) {
                               const e = new Error("no");
                               e.code = window.__popupSignInError; return Promise.reject(e); }
                             return Promise.resolve({user: {uid: "u1", isAnonymous: false,
                                                            getIdToken: () => Promise.resolve("id-token")}}); },
  signInWithRedirect(auth, p) { window.__calls.redirect++; return Promise.resolve(); },
  linkWithPopup(user, p) { window.__calls.link++;
                           if (window.__linkError) {
                             const e = new Error("no");
                             e.code = window.__linkError; return Promise.reject(e); }
                           return Promise.resolve({user: {uid: user.uid, isAnonymous: false,
                                                          getIdToken: () => Promise.resolve("id-token")}}); },
  getRedirectResult() { return Promise.resolve(window.__redirectResult); },
  signInAnonymously(auth) { window.__calls.anon++;
                            return Promise.resolve({user: {uid: "anon1", isAnonymous: true,
                                                           getIdToken: () => Promise.resolve("anon-token")}}); },
  signOut() { window.__calls.signOut++; return Promise.resolve(); },
  onAuthStateChanged(auth, cb) { cb(window.__currentUser || null); return () => {}; },
  reauthenticateWithPopup(user, p) { window.__calls.reauth++;
                                     if (window.__popupError) {
                                       const e = new Error("no");
                                       e.code = window.__popupError; throw e; }
                                     return Promise.resolve({user}); },
  reauthenticateWithRedirect(user, p) { window.__calls.reauthRedirect++;
                                        return Promise.resolve(); },
};
"""

CONFIG = """
window.__configPayload = {
  firebase: {projectId: "demo-project", apiKey: "k", authDomain: "d"},
  signed_in: false, temporary: true, auth_required: true,
  storage: "local-file", cross_device: false,
};
window.__sessionResponseOk = true;
window.__clearResponseOk = true;
window.fetch = (url, opts) => {
  if (String(url).indexOf("/auth/config") !== -1) {
    return Promise.resolve({json: () => Promise.resolve(window.__configPayload)});
  }
  if (String(url).indexOf("/auth/session/clear") !== -1) {
    return Promise.resolve({ok: window.__clearResponseOk,
                            json: () => Promise.resolve({})});
  }
  if (String(url).indexOf("/auth/session") !== -1) {
    return Promise.resolve({ok: window.__sessionResponseOk,
                            json: () => Promise.resolve({})});
  }
  return Promise.resolve({ok: true, json: () => Promise.resolve({})});
};
"""


def _source() -> str:
    """auth.js with its dynamic imports pointed at the stub."""
    src = AUTH_JS.read_text()
    src = re.sub(r'import\("https://www\.gstatic\.com/firebasejs/[^"]*firebase-app\.js"\)',
                 "Promise.resolve(window.__firebaseAppModule)", src)
    src = re.sub(r'import\("https://www\.gstatic\.com/firebasejs/[^"]*firebase-auth\.js"\)',
                 "Promise.resolve(window.__firebaseAuthModule)", src)
    return src


ORIGIN = "https://mallow.test/index.html"


def _open(browser_ctx):
    """
    A real origin, not about:blank.

    Storage is denied on an opaque origin, and `auth.js` touches sessionStorage
    for the redirect flow — so the page is served from a routed https URL to
    match how it actually runs.
    """
    pg = browser_ctx.new_page()
    pg.route("**/index.html", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<!doctype html><html><body></body></html>"))
    pg.add_init_script(STUB + CONFIG)
    pg.goto(ORIGIN)
    pg.add_script_tag(content=_source())
    return pg


@pytest.fixture()
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        yield _open(ctx)
        browser.close()


def test_the_file_loads_and_exposes_the_api(page):
    assert page.evaluate("typeof window.Mallow") == "object"
    for fn in ("boot", "config", "mallowFetch", "signInWithGoogle",
               "browseAnonymously", "signOut"):
        assert page.evaluate(f"typeof window.Mallow.{fn}") == "function", fn
    for retired in ("linkWithGoogle", "requestDriveAccess"):
        assert page.evaluate(f"typeof window.Mallow.{retired}") == "undefined", retired


def test_firebase_is_initialised_once_across_boot_and_signin(page):
    """The duplicate-app bug, asserted where it would actually happen."""
    page.evaluate("""async () => {
        await window.Mallow.boot();
        await window.Mallow.signInWithGoogle();
        await window.Mallow.firebase();
    }""")
    calls = page.evaluate("window.__calls")
    assert calls["initializeApp"] == 1, calls
    assert calls["getAuth"] == 1, calls


def test_sign_in_asks_for_identity_only(page):
    # 🔴 `prepare()` first, and that is the fix, not a detail: the provider is
    # built during boot so the press itself opens the popup with nothing
    # awaited in front of it. iOS Safari will not open a popup asked for after
    # network I/O — the user activation is gone by then.
    page.evaluate("async () => { await window.Mallow.prepare();"
                  " await window.Mallow.signInWithGoogle(); }")
    scopes = page.evaluate("window.__calls.scopes")
    assert scopes == ["openid", "email", "profile"]
    assert not any("drive" in s for s in scopes)


def test_a_blocked_popup_is_reported_and_never_becomes_a_redirect(page):
    """
    🔴 The inverse of what this file used to require, and requiring it was the
    defect. The redirect cannot complete here — Cloud Run origin, firebaseapp
    `authDomain`, Safari blocking third-party storage — so escalating to it
    turns a recoverable "the browser blocked the window" into a round trip that
    loses the person *after* they have already authorised Google.

    A blocked popup is now simply reported, with the code intact so the page
    can say which of the failures it was.
    """
    page.evaluate("() => { window.__popupSignInError = 'auth/popup-blocked'; }")
    code = page.evaluate("""async () => {
        await window.Mallow.prepare();
        try { await window.Mallow.signInWithGoogle(); return null; }
        catch (e) { return e.code; }
    }""")
    assert code == "auth/popup-blocked"
    calls = page.evaluate("window.__calls")
    assert calls["redirect"] == 0, "🚫 a known-broken path is not a fallback"


def test_a_phone_signs_in_by_popup_like_everything_else():
    """
    🔴 The phone used to be sent straight to the redirect, because that is what
    Firebase recommended for mobile. It is the one browser where it certainly
    does not work here, and it is the browser Owner actually uses.
    """
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")
        pg = _open(ctx)
        pg.evaluate("async () => { await window.Mallow.prepare();"
                    " await window.Mallow.signInWithGoogle(); }")
        calls = pg.evaluate("window.__calls")
        assert calls["popup"] == 1 and calls["redirect"] == 0
        b.close()


def test_a_request_carries_the_id_token(page):
    got = page.evaluate("""async () => {
        await window.Mallow.prepare();
        await window.Mallow.signInWithGoogle();
        let seen = null;
        window.fetch = (url, opts) => { seen = opts; return Promise.resolve({}); };
        await window.Mallow.mallowFetch("/export.json");
        return seen.headers.get("Authorization");
    }""")
    assert got == "Bearer id-token"


def test_nothing_can_open_a_popup_before_the_provider_is_ready(page):
    """
    The guard behind the disabled button: if the SDK is not in yet, these
    refuse rather than awaiting an import mid-gesture and losing the popup.
    """
    msg = page.evaluate(
        "async () => { try { await window.Mallow.signInWithGoogle(); return null; }"
        " catch (e) { return e.message; } }")
    assert msg and "not ready" in msg
    assert page.evaluate("window.Mallow.ready()") is False
    page.evaluate("async () => { await window.Mallow.prepare(); }")
    assert page.evaluate("window.Mallow.ready()") is True


def test_boot_reports_when_firebase_exists_but_the_navigation_session_does_not(page):
    ready = page.evaluate("""async () => {
        window.__currentUser = {uid: "u1", isAnonymous: false,
          email: "her@example.com", getIdToken: () => Promise.resolve("id-token")};
        window.__sessionResponseOk = false;
        const state = await window.Mallow.boot();
        return state.sessionReady;
    }""")
    assert ready is False


def test_signout_rejects_when_the_navigation_cookie_was_not_cleared(page):
    message = page.evaluate("""async () => {
        await window.Mallow.prepare();
        window.__currentUser = {uid: "u1", isAnonymous: false};
        window.__clearResponseOk = false;
        try { await window.Mallow.signOut(); return null; }
        catch (e) { return e.message; }
    }""")
    assert message and "navigation session" in message
    assert page.evaluate("window.__calls.signOut") == 1
