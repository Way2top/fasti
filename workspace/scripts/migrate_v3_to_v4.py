#!/usr/bin/env python3
"""Fasti v3 → v4 一次性事件改写器（ADR-031 / architecture §15.10）。

C-09 自动提取：事件/导出 format_version 3 → 4（新增 source=auto_extract 与可选 extraction）。
**纯加性**：只把首事件 store_initialized.data.format_version 改为 4，其余事件逐字节不变；
既有事实 source 保持 user_entry、extraction 缺失（读模型为 null）。
候选/水位/运行记录（state/extract/）为过程状态，不迁移。

安全：原子写回（临时文件 + fsync + os.replace）；校验失败不触碰 events.jsonl；
--dry-run 只打印计划；已是 v4 则幂等跳过；成功后删除 state/manifest.json。
迁移前请先备份数据目录（建议再导出一份 v3 bundle）。
"""

import argparse
import json
import os
import sys
import tempfile


class MigrationError(Exception):
    def __init__(self, message, exit_code=5):
        super().__init__(message)
        self.exit_code = exit_code


def load_events(data_dir):
    path = os.path.join(data_dir, "events.jsonl")
    if not os.path.isfile(path):
        raise MigrationError("数据目录没有 events.jsonl：%s" % data_dir)
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise MigrationError("events.jsonl:%d 不是合法 JSON：%s"
                                     % (line_number, exc))
            if not isinstance(event, dict):
                raise MigrationError("events.jsonl:%d 事件不是 JSON 对象" % line_number)
            events.append(event)
    if not events:
        raise MigrationError("events.jsonl 为空")
    return events


def plan_migration(events):
    first = events[0]
    data = first.get("data") if isinstance(first, dict) else None
    version = data.get("format_version") if isinstance(data, dict) else None
    if first.get("type") != "store_initialized" or not isinstance(version, int):
        raise MigrationError("首事件不是 store_initialized 或缺少 format_version")
    if version == 4:
        return None
    if version != 3:
        raise MigrationError("首事件 format_version=%r，本脚本只迁移 v3 → v4" % (version,))
    migrated = []
    for event in events:
        copied = json.loads(json.dumps(event, ensure_ascii=False))
        if copied.get("type") == "store_initialized":
            copied["data"]["format_version"] = 4
        migrated.append(copied)
    # 纯加性断言：除首事件 format_version 外，其余事件与原文件完全等价
    expected = json.loads(json.dumps(first, ensure_ascii=False))
    expected["data"]["format_version"] = 4
    if json.loads(json.dumps(migrated[0], ensure_ascii=False)) != expected:
        raise MigrationError("迁移计划意外修改了首事件其它字段")
    if migrated[1:] != events[1:]:
        raise MigrationError("迁移计划意外修改了其它事件")
    return migrated


def write_atomic(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".events.", suffix=".tmp", delete=False)
    tmp_path = handle.name
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fasti v3 → v4 一次性事件改写器（自动提取审计字段，C-09 / ADR-031）")
    parser.add_argument("--data-dir", required=True,
                        help="v3 数据目录（如 ~/.fasti 的副本；必须先备份）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验并打印计划，不写回")
    args = parser.parse_args(argv)
    try:
        events = load_events(args.data_dir)
        migrated = plan_migration(events)
        if migrated is None:
            print("已是 format_version 4，无需迁移（幂等跳过）：%s" % args.data_dir)
            return 0
        print("v3 → v4 迁移计划（数据目录：%s）" % os.path.abspath(args.data_dir))
        print("  format_version: 3 → 4；仅修改首事件，其余 %d 条事件不变"
              % (len(events) - 1))
        if args.dry_run:
            print("dry-run：未写回 events.jsonl（如确认无误，去掉 --dry-run 重跑）")
            return 0
        text = "".join(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False) + "\n" for event in migrated)
        write_atomic(os.path.join(args.data_dir, "events.jsonl"), text)
        manifest = os.path.join(args.data_dir, "state", "manifest.json")
        if os.path.exists(manifest):
            os.unlink(manifest)
            print("已删除派生状态 state/manifest.json（请运行 fasti recompute / render）")
        print("迁移完成：%d 条事件 → format_version 4；请运行 recompute / render / verify --deep。"
              % len(migrated))
        return 0
    except MigrationError as exc:
        sys.stderr.write("迁移失败：%s\n" % exc)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
