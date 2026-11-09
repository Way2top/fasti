"""网页表单适配器（C1W，P0 主界面；本机 UI 后端，不是对外 API 服务）。

边界（ADR-020，硬性）：
- 只绑 ``127.0.0.1``，绝不 ``0.0.0.0``/``::``；无跨机访问；
- 页面 + 同源 style.css/app.js（零外部请求，无 CDN/外链）⇒ 断网可用；
- 一次性会话令牌（URL ``?t=`` → ``X-Fasti-Token`` 请求头）+ 回环 Origin/Host 校验；
- 不发送任何 CORS 允许头；不做用户鉴权（单机单用户，OS 账户即边界）；
- 所有业务逻辑复用 ``fasti.core.api``；本模块不含领域逻辑、不直接读写数据文件。
"""

import errno
import json
import os
import secrets
import signal
import socketserver
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..core import api
from ..core import constants
from ..core import errors
from . import static_files

MAX_BODY_BYTES = 1048576

CONTEXT = {"data_dir": None, "token": None, "port": None}


# ------------------------------------------------------------ 错误码映射
def http_status_for(code):
    """错误码 → HTTP 状态码（api-spec web_adapter.error_mapping，固定）。"""
    if code == "WEB_FORBIDDEN":
        return 403
    if code.startswith("IMPORT_") or code.startswith("USAGE_") or code.startswith("VALIDATION_"):
        return 400
    if code.startswith("NOT_FOUND_"):
        return 404
    if code.startswith("CONFLICT_") or code == "STORE_NOT_INITIALIZED":
        return 409
    if code.startswith("CORRUPT_"):
        return 500
    if code.startswith("IO_") or code == "INTERNAL_UNEXPECTED":
        return 500
    return 500


def _error_envelope(exc):
    return {"ok": False, "error": exc.to_dict()}


