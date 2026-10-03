#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import gzip
import shutil
import sqlite3

BASE = Path(__file__).resolve().parent
DB = Path(__import__("os").environ.get("IRELAND_NEWS_DB", BASE / "news.db"))
BACKUPS = BASE / "backups"
DUBLIN = ZoneInfo("Europe/Dublin")
RETENTION_DAYS = 60

BACKUPS.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(DUBLIN).strftime("%Y%m%d-%H%M%S")
tmp = BACKUPS / f"news-{stamp}.db"
out = BACKUPS / f"news-{stamp}.db.gz"

src = sqlite3.connect(DB)
dst = sqlite3.connect(tmp)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()

with tmp.open("rb") as fin, gzip.open(out, "wb", compresslevel=6) as fout:
    shutil.copyfileobj(fin, fout)
tmp.unlink(missing_ok=True)

cutoff = datetime.now(DUBLIN) - timedelta(days=RETENTION_DAYS)
for path in BACKUPS.glob("news-*.db.gz"):
    if datetime.fromtimestamp(path.stat().st_mtime, DUBLIN) < cutoff:
        path.unlink(missing_ok=True)

print(out)
