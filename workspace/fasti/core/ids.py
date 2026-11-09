"""标识方案（C-06 / ADR-023）：UUIDv7（RFC 9562）+ git 风格短前缀解析。

- 仅用标准库（`time` / `os` / `threading` / `re`）；**禁用** `uuid.uuid7()`（Python 3.14+）
  与任何第三方包（uuid6 / ulid-py 等）。
- 同毫秒内 `rand_a` 单调递增（RFC 9562 §6.2 Method 1）；12-bit 溢出则等到下一毫秒。
- 规范形式：小写、带连字符 36 字符；持久化/事件/导出/`--json` 只用规范形式。
- 短 ID：32 位十六进制的第 1–12 位（= 完整 48-bit 毫秒时间戳，不同毫秒创建的 ID
  前缀必不同），仅用于 CLI/交互显示与输入；持久化/导出/`--json` 只用规范形式。

测试接缝：`set_generator()` 可注入固定 clock / random 源的 `Uuid7Generator`。
"""

import os
import re
import threading
import time

from . import errors

UUID7_CANONICAL_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SHORT_ID_LEN = 12          # 默认短 ID 位数 = 完整 48-bit 毫秒时间戳（12 个十六进制字符）
SHORT_ID_RE = re.compile(r"^[0-9a-f]{%d}$" % SHORT_ID_LEN)
_HEX_RE = re.compile(r"^[0-9a-f]+$")
SHORT_PREFIX_MIN = 4
SHORT_PREFIX_MAX = 32
_AUTO_KEY_PREFIX = "c-"


def _system_ms():
    return time.time_ns() // 1_000_000


def format_uuid7(value):
    """128-bit 整数 → 小写带连字符 36 字符规范形式。"""
    hex32 = "%032x" % value
    return "-".join((hex32[0:8], hex32[8:12], hex32[12:16], hex32[16:20], hex32[20:32]))


class Uuid7Generator(object):
    """RFC 9562 UUIDv7 生成器（进程内同毫秒单调，Method 1）。

    clock_ms: 返回毫秒时间戳的可调用对象（默认 time.time_ns()//1e6）
    random_bytes: 返回 n 字节随机源的可调用对象（默认 os.urandom）
    两个参数都是测试确定性接缝；运行时使用默认值。
    """

    def __init__(self, clock_ms=None, random_bytes=None):
        self._clock_ms = clock_ms or _system_ms
        self._random_bytes = random_bytes or os.urandom
        self._lock = threading.Lock()
        self._last_ms = -1
        self._rand_a = 0

    def _rand_bits(self, n_bytes):
        return int.from_bytes(self._random_bytes(n_bytes), "big")

    def next(self):
        with self._lock:
            ms = int(self._clock_ms())
            if ms > self._last_ms:
                self._last_ms = ms
                self._rand_a = self._rand_bits(2) & 0x0FFF
            else:
                self._rand_a += 1
                if self._rand_a > 0x0FFF:
                    # 同毫秒 4096 个已耗尽：等到下一毫秒（RFC 9562 §6.2 Method 1）
                    while True:
                        ms = int(self._clock_ms())
                        if ms > self._last_ms:
                            break
                        time.sleep(0.0005)
                    self._last_ms = ms
                    self._rand_a = self._rand_bits(2) & 0x0FFF
                else:
                    ms = self._last_ms
            rand_b = self._rand_bits(8) & 0x3FFFFFFFFFFFFFFF
            value = (
                ((ms & 0xFFFFFFFFFFFF) << 80)
                | (0x7 << 76)
                | (self._rand_a << 64)
                | (0b10 << 62)
                | rand_b
            )
            return format_uuid7(value)


_GENERATOR = Uuid7Generator()


def new_uuid7():
    """生成一个 UUIDv7 规范形式（小写、36 字符）。"""
    return _GENERATOR.next()


def set_generator(generator):
    """测试接缝：替换默认生成器（可注入固定 clock/random）。"""
    global _GENERATOR
    _GENERATOR = generator


def reset_generator():
    """测试接缝：恢复系统时钟/随机源的默认生成器。"""
    global _GENERATOR
    _GENERATOR = Uuid7Generator()


def auto_category_key():
    """自定义类别省略 key 时的自动 key：c-<UUIDv7 前 12 位十六进制>（如 c-0190a1b23c4d）。"""
    return _AUTO_KEY_PREFIX + short_id(new_uuid7())


def is_uuid7(value):
    return isinstance(value, str) and bool(UUID7_CANONICAL_RE.match(value))


def hex32(value):
    """UUIDv7 规范形式 → 32 位小写十六进制（去连字符）。"""
    return value.replace("-", "").lower()


def short_id(value):
    """短 ID = 32 位十六进制的第 1–12 位（默认显示长度，仅用于显示/输入）。"""
    if not value:
        return value
    return hex32(value)[:SHORT_ID_LEN]


def normalize_reference(value, field="fact_id"):
    """入参归一化：完整 UUIDv7（大小写不敏感）或 4..32 位十六进制短前缀。

    返回 32 位小写十六进制；非法 ⇒ VALIDATION_INVALID_ID。
    """
    if not isinstance(value, str):
        raise errors.make(
            "VALIDATION_INVALID_ID",
            "ID 形式不合法：请提供完整 UUIDv7 或 4~32 位十六进制短前缀",
            details={"field": field, "value": value},
        )
    cleaned = value.strip().replace("-", "").lower()
    if len(cleaned) < SHORT_PREFIX_MIN or len(cleaned) > SHORT_PREFIX_MAX \
            or not _HEX_RE.match(cleaned):
        raise errors.make(
            "VALIDATION_INVALID_ID",
            "ID 形式不合法：请提供完整 UUIDv7（36 字符）或 4~32 位十六进制短前缀",
            details={"field": field, "value": value},
        )
    if len(cleaned) == SHORT_PREFIX_MAX:
        candidate = "-".join((cleaned[0:8], cleaned[8:12], cleaned[12:16],
                              cleaned[16:20], cleaned[20:32]))
        if not is_uuid7(candidate):
            raise errors.make(
                "VALIDATION_INVALID_ID",
                "ID 不是合法的 UUIDv7 规范形式（版本位必须为 7、变体位为 10xx）",
                details={"field": field, "value": value},
            )
    return cleaned


def resolve_reference(references, value, *, kind, not_found_code):
    """在 references（完整 UUIDv7 集合）中按唯一前缀解析，返回完整 UUIDv7。

    - 唯一命中 ⇒ 返回完整形式；
    - 0 命中 ⇒ not_found_code；
    - >1 命中 ⇒ VALIDATION_AMBIGUOUS_REFERENCE（details.candidates 最多 10 个短 ID）；
    - 非法 ⇒ VALIDATION_INVALID_ID。
    """
    prefix = normalize_reference(value, field=kind + "_id")
    matches = sorted(ref for ref in references if hex32(ref).startswith(prefix))
    if not matches:
        raise errors.make(not_found_code, details={"field": kind + "_id", "value": value})
    if len(matches) > 1:
        raise errors.make(
            "VALIDATION_AMBIGUOUS_REFERENCE",
            "ID 短前缀匹配到多个对象（%d 个），请多输入几位（4~32 位十六进制）" % len(matches),
            details={
                "field": kind + "_id",
                "value": value,
                "candidates": [short_id(match) for match in matches[:10]],
                "count": len(matches),
            },
        )
    return matches[0]

