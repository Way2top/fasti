"""C-09 敏感内容打码（ADR-029 / api-spec conventions.extract.privacy.sensitive_filter）。

默认开启；发送前与候选落盘前都打码；替换 `[REDACTED:<kind>]`。
候选含 `[REDACTED` 或仍命中 ⇒ 丢弃（sensitive_skipped）。
"""

import re

REPLACEMENT = "[REDACTED:%s]"

PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                               re.DOTALL)),
    ("sk", re.compile(r"sk-[A-Za-z0-9_-]{16,}")),
    ("github", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("bearer", re.compile(r"Bearer\s+\S+")),
    ("credential", re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*\S+")),
    ("base64hex", re.compile(r"(?:[A-Za-z0-9+/]{32,}={0,2}|[0-9a-fA-F]{32,})")),
]


def redact_text(text, enabled=True):
    """返回 (打码后文本, 命中 kind 列表)；enabled=False 时原样返回。"""
    if not text or not enabled:
        return text, []
    kinds = []
    result = text
    for kind, pattern in PATTERNS:
        if pattern.search(result):
            kinds.append(kind)
            result = pattern.sub(lambda match, k=kind: REPLACEMENT % k, result)
    return result, kinds


def contains_redaction(text):
    return bool(text) and "[REDACTED" in text


def is_sensitive(text, enabled=True):
    """候选落盘前的判定：仍命中敏感规则或含打码占位 ⇒ 视为敏感。"""
    if not text:
        return False
    if contains_redaction(text):
        return True
    if not enabled:
        return False
    return any(pattern.search(text) for _kind, pattern in PATTERNS)
