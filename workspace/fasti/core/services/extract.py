"""C-09 自动提取应用服务（ADR-028~031）：配置/状态/运行/候选审核。

两段式：A 抽取（LLM 不接触数据库）→ 确定性召回（扫全库）→ B 判定（只看候选+召回）→ 决策。
自动写入复用 `facts.add_fact` / `facts.change_fact`（含 C-08）；候选/水位/运行记录只进
`state/extract/`（不进 events.jsonl）。
"""

import json
import os
import time

from .. import clock
from .. import constants
from .. import errors
from .. import extract_divert
from .. import extract_config
from .. import extract_llm
from .. import extract_privacy
from .. import extract_quality
from .. import extract_recall
from .. import extract_state
from .. import ids
from .. import session_source
from .. import store
from . import extract_review
from . import facts
from . import extract_sessionops as ops
from .extract_sessionops import (
    _base_url_host, _daily_auto_writes, _dedupe_index, _empty_counters,
    _entry_for_fact, _recall_entries_for,
    advance_watermark, diversion_record)
from .extract_review import _extraction_payload
from ._shared import _load_model


# ---------------------------------------------------------------- 配置/状态
def get_extract_config(data_dir):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    status = extract_config.config_status(data_dir)
    status["queue"] = {"pending": extract_state.list_candidates(
        data_dir, "pending")["counts"]["pending"]}
    status["last_run"] = extract_state.last_run(data_dir)
    status["running"] = extract_state.is_running(data_dir)
    return status


def set_extract_config(data_dir, patch):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    with store.store_lock(data_dir):
        extract_config.update_config(data_dir, patch)
    return get_extract_config(data_dir)


def set_extract_consent(data_dir, accepted):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    with store.store_lock(data_dir):
        extract_config.set_consent(data_dir, bool(accepted))
    return get_extract_config(data_dir)


def get_extract_status(data_dir):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    status = get_extract_config(data_dir)
    watermarks, _rebuilt = extract_state.load_watermarks(data_dir)
    status["watermarks"] = {
        "sessions": len(watermarks.get("sessions") or {}),
        "updated_at": watermarks.get("updated_at"),
    }
    status["queue_counts"] = extract_state.list_candidates(data_dir, "all")["counts"]
    return status


def list_extract_candidates(data_dir, status="pending"):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    return extract_state.list_candidates(data_dir, status or "pending")


def show_extract_candidate(data_dir, candidate_id):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    candidate = extract_state.get_candidate(data_dir, candidate_id)
    preview = []
    try:
        cfg = extract_config.load_config(data_dir)
        sources = (cfg["extract"].get("sources") or {})
        for path in session_source.discover_sessions(
                sources.get("dsh_home") or "~/.dsh",
                allow_plain=bool(sources.get("allow_plain"))):
            session = session_source.load_session(
                path, allow_plain=bool(sources.get("allow_plain")))
            if not session or session["session_id"] != candidate.get("session_id"):
                continue
            wanted = set(candidate.get("message_ids") or [])
            for message in session["messages"]:
                if message["message_id"] in wanted:
                    text, _kinds = extract_privacy.redact_text(message["text"])
                    preview.append({"message_id": message["message_id"],
                                    "ordinal": message["ordinal"],
                                    "snippet": text[:200]})
            break
    except errors.CoreError:
        preview = []
    recall_details, target_fact = extract_review.build_candidate_details(
        data_dir, candidate)
    return {"candidate": candidate, "evidence_preview": preview,
            "recall_details": recall_details, "target_fact": target_fact}


def list_extract_runs(data_dir, limit=20):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    return {"items": extract_state.load_runs(data_dir, limit)}


# ---------------------------------------------------------------- 运行
def resolve_extract_candidate(data_dir, candidate_id, action, patch=None,
                              confirm_token=None):
    return extract_review.resolve_extract_candidate(
        data_dir, candidate_id, action, patch, confirm_token)

