# Mallow

**Mallow gives invisible household work a shape — capturing it by voice, structuring it, and reflecting it back later.**

| | |
|---|---|
| **Hackathon** | All Things Agentic Hackathon (Google × Devpost) |
| **Track** | The Taskmaster |
| **Live app** | https://mallow-2pz3hehh7a-an.a.run.app |
| **Demo video** | https://youtu.be/Pru17N9C99k |
| **Architecture** | [`docs/architecture.svg`](docs/architecture.svg) · [in words](docs/architecture.md) |

**Stack actually used:** Gemini 3.7 Flash via the **Gemini API in Vertex AI, through the Google Gen AI SDK** (`google-genai`) · **Cloud Run** · **Cloud Firestore** · **Firebase Authentication** · **Cloud Scheduler** (OIDC) · Cloud Build · Artifact Registry.

### Why this is an agent and not a chat wrapper

Nobody presses a button to make Mallow think.

**Cloud Scheduler wakes Mallow on a schedule.** Mallow then decides, on its own,
which workspaces are due, whether there is anything worth saying, assembles a
fact pack from stored records, asks Gemini for a reflection, **validates that
reflection deterministically**, and writes a leaf. The browser cannot create a
leaf; the endpoint that can is not reachable by a user at all — it accepts an
OIDC token from one named service account for one named audience.

A chat wrapper answers when spoken to. This one writes back when nobody is there.

---

## The problem

The work that holds a household together is mostly work with **no duration**.

Remembering the dentist. Noticing the school bag is too small. Deciding which
of two clinics. Comparing, arranging, holding a deadline in mind for somebody
else. None of it has a start time and an end time, so **no time-tracking system
records it** — and what is never recorded is easy to believe never happened,
including by the person doing it.

Mallow is built for one person in particular: a parent whose hands are usually
full, who has a few minutes at a time, and who is not going to fill in a form.

## What Mallow does

She holds the rabbit and says one sentence. That is the whole interface.

- **It listens.** Traditional Chinese, English, Cantonese-English code-mix.
- **It gives the sentence a shape.** Gemini extracts structured events — what
  the activity was, which domain it belongs to, what kind of labour it is,
  and a duration **only if she said one**.
- **It answers with something small.** 🌿 grass, 🥕 carrot, or an
  acknowledgement that this is work the world already counts.
- **It writes back later, by itself.** On her own schedule — weekly by default —
  Mallow produces a leaf: a short reflection built from what she actually
  recorded, never from what it imagined.

### What is asserted, and what is inferred

Every record carries both, and says which is which:

- **asserted** — she said it. Her words, her number, her time.
- **inferred** — Mallow's reading of what she said.

It is not a confidence score. It is the line between her account and the
model's, kept visible on purpose.

---

## Immediate capture flow

```
Browser (voice or text)
  → Firebase Authentication (Google account, or an anonymous workspace)
  → Cloud Run
  → Gemini extracts structured events
  → deterministic policy maps labour_kind → grass / carrot / no food
  → Cloud Firestore (workspace-scoped record + garden state)
  → receipt in the UI
```

**The model classifies; it does not decide consequences.** The mapping is a
dictionary in `spike/voice/policy.py`:

```python
outcome = {
    "invisible_chore": "grass",      # 🌿
    "mental_load":     "carrot",     # 🥕
    "recognised_work": "none",       # recorded, no food: the world already counts it
}.get(labour_kind, "withheld")       # anything else: heard, but nothing issued
```

Reward is determined by `labour_kind` and by nothing else. `duration_minutes`
does not participate. `activity_domain` and `labour_kind` are two independent
classifications: a bus ride with a child is `transport_errands` **and**
`invisible_chore`.

If the model cannot classify an utterance, `labour_kind` is `unknown` and the
outcome is **withheld**: the record is kept and marked unclassified, and no food
is issued. Withheld is not an error state — Mallow would rather say it heard
something and file it unclassified than hand out food it is not sure about. The
records page shows such a row as `inferred (unclassified)`.

## Autonomous reflection flow

```
Cloud Scheduler tick  (OIDC, one service account, one audience)
  → protected Cloud Run endpoint  /tasks/reflections
  → Mallow checks which workspaces are due and eligible
  → builds a fact pack from stored records
  → Gemini drafts the reflection
  → deterministic validation
  → Cloud Firestore summary (the leaf)
  → the app discovers and displays it
```

**Cloud Scheduler wakes Mallow. It does not write the reflection and it does not
create the leaf.** The deployed job ticks frequently and simply asks "is anyone
due?"; how often a person actually receives a leaf is **her own cadence
setting**, weekly by default.

### When a leaf is produced

Three gates, all in `mobile/reflection.py`:

1. reflections are not switched off;
2. the time **she chose** has arrived;
3. **there is at least one new record in the period.**

The third one is the point: cadence never manufactures content. A quiet week
produces no leaf rather than a leaf about nothing.

---

## Architecture

![Architecture](docs/architecture.svg)

The same thing in words, with the file that enforces each claim:
[`docs/architecture.md`](docs/architecture.md).

