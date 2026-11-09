#!/usr/bin/env python3
"""Fasti v2 → v3 一次性事件改写器（ADR-026 / architecture.md §13.5）。

背景：C-07 把生效时间精度从「月」升级为「秒级完整时间戳」，format_version 2 → 3。
本脚本只改写事件日志（唯一真相），派生文件由后续 recompute/render 重建：

- 校验首事件 store_initialized.format_version == 2（已是 3 则幂等跳过）；
- 逐条 fact_created / fact_evolved：
  `valid_from = "<YYYY-MM>-01T00:00:00" + offset(该事件 recorded_at 的偏移)`；
- store_initialized.format_version → 3；其余字段/事件/seq 原样；
- 按瞬间校验同 line 生效时间严格递增；（v2 同 line 同月唯一 ⇒ 迁移后天然递增）
- **原子写回**（临时文件 + fsync + os.replace）：任何校验失败都不触碰 events.jsonl；
- 成功后删除 state/manifest.json（派生状态需重建）；
- 支持 --dry-run（只打印变更表，不写回）。

安全提示：迁移前请**先备份数据目录**（建议再导出一份 v2 bundle），并先在副本上演练。
本脚本不修改 ~/.fasti 之外的任何路径；也不自动备份（避免隐藏副作用）。
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime

MONTH_RE = re.compile(r"^([0-9]{4})-([0-9]{2})$")
OFFSET_RE = re.compile(r"^[+-][0-9]{2}:[0-9]{2}$")
TS_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$")
FACT_TYPES = ("fact_created", "fact_evolved")


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
                raise MigrationError("events.jsonl:%d 不是合法 JSON：%s" % (line_number, exc))
            if not isinstance(event, dict):
                raise MigrationError("events.jsonl:%d 事件不是 JSON 对象" % line_number)
            events.append(event)
    if not events:
        raise MigrationError("events.jsonl 为空")
    return events


def _offset_of(event):
    data = event.get("data") or {}
    recorded_at = data.get("recorded_at")
    if not isinstance(recorded_at, str) or len(recorded_at) < 6 \
            or not OFFSET_RE.match(recorded_at[-6:]):
        raise MigrationError(
            "seq %r 的 recorded_at 缺少合法偏移，无法确定迁移偏移：%r"
            % (event.get("seq"), recorded_at))
    return recorded_at[-6:]


def _month_to_valid_from(month, offset):
    match = MONTH_RE.match(month) if isinstance(month, str) else None
    if not match:
        raise MigrationError("valid_from 不是 v2 月格式（YYYY-MM）：%r" % (month,))
    try:
        datetime(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        raise MigrationError("valid_from 不是合法月份（YYYY-MM）：%r" % (month,))
    return "%s-01T00:00:00%s" % (month, offset)


def _instant(value):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise MigrationError("不是合法 RFC3339 时间戳：%r" % (value,))


def plan_migration(events):
    """校验并返回 (改写后 events, 变更表)。不修改输入之外的任何文件。"""
    first = events[0]
    data = first.get("data") if isinstance(first, dict) else None
    version = data.get("format_version") if isinstance(data, dict) else None
    if first.get("type") != "store_initialized" or not isinstance(version, int):
        raise MigrationError("首事件不是 store_initialized 或缺少 format_version")
    if version == 3:
        return None  # 幂等：已是 v3
    if version != 2:
        raise MigrationError("首事件 format_version=%r，本脚本只迁移 v2 → v3" % (version,))

    changes = []
    migrated = []
    for event in events:
        event = json.loads(json.dumps(event, ensure_ascii=False))  # 深拷贝
        data = event.get("data") or {}
        if event.get("type") == "store_initialized":
            data["format_version"] = 3
        elif event.get("type") in FACT_TYPES:
            old = data.get("valid_from")
            new = _month_to_valid_from(old, _offset_of(event))
            changes.append((event.get("seq"), old, new))
            data["valid_from"] = new
        migrated.append(event)

    # 按瞬间校验同 line 严格递增（事件顺序内）
    per_line = {}
    for event in migrated:
        if event.get("type") not in FACT_TYPES:
            continue
        data = event.get("data") or {}
        per_line.setdefault(data.get("line_id"), []).append(
            (event.get("seq"), data.get("valid_from")))
    for line_id, entries in per_line.items():
        previous = None
        for seq, value in entries:
            if not TS_RE.match(str(value or "")):
                raise MigrationError("seq %r 的 valid_from 迁移后不是完整时间戳：%r"
                                     % (seq, value))
            current = _instant(value)
            if previous is not None and current <= previous:
                raise MigrationError(
                    "命题线 %s 的生效时间未严格递增（seq %r：%r）"
                    % (line_id, seq, value))
            previous = current
    return migrated, changes


def write_atomic(path, text):
    """同目录临时文件 + fsync + os.replace（失败不破坏原文件）。"""
    directory = os.path.dirname(os.path.abspath(path))
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, prefix=".events.", suffix=".tmp",
        delete=False)
    tmp_path = handle.name
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp_path, path)
    except Exception:
        try:
            handle.close()
        except Exception:
            pass
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
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
        description="Fasti v2 → v3 一次性事件改写器（秒级生效时间，C-07 / ADR-026）")
    parser.add_argument("--data-dir", required=True,
                        help="v2 数据目录（如 ~/.fasti 的副本；必须先备份）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验并打印变更表，不写回")
    args = parser.parse_args(argv)

    try:
        events = load_events(args.data_dir)
        planned = plan_migration(events)
        if planned is None:
            print("已是 format_version 3，无需迁移（幂等跳过）：%s" % args.data_dir)
            return 0
        migrated, changes = planned
        print("v2 → v3 迁移计划（数据目录：%s）" % os.path.abspath(args.data_dir))
        print("  format_version: 2 → 3；改写事实段 valid_from：%d 条" % len(changes))
        for seq, old, new in changes:
            print("  seq %-4s %s  →  %s" % (seq, old, new))
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
        print("迁移完成：%d 条事件 → format_version 3；请运行 recompute / render / verify --deep。"
              % len(migrated))
        return 0
    except MigrationError as exc:
        sys.stderr.write("迁移失败：%s\n" % exc)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