def extract_run(data_dir, trigger="manual", dry_run=False, session_cwd=None,
                max_messages=None):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    cfg, _status = extract_config.require_run_ready(data_dir)
    extract_cfg = cfg["extract"]
    start_monotonic = time.time()
    run_id = ids.new_uuid7()
    started_at = clock.now_recorded_at()
    counters = _empty_counters()
    counters.update({"sessions_entered_extraction": 0, "divert_extract": 0,
                     "divert_skip": 0, "divert_failed": 0})
    run_errors = []
    fatal_error = None
    sessions_summary = []
    _, _, model = _load_model(data_dir)
    watermarks, rebuilt = extract_state.load_watermarks(data_dir)
    if rebuilt:
        rebuilt_sessions = extract_state.rebuild_from_evidence(model.all_facts())
        for session_id, entry in rebuilt_sessions.items():
            watermarks["sessions"].setdefault(session_id, entry)
    candidates_before = extract_state.load_candidates(data_dir)
    dedupe = _dedupe_index(model, data_dir)
    recalled_total = 0
    any_truncated = False
    recall_mode = None
    corpus_bytes = 0
    llm_calls = 0
    auto_writes = 0
    daily_writes = _daily_auto_writes(data_dir)
    limit_messages = int(max_messages or extract_cfg.get("max_messages_per_run") or 200)
    limit_sessions = int(extract_cfg.get("max_sessions_per_run") or 10)
    sources = extract_cfg.get("sources") or {}
    dsh_home = sources.get("dsh_home") or "~/.dsh"
    allow_plain = bool(sources.get("allow_plain"))
    sensitive = bool(extract_cfg.get("sensitive_filter", True))
    paths = session_source.discover_sessions(dsh_home, allow_plain=allow_plain)
    with extract_state.run_lock(data_dir):
        for path in paths:
            if counters["sessions_seen"] >= limit_sessions:
                break
            counters["sessions_seen"] += 1
            try:
                session = session_source.load_session(path, allow_plain=allow_plain)
            except errors.CoreError as exc:
                counters["sessions_skipped"] += 1
                counters["errors"] += 1
                run_errors.append({"code": exc.code, "session_path": path,
                                   "safe_detail": exc.message[:200]})
                sessions_summary.append({"path": path, "status": "unreadable",
                                         "code": exc.code})
                continue
            if session is None:
                counters["sessions_skipped"] += 1
                continue
            if session_cwd and session.get("cwd") != session_cwd:
                counters["sessions_skipped"] += 1
                continue
            watermark = watermarks["sessions"].get(session["session_id"]) or {}
            last_seq = int(watermark.get("last_seq") or 0)
            new_messages = [message for message in session["messages"]
                            if message["seq"] > last_seq]
            if limit_messages <= 0:
                new_messages = []
            if not new_messages:
                counters["sessions_skipped"] += 1
                sessions_summary.append({"session_id": session["session_id"],
                                         "status": "no-new-messages"})
                continue
            divert_input = session.get("divert_input") or {}
            try:
                diversion = extract_divert.divert_session(cfg, divert_input, sensitive)
            except errors.CoreError as exc:
                counters["divert_failed"] += 1
                counters["errors"] += 1
                run_errors.append({"code": exc.code, "safe_detail": exc.message[:200]})
                sessions_summary.append(
                    diversion_record(session, divert_input, "divert_failed",
                                     "skip", exc.message[:120], exc.code))
                continue  # 失败不推进水位，下次重试；不中断整次运行
            llm_calls += 1
            if diversion["decision"] == "skip":
                counters["divert_skip"] += 1
                if not dry_run:
                    advance_watermark(data_dir, watermarks, session, path,
                                      new_messages, run_id)
                sessions_summary.append(
                    diversion_record(session, divert_input, "diversion_skip",
                                     "skip", diversion["reason"]))
                continue
            counters["divert_extract"] += 1
            counters["sessions_entered_extraction"] += 1
            counters["sessions_processed"] += 1
            try:
                chunks = session_source.chunk_messages(
                    {**session, "messages": new_messages})
                categories = [{"key": category["key"],
                               "display_name": category["display_name"]}
                              for category in model.enabled_categories()]
                buffer = []
                for chunk in chunks:
                    if counters["messages_processed"] + len(chunk["messages"]) \
                            > limit_messages:
                        break
                    buffer.extend(ops.extract_chunk(cfg, chunk, model, categories,
                                                    sensitive, counters))
                    llm_calls += 1
                kept, stats = ops.postprocess_session(buffer)
                for key in ("candidates_extracted", "candidates_after_dedup",
                            "candidates_kept", "dropped_duplicate_summary",
                            "dropped_by_session_cap"):
                    counters[key] = counters.get(key, 0) + stats[key]
                prepared = ops.prepare_kept(kept, session["session_id"],
                                            candidates_before, dedupe, counters)
                created, chunk_recall = 0, None
                if prepared:
                    created, chunk_recall = _decide_session(
                        data_dir, model, cfg, session["session_id"], prepared,
                        run_id, dry_run, dedupe, counters)
                    llm_calls += 1
                if chunk_recall:
                    recall_mode = chunk_recall["mode"]
                    corpus_bytes = chunk_recall["corpus_bytes"]
                    recalled_total += chunk_recall["recalled"]
                    any_truncated = any_truncated or chunk_recall["truncated"]
                counters["messages_processed"] += len(new_messages)
            except errors.CoreError as exc:
                counters["errors"] += 1
                run_errors.append({"code": exc.code, "safe_detail": exc.message[:200]})
                sessions_summary.append({"session_id": session["session_id"],
                                         "status": "failed", "code": exc.code})
                if exc.code.startswith("EXTRACT_LLM_") \
                        or exc.code in ("EXTRACT_ZSTD_UNAVAILABLE",):
                    fatal_error = exc
                break
            if not dry_run:
                advance_watermark(data_dir, watermarks, session, path,
                                  new_messages, run_id)
            processed_record = diversion_record(
                session, divert_input, "processed", "extract", diversion["reason"])
            processed_record["messages"] = len(new_messages)
            processed_record["candidates_extracted"] = stats["candidates_extracted"]
            processed_record["candidates_after_dedup"] = stats["candidates_after_dedup"]
            processed_record["candidates_kept"] = stats["candidates_kept"]
            processed_record["dropped_duplicate_summary"] = stats[
                "dropped_duplicate_summary"]
            processed_record["dropped_by_session_cap"] = stats[
                "dropped_by_session_cap"]
            sessions_summary.append(processed_record)
    recall_summary = {"mode": recall_mode or "full", "corpus_bytes": corpus_bytes,
                      "candidates": counters["candidates_created"],
                      "recalled": recalled_total, "truncated": any_truncated}
    status = "ok"
    if counters["errors"]:
        status = "partial" if counters["candidates_created"] else "failed"
    finished_at = clock.now_recorded_at()
    run = {
        "run_id": run_id,
        "schema_version": constants.EXTRACT_STATE_SCHEMA_VERSION,
        "started_at": started_at,
        "finished_at": finished_at,
        "trigger": trigger if trigger in ("manual", "web_open", "cli") else "manual",
        "status": status,
        "counters": counters,
        "recall": recall_summary,
        "llm": {"model": cfg["llm"].get("model"),
                "base_url_host": _base_url_host(cfg),
                "latency_ms": int((time.time() - start_monotonic) * 1000),
                "retries": 0, "calls": llm_calls,
                "prompt_version": constants.EXTRACT_PROMPT_EXTRACTION,
                "prompts": {"divert": constants.EXTRACT_PROMPT_DIVERT,
                            "extraction": constants.EXTRACT_PROMPT_EXTRACTION,
                            "judgment": constants.EXTRACT_PROMPT_JUDGMENT},
                "prompt_sha256": __import__("hashlib").sha256(
                    constants.EXTRACT_PROMPT_EXTRACTION.encode("utf-8")).hexdigest()},
        "watermark_rebuilt": bool(rebuilt),
        "sessions": sessions_summary,
        "errors": run_errors,
    }
    if not dry_run:
        extract_state.append_run(data_dir, run)
    if fatal_error is not None:
        raise fatal_error
    return run