| Component | Role |
|---|---|
| Cloud Run | hosts the app and the protected task endpoint |
| Cloud Firestore | the authoritative store; one workspace per uid |
| Firebase Authentication | Google sign-in, and anonymous workspaces |
| Gemini (Gemini API in Vertex AI, via the Google Gen AI SDK) | extraction and reflection drafting |
| Cloud Scheduler | wakes the reflection endpoint with an OIDC token |
| Cloud Build / Artifact Registry | build and image storage for `gcloud run deploy --source .` |

## Technology

```
Python 3.12 · Flask 3.0.3 · gunicorn 23.0.0
google-genai 2.19.0          Gemini, via Vertex AI
google-cloud-firestore 2.19.0
google-auth 2.56.0           verifies Firebase ID tokens and the Scheduler OIDC token
reportlab 4.2.5              the PDF export
```

## Identity, state and isolation

- **One workspace per uid.** A uid becomes a directory or a Firestore path and
  never by string concatenation; there is no route, parameter or query that can
  name somebody else's workspace.
- **Anonymous is a real workspace, not a demo mode.** Its records live in
  Firestore like everyone else's. What is temporary is *access*: the key lives
  in that browser, and it does not follow her to another device.
- **An anonymous workspace exits before it signs in.** It offers one action:
  leave. Google sign-in happens at the front door and nowhere else, so nobody is
  ever standing between two identities while a popup decides which one they are.
- **`is_anonymous` is read from the token on every request**
  (`firebase.sign_in_provider`), never stored, so it cannot go stale.
- **Signing out requires the server to confirm the cookie was cleared.** If it
  was not, the app says so rather than reloading and looking finished.

## Security boundaries

- Every private route resolves identity from a **verified Firebase ID token**;
  the uid used is the one inside a signature the server checked, never a value
  the browser supplied.
- A navigation cookie exists because a plain link carries no `Authorization`
  header. It is minted by the server after token verification, accepted for
  `GET` only, and writes still carry the token.
- Firestore rules **deny all client access** (`deploy/firestore.rules`).
  The browser never talks to Firestore; the server does.
- `/tasks/reflections` accepts **only** an OIDC token from the named service
  account **for the named audience**. An audience-less check would accept a
  token minted by the same service account for a different service, so the app
  refuses to start with one configured and not the other.
- The Firebase web config (`FIREBASE_API_KEY` and friends) reaches the browser
  and is not a server credential. Security comes from token verification and
  deny-all rules, not from hiding it.
- Secrets are provided as **Cloud Run environment variables**. When updating
  them, use `--update-env-vars`; `--set-env-vars` replaces the whole set and
  would drop `MALLOW_SESSION_SECRET`, signing everybody out.

---

## Run it locally

Prerequisites: Python 3.12. A Google Cloud project with the Gemini API in
Vertex AI enabled is needed **only** for the real model; everything else runs
without one.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh doctor        # what is installed, what is missing, how to fix it
./run.sh demo          # http://127.0.0.1:8080
```

`./run.sh demo` sets `MALLOW_FAKE_MODEL=1`, pins one fixed local workspace
(`demo-owner`) and writes to a local file journal under `data/live/`. No cloud
project, no Firebase, nothing to configure.

`MALLOW_FAKE_MODEL=1` swaps in a deterministic stand-in. It is reachable only
by setting that variable on purpose and is **never** a silent fallback: a model
failure sends the person to the text box, not to a guess.

The page follows the browser's `Accept-Language` and falls back to English;
`?lang=en` and `?lang=zh-Hant` force a language, and the choice is remembered.

With the real model:

```bash
cp .env.example .env          # names and placeholders only; never commit real values
export $(grep -v '^#' .env | xargs)
./run.sh                      # the default; same as ./run.sh real
```

`./run.sh real` refuses to start without `GOOGLE_CLOUD_PROJECT` rather than
quietly falling back to the stand-in.

Health check:

```bash
curl -s localhost:8080/health
# demo:  {"model_configured":false,"ok":true,"spike":"voice"}
# real:  {"model_configured":true,"ok":true,"spike":"voice"}
```

🔴 `model_configured` reports whether `GOOGLE_CLOUD_PROJECT` is set. It is a
configuration check, not a liveness check for Gemini, and it does not tell you
which model answered.

### Reproducing the autonomous reflection

The scheduled reflection is the one part of Mallow that no person can trigger —
there is no button for it anywhere in the app, on purpose. So it has a
developer command, and synthetic records to run it against:

```bash
./run.sh seed-demo     # 8 invented records in the 'demo-owner' demo journal
./run.sh leaf          # run the scheduled task once, against that journal
```

A fresh workspace defaults to **weekly, Monday 23:00, Asia/Tokyo**, so the
first `leaf` normally answers:

```json
{"considered":1,"rule":"MALLOW-REFLECTION-002","skipped_with_error":0,"written":0}
```

`written: 0` is the gate working, not a failure — the chosen time has not
arrived. Cadence never manufactures content. To watch a leaf actually be
written, bring the due time backwards in the demo journal and run it again:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("data/live/demo-owner/reflection-preferences-demo.jsonl")
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
rows[-1]["_value"]["next_reflection_at"] = "2000-01-01T23:00:00+09:00"
rows[-1]["_value"]["period_start_at"]    = "2000-01-01T23:00:00+09:00"
p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
PY
./run.sh leaf          # {"considered":1,...,"written":1}
```

