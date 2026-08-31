"""
The deterministic stand-in for Gemini.

Why it exists: the real adapter needs a Google Cloud project, and a test suite
that needs credentials is a test suite nobody runs. This produces the same
shape as the real model — the schema in `contract.py` — from a small rule table,
so the whole path downstream of extraction can be exercised offline: validation,
policy, ledger, replay, correction, the rabbit's sentence.

Three things it is deliberately not:

  - It is never a fallback. If the real model is configured and fails, the
    person is offered the text box; they are never handed a guess dressed up as
    a model result. This module is reachable only when MALLOW_FAKE_MODEL=1 is
    set on purpose.
  - It is not a classifier. The rules below are keyword matches. They are good
    enough to demonstrate the pipeline and nowhere near good enough to decide
    anything about a real person's day.
  - It is not trained, tuned, or calibrated on anybody's real records. Every
    phrase here was written for this file.

When it is on, the app runs against a separate journal and says so on screen,
so demonstration rows can never be mistaken for a real record.
"""
from __future__ import annotations

import os
import re
from typing import Any

MARKER = "fake-deterministic-v1"

# Q-45 is a relationship, not an object vocabulary. English nouns such as
# "toy", "bottle" and "uniform" are ambiguous on their own: they may belong
# to an adult, a pet or nobody named in the sentence. They become child-care
# infrastructure only when the words say whose item it is, or when the item is
# itself child-specific. The Cantonese/Chinese list stays concrete because the
# deployed acceptance sentence uses 「幫佢洗玩具」 and the pronoun supplies the
# relationship in context.
_Q45_MAINTENANCE = (
    r"洗|消毒|清潔|整理|收拾|"
    r"wash(?:ed|ing)?|sanitis(?:e|ed|ing)|sanitiz(?:e|ed|ing)|"
    r"steriliz(?:e|ed|ing)|clean(?:ed|ing)?|tid(?:y|ied|ying)"
)
_Q45_ZH_CHILD_ITEM = r"玩具|奶樽|奶瓶|奶嘴|尿布墊|圍兜|書包|校服"
_Q45_EN_CHILD_OWNER = (
    r"(?:(?:my|the|a|our|their|his|her)\s+)?"
    r"(?:baby|child|kid|children)(?:['’]s|s['’])?\s+"
    r"(?:toys?|bottles?|clothes?|uniforms?|belongings?)"
)
_Q45_EN_CHILD_PURPOSE = (
    r"(?:(?:my|the|a|our|their|his|her)\s+)?"
    r"(?:toys?|bottles?|clothes?|uniforms?)\s+"
    r"(?:for|belonging to)\s+"
    r"(?:(?:my|the|a|our|their|his|her)\s+)?(?:baby|child|kid|children)"
)
_Q45_EN_CHILD_SPECIFIC = (
    r"baby bottles?|feeding bottles?|pacifiers?|dumm(?:y|ies)|"
    r"changing mats?|school bags?|school uniforms?|bibs?"
)
_Q45_CHILD_ITEM = (
    rf"(?:{_Q45_ZH_CHILD_ITEM}|{_Q45_EN_CHILD_OWNER}|"
    rf"{_Q45_EN_CHILD_PURPOSE}|{_Q45_EN_CHILD_SPECIFIC})"
)
_Q45_RULE = (
    rf"(?:{_Q45_CHILD_ITEM})[^。.!?]{{0,24}}?(?:{_Q45_MAINTENANCE})|"
    rf"(?:{_Q45_MAINTENANCE})[^。.!?]{{0,24}}?(?:{_Q45_CHILD_ITEM})"
)

