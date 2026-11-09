"""时间语义（C-07 / ADR-025）：秒级完整时间戳 + 三档输入 + 自适应显示 + 瞬间比较。

规则（完整定义见 api-spec `conventions.valid_time_semantics`）：
- 存储/导出：RFC3339 带**本机偏移**、秒精度（如 2026-11-01T00:00:00+08:00），不用 UTC-Z。
- 输入三档：YYYY-MM（→ 月 1 日 00:00:00）/ YYYY-MM-DD（→ 当日 00:00:00）/
  YYYY-MM-DDTHH:MM[:SS]（秒缺省 00）；统一附加写入时本机偏移；不接受时区后缀/小数秒。
- 显示自适应：日 01 且 00:00:00 → YYYY-MM；00:00:00 → YYYY-MM-DD；否则完整时间戳。
- 段终 display_end：null→今；月起点→上一月；日起点→前一日；否则 display_time(end-1s)。
- 比较/排序按**瞬间**（解析偏移后比较），不得用字符串直接比较。

本模块的「当前时刻」可由测试注入（set_now_provider），生产环境用系统时钟。
"""

import re
from datetime import datetime, timedelta, timezone

from . import errors

TS_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$")
_MONTH_RE = re.compile(r"^([0-9]{4})-([0-9]{2})$")
_DAY_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_MINUTE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})$")
_SECOND_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})$")

_INPUT_FORMS = "YYYY-MM / YYYY-MM-DD / YYYY-MM-DDTHH:MM[:SS]"

_NOW_PROVIDER = None


# ------------------------------------------------------------------ 当前时刻
def now_local():
    """当前本机时刻（带本机偏移）；测试可通过 set_now_provider 注入。"""
    if _NOW_PROVIDER is not None:
        return _NOW_PROVIDER()
    return datetime.now().astimezone()


def set_now_provider(provider):
    """测试接缝：provider() → 带本机偏移的 aware datetime。"""
    global _NOW_PROVIDER
    _NOW_PROVIDER = provider


def reset_now_provider():
    global _NOW_PROVIDER
    _NOW_PROVIDER = None


def offset_string(now):
    """aware datetime → '+08:00' 形式的本机偏移。"""
    raw = now.strftime("%z")
    return raw[:3] + ":" + raw[3:] if raw else "+00:00"


def format_timestamp(dt):
    """aware datetime → RFC3339（秒精度、显式偏移）。"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + offset_string(dt)


def now_recorded_at():
    """写入路径：本机当前时间（recorded_at）。"""
    return format_timestamp(now_local())


def now_valid_from():
    """写入路径：当前本机时刻（秒精度）作为实际生效时间。"""
    return format_timestamp(now_local().replace(microsecond=0))


# ------------------------------------------------------------------ 基础解析
def now_utc_compact():
    """UTC 时间紧凑串（导出目录名/备份名用；不含分隔符）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def is_timestamp(value):
    """RFC3339 秒级时间戳（带偏移）判定。"""
    return is_valid_time(value)


def is_month(value):
    """v2 月格式（YYYY-MM）判定：仅供迁移脚本/兼容校验使用，不用于 v3 存储。"""
    return isinstance(value, str) and bool(_MONTH_RE.match(value))


def is_valid_time(value):
    if not isinstance(value, str) or not TS_RE.match(value):
        return False
    try:
        instant(value)
        return True
    except ValueError:
        return False


def instant(value):
    """RFC3339 时间戳 → aware datetime（瞬间比较用；失败抛 ValueError）。"""
    if not isinstance(value, str) or not TS_RE.match(value):
        raise ValueError("不是合法的 RFC3339 时间戳：%r" % (value,))
    return datetime.fromisoformat(value)


def wall_datetime(value):
    """时间戳的墙钟字面值（忽略偏移，用于显示/同桶判断）。"""
    if not is_valid_time(value):
        raise ValueError("不是合法的 RFC3339 时间戳：%r" % (value,))
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def compare(left, right):
    """按瞬间比较两个 RFC3339 时间戳：-1 / 0 / 1。"""
    a, b = instant(left), instant(right)
    return (a > b) - (a < b)


def add_seconds(value, seconds):
    return format_timestamp(instant(value) + timedelta(seconds=seconds))


