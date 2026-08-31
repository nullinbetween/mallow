"""
Mallow — the product.

One screen. A rabbit in a meadow. Hold it, say what happened, let go.

This module stays thin where it can. Everything that decides anything already
exists and is already tested in `spike/voice`: the response contract, the one
model call, and the policy that turns a labour kind into grass or carrot. Those
view functions are mounted here unchanged rather than reimplemented, so the
product and the verified slice cannot drift apart.

What is added here:
  - the mobile page, the private record page, and the exports
  - identity: one verified uid per request, and a workspace that belongs to it
  - scheduled reflections: the one thing that happens with nobody watching
  - two languages over one template, because the words are furniture and the
    person's own sentences are not
  - the artwork route and the PWA plumbing
  - the sentence the rabbit says, assembled by table. No model writes Mallow's
    voice, for the same reason no model decides the food: the same receipt must
    always produce the same words.

Run:
    python3 mobile/server.py                       # http://127.0.0.1:8080
    MALLOW_FAKE_MODEL=1 python3 mobile/server.py   # no cloud project needed
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import (Flask, Response, jsonify, make_response, redirect,
                   render_template, request, send_from_directory, url_for)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ART = ROOT / "assets" / "art"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "spike" / "voice"))
import app as slice_                                                  # noqa: E402
import export as exporter                                             # noqa: E402
import fake_model                                                     # noqa: E402
import i18n                                                           # noqa: E402
import identity                                                       # noqa: E402
import reflection                                                     # noqa: E402
import reflection_schedule as reflection_schedule                     # noqa: E402
import tasks                                                          # noqa: E402
import workspaces                                                     # noqa: E402

app = Flask(__name__)
app.jinja_env.filters["canonical_clock"] = exporter.canonical_clock
app.jinja_env.filters["display_timestamp"] = exporter.display_timestamp

# Behind Cloud Run there is one reverse proxy in front of this process. Without
# this, `request.is_secure` and any generated URL describe the internal hop
# rather than what the browser actually did. Cookie security no longer depends
# on it (see `identity.secure_cookies`), but redirects and logging should still
# tell the truth.
if os.getenv("K_SERVICE"):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Refuse to start in a configuration that could never work. Both doors check
# their own: identity.py the sign-in side, tasks.py the scheduler side.
identity.preflight()
tasks.preflight()

# --------------------------------------------------------------- the store --
# One workspace per verified uid. The slice keeps its records in module-level
# dicts; those names are rebound to proxies that resolve, per request, to the
# calling person's own store. No route can name someone else's.
DEMO = fake_model.enabled()
DATA = Path(os.getenv("MALLOW_DATA_DIR", ROOT / "data" / "live"))
SUFFIX = "-demo" if DEMO else ""
EPHEMERAL = os.getenv("MALLOW_EPHEMERAL") == "1"       # tests; nothing touches disk
BACKEND = "firestore" if os.getenv("MALLOW_FIRESTORE") == "1" else "file"

# 🔴 Naming firestore and not getting one raises here rather than serving a file
# journal behind a page that promises otherwise.
workspaces.configure(None if EPHEMERAL else DATA, SUFFIX, backend=BACKEND)

slice_.RECORDS = workspaces.RECORDS
slice_.CAPTURES = workspaces.CAPTURES
slice_.COMMIT = workspaces.commit        # one transaction per capture
# 🔴 Cancelling is bound the same way, and for the same reason: the decision
# about a capture that is being committed and cancelled at once has to happen
# inside the store's own transaction, not in the view.
slice_.DISCARD = workspaces.discard


# ------------------------------------------------------------ storage truth --
# What the product may say about persistence is read off the store that is
# actually serving requests. There is no environment variable in this answer:
# a variable can say firestore while a file journal handles every write, and the
# page would then promise cross-device recovery that does not exist.
def storage() -> str:
    return workspaces.backend()


def cross_device() -> bool:
    return workspaces.cross_device()


# --------------------------------------------------------------- the model --
# The real adapter is the default. The deterministic one is reachable only by
# setting MALLOW_FAKE_MODEL=1 on purpose, and never as a silent fallback: a
# model failure sends the person to the text box, never to a guess.
if DEMO:
    slice_.understand = fake_model.understand
    slice_.understand_text = fake_model.understand_text

# The verified slice, mounted unchanged. Same code, same tests, same behaviour.
for rule, endpoint, view, methods in (
    ("/voice",         "voice",   slice_.voice,   ["POST"]),
    ("/voice/text",    "text",    slice_.text,    ["POST"]),
    ("/voice/cancel",  "cancel",  slice_.cancel,  ["POST"]),
    # 🔴 POST, so the session cookie cannot authorise it: that cookie is
    # good for GET and HEAD only, and cancelling somebody else's capture
    # from another site is exactly the thing that rule exists to stop.
    ("/voice/discard", "discard", slice_.discard, ["POST"]),
    ("/voice/state",   "state",   slice_.state,   ["GET"]),
    ("/voice/records", "raw",     slice_.records, ["GET"]),
    # 🔴 Two paths, one view, and the second one is not a convenience.
    #
    # On Cloud Run, `/healthz` never reaches the container: Google's frontend
    # answers it with its own 404 page before the request is proxied. Observed
    # 2026-08-23 on the deployed service — every sibling route in this loop
    # answered 401, `/healthz` answered a Google error page on both the
    # `run.app` URL formats, and nothing appeared in the app's request log.
    # Locally it works, which is exactly why it survived until deployment.
    #
    # `/health` is the path the runbook and any uptime check should use.
    # `/healthz` stays registered because it is correct everywhere else and
    # removing it would silently break a non-Cloud-Run deployment.
    ("/health",        "health",  slice_.healthz, ["GET"]),
    ("/healthz",       "healthz", slice_.healthz, ["GET"]),
):
    app.add_url_rule(rule, endpoint, view, methods=methods)


@app.errorhandler(identity.Unauthenticated)
def _unauthenticated(e):
    return jsonify({"error": "sign in first", "reason": str(e)}), 401


@app.errorhandler(tasks.NotTheScheduler)
def _not_the_scheduler(e):
    return jsonify({"error": "this endpoint is not for users"}), 403


@app.errorhandler(tasks.TaskEndpointOff)
def _task_off(e):
    return jsonify({"error": "the scheduled task is not configured here",
                    "reason": str(e)}), 503


# ------------------------------------------------------------------ language --
def lang() -> str:
    return getattr(request, "_mallow_lang", None) or i18n.from_request()


@app.before_request
def _pick_language():
    request._mallow_lang = i18n.from_request()          # type: ignore[attr-defined]


@app.context_processor
def _strings():
    """`t` in every template, already bound to this request's language."""
    current = lang()
    return {"t": lambda key, **fmt: i18n.t(key, current, **fmt),
            "lang": current,
            "html_lang": i18n.HTML_LANG[current],
            "other_lang": i18n.other(current),
            "script_strings": i18n.bundle(current, i18n.SCRIPT_KEYS)}


