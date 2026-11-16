"""C-10 M5：AC-18-10 页面加载零提取请求；trigger 仅 manual。"""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from _base import BaseStoreTest
from fasti.adapters import web
from fasti.core import api, extract_config, extract_llm, extract_state


class TestNoAutoTrigger(BaseStoreTest):
    def test_static_no_auto_trigger(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "fasti", "adapters", "web_static", "extract.js"),
                  encoding="utf-8") as handle:
            script = handle.read()
        for forbidden in ("auto_trigger", "maybeAutoTrigger", "last-trigger",
                          "web_open", "localStorage"):
            self.assertNotIn(forbidden, script)
        self.assertEqual(script.count('"/api/extract/run"'), 1)   # 仅按钮点击处
        self.assertIn('trigger: "manual"', script)
        with open(os.path.join(root, "fasti", "adapters", "web_static", "index.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        self.assertNotIn("打开网页自动", page)
        self.assertNotIn("auto-trigger", page)

    def _setup(self):
        self.dsh = os.path.join(self.tmp, "dsh")
        path = os.path.join(self.dsh, "sessions", "ws", "s1", "session.v4.jsonl")
        os.makedirs(os.path.dirname(path))
        events = [
            {"type": "session", "data": {"version": 4, "delegationDepth": 0,
                                         "isSeeded": False, "session_id": "s1"}},
            {"seq": 1, "type": "user/message",
             "data": {"id": "m1", "role": "user", "source": {"kind": "user"},
                      "content": [{"type": "text", "text": "我喜欢猫"}]}},
        ]
        with open(path, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        cfg = extract_config.load_config(self.data_dir)
        cfg["llm"].update({"base_url": "http://mock.local/v1", "model": "mock",
                           "api_key": "sk-mock"})
        cfg["extract"]["sources"] = {"dsh_home": self.dsh, "allow_plain": True}
        extract_config.save_config(self.data_dir, cfg)
        extract_config.set_consent(self.data_dir, True)
        extract_config.update_config(self.data_dir, {"extract": {"enabled": True}})
        self.calls = []
        state = {"n": 0}

        def transport(url, headers, body, timeout):
            state["n"] += 1
            payload = json.loads(body.decode("utf-8"))
            system = payload["messages"][0]["content"]
            if "分流" in system:
                return 200, self._envelope({"decision": "extract", "reason": "x"})
            if "可用类别" in system:
                return 200, self._envelope({"candidates": []})
            return 200, self._envelope({"judgments": []})
        extract_llm.set_transport(transport)
        self._state = state
        self.server, self.port = web.create_server(self.data_dir, 0, "tok")
        threading.Thread(target=self.server.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()

    @staticmethod
    def _envelope(payload):
        return json.dumps({"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False)}}]}).encode()

    def _get(self, path):
        joiner = "&" if "?" in path else "?"
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s%st=tok" % (self.port, path, joiner),
            headers={"X-Fasti-Token": "tok"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()

    def _post(self, path, body):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(body).encode(), method="POST",
            headers={"X-Fasti-Token": "tok", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown(); self.server.server_close()
        extract_llm.reset_transport()
        super().tearDown()

    def test_page_load_zero_llm_and_no_run(self):
        self._setup()
        for path in ("/", "/extract.js", "/app.js", "/api/extract/status",
                     "/api/extract/candidates?status=pending", "/api/extract/runs"):
            status, _body = self._get(path)
            self.assertEqual(status, 200)
        self.assertEqual(self._state["n"], 0)                   # 页面加载零 LLM 请求
        marks, _ = extract_state.load_watermarks(self.data_dir)
        self.assertEqual(marks["sessions"], {})                 # 水位不变
        self.assertEqual(api.list_extract_candidates(self.data_dir, "all")["items"], [])

    def test_manual_run_works_and_other_trigger_rejected(self):
        self._setup()
        status, payload = self._post("/api/extract/run", {"trigger": "manual"})
        self.assertEqual(status, 200)
        self.assertGreater(self._state["n"], 0)
        self.assertIn("run", payload["data"])
        calls = self._state["n"]
        status, payload = self._post("/api/extract/run", {"trigger": "web_open"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "USAGE_INVALID_ARGUMENT")
        self.assertEqual(self._state["n"], calls)               # 未发起 LLM 调用


if __name__ == "__main__":
    unittest.main()
