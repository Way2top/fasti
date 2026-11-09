"""UUIDv7 生成器与短前缀解析（C-06 / ADR-023）单元测试。

确定性：通过 `ids.set_generator(Uuid7Generator(clock_ms=..., random_bytes=...))` 注入
固定时钟/随机源，测试不依赖真实时间；每个用例结束恢复默认生成器。
"""

import re
import unittest

from _base import ROOT  # noqa: F401  （确保 workspace 在 sys.path）
from fasti.core import errors, ids

CANONICAL = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class _SeqClock(object):
    """按预设序列返回毫秒时钟；序列耗尽后返回 default。"""

    def __init__(self, values, default):
        self.values = list(values)
        self.default = default

    def __call__(self):
        if self.values:
            return self.values.pop(0)
        return self.default


def _fake_generator(clock_values, default_ms, rand_byte=0xAB):
    return ids.Uuid7Generator(clock_ms=_SeqClock(clock_values, default_ms),
                              random_bytes=lambda n: bytes([rand_byte]) * n)


class TestUuid7Generator(unittest.TestCase):
    def setUp(self):
        ids.reset_generator()

    def tearDown(self):
        ids.reset_generator()

    def test_canonical_format_and_version_variant(self):
        generated = [ids.new_uuid7() for _ in range(200)]
        self.assertEqual(len(generated), len(set(generated)))  # 唯一
        for value in generated:
            self.assertEqual(len(value), 36)
            self.assertTrue(CANONICAL.match(value), value)
            self.assertEqual(value, value.lower())
            self.assertEqual(value[14], "7")          # 版本位
            self.assertIn(value[19], "89ab")          # 变体位

    def test_same_millisecond_lexicographic_increasing(self):
        ids.set_generator(_fake_generator([], default_ms=1_700_000_000_000))
        generated = [ids.new_uuid7() for _ in range(200)]
        self.assertEqual(generated, sorted(generated))
        self.assertEqual(len(set(generated)), 200)

    def test_cross_millisecond_time_order(self):
        ids.set_generator(_fake_generator([1_700_000_000_000, 1_700_000_000_001,
                                           1_700_000_000_002], 1_700_000_000_002))
        a, b, c = ids.new_uuid7(), ids.new_uuid7(), ids.new_uuid7()
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_rand_a_overflow_waits_next_millisecond(self):
        # rand_a 初值 0x0FFF → 同毫秒第 2 次生成溢出，生成器必须等到下一毫秒
        generator = ids.Uuid7Generator(
            clock_ms=_SeqClock([1000, 1000, 1001], 1001),
            random_bytes=lambda n: b"\xff" * n,
        )
        ids.set_generator(generator)
        first = ids.new_uuid7()
        second = ids.new_uuid7()
        self.assertTrue(CANONICAL.match(first) and CANONICAL.match(second))
        self.assertLess(first, second)  # 溢出后进入下一毫秒，字典序仍递增

    def test_short_id_and_auto_category_key(self):
        value = "0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b"
        self.assertEqual(ids.short_id(value), "0190a1b23c4d")
        self.assertTrue(ids.SHORT_ID_RE.match("0190a1b23c4d"))
        self.assertFalse(ids.SHORT_ID_RE.match("0190a1b2"))
        self.assertTrue(ids.is_uuid7(value))
        self.assertFalse(ids.is_uuid7("0190a1b2-3c4d-6e5f-8a9b-0c1d2e3f4a5b"))
        key = ids.auto_category_key()
        self.assertRegex(key, r"^c-[0-9a-f]{12}$")
        self.assertEqual(len(key), 2 + ids.SHORT_ID_LEN)


class TestReferenceParsing(unittest.TestCase):
    IDS = [
        "0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b",
        "019ba1b2-1111-7222-8333-444455556666",
    ]

    def test_normalize_accepts_full_and_short(self):
        self.assertEqual(ids.normalize_reference(self.IDS[0]),
                         "0190a1b23c4d7e5f8a9b0c1d2e3f4a5b")
        self.assertEqual(ids.normalize_reference(self.IDS[0].upper()),
                         "0190a1b23c4d7e5f8a9b0c1d2e3f4a5b")
        self.assertEqual(ids.normalize_reference(self.IDS[0].replace("-", "")),
                         "0190a1b23c4d7e5f8a9b0c1d2e3f4a5b")
        self.assertEqual(ids.normalize_reference("0190"), "0190")

    def test_normalize_rejects_invalid(self):
        for value in ("abc", "zzzz", "0190a1b2-3c4d-6e5f-8a9b-0c1d2e3f4a5b",
                      "0" * 33):
            with self.assertRaises(errors.CoreError) as ctx:
                ids.normalize_reference(value)
            self.assertEqual(ctx.exception.code, "VALIDATION_INVALID_ID", value)
            self.assertEqual(ctx.exception.exit_code, 4)

    def test_unique_prefix_resolves(self):
        first = ids.short_id(self.IDS[0])
        self.assertEqual(ids.resolve_reference(self.IDS, first, kind="fact",
                                               not_found_code="NOT_FOUND_FACT"),
                         self.IDS[0])
        self.assertEqual(ids.resolve_reference(self.IDS, "019b", kind="fact",
                                               not_found_code="NOT_FOUND_FACT"),
                         self.IDS[1])

    def test_not_found(self):
        with self.assertRaises(errors.CoreError) as ctx:
            ids.resolve_reference(self.IDS, "ffff", kind="fact",
                                  not_found_code="NOT_FOUND_FACT")
        self.assertEqual(ctx.exception.code, "NOT_FOUND_FACT")
        self.assertEqual(ctx.exception.exit_code, 3)

    def test_ambiguous_prefix_lists_candidates(self):
        shared = [
            "0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b",
            "0190c3d4-1111-7222-8333-444455556666",
        ]
        ambiguous = "0190"  # 两个 ID 共享前 4 位十六进制
        with self.assertRaises(errors.CoreError) as ctx:
            ids.resolve_reference(shared, ambiguous, kind="fact",
                                  not_found_code="NOT_FOUND_FACT")
        self.assertEqual(ctx.exception.code, "VALIDATION_AMBIGUOUS_REFERENCE")
        details = ctx.exception.details
        self.assertLessEqual(len(details["candidates"]), 10)
        self.assertEqual(len(details["candidates"]), 2)

    def test_invalid_input_raises_validation_invalid_id(self):
        with self.assertRaises(errors.CoreError) as ctx:
            ids.resolve_reference(self.IDS, "xyz", kind="fact",
                                  not_found_code="NOT_FOUND_FACT")
        self.assertEqual(ctx.exception.code, "VALIDATION_INVALID_ID")


if __name__ == "__main__":
    unittest.main()
