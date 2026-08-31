"""
The decision. No model participates.

    invisible_chore  -> grass
    mental_load      -> carrot
    recognised_work  -> recorded, no food (the world already counts it)
    unknown          -> withheld: shown, but nothing is issued

"withheld" is not an error state. It is Mallow saying it heard something and
would rather record it unclassified than hand out food it is not sure about.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["grass", "carrot", "none", "withheld"]

REASON = {
    "grass":    "hands-on household or child-care work that often goes unseen",
    "carrot":   "planning, remembering, deciding or coordinating care",
    "none":     "already counted by the world - recorded, no food",
    "withheld": "heard, but not classified - no food issued",
}


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    policy_version: str = "voice-spike-2026-08-22"


def decide(labour_kind: str) -> Decision:
    outcome: Outcome = {
        "invisible_chore": "grass",
        "mental_load": "carrot",
        "recognised_work": "none",
    }.get(labour_kind, "withheld")
    return Decision(outcome, REASON[outcome])
