"""C-10 M1：会话标题解析/divert_input + 分流 prompt/校验 + 分流接入（skip/失败水位）。"""

import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import (api, constants, errors, extract_config, extract_divert,
                        extract_llm, extract_state, session_source)

MARK = session_source.TRUNCATION_MARKER


def _write_session(dsh_home, events, session="s1", workspace="ws"):
    session_dir = os.path.join(dsh_home, "sessions", workspace, session)
    os.makedirs(session_dir, exist_ok=True)
    path = os.path.join(session_dir, "session.v4.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def _header(session_id):
    return {"type": "session", "data": {"version": 4, "delegationDepth": 0,
                                        "isSeeded": False, "session_id": session_id}}


def _user(seq, message_id, text):
    return {"seq": seq, "type": "user/message",
            "data": {"id": message_id, "role": "user",
                     "source": {"kind": "user"},
                     "content": [{"type": "text", "text": text}]}}


def _title(title, kind, seq=9):
    return {"seq": seq, "type": "session/title",
            "data": {"title": title, "source": {"kind": kind}}}


def _envelope(payload):
    return json.dumps({"choices": [{"message": {
        "content": json.dumps(payload, ensure_ascii=False)}}]}).encode("utf-8")


class TestSessionDivertInput(BaseStoreTest):
    def test_provider_title_last_and_kinds(self):
        path = _write_session(self.tmp, [
            _header("s1"), _user(1, "m1", "第一条"),
            _title("标题A", "provider"), _user(2, "m2", "第二条"),
            _title("fallback标题", "fallback"), _title("标题B", "provider"),
        ])
        session = session_source.load_session(path, allow_plain=True)
        divert = session["divert_input"]
        self.assertEqual(divert["title"], "标题B")
        self.assertEqual(divert["title_source"], "provider")
        self.assertEqual(divert["title_kinds_seen"], ["provider", "fallback", "provider"])
        self.assertEqual(divert["first_user_message"], "第一条")
        self.assertEqual(divert["user_message_count"], 2)
        self.assertFalse(divert["title_truncated"])

    def test_no_provider_title_uses_first_message_fallback(self):
        path = _write_session(self.tmp, [
            _header("s2"), _user(1, "m1", "首条用户消息"),
            _title("fallback标题", "fallback"),
        ])
        divert = session_source.load_session(path, allow_plain=True)["divert_input"]
        self.assertIsNone(divert["title"])                  # 不拿 fallback 冒充
        self.assertEqual(divert["title_source"], "first_message")
        self.assertEqual(divert["first_user_message"], "首条用户消息")

    def test_no_title_at_all(self):
        path = _write_session(self.tmp, [_header("s3"), _user(1, "m1", "只有消息")])
        divert = session_source.load_session(path, allow_plain=True)["divert_input"]
        self.assertIsNone(divert["title"])
        self.assertEqual(divert["title_source"], "first_message")
        self.assertEqual(divert["first_user_message"], "只有消息")

    def test_truncation_200_and_300(self):
        path = _write_session(self.tmp, [
            _header("s4"), _user(1, "m1", "首" * 400),
            _title("标" * 250, "provider"),
        ])
        divert = session_source.load_session(path, allow_plain=True)["divert_input"]
        self.assertTrue(divert["title_truncated"])
        self.assertEqual(len(divert["title"]), constants.EXTRACT_TITLE_MAX_CHARS + len(MARK))
        self.assertTrue(divert["title"].endswith(MARK))
        self.assertTrue(divert["first_message_truncated"])
        self.assertEqual(len(divert["first_user_message"]),
                         constants.EXTRACT_FIRST_MESSAGE_MAX_CHARS + len(MARK))
        self.assertTrue(divert["first_user_message"].endswith(MARK))


class TestDivertPrompt(unittest.TestCase):
    def test_prompt_only_title_first_message_count(self):
        divert_input = {"title": "技术排障", "title_source": "provider",
                        "first_user_message": "pnpm 报错怎么修",
                        "user_message_count": 12,
                        "title_kinds_seen": ["provider"]}
        messages = extract_divert.build_divert_messages(divert_input)
        sent = json.dumps(messages, ensure_ascii=False)
        self.assertIn("技术排障", sent)
        self.assertIn("pnpm 报错怎么修", sent)
        self.assertEqual(json.loads(messages[1]["content"])["user_message_count"], 12)
        for forbidden in ("fact_id", "召回", "会话原文", "session_id", "/Users/",
                          "assistant", "tool"):
            self.assertNotIn(forbidden, sent)

    def test_v2_rules_project_progress_is_extract(self):
        """C-10 追加：项目/工作进展算值得提取（产出 1 条 summary）；纯排障 skip。"""
        from fasti.core import constants
        self.assertEqual(constants.EXTRACT_PROMPT_DIVERT, "c10-divert-v2")
        prompt = extract_divert.SYSTEM_DIVERT
        for marker in ("包括项目与工作进展", "项目 summary", "纯技术排障问答",
                       "pnpm build 报错怎么修", "我最近在做 Fasti 这个项目"):
            self.assertIn(marker, prompt)
        messages = extract_divert.build_divert_messages(
            {"title": "个人记忆系统项目进展", "title_source": "provider",
             "first_user_message": "我最近在做 Fasti 这个项目，P0 做完了",
             "user_message_count": 6})
        self.assertIn("个人记忆系统项目进展", json.dumps(messages, ensure_ascii=False))

    def test_validation_strict(self):
        self.assertEqual(extract_divert.validate_divert_result(
            '{"decision":"extract","reason":"含个人信息"}'),
            {"decision": "extract", "reason": "含个人信息"})
        for bad in ('{"decision":"maybe","reason":"x"}',
                    '{"decision":"skip"}',
                    '{"decision":"skip","reason":""}',
                    'not json'):
            with self.assertRaises(errors.CoreError) as ctx:
                extract_divert.validate_divert_result(bad)
            self.assertEqual(ctx.exception.code, "EXTRACT_LLM_INVALID_RESPONSE")


class TestDivertRuntime(BaseStoreTest):
    def setUp(self):
        super().setUp()
        self.dsh_home = os.path.join(self.tmp, "dsh")
        self.original_sleep = extract_llm.time.sleep
        extract_llm.time.sleep = lambda seconds: None
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"].update({"base_url": "http://mock.local/v1", "model": "mock",
                           "api_key": "sk-mock-1234"})
        cfg["extract"]["sources"] = {"dsh_home": self.dsh_home, "allow_plain": True}
        extract_config.save_config(self.data_dir, cfg)
        extract_config.set_consent(self.data_dir, True)
        extract_config.update_config(self.data_dir,
                                     {"extract": {"enabled": True, "auto_write": False}})

    def tearDown(self):
        extract_llm.time.sleep = self.original_sleep
        extract_llm.reset_transport()
        super().tearDown()

    def _transport(self, divert_fn, extraction=None, judgments=None):
        calls = []

        def transport(url, headers, body, timeout):
            payload = json.loads(body.decode("utf-8"))
            system = payload["messages"][0]["content"]
            calls.append(system)
            if "分流" in system:
                return divert_fn(payload)
            if "可用类别" in system:
                return 200, _envelope(extraction or {"candidates": []})
            return 200, _envelope(judgments or {"judgments": []})
        self.calls = calls
        extract_llm.set_transport(transport)

    def test_skip_advances_watermark_and_records_reason(self):
        _write_session(self.dsh_home, [_header("skip-1"), _user(1, "m1", "pnpm 排障")])
        self._transport(lambda payload: (200, _envelope(
            {"decision": "skip", "reason": "纯技术排障，无个人信息"})))
        first = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(first["counters"]["divert_skip"], 1)
        self.assertEqual(first["counters"]["candidates_created"], 0)
        record = first["sessions"][0]
        self.assertEqual(record["decision"], "skip")
        self.assertIn("排障", record["reason"])
        marks, _ = extract_state.load_watermarks(self.data_dir)
        self.assertEqual(marks["sessions"]["skip-1"]["last_seq"], 1)
        second = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(second["counters"]["divert_skip"], 0)   # 已推进，不再处理
        self.assertEqual(second["counters"]["messages_processed"], 0)

    def test_divert_failure_retries_and_continues(self):
        _write_session(self.dsh_home, [_header("bad-1"), _user(1, "m1", "第一会话")],
                       session="s1")
        _write_session(self.dsh_home, [_header("good-2"), _user(1, "m1", "第二会话")],
                       session="s2")
        state = {"bad_calls": 0}

        def divert_fn(payload):
            first = json.loads(payload["messages"][1]["content"])
            if first["first_user_message"] == "第一会话":
                state["bad_calls"] += 1
                return 200, _envelope("not a divert response")
            return 200, _envelope({"decision": "extract", "reason": "含个人信息"})
        candidate_id = extract_state.candidate_id_for(
            "good-2", ["m1"], "我叫张三", "tech", "new")
        self._transport(divert_fn,
                        extraction={"candidates": [{
                            "content": "我叫张三", "category_key": "tech",
                            "granularity": "atomic", "subject": None,
                            "time_hint": None, "extraction_confidence": 0.9,
                            "evidence": {"message_ids": ["m1"]}}]},
                        judgments={"judgments": [{
                            "candidate_id": candidate_id, "relation": "new",
                            "target_fact_id": None, "relation_confidence": 0.9,
                            "rationale": "x"}]})
        # 第二会话需要首条消息 "我叫张三" 才能匹配候选证据；改写内容
        _write_session(self.dsh_home, [_header("good-2"),
                                       _user(1, "m1", "我叫张三")], session="s2")
        first = api.extract_run(self.data_dir, trigger="manual")
        self.assertGreaterEqual(first["counters"]["divert_failed"], 1)
        self.assertEqual(first["counters"]["divert_extract"], 1)
        self.assertEqual(len(api.list_extract_candidates(self.data_dir, "pending")["items"]), 1)
        marks, _ = extract_state.load_watermarks(self.data_dir)
        self.assertNotIn("bad-1", marks["sessions"])     # 失败不推进
        self.assertEqual(state["bad_calls"], 1)
        second = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(state["bad_calls"], 2)          # 下轮重试
        self.assertEqual(second["counters"]["divert_failed"], 1)

    def test_project_progress_extract_vs_troubleshooting_skip(self):
        """mock 分流：项目进展 ⇒ extract（1 条 summary）；纯排障 ⇒ skip。"""
        _write_session(self.dsh_home, [
            _header("proj-1"),
            _title("个人记忆系统项目进展", "provider"),
            _user(1, "m1", "我最近在做 Fasti 这个项目，P0 做完了")], session="proj")
        _write_session(self.dsh_home, [
            _header("trouble-1"),
            _title("pnpm 排障", "provider"),
            _user(1, "m1", "pnpm build 报错怎么修？")], session="trouble")

        def divert_fn(payload):
            first = json.loads(payload["messages"][1]["content"])
            if "Fasti" in first["first_user_message"]:
                return 200, _envelope({"decision": "extract",
                                       "reason": "项目进展，产出 1 条 summary"})
            return 200, _envelope({"decision": "skip",
                                   "reason": "纯技术排障，无用户信息"})
        self._transport(divert_fn,
                        extraction={"candidates": [{
                            "content": "在做一个 AI memory 方向的开源项目（Fasti）",
                            "category_key": "projects", "granularity": "summary",
                            "subject": "fasti", "time_hint": None,
                            "extraction_confidence": 0.9,
                            "evidence": {"message_ids": ["m1"]}}]},
                        judgments={"judgments": []})
        run = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(run["counters"]["divert_extract"], 1)
        self.assertEqual(run["counters"]["divert_skip"], 1)
        decisions = {record["session_id"]: record["decision"]
                     for record in run["sessions"]}
        self.assertEqual(decisions["proj-1"], "extract")
        self.assertEqual(decisions["trouble-1"], "skip")
        items = api.list_extract_candidates(self.data_dir, "pending")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["granularity"], "summary")

    def test_three_prompt_routes(self):
        _write_session(self.dsh_home, [_header("routes"), _user(1, "m1", "我喜欢猫")])
        candidate_id = extract_state.candidate_id_for(
            "routes", ["m1"], "我喜欢猫", "tech", "new")
        self._transport(lambda payload: (200, _envelope(
            {"decision": "extract", "reason": "含个人信息"})),
            extraction={"candidates": [{
                "content": "我喜欢猫", "category_key": "tech",
                "granularity": "atomic", "subject": None, "time_hint": None,
                "extraction_confidence": 0.9,
                "evidence": {"message_ids": ["m1"]}}]},
            judgments={"judgments": [{"candidate_id": candidate_id,
                                      "relation": "new", "target_fact_id": None,
                                      "relation_confidence": 0.9, "rationale": "x"}]})
        api.extract_run(self.data_dir, trigger="manual")
        kinds = {"divert": any("分流" in system for system in self.calls),
                 "extract": any("可用类别" in system for system in self.calls),
                 "judge": any("判定" in system for system in self.calls)}
        self.assertTrue(all(kinds.values()), kinds)


if __name__ == "__main__":
    unittest.main()
