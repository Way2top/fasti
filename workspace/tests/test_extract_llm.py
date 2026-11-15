"""C-09 M3：LLM 客户端（urllib/超时/重试/2MiB）+ 两段 prompt 内容断言 + 输出校验。"""

import json
import socket
import unittest
import urllib.error
from unittest import mock

from fasti.core import errors, extract_llm

CFG = {
    "llm": {"base_url": "http://mock.local/v1", "model": "mock", "api_key": "sk-secret-1234",
            "timeout_seconds": 60, "max_retries": 2, "json_mode": True},
}


def _envelope(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


class MockTransport(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers,
                           "payload": json.loads(body.decode("utf-8")),
                           "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestLlmClient(unittest.TestCase):
    def setUp(self):
        self.original_sleep = extract_llm.time.sleep
        extract_llm.time.sleep = lambda seconds: None
        extract_llm.reset_transport()

    def tearDown(self):
        extract_llm.time.sleep = self.original_sleep
        extract_llm.reset_transport()

    def test_success_and_request_shape(self):
        transport = MockTransport([(200, _envelope('{"candidates":[]}'))])
        extract_llm.set_transport(transport)
        content = extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(content, '{"candidates":[]}')
        call = transport.calls[0]
        self.assertEqual(call["url"], "http://mock.local/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-secret-1234")
        self.assertEqual(call["payload"]["response_format"], {"type": "json_object"})
        self.assertFalse(call["payload"]["stream"])
        self.assertEqual(call["payload"]["temperature"], 0)
        self.assertEqual(call["timeout"], 60)

    def test_retries_on_5xx_then_succeeds(self):
        transport = MockTransport([(500, b"err"), (200, _envelope('{"candidates":[]}'))])
        extract_llm.set_transport(transport)
        extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(len(transport.calls), 2)

    def test_4xx_not_retried(self):
        transport = MockTransport([(400, b'{"error":"bad"}')])
        extract_llm.set_transport(transport)
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_HTTP_ERROR")
        self.assertEqual(ctx.exception.exit_code, 5)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("sk-secret-1234", ctx.exception.message)

    def test_retries_exhausted_429(self):
        transport = MockTransport([(429, b""), (429, b""), (429, b"")])
        extract_llm.set_transport(transport)
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_HTTP_ERROR")
        self.assertEqual(len(transport.calls), 3)

    def test_unreachable_and_timeout(self):
        extract_llm.set_transport(MockTransport([urllib.error.URLError("dns"),
                                                 urllib.error.URLError("dns"),
                                                 urllib.error.URLError("dns")]))
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_UNREACHABLE")
        extract_llm.set_transport(MockTransport([socket.timeout(), socket.timeout(),
                                                 socket.timeout()]))
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_TIMEOUT")

    def test_thinking_disable_payload_and_usage(self):
        seen = {}

        class Transport(object):
            def __call__(self, url, headers, body, timeout):
                payload = json.loads(body.decode("utf-8"))
                seen.update(payload)
                return 200, json.dumps({"choices": [{"finish_reason": "stop",
                                                     "message": {"content": "{}"}}],
                                        "usage": {"prompt_tokens": 10,
                                                  "completion_tokens": 30,
                                                  "completion_tokens_details": {
                                                      "reasoning_tokens": 12}}}).encode()
        extract_llm.set_transport(Transport())
        extract_llm.reset_usage_totals()
        extract_llm.chat_completion(CFG, [{"role": "user", "content": "x"}],
                                    disable_thinking=True)
        self.assertEqual(seen["thinking"], {"type": "disabled"})   # 必须是对象
        self.assertEqual(extract_llm.last_usage()["reasoning_tokens"], 12)
        self.assertEqual(extract_llm.usage_totals()["completion_tokens"], 30)
        self.assertEqual(extract_llm.usage_totals()["reasoning_tokens"], 12)
        seen.clear()
        extract_llm.chat_completion(CFG, [{"role": "user", "content": "x"}])
        self.assertNotIn("thinking", seen)                         # 默认不发送

    def test_truncation_is_dedicated_error(self):
        envelope = json.dumps({"choices": [{"finish_reason": "length", "message": {
            "content": '{"judgments":[{"candidate_id":"cand-'}}]}).encode()
        transport = MockTransport([(200, envelope)])
        extract_llm.set_transport(transport)
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_TRUNCATED")
        self.assertIn("截断", ctx.exception.message)
        self.assertIn("max_tokens", ctx.exception.message)

    def test_tolerant_json_parsing(self):
        fenced = "```json\n{\"candidates\": []}\n```"
        self.assertEqual(extract_llm.parse_json_object(fenced), {"candidates": []})
        prose = "这是结果：\n```json\n{\"a\": {\"b\": 1}}\n```\n以上。"
        self.assertEqual(extract_llm.parse_json_object(prose), {"a": {"b": 1}})
        with self.assertRaises(ValueError):
            extract_llm.parse_json_object('{"a": 1')

    def test_response_limits_and_invalid_envelope(self):
        extract_llm.set_transport(MockTransport([
            (200, b"x" * (2097153))]))
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_INVALID_RESPONSE")
        extract_llm.set_transport(MockTransport([(200, b"{bad json")]))
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.chat_completion(CFG, [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_INVALID_RESPONSE")

    def test_prompts_two_phases(self):
        chunk = {"chunk_id": "c", "session_id": "s", "messages": [
            {"ordinal": 1, "message_id": "m1",
             "chunk_text": "我喜欢 Python；key=sk-abcdefghijklmnop123456"},
            {"ordinal": 2, "message_id": "m2", "chunk_text": "我住在上海"},
        ]}
        categories = [{"key": "tech", "display_name": "技术背景"},
                      {"key": "goals", "display_name": "长期目标"}]
        extract_messages = extract_llm.build_extraction_messages(
            chunk, categories=categories)
        joined_a = json.dumps(extract_messages, ensure_ascii=False)
        # A 段只含用户消息（打码后），不含任何库数据/召回痕迹
        self.assertIn("我喜欢 Python", joined_a)
        self.assertIn("我住在上海", joined_a)
        self.assertIn("[REDACTED", joined_a)
        self.assertNotIn("sk-abcdefghijklmnop", joined_a)
        for marker in ("fact_id", "召回事实", "current |", "historical |", "短ID"):
            self.assertNotIn(marker, joined_a)
        self.assertNotIn("assistant", joined_a.lower())
        # 类别清单是输出 schema：必须提供，便于 LLM 使用真实 key（真实联调 bug 修复）
        self.assertIn("tech / 技术背景", joined_a)
        self.assertIn("goals / 长期目标", joined_a)
        self.assertIn("可用类别", joined_a)

        candidates = [{"candidate_id": "cand-" + "a" * 20, "content": "我喜欢 Python",
                       "category_key": "tech", "time_hint": None,
                       "extraction_confidence": 0.9}]
        recall = {"cand-" + "a" * 20: [
            "id=0190a1b23c4d | cat=tech | current | from=2026-11 | 主力语言：Python"]}
        judge_messages = extract_llm.build_judgment_messages(candidates, recall)
        joined_b = json.dumps(judge_messages, ensure_ascii=False)
        self.assertIn("cand-" + "a" * 20, joined_b)
        self.assertIn("主力语言：Python", joined_b)
        self.assertIn("id=0190a1b23c4d", joined_b)

    def test_validate_extraction_result(self):
        good = {"candidates": [
            {"content": "我喜欢 Python", "category_key": "tech",
             "extraction_confidence": 0.9, "evidence": {"message_ids": ["m1"]}},
            {"content": "", "category_key": "tech", "extraction_confidence": 0.9,
             "evidence": {"message_ids": ["m1"]}},          # 非法内容 ⇒ 丢弃
            {"content": "x", "category_key": "tech", "extraction_confidence": 2,
             "evidence": {"message_ids": ["m1"]}},           # 置信度越界 ⇒ 丢弃
        ]}
        valid, dropped = extract_llm.validate_extraction_result(json.dumps(good))
        self.assertEqual(len(valid), 1)
        self.assertEqual(dropped["invalid_candidate_structure"], 2)
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.validate_extraction_result('{"candidates": "nope"}')
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_INVALID_RESPONSE")

    def test_granularity_validation_and_prompt_rules(self):
        """M2：noise 六类/粒度规则进 prompt；granularity/subject 校验与丢弃原因。"""
        prompt = extract_llm.SYSTEM_EXTRACT
        for marker in ("禁止抽取的六类噪音", "项目/工作进度流水",
                       "子 agent / 任务执行细节", "工具报错与排障细节",
                       "代码/文件/架构具体细节", "对话过程性状态",
                       "临时计划、TODO、待办", "granularity", "summary", "atomic",
                       "subject 必填", "同一项目在同一会话中只产出 1 条 summary",
                       "在做一个 AI memory 方向的开源项目（Fasti）"):
            self.assertIn(marker, prompt)
        messages = extract_llm.build_extraction_messages(
            {"messages": [{"ordinal": 1, "message_id": "m1", "chunk_text": "x"}]},
            categories=[{"key": "tech", "display_name": "技术背景"}])
        sent = json.dumps(messages, ensure_ascii=False)
        self.assertIn("禁止抽取的六类噪音", sent)
        self.assertIn("tech / 技术背景", sent)        # 类别 schema
        self.assertNotIn("fact_id", sent)
        self.assertNotIn("召回", sent)
        payload = {"candidates": [
            {"content": "旧数据", "category_key": "tech", "extraction_confidence": 0.9,
             "evidence": {"message_ids": ["m1"]}},                     # 无 granularity：兼容
            {"content": "缺 subject", "category_key": "tech", "granularity": "summary",
             "extraction_confidence": 0.9, "evidence": {"message_ids": ["m1"]}},
            {"content": "非法粒度", "category_key": "tech", "granularity": "project",
             "extraction_confidence": 0.9, "evidence": {"message_ids": ["m1"]}},
            {"content": "偏好：喜欢猫", "category_key": "tech", "granularity": "atomic",
             "subject": None, "extraction_confidence": 0.9,
             "evidence": {"message_ids": ["m1"]}},
        ]}
        valid, dropped = extract_llm.validate_extraction_result(json.dumps(payload))
        self.assertEqual([item["granularity"] for item in valid], ["atomic", "atomic"])
        self.assertEqual(dropped["missing_subject"], 1)
        self.assertEqual(dropped["invalid_granularity"], 1)
        self.assertEqual(valid[0]["subject"], None)

    def test_validate_judgments(self):
        candidate_id = "cand-" + "a" * 20
        raw = json.dumps({"judgments": [
            {"candidate_id": candidate_id, "relation": "update",
             "target_fact_id": "fact-other", "relation_confidence": 0.9},
            {"candidate_id": "cand-" + "b" * 20, "relation": "new",
             "target_fact_id": None, "relation_confidence": 0.8},
        ]})
        result = extract_llm.validate_judgment_result(
            raw, {candidate_id, "cand-" + "b" * 20},
            {candidate_id: ["fact-1"], "cand-" + "b" * 20: []})
        self.assertTrue(result[candidate_id]["unverified"])
        self.assertFalse(result["cand-" + "b" * 20]["unverified"])
        with self.assertRaises(errors.CoreError) as ctx:
            extract_llm.validate_judgment_result(
                json.dumps({"judgments": [{"candidate_id": candidate_id,
                                           "relation": "new", "target_fact_id": "x",
                                           "relation_confidence": 0.8}]}),
                {candidate_id}, {candidate_id: []})
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_INVALID_RESPONSE")


if __name__ == "__main__":
    unittest.main()
