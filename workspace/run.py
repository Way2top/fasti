#!/usr/bin/env python3
"""无安装即可运行的入口：python3 run.py <命令>（等价于 fasti <命令>）。

安装后可直接使用 console script：pip install --no-deps -e . && fasti --help
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fasti.adapters.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
