"""The scheduled reflection: one bounded workflow, no manual trigger.

A global Cloud Scheduler checks workspaces. Each person's saved cadence decides
when their workspace is due; deterministic code then decides whether there is
new material, builds a fact pack without raw sentences, validates the model's
prose, and either places one folded leaf or remains silent.

The model writes sentences. It does not choose timing, records, rewards, state,
or recipients. A missing duration has no meaning and never affects eligibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import reflection_schedule as schedule

JST = timezone(timedelta(hours=9))

RULE_ID = "MALLOW-REFLECTION-002"

# One runaway-output budget per language. These are not writing targets: the
# selected cadence's sentence range shapes the note (Daily 1–2 through Monthly
# 4–6). The wider hard caps prevent a valid reflection from disappearing merely
# because an English translation is longer than its Chinese counterpart.
MAX_REFLECTION_CHARS = {"reflection": 2000, "reflection_zh": 1000}


def now_jst() -> datetime:
    return datetime.now(JST)


def _parse(stamp: Any) -> Optional[datetime]:
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        dt = datetime.fromisoformat(stamp.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)


# ------------------------------------------------------- eligibility --------
class Verdict:
    """Why a period did or did not get a leaf. Returned, logged, never shown."""

    def __init__(self, ok: bool, reason: str, pack: Optional[dict] = None,
                 preferences_state: Optional[dict] = None,
                 due: Optional[datetime] = None):
        self.ok, self.reason, self.pack = ok, reason, pack
        self.preferences_state, self.due = preferences_state, due

    def __repr__(self) -> str:                                    # pragma: no cover
        return f"<Verdict {'ok' if self.ok else 'no'}: {self.reason}>"


def window_records(records: list[dict], now: datetime,
                   start: Optional[datetime] = None) -> list[dict]:
    """
    The active records inside the rolling window, oldest first.

    `recorded_at` is used rather than `occurred_at`: the first is written by
    this server and always exists, the second is only there when the person
    happened to say when. Choosing the optional one would quietly drop every
    note that did not carry a time — which is most of mental load.
    """
    start = start or (now - timedelta(days=7))
    inside = []
    for r in records:
        if r.get("review_status") not in ("active", "unclassified"):
            continue
        at = _parse(r.get("recorded_at"))
        if at is not None and start < at <= now:
            inside.append((at, r))
    return [r for _, r in sorted(inside, key=lambda p: p[0])]


def eligible(ws, now: Optional[datetime] = None) -> Verdict:
    """Timing and content gates. Cadence never manufactures content."""
    now = now or now_jst()
    pref = schedule.read(ws, now=now, persist_default=True)
    if pref.get("cadence") == "off":
        return Verdict(False, "reflections are off", preferences_state=pref)
    due = _parse(pref.get("next_reflection_at"))
    if due is None or now < due:
        return Verdict(False, "the chosen reflection time has not arrived",
                       preferences_state=pref, due=due)

    start = _parse(pref.get("period_start_at")) or due - timedelta(days=7)
    rows = window_records(ws.ledger.ordered(), now, start)
    if not rows:
        return Verdict(False, "no new records in this period",
                       preferences_state=pref, due=due)

    return Verdict(True, "eligible", fact_pack(rows, now, pref, start, due),
                   pref, due)


# --------------------------------------------------------- the fact pack ----
def fact_pack(rows: list[dict], now: datetime, pref: Optional[dict] = None,
              start: Optional[datetime] = None,
              due: Optional[datetime] = None) -> dict:
    """
    What the model is allowed to know. Counted here, in code, from the store.

    Deliberately absent: durations as a total, per-day productivity measures,
    comparison with a period the pack does not contain, and the person's raw
    sentences. The model gets canonical labels and the shape of the selected
    period, and writes about that.
    Handing it the raw text back would invite it to quote something the person
    said in a bad moment into a document they may show someone.
    """
    pref = pref or schedule.make("weekly", "23:00", "Asia/Tokyo", now=now)
    start = start or (now - timedelta(days=7))
    kinds: dict[str, int] = {}
    domains: dict[str, int] = {}
    for r in rows:
        kinds[r.get("policy_result", "unknown")] = \
            kinds.get(r.get("policy_result", "unknown"), 0) + 1
        domain = (r.get("activity_domain") or "").strip()
        if domain:
            domains[domain] = domains.get(domain, 0) + 1

    activities: list[str] = []
    for r in rows:
        label = (r.get("activity_text") or "").strip()
        if label and label not in activities:
            activities.append(label)

    days = sorted({(_parse(r["recorded_at"]) or now).date().isoformat() for r in rows})
    with_duration = sum(1 for r in rows if r.get("duration_minutes") is not None)

    return {
        "rule_id": RULE_ID,
        "cadence": pref["cadence"],
        "sentence_target": list(schedule.SENTENCE_TARGETS[pref["cadence"]]),
        "window_start": start.isoformat(timespec="seconds"),
        "window_end": now.isoformat(timespec="seconds"),
        "scheduled_for": due.isoformat(timespec="seconds") if due else None,
        "record_ids": [r.get("record_id") for r in rows if r.get("record_id")],
        "record_count": len(rows),
        "distinct_days": days,
        "counts_by_food": kinds,
        "counts_by_domain": domains,
        "activities": activities[:24],
        # Reported so the model can be told not to treat it as anything. A week
        # of pure mental load has zero here and is not a lesser week.
        "records_with_a_stated_duration": with_duration,
    }


# ------------------------------------------------------------- the model ----
# Both languages in one call. The task runs with nobody reading, so there is no
# reader whose language could be consulted — and a leaf that comes out in the
# wrong language for the person holding the phone is a bug, not a translation
# problem. One call, one validation pass, the same guard applied to both.
REFLECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reflection", "reflection_zh", "cited_record_ids"],
    "properties": {
        "reflection": {
            "type": "string",
            "description": "A plain English reflection. Sentence count is "
                           "supplied in the fact pack.",
        },
        "reflection_zh": {
            "type": "string",
            "description": "The same note in Traditional Chinese (Taiwan). Same "
                           "content, not a looser or warmer version of it.",
        },
        "cited_record_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The record ids this rests on. Every one must come "
                           "from the pack.",
        },
    },
}

INSTRUCTION = """Write one private reflection from the supplied fact pack.
The pack contains counts and short canonical English activity labels, never the
person's original sentences. Address the person as "you".