# ------------------------------------------------------------ HTTP Handler
class FastiRequestHandler(BaseHTTPRequestHandler):
    server_version = "FastiLocal/%s" % constants.APP_VERSION
    protocol_version = "HTTP/1.1"

    # ---- 基础工具 ------------------------------------------------------
    def log_message(self, fmt, *args):  # 不写日志到磁盘/终端（隐私 + 确定性）
        return

    @property
    def context(self):
        return self.server.fasti_context

    def _send(self, status, raw, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _send_json(self, status, payload):
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False) + "\n").encode("utf-8")
        self._send(status, raw, "application/json; charset=utf-8")

    def _send_text(self, status, text):
        self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _ok(self, data):
        self._send_json(200, {"ok": True, "data": data})

    def _fail(self, exc):
        self._send_json(http_status_for(exc.code), _error_envelope(exc))

    def _forbidden(self, message="会话令牌无效或缺失"):
        self._send_json(403, _error_envelope(errors.make("WEB_FORBIDDEN", message)))

    # ---- 安全边界 ------------------------------------------------------
    def _check_host(self):
        port = self.context["port"]
        host_header = self.headers.get("Host") or ""
        allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if host_header not in allowed:
            self._forbidden("Host 不合法（本服务仅接受本机回环访问）")
            return False
        return True

    def _check_token(self):
        token = self.headers.get("X-Fasti-Token")
        if not token or not secrets.compare_digest(str(token), str(self.context["token"])):
            self._forbidden()
            return False
        return True

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # 非浏览器客户端（curl 等）允许
        port = self.context["port"]
        allowed = {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}
        if origin not in allowed:
            self._forbidden("Origin 不合法（仅允许本机同源请求）")
            return False
        return True

    # ---- 入口 ----------------------------------------------------------
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._method_not_allowed()

    def do_DELETE(self):
        self._method_not_allowed()

    def do_PATCH(self):
        self._method_not_allowed()

    def do_OPTIONS(self):
        self._method_not_allowed()

    def _method_not_allowed(self):
        error = errors.make("USAGE_INVALID_ARGUMENT", "方法不支持")
        self._send_json(405, _error_envelope(error))

    # ---- 主处理 --------------------------------------------------------
    def _handle(self, method):
        if not self._check_host():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self._serve_index(query)
            return
        if static_files.is_static(path):
            self._serve_static(path)
            return
        if not path.startswith("/api/"):
            self._send_json(404, _error_envelope(
                errors.make("NOT_FOUND_PATH", "路径不存在：%s" % path)))
            return
        if not self._check_token():
            return
        if method == "POST" and not self._check_origin():
            return

        try:
            if method == "GET":
                self._route_get(path, query)
            else:
                self._route_post(path)
        except errors.CoreError as exc:
            self._fail(exc)
        except Exception as exc:  # noqa: BLE001
            self._fail(errors.make("INTERNAL_UNEXPECTED",
                                   "%s: %s" % (type(exc).__name__, exc)))

    def _serve_index(self, query):
        token = (query.get("t") or [""])[0]
        if not token or not secrets.compare_digest(str(token), str(self.context["token"])):
            self._send_text(403, "会话令牌无效或缺失：请使用 fasti web 启动时打印的 URL 打开页面。")
            return
        try:
            raw = static_files.read_static("/")
        except OSError:
            self._send_text(500, "页面资源缺失")
            return
        self._send(200, raw, static_files.content_type("/"))

    def _serve_static(self, path):
        try:
            raw = static_files.read_static(path)
        except OSError:
            self._send_text(404, "静态资源缺失")
            return
        self._send(200, raw, static_files.content_type(path))

    # ---- 请求体 --------------------------------------------------------
    def _read_json_body(self):
        content_type = self.headers.get("Content-Type") or ""
        if not content_type.lower().split(";")[0].strip() == "application/json":
            self.close_connection = True
            raise _HttpFailure(415, errors.make("USAGE_INVALID_ARGUMENT",
                                                "写请求必须使用 application/json"))
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header or "0")
        except ValueError:
            self.close_connection = True
            raise _HttpFailure(400, errors.make("USAGE_INVALID_ARGUMENT",
                                                "Content-Length 不合法"))
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise _HttpFailure(413, errors.make("USAGE_INVALID_ARGUMENT",
                                                "请求体超过上限 %d B" % MAX_BODY_BYTES))
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self.close_connection = True
            raise _HttpFailure(400, errors.make("USAGE_INVALID_ARGUMENT",
                                                "请求体不是合法 JSON：%s" % exc))
        if not isinstance(value, dict):
            self.close_connection = True
            raise _HttpFailure(400, errors.make("USAGE_INVALID_ARGUMENT",
                                                "请求体必须是 JSON 对象"))
        return value

    def _route_post(self, path):
        parts = [part for part in path.split("/") if part]
        # parts[0] == "api"
        try:
            body = self._read_json_body()
        except _HttpFailure as failure:
            self._send_json(failure.status, _error_envelope(failure.error))
            return
        try:
            self._dispatch_post(parts, body)
        except errors.CoreError as exc:
            self._fail(exc)

    def _dispatch_post(self, parts, body):
        data_dir = self.context["data_dir"]
        if parts == ["api", "categories"]:
            name = _require_field(body, "name")
            key = body.get("key")
            enabled = body.get("enabled", True)
            if not isinstance(enabled, bool):
                raise errors.make("USAGE_INVALID_ARGUMENT", "enabled 必须是布尔值",
                                  details={"field": "enabled"})
            result = api.add_category(data_dir, name, key, enabled)
            self._ok({"category": result["category"]})
            return
        if len(parts) == 4 and parts[1] == "categories" and parts[3] == "enabled":
            enabled = _require_field(body, "enabled")
            if not isinstance(enabled, bool):
                raise errors.make("USAGE_INVALID_ARGUMENT", "enabled 必须是布尔值",
                                  details={"field": "enabled"})
            result = api.set_category_enabled(data_dir, parts[2], enabled)
            self._ok({"category": result["category"]})
            return
        if len(parts) == 4 and parts[1] == "categories" and parts[3] == "delete":
            handling = body.get("handling")
            migrate_to = body.get("migrate_to")
            result = api.delete_category(data_dir, parts[2], handling, migrate_to)
            self._ok({"category": result["category"],
                      "affected_facts": result["affected_facts"]})
            return
        if parts == ["api", "facts"]:
            category = _require_field(body, "category")
            content = _require_field(body, "content")
            valid_from = _require_field(body, "valid_from", code="VALIDATION_MISSING_VALID_FROM",
                                        message="缺少生效时间：请提供「从何时起为真」")
            result = api.add_fact(data_dir, category, content, valid_from)
            self._ok({"fact": result["fact"], "line": result["line"],
                      "warnings": result.get("warnings", [])})
            return
        if len(parts) == 4 and parts[1] == "facts" and parts[3] == "change":
            change_type = _require_field(body, "change_type",
                                         code="VALIDATION_CHANGE_TYPE_REQUIRED",
                                         message="必须选择变更类型：演进（evolve）或纠正（correct）")
            content = _require_field(body, "content")
            result = api.change_fact(data_dir, parts[2], change_type, content,
                                     body.get("valid_from"), body.get("note"))
            self._ok({"change_type": result["change_type"], "fact": result["fact"],
                      "revision": result["revision"],
                      "warnings": result.get("warnings", [])})
            return
        if len(parts) == 4 and parts[1] == "facts" and parts[3] == "delete":
            result = api.delete_fact(data_dir, parts[2])
            self._ok({"fact": result["fact"], "warnings": result.get("warnings", [])})
            return
        if parts == ["api", "recompute"]:
            result = api.recompute(data_dir)
            self._ok({"artifacts": result["artifacts"],
                      "host_pending": result["host_pending"],
                      "warnings": result.get("warnings", [])})
            return
        if parts == ["api", "render"]:
            host = body.get("host", "all")
            result = api.render_hosts(data_dir, host)
            self._ok({"artifacts": result["artifacts"],
                      "host_body_bytes": result["host_body_bytes"],
                      "warnings": result.get("warnings", [])})
            return
        self._send_json(404, _error_envelope(
            errors.make("NOT_FOUND_PATH", "路径不存在")))

    # ---- GET 路由 ------------------------------------------------------
    def _route_get(self, path, query):
        data_dir = self.context["data_dir"]
        parts = [part for part in path.split("/") if part]
        if parts == ["api", "health"]:
            self._ok({
                "product": constants.APP_NAME,
                "contract_version": constants.CONTRACT_VERSION,
                "data_dir": os.path.abspath(data_dir),
                "initialized": api.is_initialized(data_dir),
            })
            return
        if parts == ["api", "status"]:
            self._ok(api.get_status(data_dir))
            return
        if parts == ["api", "profile"]:
            category = _query_one(query, "category")
            trace = _query_one(query, "trace") in ("1", "true", "True")
            view = api.get_profile(data_dir, category, trace)
            self._ok({"text": api.profile_text(view), "view": view})
            return
        if parts == ["api", "profile", "at"]:
            at = _query_one(query, "at")
            if not at:
                raise errors.make("VALIDATION_MISSING_VALID_FROM",
                                  "缺少查询时点：请提供 at=YYYY-MM",
                                  details={"field": "at"})
            view = api.get_profile_at(data_dir, at, _query_one(query, "category"))
            self._ok({"text": api.profile_at_text(view), "view": view})
            return
        if parts == ["api", "history"]:
            view = api.get_history(data_dir, _query_one(query, "category"))
            self._ok({
                "text": api.history_text(view),
                "groups": view["groups"],
                "category_events": view["category_events"],
            })
            return
        if parts == ["api", "categories"]:
            self._ok({"items": api.list_categories(data_dir)["items"]})
            return
        if parts == ["api", "facts"]:
            state = _query_one(query, "state") or "current"
            result = api.list_facts(data_dir, _query_one(query, "category"),
                                    _query_one(query, "line"), state)
            self._ok({"items": result["items"], "counts": result["counts"]})
            return
        self._send_json(404, _error_envelope(
            errors.make("NOT_FOUND_PATH", "路径不存在")))


