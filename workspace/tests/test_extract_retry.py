"""JSON/截断重试与会话失败隔离回归。"""

import json

from test_extract_run import (ExtractRuntimeBase, _envelope, _write_session,
                             SESSION_ID)
import socket
import urllib.error
from fasti.core import api, errors, extract_llm, extract_state


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


class TestGlobalFatal(ExtractRuntimeBase):
    def _two_sessions(self):
        _write_session(self.dsh_home, ["第一会话"], session="s1", session_id="s1")
        _write_session(self.dsh_home, ["第二会话"], session="s2", session_id="s2")
        self._enable(auto_write=False)

    def _run_fatal(self, kind):
        state = {"calls": []}

        def transport(url, headers, body, timeout):
            payload = json.loads(body.decode())
            system = payload["messages"][0]["content"]
            user = json.loads(payload["messages"][1]["content"])
            if "分流" in system:
                state["calls"].append(("divert", user.get("first_user_message")))
                return 200, _envelope(json.dumps({"decision": "extract",
                                                  "reason": "x"}))
            state["calls"].append(("extract", user[0]["text"]))
            if kind == "unreachable":
                raise urllib.error.URLError("down")
            if kind == "timeout":
                raise socket.timeout("timeout")
            return 500, b""
        extract_llm.set_transport(transport)
        with self.assertRaises(errors.CoreError) as ctx:
            api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(ctx.exception.code, {
            "unreachable": "EXTRACT_LLM_UNREACHABLE",
            "timeout": "EXTRACT_LLM_TIMEOUT",
            "http": "EXTRACT_LLM_HTTP_ERROR"}[kind])
        run = api.list_extract_runs(self.data_dir)["items"][0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(len(run["sessions"]), 1)          # 第二会话未处理
        self.assertEqual(run["sessions"][0]["session_id"], "s1")
        self.assertEqual(run["sessions"][0]["status"], "failed")
        self.assertFalse(any(item[1] == "第二会话" for item in state["calls"]))
        marks, _ = extract_state.load_watermarks(self.data_dir)
        self.assertEqual(marks["sessions"], {})

    def test_unreachable_is_global_fatal(self):
        self._two_sessions()
        self._run_fatal("unreachable")

    def test_timeout_is_global_fatal(self):
        self._two_sessions()
        self._run_fatal("timeout")

    def test_http_error_is_global_fatal(self):
        self._two_sessions()
        self._run_fatal("http")

    def test_divert_unreachable_still_isolated(self):
        self._two_sessions()
        good_id = extract_state.candidate_id_for(
            "s2", ["m1"], "第二会话", "tech", "new")
        def transport(url, headers, body, timeout):
            payload = json.loads(body.decode())
            system = payload["messages"][0]["content"]
            if "分流" in system:
                user = json.loads(payload["messages"][1]["content"])
                if user.get("first_user_message") == "第一会话":
                    raise urllib.error.URLError("down")
                return 200, _envelope(json.dumps({"decision": "extract",
                                                  "reason": "x"}))
            if "可用类别" in system:
                return 200, _envelope(json.dumps(_valid_extraction("m1", "第二会话")))
            return 200, _envelope(json.dumps({"judgments": [{
                "candidate_id": good_id, "relation": "new", "target_fact_id": None,
                "relation_confidence": 0.9, "rationale": "x"}]}))
        extract_llm.set_transport(transport)
        run = api.extract_run(self.data_dir, trigger="manual")
        self.assertEqual(run["status"], "partial")
        statuses = {session["session_id"]: session["status"]
                    for session in run["sessions"]}
        self.assertEqual(statuses["s1"], "divert_failed")
        self.assertEqual(statuses["s2"], "processed")
        self.assertEqual(len(api.list_extract_candidates(
            self.data_dir, "pending")["items"]), 1)
        marks, _ = extract_state.load_watermarks(self.data_dir)
        self.assertNotIn("s1", marks["sessions"])
        self.assertIn("s2", marks["sessions"])
