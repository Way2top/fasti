"""事件记录结构校验（v3 / format_version 3）：UUIDv7 + 秒级时间 + C-08 确认字段。

供导入流水线第 2 步使用：结构不合法 ⇒ IMPORT_INVALID_RECORD（不在此抛错，返回问题列表）。
字段契约见 api-spec file_formats.events_jsonl（v2）。
"""

import re

from . import clock
from . import constants
from . import ids


# ------------------------------------------------------------ 字段 spec 定义
def _spec(kind, **extra):
    spec = {"kind": kind}
    spec.update(extra)
    return spec


EVENT_TYPES = (
    "store_initialized", "category_added", "category_enabled_set", "category_deleted",
    "fact_created", "fact_evolved", "fact_corrected", "fact_deleted",
)

EVENT_DATA_SPECS = {
    "store_initialized": {
        "format_version": _spec("const", value=constants.FORMAT_VERSION),
        "app_name": _spec("str"),
        "app_version": _spec("str"),
    },
    "category_added": {
        "category_key": _spec("category_key"),
        "display_name": _spec("str", min_length=1,
                              max_length=constants.CATEGORY_NAME_MAX_LEN),
        "origin": _spec("enum", values=["template", "custom"]),
        "template_order": _spec("int", min_value=1, max_value=7, nullable=True),
        "enabled": _spec("bool"),
        "created_at": _spec("ts"),
    },
    "category_enabled_set": {
        "category_key": _spec("category_key"),
        "enabled": _spec("bool"),
        "at": _spec("ts"),
    },
    "category_deleted": {
        "category_key": _spec("category_key"),
        "fact_handling": _spec("enum", values=["migrate", "delete"]),
        "migrate_to_category_key": _spec("category_key", nullable=True),
        "at": _spec("ts"),
    },
    "fact_created": {
        "fact_id": _spec("uuid7"),
        "line_id": _spec("uuid7"),
        "category_key": _spec("category_key"),
        "content": _spec("str", min_length=1, max_length=constants.CONTENT_MAX_LEN),
        "valid_from": _spec("ts"),
        "source": _spec("enum", values=["user_entry"]),
        "recorded_at": _spec("ts"),
        "duplicate_confirmation": _spec("duplicate_confirmation", optional=True),
    },
    "fact_evolved": {
        "fact_id": _spec("uuid7"),
        "line_id": _spec("uuid7"),
        "previous_fact_id": _spec("uuid7"),
        "content": _spec("str", min_length=1, max_length=constants.CONTENT_MAX_LEN),
        "valid_from": _spec("ts"),
        "source": _spec("enum", values=["user_entry"]),
        "recorded_at": _spec("ts"),
    },
    "fact_corrected": {
        "fact_id": _spec("uuid7"),
        "content": _spec("str", min_length=1, max_length=constants.CONTENT_MAX_LEN),
        "note": _spec("str", max_length=constants.NOTE_MAX_LEN, nullable=True),
        "recorded_at": _spec("ts"),
    },
    "fact_deleted": {
        "fact_id": _spec("uuid7"),
        "recorded_at": _spec("ts"),
    },
}


# ------------------------------------------------------------------ 校验实现
def _validate_value(spec, value, path, problems):
    if value is None:
        if spec.get("nullable"):
            return
        problems.append({"field": path, "message": "缺少必填字段或为 null"})
        return
    kind = spec["kind"]
    if kind == "str":
        if not isinstance(value, str):
            problems.append({"field": path, "message": "应为字符串"})
            return
        if "pattern" in spec and not re.match(spec["pattern"], value):
            problems.append({"field": path, "message": "不匹配 pattern %s" % spec["pattern"]})
        if len(value) < spec.get("min_length", 0):
            problems.append({"field": path, "message": "长度小于下限"})
        if "max_length" in spec and len(value) > spec["max_length"]:
            problems.append({"field": path, "message": "长度超过上限 %d" % spec["max_length"]})
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append({"field": path, "message": "应为整数"})
            return
        if "min_value" in spec and value < spec["min_value"]:
            problems.append({"field": path, "message": "小于下限 %d" % spec["min_value"]})
        if "max_value" in spec and value > spec["max_value"]:
            problems.append({"field": path, "message": "大于上限 %d" % spec["max_value"]})
    elif kind == "bool":
        if not isinstance(value, bool):
            problems.append({"field": path, "message": "应为布尔值"})
    elif kind == "ts":
        if not clock.is_timestamp(value):
            problems.append({"field": path, "message": "应为 RFC3339 带偏移时间（秒精度）"})
    elif kind == "month":
        if not clock.is_month(value):
            problems.append({"field": path, "message": "应为 YYYY-MM"})
    elif kind == "enum":
        if value not in spec["values"]:
            problems.append({"field": path, "message": "取值不在 %s 内" % (spec["values"],)})
    elif kind == "const":
        if value != spec["value"]:
            problems.append({"field": path, "message": "取值必须为 %r（v2 格式）" % (spec["value"],)})
    elif kind == "uuid7":
        if not ids.is_uuid7(value):
            problems.append({"field": path, "message": "应为 UUIDv7 规范形式（36 字符小写）"})
    elif kind == "category_key":
        if not isinstance(value, str) or not re.match(constants.CATEGORY_KEY_RE, value):
            problems.append({"field": path, "message": "应为合法类别 key（a-z0-9_-，1..32）"})
    elif kind == "duplicate_confirmation":
        _validate_duplicate_confirmation(value, path, problems)


