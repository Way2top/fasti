"""确定性折叠（C3，v2 = UUIDv7 标识方案）：事件流 → 读取模型（纯函数）。

约束（architecture.md §1.5 不变量 1/2/3，ADR-023）：
- 不读时钟、不读网络、不读写文件、不依赖上一次派生结果；
- 同一 events 输入 ⇒ 完全相同的 ReadModel（用于 verify 的 derived.determinism）；
- 语义错误一律抛 CoreError（定位到事件序号）；导入流水线负责把它改写为
  IMPORT_SEMANTIC_INVALID（见 importer.py）。
- v2 字段：category_key 为类别身份；line_id/fact_id 为 UUIDv7；修订无独立 ID
  （以 (fact_id, seq) 复合定位）。
"""

import re

from . import clock
from . import constants
from . import errors
from . import ids
from . import model
from . import validate

EVENT_TYPES = (
    "store_initialized", "category_added", "category_enabled_set", "category_deleted",
    "fact_created", "fact_evolved", "fact_corrected", "fact_deleted",
)


def _fail(event, message, code="CORRUPT_EVENT_LOG"):
    seq = event.get("seq", "?") if isinstance(event, dict) else "?"
    raise errors.make(code, message, location="event seq=%s" % seq)


def _check_sequence(events):
    if not events:
        raise errors.make("CORRUPT_EVENT_SEQUENCE", "事件日志为空")
    for index, event in enumerate(events):
        expected = index + 1
        seq = event.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq != expected:
            raise errors.make(
                "CORRUPT_EVENT_SEQUENCE",
                "事件序号不连续：位置 %d 期望 seq=%d，实际 %r" % (index + 1, expected, seq),
                location="event seq=%r" % (seq,),
            )
    if events[0].get("type") != "store_initialized":
        raise errors.make("CORRUPT_EVENT_SEQUENCE", "首事件必须是 store_initialized",
                          location="event seq=1")
    initialized = [event for event in events if event.get("type") == "store_initialized"]
    if len(initialized) != 1:
        raise errors.make("CORRUPT_EVENT_SEQUENCE",
                          "store_initialized 必须恰好出现一次，实际 %d 次" % len(initialized))


def _require_valid_time(event, value, field):
    if not clock.is_valid_time(value):
        _fail(event, "%s 不是合法的 RFC3339 秒级时间戳：%r" % (field, value))


def _require_after_previous(event, line_id, rm, value):
    """同一 line 生效瞬间必须严格递增（C-07：无零长区间）。"""
    for existing in rm.line_facts(line_id):
        relation = clock.compare(existing["valid_from"], value)
        if relation == 0:
            _fail(event, "同一命题线的生效瞬间重复（%s）：不得出现零长区间" % value)
        if relation > 0:
            _fail(event, "新事实段的生效瞬间早于同线后续段：%s" % value)


def _require_uuid7(event, value, field):
    if not ids.is_uuid7(value):
        _fail(event, "%s 不是合法的 UUIDv7 规范形式：%r" % (field, value))


def _require_category_key(event, value, field="category_key"):
    if not isinstance(value, str) or not re.match(constants.CATEGORY_KEY_RE, value):
        _fail(event, "%s 不是合法的类别 key：%r" % (field, value))


def _content(event, value):
    try:
        return validate.require_content(value)
    except errors.CoreError as exc:
        _fail(event, "content 不合法：%s" % exc.message)


def _note(event, value):
    try:
        return validate.require_note(value)
    except errors.CoreError as exc:
        _fail(event, "note 不合法：%s" % exc.message)


# ------------------------------------------------------------------ 事件应用
def _apply_store_initialized(rm, event, index):
    if index != 0:
        _fail(event, "store_initialized 必须是第一条事件")
    version = event["data"].get("format_version")
    if version != constants.FORMAT_VERSION:
        _fail(event, "不支持的 format_version：%r（当前支持 %d）"
              % (version, constants.FORMAT_VERSION))


def _apply_category_added(rm, event, index):
    data = event["data"]
    category_key = data["category_key"]
    _require_category_key(event, category_key)
    alive = rm.alive_categories()
    existing = rm.categories.get(category_key)
    revival = existing is not None and existing["deleted_at"] is not None
    if existing is not None and not revival:
        _fail(event, "类别 key 重复（未删除类别已存在）：%s" % category_key)
    display_name = data["display_name"]
    if any(category["display_name"] == display_name for category in alive):
        _fail(event, "未删除类别中 display_name 重复：%s" % display_name)
    origin = data["origin"]
    template_order = data.get("template_order")
    if origin == "template":
        if template_order is None or not (1 <= template_order <= 7):
            _fail(event, "origin=template 时 template_order 必须在 1..7 内")
        if any(category["origin"] == "template" and category["template_order"] == template_order
               for category in alive):
            _fail(event, "template_order 重复：%r" % template_order)
    elif template_order is not None:
        _fail(event, "origin=custom 时 template_order 必须为 null")
    category = {
        "key": category_key,
        "display_name": display_name,
        "origin": origin,
        "template_order": template_order,
        "enabled": bool(data["enabled"]),
        "created_at": data["created_at"],
        "deleted_at": None,
        "deleted_handling": None,
        "migrate_to_category_key": None,
    }
    if revival:
        # 复活：读模型重置该行，历史事件全部保留
        rm.categories[category_key] = category
        return
    rm.categories[category_key] = category
    rm.category_order.append(category_key)


