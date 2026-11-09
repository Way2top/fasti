"""C0s 事实应用服务（v2）：add / change / delete / list / show。

引用解析：`fact_id` / `line_id` 接受完整 UUIDv7 或唯一短前缀（ADR-023）；
输入来源只追加事件，不改历史。
"""

import os

from .. import clock
from .. import errors
from .. import fold
from .. import ids
from .. import store
from .. import validate
from ._shared import (WARN_RENDER, _load_model, _make_event, _require_alive_category,
                      _resolve_category, _resolve_fact, _resolve_line, _sort_facts)

__all__ = ["add_fact", "change_fact", "delete_fact", "list_facts", "show_fact"]


def add_fact(data_dir, category, content, valid_from):
    data_dir = os.path.abspath(data_dir)
    with store.store_lock(data_dir):
        _, events, model = _load_model(data_dir)
        category_obj = _resolve_category(model, category)
        _require_alive_category(category_obj)
        if not category_obj["enabled"]:
            raise errors.make(
                "VALIDATION_CATEGORY_NOT_ENABLED",
                "目标类别未启用，无法录入事实：%s" % category_obj["display_name"],
                details={"category_key": category_obj["key"], "field": "category"},
            )
        content = validate.require_content(content)
        month = validate.require_month(valid_from)
        line_id = ids.new_uuid7()
        fact_id = ids.new_uuid7()
        at = clock.now_recorded_at()
        event = _make_event(events, "fact_created", {
            "fact_id": fact_id,
            "line_id": line_id,
            "category_key": category_obj["key"],
            "content": content,
            "valid_from": month,
            "source": "user_entry",
            "recorded_at": at,
        }, at)
        new_events = events + [event]
        store.write_events(data_dir, new_events)
        new_model = fold.fold_events(new_events)
    return {
        "fact": new_model.fact(fact_id),
        "line": new_model.lines[line_id],
        "warnings": [WARN_RENDER],
        "_extra": {"category_display_name": category_obj["display_name"]},
    }


def change_fact(data_dir, fact_id, change_type, content, valid_from=None, note=None):
    data_dir = os.path.abspath(data_dir)
    with store.store_lock(data_dir):
        _, events, model = _load_model(data_dir)
        resolved_id = _resolve_fact(model, fact_id)
        fact = model.fact(resolved_id)
        if fact["deleted_at"] is not None:
            raise errors.make("VALIDATION_FACT_DELETED",
                              "该事实已删除，不能执行变更：%s" % resolved_id,
                              details={"fact_id": resolved_id})
        if change_type is None:
            raise errors.make("VALIDATION_CHANGE_TYPE_REQUIRED",
                              details={"field": "type"})
        if change_type not in ("evolve", "correct"):
            raise errors.make("VALIDATION_INVALID_CHANGE_TYPE",
                              "变更类型不合法：%r（可选 evolve / correct）" % (change_type,),
                              details={"field": "type", "value": change_type})
        content = validate.require_content(content)
        warnings = [WARN_RENDER]
        at = clock.now_recorded_at()

        if change_type == "correct":
            if valid_from is not None:
                raise errors.make(
                    "VALIDATION_UNEXPECTED_VALID_FROM",
                    "纠正不得改变生效时间（纠正不改变区间），请移除 --valid-from",
                    details={"field": "valid_from"},
                )
            note_text = validate.require_note(note)
            event = _make_event(events, "fact_corrected", {
                "fact_id": fact["fact_id"],
                "content": content,
                "note": note_text,
                "recorded_at": at,
            }, at)
            new_events = events + [event]
            store.write_events(data_dir, new_events)
            new_model = fold.fold_events(new_events)
            revisions = new_model.revisions_of(fact["fact_id"])
            return {
                "change_type": "correct",
                "fact": new_model.fact(fact["fact_id"]),
                "revision": revisions[-1],
                "warnings": warnings,
                "_extra": {"revision_seq": revisions[-1]["seq"]},
            }

        # evolve
        if valid_from is None:
            raise errors.make("VALIDATION_MISSING_VALID_FROM", details={"field": "valid_from"})
        month = validate.require_month(valid_from)
        head = model.head_fact(fact["line_id"])
        if head is None or head["fact_id"] != fact["fact_id"]:
            raise errors.make(
                "VALIDATION_EVOLVE_TARGET_NOT_HEAD",
                "演进只能针对该命题线当前最新的事实段（head 是 %s）"
                % (head["fact_id"] if head else "无"),
                details={"fact_id": fact["fact_id"],
                         "head_fact_id": head["fact_id"] if head else None},
            )
        if not (month > fact["valid_from"]):
            raise errors.make(
                "VALIDATION_VALID_FROM_NOT_AFTER_PREVIOUS",
                "新的生效时间必须晚于被演进段的生效时间（%s > %s）"
                % (month, fact["valid_from"]),
                details={"valid_from": month, "previous_valid_from": fact["valid_from"]},
            )
        if note is not None:
            validate.require_note(note)
            warnings.append("演进不保存备注（仅纠正支持备注）：已忽略 --note")
        new_fact_id = ids.new_uuid7()
        event = _make_event(events, "fact_evolved", {
            "fact_id": new_fact_id,
            "line_id": fact["line_id"],
            "previous_fact_id": fact["fact_id"],
            "content": content,
            "valid_from": month,
            "source": "user_entry",
            "recorded_at": at,
        }, at)
        new_events = events + [event]
        store.write_events(data_dir, new_events)
        new_model = fold.fold_events(new_events)
        line = new_model.lines[fact["line_id"]]
        return {
            "change_type": "evolve",
            "fact": new_model.fact(new_fact_id),
            "revision": new_model.revisions_of(new_fact_id)[0],
            "warnings": warnings,
            "_extra": {"category_display_name":
                       new_model.category(line["category_key"])["display_name"]},
        }


