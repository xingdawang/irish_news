#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import gzip
import os
import shutil
import sqlite3
import tarfile

BASE=Path(__file__).resolve().parent
DB=Path(os.environ.get("IRELAND_NEWS_DB",BASE/"news.db"))
JSON_DIR=Path(os.environ.get("IRELAND_NEWS_JSON_DIR",BASE/"json_data"))
BACKUPS=BASE/"backups"
DUBLIN=ZoneInfo("Europe/Dublin")
RETENTION_DAYS=60

BACKUPS.mkdir(parents=True,exist_ok=True)
stamp=datetime.now(DUBLIN).strftime("%Y%m%d-%H%M%S")
created=[]

if DB.exists():
    tmp=BACKUPS/f"news-{stamp}.db"
    out=BACKUPS/f"news-{stamp}.db.gz"
    src=sqlite3.connect(DB); dst=sqlite3.connect(tmp)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    with tmp.open("rb") as fin, gzip.open(out,"wb",compresslevel=6) as fout:
        shutil.copyfileobj(fin,fout)
    tmp.unlink(missing_ok=True)
    created.append(out)

if JSON_DIR.exists():
    out=BACKUPS/f"json-news-{stamp}.tar.gz"
    with tarfile.open(out,"w:gz") as tar:
        tar.add(JSON_DIR,arcname="json_data")
    created.append(out)

cutoff=datetime.now(DUBLIN)-timedelta(days=RETENTION_DAYS)
for pattern in ("news-*.db.gz","json-news-*.tar.gz"):
    for path in BACKUPS.glob(pattern):
        if datetime.fromtimestamp(path.stat().st_mtime,DUBLIN)<cutoff:
            path.unlink(missing_ok=True)

for path in created: print(path)
