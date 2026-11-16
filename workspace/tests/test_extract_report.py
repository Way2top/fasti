"""C-11：summary 同项目合并 / 覆盖统计 / 运行报告 CLI+Web / 旧 run 兼容。"""

import json
import os
import threading
import unittest
import urllib.request

from _base import BaseStoreTest
from fasti.core import api, extract_coverage, extract_state
from fasti.core.services import extract_sessionops as ops


def _summary(subject, category="tech", confidence=0.9, ordinal=1, content=None):
    return {"content": content or ("内容：" + subject), "category_key": category,
            "granularity": "summary", "subject": subject,
            "extraction_confidence": confidence,
            "message_ids": ["m%d" % ordinal], "message_ordinals": [ordinal],
            "chunk_id": "chunk-%d" % ordinal, "run_id": "run-1",
            "candidate_id": "cand-%02d" % ordinal}


class TestSummaryMerge(unittest.TestCase):
    def test_ac16_23_four_summaries_and_fitness(self):
        items = [_summary("Fasti 项目", ordinal=1),
                 _summary("个人记忆系统 Fasti", ordinal=2, confidence=0.95),
                 _summary("Fasti 个人记忆系统开发", ordinal=3),
                 _summary("AI memory 方向的开源项目（PMS）", ordinal=4),
                 _summary("健身计划", category="health", ordinal=5)]
        kept, stats = ops.postprocess_session(items)
        contents = [item["subject"] for item in kept]
        self.assertEqual(len(kept), 3)                      # 1 合并 + PMS + 健身
        self.assertIn("个人记忆系统 Fasti", contents)        # 置信度最高保留
        self.assertEqual(len([k for k in kept if k["subject"] == "健身计划"]), 1)
        merged = [item for item in kept
                  if (item.get("merge") or {}).get("auto")]
        self.assertTrue(merged)
        self.assertIn("AI memory 方向的开源项目（PMS）", contents)  # 无 alias 不自动合
        self.assertTrue(merged[0]["merge"]["merged_from"])
        self.assertTrue(stats["summary_merge"]["suggestion_groups"])

    def test_no_cross_category_or_atomic(self):
        items = [_summary("Fasti", category="tech", ordinal=1),
                 _summary("Fasti", category="projects", ordinal=2)]
        items.append({"content": "atomic", "category_key": "tech",
                      "granularity": "atomic", "subject": None,
                      "extraction_confidence": 0.9, "message_ids": ["m3"],
                      "message_ordinals": [3], "chunk_id": "c3",
                      "candidate_id": "cand-03"})
        kept, _stats = ops.postprocess_session(items)
        self.assertEqual(len(kept), 3)