@app.after_request
def _remember_language(resp: Response) -> Response:
    """An explicit `?lang=` is remembered, so the choice survives a click."""
    chosen = i18n.normalise(request.args.get("lang"))
    if chosen and request.cookies.get(i18n.COOKIE) != chosen:
        resp.set_cookie(i18n.COOKIE, chosen, max_age=60 * 60 * 24 * 365,
                        samesite="Lax", secure=identity.secure_cookies())
    return resp


@app.after_request
def _carry_local_identity(resp: Response) -> Response:
    """
    Keep a local workspace reachable across requests on this browser.

    Only in local mode, and only when there is not one already: a Firebase
    identity never gets a cookie, because its uid lives in the token.
    """
    try:
        who = identity.current()
    except Exception:                                             # noqa: BLE001
        return resp
    if who.is_local and not request.cookies.get(identity.COOKIE):
        resp.set_cookie(identity.COOKIE, identity.sign_local(who.uid),
                        max_age=60 * 60 * 24 * 365, samesite="Lax",
                        httponly=True, secure=identity.secure_cookies())
    return resp


@app.after_request
def _private_responses_are_not_browser_cache(resp: Response) -> Response:
    """A service worker or shared browser cache must never replay private state."""
    private = ("/auth", "/garden", "/records", "/voice", "/export",
               "/settings", "/tasks")
    if request.path == "/" or request.path == "/sw.js" \
            or request.path.startswith(private):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------- what Mallow says --