def _validate_duplicate_confirmation(value, path, problems):
    if not isinstance(value, dict):
        problems.append({"field": path, "message": "应为对象"})
        return
    expected = {"confirmed", "candidates", "max_similarity", "token_sha256"}
    extra = sorted(set(value) - expected)
    if extra:
        problems.append({"field": path, "message": "不允许多余字段：%s" % (extra,)})
    if value.get("confirmed") is not True:
        problems.append({"field": path + ".confirmed", "message": "必须为 true"})
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 5:
        problems.append({"field": path + ".candidates", "message": "应为 1..5 个候选 fact_id"})
    else:
        for index, candidate in enumerate(candidates):
            if not ids.is_uuid7(candidate):
                problems.append({"field": "%s.candidates[%d]" % (path, index),
                                 "message": "应为 UUIDv7"})
    similarity = value.get("max_similarity")
    if isinstance(similarity, bool) or not isinstance(similarity, (int, float)) \
            or not 0 <= similarity <= 1:
        problems.append({"field": path + ".max_similarity", "message": "应为 0..1 数值"})
    digest = value.get("token_sha256")
    if not isinstance(digest, str) or not re.match(r"^[0-9a-f]{64}$", digest):
        problems.append({"field": path + ".token_sha256", "message": "应为 64 位小写十六进制"})


def validate_event_record(event):
    """事件记录的结构校验（不抛错，返回 [{field, message}]）。"""
    problems = []
    if not isinstance(event, dict):
        return [{"field": "", "message": "事件必须是 JSON 对象"}]
    for key in ("seq", "at", "type", "data"):
        if key not in event:
            problems.append({"field": key, "message": "缺少必填字段"})
    for key in sorted(set(event) - {"seq", "at", "type", "data"}):
        problems.append({"field": key, "message": "不允许多余字段"})
    if "seq" in event:
        seq = event["seq"]
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            problems.append({"field": "seq", "message": "应为 >=1 的整数"})
    if "at" in event and not clock.is_timestamp(event["at"]):
        problems.append({"field": "at", "message": "应为 RFC3339 带偏移时间"})
    event_type = event.get("type")
    if event_type not in EVENT_TYPES:
        problems.append({"field": "type", "message": "未知事件类型"})
        return problems
    data = event.get("data")
    if not isinstance(data, dict):
        problems.append({"field": "data", "message": "data 必须是 JSON 对象"})
        return problems
    spec = EVENT_DATA_SPECS[event_type]
    for key in sorted(set(data) - set(spec)):
        problems.append({"field": "data." + key, "message": "不允许多余字段"})
    for key, field_spec in spec.items():
        path = "data." + key
        if key not in data:
            if not field_spec.get("optional"):
                problems.append({"field": path, "message": "缺少必填字段"})
            continue
        _validate_value(field_spec, data[key], path, problems)
    # 跨字段规则
    if event_type == "category_added" and not problems:
        origin = data["origin"]
        order = data.get("template_order")
        if origin == "template" and order is None:
            problems.append({"field": "data.template_order",
                             "message": "origin=template 时必须非 null"})
        if origin == "custom" and order is not None:
            problems.append({"field": "data.template_order",
                             "message": "origin=custom 时必须为 null"})
    if event_type == "category_deleted" and not problems:
        if data["fact_handling"] == "migrate" and not data.get("migrate_to_category_key"):
            problems.append({"field": "data.migrate_to_category_key",
                             "message": "handling=migrate 时必须给出迁移目标"})
        if data["fact_handling"] == "delete" and data.get("migrate_to_category_key") is not None:
            problems.append({"field": "data.migrate_to_category_key",
                             "message": "handling=delete 时不得给出迁移目标"})
    for field in ("content", "note"):
        value = data.get(field)
        if isinstance(value, str) and ("\n" in value or "\r" in value):
            problems.append({"field": "data." + field, "message": "不得包含换行符（单行）"})
    return problems
