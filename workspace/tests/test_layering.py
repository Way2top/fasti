"""分层与依赖守卫：核心层不依赖 UI/网络；全包无第三方依赖；ADR-022 适配层边界。

扫描范围（ADR-021 风险④）：`fasti/core/**` **递归**（含拆分后的 `core/services/**`）。
"""

import ast
import os
import sys
import unittest

from _base import (CORE_DIR, PACKAGE_DIR, ROOT, iter_adapter_py_files,
                   iter_core_py_files, iter_package_py_files)


class TestLayeringAndImports(unittest.TestCase):
    def test_core_does_not_import_ui_modules(self):
        violations = []
        for path in iter_core_py_files():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            name = os.path.relpath(path, CORE_DIR)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root in ("argparse", "http", "socketserver", "webbrowser"):
                            violations.append("%s: import %s" % (name, alias.name))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    if root in ("argparse", "http", "socketserver", "webbrowser"):
                        violations.append("%s: from %s import ..." % (name, node.module))
        self.assertEqual(violations, [])

    def test_core_does_not_print(self):
        violations = []
        for path in iter_core_py_files():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            name = os.path.relpath(path, CORE_DIR)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "print":
                        violations.append("%s:print" % name)
                if isinstance(node, ast.Name) and node.id == "argv":
                    violations.append("%s:argv" % name)
        self.assertEqual(violations, [])

    def test_imports_are_stdlib_or_local(self):
        allowed = set(getattr(sys, "stdlib_module_names", set()))
        allowed.update({"fasti"})
        violations = []
        for path in iter_package_py_files():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            rel = os.path.relpath(path, ROOT)
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    modules = [node.module]
                for module in modules:
                    root = module.split(".")[0]
                    if root not in allowed:
                        violations.append("%s: %s" % (rel, module))
        self.assertEqual(violations, [])

    def test_adapters_only_import_core_api(self):
        """ADR-022：适配层只允许经 `fasti.core.api` 访问核心层。

        断言：禁止 import `core.services.*`；对 `..core` 的相对 import 只允许
        api / constants / errors 三个契约模块（领域逻辑必须走 api）。
        """
        allowed_core_names = {"api", "constants", "errors"}
        violations = []
        for path in iter_adapter_py_files():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            rel = os.path.relpath(path, ROOT)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "core.services" in alias.name:
                            violations.append("%s: import %s" % (rel, alias.name))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if "core.services" in node.module:
                        violations.append("%s: from %s import ..." % (rel, node.module))
                    elif node.module == "core" and node.level >= 1:
                        for alias in node.names:
                            if alias.name not in allowed_core_names:
                                violations.append(
                                    "%s: from ..core import %s" % (rel, alias.name))
        self.assertEqual(violations, [])
