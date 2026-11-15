"""C-09 M1：LLM/提取配置（config.json + env 优先）与会话解析/分块。"""

import json
import os
import stat
import unittest
from unittest import mock

from _base import BaseStoreTest
from fasti.core import constants, errors, extract_config, session_source


def _write_plain_session(dsh_home, workspace="ws", session="s1", events=None):
    session_dir = os.path.join(dsh_home, "sessions", workspace, session)
    os.makedirs(session_dir, exist_ok=True)
    path = os.path.join(session_dir, "session.v4.jsonl")
    events = events if events is not None else [
        {"type": "session", "data": {"version": 4, "delegationDepth": 0,
                                     "isSeeded": False, "session_id": "sess-1",
                                     "cwd": "/project/a"}},
        {"seq": 1, "type": "user/message",
         "data": {"id": "m1", "role": "user",
                  "content": [{"type": "text", "text": "我喜欢 Python"}]}},
        {"seq": 2, "type": "assistant/message",
         "data": {"id": "m2", "role": "assistant",
                  "content": [{"type": "text", "text": "好的"}]}},
        {"seq": 3, "type": "tool/call", "data": {"tool": "x"}},
        {"seq": 4, "type": "user/message",
         "data": {"id": "m3", "role": "user",
                  "content": [{"type": "text", "text": "我住在上海"},
                              {"type": "text", "text": "，喜欢 C++"}]}},
    ]
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


class TestExtractConfig(BaseStoreTest):
    def test_defaults_and_roundtrip(self):
        cfg = extract_config.load_config(self.data_dir)
        self.assertFalse(cfg["extract"]["enabled"])
        self.assertFalse(cfg["extract"]["auto_write"])
        self.assertEqual(cfg["llm"]["timeout_seconds"], 60)
        cfg["llm"]["base_url"] = "http://127.0.0.1:9/v1"
        cfg["llm"]["model"] = "mock"
        cfg["llm"]["api_key"] = "sk-test-secret-abcd"
        extract_config.save_config(self.data_dir, cfg)
        path = extract_config.config_path(self.data_dir)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        status = extract_config.config_status(self.data_dir)
        self.assertTrue(status["config_ok"])
        self.assertTrue(status["api_key_set"])
        self.assertEqual(status["api_key_masked"], "****abcd")
        self.assertNotIn("api_key", status)          # 永不返回明文
        self.assertNotIn("sk-test-secret-abcd", json.dumps(status, ensure_ascii=False))

    def test_env_overrides_config(self):
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"].update({"base_url": "http://file/v1", "model": "file",
                           "api_key": "file-key-1234"})
        extract_config.save_config(self.data_dir, cfg)
        env = {"FASTI_LLM_BASE_URL": "http://env/v1", "FASTI_LLM_MODEL": "env-model",
               "FASTI_LLM_API_KEY": "env-key-abcd"}
        with mock.patch.dict(os.environ, env, clear=False):
            cfg2 = extract_config.load_config(self.data_dir)
            self.assertEqual(cfg2["llm"]["base_url"], "http://env/v1")
            self.assertEqual(cfg2["llm"]["model"], "env-model")
            self.assertEqual(cfg2["llm"]["api_key"], "env-key-abcd")
        with mock.patch.dict(os.environ, {"FASTI_EXTRACT_DISABLED": "1"}, clear=False):
            cfg3 = extract_config.load_config(self.data_dir)
            self.assertFalse(cfg3["extract"]["enabled"])

    def test_enable_requires_consent(self):
        with self.assertRaises(errors.CoreError) as ctx:
            extract_config.update_config(self.data_dir, {"extract": {"enabled": True}})
        self.assertEqual(ctx.exception.code, "EXTRACT_CONSENT_REQUIRED")
        extract_config.set_consent(self.data_dir, True)
        cfg = extract_config.update_config(self.data_dir, {"extract": {"enabled": True}})
        self.assertTrue(cfg["extract"]["enabled"])
        self.assertIsNotNone(cfg["extract"]["consent"]["accepted_at"])
        self.assertEqual(extract_config.CONFIG_REL, "config.json")

    def test_invalid_config_rejected(self):
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"]["base_url"] = "ftp://nope"
        with self.assertRaises(errors.CoreError) as ctx:
            extract_config.save_config(self.data_dir, cfg)
        self.assertEqual(ctx.exception.code, "EXTRACT_LLM_CONFIG_INVALID")
        self.assertIn("base_url", ctx.exception.message)

    def test_missing_fields_and_consent_status(self):
        status = extract_config.config_status(self.data_dir)
        self.assertEqual(status["config_missing"],
                         ["llm.base_url", "llm.model", "llm.api_key"])
        self.assertFalse(status["consent"]["accepted"])
        with self.assertRaises(errors.CoreError) as ctx:
            extract_config.require_run_ready(self.data_dir)
        self.assertEqual(ctx.exception.code, "EXTRACT_DISABLED")


