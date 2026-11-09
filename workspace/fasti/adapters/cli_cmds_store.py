"""CLI 存储/派生/界面入口类命令实现（§11.3.3）。

只调用 `fasti.core.api`（ADR-019/ADR-022）；呈现经 cli_io。
"""

from ..core import api, constants
from . import cli_io
from .cli_io import _emit, _freshness_text, _select


class _JsonModeProxy:
    """`_cmd_web` 内嵌 on_ready 中 `if _JSON:` 的只读视图。

    模块级输出状态已下沉到 cli_io（ADR-021 风险③）；保留同名只读代理，
    使该函数体与基线 AST 完全一致（行为不变）。
    """

    def __bool__(self):
        return bool(cli_io._JSON)


_JSON = _JsonModeProxy()


def _cmd_init(args, data_dir):
    result = api.init_store(data_dir)
    lines = ["已初始化：%s" % result["data_dir"], "类别："]
    for category in result["categories"]:
        lines.append("- %s %s（启用）" % (category["key"], category["display_name"]))
    lines.extend([
        "下一步：",
        '  fasti fact add --category tech --content "主力语言：C++" --valid-from 2026-11',
        "  新增/编辑后运行 fasti render 使宿主产物生效",
    ])
    _emit("init", _select(result, ["data_dir", "categories"]), "\n".join(lines),
          result.get("warnings"))
    return 0


def _cmd_status(args, data_dir):
    report = api.get_status(data_dir)
    counts = report["counts"]
    lines = [
        "数据目录：%s" % report["data_dir"],
        "事件：%d；类别：%d（启用 %d）；命题线：%d；事实：%d（已删除 %d）；修订：%d"
        % (counts["events"], counts["categories_total"], counts["categories_enabled"],
           counts["lines"], counts["facts"], counts["deleted_facts"], counts["revisions"]),
        "当前画像：%s" % _freshness_text("profile", report["freshness"]["profile"]),
        "宿主产物：%s" % _freshness_text("host", report["freshness"]["host_artifacts"]),
    ]
    if report["artifacts"]:
        lines.append("产物：")
        for artifact in report["artifacts"]:
            lines.append("- %s：%s（%d B）"
                         % (artifact["path"], artifact["state"], artifact["bytes"]))
    else:
        lines.append("产物：无（尚未重算/渲染）")
    lines.append("宿主接入：")
    for entry in report["host_access"]:
        target = entry.get("target") or "-"
        if entry["host"] == "cursor" and entry["scope"] == "none":
            lines.append("- cursor：降级（无文件形式全局规则，需 --project 放置）")
        elif entry["scope"] == "global":
            lines.append("- %s：全局已接入（%s）%s"
                         % (entry["host"], target,
                            "" if entry["current"] else "，但内容不是最新产物"))
        else:
            lines.append("- %s：未接入" % entry["host"])
    sizes = report["host_sizes"]
    lines.append("产物大小：AGENTS.md %d B、CLAUDE.md %d B、fasti.mdc %d B；host_body %d B"
                 "（建议 ≤%d B，告警阈值 %d B）"
                 % (sizes["agents_bytes"], sizes["claude_bytes"], sizes["cursor_bytes"],
                    sizes["host_body_bytes"], constants.HOST_BODY_RECOMMENDED_BYTES,
                    constants.HOST_BODY_WARN_BYTES))
    backups = report["backups"]
    lines.append("最近备份：%s" % (backups.get("path") or "无"))
    _emit("status", report, "\n".join(lines), report.get("warnings"))
    return 0


def _cmd_recompute(args, data_dir):
    result = api.recompute(data_dir)
    by_path = {item["path"]: item["bytes"] for item in result["artifacts"]}
    lines = ["已重算：profile/current.md（%d B）、profile/history.md（%d B）"
             % (by_path.get("profile/current.md", 0), by_path.get("profile/history.md", 0))]
    lines.append("宿主产物：%s" % ("待更新（运行 fasti render）" if result["host_pending"]
                                  else "已是最新"))
    _emit("recompute", _select(result, ["artifacts", "host_pending"]), "\n".join(lines),
          result.get("warnings"))
    return 0