class TestCoverage(BaseStoreTest):
    def _sessions(self, count):
        dsh = os.path.join(self.tmp, "dsh")
        for index in range(count):
            path = os.path.join(dsh, "sessions", "ws", "s%02d" % index,
                                "session.v4.jsonl")
            os.makedirs(os.path.dirname(path))
            events = [
                {"type": "session", "data": {"version": 4, "delegationDepth": 0,
                                             "isSeeded": False,
                                             "session_id": "s%02d" % index}},
                {"seq": 1, "type": "user/message",
                 "data": {"id": "m1", "role": "user", "source": {"kind": "user"},
                          "content": [{"type": "text", "text": "消息 %d" % index}]}},
            ]
            with open(path, "w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event) + "\n")
        return dsh

    def test_38_sessions_limit10_unscanned(self):
        dsh = self._sessions(38)
        coverage = extract_coverage.compute_coverage(
            dsh, True, {"sessions": {}}, 10)
        self.assertEqual(coverage["total_candidate_sessions"], 38)
        self.assertEqual(coverage["unscanned_sessions"], 38)
        self.assertEqual(coverage["estimated_runs_remaining"], 4)
        marks = {"sessions": {"s%02d" % i: {"last_seq": 1} for i in range(10)}}
        coverage = extract_coverage.compute_coverage(dsh, True, marks, 10)
        self.assertEqual(coverage["unscanned_sessions"], 28)
        self.assertEqual(coverage["estimated_runs_remaining"], 3)
        marks = {"sessions": {"s%02d" % i: {"last_seq": 1} for i in range(38)}}
        coverage = extract_coverage.compute_coverage(dsh, True, marks, 10)
        self.assertEqual(coverage["unscanned_sessions"], 0)
        self.assertEqual(coverage["estimated_runs_remaining"], 0)


class TestRunReport(BaseStoreTest):
    def _seed(self):
        run = {
            "run_id": "018bcfe5-6800-7a5a-9a5a-5a5a5a5a5a5a", "schema_version": 3,
            "started_at": "2026-11-16T10:00:00+08:00",
            "finished_at": "2026-11-16T10:01:00+08:00", "status": "ok",
            "trigger": "manual", "legacy": False,
            "counters": {"sessions_processed": 1, "messages_processed": 2,
                         "candidates_created": 1, "auto_written": 0,
                         "queued": 1, "errors": 0},
            "coverage": {"total_candidate_sessions": 38, "sessions_attempted": 10,
                         "sessions_scanned_this_run": 10, "unscanned_sessions": 28,
                         "estimated_runs_remaining": 3,
                         "unreadable_sessions": 0, "no_new_messages_sessions": 0},
            "summary_merge": {"before": 2, "after": 1, "auto_merged": 1,
                              "groups": [], "suggestion_groups": []},
            "sessions": [{"session_id": "s1",
                          "title": {"text": "技术讨论", "source": "provider",
                                    "truncated": False},
                          "status": "processed", "decision": "extract",
                          "reason": "含个人信息", "user_message_count": 2,
                          "messages": 2,
                          "candidates": {"created": 1, "auto_written": 0,
                                         "queued": 1, "dropped_by_session_cap": 0,
                                         "dropped_duplicate_summary": 0,
                                         "merged_summaries": 1}}],
            "recall": {}, "errors": [], "llm": {},
        }
        extract_state.append_run(self.data_dir, run)
        candidate = extract_state.new_candidate(
            "cand-" + "a" * 20, "s1", ["m1"], [1], "chunk", "run-1", "候选", "tech",
            "new", 0.9,
            {"mode": "full", "corpus_bytes": 0, "current_count": 0,
             "historical_count": 0, "recalled_fact_ids": [], "historical_fact_ids": [],
             "reasons": {}, "truncated": False},
            {"action": "review", "reason_code": "auto_write_disabled"},
            extract_state.evidence_key_for("s1", ["m1"], "候选"),
            granularity="summary", subject="Fasti")
        candidate["run_id"] = run["run_id"]
        extract_state.append_candidate(self.data_dir, candidate)
        return run["run_id"], candidate["candidate_id"]

    def test_cli_list_and_detail(self):
        from fasti.adapters import cli_cmds_extract
        from unittest import mock
        run_id, _cid = self._seed()
        args = mock.Mock(run_id=None, limit=20)
        self.assertEqual(cli_cmds_extract._cmd_extract_runs(args, self.data_dir), 0)
        args = mock.Mock(run_id=run_id[:8])
        self.assertEqual(cli_cmds_extract._cmd_extract_runs(args, self.data_dir), 0)

    def test_run_id_errors(self):
        self._seed()
        from fasti.core import errors
        with self.assertRaises(errors.CoreError) as ctx:
            api.get_extract_run(self.data_dir, "deadbeef")
        self.assertEqual(ctx.exception.code, "EXTRACT_RUN_NOT_FOUND")
        with self.assertRaises(errors.CoreError) as ctx:
            api.get_extract_run(self.data_dir, "bad id!")
        self.assertEqual(ctx.exception.code, "VALIDATION_INVALID_ID")

    def test_legacy_run(self):
        extract_state.append_run(self.data_dir, {
            "run_id": "018bcfe5-7000-7a5a-9a5a-5a5a5a5a5a5a", "status": "ok"})
        run = api.list_extract_runs(self.data_dir)["items"][0]
        self.assertTrue(run["legacy"])
        self.assertIsNone(run["coverage"])
        detail = api.get_extract_run(
            self.data_dir, "018bcfe5-7000-7a5a-9a5a-5a5a5a5a5a5a")["run"]
        self.assertTrue(detail["legacy"])

    def test_candidate_run_filter(self):
        run_id, _cid = self._seed()
        self.assertEqual(len(api.list_extract_candidates(
            self.data_dir, "all", run_id=run_id)["items"]), 1)
        self.assertEqual(len(api.list_extract_candidates(
            self.data_dir, "all", run_id="other")["items"]), 0)

    def test_web_runs_and_filter(self):
        from fasti.adapters import web
        run_id, _cid = self._seed()
        server, port = web.create_server(self.data_dir, 0, "tok")
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            def get(path):
                request = urllib.request.Request(
                    "http://127.0.0.1:%d%s%st=tok" % (port, path, "?" if "?" not in path else "&"),
                    headers={"X-Fasti-Token": "tok"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    return json.loads(response.read().decode())["data"]
            runs = get("/api/extract/runs?limit=10")
            self.assertEqual(runs["items"][0]["run_id"], run_id)
            detail = get("/api/extract/runs/%s" % run_id[:8])
            self.assertEqual(detail["candidates"]["counts_by_status"]["pending"], 1)
            filtered = get("/api/extract/candidates?status=all&run_id=%s" % run_id)
            self.assertEqual(len(filtered["items"]), 1)
        finally:
            server.shutdown(); server.server_close()
