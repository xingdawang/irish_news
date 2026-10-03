import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS={"fbclid","gclid","mc_cid","mc_eid"}

def canonical_url(value:str)->str:
    if not value:
        return ""
    try:
        p=urlsplit(str(value).strip())
        if p.scheme.lower() not in {"http","https"} or not p.netloc:
            return ""
        query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
               if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
        path=p.path.rstrip("/") or "/"
        return urlunsplit((p.scheme.lower(),p.netloc.lower(),path,urlencode(sorted(query)),""))
    except Exception:
        return ""

def utc_now_iso()->str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")

def payload_digest(payload:dict)->str:
    body={k:v for k,v in payload.items() if k!="sort_ts"}
    return hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()

class JsonNewsStore:
    def __init__(self, root:Path):
        self.root=Path(root)
        self.editions_dir=self.root/"editions"
        self.index_file=self.root/"index.json"
        self.lock_file=self.root/".store.lock"
        self.editions_dir.mkdir(parents=True,exist_ok=True)
        if not self.index_file.exists():
            self._atomic_write(self.index_file,{"version":1,"editions":[]})

    def _atomic_write(self,path:Path,payload):
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=path.name+".",dir=path.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f:
                json.dump(payload,f,ensure_ascii=False,indent=2,sort_keys=False)
                f.write("\n"); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def _lock(self):
        self.lock_file.parent.mkdir(parents=True,exist_ok=True)
        f=self.lock_file.open("a+")
        fcntl.flock(f.fileno(),fcntl.LOCK_EX)
        return f

    def _load_index(self):
        try:
            data=json.loads(self.index_file.read_text(encoding="utf-8"))
            return data if isinstance(data,dict) else {"version":1,"editions":[]}
        except Exception:
            return {"version":1,"editions":[]}

    def _save_index(self,editions):
        editions=sorted(editions,key=lambda x:(int(x.get("sort_ts",0)),x.get("slug","")),reverse=True)
        self._atomic_write(self.index_file,{"version":1,"editions":editions[:1000]})

    def _public_payload(self,record):
        keys=("version","slug","title","scheduled_at","generated_at","cutoff_at","published_at","item_count","items")
        return {k:record.get(k) for k in keys}

    def list_editions(self,limit=1000):
        return self._load_index().get("editions",[])[:limit]

    def latest(self):
        editions=self.list_editions(1)
        return self.get(editions[0]["slug"]) if editions else None

    def get(self,slug):
        path=self.editions_dir/f"{slug}.json"
        if not path.exists(): return None
        try:
            data=json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data,dict) else None
        except Exception:
            return None

    def recent_events(self,cutoff_ts:int,limit=500):
        out=[]
        for ed in self.list_editions():
            if int(ed.get("sort_ts",0))<cutoff_ts: continue
            record=self.get(ed["slug"])
            if not record: continue
            for item in record.get("items",[]):
                out.append({
                    "event_key":item.get("event_key"),
                    "title":item.get("title"),
                    "source_name":item.get("source_name"),
                    "source_url":item.get("source_url"),
                    "is_update":bool(item.get("is_update",False)),
                    "slug":record.get("slug"),
                    "scheduled_at":record.get("scheduled_at"),
                })
                if len(out)>=limit: return out
        return out

    def publish(self,payload:dict):
        digest=payload_digest(payload)
        lock=self._lock()
        try:
            existing=self.get(payload["slug"])
            if existing:
                if existing.get("payload_hash")==digest:
                    return {"ok":True,"status":"exists","slug":payload["slug"],"items":existing.get("item_count",0),"published_at":existing.get("published_at")}
                raise ValueError("slug already exists with different content")

            current_sort=int(payload["sort_ts"])
            incoming_events={x["event_key"]:x for x in payload["items"]}
            incoming_urls={canonical_url(x["source_url"]):x for x in payload["items"]}
            required_update_keys={x["event_key"] for x in payload["items"] if x.get("is_update")}
            matched_older_updates=set()
            for meta in self.list_editions():
                record=self.get(meta["slug"])
                if not record: continue
                older=int(record.get("sort_ts",0))<current_sort
                for prior in record.get("items",[]):
                    ek=prior.get("event_key")
                    cu=canonical_url(prior.get("source_url",""))
                    if ek in incoming_events:
                        item=incoming_events[ek]
                        if item.get("is_update"):
                            if older: matched_older_updates.add(ek)
                        else:
                            raise ValueError(f"cross-edition duplicate requires is_update=true: {ek}")
                    if cu and cu in incoming_urls:
                        item=incoming_urls[cu]
                        if not item.get("is_update"):
                            raise ValueError(f"cross-edition duplicate requires is_update=true: {item['source_url']}")
            missing=sorted(required_update_keys-matched_older_updates)
            if missing:
                raise ValueError(f"is_update requires the same event_key in an older edition: {missing[0]}")

            published=utc_now_iso()
            record={
                "version":payload.get("version",1),
                "slug":payload["slug"],
                "title":payload["title"],
                "scheduled_at":payload["scheduled_at"],
                "generated_at":payload["generated_at"],
                "cutoff_at":payload["cutoff_at"],
                "published_at":published,
                "item_count":len(payload["items"]),
                "sort_ts":current_sort,
                "payload_hash":digest,
                "items":payload["items"],
            }
            self._atomic_write(self.editions_dir/f"{payload['slug']}.json",record)
            idx=[x for x in self.list_editions() if x.get("slug")!=payload["slug"]]
            idx.append({
                "slug":record["slug"],"title":record["title"],"scheduled_at":record["scheduled_at"],
                "generated_at":record["generated_at"],"published_at":record["published_at"],
                "item_count":record["item_count"],"sort_ts":record["sort_ts"],
            })
            self._save_index(idx)
            return {"ok":True,"status":"created","slug":record["slug"],"items":record["item_count"],"published_at":published}
        finally:
            fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
            lock.close()

    def import_record(self,record:dict):
        lock=self._lock()
        try:
            self._atomic_write(self.editions_dir/f"{record['slug']}.json",record)
            idx=[x for x in self.list_editions() if x.get("slug")!=record["slug"]]
            idx.append({
                "slug":record["slug"],"title":record["title"],"scheduled_at":record.get("scheduled_at"),
                "generated_at":record.get("generated_at"),"published_at":record.get("published_at"),
                "item_count":record.get("item_count",len(record.get("items",[]))),"sort_ts":int(record.get("sort_ts",0)),
            })
            self._save_index(idx)
        finally:
            fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
            lock.close()
