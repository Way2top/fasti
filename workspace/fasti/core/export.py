"""导出（C7，v2 = UUIDv7）：OKF 形状 bundle + JSONL 双轨（ADR-012 / architecture §4.1）。

确定性：除 manifest.json 的 exported_at 外，两次导出的同名文件逐字节一致。
v2：`format_version: 2`；category 行以 key 为身份（无 category_id）；
fact_revision 行以 (fact_id, seq) 定位（无 revision_id）；line_id/fact_id 为完整 UUIDv7。
写入策略：先写 <out>.partial/，全部 fsync 成功后原子改名为 <out>/。
"""

import os
import shutil

from . import clock
from . import constants
from . import errors
from . import profile
from . import store

README_MD = """# Fasti 导出 bundle（format_version 2）

本目录是 Fasti 的一次完整导出，采用「OKF 形状 Markdown + 结构化 JSONL」双轨。
`format_version: 2` 对应 **UUIDv7 标识方案**（C-06）。

## 双轨说明

- **人读**：`index.md`、`profile.md`、`log.md`、`concepts/**.md`
- **权威结构化数据**：`data/events.jsonl`（唯一真相，也是导入的唯一来源）
- **读取模型快照**：`data/entities.jsonl`（供第三方核对，导入时忽略）

人读 Markdown 是「看得懂」的职责，`data/events.jsonl` 是「无损还原」的职责；两者冲突时以结构化数据为准。
人读文件与 Markdown 不含 ID 的截断形式；ID 只以完整 UUIDv7 规范形式出现在结构化数据与 concept frontmatter 中。

## 导入

```
fasti import <bundle 目录>
```

- 空环境：直接完整还原（类别的启用状态、自定义类别、全部事实与修订都在 events.jsonl 里）。
- 非空环境：必须显式 `--overwrite`；默认先写 `state/backups/events-<时间戳>.jsonl` 自动备份。
- **Markdown 不参与导入**：人工修改 Markdown 不会改变事实与画像；要改数据请用 fasti 命令。
- **格式门**：只接受 `format_version == 2` 的 bundle。v1（序号 ID 方案）导出包会被拒绝并提示迁移
  （`IMPORT_UNSUPPORTED_FORMAT_VERSION`），迁移方法见 architecture.md §12.5。

## 标识（v2）

- `line_id` / `fact_id`：UUIDv7（RFC 9562，36 字符小写规范形式，时间有序）。
- 类别身份：语义 `category_key`（无 `category_id`）。
- 修订：无独立 ID，以 `(fact_id, seq)` 复合定位。
- CLI text 显示的短 ID（前 8 位十六进制）**不会**写入本 bundle；本 bundle 内 ID 一律完整形式。

## OKF 对齐口径（方向对齐，不声称严格符合规范）

- 形状级对齐：Markdown + YAML frontmatter、一概念一文件、路径即概念 ID、根 `index.md`、按时间排列的 `log.md`、纯 Markdown 相对链接。
- 已知缺口：OKF 没有版本链 / `supersedes` / 有效区间 / 纠正语义，无法无损表达「演进 vs 纠正」。
- 本工具的扩展 frontmatter 字段（不属于 OKF 核心字段）：`fact_id`、`line_id`、`category`、`valid_from`、`valid_to`、`updated_at`。

## 版本

- `format_version: 2`（v2 = UUIDv7）；v1 导出包不被导入。

## 说明

- 本目录内除 `manifest.json` 的 `exported_at` 外不含生成时间、机器名或绝对路径。
"""


def default_out_dir(data_dir):
    base = os.path.join(store.paths(data_dir)["exports"],
                        "%s%s" % (constants.EXPORT_PREFIX, clock.now_utc_compact()))
    candidate = base
    index = 2
    while os.path.exists(candidate):
        candidate = "%s-%d" % (base, index)
        index += 1
    return candidate


def _concept_md(model, category, line):
    display = category["display_name"]
    key = category["key"]
    head = model.head_fact(line["line_id"])
    segments = model.line_facts(line["line_id"])
    tag_change = (head or segments[-1])["change_type"] if segments else "create"
    tags = '["category:%s", "change:%s"]' % (key, tag_change)
    if head is not None:
        fact_id = head["fact_id"]
        valid_from = '"%s"' % head["valid_from"]
        valid_to = '"%s"' % head["valid_to"] if head["valid_to"] else "null"
    else:
        fact_id = "null"
        valid_from = "null"
        valid_to = "null"
    updated_at = max(revision["recorded_at"]
                     for fact in segments for revision in model.revisions_of(fact["fact_id"]))
    lines = [
        "---",
        "type: fact-line",
        "title: %s · %s" % (display, line["line_id"]),
        "tags: %s" % tags,
        "fact_id: %s" % fact_id,
        "line_id: %s" % line["line_id"],
        "category: %s" % key,
        "valid_from: %s" % valid_from,
        "valid_to: %s" % valid_to,
        'updated_at: "%s"' % updated_at,
        "---",
        "",
        "# %s · %s" % (display, line["line_id"]),
        "",
    ]
    if head is not None:
        lines.append("当前值（自 %s 起）：%s" % (head["valid_from"], head["content"]))
    else:
        lines.append("当前值：无（该命题线没有未删除事实）")
    lines.append("")
    lines.append("## 历史")
    lines.append("")
    for fact in segments:
        prefix = ""
        if fact["deleted_at"] is not None:
            prefix = "（已删除 %s）" % fact["deleted_at"]
        lines.append("- %s%s 至 %s：%s" % (
            prefix, fact["valid_from"], clock.display_end(fact["valid_to"]), fact["content"]))
        revisions = model.revisions_of(fact["fact_id"])
        lines.append("  - 首次录入 %s" % revisions[0]["recorded_at"])
        for revision in revisions[1:]:
            previous = revisions[revision["seq"] - 2]
            lines.append("  - 纠正 %s：原内容「%s」" % (revision["recorded_at"], previous["content"]))
    return "\n".join(lines) + "\n"


