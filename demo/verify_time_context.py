"""Credentialed Q-28 check against the real configured Gemini model.

This is deliberately outside pytest.  The release gate must be runnable without
credentials; semantic extraction must still be checked against the real model
before deployment.  Every sentence here is synthetic and contains no Owner
record.

Run from the repository root after Application Default Credentials exist::

    GOOGLE_CLOUD_PROJECT=your-project python3 demo/verify_time_context.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "spike" / "voice")]

from contract import validate  # noqa: E402
from gemini import MODEL, PROMPT_VERSION, understand_text  # noqa: E402


CASES = (
    ("0740 出發搭巴士送孩子去學校", "07:40"),
    ("0900 drop off孩子 at school", "09:00"),
    ("The school form has reference number 0740.", None),
)


def main() -> None:
    failures = []
    print(f"MODEL={MODEL} PROMPT_VERSION={PROMPT_VERSION}")
    for note, expected in CASES:
        candidates = validate(understand_text(note))
        got = [event.occurred_at for event in candidates.events]
        passed = bool(got) and got[0] == expected if expected else all(
            value is None for value in got)
        print(f"{'PASS' if passed else 'FAIL'} expected={expected!r} got={got!r} note={note!r}")
        if not passed:
            failures.append((note, expected, got, candidates.rejected))
    if failures:
        raise SystemExit(f"Q-28 real-model check failed: {failures!r}")
    print("Q28_REAL_MODEL=PASS")


if __name__ == "__main__":
    main()