# (pattern, activity label, labour kind)
# Order matters: the first match wins, and mental load is checked before chores
# so that "decided what to cook" is not filed as cooking.
RULES: tuple[tuple[str, str, str], ...] = (
    (r"決定|想好|考慮|比較|挑|選|安排|記得|提醒|追|問到|查",
     "deciding or holding something in mind", "mental_load"),
    (r"decide|decided|deciding|remember|chase|compare|choose|book|plan",
     "deciding or holding something in mind", "mental_load"),

    # 🔴 Hands-on care, before the recognised_work row and after mental load.
    #
    # Before, because "哄睡" contains none of the cooking words but a future
    # rule might overlap. After mental load, because "記得要預約" is arranging
    # care, not giving it — the appointment is mental load and sitting with the
    # child is a chore, and that distinction is the whole of the ruling.
    #
    # This mirrors the extraction prompt on purpose: demo mode and production
    # must not disagree about what a week looked like.
    (r"哄睡|陪睡|安撫|餵|喂奶|餵奶|換尿布|洗澡|穿衣|照顧|看顧|陪.*(孩子|小孩)",
     "hands-on care", "invisible_chore"),
    (r"settl\w* (my |the )?(child|baby|kid)|put \w+ to (bed|sleep)|soothe|"
     r"feed|nappy|diaper|bath\w*|dress\w* (my |the )?(child|baby|kid)|"
     r"car(e|ing) for|look\w* after|sit\w* with",
     "hands-on care", "invisible_chore"),

    # Taking a dependent child to or from school is accompanying them. It moved
    # here from the recognised_work rows below; see the note there.
    (r"接送.*(孩子|小孩|學校|上學|放學|幼稚園|托兒所)|"
     r"送.*(孩子|小孩).*(學校|上學|幼稚園|托兒所)|"
     r"接.*(孩子|小孩).*(放學|下課)|"
     r"(陪|跟|同).*(孩子|小孩).*(搭|坐|乘|走路|上學|返學)",
     "accompanying a child to or from school", "invisible_chore"),
    (r"school run|drop\s*off (my |the )?(child|kid|children)|"
     r"pick\s*up (my |the )?(child|kid|children)|"
     r"take (my |the )?(child|kid|children) to (school|daycare|nursery)|"
     r"accompany (my |the )?(child|kid|children)",
     "accompanying a child to or from school", "invisible_chore"),

    (r"寫名字|名前|貼標|整理|收拾|歸位|補貨|準備.*(帶|明天|要用)|預備",
     "labelling, tidying or preparing ahead", "invisible_chore"),
    (r"label|labell|tidy|put away|restock|refill|prepare|pack",
     "labelling, tidying or preparing ahead", "invisible_chore"),

    # 🔴 Q-36 Stage 1, 2026-08-27. `接送` and `school run` used to sit on these
    # two rows, exactly as they sat in the extraction prompt, and the double
    # therefore agreed with the defect instead of catching it.
    #
    # The 戰略官's Stage 1 delta named three files and this was not one of them.
    # Leaving it would have kept the retired rule alive in the offline suite:
    # the prompt would say accompaniment, the double would say already-counted,
    # and every offline test would be green about the wrong answer.
    #
    # 🚫 This row is not evidence that the real model understands any of it —
    # see PRODUCT_DECISIONS 41 C. It exists so demo mode and production do not
    # describe the same week differently.
    # 🔴 Q-45, 2026-08-30. Before the general cooking/cleaning rows, and for the
    # same reason the care row above sits where it does: "洗玩具" contains 洗,
    # and a general cleaning row placed first answers it as already-counted work.
    #
    # The Owner recorded 「幫咗佢洗玩具用咗十五分鐘」 on the deployed build and
    # was told it was work the world already counts, while 「買咗新玩具畀佢」
    # earned grass. Both obeyed the prompt: cleaning was recognised_work and
    # buying matched "preparing what someone else will need".
    #
    # The ruling is a general rule, not a `toy` keyword: maintaining or
    # sanitising the belongings and care equipment a dependent child needs is
    # care infrastructure. General household cleaning is untouched.
    # 🔴 A concrete noun is still not ownership. The second draft stopped
    # treating a person as a belonging, but bare `bottle`, `uniform` and `toy`
    # still made adult or pet belongings look like child care. `_Q45_RULE`
    # therefore requires the sentence to connect an ambiguous English object
    # to a child; see the constants above. General washing remains ordinary
    # household work when that relationship is absent.
    (_Q45_RULE,
     "maintaining a child's things", "invisible_chore"),
    (r"煮|做飯|洗碗|洗衣|打掃|買菜",
     "ordinary household work", "recognised_work"),
    (r"cook|dishes|laundry|clean|wash|groceries",
     "ordinary household work", "recognised_work"),
)

# Only what was actually said. No estimate is ever produced here, because the
# whole product rests on never inventing a number.
#
# 🔴 Hours count, and are converted rather than dropped. "哄睡了兩小時" used to
# come back with no duration at all — not because the person withheld it, but
# because this table only read 分鐘. A unit the speaker used is theirs; only a
# number they never gave would be an invention.
# 🔴 Compound Chinese integers, minutes only (戰略官 2026-08-31).
#
# "用咗十五分鐘" is a number and a unit the speaker gave; reading it is not
# estimating. The conservative refusal below stays exactly where it was — on the
# HOURS path — because "二十小時" reaches a different order of magnitude and the
# original ruling about not guessing there is untouched.
MINUTES = re.compile(
    r"([0-9]{1,3}|[一二兩三四五六七八九十]{1,3})\s*(分鐘|分|minutes?|mins?|min)\b",
    re.I)
HOURS = re.compile(r"([0-9一二兩三四五六七八九十半]{1,3})\s*(小時|個小時|hours?|hrs?|hr)\b", re.I)
CN_NUM = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}


def cn_int(raw: str):
    """A Chinese integer from 1 to 99, or None. Never an approximation.

    十五 is fifteen, 二十 is twenty, 三十五 is thirty-five. Anything this cannot
    read exactly comes back None rather than a guess.
    """
    if raw.isdigit():
        return int(raw)
    if "十" not in raw:
        return CN_NUM.get(raw) if isinstance(CN_NUM.get(raw), int) else None
    tens, _, units = raw.partition("十")
    t = 1 if tens == "" else CN_NUM.get(tens)
    u = 0 if units == "" else CN_NUM.get(units)
    if not isinstance(t, int) or not isinstance(u, int) or not 1 <= t <= 9 or not 0 <= u <= 9:
        return None
    return t * 10 + u