class TestSessionSource(BaseStoreTest):
    def setUp(self):
        super().setUp()
        self.dsh_home = os.path.join(self.tmp, "dsh")
        _write_plain_session(self.dsh_home)

    def test_discover_and_filter_user_messages(self):
        paths = session_source.discover_sessions(self.dsh_home, allow_plain=True)
        self.assertEqual(len(paths), 1)
        session = session_source.load_session(paths[0], allow_plain=True)
        self.assertEqual(session["session_id"], "sess-1")
        self.assertEqual(session["cwd"], "/project/a")
        self.assertEqual([m["message_id"] for m in session["messages"]], ["m1", "m3"])
        self.assertEqual([m["ordinal"] for m in session["messages"]], [1, 2])
        self.assertEqual(session["messages"][1]["text"], "我住在上海，喜欢 C++")
        self.assertEqual(session["max_seq"], 4)
        # 助手/工具消息不出现
        joined = json.dumps(session["messages"], ensure_ascii=False)
        self.assertNotIn("好的", joined)

    def test_non_root_sessions_skipped(self):
        _write_plain_session(self.dsh_home, workspace="ws", session="sub",
                             events=[{"type": "session",
                                      "data": {"version": 4, "delegationDepth": 1,
                                               "isSeeded": False, "session_id": "sub"}},
                                     {"seq": 1, "type": "user/message",
                                      "data": {"id": "x", "role": "user",
                                               "content": [{"type": "text", "text": "x"}]}}])
        _write_plain_session(self.dsh_home, workspace="ws", session="seed",
                             events=[{"type": "session",
                                      "data": {"version": 4, "delegationDepth": 0,
                                               "isSeeded": True, "session_id": "seed"}}])
        paths = session_source.discover_sessions(self.dsh_home, allow_plain=True)
        self.assertEqual(len(paths), 3)
        roots = [session_source.load_session(path, allow_plain=True) for path in paths]
        roots = [item for item in roots if item is not None]
        self.assertEqual([item["session_id"] for item in roots], ["sess-1"])

    def test_chunking_respects_message_boundaries(self):
        messages = [{"seq": index, "event_sha256": "h", "message_id": "m%d" % index,
                     "ordinal": index, "text": "字" * 300} for index in range(1, 26)]
        session = {"session_id": "s", "messages": messages}
        chunks = session_source.chunk_messages(session)
        self.assertGreaterEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["messages"]), 20)
            self.assertLessEqual(chunk["chars"], 4000)
        self.assertTrue(all(len(chunk["chunk_id"]) == 16 for chunk in chunks))
        self.assertEqual(sum(len(chunk["messages"]) for chunk in chunks), 25)

    def test_per_message_truncation(self):
        path = _write_plain_session(self.dsh_home, workspace="ws", session="long",
                                    events=[{"type": "session",
                                             "data": {"version": 4, "delegationDepth": 0,
                                                      "isSeeded": False, "session_id": "long"}},
                                            {"seq": 1, "type": "user/message",
                                             "data": {"id": "m", "role": "user",
                                                      "content": [{"type": "text",
                                                                   "text": "a" * 2000}]}}])
        session = session_source.load_session(path, allow_plain=True)
        chunk = session_source.chunk_messages(session)[0]
        text = chunk["messages"][0]["chunk_text"]
        self.assertTrue(text.endswith(session_source.TRUNCATION_MARKER))
        self.assertEqual(len(text), constants.EXTRACT_PER_MESSAGE_CHARS
                         + len(session_source.TRUNCATION_MARKER))

    def test_plain_requires_allow_plain(self):
        path = _write_plain_session(self.dsh_home, workspace="ws", session="plain2")
        with self.assertRaises(errors.CoreError) as ctx:
            session_source.load_session(path, allow_plain=False)
        self.assertEqual(ctx.exception.code, "EXTRACT_SESSION_UNREADABLE")

    def test_zstd_unavailable_is_environment_error(self):
        with mock.patch.object(session_source, "_stdlib_zstd", return_value=None), \
                mock.patch.object(session_source.shutil, "which", return_value=None):
            with self.assertRaises(errors.CoreError) as ctx:
                session_source.load_session("/tmp/x/session.v4.jsonl.zstd")
        self.assertEqual(ctx.exception.code, "EXTRACT_ZSTD_UNAVAILABLE")

    def test_corrupt_json_skips_session(self):
        path = os.path.join(self.dsh_home, "sessions", "ws", "bad", "session.v4.jsonl")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        with self.assertRaises(errors.CoreError) as ctx:
            session_source.load_session(path, allow_plain=True)
        self.assertEqual(ctx.exception.code, "EXTRACT_SESSION_UNREADABLE")


if __name__ == "__main__":
    unittest.main()
