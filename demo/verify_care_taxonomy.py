#!/usr/bin/env python3
"""
The five acceptance cases from the care-labour ruling, against the real model.

🔴 Why this is a script and not a test.

The ruling of 2026-08-24 fixed how Gemini must classify hands-on care. No test
in this repository can check that: they all run against the deterministic
double, which is a keyword table that was written to agree with the ruling. A
green suite says the double agrees with us. It says nothing about the model.

So the suite asserts everything around the classification — that the rule is in
the prompt, that the boundary between giving care and arranging it is written
down, that the prompt version moved, that the double follows the same
taxonomy — and this script asserts the classification itself, by asking the
model.

It needs credentials and a network, which is exactly why it is not in the gate.

    export GOOGLE_CLOUD_PROJECT=<project>
    export GEMINI_LOCATION=global
    python3 demo/verify_care_taxonomy.py

Every line is invented. Nothing here is anybody's real day.
"""
from __future__ import annotations

from collections import Counter
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "spike" / "voice")]

# One case = one utterance and the events it must produce.
#
#   events: []              the utterance is outside what Mallow records. The
#                           rabbit still answers (`heard_no_activity`) and the
#                           capture is still stored — but no activity record and
#                           no food. 🔴 An `unknown` event here would be wrong:
#                           it would put a non-labour thing in the labour ledger.
#   domain: None            this case is not about the domain. `transport_errands`
#                           and `care_child` are both defensible for a bus ride
#                           with a child; the domain says what kind of activity,
#                           the kind says what kind of labour, and only the
#                           second one decides food.
CASES = [
    # ── hands-on care ───────────────────────────────────────────────────────
    ("哄睡了兩小時",
     [("care_child", "invisible_chore", 120)]),
    ("I spent 45 minutes settling my child to sleep.",
     [("care_child", "invisible_chore", 45)]),

    # 🔴 Q-32 #1, ruled 2026-08-27: two acts joined by an enumeration marker are
    # two traceable spans and two records. The rabbit may answer once; the ledger
    # keeps both. The script used to demand one event and was wrong to.
    ("餵奶、換了尿布",
     [("care_child", "invisible_chore", None),
      ("care_child", "invisible_chore", None)]),

    # ── mental load ─────────────────────────────────────────────────────────
    ("記得明天要替孩子預約診所",
     [("health_admin", "mental_load", None)]),
    ("remember to book the clinic for my child tomorrow",
     [("health_admin", "mental_load", None)]),

    # ── Q-45 · care infrastructure vs general cleaning ──────────────────────
    #
    # 🔴 Owner, live on mallow-00019-kj2, 2026-08-30:
    #   「幫咗佢洗玩具用咗十五分鐘」 → recognised_work, no food
    #   「買咗新玩具畀佢」           → invisible_chore, grass
    # Both obeyed the prompt of the day: cleaning was recognised_work, and
    # buying matched "preparing what someone else will need". Fifteen minutes of
    # hands-on work for a child earned nothing while a purchase earned grass.
    #
    # Third instance of one shape, after Q-12 (settling to sleep) and Q-36 (the
    # school run): child-specific work falling back into the general
    # cooking/cleaning bucket. The ruling is a general rule, not a `toy`
    # keyword — maintaining the belongings and care equipment a dependent child
    # needs is care infrastructure.
    #
    # 🔴 The domain is deliberately unpinned. `care_child` and
    # `household_upkeep` are both defensible for washing a child's toys; the
    # kind is what decides food, and the kind is what this case tests.
    ("幫佢洗玩具用咗十五分鐘",
     [(None, "invisible_chore", 15)]),
    ("I sanitised my baby's bottles.",
     [(None, "invisible_chore", None)]),
    ("I washed my child's toys.",
     [(None, "invisible_chore", None)]),
    ("I washed the toys for my child.",
     [(None, "invisible_chore", None)]),

    # 🔴 The negatives, and the half that catches over-reach. Both name a child
    # and are still ordinary household cleaning. 戰略官 2026-08-31: the first
    # draft of the deterministic double answered the first of these
    # `invisible_chore`, because it treated `baby` as a belonging.
    ("I cleaned the kitchen while my baby slept.",
     [("household_upkeep", "recognised_work", None)]),
    ("I cleaned the kitchen for my child.",
     [("household_upkeep", "recognised_work", None)]),
    ("I washed my work uniform.",
     [(None, "recognised_work", None)]),
    ("I cleaned a bottle.",
     [(None, "recognised_work", None)]),

    # The other side of the same line. General cleaning is untouched: if this
    # row ever comes back `invisible_chore`, the rule has swallowed the
    # household and §2 has been reopened without a decision.
    ("打掃廚房十五分鐘",
     [("household_upkeep", "recognised_work", 15)]),

    # ── already counted by the world ────────────────────────────────────────
    ("煮了晚飯",
     [("food_preparation", "recognised_work", None)]),
    ("打掃了三十分鐘",
     [("household_upkeep", "recognised_work", 30)]),

    # ── code-mixed ──────────────────────────────────────────────────────────
    ("幫孩子label晒啲school clothes，thirty-five minutes",
     [("clothing_laundry", "invisible_chore", 35)]),
    ("花咗20 minutes比較兩間clinic俾小朋友",
     [("health_admin", "mental_load", 20)]),

    # ── 🔴 Q-36 · the school run. Stage 1, authorised 2026-08-27 ─────────────
    #
    # Every one of these came back `recognised_work` on 2026-08-27 and the
    # person was told 已被算作工作 — the world already counts this. Twice a day,
    # to somebody whose whole reason for opening the app is that it does not.
    #
    # The model was obeying the prompt: `the school run` was written into
    # recognised_work. It is not any more.
    ("我陪孩子搭巴士去學校",
     [(None, "invisible_chore", None)]),
    ("接孩子放學",
     [(None, "invisible_chore", None)]),
    ("I accompany my child to school by bus.",
     [(None, "invisible_chore", None)]),
    ("0900 drop off 孩子",
     [(None, "invisible_chore", None)]),
    ("0740 出發搭巴士送孩子去學校",
     [(None, "invisible_chore", None)]),
    ("跟孩子搭巴士",
     [(None, "invisible_chore", None)]),

    # 🔴 r4, 戰略官覆核 2026-08-27. Every positive above writes 孩子 or child,
    # so all six could pass while the prompt threw away the way people
    # actually talk. These three name child transport without the noun.
    ("Did the school run at 8.",
     [(None, "invisible_chore", None)]),
    # 🔴 "School pickup at 3." on its own could be a reminder rather than a
    # thing that happened, and `mental_load` would be a defensible answer to
    # it. A corpus case whose expected answer is arguable tests the corpus,
    # not the model. The verb makes it a completed act. (戰略官, 2026-08-27)
    ("Did school pickup at 3.",
     [(None, "invisible_chore", None)]),
    ("Went to daycare for drop-off.",
     [(None, "invisible_chore", None)]),

    # ── 🔴 the other side of the same line ──────────────────────────────────
    #
    # The fence must not swallow the speaker's own life, and widening care must
    # not widen it into a personal commute. Both directions are load-bearing.
    ("0740 出發搭巴士",                       []),
    ("我自己搭巴士去上班",                     []),
    # 🔴 The bare journey has to hold in English too, or the boundary is only
    # tested in one language while the shorthand above is only tested in the
    # other. Both directions, both languages, same run.
    ("Caught the train at 8.",                []),
    ("今天跳舞了",                            []),
    ("went for a run this morning",           []),

    # ── time context, not merely taxonomy ──────────────────────────────────
    # This sentence contains both a school mental-load event and four digits.
    # The digits are an identifier, never a clock. It used to be listed as a
    # release blocker without this script asserting `occurred_at` at all.
    ("學校回條的 reference number 是 0740，我記得要交回去",
     [("school_community", "mental_load", None)]),
]

