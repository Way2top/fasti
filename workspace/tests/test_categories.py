"""AC-10-1/10-2/10-3/10-5 + 类别复活（v2：key 为身份，永久保留）。"""

import unittest

from _base import BaseStoreTest, reset_ids, inject_ids
from fasti.core import api, errors


class TestCategories(BaseStoreTest):
    def test_add_disable_enable_delete(self):
        added = api.add_category(self.data_dir, "家庭情况")
        key = added["category"]["key"]
        self.assertTrue(key.startswith("c-"))                       # c-<UUIDv7 前12位>
        self.assertRegex(key, r"^c-[0-9a-f]{12}$")          # 例：c-0190a1b23c4d
        self.assertEqual(added["category"]["origin"], "custom")
        self.assertIsNone(added["category"]["template_order"])
        api.add_fact(self.data_dir, "家庭情况", "有一个弟弟", "2026-01")
        api.set_category_enabled(self.data_dir, key, False)
        text = api.profile_text(api.get_profile(self.data_dir))
        self.assertNotIn("家庭情况", text)
        # 关闭后事实仍可查询（AC-10-1）
        self.assertEqual(len(api.list_facts(self.data_dir, "家庭情况")["items"]), 1)
        api.set_category_enabled(self.data_dir, key, True)
        self.assertIn("有一个弟弟",
                      api.profile_text(api.get_profile(self.data_dir)))
        # 删除 + 迁移（目标用 key）
        result = api.delete_category(self.data_dir, key, "migrate", "tech")
        self.assertEqual(result["affected_facts"], 1)
        view = api.get_profile(self.data_dir)
        self.assertEqual([item["content"] for item in view["items"]], ["有一个弟弟"])
        self.assertEqual(view["items"][0]["category_key"], "tech")
        # 历史保留类别删除动作（AC-10-3）
        kinds = [event["kind"] for event in api.get_history(self.data_dir)["category_events"]]
        self.assertIn("deleted", kinds)

    def test_order_template_then_custom(self):
        api.add_category(self.data_dir, "家庭情况")
        ordered = api.list_categories(self.data_dir)["items"]
        self.assertEqual([category["key"] for category in ordered[:2]], ["basics", "tech"])
        self.assertTrue(ordered[-1]["key"].startswith("c-"))

    def test_category_key_revival(self):
        """同 key 删除后重新新增 = 复活：读模型重置、历史事件保留（不得退化成新建）。"""
        added = api.add_category(self.data_dir, "家庭情况", "family")
        key = added["category"]["key"]
        self.assertEqual(key, "family")
        api.add_fact(self.data_dir, "family", "有一个弟弟", "2026-01")
        api.delete_category(self.data_dir, "family", "delete")

        deleted = api.list_categories(self.data_dir)["items"]
        row = [category for category in deleted if category["key"] == key][0]
        self.assertIsNotNone(row["deleted_at"])

        # 同 key 重新新增（复活）；提供新显示名与启用状态
        revived = api.add_category(self.data_dir, "家庭（新）", "family", True)
        self.assertEqual(revived["category"]["key"], key)
        self.assertIsNone(revived["category"]["deleted_at"])
        self.assertEqual(revived["category"]["display_name"], "家庭（新）")
        self.assertTrue(revived["category"]["enabled"])

        categories = api.list_categories(self.data_dir)["items"]
        family_rows = [category for category in categories if category["key"] == "family"]
        self.assertEqual(len(family_rows), 1)                       # 永远只有一行/key
        # 历史事件保留：add → delete → add（复活）
        events = [event for event in api.get_history(self.data_dir)["category_events"]
                  if event["category_key"] == "family"]
        kinds = [event["kind"] for event in events]
        self.assertEqual(kinds.count("added"), 2)
        self.assertEqual(kinds.count("deleted"), 1)
        # 复活不改变原本已删除事实的墓碑（历史保留）
        deleted_facts = api.list_facts(self.data_dir, "family", None, "deleted")["items"]
        self.assertEqual(len(deleted_facts), 1)

    def test_key_taken_only_by_alive_category(self):
        api.add_category(self.data_dir, "家庭情况", "family")
        with self.assertRaises(errors.CoreError) as ctx:
            api.add_category(self.data_dir, "家庭2", "family")
        self.assertEqual(ctx.exception.code, "VALIDATION_CATEGORY_KEY_TAKEN")


if __name__ == "__main__":
    unittest.main()