The pack includes `cadence` and an inclusive `sentence_target` [minimum,
maximum]. Respect that sentence target.

Write the same note twice: once in English, and once in Traditional Chinese as
used in Taiwan. They must say the same thing - not a warmer or longer version
in either language.

Hard output backstops are 2000 characters for English and 1000 for Chinese.
They prevent runaway output; sentence count, not the cap, controls the style.

Then list the record ids the note rests on. Use only ids from the pack.

Rules, all of them absolute:
  Describe. Do not evaluate, diagnose, warn, or advise.
  Do not say the person is tired, overloaded, burnt out, at risk, or unwell.
  Do not suggest seeing a doctor, resting, delegating, or talking to anyone.
  Do not mention totals, hours, scores, streaks, levels, or percentages.
  Do not compare with a period that is not in the pack.
  Do not treat missing durations as meaningful. Much of this work has no
    duration at all; that is normal and is not a finding.
  Do not speculate about the person's household, relationships, or feelings.

Tone: quiet, ordinary, and finished. This is a note somebody reads once."""


class ReflectionRejected(ValueError):
    """The model's note failed the contract. Discarded, never repaired."""


# Vocabulary that would turn a description into a claim. Checked on the way in
# and on the way out — a summary carrying any of it is thrown away rather than
# edited, because editing it would mean deciding what it meant to say.
FORBIDDEN = (
    "burnout", "burnt out", "burned out", "exhausted", "exhaustion", "overload",
    "overloaded", "overwork", "at risk", "unhealthy", "diagnos", "symptom",
    "disorder", "depress", "anxiety", "therapy", "therapist", "doctor",
    "clinician", "medical", "evidence", "abnormal", "warning", "alert",
    "you should", "you need to", "consider seeing", "seek help", "support brief",
    "過勞", "疲勞", "異常", "警告", "診斷", "醫生", "就醫", "求助文件", "證據",
)

NUMBER_WORDS = re.compile(r"\b\d+\s*(hours?|hrs?|minutes?|mins?|%|percent)\b", re.I)


def check_language(text: str) -> None:
    low = text.lower()
    for word in FORBIDDEN:
        if word.lower() in low:
            raise ReflectionRejected(f"reflection used forbidden wording: {word!r}")
    if NUMBER_WORDS.search(text):
        raise ReflectionRejected("reflection quantified the period")


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[.!?。！？]+", text) if part.strip()])


