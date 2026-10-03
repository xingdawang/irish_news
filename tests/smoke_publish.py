#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("IRELAND_NEWS_TEST_URL", "http://127.0.0.1:8200")
TOKEN = os.environ["IRELAND_NEWS_PUBLISH_TOKEN"]

def request(method, path, body=None, token=TOKEN):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())

if __name__ == "__main__":
    status, body = request("GET", "/health")
    assert status == 200 and body["ok"] is True
    print("health OK")
