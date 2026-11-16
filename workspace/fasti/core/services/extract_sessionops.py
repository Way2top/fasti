"""C-10 M1：抽取运行的工具函数、水位推进与分流会话记录（从 extract.py 拆出）。"""

import json
import os

from .. import clock
from .. import constants
from .. import extract_llm
from .. import extract_privacy
from .. import extract_privacy
from .. import extract_state
from .. import extract_summary_merge
from .. import session_source


def _empty_counters():
    return {"sessions_seen": 0, "sessions_processed": 0, "sessions_skipped": 0,
            "messages_processed": 0, "candidates_created": 0, "auto_written": 0,
            "queued": 0, "duplicates": 0, "sensitive_skipped": 0, "errors": 0,
            "candidates_extracted": 0, "candidates_after_dedup": 0,
            "candidates_kept": 0, "dropped_duplicate_summary": 0,
            "dropped_by_session_cap": 0,
            "dropped_invalid": 0,
            "drop_reasons": {"invalid_candidate_structure": 0,
                             "invalid_granularity": 0, "missing_subject": 0,
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


def title_record(divert_input):
    """→ {text(打码),source(provider|first_user_message_fallback|unavailable),truncated}。"""
    text = (divert_input or {}).get("title")
    if text:
        redacted, kinds = extract_privacy.redact_text(text)
        if kinds:
            redacted = "[REDACTED]"
        return {"text": redacted, "source": "provider",
                "truncated": bool(divert_input.get("title_truncated"))}
    first = (divert_input or {}).get("first_user_message") or ""
    if first:
        redacted, kinds = extract_privacy.redact_text(first)
        if kinds:
            return {"text": "[REDACTED]", "source": "first_user_message_fallback",
                    "truncated": False}
        truncated = len(redacted) > 120
        return {"text": redacted[:120] + ("…[TRUNCATED]" if truncated else ""),
                "source": "first_user_message_fallback", "truncated": truncated}
    return {"text": None, "source": "unavailable", "truncated": False}


def diversion_record(session, divert_input, status, decision=None, reason=None,
                     error_code=None):
    """逐会话分流记录（运行记录 sessions[]；不含原文）。"""
    record = {
        "session_id": session["session_id"],
        "title": title_record(divert_input),
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


def summary_subject_key(subject):
    """summary 去重键：NFKC + 小写 + 仅保留字母数字（去空白/标点）。"""
    import unicodedata
    if not subject:
        return None
    text = unicodedata.normalize("NFKC", subject).lower()
    return "".join(char for char in text if char.isalnum()) or None


def extract_chunk(cfg, chunk, model, categories, sensitive, counters):
    """单块 A 段抽取 → 校验/过滤；返回 buffer 条目（不写库、不调判定）。"""
    messages = extract_llm.build_extraction_messages(
        chunk, sensitive_filter=sensitive, categories=categories)
    extract_tokens = min(
        constants.EXTRACT_EXTRACT_TOKENS_CAP,
        constants.EXTRACT_EXTRACT_TOKENS_BASE
        + constants.EXTRACT_EXTRACT_TOKENS_PER_MESSAGE * len(chunk["messages"]))
    content = extract_llm.chat_completion(
        cfg, messages, max_tokens=extract_tokens,
        disable_thinking=constants.EXTRACT_EXTRACT_DISABLE_THINKING)
    valid, dropped = extract_llm.validate_extraction_result(content)
    for reason, count in dropped.items():
        counters["dropped_invalid"] = counters.get("dropped_invalid", 0) + count
        counters["drop_reasons"] = counters.get("drop_reasons") or {}
        counters["drop_reasons"][reason] = counters["drop_reasons"].get(reason, 0) + count
    chunk_ids = [message["message_id"] for message in chunk["messages"]]
    ordinals = {message["message_id"]: message["ordinal"]
                for message in chunk["messages"]}
    buffer = []
    for candidate in valid:
        category_key = extract_llm.resolve_category(candidate["category_key"], categories)
        if category_key is None:
            counters["dropped_invalid"] += 1
            counters["drop_reasons"]["invalid_category_key"] += 1
            continue
        category = model.category(category_key)
        if category is None or category["deleted_at"] is not None \
                or not category["enabled"]:
            counters["dropped_invalid"] += 1
            counters["drop_reasons"]["invalid_category_key"] += 1
            continue
        evidence_ids = [item for item in candidate["evidence"]["message_ids"]
                        if item in chunk_ids]
        if not evidence_ids:
            counters["dropped_invalid"] += 1
            counters["drop_reasons"]["invalid_evidence"] += 1
            continue
        redacted, _kinds = extract_privacy.redact_text(candidate["content"], sensitive)
        if extract_privacy.is_sensitive(candidate["content"], sensitive):
            counters["sensitive_skipped"] += 1
            continue
        buffer.append({
            "content": redacted,
            "category_key": category_key,
            "granularity": candidate["granularity"],
            "subject": candidate.get("subject"),
            "summary_subject_key": summary_subject_key(candidate.get("subject")),
            "time_hint": candidate["time_hint"],
            "extraction_confidence": candidate["extraction_confidence"],
            "message_ids": evidence_ids,
            "message_ordinals": [ordinals[item] for item in evidence_ids],
            "chunk_id": chunk["chunk_id"],
            "prompt_version": constants.EXTRACT_PROMPT_EXTRACTION,
        })
    return buffer


def postprocess_session(buffer, cap=None, project_aliases=None):
    """C-11：summary 同项目合并（canonical/alias/相似）→ 排序 → 截断 cap。"""
    cap = cap or constants.EXTRACT_SESSION_CANDIDATE_CAP
    summaries = [item for item in buffer if item["granularity"] == "summary"]
    atomic = [item for item in buffer if item["granularity"] != "summary"]
    merged_summaries, merge_stats = extract_summary_merge.merge_summaries(
        summaries, project_aliases=project_aliases)
    merged = merged_summaries + atomic
    merged.sort(key=lambda item: (
        0 if item["granularity"] == "summary" else 1,
        -item["extraction_confidence"], item["content"],
        min(item["message_ordinals"]), item["chunk_id"]))
    kept = merged[:cap]
    stats = {"candidates_extracted": len(buffer),
             "candidates_after_dedup": len(merged),
             "candidates_kept": len(kept),
             "dropped_duplicate_summary": merge_stats["auto_merged"],
             "dropped_by_session_cap": len(merged) - len(kept),
             "merged_summaries": merge_stats["auto_merged"],
             "summary_merge": merge_stats}
    return kept, stats


def prepare_kept(kept, session_id, candidates_before, dedupe, counters):
    """确定性 candidate_id/evidence_key + 幂等去重 → 可送召回/判定的候选。"""
    prepared = []
    for item in kept:
        candidate_id = extract_state.candidate_id_for(
            session_id, item["message_ids"], item["content"],
            item["category_key"], "new")
        evidence_key = extract_state.evidence_key_for(
            session_id, item["message_ids"], item["content"])
        if dedupe.get(evidence_key) or any(
                (candidate.get("dedupe") or {}).get("evidence_key") == evidence_key
                and candidate.get("status") in ("pending", "approved", "duplicate")
                for candidate in candidates_before.values()):
            counters["duplicates"] += 1
            continue
        prepared.append({**item, "candidate_id": candidate_id,
                         "evidence_key": evidence_key, "session_id": session_id})
    return prepared
