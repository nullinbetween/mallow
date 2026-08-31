"""
What the model is allowed to hand back, and what happens when it does not.

Gemini produces candidates. It has no write authority. Everything it returns is
untrusted input until this module has checked it.

`source_text` is the canonical field containing the user's original words,
retained verbatim rather than paraphrased. It is the one part of a record that
belongs to the person rather than to the system.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Optional

LABOUR_KINDS = ("invisible_chore", "mental_load", "recognised_work", "unknown")
ACTIVITY_DOMAINS = (
    "care_child", "household_upkeep", "food_preparation", "clothing_laundry",
    "school_community", "health_admin", "household_admin",
    "shopping_restocking", "transport_errands", "social_coordination", "other",
)

# Where a record stands. Not a quality score - a position in its own history.
REVIEW_STATUS = ("active", "unclassified", "cancelled", "superseded")

# Handed to the model as a response schema. Kept deliberately small: every field
# is either something the speaker said, or an explicit admission of not knowing.
#
# 🔴 This is a GenAI SDK `Schema`, not plain JSON Schema, and the difference is
# not cosmetic. "may be absent" is written `"nullable": True` beside a single
# `"type"`, never as the JSON-Schema union `"type": ["integer", "null"]`. The
# SDK validates this dict with pydantic before a single byte goes out, so a
# union list raises `ValidationError` locally and every capture returns 503 —
# which is what the deployed app did on 2026-08-23, at 17ms per attempt because
# no request was ever made. Nothing caught it: every test in this repository
# runs against the deterministic model, so this dict had never been handed to
# the real SDK. `test_the_response_schemas_are_valid_genai_schemas` now does
# exactly that, with no credentials and no network.
CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["events"],
    "properties": {
        "transcript": {
            "type": "string",
            "description": "What was said, in the speaker's own language. Verbatim.",
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["activity_text", "source_text", "activity_domain", "labour_kind",
                             "duration_minutes", "occurred_at"],
                "properties": {
                    "activity_text": {
                        "type": "string",
                        "description": "A short normalised label in English.",
                    },
                    "source_text": {
                        "type": "string",
                        "description": "The span of the speaker's own words this rests on. "
                                       "Never paraphrase; quote.",
                    },
                    "activity_domain": {
                        "type": "string", "enum": list(ACTIVITY_DOMAINS),
                        "description": "The household or dependent-child-care domain. "
                                       "Use other only when the activity is clearly in "
                                       "scope but fits no narrower domain.",
                    },
                    "labour_kind": {
                        "type": "string", "enum": list(LABOUR_KINDS),
                        "description": "Decide from what the work IS, not from whether a "
                                       "duration was mentioned. A chore with no stated "
                                       "duration is still a chore; deciding something that "
                                       "took twenty minutes is still mental load.",
                    },
                    "duration_minutes": {
                        "type": "integer", "nullable": True,
                        "description": "Only if the speaker said it. Otherwise null. "
                                       "Never estimate.",
                    },
                    "occurred_at": {
                        "type": "string", "nullable": True,
                        "description": "When the speaker gives an exact clock time, "
                                       "return canonical 24-hour HH:MM. In an activity "
                                       "context, compact clock forms such as 0740 and "
                                       "0900 mean 07:40 and 09:00. Otherwise preserve "
                                       "only an explicit temporal description, or null. "
                                       "Never assume 'now' and never turn an identifier "
                                       "or reference number into a time.",
                    },
                },
            },
        },
    },
}


class CandidateRejected(ValueError):
    """The model's output failed the contract. Not repaired, not guessed."""


_CLOCK_COLON = re.compile(r"^(?:([01]?\d|2[0-3])):([0-5]\d)$")
_CLOCK_COMPACT = re.compile(r"^(?:([01]\d|2[0-3]))([0-5]\d)$")


def canonical_clock(value: Any) -> Optional[str]:
    """Return ``HH:MM`` only when *value itself* is an exact clock time.

    This deliberately does not inspect a transcript.  Promoting an arbitrary
    four-digit token from somebody's sentence would turn reference numbers,
    room numbers and school codes into invented times.  The model must first
    put a temporal value in ``occurred_at``; this function only makes that
    already-extracted value consistent.

    The compact form is limited to four digits.  ``0740`` and ``0900`` are
    accepted; a bare ``740`` remains a free-form temporal description rather
    than being silently reinterpreted.
    """
    if not isinstance(value, str):
        return None
    clean = value.strip()
    match = _CLOCK_COLON.fullmatch(clean) or _CLOCK_COMPACT.fullmatch(clean)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def normalise_occurred_at(value: Any) -> Optional[str]:
    """Canonicalise an extracted clock; preserve other explicit descriptions.

    Existing records are append-only, so this is a write-side rule for new
    candidates.  Read surfaces also call :func:`canonical_clock` so legacy
    ``0900`` rows display as ``09:00`` without rewriting history.
    """
    if value is None:
        return None
    clean = value.strip()
    return canonical_clock(clean) or clean


@dataclass(frozen=True)
class Event:
    activity_text: str
    source_text: str
    activity_domain: str
    labour_kind: str
    duration_minutes: Optional[int]
    occurred_at: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Candidates:
    transcript: str
    events: tuple[Event, ...]
    rejected: tuple[dict[str, Any], ...]   # fragments that failed, kept visible


