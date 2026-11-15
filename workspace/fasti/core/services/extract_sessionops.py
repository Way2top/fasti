"""C-10 M1：抽取运行的工具函数、水位推进与分流会话记录（从 extract.py 拆出）。"""

import json
import os

from .. import clock
from .. import constants
from .. import extract_state


def _empty_counters():
    return {"sessions_seen": 0, "sessions_processed": 0, "sessions_skipped": 0,
            "messages_processed": 0, "candidates_created": 0, "auto_written": 0,
            "queued": 0, "duplicates": 0, "sensitive_skipped": 0, "errors": 0,
            "dropped_invalid": 0,
            "drop_reasons": {"invalid_candidate_structure": 0,
                             "invalid_category_key": 0, "invalid_evidence": 0}}


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




def advance_watermark(data_dir, watermarks, session, path, new_messages, run_id):
    """成功（含正常 skip）后推进该会话水位；失败路径不得调用。"""
    last = new_messages[-1]
    watermark = watermarks["sessions"].get(session["session_id"]) or {}
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


def diversion_record(session, divert_input, status, decision=None, reason=None,
                     error_code=None):
    """逐会话分流记录（运行记录 sessions[]；不含原文）。"""
    record = {
        "session_id": session["session_id"],
        "status": status,
        "decision": decision,
        "reason": reason,
        "diversion": {
            "title_source": divert_input.get("title_source"),
            "title_kinds_seen": divert_input.get("title_kinds_seen"),
            "title_chars": len(divert_input.get("title") or ""),
            "title_truncated": bool(divert_input.get("title_truncated")),
            "first_message_chars": len(divert_input.get("first_user_message") or ""),
            "first_message_truncated": bool(divert_input.get("first_message_truncated")),
            "user_message_count": divert_input.get("user_message_count"),
        },
    }
    if error_code:
        record["error_code"] = error_code
    return record
