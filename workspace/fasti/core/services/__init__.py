"""C0s 核心服务层实现（ADR-021/ADR-022）。

边界：只允许 `fasti.core.api` 引用本包；`services/*` 之间只允许依赖 `_shared`。
适配层与测试不得 import 本包（ADR-022 守卫见 tests/test_layering.py）。
"""
