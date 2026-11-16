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

    def test_detail_filters_no_new_and_shows_candidate_breakdown(self):
        run_id = "018bcfe5-8000-7a5a-9a5a-5a5a5a5a5a5a"
        extract_state.append_run(self.data_dir, {
            "run_id": run_id, "schema_version": 3, "status": "ok",
            "started_at": "2026-11-16T10:00:00+08:00",
            "finished_at": "2026-11-16T10:01:00+08:00", "trigger": "manual",
            "legacy": False, "counters": {}, "coverage": {},
            "summary_merge": {"before": 0, "after": 0, "auto_merged": 0,
                              "groups": [], "suggestion_groups": []},
            "sessions": [
                {"session_id": "skip1", "status": "no-new-messages"},
                {"session_id": "skip2", "status": "no-new-messages"},
                {"session_id": "skip3", "status": "diversion_skip",
                 "title": {"text": "排障会话", "source": "provider",
                           "truncated": False},
                 "decision": "skip", "reason": "纯技术排障",
                 "user_message_count": 3, "messages": 3,
                 "candidates": {"extracted": 0, "kept": 0, "created": 0}},
                {"session_id": "work", "status": "processed",
                 "title": {"text": "真实标题", "source": "provider",
                           "truncated": False},
                 "decision": "extract", "reason": "项目进展",
                 "user_message_count": 1,
                 "candidates": {"extracted": 2, "kept": 1, "created": 1,
                                "duplicates": 1,
                                "dropped_by_session_cap": 1,
                                "dropped_duplicate_summary": 0,
                                "merged_summaries": 0}}],
            "recall": {}, "errors": [], "llm": {}})
        from fasti.adapters import cli_cmds_extract
        from unittest import mock
        result = cli_cmds_extract._cmd_extract_runs(
            mock.Mock(run_id=run_id[:8]), self.data_dir)
        self.assertEqual(result, 0)
        import contextlib, io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli_cmds_extract._cmd_extract_runs(mock.Mock(run_id=run_id[:8]),
                                              self.data_dir)
        out = buffer.getvalue()
        self.assertNotIn("skip1", out)
        self.assertNotIn("不可用（unavailable）| - | -", out)
        self.assertIn("另有 2 个会话已处理过、本次无新消息", out)
        self.assertIn("抽取 2 → 保留 1", out)
        self.assertIn("上限丢弃 1", out)
        self.assertIn("重复 1", out)
        self.assertIn("排障会话", out)
        self.assertIn("消息 3", out)

    def test_list_includes_entered_extraction(self):
        extract_state.append_run(self.data_dir, {
            "run_id": "018bcfe5-9000-7a5a-9a5a-5a5a5a5a5a5a",
            "schema_version": 3, "status": "ok", "started_at": "2026-11-16T10:00:00+08:00",
            "counters": {"sessions_processed": 3, "sessions_entered_extraction": 2,
                         "messages_processed": 5, "candidates_created": 1,
                         "auto_written": 0, "queued": 1, "errors": 0},
            "coverage": {"unscanned_sessions": 18, "estimated_runs_remaining": 2},
            "sessions": [], "summary_merge": {}, "legacy": False})
        from fasti.adapters import cli_cmds_extract
        from unittest import mock
        import contextlib, io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli_cmds_extract._cmd_extract_runs(mock.Mock(run_id=None, limit=20),
                                              self.data_dir)
        out = buffer.getvalue()
        self.assertIn("进入抽取", out)
        self.assertIn("| 2 |", out)

    def test_limit_counts_only_candidate_sessions_and_reaches_zero(self):
        import json as _json
        dsh = os.path.join(self.tmp, "dsh")
        def write(session_id, messages, depth=0):
            path = os.path.join(dsh, "sessions", "ws", session_id,
                                "session.v4.jsonl")
            os.makedirs(os.path.dirname(path))
            events = [{"type": "session", "data": {"version": 4,
                                                   "delegationDepth": depth,
                                                   "isSeeded": False,
                                                   "session_id": session_id}}]
            for index, text in enumerate(messages, start=1):
                events.append({"seq": index, "type": "user/message",
                               "data": {"id": "m%d" % index, "role": "user",
                                        "source": {"kind": "user"},
                                        "content": [{"type": "text", "text": text}]}})
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("".join(_json.dumps(event) + "\n" for event in events))
        for index in range(20):
            write("s%02d" % index, ["消息 %d" % index])
        for index in range(15):
            write("sub%02d" % index, ["子 agent"], depth=1)
        for index in range(3):
            write("empty%02d" % index, [])
        from fasti.core import extract_config, extract_llm, constants
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"].update({"base_url": "http://mock.local/v1", "model": "mock",
                           "api_key": "sk-mock"})
        cfg["extract"].update({"enabled": True, "auto_write": False,
                               "max_sessions_per_run": 10,
                               "sources": {"dsh_home": dsh, "allow_plain": True}})
        extract_config.save_config(self.data_dir, cfg)
        extract_config.set_consent(self.data_dir, True)
        state = {"n": 0}
        def transport(url, headers, body, timeout):
            state["n"] += 1
            payload = json.loads(body.decode("utf-8"))
            system = payload["messages"][0]["content"]
            if "分流" in system:
                return 200, json.dumps({"choices": [{"message": {"content":
                    json.dumps({"decision": "skip", "reason": "测试跳过"})}}]}).encode()
            raise AssertionError("limit 测试不应进入抽取")
        extract_llm.set_transport(transport)
        first = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(first["counters"]["sessions_attempted"], 10)
        self.assertEqual(first["coverage"]["unscanned_sessions"], 10)
        self.assertEqual(first["coverage"]["estimated_runs_remaining"], 1)
        second = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(second["counters"]["sessions_attempted"], 10)
        self.assertEqual(second["coverage"]["unscanned_sessions"], 0)
        self.assertEqual(second["coverage"]["estimated_runs_remaining"], 0)
        extract_llm.reset_transport()

    def test_web_summary_merge_rendering_logic(self):
        import re, subprocess, sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "fasti", "adapters", "web_static",
                               "extract.js"), encoding="utf-8") as handle:
            script = handle.read()
        match = re.search(r"  function summaryMergeLine\(run\) \{.*?\n  \}",
                          script, re.S)
        self.assertIsNotNone(match)
        code = (match.group(0) +
                "\nconsole.log(JSON.stringify([summaryMergeLine("
                '{"summary_merge":{"before":5,"after":3,"auto_merged":2,'
                '"suggestion_groups":[{"x":1}]}}), summaryMergeLine('
                '{"summary_merge":null}), summaryMergeLine({}), '
                'summaryMergeLine({"summary_merge":{"before":0,"after":0,'
                '"auto_merged":0,"suggestion_groups":[]}})]));')
        output = subprocess.run(["node", "-e", code], capture_output=True,
                                text=True, check=True).stdout
        values = json.loads(output)
        self.assertEqual(values[0], "summary 合并：5 → 3（自动合并 2，提示 1 组）")
        self.assertIsNone(values[1])                    # 旧 run 不显示
        self.assertIsNone(values[2])
        self.assertEqual(values[3], "summary 合并：0 → 0（自动合并 0，提示 0 组）")

    def test_run_detail_api_exposes_summary_merge(self):
        run_id, _cid = self._seed()
        run = api.get_extract_run(self.data_dir, run_id)["run"]
        self.assertEqual(run["summary_merge"]["before"], 2)
        extract_state.append_run(self.data_dir, {
            "run_id": "018bcfe5-a000-7a5a-9a5a-5a5a5a5a5a5a", "status": "ok",
            "schema_version": 2, "sessions": []})
        old = api.get_extract_run(
            self.data_dir, "018bcfe5-a000-7a5a-9a5a-5a5a5a5a5a5a")["run"]
        self.assertIsNone(old.get("summary_merge"))

    def test_cli_failed_session_summary(self):
        extract_state.append_run(self.data_dir, {
            "run_id": "018bcfe5-b000-7a5a-9a5a-5a5a5a5a5a5a", "schema_version": 3,
            "status": "partial", "started_at": "2026-11-16T10:00:00+08:00",
            "counters": {}, "coverage": {}, "summary_merge": {},
            "sessions": [{"session_id": "s1",
                          "title": {"text": "失败会话", "source": "provider"},
                          "status": "failed", "decision": "extract",
                          "reason": "抽取/判定失败，下次重试",
                          "error_code": "EXTRACT_LLM_INVALID_RESPONSE",
                          "user_message_count": 2}], "errors": []})
        from fasti.adapters import cli_cmds_extract
        from unittest import mock
        import contextlib, io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli_cmds_extract._cmd_extract_runs(
                mock.Mock(run_id="018bcfe5-b000"), self.data_dir)
        self.assertIn("失败 1 个会话（抽取响应非法 JSON，下次重试）", buffer.getvalue())

    def test_cli_per_session_error_codes(self):
        extract_state.append_run(self.data_dir, {
            "run_id": "018bcfe5-c000-7a5a-9a5a-5a5a5a5a5a5a", "schema_version": 3,
            "status": "failed", "started_at": "2026-11-16T10:00:00+08:00",
            "counters": {"sessions_failed": 2}, "coverage": {}, "summary_merge": {},
            "sessions": [
                {"session_id": "a", "status": "failed", "decision": "extract",
                 "title": {"text": "会话A", "source": "provider"},
                 "error_code": "EXTRACT_LLM_TIMEOUT", "watermark_advanced": False,
                 "user_message_count": 1},
                {"session_id": "b", "status": "failed", "decision": "extract",
                 "title": {"text": "会话B", "source": "provider"},
                 "error_code": "EXTRACT_LLM_INVALID_RESPONSE",
                 "watermark_advanced": False, "user_message_count": 2}],
            "errors": []})
        from fasti.adapters import cli_cmds_extract
        from unittest import mock
        import contextlib, io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli_cmds_extract._cmd_extract_runs(
                mock.Mock(run_id="018bcfe5-c000"), self.data_dir)
        out = buffer.getvalue()
        self.assertIn("错误 EXTRACT_LLM_TIMEOUT（LLM 调用超时）", out)
        self.assertIn("错误 EXTRACT_LLM_INVALID_RESPONSE（抽取响应非法 JSON）", out)
        self.assertIn("失败 2 个会话（抽取响应非法 JSON、LLM 调用超时，下次重试）", out)

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