What was written, and by whom:

```bash
python3 -c "import json; v = json.loads(open('data/live/demo-owner/summaries-demo.jsonl').read().splitlines()[-1])['_value']; print(v['writer'], v['record_count'], v['reflection'])"
# deterministic 8 This weekly reflection holds small jobs that usually go unnoticed, …
```

`writer: deterministic` is the demo stand-in. With real credentials the same
path runs through Gemini, and the record count, the cited record ids and the
window are still counted in code, from the store — never by the model.

## Tests

```bash
pip install -r requirements.txt pytest playwright pdfminer.six
python3 -m playwright install chromium

./run.sh test          # 🔴 the release gate: every suite runs, or it fails
```

The gate refuses to run a subset. It probes the Python dependencies, Chromium,
and whether `requirements.txt` resolves on a **clean** install, and stops with
instructions if any of them is missing. Two of those probes exist because of
real incidents: the browser tests once sat behind `importorskip` with a
`|| echo` after them, so a machine with no browser engine produced a cheerful
green summary; and a pinned `google-auth` conflicted with `google-genai`, which
every local run survived — because the machine already had a compatible version
— and which stopped the Docker build before the first line of the app.

```bash
./run.sh test-python   # the Python suites only; it names what it did not cover
```

Or the same collection by hand, from the repository root:

```bash
python3 -m pytest spike/voice/tests mobile/tests -q
```

Expected collection:

```
 68  extraction   spike/voice/tests
319  product      mobile/tests/test_product.py
102  browser      mobile/tests/browser   (real Chromium, via Playwright)
---
489
```

The browser suite is not optional and does not skip. `auth.js` is the one part
of the product that only exists inside a browser — module imports, popups,
sessions — and a green Python suite says nothing about it. If Playwright
is missing, the gate stops loudly rather than reporting a skip that reads as a
pass.

Semantics are checked against the **real model**, not the stand-in, because a
deterministic double written to agree with the ruling can only ever agree with
it:

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GEMINI_LOCATION=global
python3 demo/verify_care_taxonomy.py
```

## Deploy

Full instructions, including Firebase setup, the service account and the
Scheduler job, are in [`deploy/DEPLOY.md`](deploy/DEPLOY.md).

```bash
gcloud run deploy mallow --source . --region "$REGION"
```

Two things that a deployment does **not** carry, and that have to be done
separately: the Cloud Scheduler job, and Firebase's list of authorized domains.
A Cloud Run URL that is not in that list produces `auth/unauthorized-domain`,
and sign-in fails.

The Scheduler job, with the OIDC binding that protects the endpoint:

```bash
gcloud scheduler jobs create http mallow-reflections \
  --location "$REGION" \
  --schedule "* * * * *" \
  --time-zone "Asia/Tokyo" \
  --uri "$SERVICE_URL/tasks/reflections" \
  --http-method POST \
  --oidc-service-account-email "$TASKS_SA" \
  --oidc-token-audience "$SERVICE_URL"
```

## Known limitations

- **Anonymous workspaces do not follow you to another device.** The records are
  in Firestore, but the key to them lives in that browser. Clearing site data,
  or closing a private tab, can leave a workspace you cannot open again.
- **An anonymous workspace is not upgraded in place.** This release does not
  carry an anonymous workspace over to a Google account.
- **A bare, unqualified noun can be read generously.** "I cleaned a bottle" is
  read as care work; "I cleaned the water bottle" is not. The prompt names the
  relationship rather than the object, and the ambiguous-noun case is a known
  edge.
- **Reflections are only as good as what was said.** Mallow never invents a
  record, and a period with nothing in it produces no leaf.

## Demo data

`demo/seed.py` is a **fully synthetic** seeding script, committed on purpose: a
reviewer who cannot reproduce the autonomous leaf cannot check the one thing
this submission is about. Every line in `spike/voice/fixtures/utterances.json`
was written for the evaluation set — none of it is transcribed from, adapted
from, or shaped by any real person's speech or records.

No real user's records, screenshots or exports are in this repository.

## Repository layout

```
mobile/              the product: routes, ledger, policy, reflection, exports
  static/auth.js       identity in the browser: sign-in, sessions
  static/sw.js         service worker; mutable code is network-first
  templates/           the meadow and the records page
  tests/               product tests, and browser tests under tests/browser
spike/voice/         the capture slice: prompt, contract, policy, its own tests
demo/                synthetic seeding and the real-model semantic checks
deploy/              deployment guide and Firestore rules
assets/art/          the runtime artwork (WebP)
docs/                architecture diagram, and the same thing in words
```

---

Built for the All Things Agentic Hackathon. The artwork is original and is not
licensed for reuse; no licence is granted for this repository.
