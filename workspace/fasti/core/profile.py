"""画像与历史视图（C5，v3）：纯函数，确定性渲染（ADR-008 / ADR-023 / ADR-025）。

- 不读时钟、不读网络、不读 dist/ 或 profile/、不依赖上一次派生结果；
- profile_body（含「（未录入）」）与 host_body（仅含有内容的类别）由同一视图生成；
- v2 标识：类别 slot/item 用 `category_key`（无 category_id）；line/fact 为 UUIDv7；
  修订以 `(fact_id, seq)` 定位（无 revision_id）；
- 短 ID 只用于 `--trace` 的 CLI 文本展示（`profile/current.md` 不包含任何 ID）。
"""

from . import clock
from . import ids

EMPTY_MARKER = "（未录入）"
PROFILE_TITLE = "# 当前画像"
PROFILE_NOTICE = "> 由 fasti 生成，请勿手工修改；修改事实后请运行 recompute 或 render。"
AT_NOTICE = ("结果基于当前已知事实按生效时间回溯（有效时间，秒精度，区间左闭右开），"
             "不代表该时点的历史认知。")
HISTORY_TITLE = "# 历史记录"
HISTORY_NOTICE = "> 由 fasti 生成，勿手工修改；按变更时间倒序（最新在前）；区间为生效时间（秒精度，左闭右开）。"
HISTORY_CATEGORY_EVENTS_TITLE = "## 类别变更"
AT_EMPTY_TEXT = "该时点无记录"


# ------------------------------------------------------------------ 视图构建
def _category_slot(category, has_content):
    return {
        "key": category["key"],
        "display_name": category["display_name"],
        "status": "content" if has_content else "empty",
    }


def _profile_item(category, line, fact):
    return {
        "category_key": category["key"],
        "category_display_name": category["display_name"],
        "line_id": line["line_id"],
        "fact_id": fact["fact_id"],
        "content": fact["content"],
        "valid_from": fact["valid_from"],
        "valid_to": fact["valid_to"],
        "change_type": fact["change_type"],
        "recorded_at": fact["recorded_at"],
        "latest_change": fact["latest_change"],
    }


def _trace_item(fact):
    latest = fact["latest_change"]
    return {
        "fact_id": fact["fact_id"],
        "first_recorded_at": fact["recorded_at"],
        "latest_change_kind": latest["kind"],
        "latest_change_at": latest["recorded_at"],
        "revision_count": fact["revision_count"],
    }


def _selected_categories(model, category_key=None):
    """默认 = 启用且未删除的类别；显式 --category 时只保留该类别
    （允许显式指定已关闭类别：事实仍可查，AC-10-1）。"""
    if category_key is not None:
        category = model.category(category_key)
        if category is None or category["deleted_at"] is not None:
            return []
        return [category]
    return model.enabled_categories()


def build_profile_view(model, category_key=None, trace=False):
    """当前画像视图（kind=current）。"""
    categories = []
    items = []
    trace_items = []
    for category in _selected_categories(model, category_key):
        pairs = model.head_fact_of_category(category["key"])
        categories.append(_category_slot(category, bool(pairs)))
        for line, fact in pairs:
            items.append(_profile_item(category, line, fact))
            if trace:
                trace_items.append(_trace_item(fact))
    return {
        "kind": "current",
        "at": None,
        "categories": categories,
        "items": items,
        "trace": trace_items if trace else None,
        "notices": [],
    }


def _at_fact_for_line(model, line_id, at_value):
    """取满足 valid_from <= at < valid_to 的未删除事实段（按瞬间比较）；无匹配返回 None。"""
    for fact in model.alive_line_facts(line_id):
        start = clock.compare(fact["valid_from"], at_value) <= 0
        end_ok = fact["valid_to"] is None or clock.compare(at_value, fact["valid_to"]) < 0
        if start and end_ok:
            return fact
    return None


