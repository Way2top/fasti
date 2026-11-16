"""C-09 CLI 适配器：extract 相关 9 个命令（只调用 fasti.core.api）。"""

from ..core import api
from ..core import constants
from .cli_io import _emit, _select


def _cmd_extract_run(args, data_dir):
    run = api.extract_run(data_dir, getattr(args, "trigger", "manual"),
                          bool(getattr(args, "dry_run", False)),
                          getattr(args, "session_cwd", None),
                          getattr(args, "max_messages", None))
    counters = run["counters"]
    recall = run["recall"]
    text = ("提取完成：处理 %d 会话 / %d 消息；候选 %d（自动写入 %d，待审核 %d）；"
            "召回模式 %s（%dB，截断=%s）\n详情：fasti extract queue / extract runs"
            % (counters["sessions_processed"], counters["messages_processed"],
               counters["candidates_created"], counters["auto_written"],
               counters["queued"], recall["mode"], recall["corpus_bytes"],
               "是" if recall["truncated"] else "否"))
    drops = {key: value for key, value in
             (counters.get("drop_reasons") or {}).items() if value}
    if drops:
        text += "\n丢弃原因：%s" % ",".join("%s=%d" % item for item in sorted(drops.items()))
    if run.get("watermark_rebuilt"):
        text += "\n提示：检测到水位缺失/损坏，已从事实证据重建水位（watermark_rebuilt=true）"
    _emit("extract", run, text)
    return 0


def _cmd_extract_status(args, data_dir):
    status = api.get_extract_status(data_dir)
    last = status.get("last_run") or {}
    lines = [
        "状态：%s；自动写入：%s；同意：%s；配置：%s"
        % ("已启用" if status["enabled"] else "已关闭",
           "开" if status["auto_write"] else "关",
           "已接受" if status["consent"]["accepted"] else "未接受",
           "有效" if status["config_ok"] else "缺失 " + ",".join(status["config_missing"])),
        "待审核：%d" % status["queue"]["pending"],
        "最近运行：%s / %s / 候选 %d 自动写 %d"
        % (last.get("finished_at") or "-", last.get("status") or "-",
           (last.get("counters") or {}).get("candidates_created", 0),
           (last.get("counters") or {}).get("auto_written", 0)),
        "水位：%d 会话" % status["watermarks"]["sessions"],
    ]
    _emit("extract.status", status, "\n".join(lines))
    return 0


def _cmd_extract_config(args, data_dir):
    patch = {}
    llm = {}
    if getattr(args, "base_url", None):
        llm["base_url"] = args.base_url
    if getattr(args, "model", None):
        llm["model"] = args.model
    if llm:
        patch["llm"] = llm
    extract = {}
    if getattr(args, "enable", False):
        extract["enabled"] = True
    if getattr(args, "disable", False):
        extract["enabled"] = False
    if getattr(args, "auto_write", False):
        extract["auto_write"] = True
    if getattr(args, "no_auto_write", False):
        extract["auto_write"] = False
    if getattr(args, "sensitive_filter", False):
        extract["sensitive_filter"] = True
    if getattr(args, "no_sensitive_filter", False):
        extract["sensitive_filter"] = False
    if extract:
        patch["extract"] = extract
    if patch:
        status = api.set_extract_config(data_dir, patch)
    else:
        status = api.get_extract_config(data_dir)
    if getattr(args, "api_key_stdin", False):
        import sys
        key = sys.stdin.readline().strip()
        if key:
            status = api.set_extract_config(data_dir, {"llm": {"api_key": key}})
    text = ("配置：enabled=%s auto_write=%s consent=%s/%s；base_url=%s model=%s "
            "api_key=%s；config_ok=%s"
            % (status["enabled"], status["auto_write"],
               status["consent"]["version"], status["consent"]["accepted_at"],
               status["base_url_host"], status["model"], status["api_key_masked"],
               status["config_ok"]))
    _emit("extract.config", status, text)
    return 0


