"""C-09 M5：两段式运行时（mock LLM）+ 决策矩阵 + 自动写入 + 候选审核 + 水位/幂等。"""

import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import (api, clock, errors, export, extract_config, extract_llm,
                        extract_state, fold, store)

SESSION_ID = "sess-1"


def _write_session(dsh_home, messages, session="s1", session_id=SESSION_ID):
    session_dir = os.path.join(dsh_home, "sessions", "ws", session)
    os.makedirs(session_dir, exist_ok=True)
    path = os.path.join(session_dir, "session.v4.jsonl")
    events = [{"type": "session",
               "data": {"version": 4, "delegationDepth": 0, "isSeeded": False,
                        "session_id": session_id, "cwd": "/project/a"}}]
    for index, text in enumerate(messages, start=1):
        events.append({"seq": index, "type": "user/message",
                       "data": {"id": "m%d" % index, "role": "user",
                                "content": [{"type": "text", "text": text}]}})
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def _envelope(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


class MockLlm(object):
    """按 system prompt 区分 A（抽取）/B（判定）段，返回预设 JSON。"""

    def __init__(self, extraction, judgments):
        self.extraction = extraction
        self.judgments = judgments
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        payload = json.loads(body.decode("utf-8"))
        system = payload["messages"][0]["content"]
        self.requests.append(payload)
        if "抽取" in system:
            return 200, _envelope(json.dumps(self.extraction, ensure_ascii=False))
        if isinstance(self.judgments, Exception):
            raise self.judgments
        return 200, _envelope(json.dumps(self.judgments, ensure_ascii=False))


class ExtractRuntimeBase(BaseStoreTest):
    def setUp(self):
        super().setUp()
        self.dsh_home = os.path.join(self.tmp, "dsh")
        self.original_sleep = extract_llm.time.sleep
        extract_llm.time.sleep = lambda seconds: None

    def tearDown(self):
        extract_llm.time.sleep = self.original_sleep
        extract_llm.reset_transport()
        super().tearDown()

    def _enable(self, auto_write=True, sensitive_filter=True):
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"].update({"base_url": "http://mock.local/v1", "model": "mock",
                           "api_key": "sk-mock-secret-1234"})
        cfg["extract"]["sources"] = {"dsh_home": self.dsh_home, "allow_plain": True}
        cfg["extract"]["sensitive_filter"] = sensitive_filter
        extract_config.save_config(self.data_dir, cfg)
        extract_config.set_consent(self.data_dir, True)
        extract_config.update_config(self.data_dir, {
            "extract": {"enabled": True, "auto_write": auto_write,
                        "auto_write_per_run_limit": 10,
                        "auto_write_daily_limit": 20}})

    def _run(self, extraction, judgments, **kwargs):
        mock = MockLlm(extraction, judgments)
        extract_llm.set_transport(mock)
        run = api.extract_run(self.data_dir, trigger="manual", **kwargs)
        return run, mock

    def _candidate_payload(self, content, message_id="m1", confidence=0.9,
                           category="tech"):
        return {"content": content, "category_key": category,
                "time_hint": None, "extraction_confidence": confidence,
                "evidence": {"message_ids": [message_id]}, "reason": "测试"}

    def _judgment(self, candidate_id, relation="new", confidence=0.9, target=None):
        return {"candidate_id": candidate_id, "relation": relation,
                "target_fact_id": target, "relation_confidence": confidence,
                "rationale": "测试"}


