"""类别模板加载（原 api.py 的 _load_templates）。

与 templates/ 同级存放，保持 `__file__` 相对路径表达式不变（ADR-021 风险②）。
"""

import json
import os

from . import errors


def _load_templates():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "templates", "categories.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise errors.make("IO_READ_FAILED", "无法读取类别模板：%s（%s）" % (path, exc),
                          location=path)
    return data["categories"]
