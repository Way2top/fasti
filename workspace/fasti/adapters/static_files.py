"""网页适配器的同源静态资源（§11.3.5）：白名单 + 读取 + Content-Type。

只有 3 个只读资源（页面 + 样式 + 脚本）；请求路径必须**精确命中**白名单，
因此不存在目录浏览与路径穿越面。所有资源均为包内文件、零外部请求。
"""

import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_static")

# 请求路径 → (磁盘文件名, Content-Type)
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


def is_static(path):
    """该请求路径是否属于白名单静态资源。"""
    return path in STATIC_FILES


def content_type(path):
    return STATIC_FILES[path][1]


def read_static(path):
    """读取白名单静态资源的原始字节（路径必来自 is_static 校验）。"""
    name = STATIC_FILES[path][0]
    with open(os.path.join(STATIC_DIR, name), "rb") as handle:
        return handle.read()