# Membership in this mapping means `occurred_at` is part of the expected
# result. `None` therefore means "assert null", not "do not assert".
OCCURRED_AT_EXPECTED = {
    "0900 drop off 孩子": ["09:00"],
    "0740 出發搭巴士送孩子去學校": ["07:40"],
    "學校回條的 reference number 是 0740，我記得要交回去": [None],
}

# Event count alone is insufficient for the Q-32 ruling: two duplicate events
# citing the whole utterance would still be two rows, but not two independently
# traceable acts.
SOURCE_TEXT_EXPECTED = {
    "餵奶、換了尿布": Counter(["餵奶", "換了尿布"]),
}

CONTEXT_CASE = "Hello join child school's stay and play. 2 hours then lunch 1 hour"

# 🔴 The first event's domain is a set, not a value. (戰略官, 2026-08-28)
#
# Staying at a child's school session is a school thing and a child thing at
# once, and the model has answered both across three runs of the same sentence
# with the same prompt:
#
#     run 1  school_community      run 2  care_child      run 3  care_child
#
# `labour_kind` was `invisible_chore` all three times, and the durations were
# 120 and 60 all three times. The field that decides food does not move; the
# field that describes the activity does.
#
# 🚫 The check is widened, not removed. `transport_errands` or `other` here
# would be a real regression and must still fail — an assertion that accepts
# anything is the same as no assertion, which is how a corpus quietly stops
# being able to fail.
CONTEXT_EXPECTED = [
    ({"school_community", "care_child"}, "invisible_chore", 120),
    ({"care_child"},                     "invisible_chore", 60),
]