def _cmd_render(args, data_dir):
    result = api.render_hosts(data_dir, args.host)
    lines = ["已渲染："]
    for artifact in result["artifacts"]:
        lines.append("- %s（%d B）" % (artifact["path"], artifact["bytes"]))
    lines.append("host_body %d B（建议预算 %d B，告警阈值 %d B）"
                 % (result["host_body_bytes"], constants.HOST_BODY_RECOMMENDED_BYTES,
                    constants.HOST_BODY_WARN_BYTES))
    lines.append("接入说明：dist/INSTALL.md（首次请运行 fasti install-host；软链后渲染自动同步）")
    lines.append("生效时机：宿主新开会话时读取")
    _emit("render", _select(result, ["artifacts", "host_body_bytes", "warnings"],
                            include_warnings=True),
          "\n".join(lines), result.get("warnings"))
    return 0


def _cmd_export(args, data_dir):
    result = api.export_bundle(data_dir, args.out)
    text = "已导出：%s/（%d 个文件，%d B 结构化数据）\n校验：manifest.payload.sha256=%s\n导入：fasti import %s" % (
        result["output_dir"], result["files"], result["payload_bytes"],
        result["payload_sha256"], result["output_dir"])
    _emit("export", _select(result, ["output_dir", "files", "payload_sha256",
                                     "payload_bytes", "format_version"]),
          text, result.get("warnings"))
    return 0


def _cmd_import(args, data_dir):
    result = api.import_bundle(data_dir, args.bundle_dir, args.overwrite, args.backup)
    counts = result["counts"]
    lines = ["已导入：%d 条事件（%d 条事实 / %d 个类别）"
             % (result["events_imported"], counts["facts"], counts["categories"])]
    lines.append("备份：%s" % (result["backup_path"] or "无（目标为空或未要求备份）"))
    lines.append("下一步：fasti recompute && fasti render")
    _emit("import", _select(result, ["source", "events_imported", "counts", "backup_path"]),
          "\n".join(lines), result.get("warnings"))
    return 0


def _cmd_verify(args, data_dir):
    report = api.verify(data_dir, bool(args.deep))
    lines = []
    for check in report["checks"]:
        lines.append("CHECK %s %s — %s" % (check["id"], check["status"].upper(), check["detail"]))
    lines.append("结论：%s" % ("通过" if report["ok"] else "不通过"))
    _emit("verify", report, "\n".join(lines))
    return 0 if report["ok"] else 4


def _cmd_install_host(args, data_dir):
    if args.host == "all":
        hosts = ["agents", "claude", "cursor"]
    else:
        hosts = [args.host]
    result = api.install_hosts(data_dir, hosts, args.mode, args.project, args.dry_run)
    lines = ["宿主接入："]
    for action in result["actions"]:
        scope = "全局" if action["scope"] == "global" else "项目级（降级）"
        target = action["target"] or "-"
        message = action.get("message") or ""
        lines.append("- %s：%s → %s（%s）%s"
                     % (action["host"], action["status"], target, scope,
                        message and "：" + message or ""))
    lines.append("生效时机：宿主新会话")
    _emit("install-host", _select(result, ["actions"]), "\n".join(lines),
          result.get("warnings"))
    return 0


def _cmd_web(args, data_dir):
    from . import web as web_adapter

    def on_ready(url, port):
        data = {"url": url, "host": "127.0.0.1", "port": port, "pid": _pid()}
        if _JSON:
            _emit("web", data, url)
        else:
            text = ("Fasti 网页表单已启动：%s\n仅本机可访问；Ctrl-C 退出。\n"
                    "提示：首次使用请先运行 fasti init" % url)
            _emit("web", data, text)

    web_adapter.run_web(data_dir, port=args.port, token=args.token,
                        open_browser=not args.no_browser, on_ready=on_ready)
    return 0


def _pid():
    import os
    return os.getpid()
