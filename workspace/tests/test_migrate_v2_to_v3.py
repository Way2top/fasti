"""v2 → v3 迁移脚本（ADR-026 / §13.5）：dry-run、原子写回、幂等、强回归。"""

import hashlib
import json
import os
import subprocess
import sys
import unittest

from _base import BaseStoreTest, ROOT, _sha
from fasti.core import api, store

SCRIPT = os.path.join(ROOT, "scripts", "migrate_v2_to_v3.py")


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True)


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TestMigrateV2ToV3(BaseStoreTest):
    def _make_v3_source(self):
        """生成 v3 store（含月精度外观的数据），返回事件字节与 store 路径。"""
        source = os.path.join(self.tmp, "source-v3")
        api.init_store(source)
        created = api.add_fact(source, "tech", "主力语言：Python", "2025-01")
        api.change_fact(source, created["fact"]["fact_id"], "evolve",
                        "主力语言：C++", "2026-11")
        api.add_category(source, "家庭情况", "family")
        api.add_fact(source, "family", "有一个弟弟", "2026-01")
        return source

    def _downgrade_to_v2(self, source, v2_dir):
        """把 v3 事件降级为 v2 形态（月份 valid_from、format_version=2）。"""
        os.makedirs(v2_dir, exist_ok=True)
        events = [json.loads(line) for line in
                  store.read_events_bytes(source).decode("utf-8").split("\n") if line]
        for event in events:
            if event["type"] == "store_initialized":
                event["data"]["format_version"] = 2
            elif event["type"] in ("fact_created", "fact_evolved"):
                event["data"]["valid_from"] = event["data"]["valid_from"][:7]
        with open(os.path.join(v2_dir, "events.jsonl"), "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False) + "\n")
        return v2_dir

    def test_dry_run_does_not_write(self):
        v2_dir = self._downgrade_to_v2(self._make_v3_source(),
                                       os.path.join(self.tmp, "v2-dry"))
        before = _sha256(os.path.join(v2_dir, "events.jsonl"))
        proc = _run("--data-dir", v2_dir, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("dry-run", proc.stdout)
        self.assertIn("2025-01  →  2025-01-01T00:00:00+08:00", proc.stdout)
        self.assertIn("format_version: 2 → 3", proc.stdout)
        self.assertEqual(_sha256(os.path.join(v2_dir, "events.jsonl")), before)

    def test_migrate_is_byte_identical_to_v3_and_display_preserved(self):
        """强回归：迁移后的 events 与等价 v3 store 逐字节一致；派生文本逐字节一致。"""
        source = self._make_v3_source()
        v2_dir = self._downgrade_to_v2(source, os.path.join(self.tmp, "v2-real"))
        manifest_dir = os.path.join(v2_dir, "state")
        os.makedirs(manifest_dir, exist_ok=True)
        with open(os.path.join(manifest_dir, "manifest.json"), "w", encoding="utf-8") as handle:
            handle.write("{\"stale\": true}\n")

        proc = _run("--data-dir", v2_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("迁移完成", proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(manifest_dir, "manifest.json")))
        self.assertEqual(store.read_events_bytes(v2_dir), store.read_events_bytes(source))
        first = json.loads(store.read_events_bytes(v2_dir).decode().split("\n")[0])
        self.assertEqual(first["data"]["format_version"], 3)

        for directory in (source, v2_dir):
            api.recompute(directory)
            api.render_hosts(directory, "all")
        for name in ("current.md", "history.md"):
            self.assertEqual(_sha(os.path.join(source, "profile", name)),
                             _sha(os.path.join(v2_dir, "profile", name)), name)
        for name in ("AGENTS.md", "CLAUDE.md", ".cursor/rules/fasti.mdc"):
            self.assertEqual(_sha(os.path.join(source, "dist", name)),
                             _sha(os.path.join(v2_dir, "dist", name)), name)
        # 月精度外观不变：历史文本仍是「2025-01 至 2026-10」
        history = api.history_text(api.get_history(v2_dir))
        self.assertIn("- 主力语言：Python（2025-01 至 2026-10）", history)
        # 同月演进能力恢复可用（auto-bump）
        head = api.list_facts(v2_dir)["items"][0]
        evolved = api.change_fact(v2_dir, head["fact_id"], "evolve", "主力语言：Java", "2026-11")
        self.assertTrue(evolved["auto_bumped"])

    def test_idempotent_second_run(self):
        v2_dir = self._downgrade_to_v2(self._make_v3_source(),
                                       os.path.join(self.tmp, "v2-idem"))
        self.assertEqual(_run("--data-dir", v2_dir).returncode, 0)
        before = _sha256(os.path.join(v2_dir, "events.jsonl"))
        proc = _run("--data-dir", v2_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("无需迁移", proc.stdout)
        self.assertEqual(_sha256(os.path.join(v2_dir, "events.jsonl")), before)

    def test_rejects_v1_store(self):
        v1_dir = os.path.join(self.tmp, "v1")
        os.makedirs(v1_dir)
        with open(os.path.join(v1_dir, "events.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"seq": 1, "at": "2026-11-09T10:00:00+08:00",
                                     "type": "store_initialized",
                                     "data": {"format_version": 1}},
                                    sort_keys=True, separators=(",", ":")) + "\n")
        before = _sha256(os.path.join(v1_dir, "events.jsonl"))
        proc = _run("--data-dir", v1_dir)
        self.assertEqual(proc.returncode, 5)
        self.assertIn("只迁移 v2", proc.stderr)
        self.assertEqual(_sha256(os.path.join(v1_dir, "events.jsonl")), before)

    def test_rejects_zero_length_line_atomically(self):
        source = self._make_v3_source()
        v2_dir = self._downgrade_to_v2(source, os.path.join(self.tmp, "v2-bad"))
        path = os.path.join(v2_dir, "events.jsonl")
        with open(path, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle.read().split("\n") if line]
        for event in events:
            if event["type"] == "fact_evolved":
                event["data"]["valid_from"] = \
                    [item["data"]["valid_from"] for item in events
                     if item["type"] == "fact_created"
                     and item["data"]["line_id"] == event["data"]["line_id"]][0]
        with open(path, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False) + "\n")
        before = _sha256(path)
        proc = _run("--data-dir", v2_dir)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("未严格递增", proc.stderr)
        self.assertEqual(_sha256(path), before)


if __name__ == "__main__":
    unittest.main()
