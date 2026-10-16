#!/usr/bin/env python3
"""Optional GitHub input bridge. An unchanged feed is not proof of freshness."""
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from news_schedule import due_slot

BASE = Path(__file__).resolve().parent
BRIDGE_URL = os.environ.get("IRELAND_NEWS_BRIDGE_URL", "https://raw.githubusercontent.com/xingdawang/time/master/ireland_news_bridge.json")
PUBLISH_URL = os.environ.get("IRELAND_NEWS_LOCAL_PUBLISH_URL", "http://127.0.0.1:8200/api/news/publish")
TOKEN_FILE = Path(os.environ.get("IRELAND_NEWS_TOKEN_FILE", BASE / ".publish_token"))
STATE_FILE = BASE / ".github_bridge_state.json"
REQUIRED = {"version", "slug", "title", "scheduled_at", "generated_at", "cutoff_at", "items"}


def request_json(url, data=None, token=None):
    headers = {"User-Agent": "IrelandNewsGitHubBridge/2.0", "Cache-Control": "no-cache"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = Request(url, data=data, headers=headers)
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read(1048577)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read(1000).decode('utf-8', errors='replace')}") from exc
    if len(raw) > 1048576:
        raise ValueError("JSON exceeds 1 MiB")
    return json.loads(raw.decode("utf-8-sig"))


def load_payload():
    url = urlsplit(BRIDGE_URL)
    query = parse_qsl(url.query, keep_blank_values=True) + [("_bridge_check", str(time.time_ns()))]
    payload = request_json(urlunsplit(url._replace(query=urlencode(query))))
    if not isinstance(payload, dict):
        raise ValueError("bridge JSON must be an object")
    if set(payload) != REQUIRED or payload.get("version") != 1:
        raise ValueError("bridge JSON must contain exactly the version 1 edition fields")
    if not isinstance(payload["items"], list):
        raise ValueError("bridge items must be a list (empty editions are allowed)")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload, canonical


def save_state(state):
    fd, tmp = tempfile.mkstemp(prefix=".github_bridge_state.", dir=STATE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, STATE_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run(now=None):
    now = now or datetime.now(timezone.utc)
    payload, canonical = load_payload()
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("publish token is empty")
    # Always reconcile with the API: cached state must not hide a lost edition.
    result = request_json(PUBLISH_URL, canonical.encode("utf-8"), token)
    if result.get("ok") is not True or result.get("status") not in {"created", "exists"}:
        raise RuntimeError("unexpected publish response")
    health_url = PUBLISH_URL.removesuffix("/api/news/publish") + "/health"
    health = request_json(health_url)
    scheduled = datetime.fromisoformat(payload["scheduled_at"].replace("Z", "+00:00"))
    if scheduled.tzinfo is None:
        raise ValueError("scheduled_at must have a timezone")
    stale_source = scheduled < due_slot(now)
    report = {
        "ok": not health["stale"],
        "status": "stale-source" if stale_source and health["stale"] else ("superseded" if stale_source else result["status"]),
        "slug": payload["slug"],
        "source": "github",
        "source_stale": stale_source,
        "latest_slug": health.get("latest_slug"),
        "checked_at": now.isoformat(),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
    save_state(report)
    return report


if __name__ == "__main__":
    try:
        report = run()
        print(json.dumps(report, ensure_ascii=False))
        raise SystemExit(0 if report["ok"] else 1)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
