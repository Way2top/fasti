"""US-1/US-10：初始化、事件日志规范化字节与 v2 格式（UUIDv7）。"""

import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import api, constants, errors, store

TEMPLATE_KEYS = ["basics", "tech", "experience", "projects", "communication",
                 "state", "goals"]


class TestInitAndEncoding(BaseStoreTest):
    def test_default_templates(self):
        categories = api.list_categories(self.data_dir)["items"]
        self.assertEqual(len(categories), 7)
        self.assertEqual([category["key"] for category in categories], TEMPLATE_KEYS)
        self.assertEqual([category["template_order"] for category in categories],
                         [1, 2, 3, 4, 5, 6, 7])
        self.assertTrue(all(category["enabled"] for category in categories))
        self.assertTrue(all(category["deleted_at"] is None for category in categories))
        self.assertTrue(all("category_id" not in category for category in categories))

    def test_events_are_canonical_jsonl_v2(self):
        api.add_fact(self.data_dir, "tech", "主力语言：C++", "2026-11")
        path = os.path.join(self.data_dir, "events.jsonl")
        with open(path, "rb") as handle:
            raw = handle.read()
        self.assertTrue(raw.endswith(b"\n"))
        lines = raw.decode("utf-8").split("\n")[:-1]
        self.assertEqual(len(lines), 9)  # init + 7 category_added + fact_created
        for index, line in enumerate(lines, start=1):
            event = json.loads(line)
            self.assertEqual(event["seq"], index)
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False)
            self.assertEqual(line, canonical)
        first = json.loads(lines[0])
        self.assertEqual(first["type"], "store_initialized")
        self.assertEqual(first["data"]["format_version"], 2)
        self.assertEqual(constants.FORMAT_VERSION, 2)
        category_event = json.loads(lines[1])
        self.assertEqual(category_event["type"], "category_added")
        self.assertEqual(category_event["data"]["category_key"], "basics")
        self.assertNotIn("category_id", category_event["data"])

    def test_init_twice_rejected(self):
        with self.assertRaises(errors.CoreError) as ctx:
            api.init_store(self.data_dir)
        self.assertEqual(ctx.exception.code, "CONFLICT_STORE_EXISTS")
        self.assertEqual(ctx.exception.exit_code, 5)

    def test_store_not_initialized(self):
        empty = os.path.join(self.tmp, "empty")
        with self.assertRaises(errors.CoreError) as ctx:
            api.get_status(empty)
        self.assertEqual(ctx.exception.code, "STORE_NOT_INITIALIZED")
        self.assertIn("先运行 fasti init", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
