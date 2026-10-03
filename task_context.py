#!/usr/bin/env python3
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "news.db"

def main():
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=60)).timestamp())
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    rows = con.execute("""
        SELECT n.event_key,n.title,n.source_name,n.source_url,n.published_at,
               n.is_update,n.update_note,e.slug,e.scheduled_at,e.sort_ts
        FROM news_items n
        JOIN editions e ON e.id=n.edition_id
        WHERE e.sort_ts>=?
        ORDER BY e.sort_ts DESC,n.id DESC
        LIMIT 500
    """, (cutoff,)).fetchall()
    latest = con.execute("""
        SELECT slug,title,scheduled_at,generated_at,published_at,item_count
        FROM editions ORDER BY sort_ts DESC,id DESC LIMIT 1
    """).fetchone()
    con.close()
    print(json.dumps({
        "ok": True,
        "latest": dict(latest) if latest else None,
        "events": [dict(r) for r in rows]
    }, ensure_ascii=False, separators=(",", ":")))

if __name__ == "__main__":
    main()
