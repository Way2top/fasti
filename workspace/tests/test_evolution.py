"""AC-3-1/3-2/3-4/4-1~4-4/1-4：演进 vs 纠正、回溯与删除（v2 UUIDv7）。"""

import unittest

from _base import BaseStoreTest
from fasti.core import api, fold, store


class TestEvolutionVsCorrection(BaseStoreTest):
    def setUp(self):
        super().setUp()
        created = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        self.fact_id = created["fact"]["fact_id"]
        self.line_id = created["line"]["line_id"]

    def test_evolve_then_correct(self):
        evolved = api.change_fact(self.data_dir, api.short_id(self.fact_id), "evolve",
                                  "主力语言：C++", "2026-11")
        self.assertEqual(evolved["change_type"], "evolve")
        new_fact = evolved["fact"]
        self.assertNotEqual(new_fact["fact_id"], self.fact_id)      # 演进产生新 fact_id
        self.assertEqual(new_fact["line_id"], self.line_id)          # 同 line
        self.assertEqual(new_fact["previous_fact_id"], self.fact_id)
        self.assertEqual(new_fact["valid_from"], "2026-11")
        self.assertEqual(new_fact["change_type"], "evolve")

        # 纠正只作用于旧段：区间不变、fact_id 不变、revision_seq + 1
        corrected = api.change_fact(self.data_dir, api.short_id(self.fact_id), "correct",
                                    "主力语言：Java", None, "2025-01~2026-10 实际是 Java")
        self.assertEqual(corrected["change_type"], "correct")
        self.assertEqual(corrected["fact"]["fact_id"], self.fact_id)
        self.assertEqual(corrected["fact"]["valid_from"], "2025-01")
        self.assertEqual(corrected["revision"]["seq"], 2)
        revisions = api.show_fact(self.data_dir, self.fact_id)["revisions"]
        self.assertEqual([revision["seq"] for revision in revisions], [1, 2])
        self.assertEqual([revision["revision_type"] for revision in revisions],
                         ["initial", "correct"])
        self.assertEqual(revisions[0]["content"], "主力语言：Python")
        self.assertEqual(revisions[1]["content"], "主力语言：Java")
        self.assertNotIn("revision_id", revisions[0])               # v2 无独立修订 ID

        # 当前画像 = C++（head 段最新修订）
        profile = api.get_profile(self.data_dir)
        self.assertEqual([item["content"] for item in profile["items"]],
                         ["主力语言：C++"])
        # 时点回溯 2026-06 = 纠正后的 Java（valid-time + 当前已知知识）
        at_view = api.get_profile_at(self.data_dir, "2026-06")
        self.assertEqual([item["content"] for item in at_view["items"]],
                         ["主力语言：Java"])
        self.assertIn("基于当前已知事实", at_view["notices"][0])
        # 区间：Python/Java 段显示 2025-01 至 2026-10
        history = api.get_history(self.data_dir)
        entries = history["groups"][0]["entries"]
        old = [entry for entry in entries if entry["fact_id"] == self.fact_id][0]
        self.assertEqual(old["display_end"], "2026-10")
        self.assertEqual(old["corrections"][0]["old_content"], "主力语言：Python")
        history_text = api.history_text(history)
        self.assertNotIn("- 主力语言：Python（", history_text)

    def test_at_no_record(self):
        self.assertEqual(api.get_profile_at(self.data_dir, "2024-01")["items"], [])
        self.assertEqual(api.profile_at_text(api.get_profile_at(self.data_dir, "2024-01")),
                         "该时点无记录\n")

    def test_delete_fact_tombstone(self):
        api.delete_fact(self.data_dir, api.short_id(self.fact_id))
        self.assertEqual(api.get_profile(self.data_dir)["items"], [])
        deleted = api.list_facts(self.data_dir, None, None, "deleted")["items"]
        self.assertEqual([fact["fact_id"] for fact in deleted], [self.fact_id])
        model = fold.fold_events(store.read_events(self.data_dir))
        self.assertIn("已删除", api.render_history_body(model))


if __name__ == "__main__":
    unittest.main()
