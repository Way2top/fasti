"""边界守卫（契约硬约束）：短 ID 不得出现在任何持久化/导出内容中。

短 ID（UUIDv7 前 8 位十六进制）仅用于 CLI/交互显示与输入；一旦出现在 events/profile/dist/
导出 bundle 中即判 fail。完整 UUIDv7 规范形式允许出现在事件、entities 与 concept frontmatter。
"""

import os
import re
import unittest

from _base import BaseStoreTest
from fasti.core import api, ids

UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
UUID7_ANY_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _short_token_re(short):
    return re.compile(r"(?<![0-9a-f-])%s(?![0-9a-f-])" % re.escape(short))


class TestIdPersistenceGuard(BaseStoreTest):
    def _populate_and_export(self):
        first = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        fact_id = first["fact"]["fact_id"]
        line_id = first["line"]["line_id"]
        api.change_fact(self.data_dir, fact_id, "evolve", "主力语言：C++", "2026-11")
        api.change_fact(self.data_dir, fact_id, "correct", "主力语言：Java", None, "误记")
        api.add_category(self.data_dir, "家庭情况", "family")
        api.add_fact(self.data_dir, "family", "有一个弟弟", "2026-01")
        api.recompute(self.data_dir)
        api.render_hosts(self.data_dir, "all")
        exported = api.export_bundle(self.data_dir)
        return [fact_id, line_id], exported["output_dir"]

    def _collect_texts(self, export_dir):
        paths = [
            os.path.join(self.data_dir, "events.jsonl"),
            os.path.join(self.data_dir, "profile", "current.md"),
            os.path.join(self.data_dir, "profile", "history.md"),
            os.path.join(self.data_dir, "dist", "AGENTS.md"),
            os.path.join(self.data_dir, "dist", "CLAUDE.md"),
            os.path.join(self.data_dir, "dist", ".cursor", "rules", "fasti.mdc"),
            os.path.join(self.data_dir, "dist", "INSTALL.md"),
        ]
        for dirpath, _dirnames, filenames in os.walk(export_dir):
            for name in filenames:
                paths.append(os.path.join(dirpath, name))
        texts = {}
        for path in paths:
            with open(path, "r", encoding="utf-8") as handle:
                texts[path] = handle.read()
        return texts

    def test_no_short_ids_in_persisted_or_exported_content(self):
        id_list, export_dir = self._populate_and_export()
        shorts = sorted({ids.short_id(value) for value in id_list})
        self.assertTrue(shorts)
        texts = self._collect_texts(export_dir)
        self.assertTrue(any(path.endswith("events.jsonl") for path in texts))
        for path, text in texts.items():
            for short in shorts:
                self.assertIsNone(_short_token_re(short).search(text),
                                  "短 ID %s 出现在持久化/导出内容中：%s" % (short, path))

    def test_events_and_entities_use_full_canonical_uuid7(self):
        id_list, export_dir = self._populate_and_export()
        with open(os.path.join(self.data_dir, "events.jsonl"), encoding="utf-8") as handle:
            events_text = handle.read()
        with open(os.path.join(export_dir, "data", "entities.jsonl"),
                  encoding="utf-8") as handle:
            entities_text = handle.read()
        for text, label in ((events_text, "events.jsonl"),
                            (entities_text, "entities.jsonl")):
            for value in UUID7_ANY_RE.findall(text):
                self.assertRegex(value, UUID7_RE, label)
        for value in id_list:
            self.assertIn(value, events_text)
        # v1 字段名不得残留
        for legacy in ("category_id", "revision_id", "line-0", "fact-0", "cat-0"):
            self.assertNotIn(legacy, events_text)
            self.assertNotIn(legacy, entities_text)

    def test_profile_and_host_artifacts_contain_no_ids(self):
        _id_list, export_dir = self._populate_and_export()
        paths = [
            os.path.join(self.data_dir, "profile", "current.md"),
            os.path.join(self.data_dir, "profile", "history.md"),
            os.path.join(self.data_dir, "dist", "AGENTS.md"),
            os.path.join(self.data_dir, "dist", "CLAUDE.md"),
            os.path.join(self.data_dir, "dist", ".cursor", "rules", "fasti.mdc"),
        ]
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(UUID7_ANY_RE.findall(text), [], path)


if __name__ == "__main__":
    unittest.main()
