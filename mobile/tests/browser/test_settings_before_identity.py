"""
Settings before there is anybody to save them for.

🔴 Why this file exists.

The settings button used to be revealed by one line inside the success branch
of `boot()`. Three separate things followed from that, and only the first was
noticed:

  * the pill row reflowed when the button appeared, so the app looked like it
    was still loading after it had finished;
  * somebody looking at the sign-in card was told, by an empty pill row, that
    this app has no settings at all;
  * any failure inside `boot()` — a blocked CDN, a private window, an offline
    moment — removed the button permanently for a person who was otherwise
    using the app perfectly well.

The fix is not to reveal the button earlier. It is that two questions were
being answered by one flag:

    Can this be seen and adjusted?      Nobody needs an identity for that.
    Which workspace does it save to?    That needs one, and nothing less.

So these tests are about the seam between those two. The assertion that
matters is never what is on screen; it is **which requests are made**. A panel
that opens with no workspace must ask the server for nothing at all — a 401
swallowed by a `try/catch` is not the same as a request that was never sent,
and only one of them is what the strategist ruled on 2026-08-29.

    pip install playwright && playwright install chromium
    python3 -m pytest mobile/tests/browser -q
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


def _page_html() -> str:
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
    html = server.app.test_client().get("/?lang=en").get_data(as_text=True)
    # auth.js is the real sign-in and needs Firebase. `window.Mallow` is
    # supplied by the harness instead, so that this file can decide exactly
    # when an identity turns up — which is the whole subject.
    return re.sub(r'<script src="/static/auth\.js"></script>', "", html)


# `__requests` records method and body as well as the URL. "No request was
# made" and "a request was made and its answer thrown away" look identical from
# the screen and are completely different products.
HARNESS = """
window.__requests = [];
window.__postOk = true;
window.__getOk = true;           // whether the GET succeeds at all
window.__stored = null;          // what /settings/reflection returns on GET
window.__bootResolve = null;     // filled in by the "DEFER" mode below

window.Mallow = {
  mallowFetch(url, opts) {
    opts = opts || {};
    window.__requests.push({url: String(url),
                            method: (opts.method || "GET").toUpperCase(),
                            body: opts.body || null});
    if (String(url).indexOf("/settings/reflection") === 0
        && (opts.method || "GET").toUpperCase() === "POST") {
      return Promise.resolve({ok: window.__postOk, json: () => Promise.resolve({})});
    }
    if (String(url).indexOf("/settings/reflection") === 0) {
      return Promise.resolve({ok: window.__getOk,
                              json: () => Promise.resolve(window.__stored || {})});
    }
    return Promise.resolve({ok: true, json: () => Promise.resolve({})});
  },
  config: () => Promise.resolve({auth_required: true}),
  startSession: () => Promise.resolve(),
  signInWithGoogle: () => Promise.resolve(),
  browseAnonymously: () => Promise.resolve(),
  boot: () => window.__boot === null
    ? Promise.reject(new Error("no identity"))
    : window.__boot === "HANG"
      ? new Promise(() => {})            // still waiting, as on a slow phone
      : window.__boot === "DEFER"
        // 🔴 The one the resolving-window tests need: still waiting, and the
        // test decides when it stops. "HANG" can prove the panel shows nothing
        // yet; only this can prove the same open panel fills itself in when the
        // answer finally arrives, which is the half that was broken.
        ? new Promise(res => { window.__bootResolve = res; })
        : Promise.resolve(window.__boot),
};
"""

SETTINGS_REQUESTS = """
window.__requests.filter(r => r.url.indexOf("/settings/reflection") === 0)
"""

MARK = 'window.__mark = window.__requests.length'
AFTER_MARK = """
window.__requests.slice(window.__mark)
                 .filter(r => r.url.indexOf("/settings/reflection") === 0)
