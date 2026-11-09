"""CLI 适配器入口（C1 次选入口）：分发 / 命令 id / main。

参数解析在 cli_parser、输出上下文在 cli_io、命令实现在 cli_cmds_*；
本模块只保留入口与分发（§11.3.3）。主界面是 `fasti web`（adapters/web.py）。
"""

import sys

from ..core import api, errors
from . import cli_io
from .cli_parser import build_parser
from .cli_cmds_content import (
    _cmd_category_add,
    _cmd_category_delete,
    _cmd_category_list,
    _cmd_category_toggle,
    _cmd_fact_add,
    _cmd_fact_change,
    _cmd_fact_delete,
    _cmd_fact_list,
    _cmd_fact_show,
    _cmd_profile_at,
    _cmd_profile_history,
    _cmd_profile_show,
)
from .cli_cmds_store import (
    _cmd_export,
    _cmd_import,
    _cmd_init,
    _cmd_install_host,
    _cmd_recompute,
    _cmd_render,
    _cmd_status,
    _cmd_verify,
    _cmd_web,
)


def _dispatch(args):
    data_dir = api.resolve_data_dir(getattr(args, "data_dir", None))
    command = args.command
    if command == "init":
        return _cmd_init(args, data_dir)
    if command == "status":
        return _cmd_status(args, data_dir)
    if command == "fact":
        if args.fact_command == "add":
            return _cmd_fact_add(args, data_dir)
        if args.fact_command in ("change", "evolve", "correct"):
            return _cmd_fact_change(args, data_dir)
        if args.fact_command == "delete":
            return _cmd_fact_delete(args, data_dir)
        if args.fact_command == "list":
            return _cmd_fact_list(args, data_dir)
        if args.fact_command == "show":
            return _cmd_fact_show(args, data_dir)
    if command == "category":
        if args.category_command == "list":
            return _cmd_category_list(args, data_dir)
        if args.category_command == "add":
            return _cmd_category_add(args, data_dir)
        if args.category_command in ("enable", "disable"):
            return _cmd_category_toggle(args, data_dir)
        if args.category_command == "delete":
            return _cmd_category_delete(args, data_dir)
    if command == "profile":
        if args.profile_command == "show":
            return _cmd_profile_show(args, data_dir)
        if args.profile_command == "at":
            return _cmd_profile_at(args, data_dir)
        if args.profile_command == "history":
            return _cmd_profile_history(args, data_dir)
    if command == "recompute":
        return _cmd_recompute(args, data_dir)
    if command == "render":
        return _cmd_render(args, data_dir)
    if command == "export":
        return _cmd_export(args, data_dir)
    if command == "import":
        return _cmd_import(args, data_dir)
    if command == "verify":
        return _cmd_verify(args, data_dir)
    if command == "web":
        return _cmd_web(args, data_dir)
    if command == "install-host":
        return _cmd_install_host(args, data_dir)
    raise errors.make("USAGE_INVALID_ARGUMENT", "未知命令：%r" % (command,))


def _command_id_from_args(args):
    command = getattr(args, "command", None)
    if command == "fact":
        return "fact.%s" % (getattr(args, "fact_command", "") or "")
    if command == "category":
        return "category.%s" % (getattr(args, "category_command", "") or "")
    if command == "profile":
        return "profile.%s" % (getattr(args, "profile_command", "") or "")
    return command or ""


def _guess_command(argv):
    words = [word for word in argv if not word.startswith("-")]
    if not words:
        return ""
    if words[0] in ("fact", "category", "profile") and len(words) > 1:
        return "%s.%s" % (words[0], words[1])
    return words[0]


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(raw_argv)
    except errors.CoreError as exc:
        cli_io.set_context("--json" in raw_argv, False, "")
        return cli_io._emit_error(exc, _guess_command(raw_argv))
    except SystemExit as exc:
        return int(exc.code or 0)
    cli_io.set_context(getattr(args, "json", False), getattr(args, "quiet", False),
                       _command_id_from_args(args))
    try:
        return _dispatch(args)
    except errors.CoreError as exc:
        return cli_io._emit_error(exc, cli_io._COMMAND)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - 必须转成稳定错误码（INTERNAL_UNEXPECTED）
        error = errors.make("INTERNAL_UNEXPECTED", "%s: %s（命令：%s）"
                            % (type(exc).__name__, exc, cli_io._COMMAND))
        return cli_io._emit_error(error, cli_io._COMMAND)


if __name__ == "__main__":
    sys.exit(main())