class _HttpFailure(Exception):
    def __init__(self, status, error):
        self.status = status
        self.error = error
        super().__init__(error.message)


def _query_one(query, key):
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


FIELD_LABELS = {
    "category": "类别",
    "content": "内容",
    "valid_from": "从何时起为真（YYYY-MM）",
    "change_type": "变更类型（演进 / 纠正）",
    "note": "备注",
    "enabled": "启用状态",
    "handling": "存量事实处置方式",
    "migrate_to": "迁移目标类别",
    "name": "类别名称",
    "key": "类别 key",
    "at": "查询时点（YYYY-MM）",
}


def _require_field(body, field, code="VALIDATION_MISSING_CONTENT", message=None):
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        if message is None:
            message = "缺少「%s」" % FIELD_LABELS.get(field, field)
        raise errors.make(code, message, details={"field": field})
    return value


# ------------------------------------------------------------ 服务器生命周期
class FastiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        """跳过 HTTPServer 默认的 socket.getfqdn 反查。

        默认实现会做反向 DNS 查询，在断网/受限网络下可能阻塞几十秒，
        违反「断网可用」（AC-13-2）。这里直接使用回环地址作为 server_name。
        """
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


def create_server(data_dir, port, token):
    context = {"data_dir": os.path.abspath(data_dir), "token": token, "port": port}
    try:
        server = FastiServer(("127.0.0.1", port), FastiRequestHandler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            raise errors.make(
                "CONFLICT_PORT_IN_USE",
                "监听端口 %d 已被占用；请改用 --port 0（由系统分配）或其他端口" % port,
            )
        raise errors.make("IO_WRITE_FAILED", "无法绑定 127.0.0.1:%d（%s）" % (port, exc))
    actual_port = server.server_address[1]
    context["port"] = actual_port
    server.fasti_context = context
    return server, actual_port


def run_web(data_dir, port=8787, token=None, open_browser=True, on_ready=None):
    """前台阻塞运行；Ctrl-C 退出 0；无常驻文件、无日志落盘。"""
    token = token or secrets.token_urlsafe(24)
    server, actual_port = create_server(data_dir, port, token)
    url = "http://127.0.0.1:%d/?t=%s" % (actual_port, token)

    def _stop(signum, frame):
        raise KeyboardInterrupt

    previous_term = None
    try:
        previous_term = signal.signal(signal.SIGTERM, _stop)
    except (ValueError, OSError):
        previous_term = None

    if on_ready is not None:
        on_ready(url, actual_port)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打开浏览器失败不影响服务
            pass
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if previous_term is not None:
            try:
                signal.signal(signal.SIGTERM, previous_term)
            except (ValueError, OSError):
                pass
    return 0
