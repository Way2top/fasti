"""导入（C8）：全量校验 + 原子替换 + 非空显式覆盖 + 自动备份（ADR-013）。

流水线（architecture.md §4.2，任何一步失败 ⇒ 现有数据一行不动）：
1. 解析 manifest.json
2. 逐行解析 data/events.jsonl（结构校验）
3. 纯函数折叠 + 领域不变量
4. 内存 SQLite 执行 database-schema.sql 约束
5. 原始字节 sha256 与 manifest.payload.sha256 比对
6. 目标非空 ⇒ 必须 --overwrite（并默认先备份）
7. 原子替换 events.jsonl（写入校验通过的原始字节），删除 state/manifest.json
8. 不自动 recompute/render
"""

import json
import os

from . import clock
from . import constants
from . import errors
from . import fold
from . import store
from . import validate

EVENTS_REL = "data/events.jsonl"
MANIFEST_REL = "manifest.json"


def _invalid_manifest(message, location=None, details=None):
    return errors.make("IMPORT_INVALID_MANIFEST", message, details=details, location=location)


def _read_manifest(bundle_dir):
    path = os.path.join(bundle_dir, MANIFEST_REL)
    if not os.path.isfile(path):
        raise _invalid_manifest("导入 bundle 缺少 manifest.json", location=path)
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise _invalid_manifest("manifest.json 不可解析：%s" % exc, location=path)
    if not isinstance(manifest, dict):
        raise _invalid_manifest("manifest.json 不是 JSON 对象", location=path)
    for key in ("bundle_version", "format_version", "exported_at", "counts", "payload", "files"):
        if key not in manifest:
            raise _invalid_manifest("manifest.json 缺少字段 %s" % key, location=path,
                                    details={"field": key})
    format_version = manifest.get("format_version")
    if not isinstance(format_version, int) or isinstance(format_version, bool):
        raise _invalid_manifest("manifest.format_version 必须是整数", location=path,
                                details={"field": "format_version"})
    if format_version != constants.FORMAT_VERSION:
        raise errors.make(
            "IMPORT_UNSUPPORTED_FORMAT_VERSION",
            "不支持的导出格式版本 %d（当前支持版本 %d）：旧格式（v1/v2）导出包需先迁移"
            "（v2→v3 见 architecture.md §13.5：运行 scripts/migrate_v2_to_v3.py），"
            "再用当前版本重新导出"
            % (format_version, constants.FORMAT_VERSION),
            details={"format_version": format_version,
                     "supported": constants.FORMAT_VERSION,
                     "migration": "architecture.md §13.5"},
            location=path,
        )
    payload = manifest.get("payload")
    if not isinstance(payload, dict) or not payload.get("sha256"):
        raise _invalid_manifest("manifest.payload 缺少 sha256", location=path,
                                details={"field": "payload.sha256"})
    if payload.get("path") != EVENTS_REL:
        raise _invalid_manifest("manifest.payload.path 必须是 %s" % EVENTS_REL, location=path,
                                details={"field": "payload.path", "value": payload.get("path")})
    return manifest


def _read_payload(bundle_dir):
    path = os.path.join(bundle_dir, EVENTS_REL)
    if not os.path.isfile(path):
        raise _invalid_manifest("导入 bundle 缺少 %s" % EVENTS_REL, location=path)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise errors.make("IO_READ_FAILED", "读取 %s 失败：%s" % (EVENTS_REL, exc), location=path)


