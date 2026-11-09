"""字段校验（v2）：事实内容 / 备注 / 生效月 / 类别名称 / 类别 key。

校验错误码映射（ADR-018 / api-spec error_catalog）：
- 空内容 ⇒ VALIDATION_MISSING_CONTENT
- 换行 ⇒ VALIDATION_CONTENT_MULTILINE；超长 ⇒ VALIDATION_CONTENT_TOO_LONG
- 非法月份 ⇒ VALIDATION_INVALID_MONTH
- 非法类别 key ⇒ VALIDATION_CATEGORY_KEY_INVALID
"""

import re

from . import clock
from . import constants
from . import errors


def require_content(value):
    """事实内容：trim 后 1..2000 字符、禁止换行。返回 trim 后的内容。"""
    if value is None:
        raise errors.make("VALIDATION_MISSING_CONTENT", details={"field": "content"})
    if not isinstance(value, str):
        raise errors.make("VALIDATION_MISSING_CONTENT", "内容必须是字符串",
                          details={"field": "content"})
    if "\n" in value or "\r" in value:
        raise errors.make("VALIDATION_CONTENT_MULTILINE", details={"field": "content"})
    text = value.strip()
    if len(text) < constants.CONTENT_MIN_LEN:
        raise errors.make("VALIDATION_MISSING_CONTENT", details={"field": "content"})
    if len(text) > constants.CONTENT_MAX_LEN:
        raise errors.make(
            "VALIDATION_CONTENT_TOO_LONG",
            "内容过长（%d 字符），上限 %d 字符" % (len(text), constants.CONTENT_MAX_LEN),
            details={"field": "content", "max_length": constants.CONTENT_MAX_LEN},
        )
    return text


def require_note(value):
    """变更备注：trim 后 0..500 字符、禁止换行；空 ⇒ None。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise errors.make("VALIDATION_MISSING_CONTENT", "备注必须是字符串",
                          details={"field": "note"})
    if "\n" in value or "\r" in value:
        raise errors.make("VALIDATION_CONTENT_MULTILINE", details={"field": "note"})
    text = value.strip()
    if not text:
        return None
    if len(text) > constants.NOTE_MAX_LEN:
        raise errors.make(
            "VALIDATION_CONTENT_TOO_LONG",
            "备注过长（%d 字符），上限 %d 字符" % (len(text), constants.NOTE_MAX_LEN),
            details={"field": "note", "max_length": constants.NOTE_MAX_LEN},
        )
    return text


def require_month(value, field="valid_from"):
    """生效时间：接受 YYYY-MM 或 YYYY-MM-DD（截断到月）；否则 VALIDATION_INVALID_MONTH。"""
    if value is None:
        raise errors.make("VALIDATION_MISSING_VALID_FROM", details={"field": field})
    normalized = clock.normalize_month(value)
    if normalized is None:
        raise errors.make(
            "VALIDATION_INVALID_MONTH",
            "生效时间格式不合法：%r，应为 YYYY-MM" % (value,),
            details={"field": field, "value": value},
        )
    return normalized


def require_category_name(value):
    if value is None:
        raise errors.make("VALIDATION_MISSING_CONTENT", "缺少类别名称",
                          details={"field": "name"})
    if not isinstance(value, str):
        raise errors.make("VALIDATION_MISSING_CONTENT", "类别名称必须是字符串",
                          details={"field": "name"})
    text = value.strip()
    if len(text) < constants.CATEGORY_NAME_MIN_LEN:
        raise errors.make("VALIDATION_MISSING_CONTENT", "类别名称不能为空",
                          details={"field": "name"})
    if len(text) > constants.CATEGORY_NAME_MAX_LEN:
        raise errors.make(
            "VALIDATION_CONTENT_TOO_LONG",
            "类别名称过长（%d 字符），上限 %d 字符" % (len(text), constants.CATEGORY_NAME_MAX_LEN),
            details={"field": "name", "max_length": constants.CATEGORY_NAME_MAX_LEN},
        )
    return text


def require_category_key(value):
    """类别 key：可省略（None ⇒ None）；否则必须匹配 ^[a-z][a-z0-9_-]{0,31}$。"""
    if value is None:
        return None
    if not isinstance(value, str) or not re.match(constants.CATEGORY_KEY_RE, value):
        raise errors.make("VALIDATION_CATEGORY_KEY_INVALID",
                          details={"field": "key", "value": value})
    return value