def delete_fact(data_dir, fact_id):
    data_dir = os.path.abspath(data_dir)
    with store.store_lock(data_dir):
        _, events, model = _load_model(data_dir)
        resolved_id = _resolve_fact(model, fact_id)
        fact = model.fact(resolved_id)
        if fact["deleted_at"] is not None:
            raise errors.make("VALIDATION_FACT_ALREADY_DELETED",
                              "该事实已删除，不能重复删除：%s" % resolved_id,
                              details={"fact_id": resolved_id})
        at = clock.now_recorded_at()
        event = _make_event(events, "fact_deleted", {
            "fact_id": fact["fact_id"],
            "recorded_at": at,
        }, at)
        new_events = events + [event]
        store.write_events(data_dir, new_events)
        new_model = fold.fold_events(new_events)
    return {
        "fact": new_model.fact(resolved_id),
        "warnings": [WARN_RENDER],
        "_extra": {},
    }


def list_facts(data_dir, category=None, line_id=None, state="current"):
    data_dir = os.path.abspath(data_dir)
    _, _, model = _load_model(data_dir)
    if state not in ("current", "all", "deleted"):
        raise errors.make("USAGE_INVALID_ARGUMENT",
                          "state 取值不合法：%r（可选 current / all / deleted）" % (state,),
                          details={"field": "state", "value": state})
    category_key = None
    if category is not None:
        category_key = _resolve_category(model, category)["key"]
    resolved_line = None
    if line_id is not None:
        resolved_line = _resolve_line(model, line_id)
    facts = []
    for line in model.ordered_lines(category_key):
        if resolved_line is not None and line["line_id"] != resolved_line:
            continue
        if state == "current":
            head = model.head_fact(line["line_id"])
            if head is not None:
                facts.append(head)
        else:
            for fact in model.line_facts(line["line_id"]):
                if state == "all" and fact["deleted_at"] is None:
                    facts.append(fact)
                elif state == "deleted" and fact["deleted_at"] is not None:
                    facts.append(fact)
    items = _sort_facts(model, facts)
    return {
        "items": items,
        "counts": {"returned": len(items), "total_facts": len(model.facts)},
        "_extra": {
            "category_display_names": {
                category["key"]: category["display_name"]
                for category in model.categories.values()
            },
        },
    }


def show_fact(data_dir, fact_id):
    data_dir = os.path.abspath(data_dir)
    _, _, model = _load_model(data_dir)
    resolved_id = _resolve_fact(model, fact_id)
    fact = model.fact(resolved_id)
    line = model.lines[fact["line_id"]]
    return {
        "fact": fact,
        "category": model.category(fact["category_key"]),
        "line": line,
        "revisions": model.revisions_of(resolved_id),
        "_extra": {"display_end": clock.display_end(fact["valid_to"])},
    }
