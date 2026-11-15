"""C-09 M4：确定性召回（full/retrieval、关闭类别、历史段按需、评分原因、确定性）。"""

import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import api, clock, constants, extract_recall, fold, ids, store


def _model(data_dir):
    return fold.fold_events(store.read_events(data_dir))


def _append_facts(data_dir, contents, category_key="tech"):
    """直接追加 fact_created 事件（绕开 C-08 检测，仅用于构造大语料）。"""
    events = store.read_events(data_dir)
    at = clock.now_recorded_at()
    for index, content in enumerate(contents):
        fact_id = ids.new_uuid7()
        line_id = ids.new_uuid7()
        valid_from = clock.format_timestamp(
            clock.now_local().replace(microsecond=0).replace(year=2020 + index // 100)
            + __import__("datetime").timedelta(days=index))
        events.append({
            "seq": len(events) + 1, "at": at, "type": "fact_created",
            "data": {"fact_id": fact_id, "line_id": line_id,
                     "category_key": category_key, "content": content,
                     "valid_from": valid_from, "source": "user_entry",
                     "recorded_at": at},
        })
    store.write_events(data_dir, events)
    return events


class TestRecall(BaseStoreTest):
    def test_full_mode_includes_disabled_and_no_history_by_default(self):
        api.add_fact(self.data_dir, "goals", "长期目标：通过华为考试", "2026-11")
        api.set_category_enabled(self.data_dir, "goals", False)
        api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        model = _model(self.data_dir)
        per, summary = extract_recall.recall_for_candidates(model, [
            {"candidate_id": "cand-" + "a" * 20, "content": "我的主力语言是 Python",
             "category_key": "tech"},
        ])
        result = per["cand-" + "a" * 20]
        self.assertEqual(result["mode"], "full")
        self.assertEqual(summary["mode"], "full")
        joined = "\n".join(result["lines"])
        self.assertIn("cat=tech", joined)
        self.assertIn("cat=goals|disabled", joined)          # 含已关闭类别当前值
        self.assertEqual(result["historical_count"], 0)
        self.assertIn("full_mode", result["reasons"][result["recalled_fact_ids"][0]])
        self.assertGreater(result["corpus_bytes"], 0)
        self.assertFalse(result["truncated"])

    def test_historical_only_when_needed(self):
        created = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")["fact"]
        api.change_fact(self.data_dir, created["fact_id"], "evolve", "主力语言：C++", "2026-11")
        model = _model(self.data_dir)
        # 普通 new 候选：不放历史段
        plain, _ = extract_recall.recall_for_candidates(model, [
            {"candidate_id": "cand-" + "b" * 20, "content": "数据库：了解 MySQL",
             "category_key": "tech", "change_type": "new"}])
        self.assertEqual(plain["cand-" + "b" * 20]["historical_count"], 0)
        # correct/evolve 候选：追加历史段（historical=true）
        updated, _ = extract_recall.recall_for_candidates(model, [
            {"candidate_id": "cand-" + "c" * 20, "content": "主力语言：Python",
             "category_key": "tech", "change_type": "correct"}])
        result = updated["cand-" + "c" * 20]
        self.assertGreaterEqual(result["historical_count"], 1)
        self.assertGreaterEqual(len(result["historical_fact_ids"]), 1)
        historical_line = [line for line in result["lines"] if "| historical |" in line]
        self.assertTrue(historical_line)
        self.assertIn("line=", historical_line[0])
        self.assertIn("historical_line",
                      result["reasons"][result["historical_fact_ids"][0]])

    def test_retrieval_mode_caps_and_truncates(self):
        contents = ["记录编号%03d：%s" % (index, "内容片段" * 60) for index in range(45)]
        _append_facts(self.data_dir, contents)
        model = _model(self.data_dir)
        per, summary = extract_recall.recall_for_candidates(model, [
            {"candidate_id": "cand-" + "d" * 20, "content": "记录编号001：内容片段",
             "category_key": "tech"},
        ])
        result = per["cand-" + "d" * 20]
        self.assertEqual(result["mode"], "retrieval")
        self.assertGreater(summary["corpus_bytes"], constants.EXTRACT_RECALL_FULL_BYTES)
        self.assertLessEqual(result["current_count"], constants.EXTRACT_RECALL_TOP_K)
        line_bytes = sum(len(line.encode("utf-8")) for line in result["lines"])
        self.assertLessEqual(line_bytes, constants.EXTRACT_RECALL_MAX_BYTES
                             + constants.EXTRACT_HISTORICAL_MAX_BYTES)
        self.assertTrue(result["truncated"])

    def test_deterministic(self):
        api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        model = _model(self.data_dir)
        candidates = [{"candidate_id": "cand-" + "e" * 20,
                       "content": "我的主力语言是 Python", "category_key": "tech"}]
        first, summary_a = extract_recall.recall_for_candidates(model, candidates)
        second, summary_b = extract_recall.recall_for_candidates(model, candidates)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))
        self.assertEqual(summary_a, summary_b)

    def test_one_line_format(self):
        model = _model(self.data_dir)
        entries = extract_recall.current_entries(model)
        self.assertEqual(entries, [])
        created = api.add_fact(self.data_dir, "tech", "x" * 200, "2026-11")["fact"]
        line = extract_recall.one_line(extract_recall.current_entries(_model(self.data_dir))[0])
        self.assertIn("id=" + ids.short_id(created["fact_id"]), line)
        self.assertIn("cat=tech", line)
        self.assertIn("| current |", line)
        self.assertIn("from=2026-11", line)
        self.assertTrue(line.endswith("…"))                   # >120 字截断
        self.assertLessEqual(len(line), 200)


if __name__ == "__main__":
    unittest.main()
