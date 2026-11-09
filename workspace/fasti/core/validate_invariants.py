"""领域不变量校验（v2 = UUIDv7 标识方案）。

SQL 表达力之外的部分（ADR-009 的两层校验中的第二层）：
引用完整性、UUIDv7 形态、修订连续性（(fact_id, seq)）、演进链、派生字段一致性、
类别 key/顺序/复活后的状态、删除类别的存量处置完成。

返回问题字符串列表，空 = 通过。
"""

import re

from . import clock
from . import constants
from . import ids


def _is_category_key(value):
    return isinstance(value, str) and bool(re.match(constants.CATEGORY_KEY_RE, value))


def check_invariants(model):
    problems = []

    # 1) 事件序列：从 1 连续、首事件 store_initialized、唯一、format_version == 2
    seqs = [event.get("seq") for event in model.events]
    if seqs and seqs != list(range(1, len(seqs) + 1)):
        problems.append("事件 seq 不连续：期望 1..%d" % len(seqs))
    if model.events:
        if model.events[0].get("type") != "store_initialized":
            problems.append("首事件不是 store_initialized")
        initialized = [event for event in model.events
                       if event.get("type") == "store_initialized"]
        if len(initialized) != 1:
            problems.append("store_initialized 必须恰好出现一次，实际 %d 次" % len(initialized))
        elif initialized[0].get("data", {}).get("format_version") != constants.FORMAT_VERSION:
            problems.append("store_initialized.format_version 不是 %d"
                            % constants.FORMAT_VERSION)

    # 2) 类别身份：key 形态、未删除 display_name 唯一、模板顺序唯一、复活后的字段一致性
    for category in model.categories.values():
        if not _is_category_key(category["key"]):
            problems.append("类别 key 形态非法：%r" % (category["key"],))
        if category["origin"] not in ("template", "custom"):
            problems.append("类别 origin 非法：%r" % (category["origin"],))
        if category["deleted_at"] is None:
            if category["deleted_handling"] is not None:
                problems.append("未删除类别不得带处置方式：%s" % category["key"])
        else:
            if category["deleted_handling"] not in ("migrate", "delete"):
                problems.append("已删除类别缺少合法处置方式：%s" % category["key"])
            if category["deleted_handling"] == "migrate":
                target = category["migrate_to_category_key"]
                if not target or target == category["key"]:
                    problems.append("迁移目标非法：%s" % category["key"])
    alive = model.alive_categories()
    names = {}
    for category in alive:
        if category["display_name"] in names:
            problems.append("未删除类别的 display_name 重复：%r" % category["display_name"])
        names[category["display_name"]] = True
    orders = [category["template_order"] for category in alive
              if category["origin"] == "template"]
    if len(orders) != len(set(orders)):
        problems.append("未删除模板类别的 template_order 重复")
    for category in alive:
        if category["origin"] == "template":
            if category["template_order"] is None:
                problems.append("模板类别缺少 template_order：%s" % category["key"])
        elif category["template_order"] is not None:
            problems.append("自定义类别不得有 template_order：%s" % category["key"])

    # 3) 引用完整性 + UUIDv7 形态 + 派生字段一致性
    for line in model.lines.values():
        if not ids.is_uuid7(line["line_id"]):
            problems.append("命题线 ID 不是 UUIDv7：%r" % (line["line_id"],))
        if line["category_key"] not in model.categories:
            problems.append("命题线 %s 引用了不存在的类别 %s"
                            % (line["line_id"], line["category_key"]))
    for fact in model.all_facts():
        if not ids.is_uuid7(fact["fact_id"]):
            problems.append("事实 ID 不是 UUIDv7：%r" % (fact["fact_id"],))
        if fact["line_id"] not in model.lines:
            problems.append("事实 %s 引用了不存在的命题线 %s"
                            % (fact["fact_id"], fact["line_id"]))
            continue
        line = model.lines[fact["line_id"]]
        if fact["category_key"] != line["category_key"]:
            problems.append("事实 %s 的类别与命题线不一致" % fact["fact_id"])
        # C-07：valid_from / valid_to 必须是完整 RFC3339 秒级时间戳，且 valid_to > valid_from
        if not clock.is_valid_time(fact["valid_from"]):
            problems.append("事实 %s 的 valid_from 不是完整时间戳：%r"
                            % (fact["fact_id"], fact["valid_from"]))
        if fact["valid_to"] is not None:
            if not clock.is_valid_time(fact["valid_to"]):
                problems.append("事实 %s 的 valid_to 不是完整时间戳：%r"
                                % (fact["fact_id"], fact["valid_to"]))
            elif clock.compare(fact["valid_to"], fact["valid_from"]) <= 0:
                problems.append("事实 %s 的 valid_to 未晚于 valid_from（零长/负区间）"
                                % fact["fact_id"])
        revisions = model.revisions_of(fact["fact_id"])
        if not revisions:
            problems.append("事实 %s 缺少修订" % fact["fact_id"])
            continue
        expected_seqs = list(range(1, len(revisions) + 1))
        if [revision["seq"] for revision in revisions] != expected_seqs:
            problems.append("事实 %s 的修订序号不连续" % fact["fact_id"])
        for revision in revisions:
            if revision.get("fact_id") != fact["fact_id"]:
                problems.append("修订的 fact_id 不一致：%s#%s"
                                % (fact["fact_id"], revision.get("seq")))
            if revision["seq"] == 1 and revision.get("revision_type") != "initial":
                problems.append("事实 %s 的首个修订必须是 initial" % fact["fact_id"])
            if revision["seq"] > 1 and revision.get("revision_type") != "correct":
                problems.append("事实 %s 的后续修订必须是 correct" % fact["fact_id"])
            if "revision_id" in revision:
                problems.append("v2 修订不得有 revision_id（应使用 (fact_id, seq)）")
            if not isinstance(revision.get("content"), str) or not revision["content"]:
                problems.append("修订内容为空：%s#%s" % (fact["fact_id"], revision["seq"]))
        latest = revisions[-1]
        if fact["content"] != latest["content"]:
            problems.append("事实 %s 的 content 未取最新修订" % fact["fact_id"])
        if fact["revision_count"] != len(revisions):
            problems.append("事实 %s 的 revision_count 不一致" % fact["fact_id"])
        if fact["recorded_at"] != revisions[0]["recorded_at"]:
            problems.append("事实 %s 的 recorded_at 被后续修订改写（AC-1-3）" % fact["fact_id"])
        if fact["change_type"] == "create" and fact.get("previous_fact_id") is not None:
            problems.append("事实 %s 是 create 但带 previous_fact_id" % fact["fact_id"])
        if fact["change_type"] == "evolve":
            previous_id = fact.get("previous_fact_id")
            if not ids.is_uuid7(previous_id):
                problems.append("事实 %s 的 previous_fact_id 不是 UUIDv7" % fact["fact_id"])
            previous = model.fact(previous_id)
            if previous is None:
                problems.append("事实 %s 的 previous_fact_id 不存在" % fact["fact_id"])
            else:
                if previous["line_id"] != fact["line_id"]:
                    problems.append("事实 %s 的演进链跨了命题线" % fact["fact_id"])
                if clock.compare(fact["valid_from"], previous["valid_from"]) <= 0:
                    problems.append("事实 %s 的演进未严格晚于前段" % fact["fact_id"])

    # 4) 同一 line 内未删除段生效瞬间严格递增 + 派生 valid_to 正确（C-07：按瞬间）
    for line in model.lines.values():
        segments = model.line_facts(line["line_id"])
        alive_facts = [fact for fact in segments if fact["deleted_at"] is None]
        alive_starts = [clock.instant(fact["valid_from"]) for fact in alive_facts]
        if alive_starts != sorted(set(alive_starts)):
            problems.append("命题线 %s 存在重复或乱序的生效瞬间" % line["line_id"])
        for fact in segments:
            expected = None
            later = [item["valid_from"] for item in alive_facts
                     if clock.compare(item["valid_from"], fact["valid_from"]) > 0]
            if later:
                expected = min(later, key=clock.instant)
            if fact["valid_to"] != expected:
                problems.append("事实 %s 的派生 valid_to 不一致（期望 %r，实际 %r）"
                                % (fact["fact_id"], expected, fact["valid_to"]))
        if line["segment_count"] != len(segments):
            problems.append("命题线 %s 的 segment_count 不一致" % line["line_id"])

    # 5) 类别删除的存量处置必须完成（迁移后不得有 line 仍挂在被删类别下）
    for category in model.categories.values():
        if category["deleted_at"] is None:
            continue
        if category["deleted_handling"] == "migrate":
            orphans = [line["line_id"] for line in model.lines.values()
                       if line["category_key"] == category["key"]]
            if orphans:
                problems.append("类别 %s 已删除且迁移，但命题线仍挂在它下面：%s"
                                % (category["key"], ",".join(orphans)))
    return problems
