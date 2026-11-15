"""事件记录结构校验（format_version = constants.FORMAT_VERSION）：UUIDv7 + 秒级时间 + C-08 确认字段。

供导入流水线第 2 步使用：结构不合法 ⇒ IMPORT_INVALID_RECORD（不在此抛错，返回问题列表）。
字段契约见 api-spec file_formats.events_jsonl（当前格式）。
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
        "source": _spec("enum", values=["user_entry", "auto_extract"]),
        "recorded_at": _spec("ts"),
        "duplicate_confirmation": _spec("duplicate_confirmation", optional=True),
        "extraction": _spec("extraction", optional=True),
    },
    "fact_evolved": {
        "fact_id": _spec("uuid7"),
        "line_id": _spec("uuid7"),
        "previous_fact_id": _spec("uuid7"),
        "content": _spec("str", min_length=1, max_length=constants.CONTENT_MAX_LEN),
        "valid_from": _spec("ts"),
        "source": _spec("enum", values=["user_entry", "auto_extract"]),
        "recorded_at": _spec("ts"),
        "extraction": _spec("extraction", optional=True),
    },
    "fact_corrected": {
        "fact_id": _spec("uuid7"),
        "content": _spec("str", min_length=1, max_length=constants.CONTENT_MAX_LEN),
        "note": _spec("str", max_length=constants.NOTE_MAX_LEN, nullable=True),
        "recorded_at": _spec("ts"),
        "extraction": _spec("extraction", optional=True),
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
            problems.append({"field": path, "message": "取值必须为 %r（当前格式）" % (spec["value"],)})
    elif kind == "uuid7":
        if not ids.is_uuid7(value):
            problems.append({"field": path, "message": "应为 UUIDv7 规范形式（36 字符小写）"})
    elif kind == "category_key":
        if not isinstance(value, str) or not re.match(constants.CATEGORY_KEY_RE, value):
            problems.append({"field": path, "message": "应为合法类别 key（a-z0-9_-，1..32）"})
    elif kind == "duplicate_confirmation":
        _validate_duplicate_confirmation(value, path, problems)
    elif kind == "extraction":
        _validate_extraction(value, path, problems)


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


def _validate_extraction(value, path, problems):
    if not isinstance(value, dict):
        problems.append({"field": path, "message": "应为对象"})
        return
    expected = {"candidate_id", "session_id", "message_ids", "message_ordinals",
                "mode", "run_id", "llm_relation", "system_relation", "confidence",
                "reviewed_by_user", "recall"}
    if set(value) != expected:
        problems.append({"field": path, "message": "字段集不合法"})
        return
    if not isinstance(value["candidate_id"], str) \
            or not re.match(r"^cand-[0-9a-f]{20}$", value["candidate_id"]):
        problems.append({"field": path + ".candidate_id", "message": "应为 cand- 20 位十六进制"})
    if not isinstance(value["session_id"], str) or not value["session_id"]:
        problems.append({"field": path + ".session_id", "message": "应为非空字符串"})
    for key in ("message_ids", "message_ordinals"):
        if not isinstance(value[key], list) or not value[key]:
            problems.append({"field": path + "." + key, "message": "应为非空数组"})
    if not all(isinstance(item, str) for item in value.get("message_ids") or []):
        problems.append({"field": path + ".message_ids", "message": "元素应为字符串"})
    if not all(isinstance(item, int) and not isinstance(item, bool)
               for item in value.get("message_ordinals") or []):
        problems.append({"field": path + ".message_ordinals", "message": "元素应为整数"})
    if value["mode"] not in ("auto_write", "user_approved"):
        problems.append({"field": path + ".mode", "message": "取值非法"})
    if not isinstance(value["run_id"], str) or not value["run_id"]:
        problems.append({"field": path + ".run_id", "message": "应为非空字符串"})
    if value["llm_relation"] not in ("new", "update", "conflict"):
        problems.append({"field": path + ".llm_relation", "message": "取值非法"})
    if value["system_relation"] not in ("none", "similar"):
        problems.append({"field": path + ".system_relation", "message": "取值非法"})
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0 <= confidence <= 1:
        problems.append({"field": path + ".confidence", "message": "应为 0..1"})
    if not isinstance(value["reviewed_by_user"], bool):
        problems.append({"field": path + ".reviewed_by_user", "message": "应为布尔值"})
    recall = value["recall"]
    if not isinstance(recall, dict) or set(recall) != {"mode", "current_fact_ids",
                                                      "historical_fact_ids", "truncated"}:
        problems.append({"field": path + ".recall", "message": "recall 结构不合法"})
    else:
        if recall["mode"] not in ("full", "retrieval"):
            problems.append({"field": path + ".recall.mode", "message": "取值非法"})
        for key in ("current_fact_ids", "historical_fact_ids"):
            if not isinstance(recall[key], list):
                problems.append({"field": path + ".recall." + key, "message": "应为数组"})
        if not isinstance(recall["truncated"], bool):
            problems.append({"field": path + ".recall.truncated", "message": "应为布尔值"})


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
    if event_type in ("fact_created", "fact_evolved"):
        source = data.get("source")
        extraction = data.get("extraction")
        if source == "auto_extract" and extraction is None:
            problems.append({"field": "data.extraction",
                             "message": "source=auto_extract 时必须提供 extraction"})
        if source == "user_entry" and extraction is not None:
            problems.append({"field": "data.extraction",
                             "message": "source=user_entry 时不得带 extraction"})
    if event_type == "fact_corrected" and data.get("extraction") is not None:
        extraction = data["extraction"]
        if not isinstance(extraction, dict):
            problems.append({"field": "data.extraction", "message": "应为对象"})
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