def _cmd_extract_consent(args, data_dir):
    if getattr(args, "accept", False):
        status = api.set_extract_consent(data_dir, True)
    elif getattr(args, "reset", False):
        status = api.set_extract_consent(data_dir, False)
    else:
        status = api.get_extract_config(data_dir)
    lines = ["一次性隐私告知：", status["consent"]["text"]]
    lines.append("当前：%s（version=%s at=%s）"
                 % ("已接受" if status["consent"]["accepted"] else "未接受",
                    status["consent"]["version"], status["consent"]["accepted_at"]))
    _emit("extract.consent", status, "\n".join(lines))
    return 0


def _cmd_extract_queue(args, data_dir):
    result = api.list_extract_candidates(
        data_dir, getattr(args, "status", "pending"),
        run_id=getattr(args, "run_id", None))
    lines = ["候选 ID | 内容摘要 | 类别 | 建议类型 | extraction/relation | 系统相似 | "
             "召回模式 | 冲突标记"]
    for item in result["items"]:
        relation = item.get("relation") or {}
        system = item.get("system") or {}
        content = item.get("content") or ""
        if len(content) > 40:
            content = content[:40] + "…"
        lines.append("%s | %s | %s | %s | %s/%s | %s | %s | %s" % (
            item.get("candidate_id"), content, item.get("category_key"),
            item.get("change_type") or "-", item.get("extraction_confidence"),
            relation.get("relation_confidence"), "是" if system.get("similar") else "否",
            (item.get("recall") or {}).get("mode"),
            item.get("decision", {}).get("reason_code", "-")))
    if not result["items"]:
        lines.append("（无）")
    lines.append("计数：待审 %d / 已通过 %d / 已拒绝 %d / 重复 %d / 丢弃 %d"
                 % (result["counts"]["pending"], result["counts"]["approved"],
                    result["counts"]["rejected"], result["counts"]["duplicate"],
                    result["counts"]["dropped"]))
    _emit("extract.queue", result, "\n".join(lines))
    return 0


def _granularity_label(candidate):
    granularity = (candidate or {}).get("granularity")
    if granularity == "summary":
        subject = (candidate or {}).get("subject")
        return "summary（subject=%s）" % subject if subject else "summary"
    if granularity == "atomic":
        return "atomic"
    return "未知"


def _cmd_extract_show(args, data_dir):
    result = api.show_extract_candidate(data_dir, args.candidate_id)
    candidate = result["candidate"]
    lines = [
        "候选 %s" % candidate["candidate_id"],
        "状态：%s；内容：%s" % (candidate["status"], candidate["content"]),
        "类别：%s；建议类型：%s；目标：%s"
        % (candidate.get("category_key"), candidate.get("change_type"),
           candidate.get("target_fact_id")),
        "置信度：extraction=%s relation=%s"
        % (candidate.get("extraction_confidence"),
           (candidate.get("relation") or {}).get("relation_confidence")),
        "来源：session=%s message_ids=%s ordinals=%s"
        % (candidate.get("session_id"), candidate.get("message_ids"),
           candidate.get("message_ordinals")),
        "召回：mode=%s bytes=%s current=%s historical=%s truncated=%s"
        % ((candidate.get("recall") or {}).get("mode"),
           (candidate.get("recall") or {}).get("corpus_bytes"),
           len((candidate.get("recall") or {}).get("recalled_fact_ids") or []),
           len((candidate.get("recall") or {}).get("historical_fact_ids") or []),
           (candidate.get("recall") or {}).get("truncated")),
        "系统相似：%s；两层冲突：%s"
        % ((candidate.get("system") or {}).get("similar"),
           (candidate.get("system") or {}).get("layer_mismatch")),
        "粒度：" + _granularity_label(candidate),
        "决策：%s / %s" % ((candidate.get("decision") or {}).get("action"),
                          (candidate.get("decision") or {}).get("reason_code")),
    ]
    recall = candidate.get("recall") or {}
    if result.get("recall_details"):
        lines.append("召回事实明细（mode=%s，corpus=%sB，truncated=%s）："
                     % (recall.get("mode"), recall.get("corpus_bytes"),
                        recall.get("truncated")))
        for item in result["recall_details"]:
            content = item["content"]
            if len(content) > 80:
                content = content[:80] + "…"
            lines.append("  - %s | %s%s | %s | %s | 原因：%s" % (
                api.short_id(item["fact_id"]), item["category_key"],
                "|关闭" if item.get("disabled") else "", item["kind"], content,
                ",".join(item.get("reasons") or []) or "-"))
    if result.get("target_fact"):
        item = result["target_fact"]
        content = item["content"]
        if len(content) > 120:
            content = content[:120] + "…"
        lines.append("目标事实（update/conflict）：%s | %s | %s"
                     % (api.short_id(item["fact_id"]), item["category_key"], content))
    for preview in result.get("evidence_preview") or []:
        lines.append("证据 %s（#%s）：%s"
                     % (preview["message_id"], preview["ordinal"], preview["snippet"]))
    _emit("extract.show", result, "\n".join(lines))
    return 0


