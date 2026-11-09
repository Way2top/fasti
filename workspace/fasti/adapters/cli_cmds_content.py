"""CLI 内容类命令实现：fact / category / profile（§11.3.3）。

只调用 `fasti.core.api`（ADR-019/ADR-022）；呈现经 cli_io。
"""


from ..core import api
from .cli_io import _emit, _select



def _cmd_fact_add(args, data_dir):
    result = api.add_fact(data_dir, args.category, args.content, args.valid_from)
    fact = result["fact"]
    line = result["line"]
    display = result["_extra"].get("category_display_name", line["category_key"])
    text = "已录入 fact %s（line %s · %s · 生效 %s）\n提示：fasti render" % (
        api.short_id(fact["fact_id"]), api.short_id(line["line_id"]), display,
        fact["valid_from"])
    _emit("fact.add", _select(result, ["fact", "line"]), text, result.get("warnings"))
    return 0


def _cmd_fact_change(args, data_dir):
    change_type = getattr(args, "change_type", None)
    if args.fact_command == "evolve":
        change_type = "evolve"
    elif args.fact_command == "correct":
        change_type = "correct"
    valid_from = getattr(args, "valid_from", None)
    result = api.change_fact(data_dir, args.fact_id, change_type, args.content,
                             valid_from, getattr(args, "note", None))
    fact = result["fact"]
    if result["change_type"] == "correct":
        text = "纠正：已纠正 fact %s（第 %d 次修订）\n提示：fasti render" % (
            api.short_id(fact["fact_id"]), result["_extra"]["revision_seq"])
    else:
        text = "演进：已追加 fact %s（line %s · 生效 %s）\n提示：fasti render" % (
            api.short_id(fact["fact_id"]), api.short_id(fact["line_id"]),
            fact["valid_from"])
    _emit("fact.change", _select(result, ["change_type", "fact", "revision"]),
          text, result.get("warnings"))
    return 0


def _cmd_fact_delete(args, data_dir):
    result = api.delete_fact(data_dir, args.fact_id)
    fact = result["fact"]
    text = "已删除 fact %s（%s）\n提示：fasti render" % (
        api.short_id(fact["fact_id"]), fact["deleted_at"])
    _emit("fact.delete", _select(result, ["fact"]), text, result.get("warnings"))
    return 0


def _cmd_fact_list(args, data_dir):
    result = api.list_facts(data_dir, args.category, args.line, args.state)
    display_names = result.get("_extra", {}).get("category_display_names", {})
    lines = ["短ID | line 短ID | 类别 key | 生效 | 内容 | 变更类型 | 首次录入"]
    for fact in result["items"]:
        lines.append("%s | %s | %s | %s | %s | %s | %s" % (
            api.short_id(fact["fact_id"]), api.short_id(fact["line_id"]),
            fact["category_key"],
            fact["valid_from"], fact["content"], fact["change_type"], fact["recorded_at"]))
    if not result["items"]:
        lines.append("（无）")
    lines.append("共 %d 条（返回 %d 条）"
                 % (result["counts"]["total_facts"], result["counts"]["returned"]))
    _emit("fact.list", _select(result, ["items", "counts"]), "\n".join(lines))
    return 0


def _cmd_fact_show(args, data_dir):
    result = api.show_fact(data_dir, args.fact_id)
    fact = result["fact"]
    category = result["category"]
    line = result["line"]
    display_end = result.get("_extra", {}).get("display_end",
                                               fact["valid_to"] or "今")
    lines = [
        "事实 %s" % api.short_id(fact["fact_id"]),
        "类别：%s（%s）" % (category["key"], category["display_name"]),
        "命题线：%s" % api.short_id(line["line_id"]),
        "内容：%s" % fact["content"],
        "来源：用户录入（%s）" % fact["source"],
        "首次录入：%s" % fact["recorded_at"],
        "生效：%s 至 %s" % (fact["valid_from"], display_end),
        "变更类型：%s" % fact["change_type"],
        "删除状态：%s" % (("已删除（%s）" % fact["deleted_at"])
                          if fact["deleted_at"] else "未删除"),
        "修订：",
    ]
    for revision in result["revisions"]:
        note = ("（备注：%s）" % revision["note"]) if revision.get("note") else ""
        lines.append("- #%d %s %s %s%s" % (revision["seq"], revision["revision_type"],
                                          revision["recorded_at"], revision["content"], note))
    _emit("fact.show", result, "\n".join(lines))
    return 0


def _cmd_category_list(args, data_dir):
    result = api.list_categories(data_dir)
    lines = ["key | 名称 | 来源 | 顺序 | 启用 | 删除时间"]
    for category in result["items"]:
        lines.append("%s | %s | %s | %s | %s | %s" % (
            category["key"], category["display_name"], category["origin"],
            category["template_order"] if category["template_order"] is not None else "-",
            "启用" if category["enabled"] else "关闭",
            category["deleted_at"] or "-"))
    _emit("category.list", result, "\n".join(lines))
    return 0


def _cmd_category_add(args, data_dir):
    result = api.add_category(data_dir, args.name, args.key, not args.disabled)
    category = result["category"]
    text = "已新增类别 %s（%s）\n提示：fasti render" % (
        category["key"], category["display_name"])
    _emit("category.add", _select(result, ["category"]), text, result.get("warnings"))
    return 0


def _cmd_category_toggle(args, data_dir):
    enabled = args.category_command == "enable"
    result = api.set_category_enabled(data_dir, args.category_ref, enabled)
    category = result["category"]
    if enabled:
        text = "已启用类别 %s\n提示：fasti render" % category["display_name"]
        command_id = "category.enable"
    else:
        text = "已关闭类别 %s（事实保留）\n提示：fasti render" % category["display_name"]
        command_id = "category.disable"
    _emit(command_id, _select(result, ["category"]), text, result.get("warnings"))
    return 0


def _cmd_category_delete(args, data_dir):
    result = api.delete_category(data_dir, args.category_ref, args.handling, args.migrate_to)
    extra = result["_extra"]
    if extra.get("handling") == "migrate":
        handling_text = "迁移至 %s" % extra.get("migrate_to_display_name")
    else:
        handling_text = "一并删除 %d 条" % result["affected_facts"]
    text = "已删除类别 %s（存量事实：%s）\n提示：fasti render" % (
        extra.get("display_name"), handling_text)
    _emit("category.delete", _select(result, ["category", "affected_facts"]),
          text, result.get("warnings"))
    return 0


def _cmd_profile_show(args, data_dir):
    view = api.get_profile(data_dir, args.category, bool(args.trace))
    _emit("profile.show", view, api.profile_text(view))
    return 0


def _cmd_profile_at(args, data_dir):
    view = api.get_profile_at(data_dir, args.at, args.category)
    _emit("profile.at", view, api.profile_at_text(view))
    return 0


def _cmd_profile_history(args, data_dir):
    view = api.get_history(data_dir, args.category)
    data = {"groups": view["groups"], "category_events": view["category_events"]}
    _emit("profile.history", data, api.history_text(view))
    return 0
