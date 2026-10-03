#!/usr/bin/env python3
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

BASE=Path(__file__).resolve().parent
INBOX=BASE/"inbox"
STATE=BASE/".bridge_state.json"
LOG=BASE/"backups"/"bridge.log"
URL="https://raw.githubusercontent.com/xingdawang/irish_news/main/bridge/latest.json"
SLUG_RE=re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")
MAX_BYTES=256*1024

def log(msg):
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open("a",encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())+" "+msg+"\n")

def main():
    lock=(BASE/".bridge.lock").open("w")
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    req=Request(URL+"?t="+str(int(time.time())),headers={"Cache-Control":"no-cache","User-Agent":"irish-news-bridge/1"})
    try:
        with urlopen(req,timeout=20) as r:
            raw=r.read(MAX_BYTES+1)
    except Exception as e:
        log("fetch_failed "+repr(e))
        return 2
    if len(raw)>MAX_BYTES:
        log("payload_too_large")
        return 3
    digest=hashlib.sha256(raw).hexdigest()
    if STATE.exists():
        try:
            s=json.loads(STATE.read_text(encoding="utf-8"))
            if s.get("sha256")==digest:
                return 0
        except Exception:
            pass
    try:
        payload=json.loads(raw.decode("utf-8"))
    except Exception as e:
        log("invalid_json "+repr(e)); return 4
    if not isinstance(payload,dict) or payload.get("noop") is True:
        STATE.write_text(json.dumps({"sha256":digest,"status":"noop"}),encoding="utf-8")
        return 0
    slug=payload.get("slug","")
    if not SLUG_RE.fullmatch(slug):
        log("invalid_slug "+repr(slug)); return 5
    INBOX.mkdir(mode=0o700,parents=True,exist_ok=True)
    target=INBOX/(slug+".json")
    tmp=INBOX/(slug+".tmp")
    tmp.write_bytes(raw); os.chmod(tmp,0o600); tmp.replace(target)
    p=subprocess.run([sys.executable,str(BASE/"task_publish.py"),slug],cwd=str(BASE),capture_output=True,text=True,timeout=60)
    if p.returncode!=0:
        log("publish_failed slug="+slug+" stderr="+(p.stderr or p.stdout).strip().replace("\n"," ")[:1200])
        return 6
    try:
        result=json.loads(p.stdout)
    except Exception:
        log("invalid_publisher_output slug="+slug); return 7
    if not result.get("ok") or result.get("status") not in {"created","exists"}:
        log("unexpected_publisher_output slug="+slug+" output="+p.stdout.strip()[:1200]); return 8
    STATE.write_text(json.dumps({"sha256":digest,"slug":slug,"status":result.get("status"),"processed_at":time.time()}),encoding="utf-8")
    log("published slug="+slug+" status="+str(result.get("status"))+" items="+str(result.get("items")))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