def _cmd_extract_approve(args, data_dir):
    patch = {}
    if getattr(args, "content", None):
        patch["content"] = args.content
    if getattr(args, "category", None):
        patch["category"] = args.category
    if getattr(args, "valid_from", None):
        patch["valid_from"] = args.valid_from
    if getattr(args, "change_type", None):
        patch["change_type"] = args.change_type
    if getattr(args, "note", None):
        patch["note"] = args.note
    result = api.resolve_extract_candidate(
        data_dir, args.candidate_id, "approve", patch,
        getattr(args, "confirm_duplicate", None))
    text = "已通过候选 %s（%s → fact %s）\n提示：fasti render" % (
        result["candidate"]["candidate_id"], result["change_type"],
        api.short_id(result["fact"]["fact_id"]))
    _emit("extract.approve", _select(result, ["candidate", "fact", "change_type",
                                              "warnings"]), text,
          result.get("warnings"))
    return 0


def _cmd_extract_reject(args, data_dir):
    result = api.resolve_extract_candidate(
        data_dir, args.candidate_id, "reject",
        {"reason": getattr(args, "reason", None) or ""})
    text = "已拒绝候选 %s（%s）" % (result["candidate"]["candidate_id"],
                                  result["candidate"]["review"]["reviewed_at"])
    _emit("extract.reject", _select(result, ["candidate"]), text)
    return 0


def _cmd_extract_runs(args, data_dir):
    reference = getattr(args, "run_id", None)
    if reference:
        return _cmd_extract_run_detail(data_dir, reference)
    result = api.list_extract_runs(data_dir, getattr(args, "limit", 20))
    lines = ["run_id | 开始 | 状态 | 会话/消息 | 进入抽取 | 候选/自动写/待审 | "
             "未扫描/预计剩余 | 失败"]
    for run in result["items"]:
        counters = run.get("counters") or {}
        recall = run.get("recall") or {}
        coverage = run.get("coverage") or {}
        lines.append("%s | %s | %s%s | %s/%s | %s | %s/%s/%s | %s/%s | %s" % (
            run.get("run_id"), run.get("started_at"), run.get("status"),
            "（旧记录信息不完整）" if run.get("legacy") else "",
            counters.get("sessions_processed"), counters.get("messages_processed"),
            counters.get("sessions_entered_extraction", "-"),
            counters.get("candidates_created"), counters.get("auto_written"),
            counters.get("queued"), coverage.get("unscanned_sessions", "-"),
            coverage.get("estimated_runs_remaining", "-"), counters.get("errors")))
        drops = {key: value for key, value in (counters.get("drop_reasons") or {}).items()
                 if value}
        if drops:
            lines.append("    丢弃原因：%s"
                         % ",".join("%s=%d" % item for item in sorted(drops.items())))
    if not result["items"]:
        lines.append("（无）")
    lines.append("说明：运行记录不含会话原文/密钥")
    _emit("extract.runs", result, "\n".join(lines))
    return 0