def context_matches(got, expected=None) -> bool:
    """
    Does one multi-event result satisfy the ruling?

    🔴 Pulled out of `main()` so it can be tested without credentials. While it
    was an inline expression, the only way to find out whether the widened
    domain check still rejected a wrong domain was to spend a Gemini run on it.

    `got` is [(activity_domain, labour_kind, duration_minutes), ...].
    Each expected domain is a **set** of the answers the ruling allows; kind
    and duration stay exact.
    """
    expected = CONTEXT_EXPECTED if expected is None else expected
    if len(got) != len(expected):
        return False
    return all(domain in allowed and kind == want_kind and minutes == want_minutes
               for (domain, kind, minutes), (allowed, want_kind, want_minutes)
               in zip(got, expected))


def main() -> int:
    if os.getenv("MALLOW_FAKE_MODEL") == "1":
        print("🔴 MALLOW_FAKE_MODEL=1 is set. This script exists to ask the "
              "real model; the double already agrees with us by construction.")
        return 2
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("Set GOOGLE_CLOUD_PROJECT (and GEMINI_LOCATION=global).")
        return 2

    import gemini
    from contract import validate

    print(f"model  : {gemini.MODEL}")
    print(f"prompt : {gemini.PROMPT_VERSION}\n")

    failures = 0
    for said, expected in CASES:
        try:
            cand = validate(gemini.understand_text(said))
        except Exception as e:                                    # noqa: BLE001
            print(f"❌ {said}\n     the call or the contract failed: "
                  f"{type(e).__name__}: {e}")
            failures += 1
            continue

        got = [(e.activity_domain, e.labour_kind, e.duration_minutes)
               for e in cand.events]

        if len(got) != len(expected):
            print(f"❌ {said}\n     expected {len(expected)} event(s), "
                  f"got {len(got)}: {got}")
            failures += 1
            continue

        # 🔴 Order is not asserted. "餵奶、換了尿布" is two acts; which one the
        # model reports first is not a rule anybody wrote down, and a script
        # that fails on the order would be testing itself.
        ok = True
        for (want_domain, want_kind, want_minutes), ev in zip(expected, cand.events):
            if want_domain is not None and ev.activity_domain != want_domain:
                ok = False
            if ev.labour_kind != want_kind or ev.duration_minutes != want_minutes:
                ok = False

        if said in OCCURRED_AT_EXPECTED:
            got_times = [ev.occurred_at for ev in cand.events]
            if got_times != OCCURRED_AT_EXPECTED[said]:
                ok = False

        if said in SOURCE_TEXT_EXPECTED:
            got_sources = Counter(ev.source_text for ev in cand.events)
            if got_sources != SOURCE_TEXT_EXPECTED[said]:
                ok = False

        print(f"{'✅' if ok else '❌'} {said}")
        if not expected:
            print(f"     no activity record, and none expected")
        for index, ((want_domain, want_kind, want_minutes), ev) in enumerate(
                zip(expected, cand.events)):
            if want_domain is not None:
                print(f"     domain   {ev.activity_domain:16} expected {want_domain}")
            else:
                print(f"     domain   {str(ev.activity_domain):16} (not asserted)")
            print(f"     kind     {ev.labour_kind:16} expected {want_kind}")
            print(f"     minutes  {str(ev.duration_minutes):16} expected {want_minutes}")
            if said in OCCURRED_AT_EXPECTED:
                want_time = OCCURRED_AT_EXPECTED[said][index]
                print(f"     occurred {str(ev.occurred_at):16} expected {want_time}")
            print(f"     quoted   {ev.source_text!r}")
        if said in SOURCE_TEXT_EXPECTED:
            print(f"     source spans {Counter(ev.source_text for ev in cand.events)}")
            print(f"     expected     {SOURCE_TEXT_EXPECTED[said]}")
        failures += 0 if ok else 1

    try:
        cand = validate(gemini.understand_text(CONTEXT_CASE))
        got = [(e.activity_domain, e.labour_kind, e.duration_minutes)
               for e in cand.events]
        ok = context_matches(got)
        print(f"{'✅' if ok else '❌'} {CONTEXT_CASE}")
        print(f"     events   {got}")
        print("     expected " + str([
            (sorted(allowed), kind, minutes)
            for allowed, kind, minutes in CONTEXT_EXPECTED]))
        failures += 0 if ok else 1
    except Exception as e:                                        # noqa: BLE001
        print(f"❌ {CONTEXT_CASE}\n     the call or contract failed: "
              f"{type(e).__name__}: {e}")
        failures += 1

    print()
    if failures:
        print(f"🔴 {failures} of {len(CASES) + 1} did not match the ruling.")
        print("   The prompt is what decides this. Do not change the policy or "
              "the schema to make a case pass.")
    else:
        print(f"✅ all {len(CASES) + 1} match. This is what lets Q-12 be marked "
              f"verified rather than implemented.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