# Short, and never a total. The rabbit reports that something was received and
# what kind of food it became — never how much has accumulated, never a score,
# never a category name. A running total on this screen would turn caring into
# a scoreboard, which is the one thing this product is not.
LINE_KEYS = {
    ("carrot", "grass"): "line_both",
    ("grass",):          "line_grass",
    ("carrot",):         "line_carrot",
    (): "line_plain",
}


def rabbit_line(receipt: dict, language: str = i18n.DEFAULT) -> dict:
    """
    One receipt in, one short line out, plus whether it needs confirming.

    Uncertain means Mallow heard something it could not classify, or part of
    what was said could not be filed. It then asks instead of telling — the
    person is the authority on their own day.

    The language changes which row of the table is read. It does not change
    which row: the same receipt maps to the same line in both, so a bug cannot
    hide in one language.
    """
    def s(key, **fmt):
        return i18n.t(key, language, **fmt)

    items = receipt.get("items") or []
    withheld = int(receipt.get("withheld_fragments") or 0)
    kinds = tuple(sorted({i.get("food") for i in items} & {"grass", "carrot"}))
    unsure = withheld > 0 or any(i.get("food") == "withheld" for i in items)
    occurred = []
    for item in items:
        value = item.get("occurred_at")
        if isinstance(value, str) and value.strip() and value.strip() not in occurred:
            occurred.append(value.strip())

    if not items:
        # 🔴 Two different failures, and telling them apart is the whole point.
        #
        # Nothing filed can mean the ear failed, or it can mean the person said
        # something real that simply is not an activity. One sentence used to
        # cover both, and it was "I did not catch anything to note in that one"
        # — which reads, to someone who just said "I am so tired", as: what you
        # said does not count. In a product about work nobody sees, that is the
        # injury it exists to name.
        #
        # The transcript is what separates them. It is Mallow's own record of
        # having heard, so it decides which sentence is true.
        heard = (receipt.get("heard") or "").strip()
        key = "heard_no_activity" if heard else "heard_nothing"
        return {"line": s(key), "unsure": False, "food": None}

    if unsure:
        heard = (items[0].get("source") or "").strip()
        line = (s("line_heard", heard=heard) + s("line_unsure")) if heard \
            else s("line_unsure")
    elif len(occurred) == 1 and exporter.canonical_clock(occurred[0]):
        # A clock time and a duration answer different questions.  The former
        # says when it happened; the latter says how long it took.  The old
        # receipt looked only at duration, so a correctly extracted 07:40 was
        # followed by "there was no time to note".  Acknowledge the clock
        # directly, without splicing model-written activity text into Mallow's
        # own voice.
        line = s("line_clock_time", time=exporter.canonical_clock(occurred[0]))
    elif occurred:
        line = s("line_time_description")
    elif "carrot" in kinds:
        # A carrot is named before the generic no-duration line is considered.
        # `mental_load` is semantic and may have a stated duration; ordering
        # here makes the food visible in both cases.
        #
        # The duration branch used to come first, which hid a carrot whenever
        # its item happened to have no duration. The rabbit switched sprites
        # and said nothing about it. Found on the deployed app on 2026-08-23.
        #
        # Grass is deliberately left alone. It can carry a duration, so
        # `line_grass` is reachable on its own, and for a chore with no minutes
        # the locked example says the reassurance is the right sentence:
        #   「整理了孩子明天要帶的東西」 → 「收好了。沒有提供時長也沒關係。」
        # Reordering grass as well would silently rewrite that decision.
        line = s(LINE_KEYS.get(kinds, LINE_KEYS[()]))
    elif all(i.get("duration_minutes") is None for i in items):
        # Nothing carried a duration. Say the thing that removes the guilt
        # rather than the thing that names the food — the locked examples are
        # explicit about which of the two belongs here.
        line = s("line_no_time")
    else:
        line = s(LINE_KEYS.get(kinds, LINE_KEYS[()]))

    food = "carrot" if "carrot" in kinds else ("grass" if "grass" in kinds else None)
    return {"line": line, "unsure": unsure, "food": food}