def build_profile_at_view(model, at_input, at_value, category_key=None):
    """时点回溯视图（kind=at）：valid-time + 当前已知知识。

    `at` 字段保留原样查询输入（三档之一）；匹配按 `at_value`（粒度起点瞬间）比较。
    """
    categories = []
    items = []
    for category in _selected_categories(model, category_key):
        matched = []
        for line in model.ordered_lines(category["key"]):
            fact = _at_fact_for_line(model, line["line_id"], at_value)
            if fact is not None:
                matched.append((line, fact))
        categories.append(_category_slot(category, bool(matched)))
        for line, fact in matched:
            items.append(_profile_item(category, line, fact))
    return {
        "kind": "at",
        "at": at_input,
        "categories": categories,
        "items": items,
        "trace": None,
        "notices": [
            AT_NOTICE,
            "实际查询瞬间：%s（粗粒度输入按粒度起点解释）。" % at_value,
        ],
    }


# ------------------------------------------------------------------ 历史视图
def _fact_history_entry(model, category, line, fact):
    revisions = model.revisions_of(fact["fact_id"])
    corrections = []
    for revision in revisions[1:]:
        previous = revisions[revision["seq"] - 2]
        corrections.append({
            "seq": revision["seq"],
            "recorded_at": revision["recorded_at"],
            "old_content": previous["content"],
            "note": revision["note"],
        })
    return {
        "fact_id": fact["fact_id"],
        "line_id": line["line_id"],
        "category_key": category["key"],
        "category_display_name": category["display_name"],
        "content": fact["content"],
        "valid_from": fact["valid_from"],
        "valid_to": fact["valid_to"],
        "display_end": clock.display_end(fact["valid_to"]),
        "change_type": fact["change_type"],
        "change_kind": ("创建" if fact["change_type"] == "create" else "演进"),
        "change_at": revisions[-1]["recorded_at"],
        "recorded_at": fact["recorded_at"],
        "revision_count": fact["revision_count"],
        "corrections": corrections,
        "deleted_at": fact["deleted_at"],
    }


def build_history_view(model, category_key=None):
    """历史时间线视图：按类别分组、组内按变更时间倒序 + 类别变更事件。"""
    groups = []
    for category in model.ordered_categories(include_deleted=True):
        if category_key is not None and category["key"] != category_key:
            continue
        entries = []
        for line in model.ordered_lines(category["key"]):
            for fact in model.line_facts(line["line_id"]):
                entries.append(_fact_history_entry(model, category, line, fact))
        if not entries:
            continue
        entries.sort(key=lambda entry: (entry["change_at"], entry["fact_id"]), reverse=True)
        groups.append({
            "category_key": category["key"],
            "display_name": category["display_name"],
            "entries": entries,
        })

    category_events = []
    for event in model.events:
        event_type = event["type"]
        data = event["data"]
        if event_type == "category_added":
            category_events.append({
                "at": event["at"], "seq": event["seq"], "kind": "added",
                "category_key": data["category_key"],
                "display_name": data["display_name"],
            })
        elif event_type == "category_enabled_set":
            category = model.category(data["category_key"])
            if category is None:
                continue
            category_events.append({
                "at": event["at"], "seq": event["seq"],
                "kind": "enabled" if data["enabled"] else "disabled",
                "category_key": data["category_key"],
                "display_name": category["display_name"],
            })
        elif event_type == "category_deleted":
            category = model.category(data["category_key"])
            if category is None:
                continue
            target = model.category(data.get("migrate_to_category_key"))
            category_events.append({
                "at": event["at"], "seq": event["seq"], "kind": "deleted",
                "category_key": data["category_key"],
                "display_name": category["display_name"],
                "fact_handling": data["fact_handling"],
                "migrate_to": target["display_name"] if target else None,
            })
    category_events.sort(key=lambda item: (item["at"], item["seq"]), reverse=True)
    return {"groups": groups, "category_events": category_events}


# ------------------------------------------------------------------ 文本渲染
def _items_by_category(view):
    grouped = {}
    for item in view["items"]:
        grouped.setdefault(item["category_key"], []).append(item)
    return grouped


def _trace_by_fact(view):
    traces = {}
    for trace_item in view.get("trace") or []:
        traces[trace_item["fact_id"]] = trace_item
    return traces


