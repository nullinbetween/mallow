"""
Put one invented week into a Firestore workspace, so the scheduled task has
something to be quiet or not quiet about.

🔴 WHY THIS EXISTS AS A SEPARATE FILE

`demo/seed.py` cannot do this. It requires `MALLOW_FAKE_MODEL=1`, which makes
`server.py` set `SUFFIX = "-demo"`, which `FirestoreRegistry` refuses outright:

    FirestoreUnavailable: demo mode does not run against Firestore;
                          use the file store

That refusal is correct and stays. What it protects against is **an app serving
requests in demo mode while writing the production database** — a person using
what looks like the real product while a stand-in model answers them. This
script is not that. It starts no app, serves nothing, and imports no server
module. It writes rows and exits.

🔴 WHAT IT STILL IS: a write to a real database. So it refuses to run unless
three things are true at once, and it says what it wrote.

    GOOGLE_CLOUD_PROJECT set
    MALLOW_ALLOW_FIRESTORE_SEED=1
    MALLOW_DEMO_UID exactly matches --uid

🔴 EVERY SENTENCE BELOW IS INVENTED. It was written for this file. Nobody's
household appears in it, no distribution here was fitted to any real data, and
no duration was taken from anybody's records. It is furniture.

    GOOGLE_CLOUD_PROJECT=... MALLOW_ALLOW_FIRESTORE_SEED=1 \
        python3 demo/seed_firestore.py --uid <uid> [--dry-run]

Authorised by the Owner, 2026-08-24, for the hackathon deployment.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "spike" / "voice")]

JST = timezone(timedelta(hours=9))

USERS = "users"

# Invented. A week that is ordinary on purpose: mostly small preparation, a few
# things held in mind, one afternoon of work the world already counts.
#
# The shape matters more than the words: several records inside one explicit
# synthetic demo workspace. The saved daily cadence is already due, so the
# normal scheduled path (not this script) decides whether a leaf is written.
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


def build(now: datetime) -> list[dict]:
    """The rows, with the real policy deciding the food. No model participates."""
    from policy import decide                                     # noqa: E402

    rows: list[dict] = []
    for days_ago, said, domain, kind, minutes in WEEK:
        d = decide(kind)
        rid = uuid.uuid4().hex[:16]
        rows.append({
            "record_id": rid,
            "capture_id": "demo-seed",
            "recorded_at": (now - timedelta(days=days_ago,
                                            hours=len(said) % 7)).isoformat(timespec="seconds"),
            "occurred_at": None,
            "duration_minutes": minutes,
            "transcript": said,
            "activity_text": said,
            "source_text": said,
            "activity_domain": domain,
            "labour_kind": kind,
            "model_version": "demo-seed",
            "prompt_version": "demo-seed",
            "policy_result": d.outcome,
            "policy_version": d.policy_version,
            "review_status": "active",
            "supersedes": None,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uid", required=True,
                    help="the workspace to seed. No default, on purpose.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written and touch nothing")
    a = ap.parse_args()

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        print("refusing: GOOGLE_CLOUD_PROJECT is not set.")
        return 2
    if os.getenv("MALLOW_ALLOW_FIRESTORE_SEED") != "1" and not a.dry_run:
        print("refusing: set MALLOW_ALLOW_FIRESTORE_SEED=1.")
        print("This writes invented rows into a real database. Say so out loud.")
        return 2
    if not a.uid.strip():
        print("refusing: --uid is empty.")
        return 2
    expected_uid = os.getenv("MALLOW_DEMO_UID", "").strip()
    if not expected_uid or expected_uid != a.uid.strip():
        print("refusing: MALLOW_DEMO_UID must exactly match --uid.")
        print("This prevents synthetic rows from landing in a different workspace.")
        return 2

    now = datetime.now(JST)
    rows = build(now)
    base = f"{USERS}/{a.uid.strip()}/records"

    days = sorted({r["recorded_at"][:10] for r in rows})
    print(f"project   {project}")
    print(f"workspace {base}")
    print(f"rows      {len(rows)} across {len(days)} distinct days: {', '.join(days)}")
    print()
    for r in rows:
        mins = "-" if r["duration_minutes"] is None else f"{r['duration_minutes']}m"
        print(f"  {r['recorded_at'][:16]}  {r['policy_result']:<8} {mins:>4}  "
              f"{r['transcript']}")
    print()

    if a.dry_run:
        print("dry run: nothing was written.")
        return 0

    try:
        from google.cloud import firestore
    except ImportError:
        print("refusing: google-cloud-firestore is not installed.")
        print("  pip install google-cloud-firestore")
        return 2

    db = firestore.Client(project=project)
    col = db.collection(USERS).document(a.uid.strip()).collection("records")

    # 🔴 Seeding twice would double the week and could push a workspace past a
    # threshold for the wrong reason. Refuse rather than append silently.
    existing = list(col.limit(1).stream())
    if existing:
        print(f"refusing: {base} already has at least one record.")
        print("This tool has no force mode. Use a fresh anonymous demo workspace.")
        return 3

    # One atomic batch: all invented rows and the explicit demo marker land,
    # or none of them do. A half-seeded week would be misleading evidence.
    batch = db.batch()
    # Firestore permits subcollections below a parent document that does not
    # exist. Such a parent does not appear in collection queries/snapshots, so
    # the Scheduler would never discover this workspace without a real user
    # manifest. Keep it in the same batch as the synthetic records.
    user = db.collection(USERS).document(a.uid.strip())
    batch.set(user, {"kind": "mallow-workspace", "schema_version": 1})
    for r in rows:
        r["synthetic_demo"] = True
        batch.set(col.document(r["record_id"]), r)
    settings = user.collection("settings").document("reflection")
    batch.set(settings, {
        "cadence": "daily",
        "time_local": "23:00",
        "timezone": "Asia/Tokyo",
        "day_of_month": now.day,
        "period_start_at": (now - timedelta(days=7)).isoformat(timespec="seconds"),
        "next_reflection_at": (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "synthetic_demo_workspace": True,
    })
    batch.commit()
    for r in rows:
        print(f"  wrote {base}/{r['record_id']}")

    print()
    print(f"wrote {len(rows)} rows and a synthetic demo marker into {base}")
    print("the scheduled task will decide on its own whether that earns a leaf.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
