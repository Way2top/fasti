"""测试公共基座：路径常量 / helpers / BaseStoreTest / 递归遍历工具 / ID 注入接缝。

本文件不含 test_ 方法，不会被 unittest 当测试模块收集。
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fasti.core import api  # noqa: E402
from fasti.core import clock  # noqa: E402
from fasti.core import ids  # noqa: E402

CORE_DIR = os.path.join(ROOT, "fasti", "core")
PACKAGE_DIR = os.path.join(ROOT, "fasti")
ADAPTERS_DIR = os.path.join(ROOT, "fasti", "adapters")


def iter_py_files(base_dir):
    """递归遍历 base_dir 下的 .py 文件（跳过 __pycache__）。"""
    for dirpath, dirnames, filenames in os.walk(base_dir):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def iter_core_py_files():
    """核心层全部 .py（含 core/services/**；ADR-021 风险④：分层扫描必须递归）。"""
    return iter_py_files(CORE_DIR)


def iter_package_py_files():
    return iter_py_files(PACKAGE_DIR)


def iter_adapter_py_files():
    return iter_py_files(ADAPTERS_DIR)


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _canonical_body(path):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    offset = 0
    for line in text.split("\n"):
        if line.startswith("## "):
            return text[offset:]
        offset += len(line) + 1
    return ""


class _StepClock(object):
    """顺序推进的毫秒时钟（测试用）：每次调用后推进 step_ms（0 = 同毫秒）。"""

    def __init__(self, start_ms=1_700_000_000_000, step_ms=0):
        self.current = start_ms
        self.step_ms = step_ms

    def __call__(self):
        value = self.current
        self.current += self.step_ms
        return value


def inject_ids(start_ms=1_700_000_000_000, step_ms=0):
    """测试接缝：注入确定性的 UUIDv7 生成器（固定时钟序列 + 固定随机字节）。

    返回 generator；用例结束应调用 `reset_ids()` 恢复默认生成器。
    """
    generator = ids.Uuid7Generator(
        clock_ms=_StepClock(start_ms, step_ms),
        random_bytes=lambda n: bytes([0x5A]) * n,
    )
    ids.set_generator(generator)
    return generator


def reset_ids():
    ids.reset_generator()


# 固定本机时刻（+08:00）：C-07 的「写入时本机偏移」与 auto-bump 依赖注入时钟
FIXED_NOW = datetime(2026, 11, 10, 0, 5, 0,
                     tzinfo=timezone(timedelta(hours=8)))


class _SequenceNow(object):
    """按顺序返回预设时刻（用完重复最后一个），用于 auto-bump/时钟推进用例。"""

    def __init__(self, moments):
        self.moments = list(moments)
        self.index = 0

    def __call__(self):
        moment = self.moments[min(self.index, len(self.moments) - 1)]
        self.index += 1
        return moment


def fixed_moment(month_offset=0):
    """FIXED_NOW 的相对时刻（便于构造跨月/跨日场景）。"""
    return FIXED_NOW


def inject_now(moments=None):
    """测试接缝：注入本机时钟；默认固定 FIXED_NOW（+08:00）。"""
    if moments is None:
        clock.set_now_provider(lambda: FIXED_NOW)
    elif isinstance(moments, (list, tuple)):
        clock.set_now_provider(_SequenceNow(moments))
    else:
        clock.set_now_provider(moments)


def reset_now():
    clock.reset_now_provider()


class BaseStoreTest(unittest.TestCase):
    def setUp(self):
        # 注入稀疏时钟：每个 UUID 间隔 70s（不同毫秒），使 12 位短 ID（= 完整时间戳）
        # 唯一；需要构造同毫秒歧义的用例自行 inject_ids(step_ms=0)
        inject_ids(step_ms=70_000)
        inject_now()
        self.tmp = tempfile.mkdtemp(prefix="fasti-test-")
        self.data_dir = os.path.join(self.tmp, "store")
        api.init_store(self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        reset_ids()
        reset_now()
