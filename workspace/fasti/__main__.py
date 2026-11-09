"""python3 -m fasti 入口（等价于 fasti 命令）。"""

import sys

from .adapters.cli import main

if __name__ == "__main__":
    sys.exit(main())