@app.post("/say")
def say():
    """The receipt goes in, the rabbit's sentence comes out. Deterministic."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("items", []), list):
        return jsonify({"error": "items must be an array"}), 400
    return jsonify(rabbit_line(body, lang())), 200


# ------------------------------------------------------------------- who am I --
@app.get("/auth/config")
def auth_config():
    """
    What the page needs to know before it can offer a way in.

    🔓 PUBLIC BOOTSTRAP. A signed-out person must be able to read this, or the
    sign-in button can never appear. The Firebase web config is public by
    design — it identifies the project, it does not authorise anything.
    Nothing secret is served here.
    """
    who = identity.optional()
    return jsonify({
        "firebase": {
            "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
            "apiKey": os.getenv("FIREBASE_API_KEY", ""),
            "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
        } if identity.configured() else None,
        "signed_in": who is not None,
        "mode": who.mode if who else None,
        "provider": who.provider if who else None,
        "temporary": who.is_anonymous if who else True,
        # True whenever a local workspace is not on offer — a configured
        # project closes that door by itself, not only the explicit flag.
        "auth_required": not identity.local_allowed(),
        "storage": storage(),
        "cross_device": cross_device(),
        "lang": lang(),
        "demo": DEMO,
    })


@app.post("/auth/session")
def start_session():
    """
    Turn a verified ID token into a cookie a plain navigation can carry.

    🔴 The bug this closes: an ID token can only be attached by JavaScript to a
    `fetch`. Clicking "records", downloading a PDF, or coming back from Google's
    redirect are all plain navigations — no script, no header, no token — so in
    a real deployment a signed-in person got a 401 on the entire private half of
    the product. It never showed up locally, where a cookie already existed.

    The token is verified here, by the same code as every other request. What
    the cookie carries is the uid that came out of that check, signed by this
    server, and it is accepted for GET and HEAD only.
    """
    who = identity.current()
    if who.mode != "firebase":
        return jsonify({"error": "no token to exchange"}), 400
    resp = jsonify({"ok": True, "provider": who.provider})
    resp.set_cookie(identity.SESSION_COOKIE, identity.issue_session(who),
                    max_age=identity.SESSION_MAX_AGE, samesite="Lax",
                    httponly=True, secure=identity.secure_cookies())
    return resp, 200


@app.post("/auth/session/clear")
def clear_session():
    """🔓 Signing out. Deliberately needs no identity — a stale or half-broken
    session must always be discardable."""
    resp = jsonify({"ok": True})
    resp.delete_cookie(identity.SESSION_COOKIE, samesite="Lax")
    return resp, 200


@app.get("/whoami")
def whoami():
    who = identity.current()
    return jsonify({"uid": who.uid, "provider": who.provider,
                    "mode": who.mode, "temporary": who.is_anonymous})


# -------------------------------------------------------- scheduled leaf ----
# The mechanism and the rule (`MALLOW-REFLECTION-002`, reflection.py). A leaf is made
# by the scheduled task and by nothing else: there is no route here that creates
# one, and no query parameter that conjures one, because a leaf a person asked
# for is not the thing this product is claiming to do.
@app.get("/garden")
def garden():
    """Up to five visible reflections; identity always selects the garden."""
    identity.current()
    state = workspaces.current().garden.read()
    from ledger import visible_leaves
    out = []
    current = lang()
    for leaf in visible_leaves(state):
        # Both languages were written in the same model call, at the moment the
        # task ran — there is no reader to ask when a scheduled job fires, and
        # a leaf that comes out in the wrong language for the phone holding it
        # is a bug rather than a translation problem.
        body = leaf.get("body_zh" if current == "zh-Hant" else "body") \
            or leaf.get("body", "")
        out.append({"summary_id": leaf.get("summary_id"),
                    "title": i18n.t("leaf_title", current), "body": body})
    synthetic = bool(workspaces.current().preferences.read().get(
        "synthetic_demo_workspace"))
    return jsonify({"leaves": out, "leaf": out[-1] if out else None,
                    "leaf_rule": reflection.RULE_ID,
                    "demo": DEMO or synthetic}), 200


@app.post("/garden/seen")
def garden_seen():
    """Put away one visible token; the immutable reflection remains stored."""
    identity.current()
    body = request.get_json(silent=True) or {}
    summary_id = str(body.get("summary_id") or "").strip()
    if not summary_id:
        from ledger import visible_leaves
        leaves = visible_leaves(workspaces.current().garden.read())
        summary_id = str(leaves[-1].get("summary_id", "")) if leaves else ""
    if not summary_id:
        return jsonify({"ok": True, "put_away": False}), 200
    reflection.put_away(workspaces.current(), summary_id)
    return jsonify({"ok": True, "put_away": True}), 200


# ---------------------------------------------------- reflection settings --
@app.get("/settings/reflection")
def reflection_settings():
    identity.current()
    pref = reflection_schedule.read(workspaces.current(),
                                    now=reflection.now_jst(),
                                    persist_default=True)
    return jsonify(pref), 200


@app.post("/settings/reflection")
def update_reflection_settings():
    identity.current()
    body = request.get_json(silent=True) or {}
    try:
        pref = reflection_schedule.save(
            workspaces.current(),
            str(body.get("cadence", "")),
            str(body.get("time_local", "")),
            str(body.get("timezone", "")),
            now=reflection.now_jst(),
            weekday=body.get("weekday"),
            day_of_month=body.get("day_of_month"),
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(pref), 200


@app.post("/settings/display-timezone")
def update_display_timezone():
    """
    The page telling the server which clock its reader is actually looking at.

    🔒 Deliberately not a location. An IANA zone name is a city-sized fact the
    browser already publishes to every page it loads; it is used here to print
    a timestamp and nothing else. It never enters a record, a fact pack, a
    prompt, an export column or a log line.
    """
    identity.current()
    body = request.get_json(silent=True) or {}
    try:
        pref = reflection_schedule.remember_display_timezone(
            workspaces.current(), str(body.get("timezone", "")),
            now=reflection.now_jst())
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"display_timezone": pref.get("display_timezone")}), 200


# ------------------------------------------------------- the scheduled task --
@app.post("/tasks/reflections")
@app.post("/tasks/weekly-reflection")
def scheduled_reflections():
    """
    Cloud Scheduler calls this. No person can.

    It walks every workspace. Each saved preference decides whether that
    workspace is due; deterministic content gates decide whether it has new
    material. The model never controls either decision.

    🔒 `tasks.caller()` is a different door from `identity`: a Firebase user
    token is a valid credential for that one and is refused here.
    """
    caller = tasks.caller()

    writer, writer_name = None, "gemini"
    if DEMO:
        writer, writer_name = reflection.deterministic, "deterministic"

    considered = written = failed = 0
    for uid in workspaces.REGISTRY.all_uids():
        considered += 1
        try:
            made = reflection.run_for(workspaces.for_uid(uid),
                                      writer=writer, writer_name=writer_name)
            written += 1 if made else 0
        except Exception as e:                                    # noqa: BLE001
            # One workspace failing is not the run failing.
            #
            # 🔴 The message used to be dropped for every exception, "because it
            # is one step from a person's words". That caution is right for an
            # arbitrary exception — a KeyError or a driver error can carry the
            # data that caused it. It is wrong for `ReflectionRejected`, whose
            # every message is a fixed string written in reflection.py: a word
            # from the FORBIDDEN list, a field name, a character count, a count
            # of unknown ids. None of them can contain a transcript.
            #
            # 2026-08-25 that distinction cost a deploy cycle. The first real
            # leaf was rejected in production and the log said only
            # `ReflectionRejected`, which is eight different faults with eight
            # different fixes. Protecting a person's words is not the same as
            # refusing to say which rule fired.
            failed += 1
            if isinstance(e, reflection.ReflectionRejected):
                app.logger.warning(
                    "scheduled reflection skipped a workspace: %s: %s",
                    type(e).__name__, e)
            else:
                app.logger.warning("scheduled reflection skipped a workspace: %s",
                                   type(e).__name__)

    return jsonify({"ran_as": caller, "considered": considered,
                    "written": written, "skipped_with_error": failed,
                    "rule": reflection.RULE_ID}), 200


# ----------------------------------------------------------------- records --
def provenance(row: dict) -> str:
    """
    Which layer a row's content came from, in the project's own vocabulary.

      asserted  — the person said it: their words, their number, their time
      inferred  — Mallow's reading of what they said
      observed  — generated by this server: when it was filed, what policy ran

    Every row carries all three. This names the strongest claim in it, so the
    record page can show at a glance whether a number came from a person or
    from a model. It is not a confidence score.
    """
    if row.get("labour_kind") == "unknown":
        return "inferred (unclassified)"
    if row.get("duration_minutes") is not None or row.get("occurred_at"):
        return "asserted"
    return "inferred"


# 🔴 The rows a person is given back, and only those.
#
# This used to hand every row to the exports, including the ones a person had
# cancelled — labelled with their status, which read as honesty and was not.
# Owner's ruling, 2026-08-29: cancelling means the content does not come back
# in the records page, the rollup, the reflection, the CSV or the PDF. Handing
# somebody a file containing the thing they cancelled, with a word beside it,
# is still handing it back.
#
# 🔴 `superseded` is excluded for the same reason and one more: after a
# cancelled correction, the row the correction retired is `superseded` and the
# restored copy of it is `active`. An export that printed both would show the
# same afternoon twice, and any total taken from that file would count it
# twice. One active copy, one line.
#
# This is now the same filter the records page and the rollup already use, so
# every read face agrees. An audit view that shows the full history is a
# separate, explicitly-named thing; it is not the default download.
VISIBLE = ("active", "unclassified")


def rows() -> list[dict]:
    """This person's rows. There is no argument here for a reason."""
    return [{**r, "provenance": provenance(r)}
            for r in workspaces.current().ledger.ordered()
            if r.get("review_status") in VISIBLE]


