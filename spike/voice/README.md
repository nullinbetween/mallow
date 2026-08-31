# Voice spike

**Origin: `LIMITED SPIKE`, authorised 2026-08-22. Current status: mounted by the
running product.** The extraction boundary remains deliberately small even
though its validated records now continue into the product ledger.

Hold the rabbit, say what happened, release.

```
hold  →  MediaRecorder (browser-native container)
      →  POST /voice
      →  configured Gemini model · structured output · candidate events
      →  server-side validation      (the model has no write authority)
      →  deterministic policy        grass / carrot / none / withheld
      →  receipt, then it fades
```

## Run it

```bash
python3 -m gunicorn --bind 127.0.0.1:8090 app:app
```

Then open `http://127.0.0.1:8090` and hold the rabbit.

Without `GOOGLE_CLOUD_PROJECT` the endpoint returns **503 extraction unavailable**.
That is deliberate: there is no offline substitute, and no rule-based guess is
ever passed off as a model result.

With credentials:

```bash
export GOOGLE_CLOUD_PROJECT=your-project
export GEMINI_LOCATION=global          # the model is served from global / us / eu
```

## What is deliberately absent

| | |
|---|---|
| WAV encoder, ffmpeg | Chrome's `webm/opus` and Safari's `mp4` are accepted as recorded. A test asserts neither is imported. |
| Function calling | Structured output only. Function calling would not make it more accurate and cannot bypass server-side validation. |
| Firestore, ledger, scheduled reflection | Out of spike scope. Results live in memory for the length of the process. |
| Audio persistence | The bytes exist inside one function call. Never written to Firestore, Cloud Storage, or the application log. |
| Emotion, tone, mood | Mallow records only what was said. |

## Failure behaviour

| Situation | What happens |
|---|---|
| Audio unreadable | no record, no food, *"Say it again?"* |
| Transcript exists, no labour content | no event, no food |
| Labour content, kind unknown | transcript shown, result **withheld**, no food |
| Duration or time not spoken | stored `null`. Never asked for. Classification can still succeed |
| Several events, one bad fragment | valid events proceed; the bad fragment is **shown**, not dropped |
| Same `capture_id` again | first receipt returned, nothing issued twice |
| Cancel | appends a cancellation. 🔴 **No food is deducted.** The rabbit keeps what it was given |

## Fixtures

`fixtures/utterances.json` — clean-room. Every line was written for this evaluation
set; none is transcribed from or shaped by any real person's speech or records.
17 cases: single event, multiple events, missing duration, code-mix,
unclassifiable, and
seven schema-failure fixtures.

## Acceptance — consensus baseline, 2026-08-22

| | | |
|---|---|---|
| | real browser audio reaches the model | ⬜ **unverified** — needs a browser and credentials |
| | Mandarin one-event extraction | ✅ |
| | Mandarin multi-event extraction | ✅ |
| | missing time remains null | ✅ |
| | unknown content receives no food | ✅ |
| | replay creates no duplicate reward | ✅ |
| | transcript and classification visibly separated | ✅ |
| | text fallback works | ✅ |
| | raw audio is not persisted | ✅ |

**62 extraction tests are defined.** Real browser and real-model verification is
reported separately rather than simulated; a deterministic double cannot prove
that Gemini heard a recording or classified a phrase correctly.

## Fallback contract

| Situation | What happens |
|---|---|
| Microphone unavailable | bounded text box — *"Tell Mallow what happened…"* |
| Network or model failure | retried once, then the text box |
| Transcript wrong | say again, or type it |
| Classification unknown | kept as `unclassified`. No food. No follow-up question |
| Demo microphone fails | prepared clean synthetic audio through the same pipeline |
| Total model failure | a structured dataset may demonstrate policy and UI only. 🔴 **Never presented as proof that Gemini heard anything** |

The text box is **capture, not conversation**: one note in, one receipt out. No reply,
no advice, no history. A test asserts the page contains no chat, history, advice or
assistant element.

## Record shape

Fourteen fields, all present on every record — `recorded_at` (server-generated),
`occurred_at`, `duration_minutes`, `transcript`, `activity_text`, `source_text`,
`activity_domain`, `labour_kind`, `model_version`, `prompt_version`,
`policy_result`, `policy_version`, `review_status`, `supersedes`.

`source_text` holds **the user's original words retained as source text** — quoted,
never paraphrased, never replaced by a summary. It is the one part of a record that
belongs to the person rather than to the system.

`activity_domain` is a closed, canonical English computation label for household
and dependent-child care. It does not translate or replace `transcript` or
`source_text`, including when one utterance mixes languages. Work wholly outside
this release's family-and-child scope produces no event instead of being forced
into `other`.

**Classification is semantic.** A chore with no stated duration is still a chore;
deciding something for twenty minutes is still mental load. Two tests hold that line,
and the prompt says so in as many words.

## Correction

Cancelling appends. The prior records are marked `cancelled` and drop out of future
rollups; a `superseded` record names what it replaced. Nothing is removed, no negative
amount is created, and the rabbit keeps what it was given.

Storage claim, exactly, and nothing beyond it:
> **Append-only by application policy, with traceable corrections.**

That describes what this application does with its own rows. It says nothing about the
storage layer underneath, and a test asserts that no stronger word is used anywhere.

## UI

Functional state shell only: `idle · recording · processing · receipt · nourished ·
fallback_text · failure`. **Final visual styling is deliberately absent** — it begins
after the approved sample arrives.