"""


def settings_calls_since_mark(pg):
    """Only the requests made after `mark(pg)` — i.e. caused by what came next."""
    return pg.evaluate(AFTER_MARK)


def mark(pg):
    pg.evaluate(MARK)


def opened_by_the_panel(pg):
    return len(settings_calls_since_mark(pg))


# 🔴 One browser for the module, a fresh context per case. The first draft
# started a `sync_playwright()` per page, and the second one raised inside the
# still-open loop of the first — six tests red for a reason that had nothing to
# do with settings.
@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def html():
    return _page_html()


class Meadow:
    """A page whose identity is decided before a line of the page script runs."""

    def __init__(self, browser, html):
        self._browser, self._html = browser, html
        self._open = []

    def load(self, boot, stored=None, post_ok=True, pending=None, get_ok=True):
        ctx = self._browser.new_context()
        self._open.append(ctx)
        pg = ctx.new_page()
        pg.route("**/", lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=self._html))
        pg.add_init_script(HARNESS)
        pg.add_init_script(
            "window.__boot = %s; window.__postOk = %s; window.__stored = %s;"
            " window.__getOk = %s;"
            % (json.dumps(boot), "true" if post_ok else "false", json.dumps(stored),
               "true" if get_ok else "false"))
        if pending is not None:
            pg.add_init_script(
                'try{ sessionStorage.setItem("mallow.pendingReflection", %s); }catch(e){}'
                % json.dumps(json.dumps(pending)))
        pg.goto(ORIGIN)
        pg.wait_for_timeout(400)
        return pg

    def close(self):
        for ctx in self._open:
            ctx.close()


@pytest.fixture()
def meadow(browser, html):
    m = Meadow(browser, html)
    yield m
    m.close()


@pytest.fixture()
def nobody(meadow):
    """`boot()` rejects: no Firebase, no identity, no workspace."""
    return meadow.load(None)


# ------------------------------------------------------------- visibility ---
def test_settings_is_there_before_anybody_has_chosen_a_workspace(nobody):
    """
    🔴 The regression. `boot()` failed, and the button is still there.

    This is the case that used to lose the button for good: the reveal lived in
    the success branch, so a person whose Firebase call fell over kept a working
    app with no way into its settings.
    """
    assert nobody.is_visible("#settingsOpen")
    assert nobody.eval_on_selector("#settingsOpen", "el => el.hidden") is False


def test_the_pill_row_does_not_move_when_an_identity_arrives(meadow):
    """
    The row is laid out once, at first paint. It used to gain a button when
    `boot()` resolved, which shifted every pill beside it and read as "still
    loading" several seconds after loading had finished.

    🔴 What is measured is the button, not the width of the row. The row does
    legitimately change: the DEMO pill and the temporary-workspace pill both
    come and go once the page knows what it is looking at. Asserting on the row
    would be asserting that those never change, which is a different claim and
    a false one. The question here is only whether the settings button is
    already laid out before `boot()` answers, and stays the same size after.
    """
    pending = meadow.load("HANG")
    box = pending.eval_on_selector("#settingsOpen", """el => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height, hidden: el.hidden};
    }""")
    assert box["hidden"] is False
    assert box["w"] > 0 and box["h"] > 0, \
        "the button has to occupy its place before boot() has answered anything"

    settled = meadow.load({"signedIn": True, "temporary": True, "mode": "anon"})
    after = settled.eval_on_selector("#settingsOpen", """el => {
        const r = el.getBoundingClientRect();
        return {w: r.width, h: r.height};
    }""")
    assert (box["w"], box["h"]) == (after["w"], after["h"]), \
        "the button changed size once boot() answered"


# ------------------------------------------------------- no identity, no ask -
def test_opening_settings_without_a_workspace_asks_the_server_for_nothing(nobody):
    """
    🔴 The one that cannot be faked by a try/catch.

    Before an identity exists `/settings/reflection` is a 401. Catching that
    and showing defaults would look identical on screen and would still be a
    401 in the log, a round trip on a cold connection, and a private endpoint
    called by somebody with no right to it. So: no request.
    """
    mark(nobody)
    nobody.click("#settingsOpen")
    nobody.wait_for_timeout(300)
    assert settings_calls_since_mark(nobody) == []
    assert nobody.eval_on_selector("#settingsPanel", "el => el.classList.contains('on')")


def test_the_panel_opens_on_the_product_defaults_not_on_blanks(nobody):
    """
    The defaults come from `reflection_schedule` through the template, so the
    panel and the server cannot disagree about what "default" means.
    """
    import reflection_schedule
    nobody.click("#settingsOpen")
    nobody.wait_for_timeout(300)
    assert nobody.eval_on_selector("#cadence", "el => el.value") \
        == reflection_schedule.DEFAULT_CADENCE
    assert nobody.eval_on_selector("#reflectionTime", "el => el.value") \
        == reflection_schedule.DEFAULT_TIME


def test_saving_without_a_workspace_says_so_instead_of_claiming_it_saved(nobody):
    """
    🔴 It is kept, and it is not claimed as saved. Those are different
    sentences and only one of them is true here.
    """
    nobody.click("#settingsOpen")
    nobody.wait_for_timeout(200)
    assert nobody.eval_on_selector("#settingsPending", "el => el.hidden") is False
    mark(nobody)
    nobody.select_option("#cadence", "monthly")
    nobody.click("#settingsSave")
    nobody.wait_for_timeout(300)
    assert settings_calls_since_mark(nobody) == [], "there is nowhere to save this yet"
    kept = nobody.evaluate('JSON.parse(sessionStorage.getItem("mallow.pendingReflection"))')
    assert kept["cadence"] == "monthly"


# ------------------------------------------- and then somebody chooses one ---
def test_a_choice_made_before_the_door_is_written_once_after_it(meadow):
    """
    Chosen on the way in, kept on the device, and written to the workspace the
    moment there is one. Once — not on every later page view, which is what a
    pending value that is never cleared would do.
    """
    pg = meadow.load({"signedIn": True, "temporary": True, "mode": "anon"},
                            pending={"cadence": "daily", "time_local": "21:30",
                                     "timezone": "Asia/Tokyo", "weekday": 2,
                                     "day_of_month": 4})
    posts = [r for r in pg.evaluate(SETTINGS_REQUESTS) if r["method"] == "POST"]
    assert len(posts) == 1, posts
    assert json.loads(posts[0]["body"])["cadence"] == "daily"
    assert pg.evaluate('sessionStorage.getItem("mallow.pendingReflection")') is None


def test_a_choice_is_not_written_to_a_workspace_that_never_arrived(meadow):
    """
    🔴 `boot()` failed, so there is no uid. The preference stays pending rather
    than being attached to whoever happens to turn up next.
    """
    pg = meadow.load(None, pending={"cadence": "daily", "time_local": "21:30",
                                           "timezone": "Asia/Tokyo", "weekday": 2,
                                           "day_of_month": 4})
    assert pg.evaluate(SETTINGS_REQUESTS) == []
    assert pg.evaluate('sessionStorage.getItem("mallow.pendingReflection")') is not None


def test_a_write_that_failed_is_still_pending(meadow):
    """
    Cleared on a confirmed write and on nothing else. A dropped connection at
    the moment of sign-in must not silently lose what somebody chose.
    """
    pg = meadow.load({"signedIn": True, "temporary": True}, post_ok=False,
                            pending={"cadence": "daily", "time_local": "21:30",
                                     "timezone": "Asia/Tokyo", "weekday": 2,
                                     "day_of_month": 4})
    assert [r for r in pg.evaluate(SETTINGS_REQUESTS) if r["method"] == "POST"]
    assert pg.evaluate('sessionStorage.getItem("mallow.pendingReflection")') is not None


def test_somebody_with_a_workspace_reads_their_own_settings_back(meadow):
    """
    The defaults are for people who have nowhere to save yet. Somebody who has
    saved before sees what they saved, not what the product suggests.
    """
    pg = meadow.load({"signedIn": True, "temporary": False},
                            stored={"cadence": "monthly", "time_local": "06:45",
                                    "weekday": 3, "day_of_month": 12})
    mark(pg)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(300)
    assert pg.eval_on_selector("#cadence", "el => el.value") == "monthly"
    assert pg.eval_on_selector("#reflectionTime", "el => el.value") == "06:45"
    assert pg.eval_on_selector("#settingsPending", "el => el.hidden") is True
    # `armLeafRefresh()` reads the same preference on boot, so what is counted
    # here is the request the panel itself caused, not every request on the page.
    assert opened_by_the_panel(pg) == 1


# ------------------------------------------------- while it is still resolving
#
# 🔴 Q-44. Owner, 2026-08-30, on the deployed build:
#
#     English, set daily 18:50, saved, reopened - correct. Switch to Chinese,
#     open settings - the default weekly panel. Cancel, open again - daily 18:50.
#
# Not a language bug. Changing language reloads the page, identity has to be
# resolved again, and the panel used to answer "no workspace yet" for the whole
# of that window - filling in the product defaults, which for anybody who had
# ever changed their schedule is a statement about their settings that is false.
#
# The strategist ruled it a release-required correction rather than cosmetic:
# "資料沒有遺失" does not offset "介面當下說了不真實的話".


def test_a_panel_opened_while_the_workspace_resolves_shows_no_values_at_all(meadow):
    """
    The resolving window itself. Loading is on screen, no field is, Save cannot
    be pressed - and no request is made, because there is still no uid to make
    it for.
    """
    pg = meadow.load("HANG")
    mark(pg)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(300)
    assert pg.eval_on_selector("#settingsPanel", "el => el.classList.contains('on')"), \
        "the panel still opens at once - the fix is never to delay or hide it"
    assert pg.eval_on_selector("#settingsLoading", "el => el.hidden") is False
    assert pg.eval_on_selector("#settingsForm", "el => el.hidden") is True, \
        "a field on screen here is a claim about a schedule nobody has read yet"
    assert pg.eval_on_selector("#settingsSave", "el => el.disabled") is True
    # 🔴 The sentence for "you have nowhere to save this" must not appear for
    # somebody who does have somewhere - it is simply not known yet.
    assert pg.eval_on_selector("#settingsPending", "el => el.hidden") is True
    assert settings_calls_since_mark(pg) == []


def test_the_open_panel_fills_itself_in_when_the_workspace_arrives(meadow):
    """
    The other half. The panel was opened during the window and is still open
    when the answer lands, so it has to update itself - not wait to be reopened,
    which is exactly what the Owner had to do.
    """
    pg = meadow.load("DEFER", stored={"cadence": "daily", "time_local": "18:50",
                                      "weekday": 0, "day_of_month": 1})
    pg.click("#settingsOpen")
    pg.wait_for_timeout(200)
    assert pg.eval_on_selector("#settingsForm", "el => el.hidden") is True
    assert pg.eval_on_selector("#settingsSave", "el => el.disabled") is True

    pg.evaluate('window.__bootResolve({signedIn: true, temporary: true, mode: "anon"})')
    pg.wait_for_timeout(500)

    assert pg.eval_on_selector("#cadence", "el => el.value") == "daily"
    assert pg.eval_on_selector("#reflectionTime", "el => el.value") == "18:50"
    assert pg.eval_on_selector("#settingsForm", "el => el.hidden") is False
    assert pg.eval_on_selector("#settingsSave", "el => el.disabled") is False, \
        "Save is only offered once the panel is showing something true"


def test_a_reload_never_flashes_a_default_before_the_saved_value(meadow):
    """
    🔴 The Owner's steps, as a test. What she saw was `weekly` - the product
    default - on a workspace whose stored cadence was `daily`.
    """
    pg = meadow.load("DEFER", stored={"cadence": "daily", "time_local": "18:50",
                                      "weekday": 0, "day_of_month": 1})
    pg.click("#settingsOpen")
    pg.wait_for_timeout(200)
    during = pg.evaluate("""() => ({
        formHidden: document.getElementById("settingsForm").hidden,
        cadence: document.getElementById("cadence").value,
    })""")
    assert during["formHidden"] is True
    assert during["cadence"] != "daily", "nothing has been read yet, so nothing is shown"

    pg.evaluate('window.__bootResolve({signedIn: true, temporary: false})')
    pg.wait_for_timeout(500)
    assert pg.eval_on_selector("#settingsForm", "el => el.hidden") is False
    assert pg.eval_on_selector("#cadence", "el => el.value") == "daily"


def test_a_read_that_failed_says_so_and_offers_no_default(meadow):
    """
    🔴 The failure mode that would have been worse than the bug. Falling back to
    defaults here puts a value the person never chose on screen, one Save away
    from replacing the schedule they did choose. So: say the read failed, show
    nothing, and refuse the write.
    """
    pg = meadow.load({"signedIn": True, "temporary": True},
                     stored={"cadence": "monthly", "time_local": "06:45"},
                     get_ok=False)
    mark(pg)
    pg.click("#settingsOpen")
    pg.wait_for_timeout(500)
    assert pg.eval_on_selector("#settingsFailed", "el => el.hidden") is False
    assert pg.eval_on_selector("#settingsForm", "el => el.hidden") is True
    assert pg.eval_on_selector("#settingsSave", "el => el.disabled") is True
    assert pg.eval_on_selector("#settingsPending", "el => el.hidden") is True
    assert len(settings_calls_since_mark(pg)) == 1, "it did try, once"
