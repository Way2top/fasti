"""时间语义（月精度）与 v2 标识规则（UUIDv7 规范形式 / 短 ID / 自动类别 key）。"""

import re
import unittest

from fasti.core import clock, ids

UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class TestMonthAndIds(unittest.TestCase):
    def test_month_semantics(self):
        self.assertEqual(clock.normalize_month("2026-11"), "2026-11")
        self.assertEqual(clock.normalize_month("2026-11-09"), "2026-11")
        self.assertIsNone(clock.normalize_month("2026-13"))
        self.assertEqual(clock.display_end("2026-11"), "2026-10")
        self.assertEqual(clock.display_end("2026-01"), "2025-12")
        self.assertEqual(clock.display_end(None), "今")

    def test_ids_v2(self):
        ids.reset_generator()
        value = ids.new_uuid7()
        self.assertRegex(value, UUID7_RE)
        self.assertEqual(ids.short_id(value), value[:8])
        # 完整形式/大写/无连字符 均归一化为 32 位十六进制
        self.assertEqual(ids.normalize_reference(value),
                         value.replace("-", "").lower())
        self.assertEqual(ids.normalize_reference(value.upper()),
                         value.replace("-", "").lower())
        self.assertRegex(ids.auto_category_key(), r"^c-[0-9a-f]{8}$")
        self.assertTrue(ids.is_uuid7(value))
        self.assertFalse(ids.is_uuid7("fact-0001"))
        ids.reset_generator()


if __name__ == "__main__":
    unittest.main()
