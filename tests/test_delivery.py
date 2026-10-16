import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from json_store import JsonNewsStore
from news_schedule import due_slot, edition_status
import github_bridge_poller as bridge


def edition(slot=None, items=True):
    slot = slot or due_slot(grace_minutes=0)
    return {
        "version": 1, "slug": slot.strftime("%Y-%m-%d-%H%M"),
        "title": f"🇮🇪 爱尔兰新闻简报｜{slot.strftime('%Y-%m-%d %H:00')}｜住房 & IT 优先",
        "scheduled_at": slot.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff_at": datetime.now(timezone.utc).isoformat(),
        "items": [{
            "event_key": "test-event", "category": "housing", "region": "Dublin",
            "title": "测试新闻", "summary": "测试摘要", "source_name": "Test",
            "source_url": "https://example.com/news/test", "published_at": None,
            "is_update": False,
        }] if items else [],
    }


class ScheduleTests(unittest.TestCase):
    def test_missed_slot_is_stale_even_if_published_recently(self):
        now = datetime.fromisoformat("2026-10-05T12:45:00+01:00")
        latest = {"scheduled_at": "2026-10-05T06:00:00+01:00", "published_at": now.isoformat()}
        self.assertTrue(edition_status(latest, now)["missing_due_slot"])

    def test_grace_period_and_empty_store(self):
        now = datetime.fromisoformat("2026-10-05T12:15:00+01:00")
        latest = {"scheduled_at": "2026-10-05T06:00:00+01:00", "published_at": now.isoformat()}
        self.assertFalse(edition_status(latest, now)["stale"])
        self.assertTrue(edition_status(None, now)["stale"])

    def test_dst_and_previous_day(self):
        self.assertEqual(due_slot(datetime.fromisoformat("2026-03-29T05:35:00Z")).isoformat(), "2026-03-29T06:00:00+01:00")
        self.assertEqual(due_slot(datetime.fromisoformat("2026-10-25T06:35:00Z")).isoformat(), "2026-10-25T06:00:00+00:00")
        self.assertEqual(due_slot(datetime.fromisoformat("2026-10-05T00:05:00+01:00")).strftime("%Y-%m-%d-%H%M"), "2026-10-04-1800")


class PublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"IRELAND_NEWS_JSON_DIR": cls.initial.name}):
            import app
        cls.app = app

    @classmethod
    def tearDownClass(cls):
        cls.initial.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JsonNewsStore(Path(self.tmp.name))
        self.old_store = self.app.store
        self.app.store = self.store
        self.addCleanup(setattr, self.app, "store", self.old_store)
        self.token = "t" * 64

    def publish(self, payload, token=None):
        from starlette.requests import Request
        async def receive():
            return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
        request = Request({"type": "http", "method": "POST", "path": "/api/news/publish", "headers": [(b"authorization", ("Bearer " + (token or self.token)).encode())]}, receive)
        with patch.object(self.app, "get_publish_token", return_value=self.token):
            return asyncio.run(self.app.api_publish(request))

    def test_publish_replay_conflict_and_auth(self):
        from fastapi import HTTPException
        payload = edition()
        self.assertEqual(self.publish(payload)["status"], "created")
        first = self.store.latest()["published_at"]
        self.assertEqual(self.publish(payload)["status"], "exists")
        self.assertEqual(self.store.latest()["published_at"], first)
        changed = copy.deepcopy(payload)
        changed["items"][0]["summary"] = "不同的内容"
        with self.assertRaises(HTTPException) as exc:
            self.publish(changed)
        self.assertEqual(exc.exception.status_code, 409)
        with self.assertRaises(HTTPException) as exc:
            self.publish(payload, "wrong")
        self.assertEqual(exc.exception.status_code, 401)

    def test_cross_edition_duplicate_requires_real_update(self):
        from fastapi import HTTPException
        slot = due_slot(grace_minutes=0)
        self.publish(edition(slot - timedelta(hours=6)))
        payload = edition(slot)
        with self.assertRaises(HTTPException) as exc:
            self.publish(payload)
        self.assertEqual(exc.exception.status_code, 409)
        payload["items"][0].update(is_update=True, update_note="出现了新的决定")
        self.assertEqual(self.publish(payload)["status"], "created")

    def test_empty_edition_is_valid_and_fresh(self):
        self.assertEqual(self.publish(edition(items=False))["items"], 0)
        self.assertFalse(self.app.health()["stale"])

    def test_json_context_has_new_editions_without_sqlite(self):
        self.publish(edition())
        result = subprocess.run([sys.executable, "task_context.py"], cwd=Path(__file__).resolve().parents[1], env={**os.environ, "IRELAND_NEWS_JSON_DIR": self.tmp.name}, capture_output=True, text=True, check=True)
        context = json.loads(result.stdout)
        self.assertEqual(context["latest"]["slug"], self.store.latest()["slug"])
        self.assertEqual(context["events"][0]["event_key"], "test-event")
        self.assertIn("update_note", context["events"][0])
        self.assertFalse((Path(self.tmp.name) / "news.db").exists())


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = Path(self.tmp.name) / "token"
        self.token.write_text("t" * 64)
        self.state = Path(self.tmp.name) / "state.json"
        self.now = datetime.fromisoformat("2026-10-05T12:45:00+01:00")

    def run_bridge(self, source_slot, stale):
        payload = edition(datetime.fromisoformat(source_slot))
        result = {"ok": True, "status": "exists", "slug": payload["slug"]}
        health = {"stale": stale, "latest_slug": payload["slug"]}
        with patch.object(bridge, "TOKEN_FILE", self.token), patch.object(bridge, "STATE_FILE", self.state), patch.object(bridge, "load_payload", return_value=(payload, json.dumps(payload))), patch.object(bridge, "request_json", side_effect=[result, health]) as http:
            report = bridge.run(self.now)
            self.assertEqual(http.call_count, 2)
            return report

    def test_unchanged_old_feed_reports_failure(self):
        report = self.run_bridge("2026-10-04T18:00:00+01:00", True)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "stale-source")

    def test_direct_publish_can_supersede_old_bridge(self):
        report = self.run_bridge("2026-10-04T18:00:00+01:00", False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "superseded")
        self.assertTrue(report["source_stale"])

    def test_reconcile_even_with_cached_state(self):
        self.run_bridge("2026-10-05T12:00:00+01:00", False)
        report = self.run_bridge("2026-10-05T12:00:00+01:00", False)
        self.assertEqual(report["status"], "exists")

    def test_empty_feed_and_cache_bypass(self):
        payload = edition(items=False)
        with patch.object(bridge, "request_json", return_value=payload) as http:
            self.assertEqual(bridge.load_payload()[0]["items"], [])
            self.assertIn("_bridge_check=", http.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
