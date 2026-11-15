"""C-09 确定性召回（ADR-030 / api-spec conventions.extract.recall）。

- 语料：当前有效事实（每 line 的 head，**含已关闭类别**）+ 历史段（被演进取代的旧段，
  不含已删除段）。
- 规模：当前事实单行压缩总字节 ≤8192 ⇒ mode=full（全量当前事实）；>8192 ⇒ mode=retrieval
  （每候选独立 top-K ≤20 / ≤6KB，截断标 truncated）。
- 历史段按需：候选建议 evolve/correct、系统相似命中、或与历史段评分达阈值时追加
  （≤10 条 / ≤2KB，标 historical）。
- 评分：max(C-08 两级相似度, 中文 bigram Dice, ASCII 实体重叠分)；同分按 fact_id 升序。
- 一行格式：`id=<短ID> | cat=<key>[|disabled] | current|historical | from=<display> | <内容≤120字>`
  （historical 额外标 `line=<短ID>`）。
- 可解释性：每个候选记录 recall{mode, corpus_bytes, current_count, historical_count,
  recalled_fact_ids, historical_fact_ids, reasons{}, truncated}；确定性。
"""

from . import clock
from . import constants
from . import ids
from . import similarity

_HAN_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))


def _is_han(char):
    code = ord(char)
    return any(start <= code <= end for start, end in _HAN_RANGES)


def cjk_bigrams(text):
    chars = [char for char in (text or "") if _is_han(char)]
    return [chars[index] + chars[index + 1] for index in range(len(chars) - 1)]


def cjk_bigram_dice(left, right):
    """中文保守检查：汉字 bigram Dice + 共享 bigram 数 → (dice, shared)。"""
    a = cjk_bigrams(left)
    b = cjk_bigrams(right)
    if not a or not b:
        return 0.0, 0
    from collections import Counter
    counter_a, counter_b = Counter(a), Counter(b)
    shared = sum(min(counter_a[key], counter_b[key]) for key in counter_a.keys() & counter_b.keys())
    total = len(a) + len(b)
    return (2.0 * shared / total if total else 0.0), shared


def ascii_entity_overlap(left, right):
    a = similarity.ascii_entities(left)
    b = similarity.ascii_entities(right)
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def score_candidate(candidate_text, fact_text):
    """→ (score, reasons)。score = max(C-08 相似度, 中文 bigram, ASCII 实体重叠)。"""
    c08 = similarity.text_similarity(candidate_text, fact_text)
    c08_shared = sorted(similarity.ascii_entities(candidate_text)
                        & similarity.ascii_entities(fact_text))
    cjk_dice, cjk_shared = cjk_bigram_dice(candidate_text, fact_text)
    entity = ascii_entity_overlap(candidate_text, fact_text)
    reasons = []
    if similarity.is_similar_hit(c08, len(c08_shared)):
        reasons.append("tier1" if c08 >= constants.SIMILARITY_THRESHOLD else "tier2")
    if cjk_dice >= constants.EXTRACT_CJK_BIGRAM_THRESHOLD \
            and cjk_shared >= constants.EXTRACT_CJK_MIN_SHARED:
        reasons.append("cjk_bigram")
    if entity > 0:
        reasons.append("entity_overlap")
    return max(c08, cjk_dice, entity), reasons


def current_entries(model):
    """当前有效事实（每 line 的 head；含已关闭类别的当前值）。"""
    entries = []
    for line in model.ordered_lines():
        head = model.head_fact(line["line_id"])
        if head is None:
            continue
        category = model.category(line["category_key"])
        entries.append({
            "fact_id": head["fact_id"],
            "line_id": line["line_id"],
            "category_key": line["category_key"],
            "kind": "current",
            "content": head["content"],
            "valid_from": head["valid_from"],
            "valid_to": head["valid_to"],
            "disabled": bool(category and not category["enabled"]),
        })
    return entries


def historical_entries(model, line_id=None, category_key=None):
    """历史段（被演进取代、未删除的旧段）。"""
    entries = []
    for line in model.ordered_lines(category_key):
        if line_id is not None and line["line_id"] != line_id:
            continue
        head = model.head_fact(line["line_id"])
        for fact in model.line_facts(line["line_id"]):
            if fact["deleted_at"] is not None:
                continue
            if head is not None and fact["fact_id"] == head["fact_id"]:
                continue
            category = model.category(line["category_key"])
            entries.append({
                "fact_id": fact["fact_id"],
                "line_id": line["line_id"],
                "category_key": line["category_key"],
                "kind": "historical",
                "content": fact["content"],
                "valid_from": fact["valid_from"],
                "valid_to": fact["valid_to"],
                "disabled": bool(category and not category["enabled"]),
            })
    return entries


def one_line(entry):
    """`id=<短ID> | cat=<key>[|disabled] | current|historical | from=<display> | <内容≤120字>`"""
    content = entry["content"]
    if len(content) > 120:
        content = content[:120] + "…"
    parts = [
        "id=" + ids.short_id(entry["fact_id"]),
        "cat=" + entry["category_key"] + ("|disabled" if entry.get("disabled") else ""),
        entry["kind"],
        "from=" + clock.display_time(entry["valid_from"]),
    ]
    if entry["kind"] == "historical":
        parts.append("line=" + ids.short_id(entry["line_id"]))
    return " | ".join(parts) + " | " + content


