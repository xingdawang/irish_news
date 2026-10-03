#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
TOKEN_FILE = Path(os.environ.get("IRELAND_NEWS_TOKEN_FILE", BASE / ".publish_token"))

def token():
    value = os.environ.get("IRELAND_NEWS_PUBLISH_TOKEN", "").strip()
    if value:
        return value
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

def main():
    parser = argparse.ArgumentParser(description="Publish one Ireland News edition through the authenticated API")
    parser.add_argument("json_file")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8200/api/news/publish")
    args = parser.parse_args()

    auth = token()
    if not auth:
        raise SystemExit("publish token is not configured")
    payload = Path(args.json_file).read_bytes()
    req = Request(
        args.endpoint,
        data=payload,
        method="POST",
        headers={"Authorization": "Bearer " + auth, "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            print(body)
            result = json.loads(body)
            if not result.get("ok"):
                raise SystemExit(1)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(body, file=sys.stderr)
        raise SystemExit(exc.code)
    except URLError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        raise SystemExit(2)

if __name__ == "__main__":
    main()
