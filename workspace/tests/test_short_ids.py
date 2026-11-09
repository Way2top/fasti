"""短 ID 输入解析（ADR-023）：唯一前缀 / 歧义 / 非法 / 0 命中；禁止静默取第一个（AS-19）。"""

import unittest

from _base import BaseStoreTest, inject_ids, reset_ids
from fasti.core import api, errors


class TestShortIds(BaseStoreTest):
    def _create_spaced_facts(self):
        """注入稀疏时钟，使各 fact 的 8 位前缀互不相同（UUIDv7 前 8 位 ≈ 65 秒窗口）。"""
        inject_ids(start_ms=1_700_000_000_000, step_ms=70_000)
        first = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        second = api.add_fact(self.data_dir, "tech", "常用框架：Django", "2024-06")
        return [first["fact"]["fact_id"], second["fact"]["fact_id"]]

    def test_unique_prefix_resolves(self):
        fact_ids = self._create_spaced_facts()
        for fact_id in fact_ids:
            short = fact_id[:8]
            detail = api.show_fact(self.data_dir, short)
            self.assertEqual(detail["fact"]["fact_id"], fact_id)
            self.assertEqual(detail["fact"]["fact_id"][:8], short)
        # 大写/带连字符/完整形式同样可解析
        self.assertEqual(api.show_fact(self.data_dir, fact_ids[0].upper())["fact"]["fact_id"],
                         fact_ids[0])
        self.assertEqual(api.show_fact(self.data_dir, fact_ids[0].replace("-", ""))["fact"]["fact_id"],
                         fact_ids[0])

    def test_ambiguous_prefix_lists_candidates(self):
        reset_ids()
        inject_ids(start_ms=1_700_000_000_000, step_ms=0)   # 同毫秒生成 → 前缀共享
        first = api.add_fact(self.data_dir, "tech", "x", "2025-01")["fact"]["fact_id"]
        second = api.add_fact(self.data_dir, "tech", "y", "2025-02")["fact"]["fact_id"]
        prefix = first.replace("-", "")[:4]
        with self.assertRaises(errors.CoreError) as ctx:
            api.show_fact(self.data_dir, prefix)
        self.assertEqual(ctx.exception.code, "VALIDATION_AMBIGUOUS_REFERENCE")
        details = ctx.exception.details
        self.assertGreaterEqual(len(details["candidates"]), 2)
        self.assertLessEqual(len(details["candidates"]), 10)
        self.assertIn(first[:8], details["candidates"])
        self.assertIn(second[:8], details["candidates"])

    def test_invalid_id_format(self):
        fact_id = api.add_fact(self.data_dir, "tech", "x", "2026-11")["fact"]["fact_id"]
        for value in ("abc", "zzzz", "not-an-id", "0" * 33,
                      "0190a1b2-3c4d-6e5f-8a9b-0c1d2e3f4a5b"):
            with self.assertRaises(errors.CoreError) as ctx:
                api.show_fact(self.data_dir, value)
            self.assertEqual(ctx.exception.code, "VALIDATION_INVALID_ID", value)
            self.assertEqual(ctx.exception.exit_code, 4)
        self.assertEqual(api.show_fact(self.data_dir, fact_id[:8])["fact"]["fact_id"],
                         fact_id)

    def test_not_found_prefix(self):
        api.add_fact(self.data_dir, "tech", "x", "2026-11")
        with self.assertRaises(errors.CoreError) as ctx:
            api.show_fact(self.data_dir, "ffff")
        self.assertEqual(ctx.exception.code, "NOT_FOUND_FACT")
        self.assertEqual(ctx.exception.exit_code, 3)
        with self.assertRaises(errors.CoreError) as ctx:
            api.list_facts(self.data_dir, None, "ffff", "current")
        self.assertEqual(ctx.exception.code, "NOT_FOUND_LINE")

    def test_line_filter_by_short_prefix(self):
        created = api.add_fact(self.data_dir, "tech", "x", "2026-11")
        line_id = created["line"]["line_id"]
        items = api.list_facts(self.data_dir, None, line_id[:8], "current")["items"]
        self.assertEqual([fact["fact_id"] for fact in items], [created["fact"]["fact_id"]])


if __name__ == "__main__":
    unittest.main()