def _parse_input(value, field="valid_from", invalid_code="VALIDATION_INVALID_VALID_FROM"):
    """解析三档输入 → (naive datetime, precision)。非法 ⇒ CoreError。"""
    if not isinstance(value, str):
        raise errors.make(invalid_code,
                          "%s 无法解析：必须为 %s 三档之一" % (field, _INPUT_FORMS),
                          details={"field": field, "value": value})
    text = value.strip()
    match = _MONTH_RE.match(text)
    try:
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            return datetime(year, month, 1, 0, 0, 0), "month"
        match = _DAY_RE.match(text)
        if match:
            return datetime(int(match.group(1)), int(match.group(2)),
                            int(match.group(3)), 0, 0, 0), "day"
        match = _MINUTE_RE.match(text)
        if match:
            groups = [int(item) for item in match.groups()]
            return datetime(groups[0], groups[1], groups[2], groups[3], groups[4], 0), "second"
        match = _SECOND_RE.match(text)
        if match:
            groups = [int(item) for item in match.groups()]
            return datetime(groups[0], groups[1], groups[2], groups[3], groups[4],
                            groups[5]), "second"
    except ValueError:
        pass
    raise errors.make(
        invalid_code,
        "%s 无法解析：必须为 %s 三档之一（不接受时区后缀/小数秒/不存在日期）"
        % (field, _INPUT_FORMS),
        details={"field": field, "value": value},
    )


def normalize_valid_time(value, now, field="valid_from",
                         invalid_code="VALIDATION_INVALID_VALID_FROM"):
    """三档输入 → {value: 完整时间戳, precision: month|day|second, naive, dt}。"""
    naive, precision = _parse_input(value, field, invalid_code)
    offset = offset_string(now)
    dt = naive.replace(tzinfo=timezone(
        timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6]))
        * (1 if offset[0] == "+" else -1)))
    return {
        "value": format_timestamp(dt),
        "precision": precision,
        "naive": naive,
        "dt": dt,
        "offset": offset,
    }


def parse_at(value, now):
    """--at 三档输入 → (原样输入, 实际查询瞬间, precision)；粗粒度=粒度起点。"""
    normalized = normalize_valid_time(value, now, field="at",
                                      invalid_code="VALIDATION_INVALID_AT")
    return value, normalized["value"], normalized["precision"]


# ------------------------------------------------------------------ 自适应显示
def display_time(value):
    """单时间点自适应显示：月 / 日 / 完整时刻（墙钟字面值）。"""
    if not is_valid_time(value):
        raise ValueError("不是合法的 RFC3339 时间戳：%r" % (value,))
    day = int(value[8:10])
    clock_part = value[11:19]
    if clock_part == "00:00:00":
        return value[:7] if day == 1 else value[:10]
    return value[:19]


def display_input_at(value):
    """--at 原样输入（三档）→ 标题显示（与 display_time(粒度起点) 结果一致）。"""
    text = (value or "").strip()
    match = _MONTH_RE.match(text)
    if match or _DAY_RE.match(text):
        return text
    match = _MINUTE_RE.match(text)
    if match:
        return text + ":00"
    match = _SECOND_RE.match(text)
    if match:
        wall = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
        if wall.hour == 0 and wall.minute == 0 and wall.second == 0:
            return text[:7] if wall.day == 1 else text[:10]
        return text
    if is_valid_time(text):
        return display_time(text)
    return text


def display_end(valid_to):
    """段终（闭区间显示）。"""
    if valid_to is None:
        return "今"
    wall = wall_datetime(valid_to)
    if wall.hour == 0 and wall.minute == 0 and wall.second == 0:
        if wall.day == 1:
            previous = (wall.replace(day=1) - timedelta(days=1))
            return previous.strftime("%Y-%m")
        return (wall - timedelta(days=1)).strftime("%Y-%m-%d")
    return display_time(format_timestamp(instant(valid_to) - timedelta(seconds=1)))


# ------------------------------------------------------- 同桶 auto-bump
def _same_bucket(precision, candidate_naive, head_value):
    head_wall = wall_datetime(head_value)
    if precision == "month":
        return (candidate_naive.year, candidate_naive.month) == \
               (head_wall.year, head_wall.month)
    if precision == "day":
        return candidate_naive.date() == head_wall.date()
    return False


def _bumped_value(now, head_value):
    moment = format_timestamp(now.replace(microsecond=0))
    if compare(moment, head_value) <= 0:
        moment = add_seconds(head_value, 1)
    return moment


def resolve_valid_from(value, now, head_value=None):
    """录入/演进时间解析 + 同粗粒度桶 auto-bump。

    返回 {value, precision, auto_bumped, input}。
    - 粗粒度（月/日）且候选瞬间 <= head 且与 head 同桶 ⇒ 取当前时刻（仍 <= head 则 head+1s）；
    - 显式时刻 / 不同桶 / 候选 > head：不提升，交由调用方做严格递增校验。
    """
    normalized = normalize_valid_time(value, now)
    candidate = normalized["value"]
    precision = normalized["precision"]
    auto_bumped = False
    if head_value is not None and compare(candidate, head_value) <= 0 \
            and precision in ("month", "day") \
            and _same_bucket(precision, normalized["naive"], head_value):
        candidate = _bumped_value(now, head_value)
        auto_bumped = True
    return {"value": candidate, "precision": precision,
            "auto_bumped": auto_bumped, "input": value}
