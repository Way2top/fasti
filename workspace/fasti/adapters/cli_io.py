"""CLI 输出上下文与呈现工具（C1 适配层；§11.3.3）。

模块级输出状态（_JSON/_QUIET/_COMMAND）原在 cli.py，现下沉到本模块
（ADR-021 风险③），由 main() 调用 set_context() 写入；_emit/_emit_error 等
函数体与基线逐字一致。
"""

import json
import sys


_JSON = False
_QUIET = False
_COMMAND = ""


def set_context(json_mode, quiet, command):
    """由 main() 调用：写入输出上下文（纯状态搬迁，行为不变）。"""
    global _JSON, _QUIET, _COMMAND
    _JSON = bool(json_mode)
    _QUIET = bool(quiet)
    _COMMAND = command


def _emit(command_id, data, text, warnings=None):
    if _JSON:
        envelope = {"ok": True, "command": command_id, "data": data}
        sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()
    if warnings and not _QUIET:
        for warning in warnings:
            sys.stderr.write(warning + "\n")
        sys.stderr.flush()


def _emit_error(exc, command_id):
    if _JSON:
        envelope = {"ok": False, "command": command_id, "error": exc.to_dict()}
        sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        sys.stderr.write("错误 %s：%s\n" % (exc.code, exc.message))
        sys.stderr.flush()
    return exc.exit_code


def _select(result, keys, include_warnings=False):
    data = {key: result[key] for key in keys if key in result}
    if include_warnings:
        data["warnings"] = result.get("warnings", [])
    return data


def _fact_render_text(action, fact, line_id, valid_from):
    return "%s %s（%s · 生效 %s）\n提示：fasti render" % (
        action, fact["fact_id"], line_id, valid_from)


def _freshness_text(kind, value):
    mapping = {
        "up-to-date": "已是最新",
        "pending-recompute": "待重算（运行 fasti recompute）",
        "pending-render": "待更新（运行 fasti render）",
        "missing": "缺失（运行 fasti %s）" % ("recompute" if kind == "profile" else "render"),
        "not-rendered": "尚未生成（运行 fasti %s）" % ("recompute" if kind == "profile"
                                                       else "render"),
    }
    return mapping.get(value, value)
