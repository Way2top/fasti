"""Fasti：个人画像自用工具（P0）。

分层（ADR-019）：
- ``fasti.core``    ：核心层，UI 无关，不 import argparse/http.server，不 print；
- ``fasti.adapters``：界面适配层（网页表单为主、CLI 为次），只调用 ``fasti.core.api``。
"""

__version__ = "0.1.0"