def _parse_records(raw):
    """逐行解析 + 结构校验。返回 (events, line_numbers)。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise errors.make("IMPORT_INVALID_RECORD", "data/events.jsonl 不是合法 UTF-8：%s" % exc,
                          location="%s:1" % EVENTS_REL)
    events = []
    line_numbers = []
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise errors.make(
                "IMPORT_INVALID_RECORD",
                "data/events.jsonl 第 %d 行结构不合法：不是合法 JSON（%s）" % (line_number, exc),
                details={"field": "（整行）"},
                location="%s:%d" % (EVENTS_REL, line_number),
            )
        problems = validate.validate_event_record(event)
        if problems:
            first = problems[0]
            raise errors.make(
                "IMPORT_INVALID_RECORD",
                "data/events.jsonl 第 %d 行结构不合法：%s %s"
                % (line_number, first["field"], first["message"]),
                details={"field": first["field"], "message": first["message"],
                         "problems": problems[:5]},
                location="%s:%d" % (EVENTS_REL, line_number),
            )
        events.append(event)
        line_numbers.append(line_number)
    return events, line_numbers


def _semantic_error(message, location="data/events.jsonl", details=None):
    return errors.make("IMPORT_SEMANTIC_INVALID", message, details=details, location=location)


def _fold_and_validate(events, line_numbers):
    try:
        model = fold.fold_events(events)
    except errors.CoreError as exc:
        location = "data/events.jsonl"
        seq = None
        if exc.location and exc.location.startswith("event seq="):
            try:
                seq = int(exc.location.split("=", 1)[1].strip())
            except ValueError:
                seq = None
        if seq is not None and 1 <= seq <= len(line_numbers):
            location = "%s:%d" % (EVENTS_REL, line_numbers[seq - 1])
        raise _semantic_error(exc.message, location=location,
                             details={"reason": exc.message})
    problems = validate.check_invariants(model)
    if problems:
        raise _semantic_error("导入事件语义校验失败：%s" % problems[0],
                             details={"problems": problems[:5]})
    ok, detail = validate.check_sql_constraints(model)
    if not ok:
        raise _semantic_error("导入事件语义校验失败：%s" % detail, details={"reason": detail})
    return model


def _target_non_empty(data_dir):
    if not store.is_initialized(data_dir):
        return False
    try:
        raw = store.read_events_bytes(data_dir)
    except errors.CoreError:
        return True
    return bool(raw.strip())


def import_bundle(data_dir, bundle_dir, overwrite, backup):
    data_dir = os.path.abspath(data_dir)
    bundle_dir = store.expand_home(bundle_dir)
    if not os.path.isdir(bundle_dir):
        raise errors.make("NOT_FOUND_PATH", "导入 bundle 目录不存在：%s" % bundle_dir,
                          location=bundle_dir)

    manifest = _read_manifest(bundle_dir)            # 1
    raw = _read_payload(bundle_dir)
    events, line_numbers = _parse_records(raw)       # 2
    model = _fold_and_validate(events, line_numbers)  # 3 + 4

    expected = manifest["payload"]["sha256"]         # 5
    actual = store.sha256_hex(raw)
    if expected != actual:
        raise errors.make(
            "IMPORT_PAYLOAD_CHECKSUM_MISMATCH",
            "结构化数据校验失败：data/events.jsonl sha256 与 manifest 不一致"
            "（期望 %s，实际 %s）" % (expected, actual),
            details={"expected": expected, "actual": actual},
            location="%s" % EVENTS_REL,
        )

    non_empty = _target_non_empty(data_dir)          # 6
    if non_empty and not overwrite:
        raise errors.make(
            "CONFLICT_IMPORT_TARGET_NOT_EMPTY",
            "导入目标非空：请先备份，然后显式使用 --overwrite 覆盖（或取消本次导入）",
            details={"target": data_dir},
            location=data_dir,
        )

    backup_path = None
    store.ensure_store_dirs(data_dir)
    if non_empty and backup:
        backup_path = store.backup_events(data_dir, clock.now_utc_compact())

    store.write_events_bytes(data_dir, raw)          # 7：写入校验通过的原始字节
    store.delete_manifest(data_dir)                  # 派生状态回到「待重算」

    warnings = []
    warnings.append("已导入结构化数据（format_version 2）；Markdown 人读文件不参与导入，可用 recompute/render 重新生成")
    view_files = ("profile.md", "log.md")
    recorded = {entry["path"]: entry.get("sha256") for entry in manifest.get("files", [])
                if isinstance(entry, dict)}
    for rel in view_files:
        path = os.path.join(bundle_dir, rel)
        entry_digest = recorded.get(rel)
        if not os.path.isfile(path) or not entry_digest:
            continue
        try:
            with open(path, "rb") as handle:
                current = store.sha256_hex(handle.read())
        except OSError:
            continue
        if current != entry_digest:
            warnings.append("人读文件 %s 已被修改（不参与导入，已忽略）" % rel)

    counts = model.counts()
    return {
        "source": bundle_dir,
        "events_imported": len(events),
        "counts": {
            "categories": counts["categories_total"],
            "lines": counts["lines"],
            "facts": len(model.all_facts()),
            "revisions": counts["revisions"],
        },
        "backup_path": backup_path,
        "warnings": warnings,
    }
