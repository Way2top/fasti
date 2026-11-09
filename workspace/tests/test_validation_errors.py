"""错误码与退出码映射（v2：UUIDv7 引用解析 + VALIDATION_INVALID_ID）。"""

import unittest

from _base import BaseStoreTest
from fasti.core import api, errors


class TestValidationErrors(BaseStoreTest):
    def assert_error(self, code, func, *args, **kwargs):
        with self.assertRaises(errors.CoreError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_missing_valid_from(self):
        exc = self.assert_error("VALIDATION_MISSING_VALID_FROM", api.add_fact,
                                self.data_dir, "tech", "x", None)
        self.assertIn("从何时起为真", exc.message)
        self.assertEqual(exc.exit_code, 4)

    def test_missing_content_and_multiline(self):
        self.assert_error("VALIDATION_MISSING_CONTENT", api.add_fact,
                          self.data_dir, "tech", "  ", "2026-11")
        self.assert_error("VALIDATION_CONTENT_MULTILINE", api.add_fact,
                          self.data_dir, "tech", "a\nb", "2026-11")

    def test_invalid_month(self):
        self.assert_error("VALIDATION_INVALID_MONTH", api.add_fact,
                          self.data_dir, "tech", "x", "2026-13")

    def test_change_type_required(self):
        fact_id = api.add_fact(self.data_dir, "tech", "x", "2026-11")["fact"]["fact_id"]
        exc = self.assert_error("VALIDATION_CHANGE_TYPE_REQUIRED", api.change_fact,
                                self.data_dir, fact_id, None, "y", None)
        self.assertIn("必须选择变更类型", exc.message)
        self.assertIn("演进", exc.message)
        self.assertIn("纠正", exc.message)

    def test_correct_must_not_change_range(self):
        fact_id = api.add_fact(self.data_dir, "tech", "x", "2026-11")["fact"]["fact_id"]
        self.assert_error("VALIDATION_UNEXPECTED_VALID_FROM", api.change_fact,
                          self.data_dir, fact_id, "correct", "y", "2026-12")

    def test_evolve_target_must_be_head(self):
        first = api.add_fact(self.data_dir, "tech", "x", "2025-01")["fact"]["fact_id"]
        api.change_fact(self.data_dir, first, "evolve", "y", "2026-01")
        self.assert_error("VALIDATION_EVOLVE_TARGET_NOT_HEAD", api.change_fact,
                          self.data_dir, first, "evolve", "z", "2027-01")

    def test_evolve_month_must_advance(self):
        first = api.add_fact(self.data_dir, "tech", "x", "2026-11")["fact"]["fact_id"]
        exc = self.assert_error("VALIDATION_VALID_FROM_NOT_AFTER_PREVIOUS", api.change_fact,
                                self.data_dir, first, "evolve", "y", "2026-11")
        self.assertIn("必须晚于", exc.message)

    def test_category_state_checks(self):
        api.set_category_enabled(self.data_dir, "tech", False)
        self.assert_error("VALIDATION_CATEGORY_NOT_ENABLED", api.add_fact,
                          self.data_dir, "tech", "x", "2026-11")
        self.assert_error("NOT_FOUND_CATEGORY", api.add_fact,
                          self.data_dir, "nope", "x", "2026-11")

    def test_delete_twice(self):
        fact_id = api.add_fact(self.data_dir, "tech", "x", "2026-11")["fact"]["fact_id"]
        api.delete_fact(self.data_dir, fact_id)
        self.assert_error("VALIDATION_FACT_ALREADY_DELETED", api.delete_fact,
                          self.data_dir, fact_id)

    def test_category_delete_requires_handling(self):
        api.add_fact(self.data_dir, "tech", "x", "2026-11")
        self.assert_error("VALIDATION_INVALID_HANDLING", api.delete_category,
                          self.data_dir, "tech", None, None)
        self.assert_error("VALIDATION_MISSING_MIGRATE_TARGET", api.delete_category,
                          self.data_dir, "tech", "migrate", None)


if __name__ == "__main__":
    unittest.main()
