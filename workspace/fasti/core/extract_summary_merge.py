"""C-11 ADR-037：summary 同项目合并（确定性 canonical + 别名 + 保守相似）。"""

import hashlib
import re
import unicodedata

from . import similarity

STOPWORDS = (
    "项目", "系统", "工程", "工具", "平台", "产品", "应用", "软件", "程序", "方向",
    "开源", "个人", "我的", "记忆", "笔记", "助手", "仓库", "代码库", "进展", "计划",
    "当前", "这个", "那个", "的", "了", "project", "system", "tool", "platform",
    "product", "app", "application", "open", "source", "personal", "memory", "note",
    "assistant", "repo", "current", "my",
)
_ASCII_TOKEN = re.compile(r"[a-z][a-z0-9+#]{1,}")


def _normalize(text):
    return unicodedata.normalize("NFKC", text or "").lower()


def canonical_subject_key(subject, project_aliases=None):
    """→ (canonical, aliases[])；括号内容/ASCII 实体词/配置别名。"""
    text = _normalize(subject)
    aliases = set()
    for match in re.findall(r"[（(]([^）)]{1,40})[）)]", text):
        alias = _clean(match)
        if len(alias) >= 2:
            aliases.add(alias)
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    for token in _ASCII_TOKEN.findall(text):
        if len(token) >= 2 and token not in STOPWORDS:
            aliases.add(token)
    cleaned = _clean(text)
    if not cleaned:
        cleaned = _clean(subject)
    aliases.add(cleaned)
    mapping = project_aliases or {}
    for alias in mapping.get(cleaned, []) or []:
        value = _clean(alias)
        if len(value) >= 2:
            aliases.add(value)
    return cleaned, sorted(aliases)


def _clean(text):
    text = _normalize(text)
    for word in STOPWORDS:
        text = text.replace(word, "")
    return "".join(char for char in text if char.isalnum())


def _group_key(category_key, canonical):
    digest = hashlib.sha256(("%s\0%s" % (category_key, canonical)).encode("utf-8"))
    return digest.hexdigest()[:12]


def _shared_ascii(a, b):
    return set(_ASCII_TOKEN.findall(a or "")) & set(_ASCII_TOKEN.findall(b or ""))


def _should_merge(a, b):
    if a["canonical_subject_key"] == b["canonical_subject_key"]:
        return True, 1.0
    left = set([a["canonical_subject_key"]] + a["subject_aliases"])
    right = set([b["canonical_subject_key"]] + b["subject_aliases"])
    if any(len(token) >= 2 and token in right for token in left) or \
            any(len(token) >= 2 and token in left for token in right):
        return True, 0.9
    if len(a["canonical_subject_key"]) >= 3 and len(b["canonical_subject_key"]) >= 3:
        score = similarity.text_similarity(a["canonical_subject_key"],
                                           b["canonical_subject_key"])
        if score >= 0.85:
            return True, score
    return False, 0.0


def _retention_key(item):
    return (-item["extraction_confidence"], -len(item["content"]),
            -max(item["message_ordinals"]), item.get("candidate_id") or "",
            item.get("chunk_id") or "")


def _describe(item):
    return {"subject": item.get("subject"), "canonical": item["canonical_subject_key"],
            "extraction_confidence": item["extraction_confidence"],
            "content_sha256": hashlib.sha256(item["content"].encode("utf-8")).hexdigest(),
            "message_ids": list(item["message_ids"]), "chunk_id": item["chunk_id"]}


def merge_summaries(items, project_aliases=None):
    """同类别 summary 合并；返回 (kept_items, stats)。atomic 不在本函数内。"""
    for item in items:
        canonical, aliases = canonical_subject_key(item.get("subject"), project_aliases)
        item["canonical_subject_key"] = canonical
        item["subject_aliases"] = aliases
    groups_by_category = {}
    for item in items:
        groups_by_category.setdefault(item["category_key"], []).append(item)
    kept, groups, suggestion_groups = [], [], []
    before = len(items)
    for category in sorted(groups_by_category):
        nodes = groups_by_category[category]
        parent = list(range(len(nodes)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        similarities = {}
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                merge, score = _should_merge(nodes[i], nodes[j])
                if merge:
                    parent[find(j)] = find(i)
                    similarities[(i, j)] = round(score, 3)
        clusters = {}
        for index in range(len(nodes)):
            clusters.setdefault(find(index), []).append(nodes[index])
        category_keepers = []
        for members in clusters.values():
            members.sort(key=_retention_key)
            keeper = members[0]
            if len(members) > 1:
                keeper["merge"] = {
                    "group_key": _group_key(category, keeper["canonical_subject_key"]),
                    "auto": True, "merged_count": len(members) - 1,
                    "merged_from": [_describe(item) for item in members[1:]],
                }
                groups.append({
                    "category_key": category,
                    "canonical": keeper["canonical_subject_key"],
                    "kept_subject": keeper.get("subject"),
                    "merged_subjects": [item.get("subject") for item in members[1:]],
                    "similarities": sorted(similarities.values(), reverse=True)})
            else:
                others = [item for item in nodes if item is not keeper]
                suggested = []
                for other in others:
                    score = round(similarity.text_similarity(
                        keeper["canonical_subject_key"],
                        other["canonical_subject_key"]), 3)
                    if score >= 0.35 or _shared_ascii(keeper["canonical_subject_key"],
                                                      other["canonical_subject_key"]):
                        suggested.append({"subject": other.get("subject"),
                                          "canonical": other["canonical_subject_key"],
                                          "similarity": score})
                if suggested:
                    keeper["merge"] = {
                        "group_key": _group_key(category, keeper["canonical_subject_key"]),
                        "auto": False, "merged_count": 0, "merged_from": [],
                        "suggestions": suggested}
                    suggestion_groups.append({
                        "category_key": category,
                        "kept_subject": keeper.get("subject"),
                        "candidates": suggested})
            kept.append(keeper)
            category_keepers.append(keeper)
        if len(category_keepers) >= 2:
            for keeper in category_keepers:
                others = [item for item in category_keepers if item is not keeper]
                suggestions = []
                for other in others:
                    suggestions.append({
                        "subject": other.get("subject"),
                        "canonical": other["canonical_subject_key"],
                        "similarity": round(similarity.text_similarity(
                            keeper["canonical_subject_key"],
                            other["canonical_subject_key"]), 3)})
                existing = keeper.get("merge") or {}
                if suggestions and not existing.get("auto") \
                        and not existing.get("suggestions"):
                    keeper["merge"] = {
                        "group_key": _group_key(category,
                                                keeper["canonical_subject_key"]),
                        "auto": False, "merged_count": 0, "merged_from": [],
                        "suggestions": suggestions}
            suggestion_groups.append({
                "category_key": category,
                "subjects": [item.get("subject") for item in category_keepers],
                "reason": "无法自动判定，请人工确认（两条候选均保留）"})
    kept.sort(key=lambda item: (0 if item["granularity"] == "summary" else 1,
                                -item["extraction_confidence"],
                                min(item["message_ordinals"]), item["content"],
                                item["chunk_id"]))
    stats = {"before": before, "after": len(kept),
             "auto_merged": before - len(kept), "groups": groups,
             "suggestion_groups": suggestion_groups}
    return kept, stats
