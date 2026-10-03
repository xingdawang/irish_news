#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
INBOX = BASE / "inbox"
SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}$")
MAX_BYTES = 256 * 1024

def fail(message, code=1):
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    args = parser.parse_args()
    if not SLUG_RE.fullmatch(args.slug):
        fail("invalid slug")
    INBOX.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = INBOX / (args.slug + ".json")
    if not path.is_file():
        fail("inbox file not found", 2)
    if path.stat().st_size > MAX_BYTES:
        fail("payload exceeds 256 KiB", 2)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail("invalid JSON: " + str(exc), 2)
    if not isinstance(payload, dict) or payload.get("slug") != args.slug:
        fail("payload slug does not match filename", 2)
    result = subprocess.run(
        [sys.executable, str(BASE / "publish.py"), str(path)],
        cwd=str(BASE), capture_output=True, text=True
    )
    if result.returncode != 0:
        fail((result.stderr or result.stdout).strip(), 3)
    try:
        response = json.loads(result.stdout)
    except Exception:
        fail("publisher returned invalid JSON", 3)
    if not response.get("ok"):
        fail("publisher did not confirm success", 3)
    published = BASE / "published"
    published.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt = published / path.name
    path.replace(receipt)
    receipt.chmod(0o600)
    response["receipt"] = receipt.name
    print(json.dumps(response, ensure_ascii=False))

if __name__ == "__main__":
    main()