def render_profile_text(view):
    """当前画像文本 = profile/current.md 的字节源（含「（未录入）」）。"""
    grouped = _items_by_category(view)
    traces = _trace_by_fact(view)
    lines = [PROFILE_TITLE, "", PROFILE_NOTICE, ""]
    for slot in view["categories"]:
        lines.append("## " + slot["display_name"])
        items = grouped.get(slot["key"], [])
        if not items:
            lines.append(EMPTY_MARKER)
            continue
        for item in items:
            lines.append("- " + item["content"])
            trace_item = traces.get(item["fact_id"])
            if trace_item is not None:
                lines.append(
                    "  ↳ 溯源：fact %s · 首次录入 %s · 最近变更：%s（%s，共 %d 次修订）"
                    % (ids.short_id(trace_item["fact_id"]), trace_item["first_recorded_at"],
                       trace_item["latest_change_kind"], trace_item["latest_change_at"],
                       trace_item["revision_count"])
                )
    return "\n".join(lines) + "\n"


def render_at_text(view):
    """时点回溯文本（profile at）。"""
    if not view["items"]:
        return AT_EMPTY_TEXT + "\n"
    grouped = _items_by_category(view)
    lines = ["# 画像回溯 · %s" % clock.display_input_at(view["at"]), ""]
    for notice in view["notices"]:
        lines.append("> " + notice)
    lines.append("")
    for slot in view["categories"]:
        items = grouped.get(slot["key"], [])
        if not items:
            continue
        lines.append("## " + slot["display_name"])
        for item in items:
            lines.append("- %s（%s 至 %s）" % (
                item["content"], clock.display_time(item["valid_from"]),
                clock.display_end(item["valid_to"])))
    return "\n".join(lines) + "\n"


def _history_entry_lines(entry):
    prefix = ""
    if entry["deleted_at"] is not None:
        prefix = "（已删除 %s）" % entry["deleted_at"]
    lines = ["- %s%s（%s 至 %s）" % (prefix, entry["content"],
                                     clock.display_time(entry["valid_from"]),
                                     entry["display_end"])]
    lines.append("  ↳ 变更：%s（%s）" % (entry["change_kind"], entry["recorded_at"]))
    for correction in entry["corrections"]:
        lines.append("  ↳ 纠正（%s）：原内容「%s」"
                     % (correction["recorded_at"], correction["old_content"]))
    return lines


def render_history_text(view):
    """历史记录文本 = profile/history.md 的字节源。"""
    lines = [HISTORY_TITLE, "", HISTORY_NOTICE, ""]
    for group in view["groups"]:
        lines.append("## " + group["display_name"])
        for entry in group["entries"]:
            lines.extend(_history_entry_lines(entry))
    lines.append("")
    lines.append(HISTORY_CATEGORY_EVENTS_TITLE)
    if not view["category_events"]:
        lines.append("- （无）")
    for event in view["category_events"]:
        if event["kind"] == "added":
            lines.append("- %s 新增类别「%s」（%s）"
                         % (event["at"], event["display_name"], event["category_key"]))
        elif event["kind"] == "enabled":
            lines.append("- %s 启用类别「%s」" % (event["at"], event["display_name"]))
        elif event["kind"] == "disabled":
            lines.append("- %s 关闭类别「%s」" % (event["at"], event["display_name"]))
        elif event["kind"] == "deleted":
            if event.get("fact_handling") == "migrate":
                handling = "迁移至「%s」" % event.get("migrate_to")
            else:
                handling = "一并删除"
            lines.append("- %s 删除类别「%s」（存量事实：%s）"
                         % (event["at"], event["display_name"], handling))
    return "\n".join(lines) + "\n"


# ---------------------------------------------- 契约要求的纯函数 helpers
def render_profile_body(model):
    """profile_body：含「（未录入）」（写 profile/current.md；不含任何 ID）。"""
    return render_profile_text(build_profile_view(model))


def render_host_body(model):
    """host_body：仅包含「启用、未删除、且至少有一条 line 有当前值」的类别。

    三份宿主产物共享该正文（逐字节一致，ADR-010）；不含 ID。
    """
    lines = []
    for category in model.enabled_categories():
        pairs = model.head_fact_of_category(category["key"])
        if not pairs:
            continue
        lines.append("## " + category["display_name"])
        for _line, fact in pairs:
            lines.append("- " + fact["content"])
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def render_history_body(model):
    """history_body：写 profile/history.md（不含 ID）。"""
    return render_history_text(build_history_view(model))
