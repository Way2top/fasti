"""C-08 B（拦截）端到端：AC-15-1/15-3/15-4/15-5/15-6/15-7 + 审计/导出 + CLI/HTTP 双路。"""

import json
import os
import re
import subprocess
import sys
import threading
import unittest
import urllib.error as urlerror
import urllib.request as urlrequest

from _base import BaseStoreTest, ROOT, _sha
from fasti.core import api, errors, validate

REAL_A = "最熟悉的还是Python，但是工作所需，现在需要主力使用C++了。"
REAL_B = (
    "最熟悉的语言是Python，但是目前工作需要使用C++。Django和Flask都有一定了解，但不深入。"
    "数据库了解MySql，Redis了解但未使用过。AI相关，使用Langchain开发过一个GaoKaoAgent。")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


class TestDuplicateConflictApi(BaseStoreTest):
    def setUp(self):
        super().setUp()
        created = api.add_fact(self.data_dir, "tech", "主力语言：C++", "2026-11")
        self.base_fact_id = created["fact"]["fact_id"]

    def _conflict(self, content, category="tech", token=None, valid_from="2026-12"):
        with self.assertRaises(errors.CoreError) as ctx:
            api.add_fact(self.data_dir, category, content, valid_from,
                         confirm_token=token)
        return ctx.exception

    def _events_sha(self):
        return _sha(os.path.join(self.data_dir, "events.jsonl"))

    def test_no_token_rejected_without_event(self):
        before = self._events_sha()
        count_before = len(api.list_facts(self.data_dir)["items"])
        exc = self._conflict("主力语言是C++")
        self.assertEqual(exc.code, "CONFLICT_SIMILAR_FACT")
        self.assertEqual(exc.exit_code, 5)
        self.assertIn("相似", exc.message)
        candidates = exc.details["candidates"]
        self.assertEqual(candidates[0]["fact_id"], self.base_fact_id)
        self.assertEqual(candidates[0]["similarity"], 0.851)
        self.assertIn("c++", candidates[0]["shared_entities"])
        self.assertRegex(exc.details["confirm_token"], TOKEN_RE)
        self.assertEqual(self._events_sha(), before)                    # 不追加事件
        self.assertEqual(len(api.list_facts(self.data_dir)["items"]), count_before)

    def test_confirm_success_audit_and_export(self):
        token = self._conflict("主力语言是C++").details["confirm_token"]
        created = api.add_fact(self.data_dir, "tech", "主力语言是C++", "2026-12",
                               confirm_token=token)
        fact = created["fact"]
        confirmation = fact["duplicate_confirmation"]
        self.assertTrue(confirmation["confirmed"])
        self.assertEqual(confirmation["candidates"], [self.base_fact_id])
        self.assertEqual(confirmation["max_similarity"], 0.851)
        self.assertRegex(confirmation["token_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(fact["valid_from"], "2026-12-01T00:00:00+08:00")
        # 事件落库
        with open(os.path.join(self.data_dir, "events.jsonl"), encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        event = [item for item in events if item["type"] == "fact_created"
                 and item["data"]["fact_id"] == fact["fact_id"]][0]
        self.assertEqual(event["data"]["duplicate_confirmation"], confirmation)
        # 历史审计行 + show JSON 字段
        history = api.history_text(api.get_history(self.data_dir))
        self.assertIn("↳ 重复确认：与 %s 相似（0.851）后新增" % api.short_id(self.base_fact_id),
                      history)
        shown = api.show_fact(self.data_dir, fact["fact_id"])
        self.assertEqual(shown["fact"]["duplicate_confirmation"], confirmation)
        # 导出 entities 可审计
        exported = api.export_bundle(self.data_dir)
        with open(os.path.join(exported["output_dir"], "data", "entities.jsonl"),
                  encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        fact_rows = [row for row in rows if row["type"] == "fact"
                     and row["fact_id"] == fact["fact_id"]]
        self.assertEqual(fact_rows[0]["duplicate_confirmation"], confirmation)

    def test_token_reuse_on_changed_content_rejected(self):
        token = self._conflict("主力语言是C++").details["confirm_token"]
        before = self._events_sha()
        exc = self._conflict("主力语言是Java", token=token)
        self.assertEqual(exc.code, "VALIDATION_INVALID_CONFIRM_TOKEN")
        self.assertEqual(exc.exit_code, 4)
        self.assertIn("重新确认", exc.message)
        self.assertEqual(self._events_sha(), before)

    def test_token_bound_to_category(self):
        # 两个类别各有相同候选 ⇒ 候选集不同（category 不同）会使令牌失效
        api.add_fact(self.data_dir, "goals", "主力语言：C++", "2026-11")
        token = self._conflict("主力语言是C++").details["confirm_token"]
        exc = self._conflict("主力语言是C++", category="goals", token=token)
        self.assertEqual(exc.code, "VALIDATION_INVALID_CONFIRM_TOKEN")
        # 用 goals 自己的令牌应成功
        goals_token = self._conflict("主力语言是C++", category="goals").details["confirm_token"]
        created = api.add_fact(self.data_dir, "goals", "主力语言是C++", "2026-12",
                               confirm_token=goals_token)
        self.assertTrue(created["fact"]["duplicate_confirmation"]["confirmed"])

    def test_real_duplicate_pair_tier2(self):
        """AC-15-1 对应真实用户重复对：Tier2 命中（sim 0.307 + 共享 python/c++）。"""
        api.add_fact(self.data_dir, "tech", REAL_A, "2026-11")
        exc = self._conflict(REAL_B)
        candidate = exc.details["candidates"][0]
        self.assertEqual(candidate["similarity"], 0.307)
        self.assertEqual(candidate["shared_entities"], ["c++", "python"])
        self.assertEqual(candidate["content"], REAL_A)
        token = exc.details["confirm_token"]
        created = api.add_fact(self.data_dir, "tech", REAL_B, "2026-12",
                               confirm_token=token)
        self.assertEqual(created["fact"]["duplicate_confirmation"]["max_similarity"], 0.307)

    def test_obvious_different_creates_silently(self):
        created = api.add_fact(self.data_dir, "tech", "数据库：了解 MySQL", "2026-12")
        self.assertIsNone(created["fact"]["duplicate_confirmation"])

    def test_scope_cross_category_and_history(self):
        # 跨类别：他类别相似不触发
        created = api.add_fact(self.data_dir, "goals", "主力语言是C++", "2026-11")
        self.assertIsNone(created["fact"]["duplicate_confirmation"])
        # 历史旧段：演进后旧段不再参与
        api.change_fact(self.data_dir, self.base_fact_id, "evolve",
                        "数据库：了解 MySQL", "2026-12")
        created = api.add_fact(self.data_dir, "tech", "主力语言：C++ 深入", "2026-12")
        self.assertIsNone(created["fact"]["duplicate_confirmation"])

    def test_change_never_conflicts(self):
        """AC-15-2：改成演进/纠正不触发相似确认（不形成确认循环）。"""
        created = api.add_fact(self.data_dir, "tech", "数据库：了解 MySQL", "2026-11")
        evolved = api.change_fact(self.data_dir, created["fact"]["fact_id"], "evolve",
                                  "主力语言是C++", "2026-12")   # 与既有 head 高度相似
        self.assertEqual(evolved["change_type"], "evolve")
        self.assertFalse(evolved["auto_bumped"])


class TestDuplicateEventSchema(unittest.TestCase):
    def _event(self, confirmation):
        return {
            "seq": 9, "at": "2026-11-10T00:00:00+08:00", "type": "fact_created",
            "data": {
                "fact_id": "0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b",
                "line_id": "0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b",
                "category_key": "tech", "content": "x",
                "valid_from": "2026-11-01T00:00:00+08:00",
                "source": "user_entry", "recorded_at": "2026-11-10T00:00:00+08:00",
                "duplicate_confirmation": confirmation,
            },
        }

    def test_valid_confirmation_passes(self):
        event = self._event({
            "confirmed": True,
            "candidates": ["0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b"],
            "max_similarity": 0.851,
            "token_sha256": "a" * 64,
        })
        self.assertEqual(validate.validate_event_record(event), [])

    def test_malformed_confirmation_rejected(self):
        bad_values = [
            {"confirmed": False, "candidates": ["0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b"],
             "max_similarity": 0.5, "token_sha256": "a" * 64},
            {"confirmed": True, "candidates": [], "max_similarity": 0.5,
             "token_sha256": "a" * 64},
            {"confirmed": True, "candidates": ["0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b"],
             "max_similarity": 1.5, "token_sha256": "a" * 64},
            {"confirmed": True, "candidates": ["0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b"],
             "max_similarity": 0.5, "token_sha256": "xyz"},
            {"confirmed": True, "candidates": ["0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b"],
             "max_similarity": 0.5, "token_sha256": "a" * 64, "extra": 1},
        ]
        for value in bad_values:
            self.assertTrue(validate.validate_event_record(self._event(value)), value)


class TestDuplicateConflictEntryPoints(BaseStoreTest):
    def setUp(self):
        super().setUp()
        self.base = api.add_fact(self.data_dir, "tech", "主力语言：C++", "2026-11")

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "run.py"), "--data-dir", self.data_dir]
            + list(args), capture_output=True, text=True, timeout=120)

    def test_cli_conflict_and_confirm(self):
        before = _sha(os.path.join(self.data_dir, "events.jsonl"))
        rejected = self._run("fact", "add", "--category", "tech",
                             "--content", "主力语言是C++", "--valid-from", "2026-12")
        self.assertEqual(rejected.returncode, 5)
        self.assertIn("CONFLICT_SIMILAR_FACT", rejected.stderr)
        self.assertIn("--confirm-duplicate", rejected.stderr)
        token = re.search(r"--confirm-duplicate ([A-Za-z0-9_-]{22})", rejected.stderr).group(1)
        self.assertEqual(_sha(os.path.join(self.data_dir, "events.jsonl")), before)

        confirmed = self._run("fact", "add", "--category", "tech",
                              "--content", "主力语言是C++", "--valid-from", "2026-12",
                              "--confirm-duplicate", token)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        payload = json.loads(self._run("--json", "fact", "list").stdout)
        items = payload["data"]["items"]
        self.assertTrue(any(item.get("duplicate_confirmation") for item in items))

        reused = self._run("fact", "add", "--category", "tech",
                           "--content", "主力语言是Java", "--valid-from", "2026-12",
                           "--confirm-duplicate", token)
        self.assertEqual(reused.returncode, 4)
        self.assertIn("VALIDATION_INVALID_CONFIRM_TOKEN", reused.stderr)

    def test_http_conflict_409_and_token_retry(self):
        from fasti.adapters import web
        server, port = web.create_server(self.data_dir, 0, "test-token-dup")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        base = "http://127.0.0.1:%d" % port

        def post(path, body):
            data = json.dumps(body).encode("utf-8")
            request = urlrequest.Request(base + path, data=data, method="POST",
                                         headers={"X-Fasti-Token": "test-token-dup",
                                                  "Content-Type": "application/json"})
            try:
                with urlrequest.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urlerror.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))
        try:
            status, payload = post("/api/facts", {
                "category": "tech", "content": "主力语言是C++", "valid_from": "2026-12"})
            self.assertEqual(status, 409)                                   # AC-15-1/15-4
            error = payload["error"]
            self.assertEqual(error["code"], "CONFLICT_SIMILAR_FACT")
            candidates = error["details"]["candidates"]
            self.assertEqual(candidates[0]["fact_id"], self.base["fact"]["fact_id"])
            token = error["details"]["confirm_token"]
            self.assertRegex(token, TOKEN_RE)

            status, created = post("/api/facts", {
                "category": "tech", "content": "主力语言是C++",
                "valid_from": "2026-12", "confirm_token": token})
            self.assertEqual(status, 200)                                   # AC-15-3/15-5
            self.assertTrue(created["data"]["fact"]["duplicate_confirmation"]["confirmed"])

            status, invalid = post("/api/facts", {
                "category": "tech", "content": "主力语言是Java",
                "valid_from": "2026-12", "confirm_token": token})
            self.assertEqual(status, 400)                                   # AC-15-5
            self.assertEqual(invalid["error"]["code"], "VALIDATION_INVALID_CONFIRM_TOKEN")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
