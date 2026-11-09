"""AC-11-*/AC-12-*：导出 bundle 形状、确定性与导入还原（v2 format_version 2）。"""

import json
import os
import shutil
import unittest

from _base import BaseStoreTest, _sha
from fasti.core import api, errors, store


class TestExportImport(BaseStoreTest):
    def _populate(self):
        created = api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        self.fact_id = created["fact"]["fact_id"]
        self.line_id = created["line"]["line_id"]
        api.change_fact(self.data_dir, self.fact_id, "evolve", "主力语言：C++", "2026-11")
        api.add_category(self.data_dir, "家庭情况", "family")
        api.add_fact(self.data_dir, "family", "有一个弟弟", "2026-01")
        api.set_category_enabled(self.data_dir, "goals", False)

    def test_export_shape_and_determinism(self):
        self._populate()
        first = api.export_bundle(self.data_dir)
        second = api.export_bundle(self.data_dir)
        out1, out2 = first["output_dir"], second["output_dir"]
        self.assertEqual(first["format_version"], 2)
        concept = os.path.join(out1, "concepts", "tech", self.line_id + ".md")
        self.assertTrue(os.path.isfile(concept))
        for name in ("index.md", "profile.md", "log.md", "data/events.jsonl",
                     "data/entities.jsonl", "README.md",
                     "concepts/tech/%s.md" % self.line_id):
            self.assertEqual(_sha(os.path.join(out1, name)), _sha(os.path.join(out2, name)),
                             name)
        with open(os.path.join(out1, "index.md"), encoding="utf-8") as handle:
            index = handle.read()
        self.assertIn('okf_version: "0.1"', index)
        self.assertIn("type: index", index)
        with open(concept, encoding="utf-8") as handle:
            concept_text = handle.read()
        for field in ("type: fact-line", "fact_id:", "line_id:", "valid_from:",
                      "valid_to:", "category: tech"):
            self.assertIn(field, concept_text)
        with open(os.path.join(out1, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["format_version"], 2)
        self.assertEqual(manifest["payload"]["path"], "data/events.jsonl")
        self.assertEqual(manifest["payload"]["sha256"],
                         _sha(os.path.join(out1, "data", "events.jsonl")))

    def test_import_roundtrip(self):
        self._populate()
        exported = api.export_bundle(self.data_dir)
        target = os.path.join(self.tmp, "imported")
        result = api.import_bundle(target, exported["output_dir"], False, True)
        self.assertEqual(result["events_imported"], 13)
        self.assertEqual(store.read_events_bytes(target),
                         store.read_events_bytes(self.data_dir))
        self.assertIsNone(store.load_manifest(target))
        api.recompute(self.data_dir)
        api.render_hosts(self.data_dir, "all")
        api.recompute(target)
        api.render_hosts(target, "all")
        for name in ("current.md", "history.md"):
            self.assertEqual(_sha(os.path.join(self.data_dir, "profile", name)),
                             _sha(os.path.join(target, "profile", name)))
        self.assertEqual(_sha(os.path.join(self.data_dir, "dist", "AGENTS.md")),
                         _sha(os.path.join(target, "dist", "AGENTS.md")))

    def test_import_target_not_empty_requires_overwrite(self):
        self._populate()
        exported = api.export_bundle(self.data_dir)
        before = store.read_events_bytes(self.data_dir)
        with self.assertRaises(errors.CoreError) as ctx:
            api.import_bundle(self.data_dir, exported["output_dir"], False, True)
        self.assertEqual(ctx.exception.code, "CONFLICT_IMPORT_TARGET_NOT_EMPTY")
        self.assertIn("先备份", ctx.exception.message)
        self.assertIn("--overwrite", ctx.exception.message)
        self.assertEqual(store.read_events_bytes(self.data_dir), before)

    def test_import_rejects_broken_bundle(self):
        self._populate()
        exported = api.export_bundle(self.data_dir)
        bundle = exported["output_dir"]
        events_path = os.path.join(bundle, "data", "events.jsonl")
        with open(events_path, "rb") as handle:
            raw = handle.read()
        broken = raw.replace(b'"content":', b'"contnt":', 1)
        with open(events_path, "wb") as handle:
            handle.write(broken)
        target = os.path.join(self.tmp, "broken-target")
        with self.assertRaises(errors.CoreError) as ctx:
            api.import_bundle(target, bundle, False, True)
        self.assertEqual(ctx.exception.code, "IMPORT_INVALID_RECORD")
        self.assertTrue(ctx.exception.location.startswith("data/events.jsonl:"))
        self.assertFalse(os.path.exists(os.path.join(target, "events.jsonl")))

    def test_import_checksum_mismatch(self):
        self._populate()
        exported = api.export_bundle(self.data_dir)
        bundle = exported["output_dir"]
        events_path = os.path.join(bundle, "data", "events.jsonl")
        with open(events_path, "rb") as handle:
            raw = handle.read().replace("C++".encode(), "Rust".encode())
        with open(events_path, "wb") as handle:
            handle.write(raw)
        target = os.path.join(self.tmp, "checksum-target")
        with self.assertRaises(errors.CoreError) as ctx:
            api.import_bundle(target, bundle, False, True)
        self.assertEqual(ctx.exception.code, "IMPORT_PAYLOAD_CHECKSUM_MISMATCH")
        self.assertIn("校验失败", ctx.exception.message)

    def test_verify_deep(self):
        self._populate()
        api.recompute(self.data_dir)
        api.render_hosts(self.data_dir, "all")
        report = api.verify(self.data_dir, deep=True)
        self.assertTrue(report["ok"], report)
        statuses = {check["id"]: check["status"] for check in report["checks"]}
        self.assertEqual(statuses["sql.constraints"], "pass")
        self.assertEqual(statuses["derived.determinism"], "pass")
        self.assertEqual(statuses["derived.consistency"], "pass")
        self.assertEqual(statuses["export.roundtrip"], "pass")


if __name__ == "__main__":
    unittest.main()