def validate(raw: Any, pack: dict) -> dict:
    """
    The model's output is untrusted input until this returns.

    The citation check is the one that matters: a note that names a record this
    person does not have is either a hallucination or somebody else's data, and
    there is no version of either that is safe to write down.
    """
    if not isinstance(raw, dict):
        raise ReflectionRejected("response must be a JSON object")

    written = {}
    minimum, maximum = pack.get("sentence_target", (1, 6))
    for field in ("reflection", "reflection_zh"):
        text = raw.get(field)
        if not isinstance(text, str) or not text.strip():
            raise ReflectionRejected(f"{field} must be a non-empty string")
        text = text.strip()
        limit = MAX_REFLECTION_CHARS[field]
        if len(text) > limit:
            raise ReflectionRejected(
                f"{field} is longer than {limit} characters "
                f"({len(text)})")
        count = _sentence_count(text)
        if not minimum <= count <= maximum:
            raise ReflectionRejected(
                f"{field} has {count} sentences; expected {minimum}-{maximum}")
        check_language(text)
        written[field] = text

    cited = raw.get("cited_record_ids")
    if not isinstance(cited, list) or not cited:
        raise ReflectionRejected("cited_record_ids must be a non-empty array")

    known = set(pack["record_ids"])
    unknown = [c for c in cited if c not in known]
    if unknown:
        raise ReflectionRejected(
            f"reflection cited {len(unknown)} record id(s) not in this period's pack")

    return {**written, "cited_record_ids": list(dict.fromkeys(cited))}


def ask_gemini(pack: dict) -> dict:
    """The one model call on this path. Same client, same strictness as capture."""
    from gemini import GEMINI_LOCATION, MODEL, ModelMisconfigured

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise ModelMisconfigured("GOOGLE_CLOUD_PROJECT is unset")
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=project, location=GEMINI_LOCATION)
    resp = client.models.generate_content(
        model=MODEL,
        contents=f"{INSTRUCTION}\n\nPACK:\n{json.dumps(pack, ensure_ascii=False)}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=REFLECTION_SCHEMA,
            temperature=0,
        ),
    )
    if not getattr(resp, "text", None):
        raise ReflectionRejected("model returned nothing")
    return json.loads(resp.text)


def deterministic(pack: dict) -> dict:
    """
    The offline stand-in, for tests and for the demo journal.

    It is a sentence template over the counts, and it is honest about being one:
    the summary it produces is stamped `writer: "deterministic"`, so nothing
    downstream can mistake it for the model having run. It is never a fallback —
    a model failure leaves the period with no leaf, which is the normal outcome
    anyway.
    """
    kinds = pack["counts_by_food"]
    parts = []
    if kinds.get("grass"):
        parts.append("small jobs that usually go unnoticed")
    if kinds.get("carrot"):
        parts.append("planning, deciding and keeping track")
    if kinds.get("none"):
        parts.append("the ordinary household work")
    subject = ", and ".join(parts) if parts else "a few different things"
    cadence = pack.get("cadence", "weekly")

    zh_parts = []
    if kinds.get("grass"):
        zh_parts.append("平常不會被算作工作的小事")
    if kinds.get("carrot"):
        zh_parts.append("安排、決定與記著的事")
    if kinds.get("none"):
        zh_parts.append("一般的家事")
    zh_subject = "、".join(zh_parts) if zh_parts else "幾件不同的事"
    if cadence == "daily":
        text = f"Today you recorded {subject}."
        text_zh = f"今天你記下了{zh_subject}。"
    else:
        text = (f"This {cadence} reflection holds {subject}. "
                "It stays with what was recorded and reads nothing else into it.")
        text_zh = (f"這份回顧記下了{zh_subject}。"
                   "它只停在已有的紀錄，不多讀出別的意思。")
        minimum = pack.get("sentence_target", (2, 4))[0]
        if minimum >= 3:
            text += " The activity labels remain separate from your own words."
            text_zh += "活動標籤與你自己的話仍然分開。"
        if minimum >= 4:
            text += " Nothing has been shared or sent."
            text_zh += "沒有任何內容被分享或送出。"

    return {"reflection": text, "reflection_zh": text_zh,
            "cited_record_ids": list(pack["record_ids"])}


