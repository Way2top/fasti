"""时间语义（C-07 / ADR-025，秒级）：三档输入归一 / 自适应显示 / 瞬间比较 / 同桶 auto-bump。"""

import unittest
from datetime import datetime, timedelta, timezone

from fasti.core import clock, errors

NOW = datetime(2026, 11, 10, 0, 5, 0, tzinfo=timezone(timedelta(hours=8)))
HEAD = "2026-11-01T00:00:00+08:00"


class TestTimeSemantics(unittest.TestCase):
    def setUp(self):
        clock.set_now_provider(lambda: NOW)

    def tearDown(self):
        clock.reset_now_provider()

    def test_three_tier_normalization(self):
        cases = [
            ("2026-11", "2026-11-01T00:00:00+08:00", "month"),
            ("2026-11-09", "2026-11-09T00:00:00+08:00", "day"),
            ("2026-11-09T23:50", "2026-11-09T23:50:00+08:00", "second"),
            ("2026-11-09T23:50:05", "2026-11-09T23:50:05+08:00", "second"),
        ]
        for text, expected, precision in cases:
            normalized = clock.normalize_valid_time(text, NOW)
            self.assertEqual(normalized["value"], expected)
            self.assertEqual(normalized["precision"], precision)

    def test_invalid_inputs(self):
        for text in ("2026-13", "2026-02-30", "2026-11-31", "2026-11-09T25:00",
                     "2026-11-09T00:00:00+08:00", "2026-11-09T00:00:00Z",
                     "2026-11-09T00:00:00.123", "2026/11", "2026-11-9", ""):
            with self.assertRaises(errors.CoreError) as ctx:
                clock.normalize_valid_time(text, NOW)
            self.assertEqual(ctx.exception.code, "VALIDATION_INVALID_VALID_FROM")
            self.assertIn("YYYY-MM", ctx.exception.message)
            self.assertIn("YYYY-MM-DDTHH:MM[:SS]", ctx.exception.message)

    def test_invalid_at_uses_at_code(self):
        with self.assertRaises(errors.CoreError) as ctx:
            clock.normalize_valid_time("2026-99", NOW, field="at",
                                       invalid_code="VALIDATION_INVALID_AT")
        self.assertEqual(ctx.exception.code, "VALIDATION_INVALID_AT")

    def test_display_time_adaptive(self):
        cases = [
            ("2026-11-01T00:00:00+08:00", "2026-11"),
            ("2026-11-09T00:00:00+08:00", "2026-11-09"),
            ("2026-11-09T23:50:05+08:00", "2026-11-09T23:50:05"),
            ("2025-01-01T00:00:00+08:00", "2025-01"),
        ]
        for value, expected in cases:
            self.assertEqual(clock.display_time(value), expected)

    def test_display_end_adaptive(self):
        self.assertEqual(clock.display_end(None), "今")
        self.assertEqual(clock.display_end("2026-11-01T00:00:00+08:00"), "2026-10")
        self.assertEqual(clock.display_end("2026-01-01T00:00:00+08:00"), "2025-12")
        self.assertEqual(clock.display_end("2026-11-09T00:00:00+08:00"), "2026-11-08")
        self.assertEqual(clock.display_end("2026-11-10T12:00:00+08:00"),
                         "2026-11-10T11:59:59")
        self.assertEqual(clock.display_end("2026-11-10T00:04:59+08:00"),
                         "2026-11-10T00:04:58")

    def test_display_input_at(self):
        self.assertEqual(clock.display_input_at("2026-11"), "2026-11")
        self.assertEqual(clock.display_input_at("2026-11-09"), "2026-11-09")
        self.assertEqual(clock.display_input_at("2026-11-09T23:50"),
                         "2026-11-09T23:50:00")
        self.assertEqual(clock.display_input_at("2026-11-09T23:50:05"),
                         "2026-11-09T23:50:05")

    def test_compare_by_instant_not_string(self):
        # 同一瞬间、不同偏移：字符串不同但瞬间相等
        self.assertEqual(clock.compare("2026-11-01T00:00:00+08:00",
                                       "2026-10-31T16:00:00+00:00"), 0)
        # 字符串比较会得出相反结论，按瞬间比较必须正确
        self.assertEqual(clock.compare("2026-11-01T00:00:00+08:00",
                                       "2026-10-31T23:00:00+08:00"), 1)

    def test_resolve_auto_bump_same_month(self):
        resolved = clock.resolve_valid_from("2026-11", NOW, head_value=HEAD)
        self.assertTrue(resolved["auto_bumped"])
        self.assertEqual(resolved["value"], "2026-11-10T00:05:00+08:00")
        self.assertGreater(clock.compare(resolved["value"], HEAD), 0)

    def test_resolve_does_not_bump_explicit_or_earlier_bucket(self):
        explicit = clock.resolve_valid_from("2026-12-01T00:00:00", NOW, head_value=HEAD)
        self.assertFalse(explicit["auto_bumped"])
        self.assertEqual(explicit["value"], "2026-12-01T00:00:00+08:00")
        earlier = clock.resolve_valid_from("2025-06", NOW, head_value=HEAD)
        self.assertFalse(earlier["auto_bumped"])
        self.assertEqual(earlier["value"], "2025-06-01T00:00:00+08:00")
        later = clock.resolve_valid_from("2026-12", NOW, head_value=HEAD)
        self.assertFalse(later["auto_bumped"])

    def test_resolve_without_head_never_bumps(self):
        resolved = clock.resolve_valid_from("2026-11", NOW, head_value=None)
        self.assertFalse(resolved["auto_bumped"])
        self.assertEqual(resolved["value"], "2026-11-01T00:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