class TestExtractRuntime(ExtractRuntimeBase):
    def test_auto_write_high_confidence(self):
        api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        _write_session(self.dsh_home, ["我会用 Rust 写服务"])
        self._enable(auto_write=True)
        content = "我会用 Rust 写服务"
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], content,
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload(content)], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id)], "notes": ""}
        run, mock = self._run(extraction, judgments)
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["counters"]["auto_written"], 1)
        self.assertEqual(run["counters"]["queued"], 0)
        self.assertEqual(run["recall"]["mode"], "full")
        facts = api.list_facts(self.data_dir)["items"]
        fact = [item for item in facts if item["content"] == content][0]
        self.assertEqual(fact["source"], "auto_extract")
        extraction_field = fact["extraction"]
        self.assertEqual(extraction_field["candidate_id"], candidate_id)
        self.assertEqual(extraction_field["mode"], "auto_write")
        self.assertEqual(extraction_field["llm_relation"], "new")
        self.assertFalse(extraction_field["reviewed_by_user"])
        self.assertIn(extraction_field["recall"]["mode"], ("full", "retrieval"))
        events = [json.loads(line) for line in
                  open(os.path.join(self.data_dir, "events.jsonl"), encoding="utf-8")
                  if line.strip()]
        event = [item for item in events if item["type"] == "fact_created"
                 and item["data"]["fact_id"] == fact["fact_id"]][0]
        self.assertEqual(event["data"]["source"], "auto_extract")
        self.assertEqual(event["data"]["extraction"]["candidate_id"], candidate_id)
        history = api.history_text(api.get_history(self.data_dir))
        self.assertIn("↳ 来源：自动提取", history)
        exported = api.export_bundle(self.data_dir)
        with open(os.path.join(exported["output_dir"], "data", "entities.jsonl"),
                  encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        exported_fact = [row for row in rows if row["type"] == "fact"
                         and row["fact_id"] == fact["fact_id"]][0]
        self.assertEqual(exported_fact["source"], "auto_extract")
        self.assertEqual(exported_fact["extraction"]["candidate_id"], candidate_id)
        # A 段请求不含库数据（B 段才含召回）
        extract_request = [item for item in mock.requests
                           if "抽取" in item["messages"][0]["content"]][0]
        self.assertNotIn("主力语言：Python",
                         json.dumps(extract_request, ensure_ascii=False))
        self.assertFalse(os.path.exists(
            os.path.join(self.data_dir, "state", "extract", ".run.lock")))

    def test_update_goes_to_review_then_approve_over_evolve(self):
        base = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")["fact"]
        _write_session(self.dsh_home, ["我的主力语言现在改成 Rust 了"])
        self._enable(auto_write=True)
        content = "我的主力语言现在改成 Rust 了"
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], content,
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload(content)], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "update", 0.9,
                                                  base["fact_id"])], "notes": ""}
        run, _mock = self._run(extraction, judgments)
        self.assertEqual(run["counters"]["auto_written"], 0)
        self.assertEqual(run["counters"]["queued"], 1)
        pending = api.list_extract_candidates(self.data_dir, "pending")["items"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["relation"]["llm"], "update")
        with self.assertRaises(errors.CoreError) as ctx:
            api.resolve_extract_candidate(self.data_dir, candidate_id, "approve", {})
        self.assertEqual(ctx.exception.code, "EXTRACT_CANDIDATE_INVALID")
        result = api.resolve_extract_candidate(
            self.data_dir, candidate_id, "approve",
            {"change_type": "evolve", "valid_from": "2026-11"})
        self.assertEqual(result["change_type"], "evolve")
        self.assertEqual(result["fact"]["source"], "auto_extract")
        self.assertEqual(result["candidate"]["status"], "approved")
        self.assertEqual(api.list_extract_candidates(self.data_dir, "pending")["items"], [])
        revised = api.show_fact(self.data_dir, base["fact_id"])
        self.assertEqual(revised["fact"]["valid_to"], result["fact"]["valid_from"])

    def test_candidate_detail_recall_and_target(self):
        base = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")["fact"]
        _write_session(self.dsh_home, ["我的主力语言现在改成 Rust 了"])
        self._enable(auto_write=True)
        content = "我的主力语言现在改成 Rust 了"
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], content,
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload(content)], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "update", 0.9,
                                                  base["fact_id"])], "notes": ""}
        self._run(extraction, judgments)
        detail = api.show_extract_candidate(self.data_dir, candidate_id)
        self.assertEqual(detail["candidate"]["candidate_id"], candidate_id)
        details = detail["recall_details"]
        self.assertTrue(details)
        first = [item for item in details if item["fact_id"] == base["fact_id"]][0]
        self.assertEqual(first["kind"], "current")
        self.assertEqual(first["category_key"], "tech")
        self.assertTrue(first["reasons"])          # 命中原因码（cjk/full_mode/...）
        self.assertTrue(detail["target_fact"])
        self.assertEqual(detail["target_fact"]["content"], "主力语言：Python")
        self.assertEqual(detail["target_fact"]["fact_id"], base["fact_id"])

    def test_display_name_maps_to_real_key(self):
        """真实 LLM 返回中文 display_name（技术背景）⇒ 容错映射为 tech。"""
        _write_session(self.dsh_home, ["我会用 Rust 写服务"])
        self._enable(auto_write=False)
        content = "我会用 Rust 写服务"
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], content,
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload(
            content, category="技术背景")], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        run, _mock = self._run(extraction, judgments)
        self.assertEqual(run["counters"]["queued"], 1)
        candidate = api.list_extract_candidates(self.data_dir, "pending")["items"][0]
        self.assertEqual(candidate["category_key"], "tech")

    def test_invalid_category_key_dropped_with_reason(self):
        """LLM 自拟非法 key（学习与技能）⇒ 丢弃且记录原因（不含原文）。"""
        _write_session(self.dsh_home, ["我会用 Rust 写服务"])
        self._enable(auto_write=False)
        extraction = {"candidates": [self._candidate_payload(
            "我会用 Rust 写服务", category="学习与技能")], "notes": ""}
        run, mock = self._run(extraction, {"judgments": [], "notes": ""})
        self.assertEqual(run["counters"]["candidates_created"], 0)
        self.assertEqual(run["counters"]["drop_reasons"]["invalid_category_key"], 1)
        self.assertEqual(run["counters"]["dropped_invalid"], 1)
        # A 段 prompt 含类别 schema（key/显示名），但不含任何事实内容
        request = json.dumps(mock.requests[0], ensure_ascii=False)
        self.assertIn("tech / 技术背景", request)
        self.assertIn("goals / 长期目标", request)

    def test_a_prompt_includes_categories_but_no_facts(self):
        """硬约束复验：A 段含启用类别清单（schema），不含 fact_id/召回/事实内容。"""
        api.add_fact(self.data_dir, "tech", "数据库里的秘密事实", "2025-01")
        _write_session(self.dsh_home, ["我会用 Rust 写服务"])
        self._enable(auto_write=False)
        extraction = {"candidates": [self._candidate_payload("我会用 Rust 写服务")],
                      "notes": ""}
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"],
                                                      "我会用 Rust 写服务", "tech", "new")
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        run, mock = self._run(extraction, judgments)
        extract_request = [item for item in mock.requests
                           if "抽取" in item["messages"][0]["content"]][0]
        sent = json.dumps(extract_request, ensure_ascii=False)
        self.assertIn("tech / 技术背景", sent)          # 类别 schema 必须注入
        self.assertNotIn("数据库里的秘密事实", sent)     # 事实内容不得出现
        self.assertNotIn("fact_id", sent)
        self.assertNotIn("召回", sent)

    def test_system_similar_blocks_llm_new(self):
        api.add_fact(self.data_dir, "basics", "老家湖北黄冈", "2025-01")
        _write_session(self.dsh_home, ["我出生在湖北黄冈"])
        self._enable(auto_write=True)
        content = "我出生在湖北黄冈"
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], content,
                                                      "basics", "new")
        extraction = {"candidates": [self._candidate_payload(
            content, category="basics")], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.95)],
                     "notes": ""}
        run, _mock = self._run(extraction, judgments)
        self.assertEqual(run["counters"]["auto_written"], 0)
        candidate = api.list_extract_candidates(self.data_dir, "pending")["items"][0]
        self.assertTrue(candidate["system"]["similar"])
        self.assertIn(candidate["decision"]["reason_code"], ("layer_mismatch",))
        self.assertIn("cjk_bigram", candidate["system"]["matches"][0]["reasons"])

    def test_auto_write_off_all_review(self):
        _write_session(self.dsh_home, ["我养了一只猫"])
        self._enable(auto_write=False)
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], "我养了一只猫",
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload("我养了一只猫")],
                      "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.99)],
                     "notes": ""}
        run, _mock = self._run(extraction, judgments)
        self.assertEqual(run["counters"]["auto_written"], 0)
        self.assertEqual(run["counters"]["queued"], 1)
        candidate = api.list_extract_candidates(self.data_dir, "pending")["items"][0]
        self.assertEqual(candidate["decision"]["reason_code"], "auto_write_disabled")

    def test_sensitive_candidate_dropped_and_redacted_in_prompt(self):
        _write_session(self.dsh_home, ["我的 key 是 sk-abcdefghijklmnop123456"])
        self._enable(auto_write=True)
        # 候选内容含敏感串 ⇒ 打码后仍命中 ⇒ 丢弃；A 段发送内容已打码
        content = "我的 key 是 sk-abcdefghijklmnop123456"
        extraction = {"candidates": [self._candidate_payload(content)], "notes": ""}
        run, mock = self._run(extraction, {"judgments": [], "notes": ""})
        self.assertEqual(run["counters"]["sensitive_skipped"], 1)
        self.assertEqual(run["counters"]["auto_written"], 0)
        sent = json.dumps(mock.requests[0], ensure_ascii=False)
        self.assertNotIn("sk-abcdefghijklmnop123456", sent)
        self.assertIn("[REDACTED", sent)
        runs_text = open(os.path.join(self.data_dir, "state", "extract", "runs.jsonl"),
                         encoding="utf-8").read()
        self.assertNotIn("sk-abcdefghijklmnop", runs_text)
        self.assertNotIn("我的 key", runs_text)

    def test_llm_invalid_json_run_failed_no_watermark(self):
        _write_session(self.dsh_home, ["随便聊聊"])
        self._enable(auto_write=True)

        class BadTransport(object):
            def __call__(self, url, headers, body, timeout):
                return 200, _envelope("{not json")
        extract_llm.set_transport(BadTransport())
        with self.assertRaises(errors.CoreError) as ctx:
            api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_INVALID_RESPONSE")
        marks, _rebuilt = extract_state.load_watermarks(self.data_dir)
        self.assertEqual(marks["sessions"], {})
        runs = api.list_extract_runs(self.data_dir)["items"]
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(api.list_extract_candidates(self.data_dir, "all")["items"], [])

    def test_bad_session_skipped_partial(self):
        good = _write_session(self.dsh_home, ["我叫张三"])
        bad_dir = os.path.join(self.dsh_home, "sessions", "ws", "bad")
        os.makedirs(bad_dir)
        with open(os.path.join(bad_dir, "session.v4.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("{broken\n")
        self._enable(auto_write=True)
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], "我叫张三",
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload("我叫张三")], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        run, _mock = self._run(extraction, judgments)
        self.assertIn(run["status"], ("partial", "ok"))
        self.assertGreaterEqual(run["counters"]["sessions_skipped"], 1)
        self.assertEqual(run["counters"]["auto_written"], 1)

    def test_idempotent_watermark_and_new_session(self):
        _write_session(self.dsh_home, ["我喜欢音乐"])
        self._enable(auto_write=False)
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], "我喜欢音乐",
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload("我喜欢音乐")], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        first, _ = self._run(extraction, judgments)
        self.assertEqual(first["counters"]["candidates_created"], 1)
        second, _ = self._run(extraction, judgments)
        self.assertEqual(second["counters"]["messages_processed"], 0)
        self.assertEqual(second["counters"]["candidates_created"], 0)
        # 新会话（新 session_id）从 seq=1 开始处理
        _write_session(self.dsh_home, ["我喜欢音乐"], session="s2",
                       session_id="sess-2")
        candidate_id_2 = extract_state.candidate_id_for(
            "sess-2", ["m1"], "我喜欢音乐", "tech", "new")
        judgments_2 = {"judgments": [self._judgment(candidate_id_2, "new", 0.9)],
                       "notes": ""}
        third, _ = self._run(extraction, judgments_2)
        self.assertEqual(third["counters"]["messages_processed"], 1)

    def test_watermark_rebuild_no_duplicate_write(self):
        _write_session(self.dsh_home, ["我喜欢音乐"])
        self._enable(auto_write=False)
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], "我喜欢音乐",
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload("我喜欢音乐")], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        self._run(extraction, judgments)
        os.unlink(os.path.join(self.data_dir, extract_state.WATERMARKS_REL))
        rebuilt_run, _mock = self._run(extraction, judgments)
        self.assertTrue(rebuilt_run["watermark_rebuilt"])
        all_candidates = api.list_extract_candidates(self.data_dir, "all")["items"]
        self.assertEqual(len(all_candidates), 1)      # evidence_key 去重
        self.assertEqual(len(api.list_facts(self.data_dir)["items"]), 0)

    def test_empty_store_full_mode(self):
        _write_session(self.dsh_home, ["我是学生"])
        self._enable(auto_write=False)
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], "我是学生",
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload("我是学生")], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        run, _mock = self._run(extraction, judgments)
        self.assertEqual(run["recall"]["mode"], "full")
        self.assertEqual(run["recall"]["corpus_bytes"], 0)
        self.assertEqual(run["counters"]["queued"], 1)

    def test_gates_disabled_consent_config(self):
        with self.assertRaises(errors.CoreError) as ctx:
            api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(ctx.exception.code, "EXTRACT_DISABLED")
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"].update({"base_url": "http://mock.local/v1", "model": "mock",
                           "api_key": "sk-x"})
        extract_config.save_config(self.data_dir, cfg)
        extract_config.set_consent(self.data_dir, True)
        extract_config.update_config(self.data_dir, {"extract": {"enabled": True}})
        extract_config.set_consent(self.data_dir, False)
        extract_config.update_config(self.data_dir, {"extract": {"enabled": False}})
        with self.assertRaises(errors.CoreError) as ctx:
            api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(ctx.exception.code, "EXTRACT_DISABLED")

    def test_a_prompt_no_database_data(self):
        api.add_fact(self.data_dir, "tech", "数据库里的秘密事实", "2025-01")
        _write_session(self.dsh_home, ["用户消息内容"])
        self._enable(auto_write=True)
        candidate_id = extract_state.candidate_id_for(SESSION_ID, ["m1"], "用户消息内容",
                                                      "tech", "new")
        extraction = {"candidates": [self._candidate_payload("用户消息内容")], "notes": ""}
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        run, mock = self._run(extraction, judgments)
        extract_requests = [item for item in mock.requests
                            if "抽取" in item["messages"][0]["content"]]
        self.assertGreaterEqual(len(extract_requests), 1)
        for request in extract_requests:
            sent = json.dumps(request, ensure_ascii=False)
            self.assertNotIn("数据库里的秘密事实", sent)
            self.assertNotIn("fact_id", sent)
            self.assertNotIn("召回", sent)
        judge_requests = [item for item in mock.requests
                          if "判定" in item["messages"][0]["content"]]
        self.assertGreaterEqual(len(judge_requests), 1)
        self.assertIn("数据库里的秘密事实",
                      json.dumps(judge_requests[0], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