def _apply_category_enabled_set(rm, event, index):
    data = event["data"]
    category = rm.categories.get(data["category_key"])
    if category is None:
        _fail(event, "类别不存在：%s" % data["category_key"])
    if category["deleted_at"] is not None:
        _fail(event, "类别已删除，不能再启用/关闭：%s" % data["category_key"])
    category["enabled"] = bool(data["enabled"])


def _apply_category_deleted(rm, event, index):
    data = event["data"]
    category_key = data["category_key"]
    _require_category_key(event, category_key)
    category = rm.categories.get(category_key)
    if category is None:
        _fail(event, "类别不存在：%s" % category_key)
    if category["deleted_at"] is not None:
        _fail(event, "类别已删除：%s" % category_key)
    handling = data["fact_handling"]
    if handling not in ("migrate", "delete"):
        _fail(event, "fact_handling 不合法：%r" % handling)
    migrate_to = data.get("migrate_to_category_key")
    at = data["at"]
    if handling == "migrate":
        _require_category_key(event, migrate_to, "migrate_to_category_key")
        target = rm.categories.get(migrate_to)
        if target is None:
            _fail(event, "迁移目标类别不存在：%r" % migrate_to)
        if target["deleted_at"] is not None:
            _fail(event, "迁移目标类别已删除：%r" % migrate_to)
        if not target["enabled"]:
            _fail(event, "迁移目标类别未启用：%r" % migrate_to)
        if migrate_to == category_key:
            _fail(event, "迁移目标不得是自身")
        for line in rm.lines.values():
            if line["category_key"] == category_key:
                line["category_key"] = migrate_to
    else:
        if migrate_to is not None:
            _fail(event, "handling=delete 时不得给出迁移目标")
        for fact in rm.facts.values():
            line = rm.lines[fact["line_id"]]
            if line["category_key"] == category_key and fact["deleted_at"] is None:
                fact["deleted_at"] = at
    category["deleted_at"] = at
    category["deleted_handling"] = handling
    category["migrate_to_category_key"] = migrate_to


def _new_line(rm, event, line_id, category_key, created_at):
    _require_uuid7(event, line_id, "line_id")
    if line_id in rm.lines:
        _fail(event, "命题线 ID 重复：%s" % line_id)
    line = {
        "line_id": line_id,
        "category_key": category_key,
        "created_at": created_at,
        "segment_count": 0,
    }
    rm.lines[line_id] = line
    rm.line_order.append(line_id)
    return line


def _new_fact(rm, event, fact_id, line_id, valid_from, content, change_type,
              previous_fact_id, source, recorded_at):
    _require_uuid7(event, fact_id, "fact_id")
    if fact_id in rm.facts:
        _fail(event, "事实 ID 重复：%s" % fact_id)
    fact = {
        "fact_id": fact_id,
        "line_id": line_id,
        "category_key": rm.lines[line_id]["category_key"],
        "content": content,
        "valid_from": valid_from,
        "valid_to": None,
        "change_type": change_type,
        "previous_fact_id": previous_fact_id,
        "source": source,
        "recorded_at": recorded_at,
        "latest_change": None,
        "revision_count": 1,
        "deleted_at": None,
    }
    rm.facts[fact_id] = fact
    rm.fact_order.append(fact_id)
    rm.revisions[fact_id] = [{
        "fact_id": fact_id,
        "seq": 1,
        "content": content,
        "revision_type": "initial",
        "note": None,
        "recorded_at": recorded_at,
    }]
    return fact


def _apply_fact_created(rm, event, index):
    data = event["data"]
    category_key = data["category_key"]
    _require_category_key(event, category_key)
    category = rm.categories.get(category_key)
    if category is None:
        _fail(event, "类别不存在：%s" % category_key)
    if category["deleted_at"] is not None:
        _fail(event, "类别已删除：%s" % category_key)
    if not category["enabled"]:
        _fail(event, "类别未启用：%s" % category_key)
    _require_valid_time(event, data["valid_from"], "valid_from")
    if data["source"] != "user_entry":
        _fail(event, "source 取值不合法：%r" % data["source"])
    content = _content(event, data["content"])
    _new_line(rm, event, data["line_id"], category_key, data["recorded_at"])
    _new_fact(rm, event, data["fact_id"], data["line_id"], data["valid_from"], content,
              "create", None, data["source"], data["recorded_at"])


