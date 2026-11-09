"""C0s 共享辅助（v2 = UUIDv7 标识方案 / ADR-023）。

包含 10 个公开 helpers、WARN 常量、模型加载（含 format_version 门）、类别/事实/命题线
引用解析与排序工具；被 other services 单向依赖（ADR-022）。
"""

import os

from .. import constants
from .. import errors
from .. import fold
from .. import ids
from .. import profile as profile_module
from .. import store

WARN_RENDER = "画像已变更，宿主产物待更新；运行 fasti render 使宿主产物生效"
WARN_RECOMPUTE = "宿主产物待更新；运行 fasti render 使宿主产物生效"


# ------------------------------------------------------------ 公开 helpers
def resolve_data_dir(explicit=None):
    """解析数据目录：--data-dir > FASTI_HOME > ~/.fasti（唯一读环境变量的函数）。"""
    return store.resolve_data_dir(explicit)


def is_initialized(data_dir):
    """数据目录是否已初始化（供适配器做页面初始状态判断，不触发折叠）。"""
    return store.is_initialized(os.path.abspath(data_dir))


def host_global_paths(host):
    """该宿主的用户级全局位置；cursor 无文件形式全局位置 ⇒ None。"""
    return constants.HOST_GLOBAL_PATHS.get(host)


def fold_events(events):
    return fold.fold_events(events)


def render_profile_body(model):
    return profile_module.render_profile_body(model)


def render_host_body(model):
    return profile_module.render_host_body(model)


def render_history_body(model):
    return profile_module.render_history_body(model)


def profile_text(view):
    return profile_module.render_profile_text(view)


def profile_at_text(view):
    return profile_module.render_at_text(view)


def history_text(view):
    return profile_module.render_history_text(view)


# ------------------------------------------------------------ 内部工具
def _require_format_version(events):
    """格式门（api-spec conventions.format_versioning.load_gate）。

    首事件必须是 store_initialized 且 format_version == 2，否则 STORE_FORMAT_UNSUPPORTED
    （退出码 5，消息含「迁移」与迁移指引）。v1 数据**不向后兼容**（ADR-024）。
    """
    if not events:
        return  # 交给 fold 报 CORRUPT_EVENT_SEQUENCE
    first = events[0]
    if not isinstance(first, dict) or first.get("type") != "store_initialized":
        return  # 交给 fold 报 CORRUPT_EVENT_SEQUENCE
    data = first.get("data")
    version = data.get("format_version") if isinstance(data, dict) else None
    if version != constants.FORMAT_VERSION:
        raise errors.make(
            "STORE_FORMAT_UNSUPPORTED",
            "数据目录是旧格式（format_version=%r，当前支持 %d）：不支持向后兼容读取；"
            "请先备份数据目录，再按迁移指引迁移到 v2（architecture.md §12.5："
            "清空重建，或使用一次性事件改写器）后重试"
            % (version, constants.FORMAT_VERSION),
            details={"format_version": version, "supported": constants.FORMAT_VERSION,
                     "migration": "architecture.md §12.5"},
            location="event seq=1",
        )


def _load_model(data_dir):
    """读取事件日志 → 格式门 → 纯函数折叠（失败抛 CORRUPT_* / STORE_FORMAT_UNSUPPORTED）。"""
    store.require_initialized(data_dir)
    raw = store.read_events_bytes(data_dir)
    events = store.parse_events_bytes(raw)
    _require_format_version(events)
    model = fold.fold_events(events)
    return raw, events, model


def _make_event(events, event_type, data, at):
    return {"seq": len(events) + 1, "at": at, "type": event_type, "data": data}


def _resolve_category(model, reference, field="category"):
    """类别引用解析（conventions.references）：精确 category_key → 精确 display_name。

    key 是身份（含已删除类别仍可精确命中，由调用方决定是否拒绝）；display_name 只匹配
    未删除类别，若唯一命中已删除类别的名称则作为兜底返回（供报「已删除」错误）。
    """
    if reference is None or (isinstance(reference, str) and not reference.strip()):
        raise errors.make("NOT_FOUND_CATEGORY", "缺少类别引用：请用 --category 指定类别",
                          details={"field": field})
    ref = reference.strip()
    exact = model.categories.get(ref)
    if exact is not None:
        return exact
    alive_hits = [category for category in model.alive_categories()
                  if category["display_name"] == ref]
    if len(alive_hits) == 1:
        return alive_hits[0]
    if len(alive_hits) > 1:
        raise errors.make(
            "VALIDATION_AMBIGUOUS_REFERENCE",
            "类别引用 %r 可匹配多个类别，请使用 category_key" % ref,
            details={"field": field,
                     "candidates": [category["key"] for category in alive_hits]},
        )
    deleted_hits = [category for category in model.categories.values()
                    if category["deleted_at"] is not None and category["display_name"] == ref]
    if len(deleted_hits) == 1:
        return deleted_hits[0]
    if len(deleted_hits) > 1:
        raise errors.make(
            "VALIDATION_AMBIGUOUS_REFERENCE",
            "类别引用 %r 可匹配多个已删除类别，请使用 category_key" % ref,
            details={"field": field,
                     "candidates": [category["key"] for category in deleted_hits]},
        )
    raise errors.make("NOT_FOUND_CATEGORY", "类别不存在：%r" % ref,
                      details={"field": field, "reference": ref})


def _resolve_fact(model, reference):
    """事实引用解析：完整 UUIDv7 或唯一短前缀（0 命中 NOT_FOUND_FACT）。"""
    return ids.resolve_reference(model.facts.keys(), reference, kind="fact",
                                 not_found_code="NOT_FOUND_FACT")


def _resolve_line(model, reference):
    """命题线引用解析：完整 UUIDv7 或唯一短前缀（0 命中 NOT_FOUND_LINE）。"""
    return ids.resolve_reference(model.lines.keys(), reference, kind="line",
                                 not_found_code="NOT_FOUND_LINE")


def _require_alive_category(category, action="录入事实"):
    if category["deleted_at"] is not None:
        raise errors.make(
            "VALIDATION_CATEGORY_DELETED",
            "目标类别已删除（不存在或未启用），无法%s：%s"
            % (action, category["display_name"]),
            details={"category_key": category["key"], "field": "category"},
        )


def _state_rank(model):
    category_rank = {category["key"]: index
                     for index, category in enumerate(model.ordered_categories(include_deleted=True))}
    line_rank = {line["line_id"]: index
                 for index, line in enumerate(model.ordered_lines())}
    return category_rank, line_rank


def _sort_facts(model, facts):
    category_rank, line_rank = _state_rank(model)
    return sorted(facts, key=lambda fact: (
        category_rank.get(fact["category_key"], 10 ** 6),
        line_rank.get(fact["line_id"], 10 ** 6),
        fact["valid_from"],
        fact["fact_id"],
    ))