def _index_md(model):
    lines = [
        "---",
        'okf_version: "0.1"',
        "type: index",
        "title: 个人画像导出",
        "---",
        "",
        "# 个人画像导出",
        "",
        "- 当前画像：[profile.md](profile.md)",
        "- 历史记录：[log.md](log.md)",
        "- 结构化数据：[data/events.jsonl](data/events.jsonl)、"
        "[data/entities.jsonl](data/entities.jsonl)",
        "",
        "## 类别与概念",
        "",
    ]
    for category in model.ordered_categories():
        lines.append("- %s（%s）" % (category["display_name"], category["key"]))
        for line in model.ordered_lines(category["key"]):
            lines.append("  - [%s](concepts/%s/%s.md)"
                         % (line["line_id"], category["key"], line["line_id"]))
    return "\n".join(lines) + "\n"


def _entities_rows(model):
    rows = []
    for category in model.ordered_categories(include_deleted=True):
        row = {"type": "category"}
        row.update(category)
        rows.append(row)
    for line in model.ordered_lines():
        row = {"type": "fact_line"}
        row.update(line)
        rows.append(row)
    for line in model.ordered_lines():
        for fact in model.line_facts(line["line_id"]):
            row = {"type": "fact"}
            row.update(fact)
            rows.append(row)
            for revision in model.revisions_of(fact["fact_id"]):
                revision_row = {"type": "fact_revision"}
                revision_row.update(revision)
                rows.append(revision_row)
    return rows


def build_bundle_files(model, events_raw):
    """生成 bundle 内全部文件内容（不写盘）。返回 {相对路径: bytes}。"""
    files = {
        "index.md": _index_md(model).encode("utf-8"),
        "profile.md": profile.render_profile_body(model).encode("utf-8"),
        "log.md": profile.render_history_body(model).encode("utf-8"),
        "README.md": README_MD.encode("utf-8"),
        "data/events.jsonl": events_raw,
        "data/entities.jsonl": store.serialize_lines(_entities_rows(model)),
    }
    for category in model.ordered_categories():
        for line in model.ordered_lines(category["key"]):
            rel = "concepts/%s/%s.md" % (category["key"], line["line_id"])
            files[rel] = _concept_md(model, category, line).encode("utf-8")
    return files


def _write_file(root, rel_path, raw):
    target = os.path.join(root, rel_path)
    parent = os.path.dirname(target)
    if parent:
        store.ensure_dir(parent)
    store.write_bytes_atomic(target, raw)


def export_bundle(data_dir, model, events_raw, out_dir=None):
    if out_dir is None:
        target_dir = default_out_dir(data_dir)
    else:
        target_dir = store.expand_home(out_dir)
    if os.path.exists(target_dir):
        if os.path.isdir(target_dir) and not os.listdir(target_dir):
            os.rmdir(target_dir)
        else:
            raise errors.make(
                "USAGE_INVALID_ARGUMENT",
                "输出目录已存在且非空，拒绝覆盖：%s" % target_dir,
                location=target_dir,
            )
    parent = os.path.dirname(target_dir)
    if parent:
        store.ensure_dir(parent)
    partial = target_dir + ".partial"
    if os.path.exists(partial):
        shutil.rmtree(partial)
    store.ensure_dir(partial)

    files = build_bundle_files(model, events_raw)
    try:
        for rel_path in sorted(files):
            _write_file(partial, rel_path, files[rel_path])
        payload_sha = store.sha256_hex(events_raw)
        file_entries = [
            {"path": rel_path, "sha256": store.sha256_hex(files[rel_path])}
            for rel_path in sorted(files)
        ]
        counts = model.counts()
        manifest = {
            "bundle_version": constants.BUNDLE_VERSION,
            "format_version": constants.FORMAT_VERSION,
            "exported_at": clock.now_recorded_at(),
            "counts": {
                "categories": counts["categories_total"],
                "lines": counts["lines"],
                "facts": len(model.all_facts()),
                "revisions": counts["revisions"],
                "events": counts["events"],
            },
            "payload": {
                "path": "data/events.jsonl",
                "sha256": payload_sha,
                "bytes": len(events_raw),
            },
            "files": file_entries,
        }
        manifest_raw = (store.canonical_json(manifest) + "\n").encode("utf-8")
        _write_file(partial, "manifest.json", manifest_raw)
        os.replace(partial, target_dir)
    except errors.CoreError:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(partial, ignore_errors=True)
        raise errors.make("IO_WRITE_FAILED", "导出失败：%s" % exc, location=target_dir)

    return {
        "output_dir": os.path.abspath(target_dir),
        "files": len(files) + 1,
        "payload_sha256": payload_sha,
        "payload_bytes": len(events_raw),
        "format_version": constants.FORMAT_VERSION,
        "warnings": [],
    }
