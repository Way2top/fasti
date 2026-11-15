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
def _empty_counters():
    return {"sessions_seen": 0, "sessions_processed": 0, "sessions_skipped": 0,
            "messages_processed": 0, "candidates_created": 0, "auto_written": 0,
            "queued": 0, "duplicates": 0, "sensitive_skipped": 0, "errors": 0}


def _base_url_host(cfg):
    base_url = (cfg.get("llm", {}).get("base_url") or "")
    return base_url.split("://", 1)[-1].split("/", 1)[0] if base_url else None


def _daily_auto_writes(data_dir):
    from .. import clock as clock_module
    today = clock_module.now_local().strftime("%Y-%m-%d")
    count = 0
    for run in extract_state.load_runs(data_dir, 100):
        if str(run.get("started_at") or "").startswith(today):
            count += int((run.get("counters") or {}).get("auto_written") or 0)
    return count


def _entry_for_fact(model, fact_id):
    fact = model.fact(fact_id)
    if fact is None:
        return None
    line = model.lines[fact["line_id"]]
    category = model.category(line["category_key"])
    return {"fact_id": fact["fact_id"], "line_id": fact["line_id"],
            "category_key": line["category_key"], "kind": "current",
            "content": fact["content"], "valid_from": fact["valid_from"],
            "valid_to": fact["valid_to"],
            "disabled": bool(category and not category["enabled"])}


def _recall_entries_for(model, recall_result):
    entries = []
    for fact_id in (recall_result.get("recalled_fact_ids") or []):
        entry = _entry_for_fact(model, fact_id)
        if entry:
            entries.append(entry)
    for fact_id in (recall_result.get("historical_fact_ids") or []):
        fact = model.fact(fact_id)
        if fact is None:
            continue
        line = model.lines[fact["line_id"]]
        category = model.category(line["category_key"])
        entries.append({"fact_id": fact["fact_id"], "line_id": fact["line_id"],
                        "category_key": line["category_key"], "kind": "historical",
                        "content": fact["content"], "valid_from": fact["valid_from"],
                        "valid_to": fact["valid_to"],
                        "disabled": bool(category and not category["enabled"])})
    return entries

def resolve_extract_candidate(data_dir, candidate_id, action, patch=None,
                              confirm_token=None):
    return extract_review.resolve_extract_candidate(
        data_dir, candidate_id, action, patch, confirm_token)

def _dedupe_index(model, data_dir):
    """evidence_key → existing_fact_id（来自既有事实 extraction）+ 已接受候选。"""
    index = {}
    for fact in model.all_facts():
        extraction = fact.get("extraction")
        if not extraction:
            continue
        key = extract_state.evidence_key_for(
            extraction.get("session_id"), extraction.get("message_ids") or [],
            fact["content"])
        index[key] = fact["fact_id"]
    for candidate in extract_state.load_candidates(data_dir).values():
        key = (candidate.get("dedupe") or {}).get("evidence_key")
        if key and candidate.get("status") in ("pending", "approved", "duplicate"):
            index.setdefault(key, (candidate.get("write") or {}).get("fact_id"))
    return index


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
            counters["sessions_processed"] += 1
            try:
                chunks = session_source.chunk_messages(
                    {**session, "messages": new_messages})
                for chunk in chunks:
                    if counters["messages_processed"] + len(chunk["messages"]) > limit_messages:
                        break
                    created, chunk_recall = _process_chunk(
                        data_dir, model, cfg, chunk, run_id, dry_run, dedupe,
                        candidates_before, counters)
                    llm_calls += 2 if created else 1
                    if chunk_recall:
                        recall_mode = chunk_recall["mode"]
                        corpus_bytes = chunk_recall["corpus_bytes"]
                        recalled_total += chunk_recall["recalled"]
                        any_truncated = any_truncated or chunk_recall["truncated"]
                    counters["messages_processed"] += len(chunk["messages"])
            except errors.CoreError as exc:
                counters["errors"] += 1
                run_errors.append({"code": exc.code, "safe_detail": exc.message[:200]})
                sessions_summary.append({"session_id": session["session_id"],
                                         "status": "failed", "code": exc.code})
                if exc.code.startswith("EXTRACT_LLM_") \
                        or exc.code in ("EXTRACT_ZSTD_UNAVAILABLE",):
                    fatal_error = exc
                break
            if not dry_run and new_messages:
                last = new_messages[-1]
                watermarks["sessions"][session["session_id"]] = {
                    "path": path,
                    "last_seq": max(message["seq"] for message in new_messages),
                    "last_message_id": last["message_id"],
                    "last_event_sha256": last["event_sha256"],
                    "processed_user_messages": (watermark.get("processed_user_messages") or 0)
                    + len(new_messages),
                    "updated_at": clock.now_recorded_at(),
                    "run_id": run_id,
                }
                watermarks["updated_at"] = clock.now_recorded_at()
                extract_state.save_watermarks(data_dir, watermarks)
            sessions_summary.append({"session_id": session["session_id"],
                                     "status": "processed",
                                     "messages": len(new_messages)})
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
                "retries": 0, "calls": llm_calls},
        "watermark_rebuilt": bool(rebuilt),
        "sessions": sessions_summary,
        "errors": run_errors,
    }
    if not dry_run:
        extract_state.append_run(data_dir, run)
    if fatal_error is not None:
        raise fatal_error
    return run