def minutes_in(fragment: str):
    """The number the speaker said, in minutes, or None. Never a guess."""
    m = MINUTES.search(fragment)
    if m:
        return cn_int(m.group(1))
    h = HOURS.search(fragment)
    if not h:
        return None
    raw = h.group(1)
    try:
        value = float(raw)
    except ValueError:
        if raw not in CN_NUM:
            return None            # 十一, 二十… not read rather than guessed
        value = CN_NUM[raw]
    return int(value * 60) or None


class _Duration:
    """Kept so the rest of this file reads the same as before."""

    @staticmethod
    def search(fragment):
        return minutes_in(fragment) is not None or None

    @staticmethod
    def sub(_repl, fragment):
        return HOURS.sub("", MINUTES.sub("", fragment))


DURATION = _Duration()

SPLIT = re.compile(r"[、，,；;。.]|\band\b|\bthen\b")


def enabled() -> bool:
    return os.getenv("MALLOW_FAKE_MODEL") == "1"


def _kind(fragment: str) -> tuple[str, str]:
    for pattern, label, kind in RULES:
        if re.search(pattern, fragment, re.I):
            return label, kind
    return "something that was said", "unknown"


def _domain(fragment: str) -> str:
    """A small demo-only domain table; not a production classifier."""
    rules = (
        (r"衣服|洗衣|名前|label.*cloth|laundry", "clothing_laundry"),
        (r"診所|牙醫|藥|clinic|dentist|medicine", "health_admin"),
        (r"學校|托兒|幼稚園|daycare|school|teacher|回條", "school_community"),
        (r"煮|做飯|cook|dinner|meal", "food_preparation"),
        (r"補貨|衛生紙|買菜|restock|refill|groceries", "shopping_restocking"),
        (r"接送|school run|pick.?up|drop.?off", "transport_errands"),
        (r"哄睡|陪睡|餵|喂奶|餵奶|尿布|洗澡|穿衣|照顧|看顧|陪.*(孩子|小孩)|"
         r"settle.*(child|baby|kid)|feed.*(child|baby|kid)|nappy|diaper|"
         r"bath.*(child|baby|kid)|dress.*(child|baby|kid)", "care_child"),
        (r"打掃|洗碗|整理|收拾|歸位|clean|dishes|tidy|put away",
         "household_upkeep"),
        (r"安排|記得|提醒|追|schedule|remember|chase|plan", "household_admin"),
    )
    for pattern, domain in rules:
        if re.search(pattern, fragment, re.I):
            return domain
    return "other"


def understand_text(note: str) -> dict[str, Any]:
    """
    Same output shape as the real adapter. Nothing here is inferred.

    🔴 Every `source_text` is cut out of the transcript by offset, never
    rebuilt from pieces. `contract._span_of` requires the span to be a real
    substring of the transcript, and the double has to obey the contract it is
    standing in for — a double that is allowed to produce output the real
    pipeline would reject is a double that hides defects rather than finding
    them. This used to join a merged fragment with a hard-coded full-width
    comma, so "labelled the clothes, 5 minutes" came back as
    "labelled the clothes，5 minutes": one character different from anything
    the person said, and no longer quotable from the transcript.
    """
    said = note.strip()
    events: list[dict[str, Any]] = []
    cursor = 0                      # how far into `said` we have already read
    for piece in SPLIT.split(said):
        fragment = piece.strip()
        if len(fragment) < 2:
            cursor += len(piece)
            continue
        start = said.index(fragment, cursor)
        end = start + len(fragment)
        cursor = end
        m = minutes_in(fragment) is not None

        # "幫衣服寫名字，35 分鐘" is one thing a person said, not two. A fragment
        # that is nothing but a duration belongs to the clause before it; on its
        # own it would be filed as unclassified, which is an artefact of where
        # the comma fell rather than anything about the work. The joined span is
        # taken from the transcript between the two, separator and all.
        if m and not DURATION.sub("", fragment).strip(" 　,.、，。") and events:
            prev = events[-1]
            prev["duration_minutes"] = prev["duration_minutes"] or minutes_in(fragment)
            prev["source_text"] = said[prev["_start"]:end]
            continue

        label, kind = _kind(fragment)
        events.append({
            "activity_text": label,
            "source_text": fragment,          # the speaker's words, quoted
            "activity_domain": _domain(fragment),
            "labour_kind": kind,
            "duration_minutes": minutes_in(fragment),
            "occurred_at": None,                # never assumed
            "_start": start,
        })
    for ev in events:
        ev.pop("_start", None)
    return {"transcript": said, "events": events}


def understand(audio: bytes, mime_type: str) -> dict[str, Any]:
    """
    There is no offline speech recognition here and none is pretended.

    A demonstration recording cannot be transcribed by a keyword table, so the
    fake adapter reports the audio as unreadable and the app does what it does
    for any unreadable audio: it opens the text box. That is the honest
    behaviour, and it exercises the same failure path a real user would hit.
    """
    from gemini import AudioUnreadable
    raise AudioUnreadable("fake model does not transcribe audio")
