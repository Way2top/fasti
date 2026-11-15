"""C-09 M7：v3 → v4 纯加性迁移（dry-run/原子/幂等/旧目录拒绝/派生文本不变）。"""

import json
import os
import subprocess
import sys
import unittest

from _base import BaseStoreTest, ROOT, _sha
from fasti.core import api, errors, store

SCRIPT = os.path.join(ROOT, "scripts", "migrate_v3_to_v4.py")


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True)


class TestMigrateV3ToV4(BaseStoreTest):
    def _make_v4_source(self):
        source = os.path.join(self.tmp, "source-v4")
        api.init_store(source)
        created = api.add_fact(source, "tech", "主力语言：Python", "2025-01")
        api.change_fact(source, created["fact"]["fact_id"], "evolve",
                        "主力语言：C++", "2026-11")
        return source

    def _downgrade_to_v3(self, source, target):
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(source, "events.jsonl"), "r", encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        events[0]["data"]["format_version"] = 3
        with open(os.path.join(target, "events.jsonl"), "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False) + "\n")
        return target

    def test_v3_store_rejected_before_migration(self):
        v3_dir = self._downgrade_to_v3(self._make_v4_source(),
                                       os.path.join(self.tmp, "v3-reject"))
        with self.assertRaises(errors.CoreError) as ctx:
            api.list_facts(v3_dir)
        self.assertEqual(ctx.exception.code, "STORE_FORMAT_UNSUPPORTED")
        self.assertIn("迁移", ctx.exception.message)

    def test_dry_run_does_not_write(self):
        v3_dir = self._downgrade_to_v3(self._make_v4_source(),
                                       os.path.join(self.tmp, "v3-dry"))
        before = _sha(os.path.join(v3_dir, "events.jsonl"))
        proc = _run("--data-dir", v3_dir, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("format_version: 3 → 4", proc.stdout)
        self.assertIn("其余", proc.stdout)
        self.assertIn("dry-run", proc.stdout)
        self.assertEqual(_sha(os.path.join(v3_dir, "events.jsonl")), before)

    def test_migration_is_pure_additive_and_display_preserved(self):
        source = self._make_v4_source()
        v3_dir = self._downgrade_to_v3(source, os.path.join(self.tmp, "v3-real"))
        manifest_dir = os.path.join(v3_dir, "state")
        os.makedirs(manifest_dir, exist_ok=True)
        with open(os.path.join(manifest_dir, "manifest.json"), "w", encoding="utf-8") as handle:
            handle.write("{\"stale\": true}\n")
        with open(os.path.join(source, "events.jsonl"), "rb") as handle:
            source_lines = [line for line in handle.read().split(b"\n") if line]
        with open(os.path.join(v3_dir, "events.jsonl"), "rb") as handle:
            v3_lines = [line for line in handle.read().split(b"\n") if line]

        proc = _run("--data-dir", v3_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("迁移完成", proc.stdout)
        self.assertEqual(store.read_events_bytes(v3_dir), store.read_events_bytes(source))
        self.assertFalse(os.path.exists(os.path.join(manifest_dir, "manifest.json")))
        with open(os.path.join(v3_dir, "events.jsonl"), "rb") as handle:
            migrated_lines = [line for line in handle.read().split(b"\n") if line]
        self.assertEqual(len(migrated_lines), len(v3_lines))
        # 纯加性：除首事件 format_version 外，其余事件逐字节不变
        first = json.loads(migrated_lines[0])
        self.assertEqual(first["data"]["format_version"], 4)
        self.assertEqual(migrated_lines[1:], v3_lines[1:])
        self.assertEqual(api.get_status(v3_dir)["counts"]["facts"], 2)
        for directory in (source, v3_dir):
            api.recompute(directory)
            api.render_hosts(directory, "all")
        for name in ("current.md", "history.md"):
            self.assertEqual(_sha(os.path.join(source, "profile", name)),
                             _sha(os.path.join(v3_dir, "profile", name)), name)
        for name in ("AGENTS.md", "CLAUDE.md", ".cursor/rules/fasti.mdc"):
            self.assertEqual(_sha(os.path.join(source, "dist", name)),
                             _sha(os.path.join(v3_dir, "dist", name)), name)
        report = api.verify(v3_dir, deep=True)
        self.assertTrue(report["ok"], report)

    def test_idempotent_second_run(self):
        v3_dir = self._downgrade_to_v3(self._make_v4_source(),
                                       os.path.join(self.tmp, "v3-idem"))
        self.assertEqual(_run("--data-dir", v3_dir).returncode, 0)
        before = _sha(os.path.join(v3_dir, "events.jsonl"))
        proc = _run("--data-dir", v3_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("无需迁移", proc.stdout)
        self.assertEqual(_sha(os.path.join(v3_dir, "events.jsonl")), before)

    def test_v2_store_rejected(self):
        v2_dir = os.path.join(self.tmp, "v2")
        os.makedirs(v2_dir)
        with open(os.path.join(v2_dir, "events.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"seq": 1, "at": "2026-11-15T00:00:00+08:00",
                                     "type": "store_initialized",
                                     "data": {"format_version": 2}},
                                    sort_keys=True, separators=(",", ":")) + "\n")
        before = _sha(os.path.join(v2_dir, "events.jsonl"))
        proc = _run("--data-dir", v2_dir)
        self.assertEqual(proc.returncode, 5)
        self.assertIn("只迁移 v3", proc.stderr)
        self.assertEqual(_sha(os.path.join(v2_dir, "events.jsonl")), before)


if __name__ == "__main__":
    unittest.main()
