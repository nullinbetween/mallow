"""The records page withdraws one whole capture and never lies on failure."""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


MOBILE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(MOBILE), str(MOBILE.parent / "spike" / "voice")]
ORIGIN = "https://mallow-records.test/records"


def _records_html() -> str:
    os.environ["MALLOW_FAKE_MODEL"] = "1"
    os.environ["MALLOW_EPHEMERAL"] = "1"
    os.environ.setdefault("MALLOW_SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("MALLOW_TASK_KEY", "test-task-key")
    for name in ("server", "ledger", "fake_model", "app", "identity", "workspaces",
                 "export", "i18n", "reflection", "tasks", "firestore_store",
                 "reflection_schedule"):
        sys.modules.pop(name, None)
    server = importlib.import_module("server")
    client = server.app.test_client()
    filed = client.post("/voice/text?lang=en", json={
        "capture_id": "cap-record-page",
        "note": "Folded the laundry for 20 minutes",
    })
    assert filed.status_code == 200 and filed.get_json()["items"]
    html = client.get("/records?lang=en").get_data(as_text=True)
    return re.sub(r'<script src="/static/auth\.js"></script>', "", html)


HARNESS = """
function __recordAuthBump(k) {
  const c = JSON.parse(sessionStorage.getItem("recordAuth") || "{}");
  c[k] = (c[k] || 0) + 1;
  sessionStorage.setItem("recordAuth", JSON.stringify(c));
}
window.Mallow = {
  boot: () => Promise.resolve({signedIn:true, temporary:true, anonymous:true,
                               mode:"firebase", sessionReady:true}),
  ready: () => true,
  mallowFetch: (url, opts) => {
    if (String(url).indexOf("/voice/discard") === 0) {
      sessionStorage.setItem("lastRemove", String(opts.body || ""));
      if (window.__removeMode === "500") {
        return Promise.resolve({ok:false, status:500,
                                json:() => Promise.resolve({})});
      }
      if (window.__removeMode === "reject") {
        return Promise.reject(new TypeError("offline"));
      }
      return Promise.resolve({ok:true,
        json:() => Promise.resolve({state:"discarded", outcome:"compensated"})});
    }
    return Promise.resolve({ok:true, json:() => Promise.resolve({})});
  },
  startSession: () => { __recordAuthBump("session"); return Promise.resolve(true); },
  signOut: () => { __recordAuthBump("signOut"); return Promise.resolve(); },
};
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        instance = pw.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def html():
    return _records_html()


@pytest.fixture()
def page(browser, html):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.route("**/records*", lambda route: route.fulfill(
        status=200, content_type="text/html; charset=utf-8", body=html))
    page.route("https://mallow-records.test/?lang=en", lambda route: route.fulfill(
        status=200, content_type="text/html; charset=utf-8", body="<!doctype html><title>gate</title>"))
    page.add_init_script(HARNESS)
    page.goto(ORIGIN)
    page.wait_for_timeout(200)
    yield page
    context.close()


def test_remove_is_capture_level_and_starts_with_a_calm_confirmation(page):
    page.click(".remove-entry")
    assert page.is_visible("#removeDialog")
    assert page.get_attribute(".remove-entry", "data-capture-id") == "cap-record-page"
    record_ids = page.get_attribute(".remove-entry", "data-record-ids")
    assert record_ids and record_ids.startswith("[")
    page.click("#removeKeep")
    assert page.eval_on_selector("#removeDialog", "el => el.hidden") is True
    assert page.evaluate("sessionStorage.getItem('lastRemove')") is None


def test_a_failed_remove_keeps_the_entry_visible_and_says_it_is_unconfirmed(page):
    page.evaluate("window.__removeMode = '500'")
    page.click(".remove-entry")
    page.click("#removeConfirm")
    page.wait_for_timeout(1600)
    assert page.is_visible("#removeDialog")
    assert page.is_visible("#removeError")
    assert "could not confirm" in page.text_content("#removeError")
    assert page.is_visible(".capture")
    assert page.is_enabled("#removeConfirm")


def test_an_offline_remove_also_keeps_the_entry_and_restores_the_button(page):
    page.evaluate("window.__removeMode = 'reject'")
    page.click(".remove-entry")
    page.click("#removeConfirm")
    page.wait_for_timeout(1600)
    assert page.is_visible("#removeError")
    assert page.is_visible(".capture")
    assert page.is_enabled("#removeConfirm")


def test_a_confirmed_remove_sends_the_capture_and_visible_record_ids(page):
    page.click(".remove-entry")
    with page.expect_navigation():
        page.click("#removeConfirm")
    sent = page.evaluate("sessionStorage.getItem('lastRemove')")
    assert sent
    assert '"capture_id":"cap-record-page"' in sent
    assert '"record_ids":[' in sent


def test_records_page_exits_anonymous_mode_without_opening_google(page):
    with page.expect_navigation():
        page.click("#leaveAnonymous")
    calls = page.evaluate("JSON.parse(sessionStorage.getItem('recordAuth') || '{}')")
    assert calls.get("signOut") == 1
    assert calls.get("link", 0) == 0
    assert calls.get("popup", 0) == 0
    assert page.url == "https://mallow-records.test/?lang=en"