def display_timezone(ws=None) -> str:
    """
    The zone a person's own records are printed against.

    Three sources, strongest first: the zone their device last reported, the
    zone their reflection schedule runs in, and this module's default. The
    device wins because a printed timestamp is meant to agree with the clock
    in the reader's hand.

    🔴 None of this changes how history is stored. Every `recorded_at` is a
    full ISO instant carrying its own offset, so reading it against a
    different zone is a conversion, not a correction, and no row is rewritten.
    """
    workspace = ws or workspaces.current()
    saved = workspace.preferences.read()
    for key in ("display_timezone", "timezone"):
        value = saved.get(key)
        if isinstance(value, str) and value:
            return value
    return reflection_schedule.DEFAULT_TIMEZONE


@app.get("/records")
def records_page():
    try:
        who = identity.current()
    except identity.Unauthenticated:
        # A presented credential that failed verification is still an auth
        # error, not an ordinary signed-out navigation. Do not hide expired,
        # forged, or wrong-project tokens behind a friendly redirect.
        if request.headers.get("Authorization", "").startswith("Bearer "):
            raise
        # A clicked page is a navigation, not an API call. Keep every private
        # data route closed, but bring a signed-out person to the public gate
        # instead of printing the JSON form of Unauthenticated in the browser.
        # `next` is an enum rather than a URL, so this cannot become an open
        # redirect; the home page maps only "records" back to this route.
        return redirect(url_for("home", lang=lang(), next="records"))
    ws = workspaces.current()
    demo_workspace = DEMO or bool(ws.preferences.read().get(
        "synthetic_demo_workspace"))
    current_rows = rows()
    effective = [r for r in current_rows
                 if r.get("review_status") in ("active", "unclassified")]
    totals = {
        "grass": sum(r.get("policy_result") == "grass" for r in effective),
        "carrot": sum(r.get("policy_result") == "carrot" for r in effective),
        "leaves": len(ws.summaries),
    }
    return render_template("records.html", groups=exporter.by_capture(current_rows),
                           totals=totals,
                           demo=demo_workspace, who=who,
                           storage=storage(), cross_device=cross_device(),
                           display_timezone=display_timezone(ws))