# ------------------------------------------------------------- writing ------
def summary_id(pack: dict) -> str:
    """Stable for one due period and record set, so a retry cannot double-write."""
    seed = (str(pack.get("scheduled_for")) + "|" + pack.get("cadence", "weekly")
            + "|" + ",".join(sorted(pack["record_ids"])))
    return "s_" + hashlib.sha256(seed.encode()).hexdigest()[:16]


TITLE = "A folded leaf"
TITLE_ZH = "一片摺好的葉子"


def run_for(ws, *, now: Optional[datetime] = None,
            writer: Optional[Callable[[dict], dict]] = None,
            writer_name: str = "gemini") -> Optional[dict]:
    """
    One workspace, one scheduled check. Returns a summary or None for silence.

    The order is the whole safety argument: eligibility and the fact pack are
    settled before the model is reached, the model's answer is validated against
    the pack, the summary is written, and only then does the garden learn there
    is a leaf. A failure at any point leaves the meadow exactly as it was.
    """
    now = now or now_jst()
    verdict = eligible(ws, now)
    if not verdict.ok:
        # A due check is complete even when it stays silent. Advancing here
        # prevents a record added tomorrow from triggering yesterday's missed
        # reflection outside the cadence the person chose.
        if verdict.due is not None and now >= verdict.due:
            expected = verdict.preferences_state or {}
            ws.advance_reflection(
                expected, schedule.completed(expected, verdict.due, now=now))
        return None

    pack = verdict.pack or {}
    write = writer or ask_gemini
    raw = write(pack)
    note = validate(raw, pack)

    sid = summary_id(pack)

    summary = {
        "summary_id": sid,
        "created_at": now.isoformat(timespec="seconds"),
        "scheduled_for": pack.get("scheduled_for"),
        "cadence": pack.get("cadence"),
        "rule_id": RULE_ID,
        "window_start": pack["window_start"],
        "window_end": pack["window_end"],
        "reflection": note["reflection"],
        "reflection_zh": note["reflection_zh"],
        "cited_record_ids": note["cited_record_ids"],
        "record_count": pack["record_count"],
        "writer": writer_name,
        "generated_by": "scheduled-task",     # never a person pressing something
        "provenance": "inferred",             # the model's reading, labelled as such
    }
    # 🔴 One write, not two.
    #
    # These used to be `ws.summaries[sid] = …` followed by `ws.garden.write(…)`.
    # A failure between them left the summary stored and the meadow empty — and
    # because the next run started with `if sid in ws.summaries: return None`,
    # it exited early every time afterwards. That leaf could never appear. The
    # early return is gone and both documents go in together.
    next_preferences = schedule.completed(
        verdict.preferences_state or {}, verdict.due or now, now=now)
    next_preferences["last_reflection_at"] = summary["created_at"]
    committed = ws.commit_reflection(
        sid, summary, _garden_for(sid, note, summary), next_preferences,
        expected_preferences=verdict.preferences_state or {})
    return summary if committed else None


def _garden_for(sid: str, note: dict, summary: dict) -> dict:
    return {
        "leaf": {"summary_id": sid, "title": TITLE, "title_zh": TITLE_ZH,
                 "body": note["reflection"], "body_zh": note["reflection_zh"]},
        "last_summary_at": summary["created_at"],
        "seen_at": None,
    }


def reconcile(ws) -> Optional[dict]:
    """
    Put a leaf back out when the summary exists and the meadow does not show it.

    Only for the unambiguous case — a stored summary, and a garden that has no
    leaf at all and has never pointed at one. A garden that has moved on to a
    later period is left alone: bringing an old leaf back would be inventing an
    event, and this function exists to repair a dropped write, not to decide
    anything.
    """
    latest = ws.summaries.latest()
    if latest is None:
        return None
    state = ws.garden.read()
    if state.get("leaf") or state.get("last_summary_at"):
        return None
    note = {"reflection": latest.get("reflection", ""),
            "reflection_zh": latest.get("reflection_zh", "")}
    ws.commit_reflection(latest["summary_id"], latest,
                         _garden_for(latest["summary_id"], note, latest))
    return latest


def put_away(ws, summary_id: str, *, now: Optional[datetime] = None) -> dict:
    """Remove one token from the meadow; its summary remains in history."""
    now = now or now_jst()
    return ws.put_away_leaf(summary_id, now.isoformat(timespec="seconds"))
