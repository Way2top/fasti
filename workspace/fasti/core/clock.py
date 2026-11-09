"""时间格式与月份语义（纯函数；只有 now_* 读系统时钟，且仅写路径使用）。

时间语义（architecture.md §2.3 / ADR-006）：
- recorded_at：RFC3339 带时区偏移、秒精度，例如 2026-11-09T13:30:00+08:00
- valid_from：日历月 YYYY-MM，无时区
- 有效区间左闭右开 [valid_from, valid_to)
"""

import re
from datetime import datetime, timezone

MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$"
)
_MONTH_DAY_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])-[0-9]{2}$")


def is_month(value):
    return isinstance(value, str) and bool(MONTH_RE.match(value))


def is_timestamp(value):
    return isinstance(value, str) and bool(TIMESTAMP_RE.match(value))


def normalize_month(value):
    """把 YYYY-MM 或 YYYY-MM-DD 归一为 YYYY-MM；不合法返回 None。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if is_month(text):
        return text
    if _MONTH_DAY_RE.match(text):
        return text[:7]
    return None


def prev_month(month):
    """返回上一月（YYYY-MM）。"""
    year = int(month[:4])
    mon = int(month[5:7])
    mon -= 1
    if mon == 0:
        year -= 1
        mon = 12
    return "%04d-%02d" % (year, mon)


def display_end(valid_to):
    """段终显示：valid_to 为 None ⇒ 「今」；否则显示其前一月。"""
    if valid_to is None:
        return "今"
    return prev_month(valid_to)


def format_recorded_at(dt):
    """datetime ⇒ RFC3339（秒精度、显式偏移，无小数秒）。"""
    offset = dt.strftime("%z")
    if not offset:
        offset = "+00:00"
    else:
        offset = offset[:3] + ":" + offset[3:]
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + offset


def now_recorded_at():
    """写入路径专用：本机当前时间（带显式偏移）。派生路径禁止调用。"""
    return format_recorded_at(datetime.now().astimezone())


def now_utc_compact():
    """导出目录名用的 UTC 紧凑时间戳（易变元信息，允许）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def parse_month_to_index(month):
    """YYYY-MM ⇒ 可比较的整数（年*12+月）。"""
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def compare_month(a, b):
    """a<b ⇒ -1；a==b ⇒ 0；a>b ⇒ 1。"""
    ia = parse_month_to_index(a)
    ib = parse_month_to_index(b)
    return (ia > ib) - (ia < ib)
