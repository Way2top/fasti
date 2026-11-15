"""C-09 候选审核（approve/reject）：审核通过复用既有写入路径 + extraction 审计。"""

import os

from .. import clock
from .. import errors
from .. import extract_privacy
from .. import extract_state
from .. import store
from . import facts


def _extraction_payload(candidate, mode, run_id, reviewed_by_user):
    relation = candidate.get("relation") or {}
    return {
        "candidate_id": candidate["candidate_id"],
        "session_id": candidate["session_id"],
        "message_ids": list(candidate["message_ids"]),
        "message_ordinals": list(candidate["message_ordinals"]),
        "mode": mode,
        "run_id": run_id,
        "llm_relation": relation.get("llm") or "new",
        "system_relation": "similar" if (candidate.get("system") or {}).get("similar")
                           else "none",
        "confidence": relation.get("relation_confidence")
                      if relation.get("relation_confidence") is not None
                      else candidate.get("extraction_confidence", 0.0),
        "reviewed_by_user": reviewed_by_user,
        "recall": {
            "mode": candidate["recall"]["mode"],
            "current_fact_ids": list(candidate["recall"].get("recalled_fact_ids") or []),
            "historical_fact_ids": list(
                candidate["recall"].get("historical_fact_ids") or []),
            "truncated": bool(candidate["recall"].get("truncated")),
        },
    }


def resolve_extract_candidate(data_dir, candidate_id, action, patch=None,
                              confirm_token=None):
    data_dir = os.path.abspath(data_dir)
    store.require_initialized(data_dir)
    if action not in ("approve", "reject"):
        raise errors.make("VALIDATION_INVALID_HANDLING",
                          "action 只能是 approve / reject")
    patch = patch or {}
    candidate = extract_state.get_candidate(data_dir, candidate_id)
    if candidate.get("status") != "pending":
        raise errors.make("EXTRACT_CANDIDATE_ALREADY_RESOLVED",
                          "候选已处理（当前状态：%s），不能再次操作"
                          % candidate.get("status"),
                          details={"status": candidate.get("status")})
    if action == "reject":
        candidate["status"] = "rejected"
        candidate["review"]["status"] = "rejected"
        candidate["review"]["reviewed_at"] = clock.now_recorded_at()
        candidate["review"]["reject_reason"] = (patch.get("reason") or "")[:500]
        extract_state.update_candidate(data_dir, candidate)
        return {"candidate": candidate}
    if patch.get("content") is not None:
        candidate["review"]["original_content"] = candidate["content"]
        candidate["content"] = extract_privacy.redact_text(str(patch["content"]), True)[0]
        candidate["review"]["final_content"] = candidate["content"]
    if patch.get("category") is not None:
        candidate["category_key"] = str(patch["category"])
    change_type = patch.get("change_type")
    relation_name = (candidate.get("relation") or {}).get("llm")
    if change_type is None:
        if relation_name in ("update", "conflict"):
            raise errors.make(
                "EXTRACT_CANDIDATE_INVALID",
                "更新/冲突类候选通过时必须显式选择演进（evolve）或纠正（correct）",
                details={"field": "change_type"})
        change_type = "new"
    if change_type == "new" and relation_name in ("update", "conflict"):
        raise errors.make("EXTRACT_CANDIDATE_INVALID",
                          "更新/冲突类候选不能按 new 通过",
                          details={"field": "change_type"})
    if change_type not in ("new", "evolve", "correct"):
        raise errors.make("EXTRACT_CANDIDATE_INVALID",
                          "change_type 非法", details={"field": "change_type"})
    extraction = _extraction_payload(candidate, "user_approved",
                                     candidate.get("run_id"), True)
    if change_type == "new":
        result = facts.add_fact(
            data_dir, candidate["category_key"], candidate["content"],
            patch.get("valid_from") or candidate.get("time_hint")
            or clock.display_time(clock.now_valid_from()),
            confirm_token=confirm_token, source="auto_extract",
            extraction=extraction)
        fact_id = result["fact"]["fact_id"]
        warnings = result.get("warnings", [])
    else:
        target = (candidate.get("relation") or {}).get("target_fact_id") \
            or candidate.get("target_fact_id")
        if not target:
            raise errors.make("EXTRACT_CANDIDATE_INVALID",
                              "更新类候选缺少目标事实",
                              details={"field": "target_fact_id"})
        result = facts.change_fact(
            data_dir, target, change_type, candidate["content"],
            patch.get("valid_from"), patch.get("note"), extraction=extraction)
        fact_id = result["fact"]["fact_id"]
        warnings = result.get("warnings", [])
    candidate["status"] = "approved"
    candidate["change_type"] = change_type
    candidate["review"]["status"] = "approved"
    candidate["review"]["reviewed_at"] = clock.now_recorded_at()
    candidate["review"]["final_content"] = candidate["content"]
    candidate["write"] = {"fact_id": fact_id, "event_seq": None}
    extract_state.update_candidate(data_dir, candidate)
    return {"candidate": candidate, "fact": result["fact"],
            "change_type": change_type, "warnings": warnings}