def _one(raw: Any) -> Event:
    if not isinstance(raw, dict):
        raise CandidateRejected(f"event must be an object, got {type(raw).__name__}")

    for f in ("activity_text", "source_text", "activity_domain", "labour_kind"):
        v = raw.get(f)
        if not isinstance(v, str) or not v.strip():
            raise CandidateRejected(f"{f} must be a non-empty string")

    kind = raw["labour_kind"]
    if kind not in LABOUR_KINDS:
        raise CandidateRejected(f"labour_kind {kind!r} is not one of {LABOUR_KINDS}")
    domain = raw["activity_domain"]
    if domain not in ACTIVITY_DOMAINS:
        raise CandidateRejected(
            f"activity_domain {domain!r} is not one of {ACTIVITY_DOMAINS}")

    d = raw.get("duration_minutes")
    if d is not None and (not isinstance(d, int) or isinstance(d, bool) or d <= 0):
        raise CandidateRejected(f"duration_minutes must be a positive integer or null, got {d!r}")

    t = raw.get("occurred_at")
    if t is not None and (not isinstance(t, str) or not t.strip()):
        raise CandidateRejected(f"occurred_at must be a non-empty string or null, got {t!r}")

    return Event(raw["activity_text"].strip(), raw["source_text"].strip(),
                 domain, kind, d, normalise_occurred_at(t))


def _span_of(ev: Event, transcript: str) -> None:
    """
    🔴 `source_text` must be a span the person actually said.

    The two fields divide the person's words between them, and the division is
    the traceability contract:

        transcript   everything they said, verbatim, never rewritten
        source_text  the stretch of that sentence this one event rests on

    One sentence often becomes three records, and each has to point at its own
    stretch — otherwise all three carry the whole sentence and nobody can tell
    which record came from which part. So `source_text` is a substring, and
    checking that it really is one is what stops a model from quietly
    paraphrasing, translating, tidying, or inventing the words it attributes to
    a person.

    Nothing here was enforced until 2026-08-23. On the deployed app, a
    recording of silence came back as a fluent French sentence about sewing a
    button onto Thomas's coat, and it was filed as though the person had said
    it. This check would not have caught that one — the model invented the
    transcript too, and the span agreed with the invention — but it closes the
    other half of the same door: from here on, whatever ends up in a record as
    "the person's own words" is provably a piece of what Mallow transcribed.

    An event whose span fails is rejected, not repaired. `validate` keeps it in
    `rejected` so the person sees that part of what they said could not be
    filed, rather than having it silently disappear.
    """
    if ev.source_text not in transcript:
        raise CandidateRejected(
            f"source_text {ev.source_text!r} is not a span of the transcript. "
            "It must be quoted from it, never paraphrased or translated.")


def validate(raw: Any) -> Candidates:
    """
    One bad fragment does not discard the rest. Each event is checked on its own
    and a failure is kept, visibly, rather than silently dropped - the person is
    entitled to see that Mallow could not use part of what they said.
    """
    if not isinstance(raw, dict):
        raise CandidateRejected("response must be a JSON object")

    transcript = raw.get("transcript")
    if not isinstance(transcript, str):
        raise CandidateRejected("transcript must be a string")

    events_raw = raw.get("events")
    if not isinstance(events_raw, list):
        raise CandidateRejected("events must be an array")

    clean = transcript.strip()

    good, bad = [], []
    for item in events_raw:
        try:
            ev = _one(item)
            _span_of(ev, clean)
            good.append(ev)
        except CandidateRejected as e:
            bad.append({"fragment": item, "reason": str(e)})

    return Candidates(clean, tuple(good), tuple(bad))


# --- the record shape -------------------------------------------------------
# Every field the consensus baseline requires. Assembled server-side; the model
# never sees this and never writes it.
RECORD_FIELDS = (
    "recorded_at",        # server-generated, always present
    "occurred_at",        # optional - only if the speaker said it
    "duration_minutes",   # optional - only if the speaker said it
    "transcript",
    "activity_text",
    "source_text",
    "activity_domain",
    "labour_kind",
    "model_version",
    "prompt_version",
    "policy_result",
    "policy_version",
    "review_status",
    "supersedes",
)


def build_record(*, event: Event, transcript: str, decision, recorded_at: str,
                 model_version: str, prompt_version: str,
                 review_status: str = "active",
                 supersedes: Optional[str] = None) -> dict[str, Any]:
    """One event becomes one record. Nothing is inferred here that was not decided upstream."""
    if review_status not in REVIEW_STATUS:
        raise CandidateRejected(f"review_status {review_status!r} is not one of {REVIEW_STATUS}")
    rec = {
        "recorded_at": recorded_at,
        "occurred_at": event.occurred_at,
        "duration_minutes": event.duration_minutes,
        "transcript": transcript,
        "activity_text": event.activity_text,
        "source_text": event.source_text,
        "activity_domain": event.activity_domain,
        "labour_kind": event.labour_kind,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "policy_result": decision.outcome,
        "policy_version": decision.policy_version,
        "review_status": "unclassified" if event.labour_kind == "unknown" else review_status,
        "supersedes": supersedes,
    }
    missing = [f for f in RECORD_FIELDS if f not in rec]
    if missing:
        raise CandidateRejected(f"record is missing {missing}")
    return rec
