#!/usr/bin/env python3
import argparse
import json
import sqlite3
from pathlib import Path
from json_store import JsonNewsStore, payload_digest

def epoch_for(value):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not value: return 0
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=ZoneInfo("Europe/Dublin"))
        return int(dt.timestamp())
    except Exception: return 0

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default="news.db")
    ap.add_argument("--out",default="json_data")
    args=ap.parse_args()
    db=Path(args.db); out=Path(args.out)
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row
    store=JsonNewsStore(out)
    editions=con.execute("select * from editions order by sort_ts,id").fetchall()
    count=0
    for ed in editions:
        rows=con.execute("select * from news_items where edition_id=? order by id",(ed["id"],)).fetchall()
        items=[]
        for r in rows:
            item={
                "event_key":r["event_key"],"category":r["category"],"region":r["region"],
                "title":r["title"],"summary":r["summary"],"source_name":r["source_name"],
                "source_url":r["source_url"],"published_at":r["published_at"],
                "is_update":bool(r["is_update"]) if "is_update" in r.keys() else False,
            }
            if "update_note" in r.keys() and r["update_note"]:
                item["update_note"]=r["update_note"]
            items.append(item)
        scheduled=ed["scheduled_at"] if "scheduled_at" in ed.keys() else None
        generated=ed["generated_at"]
        sort_ts=ed["sort_ts"] if "sort_ts" in ed.keys() and ed["sort_ts"] else epoch_for(scheduled or generated)
        record={
            "version":1,
            "slug":ed["slug"],
            "title":ed["title"],
            "scheduled_at":scheduled,
            "generated_at":generated,
            "cutoff_at":ed["cutoff_at"] if "cutoff_at" in ed.keys() else generated,
            "published_at":ed["published_at"] if "published_at" in ed.keys() and ed["published_at"] else generated,
            "item_count":len(items),
            "sort_ts":int(sort_ts or 0),
            "payload_hash":None,
            "items":items,
        }
        digest_input={k:record[k] for k in ("version","slug","title","scheduled_at","generated_at","cutoff_at","items")}
        digest_input["sort_ts"]=record["sort_ts"]
        record["payload_hash"]=payload_digest(digest_input)
        store.import_record(record); count+=1
        print("migrated",record["slug"],len(items))
    con.close()
    print(json.dumps({"ok":True,"editions":count,"output":str(out.resolve())}))

if __name__=="__main__":
    main()
