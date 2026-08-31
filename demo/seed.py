"""
Invented records for exercising the scheduled reflection in local demo mode.

🔴 This is not part of the product. It lives outside `mobile/`, it is excluded
from the container image by the Dockerfile's explicit COPY list, and it refuses
to run unless `MALLOW_FAKE_MODEL=1` — which also means it can only ever write
into the demo journal, never a real one.

Why it exists: a scheduled reflection needs records to describe. This puts
plausible synthetic records in place without pretending they came from a
person or bypassing the normal eligibility and writing path.

🔴 Every sentence below was written for this file. None of it is anybody's real
day, nobody's household appears in it, and no distribution here was fitted to
any real data. It is furniture.

    MALLOW_FAKE_MODEL=1 python3 demo/seed.py [uid]
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "mobile"), str(ROOT / "spike" / "voice")]

# Invented. A week that is ordinary on purpose: mostly small preparation, a few
# things held in mind, one afternoon of work the world already counts.
WEEK: tuple[tuple[int, str, str, str, int | None], ...] = (
    (5, "labelled the new water bottle before it goes out", "school_community", "invisible_chore", 10),
    (5, "worked out which of the two swimming classes actually fits", "school_community", "mental_load", None),
    (4, "put the winter things back in the right box", "clothing_laundry", "invisible_chore", 25),
    (3, "kept the dentist reminder in mind all afternoon", "health_admin", "mental_load", None),
    (3, "bought more of the sunscreen before it ran out", "shopping_restocking", "invisible_chore", 15),
    (2, "chased the reply about the class list", "school_community", "mental_load", None),
    (1, "cooked dinner", "food_preparation", "recognised_work", 40),
    (1, "laid out what everyone needs in the morning", "household_admin", "invisible_chore", 12),
)


def main() -> int:
    if os.getenv("MALLOW_FAKE_MODEL") != "1":
        print("refusing: set MALLOW_FAKE_MODEL=1. This writes demo rows only.")
        return 2

    import server                                       # noqa: E402
    from policy import decide                           # noqa: E402
    from reflection import now_jst                      # noqa: E402

    uid = sys.argv[1] if len(sys.argv) > 1 else "demo-owner"
    ws = server.workspaces.for_uid(uid)
    now = now_jst()

    for days_ago, said, domain, kind, minutes in WEEK:
        d = decide(kind)
        rid = uuid.uuid4().hex[:16]
        ws.ledger[rid] = {
            "record_id": rid, "capture_id": "demo-seed",
            "recorded_at": (now - timedelta(days=days_ago,
                                            hours=len(said) % 7)).isoformat(timespec="seconds"),
            "occurred_at": None, "duration_minutes": minutes,
            "transcript": said, "activity_text": said, "source_text": said,
            "activity_domain": domain,
            "labour_kind": kind,
            "model_version": "demo-seed", "prompt_version": "demo-seed",
            "policy_result": d.outcome, "policy_version": d.policy_version,
            "review_status": "active", "supersedes": None,
        }

    print(f"seeded {len(WEEK)} demo records into workspace {uid!r} "
          f"({ws.path or 'in memory'})")
    print("now run:  ./run.sh leaf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
