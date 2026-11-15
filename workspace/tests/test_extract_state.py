"""C-09 M2：过程状态（水位/候选/运行记录）与确定性 ID、幂等、并发锁。"""

import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import errors, extract_state


class TestExtractState(BaseStoreTest):
    def test_candidate_id_and_evidence_key_are_deterministic(self):
        first = extract_state.candidate_id_for(
            "s1", ["m1", "m2"], "内容", "tech", "new")
        second = extract_state.candidate_id_for(
            "s1", ["m1", "m2"], "内容", "tech", "new")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^cand-[0-9a-f]{20}$")
        self.assertNotEqual(first, extract_state.candidate_id_for(
            "s1", ["m1", "m2"], "内容改", "tech", "new"))
        self.assertNotEqual(first, extract_state.candidate_id_for(
            "s1", ["m1", "m2"], "内容", "goals", "new"))
        key1 = extract_state.evidence_key_for("s1", ["m2", "m1"], "同一 内容")
        key2 = extract_state.evidence_key_for("s1", ["m1", "m2"], "同一内容")
        self.assertEqual(key1, key2)          # 排序 + 归一化后一致
        self.assertEqual(len(key1), 64)

    def test_watermarks_missing_and_corrupt_rebuild(self):
        marks, rebuilt = extract_state.load_watermarks(self.data_dir)
        self.assertTrue(rebuilt)
        self.assertEqual(marks["sessions"], {})
        path = os.path.join(self.data_dir, extract_state.WATERMARKS_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{bad json")
        marks, rebuilt = extract_state.load_watermarks(self.data_dir)
        self.assertTrue(rebuilt)
        self.assertEqual(marks["sessions"], {})

    def test_watermark_save_load_atomic(self):
        marks = extract_state.empty_watermarks()
        marks["sessions"]["s1"] = {
            "path": "/x", "path_hash": "h", "last_seq": 7, "last_message_id": "m7",
            "last_event_sha256": "sha", "processed_user_messages": 3,
            "updated_at": "2026-11-15T00:00:00+08:00", "run_id": "run-1",
        }
        extract_state.save_watermarks(self.data_dir, marks)
        loaded, rebuilt = extract_state.load_watermarks(self.data_dir)
        self.assertFalse(rebuilt)
        self.assertEqual(loaded["sessions"]["s1"]["last_seq"], 7)
        self.assertEqual(loaded["sessions"]["s1"]["processed_user_messages"], 3)

    def test_rebuild_watermarks_from_fact_evidence(self):
        facts = [
            {"extraction": {"session_id": "s1", "message_ids": ["m1", "m2"],
                            "message_ordinals": [1, 2]}},
            {"extraction": {"session_id": "s1", "message_ids": ["m3"],
                            "message_ordinals": [3]}},
            {"extraction": None},
        ]
        candidates = [{"status": "approved", "session_id": "s2",
                       "message_ordinals": [1, 2],
                       "evidence": {"message_ids": ["a", "b"]}}]
        sessions = extract_state.rebuild_from_evidence(facts, candidates)
        self.assertEqual(sessions["s1"]["processed_user_messages"], 3)
        self.assertEqual(sessions["s1"]["last_message_id"], "m3")
        self.assertEqual(sessions["s2"]["processed_user_messages"], 2)
        self.assertTrue(sessions["s1"]["rebuilt"])

    def test_candidates_append_fold_and_filter(self):
        candidate = extract_state.new_candidate(
            "cand-" + "a" * 20, "s1", ["m1"], [1], "chunk", "run-1",
            "我喜欢 Python", "tech", "new", 0.9,
            {"mode": "full", "corpus_bytes": 0, "recalled_fact_ids": [],
             "historical_fact_ids": [], "truncated": False, "reasons": {}},
            {"action": "review", "reason_code": "auto_write_disabled"},
            extract_state.evidence_key_for("s1", ["m1"], "我喜欢 Python"))
        extract_state.append_candidate(self.data_dir, candidate)
        updated = dict(candidate)
        updated["status"] = "approved"
        updated["write"] = {"fact_id": "f1", "event_seq": 9}
        extract_state.append_candidate(self.data_dir, updated)
        listed = extract_state.list_candidates(self.data_dir, "pending")
        self.assertEqual(listed["items"], [])
        self.assertEqual(listed["counts"]["approved"], 1)
        folded = extract_state.get_candidate(self.data_dir, candidate["candidate_id"])
        self.assertEqual(folded["status"], "approved")
        all_items = extract_state.list_candidates(self.data_dir, "all")["items"]
        self.assertEqual(len(all_items), 1)

    def test_candidate_not_found(self):
        with self.assertRaises(errors.CoreError) as ctx:
            extract_state.get_candidate(self.data_dir, "cand-" + "0" * 20)
        self.assertEqual(ctx.exception.code, "EXTRACT_CANDIDATE_NOT_FOUND")

    def test_runs_append_and_limit(self):
        for index in range(3):
            extract_state.append_run(self.data_dir, {
                "run_id": "run-%d" % index, "trigger": "manual", "status": "ok",
                "counters": {"candidates_created": index}})
        runs = extract_state.load_runs(self.data_dir, 2)
        self.assertEqual([run["run_id"] for run in runs], ["run-2", "run-1"])
        self.assertEqual(extract_state.last_run(self.data_dir)["run_id"], "run-2")
        with open(os.path.join(self.data_dir, extract_state.RUNS_REL),
                  encoding="utf-8") as handle:
            raw = handle.read()
        self.assertNotIn("api_key", raw)
        self.assertNotIn("我喜欢", raw)

    def test_run_lock_exclusive(self):
        self.assertFalse(extract_state.is_running(self.data_dir))
        with extract_state.run_lock(self.data_dir):
            self.assertTrue(extract_state.is_running(self.data_dir))
            with self.assertRaises(errors.CoreError) as ctx:
                with extract_state.run_lock(self.data_dir):
                    pass
            self.assertEqual(ctx.exception.code, "EXTRACT_RUN_IN_PROGRESS")
        self.assertFalse(extract_state.is_running(self.data_dir))


if __name__ == "__main__":
    unittest.main()
