#!/usr/bin/env python3
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/home/ubuntu/ireland-news")
DOC_ID = "1A4IGECJBv-LCdk5_ZqzUm80ZOauaYfSq4FIUeU3aM0Q"
BRIDGE_URL = f"https://docs.google.com/document/d/{DOC_ID}/export?format=txt"
PUBLISH_URL = "http://127.0.0.1:8200/api/news/publish"
TOKEN_FILE = BASE / ".publish_token"
STATE_FILE = BASE / ".drive_bridge_state.json"

def fetch_bridge():
    req = urllib.request.Request(
        BRIDGE_URL,
        headers={"User-Agent": "IrelandNewsBridge/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status != 200:
            raise RuntimeError(f"bridge HTTP {resp.status}")
        text = resp.read().decode("utf-8-sig").strip()
    if not text:
        raise RuntimeError("bridge document is empty")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError("bridge JSON must be an object")
    required = {"version", "slug", "title", "scheduled_at", "generated_at", "cutoff_at", "items"}
    missing = sorted(required - payload.keys())
    if missing:
        raise RuntimeError("bridge JSON missing: " + ", ".join(missing))
    if not isinstance(payload.get("items"), list):
        raise RuntimeError("bridge JSON items must be a list")
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return payload, canonical

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def save_state(obj):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".drive_bridge_state.", suffix=".tmp", dir=str(BASE))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        os.replace(tmp, STATE_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def publish(canonical):
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("publish token is empty")
    req = urllib.request.Request(
        PUBLISH_URL,
        data=canonical.encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
            "User-Agent": "IrelandNewsDriveBridge/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"publish HTTP {e.code}: {body[:1000]}") from e
    if status != 200:
        raise RuntimeError(f"publish HTTP {status}: {body[:1000]}")
    result = json.loads(body)
    if result.get("ok") is not True or result.get("status") not in {"created", "exists"}:
        raise RuntimeError(f"unexpected publish result: {body[:1000]}")
    return result

def main():
    payload, canonical = fetch_bridge()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    state = load_state()
    if state.get("sha256") == digest:
        print(json.dumps({"ok": True, "status": "unchanged", "slug": payload.get("slug")}, ensure_ascii=False))
        return 0
    result = publish(canonical)
    save_state({
        "sha256": digest,
        "slug": payload.get("slug"),
        "published_status": result.get("status"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })
    print(json.dumps({
        "ok": True,
        "status": result.get("status"),
        "slug": payload.get("slug"),
        "items": len(payload.get("items", [])),
    }, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