def _cmd_extract_run_detail(data_dir, reference):
    result = api.get_extract_run(data_dir, reference)
    run = result["run"]
    lines = ["运行：%s" % run.get("run_id"),
             "开始/结束：%s / %s" % (run.get("started_at"), run.get("finished_at")),
             "状态：%s%s" % (run.get("status"),
                            "（旧记录信息不完整）" if run.get("legacy") else "")]
    coverage = run.get("coverage") or {}
    if coverage:
        cumulative = coverage.get("sessions_scanned_cumulative")
        if cumulative is None:
            cumulative = ((coverage.get("total_candidate_sessions") or 0)
                          - (coverage.get("unscanned_sessions") or 0))
        lines.append("覆盖：候选会话 %s，累计已扫描 %s/%s，本次 %s，未扫描 %s，预计还需 %s 次"
                     % (coverage.get("total_candidate_sessions"), cumulative,
                        coverage.get("total_candidate_sessions"),
                        coverage.get("sessions_scanned_this_run"),
                        coverage.get("unscanned_sessions"),
                        coverage.get("estimated_runs_remaining")))
    merge = run.get("summary_merge") or {}
    if merge:
        lines.append("summary 合并：%s → %s（自动合并 %s，提示 %s 组）"
                     % (merge.get("before"), merge.get("after"),
                        merge.get("auto_merged"), len(merge.get("suggestion_groups") or [])))
    sessions = run.get("sessions") or []
    skipped = [session for session in sessions
               if session.get("status") == "no-new-messages"]
    processed = [session for session in sessions
                 if session.get("status") != "no-new-messages"]
    if not processed:
        lines.append("本次运行没有处理任何会话")
    else:
        lines.append("逐会话：")
        for session in processed:
            title = session.get("title") or {}
            text = title.get("text") or "不可用"
            source = title.get("source") or "unavailable"
            counts = session.get("candidates") or {}
            extracted = counts.get("extracted",
                                   session.get("candidates_extracted"))
            kept = counts.get("kept", counts.get("created",
                                                session.get("candidates_kept")))
            detail = ["抽取 %s → 保留 %s" % (extracted if extracted is not None else "-",
                                            kept if kept is not None else "-")]
            if counts.get("dropped_by_session_cap"):
                detail.append("上限丢弃 %s" % counts["dropped_by_session_cap"])
            if counts.get("dropped_duplicate_summary"):
                detail.append("重复 summary %s" % counts["dropped_duplicate_summary"])
            if counts.get("duplicates"):
                detail.append("重复 %s" % counts["duplicates"])
            error_code = session.get("error_code") or session.get("code")
            error_text = ""
            if error_code:
                error_text = " | 错误 %s（%s）" % (
                    error_code,
                    session.get("error_reason")
                    or constants.EXTRACT_ERROR_LABELS.get(error_code, "未知错误"))
            lines.append("  - %s（%s）| %s | %s | 消息 %s | %s%s"
                         % (text, source, session.get("decision") or "-",
                            (session.get("reason") or "-")[:60],
                            session.get("user_message_count",
                                       session.get("messages", "-")),
                            "；".join(detail), error_text))
    failed = [session for session in processed if session.get("status") == "failed"]
    failed_count = int((run.get("counters") or {}).get("sessions_failed",
                                                       len(failed)))
    if failed_count:
        codes = sorted({session.get("error_code") or session.get("code") or ""
                        for session in failed})
        labels_text = "、".join(
            constants.EXTRACT_ERROR_LABELS.get(code, "未知错误") for code in codes)
        lines.append("失败 %d 个会话（%s，下次重试）" % (failed_count, labels_text))
    if skipped:
        lines.append("另有 %d 个会话已处理过、本次无新消息" % len(skipped))
    counts = (result.get("candidates") or {}).get("counts_by_status") or {}
    lines.append("候选：待审 %s / 通过 %s / 拒绝 %s"
                 % (counts.get("pending", 0), counts.get("approved", 0),
                    counts.get("rejected", 0)))
    _emit("extract.runs.detail", result, "\n".join(lines))
    return 0
