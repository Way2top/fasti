"""C-08 防重复：零依赖相似检测 + stateless 确认令牌（ADR-027 / architecture §14）。

两级规则（api-spec `conventions.similarity`）：
- Tier1 高相似：字符级 unigram+bigram Dice（NFKC→小写→仅保留 isalnum 与 `+`/`#`），
  `sim = 0.5·Dice(U)+0.5·Dice(B)`，完全相等=1.0，任一侧归一化长度<2 记 0；
- Tier2 主题改写：`sim >= 0.25` 且共享 ASCII 实体词（正则 `[a-z][a-z0-9+#]*`，长度≥2）>= 2。

比对范围：同 category_key 下每条 line 的当前 head（未删除）；候选按 sim 降序、同分 fact_id 升序，
最多 5 条。`change` 不参与（由服务层保证）。

确认令牌：base64url(sha256(canonical_json(payload))[:16])，22 字符，stateless；
payload 绑定 操作/类别/原始内容/候选 fact_id 升序集/算法/三阈值。令牌重算不一致 ⇒ 失效。
"""

import base64
import hashlib
import json
import re
import unicodedata
from collections import Counter

from . import constants

KEEP = set("+#")
TOKEN_RE = re.compile(r"[a-z][a-z0-9+#]*")
MAX_CANDIDATES = 5


def normalize_content(text):
    """NFKC → 小写 → 仅保留 isalnum 与 +/#（保留 C++/C# 区分）。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(char for char in normalized if char.isalnum() or char in KEEP)


def ngrams(text, size):
    if size == 1:
        return Counter(text)
    if len(text) < size:
        return Counter({text: 1}) if text else Counter()
    return Counter(text[index:index + size] for index in range(len(text) - size + 1))


def dice(left, right):
    if not left or not right:
        return 0.0
    overlap = sum(min(left[key], right[key]) for key in left.keys() & right.keys())
    return 2.0 * overlap / (sum(left.values()) + sum(right.values()))


def text_similarity(new_content, candidate_content):
    """返回 round(...,3) 的相似度（0..1）。"""
    left = normalize_content(new_content)
    right = normalize_content(candidate_content)
    if left == right:
        return 1.0
    if len(left) < 2 or len(right) < 2:
        return 0.0
    value = 0.5 * dice(ngrams(left, 1), ngrams(right, 1)) \
        + 0.5 * dice(ngrams(left, 2), ngrams(right, 2))
    return round(value, 3)


def ascii_entities(text):
    """ASCII 实体词集合（NFKC→小写，匹配 [a-z][a-z0-9+#]*，长度≥2）。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return {token for token in TOKEN_RE.findall(normalized) if len(token) >= 2}


def shared_entity_count(left, right):
    return len(ascii_entities(left) & ascii_entities(right))


def is_similar_hit(similarity, shared_count):
    if similarity >= constants.SIMILARITY_THRESHOLD:
        return True
    return (similarity >= constants.SIMILARITY_TOPIC_THRESHOLD
            and shared_count >= constants.SIMILARITY_MIN_SHARED_TOKENS)


def similarity_hit(new_content, candidate_content):
    """(similarity, shared_entities sorted, is_hit) — 供单测/标定直接使用。"""
    similarity = text_similarity(new_content, candidate_content)
    shared = sorted(ascii_entities(new_content) & ascii_entities(candidate_content))
    return similarity, shared, is_similar_hit(similarity, len(shared))


def find_similar(model, category_key, new_content):
    """同类别当前有效事实中命中相似规则的候选（最多 5，sim 降序 / fact_id 升序）。"""
    hits = []
    for line in model.ordered_lines(category_key):
        head = model.head_fact(line["line_id"])
        if head is None:
            continue
        similarity = text_similarity(new_content, head["content"])
        shared = sorted(ascii_entities(new_content) & ascii_entities(head["content"]))
        if not is_similar_hit(similarity, len(shared)):
            continue
        hits.append({
            "fact_id": head["fact_id"],
            "content": head["content"],
            "similarity": similarity,
            "shared_entities": shared,
        })
    hits.sort(key=lambda hit: (-hit["similarity"], hit["fact_id"]))
    return hits[:MAX_CANDIDATES]


# ---------------------------------------------------------------- 确认令牌
def _token_payload(operation, category_key, content, candidate_ids):
    return {
        "v": 1,
        "operation": operation,
        "category_key": category_key,
        "content": content,
        "candidates": sorted(candidate_ids),
        "algorithm": constants.SIMILARITY_TOKEN_ALGORITHM,
        "high_threshold": constants.SIMILARITY_THRESHOLD,
        "topic_threshold": constants.SIMILARITY_TOPIC_THRESHOLD,
        "min_shared_tokens": constants.SIMILARITY_MIN_SHARED_TOKENS,
    }


def canonical_payload_bytes(operation, category_key, content, candidate_ids):
    return json.dumps(_token_payload(operation, category_key, content, candidate_ids),
                      sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def confirm_token(operation, category_key, content, candidate_ids):
    """stateless 确认令牌：base64url(sha256(payload)[:16])，22 字符。"""
    digest = hashlib.sha256(
        canonical_payload_bytes(operation, category_key, content, candidate_ids)).digest()
    return base64.urlsafe_b64encode(digest[:16]).decode("ascii").rstrip("=")


def token_sha256(operation, category_key, content, candidate_ids):
    """审计用的完整内容哈希（sha256 十六进制，64 字符）。"""
    return hashlib.sha256(
        canonical_payload_bytes(operation, category_key, content, candidate_ids)).hexdigest()
