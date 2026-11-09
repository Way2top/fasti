"""CLI 退出码/短 ID 呈现 + 网页适配器端点与安全边界 + 静态资源守卫。"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib import error as urlerror
from urllib import request as urlrequest

from _base import _sha, ROOT
from fasti.core import api, clock

UUID7_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class TestCliAndWeb(unittest.TestCase):
    WEB_STATIC_DIR = os.path.join(ROOT, "fasti", "adapters", "web_static")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fasti-cli-")
        self.data_dir = os.path.join(self.tmp, "store")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        command = [sys.executable, os.path.join(ROOT, "run.py"),
                   "--data-dir", self.data_dir] + list(args)
        return subprocess.run(command, capture_output=True, text=True, timeout=120)

    def _static_text(self, name):
        with open(os.path.join(self.WEB_STATIC_DIR, name), encoding="utf-8") as handle:
            return handle.read()

    # ------------------------------------------------------------- CLI
    def test_cli_exit_codes(self):
        self.assertEqual(self._run("init").returncode, 0)
        missing = self._run("fact", "add", "--category", "tech", "--content", "x")
        self.assertEqual(missing.returncode, 4)
        self.assertIn("VALIDATION_MISSING_VALID_FROM", missing.stderr)
        added = self._run("fact", "add", "--category", "tech", "--content", "主力语言：C++",
                          "--valid-from", "2026-11")
        self.assertEqual(added.returncode, 0)
        unknown = self._run("nope")
        self.assertEqual(unknown.returncode, 2)
        json_error = self._run("--json", "fact", "show", "ffff")
        self.assertEqual(json_error.returncode, 3)
        payload = json.loads(json_error.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "NOT_FOUND_FACT")

    def test_cli_profile_text_matches_file(self):
        self._run("init")
        self._run("fact", "add", "--category", "tech", "--content", "主力语言：C++",
                  "--valid-from", "2026-11")
        self.assertEqual(self._run("recompute").returncode, 0)
        shown = self._run("profile", "show")
        self.assertEqual(shown.returncode, 0)
        with open(os.path.join(self.data_dir, "profile", "current.md"),
                  encoding="utf-8") as handle:
            self.assertEqual(shown.stdout, handle.read())

    def test_cli_short_id_display_and_input(self):
        self.assertEqual(self._run("init").returncode, 0)
        added = self._run("fact", "add", "--category", "tech", "--content", "主力语言：C++",
                          "--valid-from", "2026-11")
        self.assertEqual(added.returncode, 0)
        self.assertRegex(added.stdout, r"已录入 fact [0-9a-f]{12}（line [0-9a-f]{12} · 技术背景")
        short = re.search(r"已录入 fact ([0-9a-f]{12})", added.stdout).group(1)
        # text 输出显示短 ID；--json 输出完整 UUIDv7
        as_json = self._run("--json", "fact", "show", short)
        self.assertEqual(as_json.returncode, 0)
        payload = json.loads(as_json.stdout)
        fact_id = payload["data"]["fact"]["fact_id"]
        self.assertRegex(fact_id, UUID7_RE)
        self.assertEqual(api.short_id(fact_id), short)
        # 完整形式同样可作入参
        self.assertEqual(self._run("fact", "show", fact_id).returncode, 0)

    def test_cli_time_display_and_auto_bump(self):
        """C-07：粗粒度输入显示仍为月；同月再演进自动按当前时刻提升（auto_bumped）。"""
        self.assertEqual(self._run("init").returncode, 0)
        month = clock.now_local().strftime("%Y-%m")
        added = self._run("fact", "add", "--category", "tech", "--content", "x",
                          "--valid-from", month)
        self.assertEqual(added.returncode, 0)
        self.assertIn("生效 %s）" % month, added.stdout)          # 自适应显示=月
        self.assertIn("提示：fasti render", added.stdout)
        short = re.search(r"已录入 fact ([0-9a-f]{12})", added.stdout).group(1)
        evolved = self._run("fact", "evolve", short, "--content", "y",
                            "--valid-from", month)
        self.assertEqual(evolved.returncode, 0)
        self.assertIn("已在同月/同日之后，按当前时刻", evolved.stdout)
        payload = json.loads(self._run("--json", "fact", "show", short).stdout)
        full = payload["data"]["fact"]["valid_from"]
        self.assertRegex(full, r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}$")
        # --at 粗粒度 = 桶起点：月初时点看到的是演进前的值
        at = self._run("profile", "at", "--at", month)
        self.assertEqual(at.returncode, 0)
        self.assertIn("- x（%s 至 " % month, at.stdout)
        self.assertIn("实际查询瞬间：%s-01T00:00:00" % month, at.stdout)

    # ------------------------------------------------------------- Web
    def test_web_endpoints_and_security(self):
        from fasti.adapters import web

        api.init_store(self.data_dir)
        server, port = web.create_server(self.data_dir, 0, "test-token-123")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        base = "http://127.0.0.1:%d" % port

        def fetch(path, method="GET", body=None, token="test-token-123"):
            data = json.dumps(body).encode("utf-8") if body is not None else None
            headers = {"X-Fasti-Token": token}
            if data is not None:
                headers["Content-Type"] = "application/json"
            request = urlrequest.Request(base + path, data=data, headers=headers,
                                         method=method)
            with urlrequest.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))

        try:
            status, health = fetch("/api/health")
            self.assertEqual(status, 200)
            self.assertEqual(health["data"]["product"], "Fasti")
            with self.assertRaises(urlerror.HTTPError) as ctx:
                fetch("/api/profile", token="wrong-token")
            self.assertEqual(ctx.exception.code, 403)
            status, created = fetch("/api/facts", "POST",
                                    {"category": "tech", "content": "主力语言：C++",
                                     "valid_from": "2026-11"})
            self.assertEqual(status, 200)
            fact_id = created["data"]["fact"]["fact_id"]
            self.assertRegex(fact_id, UUID7_RE)
            # 网页 API 返回完整 UUIDv7；短前缀作为路径参数同样可解析
            short = api.short_id(fact_id)
            status, changed = fetch("/api/facts/%s/change" % short, "POST",
                                    {"change_type": "correct",
                                     "content": "主力语言：Java"})
            self.assertEqual(status, 200)
            self.assertEqual(changed["data"]["fact"]["fact_id"], fact_id)
            status, profile = fetch("/api/profile")
            self.assertIn("- 主力语言：Java", profile["data"]["text"])
            status, rendered = fetch("/api/render", "POST", {"host": "all"})
            self.assertEqual(status, 200)
            self.assertGreater(rendered["data"]["host_body_bytes"], 0)
            # C-07：同月粗输入再演进 ⇒ auto_bumped=true + warnings 明示实际时刻
            status, evolved = fetch("/api/facts/%s/change" % short, "POST",
                                    {"change_type": "evolve",
                                     "content": "主力语言：Rust",
                                     "valid_from": clock.now_local().strftime("%Y-%m")})
            self.assertEqual(status, 200)
            self.assertTrue(evolved["data"]["auto_bumped"])
            self.assertTrue(evolved["data"]["warnings"])
            # 类别以 key 为身份（无 category_id）
            status, categories = fetch("/api/categories")
            self.assertEqual([item["key"] for item in categories["data"]["items"]][:2],
                             ["basics", "tech"])
            self.assertNotIn("category_id", categories["data"]["items"][0])
            with self.assertRaises(urlerror.HTTPError) as ctx:
                fetch("/api/facts", "POST", {"category": "tech", "valid_from": "2026-11"})
            self.assertEqual(ctx.exception.code, 400)
            payload = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(payload["error"]["details"]["field"], "content")
            request = urlrequest.Request(base + "/api/profile", method="PUT",
                                         headers={"X-Fasti-Token": "test-token-123"})
            with self.assertRaises(urlerror.HTTPError) as ctx:
                urlrequest.urlopen(request, timeout=10)
            self.assertEqual(ctx.exception.code, 405)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    # ----------------------------------------------------- 静态资源守卫
    def test_web_static_js_syntax(self):
        app_js = os.path.join(self.WEB_STATIC_DIR, "app.js")
        text = self._static_text("app.js")
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))
        lines = text.split("\n")
        self.assertEqual(lines[0], "(function () {")
        self.assertEqual(lines[-2], "})();")
        self.assertIn('"use strict";', lines[1])
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不可用：已执行 IIFE 首尾结构断言，跳过 node --check")
        result = subprocess.run([node, "--check", app_js], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_web_static_assets_integrity(self):
        for name in ("index.html", "style.css", "app.js"):
            path = os.path.join(self.WEB_STATIC_DIR, name)
            self.assertTrue(os.path.isfile(path), name)
            text = self._static_text(name)
            self.assertGreater(len(text), 0, name)
            self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), name)
        index = self._static_text("index.html")
        self.assertEqual(index.count('href="/style.css"'), 1)
        self.assertEqual(index.count('src="/app.js"'), 1)
        for name in ("index.html", "style.css", "app.js"):
            self.assertEqual(re.findall(r"https?://", self._static_text(name)), [], name)
        # v2（C-06）起 app.js 有意更新了 category_key 语义；此处只保留结构/语法/引用守卫。
        app_js = self._static_text("app.js")
        self.assertIn("category_key", app_js)
        self.assertNotIn("category_id", app_js)
        # P1 回归：短 ID 必须先去掉连字符再截 12 位（与 CLI 一致），不能显示 "01a1c0c9-c2f"
        self.assertIn('replace(/-/g, "").slice(0, 12)', app_js)
        self.assertNotIn("fact.fact_id.slice(0, 12)", app_js)


if __name__ == "__main__":
    unittest.main()
