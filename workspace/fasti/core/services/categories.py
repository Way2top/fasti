"""C0s 类别应用服务（v2）：list / add / enable / delete。

类别身份 = 语义 `category_key`（ADR-023）：key 永久保留（含已删除），同 key 重新新增
= 「复活」（读模型重置该行、历史事件保留，由 fold 负责）。
"""

import os

from .. import clock
from .. import errors
from .. import fold
from .. import ids
from .. import store
from .. import validate
from ._shared import (WARN_RENDER, _load_model, _make_event, _resolve_category)

__all__ = ["list_categories", "add_category", "set_category_enabled", "delete_category"]


def list_categories(data_dir):
    data_dir = os.path.abspath(data_dir)
    _, _, model = _load_model(data_dir)
    return {"items": model.ordered_categories(include_deleted=True)}


def add_category(data_dir, name, key=None, enabled=True):
    data_dir = os.path.abspath(data_dir)
    with store.store_lock(data_dir):
        _, events, model = _load_model(data_dir)
        display_name = validate.require_category_name(name)
        key = validate.require_category_key(key)
        alive = model.alive_categories()
        if any(category["display_name"] == display_name for category in alive):
            raise errors.make("VALIDATION_CATEGORY_NAME_TAKEN",
                              "类别名称已被占用：%s" % display_name,
                              details={"field": "name", "display_name": display_name})
        if key is None:
            key = ids.auto_category_key()
        elif any(category["key"] == key for category in alive):
            raise errors.make("VALIDATION_CATEGORY_KEY_TAKEN",
                              "类别 key 已被占用：%s" % key,
                              details={"field": "key", "key": key})
        at = clock.now_recorded_at()
        data = {
            "category_key": key,
            "display_name": display_name,
            "origin": "custom",
            "template_order": None,
            "enabled": bool(enabled),
            "created_at": at,
        }
        event = _make_event(events, "category_added", data, at)
        new_events = events + [event]
        store.write_events(data_dir, new_events)
        new_model = fold.fold_events(new_events)
    return {
        "category": new_model.category(key),
        "warnings": [WARN_RENDER],
        "_extra": {},
    }


def set_category_enabled(data_dir, category, enabled):
    data_dir = os.path.abspath(data_dir)
    with store.store_lock(data_dir):
        _, events, model = _load_model(data_dir)
        category_obj = _resolve_category(model, category)
        if category_obj["deleted_at"] is not None:
            raise errors.make("VALIDATION_CATEGORY_ALREADY_DELETED",
                              "类别已删除，不能启用/关闭：%s" % category_obj["display_name"],
                              details={"category_key": category_obj["key"]})
        at = clock.now_recorded_at()
        event = _make_event(events, "category_enabled_set", {
            "category_key": category_obj["key"],
            "enabled": bool(enabled),
            "at": at,
        }, at)
        new_events = events + [event]
        store.write_events(data_dir, new_events)
        new_model = fold.fold_events(new_events)
    return {
        "category": new_model.category(category_obj["key"]),
        "warnings": [WARN_RENDER],
        "_extra": {"display_name": category_obj["display_name"]},
    }


def delete_category(data_dir, category, handling, migrate_to=None):
    data_dir = os.path.abspath(data_dir)
    with store.store_lock(data_dir):
        _, events, model = _load_model(data_dir)
        category_obj = _resolve_category(model, category)
        if category_obj["deleted_at"] is not None:
            raise errors.make("VALIDATION_CATEGORY_ALREADY_DELETED",
                              "类别已删除：%s" % category_obj["display_name"],
                              details={"category_key": category_obj["key"]})
        if handling is None or handling not in ("migrate", "delete"):
            raise errors.make("VALIDATION_INVALID_HANDLING",
                              "存量事实处置方式不合法：%r（可选 migrate / delete）" % (handling,),
                              details={"field": "handle_facts", "value": handling})
        target = None
        if handling == "migrate":
            if not migrate_to:
                raise errors.make("VALIDATION_MISSING_MIGRATE_TARGET",
                                  "删除类别时必须提供迁移目标（--migrate-to）",
                                  details={"field": "migrate_to"})
            try:
                target = _resolve_category(model, migrate_to, field="migrate_to")
            except errors.CoreError as exc:
                raise errors.make("VALIDATION_INVALID_MIGRATE_TARGET",
                                  "迁移目标不合法：%s" % exc.message,
                                  details={"field": "migrate_to", "value": migrate_to})
            if target["deleted_at"] is not None:
                raise errors.make("VALIDATION_INVALID_MIGRATE_TARGET",
                                  "迁移目标已删除：%s" % target["display_name"],
                                  details={"field": "migrate_to"})
            if not target["enabled"]:
                raise errors.make("VALIDATION_INVALID_MIGRATE_TARGET",
                                  "迁移目标未启用：%s" % target["display_name"],
                                  details={"field": "migrate_to"})
            if target["key"] == category_obj["key"]:
                raise errors.make("VALIDATION_INVALID_MIGRATE_TARGET",
                                  "迁移目标不得是被删除类别自身",
                                  details={"field": "migrate_to"})
        affected = 0
        for line in model.ordered_lines(category_obj["key"]):
            for fact in model.line_facts(line["line_id"]):
                if handling == "migrate":
                    affected += 1
                elif fact["deleted_at"] is None:
                    affected += 1
        at = clock.now_recorded_at()
        event = _make_event(events, "category_deleted", {
            "category_key": category_obj["key"],
            "fact_handling": handling,
            "migrate_to_category_key": target["key"] if target else None,
            "at": at,
        }, at)
        new_events = events + [event]
        store.write_events(data_dir, new_events)
        new_model = fold.fold_events(new_events)
    return {
        "category": new_model.category(category_obj["key"]),
        "affected_facts": affected,
        "warnings": [WARN_RENDER],
        "_extra": {
            "display_name": category_obj["display_name"],
            "handling": handling,
            "migrate_to_display_name": target["display_name"] if target else None,
        },
    }