def _line_bytes(entry):
    return len(one_line(entry).encode("utf-8"))


def corpus_bytes(entries):
    return sum(_line_bytes(entry) for entry in entries)


def choose_mode(model):
    entries = current_entries(model)
    total = corpus_bytes(entries)
    return ("full" if total <= constants.EXTRACT_RECALL_FULL_BYTES else "retrieval"), total


def recall_for_candidates(model, candidates):
    """对每个候选做确定性召回；返回 (per_candidate, run_summary)。

    candidates: [{candidate_id, content, category_key, change_type?}]
    """
    current = current_entries(model)
    mode, total_bytes = (("full", corpus_bytes(current))
                         if corpus_bytes(current) <= constants.EXTRACT_RECALL_FULL_BYTES
                         else ("retrieval", corpus_bytes(current)))
    per_candidate = {}
    recalled_total = 0
    any_truncated = False
    for candidate in candidates:
        content = candidate["content"]
        category_key = candidate.get("category_key")
        scores = []
        for entry in current:
            score, reasons = score_candidate(content, entry["content"])
            if entry["category_key"] == category_key and "same_category" not in reasons:
                reasons = ["same_category"] + reasons
            scores.append((score, entry, reasons))
        scores.sort(key=lambda item: (-item[0], item[1]["fact_id"]))
        selected = []
        truncated = False
        if mode == "full":
            selected = [(entry, ["full_mode"] + reasons) for _score, entry, reasons in scores]
        else:
            budget = constants.EXTRACT_RECALL_MAX_BYTES
            for score, entry, reasons in scores:
                if len(selected) >= constants.EXTRACT_RECALL_TOP_K:
                    truncated = True
                    break
                cost = _line_bytes(entry)
                if budget - cost < 0:
                    truncated = True
                    continue
                budget -= cost
                selected.append((entry, ["retrieval"] + reasons))
        historical = _historical_for(candidate, selected, model, content,
                                     category_key, mode)
        entries = [entry for entry, _reasons in selected]
        if historical:
            entries = entries + [entry for entry, _reasons in historical]
        lines = [one_line(entry) for entry in entries]
        reasons = {}
        for entry, entry_reasons in selected + historical:
            reasons[entry["fact_id"]] = sorted(set(entry_reasons))
        recalled_ids = [entry["fact_id"] for entry, _ in selected]
        historical_ids = [entry["fact_id"] for entry, _ in historical]
        historical_count = len(historical_ids)
        current_count = len(recalled_ids)
        available_history = _historical_available(model, category_key, candidate)
        budget_truncated = truncated or len(historical_ids) < len(available_history)
        result = {
            "mode": mode,
            "corpus_bytes": total_bytes,
            "current_count": current_count,
            "historical_count": historical_count,
            "recalled_fact_ids": recalled_ids,
            "historical_fact_ids": historical_ids,
            "reasons": reasons,
            "truncated": bool(budget_truncated),
            "lines": lines,
        }
        per_candidate[candidate["candidate_id"]] = result
        recalled_total += current_count + historical_count
        any_truncated = any_truncated or result["truncated"]
    summary = {
        "mode": mode,
        "corpus_bytes": total_bytes,
        "candidates": len(candidates),
        "recalled": recalled_total,
        "truncated": any_truncated,
    }
    return per_candidate, summary


def _historical_available(model, category_key, candidate):
    entries = historical_entries(model, category_key=category_key)
    if candidate.get("change_type") in ("evolve", "correct") or candidate.get("system_similar"):
        return entries
    return []


def _historical_for(candidate, selected, model, content, category_key, mode):
    """按需追加历史段：change_type=evolve/correct、系统相似命中、或评分达阈值。"""
    needs = candidate.get("change_type") in ("evolve", "correct") \
        or candidate.get("system_similar")
    matched_line = None
    if selected:
        top_entry, top_reasons = selected[0]
        if "tier1" in top_reasons or "tier2" in top_reasons or "cjk_bigram" in top_reasons:
            needs = True
            matched_line = top_entry["line_id"]
    if not needs:
        return []
    pool = []
    for entry in historical_entries(model, category_key=category_key):
        score, reasons = score_candidate(content, entry["content"])
        if matched_line is not None and entry["line_id"] == matched_line:
            entry_reasons = ["historical_line"] + reasons
        elif score >= constants.SIMILARITY_TOPIC_THRESHOLD and reasons:
            entry_reasons = ["historical_lexical"] + reasons
        else:
            continue
        pool.append((score, entry, entry_reasons))
    pool.sort(key=lambda item: (-item[0], item[1]["fact_id"]))
    selected = []
    budget = constants.EXTRACT_HISTORICAL_MAX_BYTES
    for score, entry, entry_reasons in pool:
        if len(selected) >= constants.EXTRACT_HISTORICAL_TOP_K:
            break
        cost = _line_bytes(entry)
        if budget - cost < 0:
            continue
        budget -= cost
        selected.append((entry, entry_reasons))
    return selected
