"""The publishing schedule always follows Dublin time, including DST."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DUBLIN = ZoneInfo("Europe/Dublin")


def due_slot(now=None, grace_minutes=30):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(DUBLIN) - timedelta(minutes=grace_minutes)
    return local.replace(hour=local.hour // 6 * 6, minute=0, second=0, microsecond=0)


def edition_status(latest, now=None, stale_hours=8, grace_minutes=30):
    now = now or datetime.now(timezone.utc)
    due = due_slot(now, grace_minutes)
    scheduled = published = None
    if latest:
        scheduled = datetime.fromisoformat(latest["scheduled_at"].replace("Z", "+00:00"))
        value = latest.get("published_at") or latest.get("generated_at")
        if value:
            published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    age = max(0, (now - published).total_seconds() / 3600) if published else None
    missing = scheduled is None or scheduled < due
    return {
        "due_slug": due.strftime("%Y-%m-%d-%H%M"),
        "latest_scheduled_at": latest.get("scheduled_at") if latest else None,
        "missing_due_slot": missing,
        "stale": missing or age is None or age > stale_hours,
        "age_hours": round(age, 2) if age is not None else None,
    }
