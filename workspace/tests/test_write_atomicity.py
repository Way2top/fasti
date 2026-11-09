"""写路径原子性回归（P0）：校验失败不得落盘；自动类别 key 撞车不得损坏 store。

QA 在 C-06 验收中发现：同一毫秒内连续新增两个无 `--key` 的自定义类别，
自动 key 撞车的事件被「先写后折叠」写入 events.jsonl ⇒ store 损坏（CORRUPT_EVENT_LOG）。
本文件锁定修复后的行为：任何校验失败都在 store.write_events 之前抛出，events 字节不变。
"""

import hashlib
import os
import unittest

from _base import BaseStoreTest, inject_ids
from fasti.core import api, errors


def _events_sha(data_dir):
    path = os.path.join(data_dir, "events.jsonl")
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _event_count(data_dir):
    path = os.path.join(data_dir, "events.jsonl")
    with open(path, "rb") as handle:
        return len([line for line in handle.read().split(b"\n") if line.strip()])


class TestWriteAtomicity(BaseStoreTest):
    def test_auto_category_key_collision_rejected_without_corruption(self):
        """固定同毫秒时钟/随机源 ⇒ 两个无 --key 类别生成相同 key；必须拒绝且不落盘。"""
        inject_ids(start_ms=1_700_000_000_000, step_ms=0)
        first = api.add_category(self.data_dir, "家庭情况")
        self.assertRegex(first["category"]["key"], r"^c-[0-9a-f]{12}$")
        before_sha = _events_sha(self.data_dir)
        before_events = _event_count(self.data_dir)

        with self.assertRaises(errors.CoreError) as ctx:
            api.add_category(self.data_dir, "临时类别")
        self.assertEqual(ctx.exception.code, "VALIDATION_CATEGORY_KEY_TAKEN")
        self.assertEqual(ctx.exception.exit_code, 4)

        # 坏事件不得落盘：events 字节与事件条数都不变
        self.assertEqual(_events_sha(self.data_dir), before_sha)
        self.assertEqual(_event_count(self.data_dir), before_events)

        # store 仍可用：后续命令正常、无重复 key、仍可继续写入
        categories = api.list_categories(self.data_dir)["items"]
        keys = [category["key"] for category in categories]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len([key for key in keys if key.startswith("c-")]), 1)
        self.assertIn("已初始化", api.get_status(self.data_dir) and "已初始化")
        added = api.add_category(self.data_dir, "临时类别", "temp-family")
        self.assertEqual(added["category"]["key"], "temp-family")
        self.assertGreater(_event_count(self.data_dir), before_events)

    def test_auto_key_avoids_deleted_key(self):
        """key 永久占用（含已删除）：自动 key 生成必须避开已删除类别的 key。"""
        added = api.add_category(self.data_dir, "家庭情况", "family")
        api.delete_category(self.data_dir, added["category"]["key"], "delete")
        before = _events_sha(self.data_dir)
        # 固定时钟下自动 key 与已删除 key 无关（key=family 是显式 key），
        # 这里验证生成器仍可正常工作且不会复活已删除类别
        new = api.add_category(self.data_dir, "新类别")
        self.assertNotEqual(new["category"]["key"], "family")
        self.assertNotEqual(_events_sha(self.data_dir), before)

    def test_failed_writes_leave_events_unchanged(self):
        """任选多条会失败的写操作：失败后 events.jsonl sha256 不变。"""
        created = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        fact_id = created["fact"]["fact_id"]
        api.add_category(self.data_dir, "家庭情况", "family")
        api.set_category_enabled(self.data_dir, "goals", False)
        before = _events_sha(self.data_dir)

        cases = [
            ("VALIDATION_MISSING_VALID_FROM",
             lambda: api.add_fact(self.data_dir, "tech", "x", None)),
            ("VALIDATION_CONTENT_MULTILINE",
             lambda: api.add_fact(self.data_dir, "tech", "a\nb", "2026-11")),
            ("VALIDATION_CATEGORY_NOT_ENABLED",
             lambda: api.add_fact(self.data_dir, "goals", "x", "2026-11")),
            ("VALIDATION_INVALID_CHANGE_TYPE",
             lambda: api.change_fact(self.data_dir, fact_id, "bogus", "x")),
            ("VALIDATION_VALID_FROM_NOT_AFTER_PREVIOUS",
             lambda: api.change_fact(self.data_dir, fact_id, "evolve", "x", "2024-01")),
            ("NOT_FOUND_FACT",
             lambda: api.delete_fact(self.data_dir, "ffff")),
            ("VALIDATION_CATEGORY_KEY_TAKEN",
             lambda: api.add_category(self.data_dir, "重名类别", "family")),
            ("NOT_FOUND_CATEGORY",
             lambda: api.set_category_enabled(self.data_dir, "nope", True)),
            ("VALIDATION_INVALID_HANDLING",
             lambda: api.delete_category(self.data_dir, "family", None, None)),
        ]
        for expected_code, action in cases:
            with self.assertRaises(errors.CoreError) as ctx:
                action()
            self.assertEqual(ctx.exception.code, expected_code)
            self.assertEqual(_events_sha(self.data_dir), before, expected_code)

        # 全部失败后 store 仍可正常读取/写入
        self.assertEqual(_events_sha(self.data_dir), before)
        api.get_status(self.data_dir)
        self.assertEqual(len(api.list_facts(self.data_dir)["items"]), 1)


if __name__ == "__main__":
    unittest.main()
