#!/usr/bin/env python3
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from json_store import JsonNewsStore
from news_schedule import due_slot

BASE = Path(__file__).resolve().parent

def main():
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=60)).timestamp())
    store = JsonNewsStore(Path(os.environ.get("IRELAND_NEWS_JSON_DIR", BASE / "json_data")))
    latest = store.latest()
    due = due_slot(grace_minutes=0)
    print(json.dumps({
        "ok": True,
        "due_slug": due.strftime("%Y-%m-%d-%H%M"),
        "scheduled_at": due.isoformat(),
        "latest": {k: v for k, v in latest.items() if k != "items"} if latest else None,
        "events": store.recent_events(cutoff, 500)
    }, ensure_ascii=False, separators=(",", ":")))

if __name__ == "__main__":
    main()
