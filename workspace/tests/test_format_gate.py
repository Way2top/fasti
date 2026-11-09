"""格式门（ADR-024）：v1 数据目录与 v1 导出包必须被明确拒绝，且给迁移指引。"""

import hashlib
import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import api, errors

V1_EVENTS = [
    {"seq": 1, "at": "2026-11-09T10:00:00+08:00",
     "type": "store_initialized",
     "data": {"format_version": 1, "app_name": "Fasti", "app_version": "0.1.0"}},
    {"seq": 2, "at": "2026-11-09T10:00:00+08:00",
     "type": "category_added",
     "data": {"category_id": "cat-0001", "key": "basics", "display_name": "基本背景",
              "origin": "template", "template_order": 1, "enabled": True,
              "created_at": "2026-11-09T10:00:00+08:00"}},
]


def _write_v1_store(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "events.jsonl"), "w", encoding="utf-8") as handle:
        for event in V1_EVENTS:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False) + "\n")


def _write_bundle(bundle_dir, format_version, events=V1_EVENTS):
    os.makedirs(os.path.join(bundle_dir, "data"), exist_ok=True)
    raw = ("\n".join(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False) for event in events) + "\n").encode("utf-8")
    with open(os.path.join(bundle_dir, "data", "events.jsonl"), "wb") as handle:
        handle.write(raw)
    manifest = {
        "bundle_version": 1,
        "format_version": format_version,
        "exported_at": "2026-11-09T10:00:00+08:00",
        "counts": {"categories": 1, "lines": 0, "facts": 0, "revisions": 0,
                   "events": len(events)},
        "payload": {"path": "data/events.jsonl", "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw)},
        "files": [],
    }
    with open(os.path.join(bundle_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False) + "\n")


class TestFormatGate(BaseStoreTest):
    def test_new_store_is_format_version_2(self):
        with open(os.path.join(self.data_dir, "events.jsonl"), encoding="utf-8") as handle:
            first = json.loads(handle.readline())
        self.assertEqual(first["data"]["format_version"], 2)

    def test_v1_store_rejected_with_migration_guidance(self):
        v1_dir = os.path.join(self.tmp, "v1-store")
        _write_v1_store(v1_dir)
        for command in (lambda: api.get_status(v1_dir),
                        lambda: api.get_profile(v1_dir),
                        lambda: api.list_facts(v1_dir)):
            with self.assertRaises(errors.CoreError) as ctx:
                command()
            self.assertEqual(ctx.exception.code, "STORE_FORMAT_UNSUPPORTED")
            self.assertEqual(ctx.exception.exit_code, 5)
            self.assertIn("迁移", ctx.exception.message)
            self.assertIn("§12.5", ctx.exception.message)
            self.assertIn("2", ctx.exception.message)

    def test_verify_on_v1_store_raises_format_error(self):
        v1_dir = os.path.join(self.tmp, "v1-store-verify")
        _write_v1_store(v1_dir)
        with self.assertRaises(errors.CoreError) as ctx:
            api.verify(v1_dir)
        self.assertEqual(ctx.exception.code, "STORE_FORMAT_UNSUPPORTED")
        self.assertEqual(ctx.exception.exit_code, 5)

    def test_v1_bundle_rejected(self):
        bundle = os.path.join(self.tmp, "v1-bundle")
        _write_bundle(bundle, 1)
        target = os.path.join(self.tmp, "import-target")
        with self.assertRaises(errors.CoreError) as ctx:
            api.import_bundle(target, bundle, False, True)
        self.assertEqual(ctx.exception.code, "IMPORT_UNSUPPORTED_FORMAT_VERSION")
        self.assertEqual(ctx.exception.exit_code, 4)
        self.assertIn("UUIDv7 迁移", ctx.exception.message)
        self.assertIn("当前支持版本 2", ctx.exception.message)
        self.assertFalse(os.path.exists(os.path.join(target, "events.jsonl")))

    def test_future_bundle_version_rejected(self):
        bundle = os.path.join(self.tmp, "v3-bundle")
        _write_bundle(bundle, 3)
        target = os.path.join(self.tmp, "import-target-3")
        with self.assertRaises(errors.CoreError) as ctx:
            api.import_bundle(target, bundle, False, True)
        self.assertEqual(ctx.exception.code, "IMPORT_UNSUPPORTED_FORMAT_VERSION")
        self.assertEqual(ctx.exception.exit_code, 4)

    def test_export_manifest_is_format_version_2(self):
        api.add_fact(self.data_dir, "tech", "x", "2026-11")
        exported = api.export_bundle(self.data_dir)
        with open(os.path.join(exported["output_dir"], "manifest.json"),
                  encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["format_version"], 2)
        self.assertEqual(exported["format_version"], 2)


if __name__ == "__main__":
    unittest.main()