def _decide_session(data_dir, model, cfg, session_id, prepared, run_id, dry_run,
                    dedupe, counters):
    sensitive = bool(cfg["extract"].get("sensitive_filter", True))
    if not prepared:
        return 0, None
    per_candidate, summary = extract_recall.recall_for_candidates(model, prepared)
    recall_lines = {candidate_id: result["lines"]
                    for candidate_id, result in per_candidate.items()}
    judge_payload = extract_llm.build_judgment_messages(prepared, recall_lines,
                                                        sensitive_filter=sensitive)
    judge_tokens = min(
        constants.EXTRACT_JUDGE_TOKENS_CAP,
        constants.EXTRACT_JUDGE_TOKENS_BASE
        + constants.EXTRACT_JUDGE_TOKENS_PER_CANDIDATE * len(prepared))
    judgments_content = extract_llm.chat_completion(cfg, judge_payload,
                                                    max_tokens=judge_tokens)
    judgments = extract_llm.validate_judgment_result(
        judgments_content, {item["candidate_id"] for item in prepared},
        {item["candidate_id"]: per_candidate[item["candidate_id"]]["recalled_fact_ids"]
         for item in prepared})
    daily_writes = _daily_auto_writes(data_dir)
    created = 0
    for item in prepared:
        candidate_id = item["candidate_id"]
        recall_result = per_candidate[candidate_id]
        relation = dict(judgments.get(candidate_id) or {
            "llm": "new", "target_fact_id": None, "relation_confidence": 0.0,
            "rationale": "", "unverified": True})
        entries = _recall_entries_for(model, recall_result)
        system = extract_quality.system_check(item["content"], entries)
        decision = extract_quality.apply_decision(
            item["extraction_confidence"], relation,
            relation["relation_confidence"], system, relation.get("unverified"),
            bool(cfg["extract"].get("auto_write")), counters["auto_written"],
            daily_writes)
        candidate = extract_state.new_candidate(
            candidate_id, session_id, item["message_ids"],
            item["message_ordinals"], item["chunk_id"], run_id, item["content"],
            item["category_key"], "new", item["extraction_confidence"],
            recalc_for_candidate(recall_result), decision, item["evidence_key"],
            relation={"llm": relation["llm"], "target_fact_id": relation["target_fact_id"],
                      "relation_confidence": relation["relation_confidence"],
                      "rationale": relation.get("rationale"),
                      "unverified": relation.get("unverified", False)},
            system=system, target_fact_id=relation.get("target_fact_id"),
            granularity=item.get("granularity"), subject=item.get("subject"),
            summary_subject_key=None, prompt_version=item.get("prompt_version"))
        counters["candidates_created"] += 1
        created += 1
        if decision["action"] == "auto_write" and not dry_run:
            extraction = _extraction_payload(candidate, "auto_write", run_id, False)
            try:
                result = facts.add_fact(
                    data_dir, item["category_key"], item["content"],
                    item["time_hint"] or clock.display_time(clock.now_valid_from()),
                    source="auto_extract", extraction=extraction)
            except errors.CoreError as exc:
                if exc.code == "CONFLICT_SIMILAR_FACT":
                    candidate["decision"] = {"action": "review",
                                             "reason_code": "c08_conflict"}
                    candidate["system"]["similar"] = True
                    counters["queued"] += 1
                else:
                    raise
            else:
                candidate["status"] = "approved"
                candidate["write"] = {"fact_id": result["fact"]["fact_id"],
                                      "event_seq": None}
                candidate["review"]["status"] = "approved"
                counters["auto_written"] += 1
                counters["auto_written_daily"] = counters.get("auto_written_daily", 0) + 1
                dedupe[item["evidence_key"]] = result["fact"]["fact_id"]
                if not dry_run:
                    extract_state.append_candidate(data_dir, candidate)
                continue
        if not dry_run and candidate["status"] == "pending":
            counters["queued"] += 1
            extract_state.append_candidate(data_dir, candidate)
    return created, summary




def recalc_for_candidate(recall_result):
    return {
        "mode": recall_result["mode"],
        "corpus_bytes": recall_result["corpus_bytes"],
        "current_count": recall_result["current_count"],
        "historical_count": recall_result["historical_count"],
        "recalled_fact_ids": list(recall_result["recalled_fact_ids"]),
        "historical_fact_ids": list(recall_result["historical_fact_ids"]),
        "reasons": dict(recall_result["reasons"]),
        "truncated": bool(recall_result["truncated"]),
    }


# ---------------------------------------------------------------- 候选审核


def resolve_extract_candidate(data_dir, candidate_id, action, patch=None,
                              confirm_token=None):
    return extract_review.resolve_extract_candidate(
        data_dir, candidate_id, action, patch, confirm_token)
