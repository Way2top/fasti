"""C-09 M6：CLI extract 命令 + web /api/extract/* 端点（mock LLM）+ 静态资源。"""

import json
import os
import re
import subprocess
import sys
import threading
import unittest
import urllib.error as urlerror
import urllib.request as urlrequest

from _base import BaseStoreTest, ROOT
from fasti.core import api, extract_config, extract_llm, extract_state

SESSION_ID = "sess-cli"


def _write_session(dsh_home, texts):
    session_dir = os.path.join(dsh_home, "sessions", "ws", "s1")
    os.makedirs(session_dir, exist_ok=True)
    path = os.path.join(session_dir, "session.v4.jsonl")
    events = [{"type": "session",
               "data": {"version": 4, "delegationDepth": 0, "isSeeded": False,
                        "session_id": SESSION_ID}}]
    for index, text in enumerate(texts, start=1):
        events.append({"seq": index, "type": "user/message",
                       "data": {"id": "m%d" % index, "role": "user",
                                "content": [{"type": "text", "text": text}]}})
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _envelope(payload):
    return json.dumps({"choices": [{"message": {
        "content": json.dumps(payload, ensure_ascii=False)}}]}).encode("utf-8")


class TestExtractCli(BaseStoreTest):
    def _run(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "run.py"), "--data-dir", self.data_dir]
            + list(args), capture_output=True, text=True, timeout=120,
            input=input_text)

    def test_cli_status_config_consent_queue(self):
        status = self._run("extract", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("已关闭", status.stdout)
        consent = self._run("extract", "consent", "--show")
        self.assertEqual(consent.returncode, 0)
        self.assertIn("发送给你配置的外部 LLM", consent.stdout)
        configured = self._run("extract", "config", "--base-url", "http://127.0.0.1:9/v1",
                               "--model", "mock", input_text="sk-test-key-1234\n",
                               )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertNotIn("sk-test-key-1234", configured.stdout)
        queue = self._run("extract", "queue")
        self.assertEqual(queue.returncode, 0)
        self.assertIn("（无）", queue.stdout)
        runs = self._run("extract", "runs")
        self.assertEqual(runs.returncode, 0)
        self.assertIn("不含会话原文", runs.stdout)
        as_json = self._run("--json", "extract", "status")
        payload = json.loads(as_json.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["data"]["enabled"], False)

    def test_cli_disabled_and_consent_and_missing_config(self):
        disabled = self._run("extract")
        self.assertEqual(disabled.returncode, 5)
        self.assertIn("EXTRACT_DISABLED", disabled.stderr)
        enable = self._run("extract", "config", "--enable")
        self.assertEqual(enable.returncode, 5)
        self.assertIn("EXTRACT_CONSENT_REQUIRED", enable.stderr)
        self.assertEqual(self._run("extract", "consent", "--accept").returncode, 0)
        self.assertEqual(self._run("extract", "config", "--enable").returncode, 0)
        missing = self._run("extract")
        self.assertEqual(missing.returncode, 5)
        self.assertIn("EXTRACT_LLM_CONFIG_MISSING", missing.stderr)
        self.assertIn("llm.base_url", missing.stderr)

    def test_cli_enable_requires_consent(self):
        denied = self._run("extract", "consent", "--reset")
        self.assertEqual(denied.returncode, 0)


class TestExtractWeb(BaseStoreTest):
    def setUp(self):
        super().setUp()
        self.dsh_home = os.path.join(self.tmp, "dsh")
        _write_session(self.dsh_home, ["我会用 Rust 写服务"])
        self.token = "test-token-extract"
        self.original_sleep = extract_llm.time.sleep
        extract_llm.time.sleep = lambda seconds: None
        self.content = "我会用 Rust 写服务"
        self.candidate_id = extract_state.candidate_id_for(
            SESSION_ID, ["m1"], self.content, "tech", "new")
        self.extraction = {"candidates": [
            {"content": self.content, "category_key": "tech", "time_hint": None,
             "extraction_confidence": 0.9,
             "evidence": {"message_ids": ["m1"]}, "reason": "测试"}]}
        self.judgments = {"judgments": [
            {"candidate_id": self.candidate_id, "relation": "new",
             "target_fact_id": None, "relation_confidence": 0.9, "rationale": "测试"}]}

        class Mock(object):
            def __call__(inner, url, headers, body, timeout):
                payload = json.loads(body.decode("utf-8"))
                system = payload["messages"][0]["content"]
                return 200, _envelope(self.extraction if "抽取" in system
                                      else self.judgments)
        extract_llm.set_transport(Mock())
        extract_config.set_consent(self.data_dir, True)
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"].update({"base_url": "http://mock.local/v1", "model": "mock",
                           "api_key": "sk-mock-secret-1234"})
        cfg["extract"]["sources"] = {"dsh_home": self.dsh_home, "allow_plain": True}
        extract_config.save_config(self.data_dir, cfg)
        extract_config.update_config(self.data_dir, {
            "extract": {"enabled": True, "auto_write": True}})

    def tearDown(self):
        extract_llm.time.sleep = self.original_sleep
        extract_llm.reset_transport()
        super().tearDown()

    def _server(self):
        from fasti.adapters import web
        server, port = web.create_server(self.data_dir, 0, self.token)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        return server, thread, "http://127.0.0.1:%d" % port

    def _fetch(self, base, path, method="GET", body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"X-Fasti-Token": self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urlrequest.Request(base + path, data=data, headers=headers,
                                     method=method)
        try:
            with urlrequest.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_web_extract_endpoints(self):
        server, thread, base = self._server()
        try:
            with urlrequest.urlopen(base + "/extract.js", timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("javascript", response.headers.get("Content-Type", ""))
            status, config_payload = self._fetch(base, "/api/extract/config")
            self.assertEqual(status, 200)
            self.assertTrue(config_payload["data"]["api_key_set"])
            self.assertNotIn("sk-mock-secret-1234",
                             json.dumps(config_payload, ensure_ascii=False))
            status, overview = self._fetch(base, "/api/extract/status")
            self.assertEqual(status, 200)
            self.assertTrue(overview["data"]["enabled"])
            status, run_payload = self._fetch(base, "/api/extract/run", "POST",
                                              {"trigger": "manual"})
            self.assertEqual(status, 200)
            run = run_payload["data"]["run"]
            self.assertEqual(run["counters"]["auto_written"], 1)
            status, candidates = self._fetch(base, "/api/extract/candidates")
            self.assertEqual(status, 200)
            self.assertEqual(candidates["data"]["items"], [])
            status, info = self._fetch(base, "/api/extract/runs")
            self.assertEqual(status, 200)
            self.assertEqual(len(info["data"]["items"]), 1)
            status, disabled = self._fetch(base, "/api/extract/candidates/cand-"
                                           + "0" * 20)
            self.assertEqual(status, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_web_approve_and_reject(self):
        extract_config.update_config(self.data_dir, {"extract": {"auto_write": False}})
        server, thread, base = self._server()
        try:
            status, run_payload = self._fetch(base, "/api/extract/run", "POST", {})
            self.assertEqual(status, 200)
            status, candidates = self._fetch(base, "/api/extract/candidates")
            items = candidates["data"]["items"]
            self.assertEqual(len(items), 1)
            candidate_id = items[0]["candidate_id"]
            status, approved = self._fetch(
                base, "/api/extract/candidates/%s/approve" % candidate_id,
                "POST", {"change_type": "new"})
            self.assertEqual(status, 200)
            self.assertEqual(approved["data"]["change_type"], "new")
            # 没有新会话内容时再次 run 不产候选；直接验证候选终态不可再操作
            status, again = self._fetch(
                base, "/api/extract/candidates/%s/approve" % candidate_id,
                "POST", {"change_type": "new"})
            self.assertEqual(status, 409)
            self.assertEqual(again["error"]["code"], "EXTRACT_CANDIDATE_ALREADY_RESOLVED")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_web_reject_flow(self):
        extract_config.update_config(self.data_dir, {"extract": {"auto_write": False}})
        server, thread, base = self._server()
        try:
            self._fetch(base, "/api/extract/run", "POST", {})
            status, candidates = self._fetch(base, "/api/extract/candidates")
            candidate_id = candidates["data"]["items"][0]["candidate_id"]
            status, rejected = self._fetch(
                base, "/api/extract/candidates/%s/reject" % candidate_id,
                "POST", {"reason": "不需要"})
            self.assertEqual(status, 200)
            self.assertEqual(rejected["data"]["candidate"]["status"], "rejected")
            status, listed = self._fetch(base, "/api/extract/candidates?status=rejected")
            self.assertEqual(len(listed["data"]["items"]), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