def _process_chunk(data_dir, model, cfg, chunk, run_id, dry_run, dedupe,
                   candidates_before, counters):
    sensitive = bool(cfg["extract"].get("sensitive_filter", True))
    messages = extract_llm.build_extraction_messages(chunk, sensitive_filter=sensitive)
    content = extract_llm.chat_completion(cfg, messages, max_tokens=800)
    valid, dropped = extract_llm.validate_extraction_result(content)
    counters["dropped_invalid"] = counters.get("dropped_invalid", 0) + dropped
    chunk_ids = [message["message_id"] for message in chunk["messages"]]
    prepared = []
    for candidate in valid:
        category_key = candidate["category_key"]
        category = model.category(category_key)
        if category is None or category["deleted_at"] is not None \
                or not category["enabled"]:
            counters["dropped_invalid"] = counters.get("dropped_invalid", 0) + 1
            continue
        evidence_ids = [item for item in candidate["evidence"]["message_ids"]
                        if item in chunk_ids]
        if not evidence_ids:
            counters["dropped_invalid"] = counters.get("dropped_invalid", 0) + 1
            continue
        redacted, _kinds = extract_privacy.redact_text(candidate["content"], sensitive)
        if extract_privacy.is_sensitive(candidate["content"], sensitive):
            counters["sensitive_skipped"] += 1
            continue
        candidate_id = extract_state.candidate_id_for(
            chunk["session_id"], evidence_ids, redacted, category_key, "new")
        evidence_key = extract_state.evidence_key_for(
            chunk["session_id"], evidence_ids, redacted)
        existing_fact_id = dedupe.get(evidence_key)
        existing_candidate = next((item for item in candidates_before.values()
                                   if (item.get("dedupe") or {}).get("evidence_key")
                                   == evidence_key
                                   and item.get("status") in ("pending", "approved",
                                                              "duplicate")), None)
        if existing_fact_id or existing_candidate:
            counters["duplicates"] += 1
            continue
        ordinals = {message["message_id"]: message["ordinal"]
                    for message in chunk["messages"]}
        prepared.append({
            "candidate_id": candidate_id,
            "content": redacted,
            "category_key": category_key,
            "time_hint": candidate["time_hint"],
            "extraction_confidence": candidate["extraction_confidence"],
            "message_ids": evidence_ids,
            "message_ordinals": [ordinals[item] for item in evidence_ids],
            "evidence_key": evidence_key,
            "change_type": "new",
        })
    if not prepared:
        return 0, None
    per_candidate, summary = extract_recall.recall_for_candidates(model, prepared)
    recall_lines = {candidate_id: result["lines"]
                    for candidate_id, result in per_candidate.items()}
    judge_payload = extract_llm.build_judgment_messages(prepared, recall_lines,
                                                        sensitive_filter=sensitive)
    judgments_content = extract_llm.chat_completion(cfg, judge_payload, max_tokens=600)
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
            candidate_id, chunk["session_id"], item["message_ids"],
            item["message_ordinals"], chunk["chunk_id"], run_id, item["content"],
            item["category_key"], "new", item["extraction_confidence"],
            recalc_for_candidate(recall_result), decision, item["evidence_key"],
            relation={"llm": relation["llm"], "target_fact_id": relation["target_fact_id"],
                      "relation_confidence": relation["relation_confidence"],
                      "rationale": relation.get("rationale"),
                      "unverified": relation.get("unverified", False)},
            system=system, target_fact_id=relation.get("target_fact_id"))
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
