"""C-10 M4：config schema 1→2 迁移 + 过程状态 schema 2 + 旧数据兼容。"""

import json
import os
import stat
import unittest

from _base import BaseStoreTest
from fasti.core import api, constants, extract_config, extract_state
from test_extract_run import ExtractRuntimeBase, _write_session, SESSION_ID


def _write_v1_config(data_dir, api_key="sk-test-abcd"):
    cfg = {
        "schema_version": 1,
        "llm": {"base_url": "http://127.0.0.1:9/v1", "model": "mock",
                "api_key": api_key, "timeout_seconds": 60, "max_retries": 2,
                "json_mode": True},
        "extract": {
            "enabled": True, "auto_write": False,
            "auto_trigger_on_open": True,
            "auto_trigger_min_interval_seconds": 60,
            "consent": {"version": 1, "accepted_at": "2026-11-01T00:00:00+08:00",
                        "text_sha256": "abc"},
            "max_messages_per_run": 200, "max_sessions_per_run": 10,
            "auto_write_daily_limit": 20, "auto_write_per_run_limit": 10,
            "sensitive_filter": True,
            "sources": {"dsh_home": "~/.dsh", "allow_plain": False},
        },
    }
    path = os.path.join(data_dir, "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle)
    os.chmod(path, 0o644)
    return path


class TestConfigMigration(BaseStoreTest):
    def test_v1_to_v2_atomic_and_preserves(self):
        path = _write_v1_config(self.data_dir)
        cfg = extract_config.load_config(self.data_dir)
        self.assertEqual(cfg["schema_version"], 2)
        self.assertNotIn("auto_trigger_on_open", cfg["extract"])
        self.assertNotIn("auto_trigger_min_interval_seconds", cfg["extract"])
        self.assertEqual(cfg["extract"]["max_sessions_per_run"], 10)   # 其余保留
        self.assertEqual(cfg["extract"]["consent"]["text_sha256"], "abc")
        self.assertEqual(cfg["llm"]["api_key"], "sk-test-abcd")
        with open(path, encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(on_disk["schema_version"], 2)
        self.assertNotIn("auto_trigger_on_open", on_disk["extract"])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        status = extract_config.config_status(self.data_dir)
        self.assertEqual(status["config_version"], 2)
        self.assertTrue(status["config_migrated"])
        self.assertNotIn("auto_trigger_on_open", status)

    def test_idempotent_no_rewrite(self):
        path = _write_v1_config(self.data_dir)
        extract_config.load_config(self.data_dir)
        os.utime(path, (1000000000, 1000000000))
        before = os.stat(path).st_mtime_ns
        extract_config.load_config(self.data_dir)
        self.assertEqual(os.stat(path).st_mtime_ns, before)   # 第二次不重写

    def test_readonly_dir_warns_without_crash(self):
        from unittest import mock
        _write_v1_config(self.data_dir)
        with mock.patch.object(extract_config, "_write_raw_atomic",
                               side_effect=OSError("read-only")):
            cfg = extract_config.load_config(self.data_dir)
            self.assertEqual(cfg["schema_version"], 2)
            self.assertNotIn("auto_trigger_on_open", cfg["extract"])
            self.assertEqual(cfg["llm"]["model"], "mock")
            status = extract_config.config_status(self.data_dir)
            self.assertTrue(status["config_migrated"])
            self.assertFalse(status["config_persisted"])
            self.assertTrue(status["config_warning"])
        # 原文件未落盘（仍是 v1，仅内存迁移）
        with open(os.path.join(self.data_dir, "config.json"),
                  encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["schema_version"], 1)

    def test_corrupt_json_does_not_crash(self):
        with open(os.path.join(self.data_dir, "config.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        cfg = extract_config.load_config(self.data_dir)
        self.assertEqual(cfg["schema_version"], 2)
        self.assertIn("_config_warning", cfg)
        status = extract_config.config_status(self.data_dir)
        self.assertTrue(status["config_warning"])


class TestOldStateCompatibility(BaseStoreTest):
    def _old_candidate(self, content="旧候选", status="pending"):
        suffix = ("a" if "通过" in content else "b") * 20
        candidate = extract_state.new_candidate(
            "cand-" + suffix, "s1", ["m1"], [1], "chunk", "run-1", content,
            "tech", "new", 0.9,
            {"mode": "full", "corpus_bytes": 0, "current_count": 0,
             "historical_count": 0, "recalled_fact_ids": [],
             "historical_fact_ids": [], "reasons": {}, "truncated": False},
            {"action": "review", "reason_code": "auto_write_disabled"},
            extract_state.evidence_key_for("s1", ["m1"], content), status=status)
        for key in ("granularity", "subject", "summary_subject_key", "prompt_version"):
            candidate.pop(key, None)                    # 模拟 C-09 v1 旧数据
        extract_state.append_candidate(self.data_dir, candidate)
        return candidate

    def test_old_candidate_queue_show_approve_reject(self):
        approve_candidate = self._old_candidate("旧候选-通过")
        reject_candidate = self._old_candidate("旧候选-拒绝")
        items = api.list_extract_candidates(self.data_dir, "pending")["items"]
        self.assertEqual(len(items), 2)
        detail = api.show_extract_candidate(self.data_dir,
                                            approve_candidate["candidate_id"])
        self.assertIsNone(detail["candidate"].get("granularity"))
        api.resolve_extract_candidate(self.data_dir,
                                      approve_candidate["candidate_id"], "approve")
        api.resolve_extract_candidate(self.data_dir,
                                      reject_candidate["candidate_id"], "reject")
        self.assertEqual(api.list_extract_candidates(self.data_dir, "approved")["items"][0]
                         ["candidate_id"], approve_candidate["candidate_id"])
        self.assertEqual(api.list_extract_candidates(self.data_dir, "rejected")["items"][0]
                         ["candidate_id"], reject_candidate["candidate_id"])

    def test_old_runs_readable(self):
        runs_path = os.path.join(self.data_dir, "state", "extract", "runs.jsonl")
        os.makedirs(os.path.dirname(runs_path), exist_ok=True)
        with open(runs_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"run_id": "old-run", "status": "ok",
                                     "schema_version": 1}) + "\n")
        items = api.list_extract_runs(self.data_dir)["items"]
        self.assertEqual(items[0]["run_id"], "old-run")
        self.assertEqual(items[0]["schema_version"], 1)


class TestStateSchema2(ExtractRuntimeBase):
    def test_new_run_and_candidate_have_schema2_fields(self):
        _write_session(self.dsh_home, ["我喜欢猫"])
        self._enable(auto_write=False)
        extraction = {"candidates": [{
            "content": "我喜欢猫", "category_key": "tech",
            "granularity": "atomic", "subject": None, "time_hint": None,
            "extraction_confidence": 0.9, "evidence": {"message_ids": ["m1"]}}],
            "notes": ""}
        candidate_id = extract_state.candidate_id_for(
            SESSION_ID, ["m1"], "我喜欢猫", "tech", "new")
        run, _mock = self._run(extraction, {"judgments": [
            {"candidate_id": candidate_id, "relation": "new",
             "target_fact_id": None, "relation_confidence": 0.9,
             "rationale": "x"}]})
        self.assertEqual(run["schema_version"], 3)
        self.assertIn("prompts", run["llm"])
        self.assertEqual(run["llm"]["prompts"]["divert"], "c10-divert-v2")
        self.assertEqual(run["llm"]["prompts"]["extraction"], "c10-extract-v2")
        self.assertEqual(len(run["llm"]["prompt_sha256"]), 64)
        self.assertIn("dropped_duplicate_summary", run["counters"])
        candidate = api.list_extract_candidates(self.data_dir, "pending")["items"][0]
        for key in ("granularity", "subject", "summary_subject_key",
                    "prompt_version"):
            self.assertIn(key, candidate)
        self.assertEqual(candidate["prompt_version"], "c10-extract-v2")


if __name__ == "__main__":
    unittest.main()
