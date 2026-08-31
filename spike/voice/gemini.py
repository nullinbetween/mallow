"""
The one call to the model.

Audio in, candidate events out. Nothing is written anywhere by this module, and
the audio bytes are never returned, stored, or logged - they exist inside one
function call and are gone when it returns.

Browser-native container formats are sent as recorded. Chrome produces
audio/webm;codecs=opus and Safari audio/mp4; both are accepted by the model, so
there is no encoder and no ffmpeg in this path. The client sends its actual
blob.type and that is what is forwarded.
"""
from __future__ import annotations

import json
import os
from typing import Any

from contract import CANDIDATE_SCHEMA

# ✅ Checked against Google's own documentation on 2026-08-23: `gemini-3.7-flash`
# is a current model ID and is the one used in the official audio-understanding
# examples. It stays behind `MALLOW_MODEL` so a deployment can move without a
# code change — a model ID is configuration, not a decision this file gets to
# freeze.
MODEL = os.getenv("MALLOW_MODEL", "gemini-3.7-flash")
GEMINI_LOCATION = os.getenv("GEMINI_LOCATION", "global")
# 🔴 Still a Stage 1 mechanism: the model chooses `labour_kind` and a
# deterministic policy maps it. Stage 2 (voice-extract-v7-semantic-gate) removes
# that mechanism entirely, so this string is temporary on purpose. Do not keep
# both once Stage 2 is verified.
#
# Taxonomy history, kept here rather than inside INSTRUCTION
# (PRODUCT_DECISIONS §44 · MALLOW-HYGIENE-001, Owner 2026-08-30). The model
# needs the current rule; it does not need the project's confession, and a
# retired rule sitting beside the live one is a second, competing statement:
#
#   2026-08-27  Q-36. `the school run` was listed under recognised_work. A
#               person doing it twice a day was told the world already counts
#               it. Moved to invisible_chore.
#   2026-08-30  Q-45. Washing a child's toys fell into the general `cleaning`
#               row and earned nothing, while buying toys earned grass. Third
#               instance of one shape, after Q-12 and Q-36: child-specific work
#               falling back into the general household bucket. Fixed with a
#               general care-infrastructure rule, not a `toy` keyword.
#   2026-08-30  Prompt hygiene. Version history, motive sentences, document
#               navigation and one duplicated rule removed from INSTRUCTION:
#               8,410 → 7,400 characters, sent on every call.
PROMPT_VERSION = "voice-extract-v6-care-context-r5"

# What a browser actually produces, and it is supported.
#
# ✅ Checked by the 戰略官 against the Gemini 3.7 Flash page on Google Cloud,
# 2026-08-23: `audio/webm` and `audio/mp4` are both listed — which is exactly
# Chrome and Safari. An earlier note in this file said the opposite, having read
# only the shorter format list on ai.google.dev and treated "not on that page"
# as "not supported". That is the same mistake this project already made once,
# and it very nearly bought a WAV encoder nobody needed. The rule stands: a
# format list that does not mention something is not a format list that refuses
# it, and the way to find out is to send one.
#
# 🔴 The real-phone test is still required — but the risk it is testing is Blob
# MIME strings, SDK transport and browser behaviour, NOT whether the format is
# supported. If something fails there, read the error before believing it is
# the container.
ACCEPTED_MIME = ("audio/webm", "audio/mp4", "audio/wav", "audio/ogg",
                 "audio/mpeg", "audio/aac", "audio/flac")