def _apply_fact_evolved(rm, event, index):
    data = event["data"]
    _require_uuid7(event, data["previous_fact_id"], "previous_fact_id")
    previous = rm.facts.get(data["previous_fact_id"])
    if previous is None:
        _fail(event, "被演进的事实不存在：%s" % data["previous_fact_id"])
    if previous["deleted_at"] is not None:
        _fail(event, "被演进的事实已删除：%s" % data["previous_fact_id"])
    _require_uuid7(event, data["line_id"], "line_id")
    if data["line_id"] not in rm.lines:
        _fail(event, "命题线不存在：%s" % data["line_id"])
    if previous["line_id"] != data["line_id"]:
        _fail(event, "演进事实的 line_id 与被演进段不一致")
    head = rm.head_fact(data["line_id"])
    if head is None or head["fact_id"] != previous["fact_id"]:
        _fail(event, "被演进的事实不是该命题线当前 head（head=%s）"
              % (head["fact_id"] if head else None))
    _require_valid_time(event, data["valid_from"], "valid_from")
    if clock.compare(data["valid_from"], previous["valid_from"]) <= 0:
        _fail(event, "演进的新生效瞬间必须严格晚于被演进段（%s <= %s）"
              % (data["valid_from"], previous["valid_from"]))
    _require_after_previous(event, data["line_id"], rm, data["valid_from"])
    if data["source"] != "user_entry":
        _fail(event, "source 取值不合法：%r" % data["source"])
    content = _content(event, data["content"])
    _new_fact(rm, event, data["fact_id"], data["line_id"], data["valid_from"], content,
              "evolve", previous["fact_id"], data["source"], data["recorded_at"])


def _apply_fact_corrected(rm, event, index):
    data = event["data"]
    _require_uuid7(event, data["fact_id"], "fact_id")
    fact = rm.facts.get(data["fact_id"])
    if fact is None:
        _fail(event, "事实不存在：%s" % data["fact_id"])
    if fact["deleted_at"] is not None:
        _fail(event, "事实已删除，不能纠正：%s" % data["fact_id"])
    content = _content(event, data["content"])
    note = _note(event, data.get("note"))
    revisions = rm.revisions[fact["fact_id"]]
    seq = len(revisions) + 1
    revisions.append({
        "fact_id": fact["fact_id"],
        "seq": seq,
        "content": content,
        "revision_type": "correct",
        "note": note,
        "recorded_at": data["recorded_at"],
    })


def _apply_fact_deleted(rm, event, index):
    data = event["data"]
    _require_uuid7(event, data["fact_id"], "fact_id")
    fact = rm.facts.get(data["fact_id"])
    if fact is None:
        _fail(event, "事实不存在：%s" % data["fact_id"])
    if fact["deleted_at"] is not None:
        _fail(event, "事实已删除：%s" % data["fact_id"])
    fact["deleted_at"] = data["recorded_at"]


_APPLIERS = {
    "store_initialized": _apply_store_initialized,
    "category_added": _apply_category_added,
    "category_enabled_set": _apply_category_enabled_set,
    "category_deleted": _apply_category_deleted,
    "fact_created": _apply_fact_created,
    "fact_evolved": _apply_fact_evolved,
    "fact_corrected": _apply_fact_corrected,
    "fact_deleted": _apply_fact_deleted,
}


# ------------------------------------------------------------------ finalize
def _finalize(rm):
    for line_id in rm.line_order:
        line = rm.lines[line_id]
        segments = rm.line_facts(line_id)
        alive = [fact for fact in segments if fact["deleted_at"] is None]
        for fact in segments:
            later = [item for item in alive
                     if clock.compare(item["valid_from"], fact["valid_from"]) > 0]
            fact["valid_to"] = (min(later, key=lambda item: clock.instant(item["valid_from"]))
                                ["valid_from"] if later else None)
            fact["category_key"] = line["category_key"]
            revisions = rm.revisions_of(fact["fact_id"])
            latest = revisions[-1]
            fact["content"] = latest["content"]
            fact["revision_count"] = len(revisions)
            if latest["revision_type"] == "correct":
                kind = "纠正"
            elif fact["change_type"] == "create":
                kind = "创建"
            else:
                kind = "演进"
            fact["latest_change"] = {
                "kind": kind,
                "recorded_at": latest["recorded_at"],
                "revision_count": len(revisions),
            }
        line["segment_count"] = len(segments)


def fold_events(events):
    """纯函数：事件列表 → ReadModel。"""
    rm = model.ReadModel()
    rm.events = list(events)
    _check_sequence(rm.events)
    for index, event in enumerate(rm.events):
        event_type = event.get("type")
        applier = _APPLIERS.get(event_type)
        if applier is None:
            _fail(event, "未知事件类型：%r" % event_type)
        applier(rm, event, index)
    _finalize(rm)
    return rm
