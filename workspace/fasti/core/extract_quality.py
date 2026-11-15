"""C-09 系统相似检查、置信度与决策矩阵（ADR-030）。

第二层（确定性）：在召回语料上跑 C-08 Tier1/Tier2 + 中文保守检查
（汉字 bigram Dice≥0.45 且共享 bigram≥2）。任何一层认为已有关系 ⇒ 不自动写。
"""

from . import constants
from . import extract_recall
from . import similarity


def system_check(candidate_text, recall_entries):
    """→ {similar, matches[], layer_mismatch:False}（layer_mismatch 由调用方结合 LLM 判定）。"""
    matches = []
    for entry in recall_entries:
        c08 = similarity.text_similarity(candidate_text, entry["content"])
        c08_shared = sorted(similarity.ascii_entities(candidate_text)
                            & similarity.ascii_entities(entry["content"]))
        cjk_dice, cjk_shared = extract_recall.cjk_bigram_dice(candidate_text, entry["content"])
        reasons = []
        if similarity.is_similar_hit(c08, len(c08_shared)):
            reasons.append("c08_tier1" if c08 >= constants.SIMILARITY_THRESHOLD
                           else "c08_tier2")
        if cjk_dice >= constants.EXTRACT_CJK_BIGRAM_THRESHOLD \
                and cjk_shared >= constants.EXTRACT_CJK_MIN_SHARED:
            reasons.append("cjk_bigram")
        if reasons:
            matches.append({"fact_id": entry["fact_id"], "similarity": round(c08, 3),
                            "cjk_bigram_dice": round(cjk_dice, 3),
                            "reasons": reasons})
    matches.sort(key=lambda item: (-item["similarity"], item["fact_id"]))
    return {"similar": bool(matches), "matches": matches, "layer_mismatch": False}


def confidence_band(extraction_confidence, relation, relation_confidence, system_similar):
    if extraction_confidence >= constants.EXTRACT_AUTO_HIGH_CONFIDENCE \
            and relation == "new" \
            and relation_confidence >= constants.EXTRACT_AUTO_RELATION_CONFIDENCE \
            and not system_similar:
        return "high"
    if extraction_confidence < constants.EXTRACT_LOW_CONFIDENCE \
            or relation_confidence < constants.EXTRACT_LOW_RELATION:
        return "low"
    return "medium"


def apply_decision(extraction_confidence, relation, relation_confidence, system,
                   unverified, auto_write_enabled, run_auto_writes,
                   daily_auto_writes):
    """决策矩阵（优先级从高到低）→ {action: auto_write|review|reject, reason_code, band}。"""
    relation_name = relation.get("llm") if isinstance(relation, dict) else relation
    system_similar = bool(system.get("similar"))
    unverified = bool(unverified or (relation.get("unverified")
                                    if isinstance(relation, dict) else False))
    layer_mismatch = False
    if relation_name == "new" and system_similar:
        layer_mismatch = True
    if relation_name in ("update", "conflict") and not system_similar:
        layer_mismatch = True
    band = confidence_band(extraction_confidence, relation_name, relation_confidence,
                           system_similar)
    if unverified:
        return {"action": "review", "reason_code": "relation_unverified", "band": band,
                "layer_mismatch": layer_mismatch}
    if relation_name in ("update", "conflict"):
        return {"action": "review", "reason_code": relation_name, "band": band,
                "layer_mismatch": layer_mismatch}
    if system_similar:
        return {"action": "review", "reason_code": "layer_mismatch", "band": band,
                "layer_mismatch": True}
    if band == "low":
        return {"action": "review", "reason_code": "low_confidence", "band": band,
                "layer_mismatch": False}
    if not auto_write_enabled:
        return {"action": "review", "reason_code": "auto_write_disabled", "band": band,
                "layer_mismatch": False}
    if run_auto_writes >= constants.EXTRACT_AUTO_WRITE_PER_RUN \
            or daily_auto_writes >= constants.EXTRACT_AUTO_WRITE_DAILY:
        return {"action": "review", "reason_code": "auto_write_capped", "band": band,
                "layer_mismatch": False}
    return {"action": "auto_write", "reason_code": "high_confidence", "band": band,
            "layer_mismatch": False}
