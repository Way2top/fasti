"""JSON/截断重试与会话失败隔离回归。"""

import json

from test_extract_run import (ExtractRuntimeBase, _envelope, _write_session,
                             SESSION_ID)
from fasti.core import api, extract_llm, extract_state


def _valid_extraction(message_id="m1", content="我喜欢猫"):
    return {"candidates": [{
        "content": content, "category_key": "tech", "granularity": "atomic",
        "subject": None, "time_hint": None, "extraction_confidence": 0.9,
        "evidence": {"message_ids": [message_id]}}]}


class TestRetry(ExtractRuntimeBase):
    def test_invalid_json_retries_then_succeeds(self):
        _write_session(self.dsh_home, ["我喜欢猫"])
        self._enable(auto_write=False)
        state = {"extract_calls": 0}
        candidate_id = extract_state.candidate_id_for(
            SESSION_ID, ["m1"], "我喜欢猫", "tech", "new")
        def transport(url, headers, body, timeout):
            system = json.loads(body.decode())["messages"][0]["content"]
            if "分流" in system:
                return 200, _envelope(json.dumps({"decision": "extract", "reason": "x"}))
            if "可用类别" in system:
                state["extract_calls"] += 1
                if state["extract_calls"] == 1:
                    return 200, _envelope("{not json")
                return 200, _envelope(json.dumps(_valid_extraction(), ensure_ascii=False))
            return 200, _envelope(json.dumps({"judgments": [{
                "candidate_id": candidate_id, "relation": "new",
                "target_fact_id": None, "relation_confidence": 0.9,
                "rationale": "x"}]}, ensure_ascii=False))
        extract_llm.set_transport(transport)
        run = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(state["extract_calls"], 2)      # 1 次失败 + 1 次成功
        self.assertEqual(len(api.list_extract_candidates(
            self.data_dir, "pending")["items"]), 1)

    def test_truncated_retries_once(self):
        _write_session(self.dsh_home, ["我喜欢猫"])
        self._enable(auto_write=False)
        state = {"extract_calls": 0}
        def transport(url, headers, body, timeout):
            system = json.loads(body.decode())["messages"][0]["content"]
            if "分流" in system:
                return 200, _envelope(json.dumps({"decision": "extract", "reason": "x"}))
            if "可用类别" in system:
                state["extract_calls"] += 1
                if state["extract_calls"] == 1:
                    return 200, json.dumps({"choices": [{
                        "finish_reason": "length",
                        "message": {"content": '{"candidates":['}}]}).encode()
                return 200, _envelope(json.dumps(_valid_extraction(), ensure_ascii=False))
            return 200, _envelope(json.dumps({"judgments": []}))
        extract_llm.set_transport(transport)
        run = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(state["extract_calls"], 2)

    def test_one_session_failure_isolated_partial(self):
        _write_session(self.dsh_home, ["第一会话"], session="s1", session_id="s1")
        _write_session(self.dsh_home, ["第二会话"], session="s2", session_id="s2")
        self._enable(auto_write=False)
        state = {"bad_calls": 0}
        candidate_id = extract_state.candidate_id_for(
            "s2", ["m1"], "第二会话", "tech", "new")
        def transport(url, headers, body, timeout):
            system = json.loads(body.decode())["messages"][0]["content"]
            if "分流" in system:
                return 200, _envelope(json.dumps({"decision": "extract", "reason": "x"}))
            if "可用类别" in system:
                block = json.loads(json.loads(body.decode())["messages"][1]["content"])
                if block[0]["text"] == "第一会话":
                    state["bad_calls"] += 1
                    return 200, _envelope("{not json")
                return 200, _envelope(json.dumps(_valid_extraction("m1", "第二会话"), ensure_ascii=False))
            return 200, _envelope(json.dumps({"judgments": [{
                "candidate_id": candidate_id, "relation": "new",
                "target_fact_id": None, "relation_confidence": 0.9,
                "rationale": "x"}]}, ensure_ascii=False))
        extract_llm.set_transport(transport)
        run = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(run["status"], "partial")
        self.assertEqual(state["bad_calls"], 3)          # 初始 + 2 次重试
        failed = [s for s in run["sessions"] if s["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["error_code"], "EXTRACT_LLM_INVALID_RESPONSE")
        marks, _ = extract_state.load_watermarks(self.data_dir)
        self.assertNotIn("s1", marks["sessions"])
        self.assertIn("s2", marks["sessions"])
        self.assertEqual(len(api.list_extract_candidates(
            self.data_dir, "pending")["items"]), 1)

    def test_all_sessions_failed(self):
        _write_session(self.dsh_home, ["第一会话"], session="s1", session_id="s1")
        _write_session(self.dsh_home, ["第二会话"], session="s2", session_id="s2")
        self._enable(auto_write=False)
        def transport(url, headers, body, timeout):
            system = json.loads(body.decode())["messages"][0]["content"]
            if "分流" in system:
                return 200, _envelope(json.dumps({"decision": "extract", "reason": "x"}))
            return 200, _envelope("{not json")
        extract_llm.set_transport(transport)
        run = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(len([s for s in run["sessions"] if s["status"] == "failed"]), 2)
        marks, _ = extract_state.load_watermarks(self.data_dir)
        self.assertEqual(marks["sessions"], {})
        self.assertEqual(api.list_extract_candidates(self.data_dir, "all")["items"], [])
