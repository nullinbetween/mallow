"""Per-workspace timing for Mallow's scheduled private reflections.

One global scheduler may call the application frequently.  This module decides
whether an individual workspace is due and calculates its next due boundary.
It contains no model call and cannot create a leaf.
"""
from __future__ import annotations

import calendar
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CADENCES = ("off", "daily", "weekly", "biweekly", "monthly")
DEFAULT_CADENCE = "weekly"
DEFAULT_TIME = "23:00"
DEFAULT_TIMEZONE = "Asia/Tokyo"
SENTENCE_TARGETS = {
    "daily": (1, 2),
    "weekly": (2, 4),
    "biweekly": (3, 5),
    "monthly": (4, 6),
}


def parse_stamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone is not recognised") from None


def valid_timezone(name: str) -> str:
    """The IANA name, or `ValueError`. Nothing unchecked reaches a preference."""
    text = str(name or "").strip()
    if not text:
        raise ValueError("timezone is required")
    _zone(text)
    return text


def _clock(value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour, minute)
    except (TypeError, ValueError):
        raise ValueError("time_local must be HH:MM") from None


def _next_month(local: datetime, wanted_day: int) -> datetime:
    year, month = local.year, local.month + 1
    if month == 13:
        year, month = year + 1, 1
    day = min(wanted_day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day)


def advance(preferences: dict, due: datetime, *, after: datetime) -> datetime:
    """Advance in local calendar time until the boundary is in the future."""
    cadence = preferences["cadence"]
    zone = _zone(preferences["timezone"])
    local = due.astimezone(zone)
    wanted_day = int(preferences.get("day_of_month") or local.day)
    while local <= after.astimezone(zone):
        if cadence == "daily":
            local += timedelta(days=1)
        elif cadence == "weekly":
            local += timedelta(days=7)
        elif cadence == "biweekly":
            local += timedelta(days=14)
        elif cadence == "monthly":
            local = _next_month(local, wanted_day)
        else:
            raise ValueError(f"cannot advance cadence {cadence!r}")
    return local


def make(cadence: str, time_local: str, timezone_name: str,
         *, now: datetime, weekday: int | None = None,
         day_of_month: int | None = None) -> dict:
    if cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}")
    clock = _clock(time_local)
    zone = _zone(timezone_name)
    local_now = now.astimezone(zone)
    weekday = local_now.weekday() if weekday is None else int(weekday)
    day_of_month = local_now.day if day_of_month is None else int(day_of_month)
    if weekday not in range(7):
        raise ValueError("weekday must be 0-6")
    if day_of_month not in range(1, 32):
        raise ValueError("day_of_month must be 1-31")
    base = datetime.combine(local_now.date(), clock, tzinfo=zone)
    if cadence == "off":
        next_due = None
        period_start = now
    else:
        if cadence in ("weekly", "biweekly"):
            base += timedelta(days=(weekday - local_now.weekday()) % 7)
        elif cadence == "monthly":
            base = base.replace(day=min(day_of_month,
                                        calendar.monthrange(base.year, base.month)[1]))
        template = {"cadence": cadence, "timezone": timezone_name,
                    "day_of_month": day_of_month}
        next_due = advance(template, base, after=now) if base <= now else base
        days = {"daily": 1, "weekly": 7, "biweekly": 14,
                "monthly": calendar.monthrange(local_now.year,
                                                 local_now.month)[1]}[cadence]
        period_start = next_due - timedelta(days=days)
    return {
        "cadence": cadence,
        "time_local": time_local,
        "timezone": timezone_name,
        "weekday": weekday,
        "day_of_month": day_of_month,
        "period_start_at": period_start.isoformat(timespec="seconds"),
        "next_reflection_at": (next_due.isoformat(timespec="seconds")
                               if next_due else None),
        "updated_at": now.isoformat(timespec="seconds"),
    }


def read(ws, *, now: datetime, persist_default: bool = False) -> dict:
    found = ws.preferences.read()
    if found:
        return found
    default = make(DEFAULT_CADENCE, DEFAULT_TIME, DEFAULT_TIMEZONE, now=now)
    if persist_default:
        ws.preferences.write(default)
    return default


def remember_display_timezone(ws, name: str, *, now: datetime) -> dict:
    """
    Record the zone this reader's device is in, without touching the schedule.

    A stored `recorded_at` is a full ISO instant and stays exactly as written;
    the only question this answers is which clock it is printed against. That
    used to be answered by `timezone`, which is written only when somebody
    opens the reflection settings and presses save — so a person who never
    opened that panel read their own day in Tokyo time.

    🔴 `timezone`, `period_start_at`, `next_reflection_at` and `last_checked_at`
    are left exactly as they were found. Those four are the scheduler's memory
    of what has already run, and a reflection that has run must not be able to
    run a second time because somebody opened the page in another country.

    The one case where the device's zone does reach the schedule is a workspace
    with no preference at all: there is nothing there to preserve, and the
    device's own zone is a better first guess than this module's default.
    """
    zone = valid_timezone(name)
    existing = ws.preferences.read()
    if existing:
        if existing.get("display_timezone") == zone:
            return existing                       # no write, no churn
        merged = {**existing, "display_timezone": zone}
    else:
        merged = {**make(DEFAULT_CADENCE, DEFAULT_TIME, zone, now=now),
                  "display_timezone": zone}
    ws.preferences.write(merged)
    return merged


def save(ws, cadence: str, time_local: str, timezone_name: str,
         *, now: datetime, weekday: int | None = None,
         day_of_month: int | None = None) -> dict:
    chosen = make(cadence, time_local, timezone_name, now=now,
                  weekday=weekday, day_of_month=day_of_month)
    ws.preferences.write(chosen)
    return chosen


def completed(preferences: dict, due: datetime, *, now: datetime) -> dict:
    next_due = advance(preferences, due, after=now)
    return {**preferences,
            "period_start_at": due.isoformat(timespec="seconds"),
            "next_reflection_at": next_due.isoformat(timespec="seconds"),
            "last_checked_at": now.isoformat(timespec="seconds")}