INSTRUCTION = """You are listening to one short spoken note from a parent or
guardian carrying household work or the everyday care of a dependent child.

Transcribe what they said, verbatim, in the language they said it in.

Then split it into one event per distinct piece of work. A single sentence often
contains two or three.

For each event report:
  activity_text    a short neutral label in English
  source_text    the speaker's own words that this rests on - quote, never paraphrase
  activity_domain  care_child | household_upkeep | food_preparation |
                   clothing_laundry | school_community | health_admin |
                   household_admin | shopping_restocking |
                   transport_errands | social_coordination | other
  labour_kind      invisible_chore | mental_load | recognised_work | unknown
  duration_minutes ONLY if they said it. Otherwise null. Never estimate.
                   A duration given in hours is reported in minutes: "two hours"
                   is 120. Converting the unit someone used is not estimating;
                   supplying one they never gave is.
  occurred_at      ONLY if they said or clearly implied it. Otherwise null.
                   Exact clock times use 24-hour HH:MM. In an activity context,
                   compact clock forms such as "0740 出發" and "0900 drop off"
                   mean 07:40 and 09:00. A number used as an identifier, such
                   as "reference number 0740", is not a clock time. Never add a
                   date, timezone, duration or assumed "now" to this field.

labour_kind:
  invisible_chore  took real time, but nobody counts it as work - labelling,
                   restocking before it runs out, putting things back,
                   preparing what someone else will need.

                   ALSO: unpaid, hands-on care of a dependent child. Settling a
                   child to sleep, sitting with them until they do, feeding,
                   dressing, bathing, supervising, accompanying them, and
                   responding to their immediate daily needs. This is care that
                   took real time and that the world does not count as work, so
                   it belongs here and not under recognised_work. No minimum
                   duration is required. No minimum share of caregiving is
                   required.

                   ALSO: accompanying the child through
                   something - staying at their school session, sitting with
                   them through a meal, taking them to an appointment and
                   waiting. Read the whole utterance before deciding: "picked
                   her up, then lunch for an hour" is an hour of accompanying a
                   child, not an hour of the speaker eating. The surrounding
                   sentence is what says whose meal, whose appointment, whose
                   afternoon it was.

                   Child transport is invisible_chore when the utterance
                   describes a completed act of taking, collecting or
                   accompanying a dependent child to or from school, daycare,
                   nursery, a class, an activity or an appointment. This
                   includes established phrases such as "the school run",
                   "school pickup", and completed school/daycare/nursery
                   drop-off even when the word child is omitted.

                   The utterance itself must supply the child-care purpose;
                   the product's audience is not evidence. A bare personal
                   journey, commute or errand with no household or child-care
                   purpose returns no event. Do not invent a companion,
                   destination or purpose.

                   Duration, frequency and the speaker's share of caregiving
                   do not change this classification.

                   Maintaining or sanitising belongings or care equipment
                   specifically because a dependent child uses or needs them
                   is care infrastructure and is invisible_chore. This
                   includes toys, bottles, feeding items, school/daycare items
                   and similar child-specific belongings. General household
                   cooking and cleaning remain recognised_work.

                   It must describe care that happened, not mere proximity.
                   Arranging or remembering care is mental_load, not
                   invisible_chore.
  mental_load      deciding, holding a deadline in mind, comparing options,
                   chasing a reply, remembering what the child or household
                   needs. Arranging, booking, tracking or planning child care
                   stays here: making the appointment is mental load, and
                   sitting with the child is a chore.
  recognised_work  ordinary household work conventionally recognised as a
                   chore, including general cooking and general cleaning.
                   Cooking remains here even when it serves someone in the
                   speaker's care. General cleaning remains here unless the
                   utterance specifically describes maintaining a dependent
                   child's belongings or care equipment, which is
                   invisible_chore. Child transport described under
                   invisible_chore is not recognised_work.

                   It must still be household or child-care work. Personal
                   travel, exercise, hobbies, rest and paid employment return
                   an empty events array.
  unknown          you can tell that this is household operation or
                   dependent-child care, but you cannot reliably determine its
                   labour kind. Use unknown only inside this product scope.

                   Do not stretch one of the three labour kinds to cover an
                   unclear in-scope activity. Mallow may record an in-scope
                   unknown without awarding food. Work wholly outside this
                   product scope returns an empty events array instead.

activity_domain:
  Choose the narrowest household or dependent-child-care domain that describes
  the activity. health_admin, transport_errands and social_coordination are in
  scope only when they serve this household or the child's care. Use other only
  for work clearly inside this product scope that fits no narrower domain.
  There is no adult or elder-care domain in this release.

Rules:
  Report only what was said. Do not infer mood, tone, or feeling.
  Preserve code-mixed speech exactly in transcript and source_text. Do not
  translate, regularise, or replace the speaker's words. activity_text alone is
  a short canonical English label for computation.
  Work wholly outside household operation and dependent-child care returns an
  empty events array; do not force it into unknown.
  If a piece is unclear, mark it unknown rather than choosing the likeliest kind.
  If the audio contains no work at all, return an empty events array.

  Classification depends on the activity and whom it served, not on whether a
  duration was stated. Mental load may have a duration; missing duration does
  not turn a chore into mental load.
"""


TEXT_INSTRUCTION = INSTRUCTION.replace(
    "You are listening to one short spoken note from",
    "You are reading one short typed note from").replace(
    "Transcribe what they said, verbatim, in the language they said it in.",
    "Repeat their text back verbatim as the transcript.")


class AudioUnreadable(RuntimeError):
    """The model could not make anything of the audio."""


class ModelUnavailable(RuntimeError):
    """No usable model path. Never silently substituted with a guess."""


class ModelMisconfigured(ModelUnavailable):
    """
    Deterministic: no project, unsupported container. Retrying changes nothing,
    so the caller must go straight to the text box rather than waiting twice.
    """


def _client():
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise ModelMisconfigured("GOOGLE_CLOUD_PROJECT is unset; no offline substitute exists")
    from google import genai
    return genai.Client(vertexai=True, project=project, location=GEMINI_LOCATION), project


def understand_text(note: str) -> dict[str, Any]:
    """
    The bounded text fallback. Same contract, same validation, same policy.
    This is capture, not conversation: one note in, candidate events out. There
    is no history, no reply, and nothing here that answers a question.
    """
    client, _ = _client()
    from google.genai import types
    resp = client.models.generate_content(
        model=MODEL,
        contents=f"{TEXT_INSTRUCTION}\n\nNOTE:\n{note}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CANDIDATE_SCHEMA,
            temperature=0,
        ),
    )
    if not getattr(resp, "text", None):
        raise AudioUnreadable("model returned nothing for this note")
    return json.loads(resp.text)


def understand(audio: bytes, mime_type: str) -> dict[str, Any]:
    """
    Returns the raw model response as a dict. Validation happens elsewhere, on
    purpose: this function must not be able to make bad output look good.
    """
    if mime_type.split(";")[0].strip() not in ACCEPTED_MIME:
        raise ModelMisconfigured(f"unsupported container {mime_type!r}")

    client, _ = _client()
    from google.genai import types
    resp = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=audio, mime_type=mime_type),
            INSTRUCTION,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CANDIDATE_SCHEMA,
            temperature=0,
        ),
    )
    if not getattr(resp, "text", None):
        raise AudioUnreadable("model returned nothing for this audio")
    return json.loads(resp.text)