def _attachment(data, mime: str, name: str) -> Response:
    return Response(data, mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/export.csv")
def export_csv():
    return _attachment(exporter.to_csv(rows()), "text/csv; charset=utf-8",
                       f"mallow-records{SUFFIX}.csv")


@app.get("/export.json")
def export_json():
    demo_workspace = DEMO or bool(workspaces.current().preferences.read().get(
        "synthetic_demo_workspace"))
    return jsonify(exporter.to_json(rows(), demo=demo_workspace))


@app.get("/export.pdf")
def export_pdf():
    return _attachment(exporter.to_pdf(rows(), lang=lang(),
                                       timezone_name=display_timezone()), "application/pdf",
                       f"mallow-records{SUFFIX}.pdf")


# ------------------------------------------------------------------- pages --
@app.get("/")
def home():
    """🔓 PUBLIC BOOTSTRAP. The shell renders signed-out; the page asks for an
    identity before it fetches anything that belongs to one."""
    who = identity.optional()
    # 🔴 The settings panel opens before there is an identity, so it needs the
    # product defaults without asking a private endpoint for them. They are
    # rendered from `reflection_schedule` rather than written out again in the
    # template: two copies of "the default cadence" is how a page ends up
    # showing one thing and saving another.
    defaults = {"cadence": reflection_schedule.DEFAULT_CADENCE,
                "time_local": reflection_schedule.DEFAULT_TIME}
    auth_return_to = (f"/records?lang={lang()}"
                      if request.args.get("next") == "records" else "")
    return make_response(render_template("index.html", demo=DEMO, who=who,
                                         storage=storage(),
                                         reflection_defaults=defaults,
                                         cross_device=cross_device(),
                                         auth_return_to=auth_return_to))


@app.get("/art/<path:name>")
def art(name: str):
    return send_from_directory(ART, name, max_age=86400)


@app.get("/static/<path:name>")
def static_file(name: str):
    return send_from_directory(HERE / "static", name)


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(HERE / "static", "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # Served from the root so its scope covers the whole app.
    return send_from_directory(HERE / "static", "sw.js", mimetype="text/javascript")


if __name__ == "__main__":
    mode = "firebase" if identity.configured() else "local identity"
    print(f"Mallow on http://127.0.0.1:{os.getenv('PORT', 8080)}  "
          f"[{mode}] [{storage()}]"
          + ("  [DEMO — deterministic model, separate journal]" if DEMO else ""))
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", 8080)), debug=False)
