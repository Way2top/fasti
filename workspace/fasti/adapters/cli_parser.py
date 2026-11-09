"""CLI 参数解析（C1 适配层；§11.3.3）。

argparse 声明与基线逐字一致（命令/选项/默认值/顺序均不变）。
"""


import argparse
from ..core import constants, errors



class _Parser(argparse.ArgumentParser):
    """argparse 的用法错误统一转成稳定错误码 USAGE_INVALID_ARGUMENT（退出码 2）。"""

    def error(self, message):
        raise errors.make("USAGE_INVALID_ARGUMENT", message)


def _global_parent():
    """让 --data-dir / --json / --quiet 既可以放在命令前，也可以放在命令后。"""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--data-dir", dest="data_dir", default=argparse.SUPPRESS)
    parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    return parent


def build_parser():
    g = _global_parent()
    parser = _Parser(prog=constants.COMMAND,
                     description="%s · 个人画像（P0）" % constants.APP_NAME)
    parser.add_argument("--data-dir", dest="data_dir", default=None,
                        help="数据目录（默认 ~/.fasti，可用 FASTI_HOME 覆盖）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--quiet", action="store_true", help="抑制 stderr 提示")
    parser.add_argument("--version", action="version",
                        version="%s %s" % (constants.COMMAND, constants.APP_VERSION))
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    sub.add_parser("init", parents=[g], help="初始化数据目录")
    sub.add_parser("status", parents=[g], help="查看状态")

    # ---- fact -------------------------------------------------------------
    fact = sub.add_parser("fact", parents=[g], help="事实录入与修订")
    fact_sub = fact.add_subparsers(dest="fact_command")
    fact_sub.required = True

    add = fact_sub.add_parser("add", parents=[g], help="录入一条事实")
    add.add_argument("--category")
    add.add_argument("--content")
    add.add_argument("--valid-from", dest="valid_from",
                 help="生效时间三档：YYYY-MM / YYYY-MM-DD / YYYY-MM-DDTHH:MM[:SS]")
    add.add_argument("--confirm-duplicate", dest="confirm_duplicate",
                     help="C-08：确认「这确实是新增」（令牌来自 CONFLICT_SIMILAR_FACT 的 "
                          "details.confirm_token；内容/类别/候选变化即失效）")

    change = fact_sub.add_parser("change", parents=[g], help="变更事实（演进/纠正）")
    change.add_argument("fact_id", nargs="?")
    change.add_argument("--type", dest="change_type")
    change.add_argument("--content")
    change.add_argument("--valid-from", dest="valid_from",
                    help="新生效时间三档；同月/同日粗输入冲突时按当前时刻自动提升")
    change.add_argument("--note")

    evolve = fact_sub.add_parser("evolve", parents=[g], help="等价于 --type evolve")
    evolve.add_argument("fact_id", nargs="?")
    evolve.add_argument("--content")
    evolve.add_argument("--valid-from", dest="valid_from",
                    help="新生效时间三档；同月/同日粗输入冲突时按当前时刻自动提升")
    evolve.add_argument("--note")

    correct = fact_sub.add_parser("correct", parents=[g], help="等价于 --type correct")
    correct.add_argument("fact_id", nargs="?")
    correct.add_argument("--content")
    correct.add_argument("--valid-from", dest="valid_from",
                     help="纠正不得改变生效时间（应省略）")
    correct.add_argument("--note")

    delete_fact = fact_sub.add_parser("delete", parents=[g], help="删除事实段（墓碑）")
    delete_fact.add_argument("fact_id", nargs="?")

    list_facts = fact_sub.add_parser("list", parents=[g], help="列出事实")
    list_facts.add_argument("--category")
    list_facts.add_argument("--line")
    list_facts.add_argument("--state", default="current")

    show_fact = fact_sub.add_parser("show", parents=[g], help="查看事实详情")
    show_fact.add_argument("fact_id", nargs="?")

    # ---- category ---------------------------------------------------------
    category = sub.add_parser("category", parents=[g], help="类别管理")
    category_sub = category.add_subparsers(dest="category_command")
    category_sub.required = True

    category_sub.add_parser("list", parents=[g], help="列出类别")

    add_cat = category_sub.add_parser("add", parents=[g], help="新增自定义类别")
    add_cat.add_argument("--name")
    add_cat.add_argument("--key")
    add_cat.add_argument("--disabled", action="store_true")

    for name, help_text in (("enable", "启用类别"), ("disable", "关闭类别")):
        enable_parser = category_sub.add_parser(name, parents=[g], help=help_text)
        enable_parser.add_argument("category_ref", nargs="?")

    delete_cat = category_sub.add_parser("delete", parents=[g], help="删除类别")
    delete_cat.add_argument("category_ref", nargs="?")
    delete_cat.add_argument("--handle-facts", dest="handling")
    delete_cat.add_argument("--migrate-to", dest="migrate_to")

    # ---- profile ----------------------------------------------------------
    profile = sub.add_parser("profile", parents=[g], help="画像与历史")
    profile_sub = profile.add_subparsers(dest="profile_command")
    profile_sub.required = True

    show = profile_sub.add_parser("show", parents=[g], help="查看当前画像")
    show.add_argument("--category")
    show.add_argument("--trace", action="store_true")

    at = profile_sub.add_parser("at", parents=[g], help="时点回溯")
    at.add_argument("--at")
    at.add_argument("--category")

    history = profile_sub.add_parser("history", parents=[g], help="历史时间线")
    history.add_argument("--category")

    # ---- 其它 -------------------------------------------------------------
    sub.add_parser("recompute", parents=[g], help="重算并写出画像文件")

    render = sub.add_parser("render", parents=[g], help="渲染宿主产物")
    render.add_argument("--host", default="all", choices=list(constants.HOST_CHOICES))

    export = sub.add_parser("export", parents=[g], help="导出 bundle")
    export.add_argument("--out")

    import_parser = sub.add_parser("import", parents=[g], help="从 bundle 导入")
    import_parser.add_argument("bundle_dir", nargs="?")
    import_parser.add_argument("--overwrite", action="store_true")
    import_parser.add_argument("--no-backup", dest="backup", action="store_false",
                               default=True)

    verify = sub.add_parser("verify", parents=[g], help="完整性自检")
    verify.add_argument("--deep", action="store_true")

    web = sub.add_parser("web", parents=[g], help="启动本机网页表单（P0 主界面）")
    web.add_argument("--port", type=int, default=8787)
    web.add_argument("--no-browser", dest="no_browser", action="store_true")
    web.add_argument("--token")

    install = sub.add_parser("install-host", parents=[g], help="放置宿主产物")
    install.add_argument("--host", default="all", choices=list(constants.HOST_CHOICES))
    install.add_argument("--mode", default="symlink", choices=["symlink", "copy"])
    install.add_argument("--project")
    install.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser
