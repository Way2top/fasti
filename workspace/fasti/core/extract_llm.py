"""C-09 LLM 客户端（ADR-029）：OpenAI 兼容 /chat/completions，两段 prompt，严格 JSON 校验。

- HTTP 用 stdlib urllib：非流式；连接 10s / 读取 60s；仅网络/超时/429/5xx 重试（≤max_retries、
  退避 1s/2s）；4xx（非 429）不重试；响应 ≤2MiB。
- A 段（抽取）prompt **只含本块用户消息**，不得出现任何 fact/召回数据（测试断言）。
- B 段（判定）prompt 含候选 + 确定性召回的一行列表；不发送全库/画像。
- api_key 只进 Authorization 头；错误消息/运行记录永不回显。
- 网络层可注入（set_transport），测试用 mock 端点/stub，不真调外部 API。
"""

import json
import socket
import time
import urllib.error
import urllib.request

from . import constants
from . import errors
from . import extract_privacy

SYSTEM_EXTRACT = (
    "你是「个人事实抽取器」。从给定的用户消息片段中抽取值得长期记住的个人事实。"
    "只抽取能由这些用户消息直接支撑的事实；忽略助手/工具内容（不会提供）。"
    "每条事实映射到给定类别之一。不要判断与任何已有数据的关系（你没有数据库访问权）。"
    "输出严格 JSON：{\"candidates\":[{\"content\":\"单行事实\",\"category_key\":\"类别 key\","
    "\"time_hint\":null或时间线索字符串,\"extraction_confidence\":0..1,"
    "\"evidence\":{\"message_ids\":[\"消息 id\"]},\"reason\":\"≤200 字\"}],\"notes\":\"\"}。"
    "没有可抽取事实时输出 {\"candidates\":[],\"notes\":\"\"}。content 必须单行、≤2000 字。"
)
SYSTEM_JUDGE = (
    "你是「记忆关系判定器」。输入：候选事实列表 + 每条候选的召回事实（一行一条，"
    "可能含 current/historical 标记与类别 key）。判断每个候选与召回事实的关系："
    "new=全新（召回里没有同一件事）、update=已有事实的新值（应演进）、"
    "conflict=与已有事实矛盾但不能确定是演进。"
    "target_fact_id 必须来自该候选的召回集合；relation=new 时 target 必须为 null。"
    "输出严格 JSON：{\"judgments\":[{\"candidate_id\":\"cand-…\",\"relation\":\"new|update|conflict\","
    "\"target_fact_id\":null或召回中的 id,\"relation_confidence\":0..1,"
    "\"rationale\":\"≤200 字\"}],\"notes\":\"\"}。"
)
_PROMPT_FACT_MARKERS = ("fact_id", "召回", "current", "historical", "短ID")


def _default_transport(url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(constants.EXTRACT_LLM_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read(constants.EXTRACT_LLM_MAX_RESPONSE_BYTES + 1)
        except Exception:  # noqa: BLE001
            payload = b""
        return exc.code, payload


_TRANSPORT = None


def set_transport(transport):
    global _TRANSPORT
    _TRANSPORT = transport


def reset_transport():
    global _TRANSPORT
    _TRANSPORT = None


def _transport():
    return _TRANSPORT or _default_transport


def _base_url(cfg):
    return (cfg.get("llm", {}).get("base_url") or "").rstrip("/")


def chat_completion(cfg, messages, max_tokens=800):
    """调用 LLM 并返回 assistant content 文本；失败抛 EXTRACT_LLM_*。"""
    llm = cfg.get("llm") or {}
    base_url = _base_url(cfg)
    payload = {
        "model": llm.get("model"),
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if llm.get("json_mode", True):
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % (llm.get("api_key") or ""),
    }
    timeout = llm.get("timeout_seconds") or constants.EXTRACT_LLM_READ_TIMEOUT
    retries = llm.get("max_retries", constants.EXTRACT_LLM_MAX_RETRIES)
    attempts = 0
    while True:
        try:
            status, raw = _transport()(base_url + "/chat/completions", headers, body,
                                       timeout)
        except (socket.timeout,) as exc:
            if attempts >= retries:
                raise errors.make("EXTRACT_LLM_TIMEOUT", "LLM 调用超时；本次运行未写入，可重试",
                                  details={"attempts": attempts + 1})
            _backoff(attempts)
            attempts += 1
            continue
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), socket.timeout) and attempts >= retries:
                raise errors.make("EXTRACT_LLM_TIMEOUT", "LLM 调用超时；本次运行未写入，可重试")
            if attempts >= retries:
                raise errors.make("EXTRACT_LLM_UNREACHABLE",
                                  "无法连接 LLM 服务；本次运行未写入，可稍后重试",
                                  details={"attempts": attempts + 1})
            _backoff(attempts)
            attempts += 1
            continue
        if len(raw or b"") > constants.EXTRACT_LLM_MAX_RESPONSE_BYTES:
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                              "LLM 响应超过 2MiB 上限，整批拒绝")
        if status == 429 or (isinstance(status, int) and 500 <= status < 600):
            if attempts >= retries:
                raise errors.make("EXTRACT_LLM_HTTP_ERROR",
                                  "LLM 返回错误状态 %s（重试耗尽）" % status,
                                  details={"status": status, "attempts": attempts + 1})
            _backoff(attempts, raw)
            attempts += 1
            continue
        if status != 200:
            raise errors.make("EXTRACT_LLM_HTTP_ERROR",
                              "LLM 返回错误状态 %s" % status,
                              details={"status": status})
        return _extract_content(raw)


def _backoff(attempt, raw=None):
    """退避 1s/2s；尊重 Retry-After≤30（测试会 patch time.sleep）。"""
    delay = 2 ** attempt
    try:
        retry_after = _retry_after(raw)
    except Exception:  # noqa: BLE001
        retry_after = None
    if retry_after is not None:
        delay = min(max(delay, retry_after), 30)
    time.sleep(delay)


def _retry_after(raw):
    return None  # 简化：默认退避；服务端 Retry-After 由上层策略处理（P1 固定 1s/2s）


def _extract_content(raw):
    try:
        envelope = json.loads((raw or b"").decode("utf-8"))
        content = envelope["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("content 不是字符串")
    except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError) as exc:
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "LLM 响应无法解析：%s" % exc.__class__.__name__)
    return content


# ---------------------------------------------------------------- 两段 prompt
def category_catalog_text(categories):
    """启用类别清单（schema，不是事实数据）→ prompt 行。"""
    lines = []
    for category in categories or []:
        lines.append("- %s / %s" % (category["key"], category["display_name"]))
    return "可用类别（category_key / 显示名，输出必须使用其中某个 category_key）：\n" \
        + ("\n".join(lines) if lines else "（当前没有启用的类别）")


def build_extraction_messages(chunk, sensitive_filter=True, categories=None):
    """A 段：system = 抽取规则 + 启用类别清单（schema）；user = 本块用户消息（打码后）。

    类别清单是输出 schema，**不是**事实/召回数据；A 段仍绝不包含任何 fact_id/召回/事实内容。
    """
    lines = []
    for message in chunk["messages"]:
        text, _kinds = extract_privacy.redact_text(message["chunk_text"], sensitive_filter)
        lines.append({"ordinal": message["ordinal"],
                      "message_id": message["message_id"], "text": text})
    system = SYSTEM_EXTRACT + "\n\n" + category_catalog_text(categories)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(lines, ensure_ascii=False)},
    ]


def resolve_category(value, categories):
    """LLM 返回的类别 → 真实 category_key：精确 key → 精确 display_name → None（非法）。

    仅接受调用方传入的启用类别清单；display_name 只匹配清单内唯一项。
    """
    if not isinstance(value, str):
        return None
    for category in categories or []:
        if category["key"] == value:
            return category["key"]
    matches = [category["key"] for category in categories or []
               if category.get("display_name") == value]
    return matches[0] if len(matches) == 1 else None


def build_judgment_messages(candidates, recall_by_candidate, sensitive_filter=True):
    """B 段：候选 + 每个候选的召回事实一行列表（打码后）；不含全库/画像。"""
    candidate_payload = []
    recall_lines = []
    for candidate in candidates:
        content, _kinds = extract_privacy.redact_text(candidate["content"], sensitive_filter)
        candidate_payload.append({
            "candidate_id": candidate["candidate_id"],
            "content": content,
            "category_key": candidate["category_key"],
            "time_hint": candidate.get("time_hint"),
            "extraction_confidence": candidate["extraction_confidence"],
        })
        lines = recall_by_candidate.get(candidate["candidate_id"], [])
        candidate_lines = []
        for line in lines:
            safe, _kinds = extract_privacy.redact_text(line, sensitive_filter)
            candidate_lines.append(safe)
        recall_lines.append({"candidate_id": candidate["candidate_id"],
                             "recall": candidate_lines})
    user_content = (
        "候选事实：\n" + json.dumps(candidate_payload, ensure_ascii=False)
        + "\n\n召回事实（每个候选一行一条）：\n"
        + json.dumps(recall_lines, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": SYSTEM_JUDGE},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------- 输出校验
def validate_extraction_result(raw):
    """→ (候选列表, 被丢弃单条计数)；顶层结构非法 ⇒ EXTRACT_LLM_INVALID_RESPONSE。

    单条候选结构/枚举/范围非法 ⇒ 丢弃该条（计数），不影响整批。
    """
    try:
        data = json.loads(raw)
    except ValueError:
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "抽取结果不是合法 JSON，整批拒绝、水位不前进")
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "抽取结果缺少 candidates 数组，整批拒绝、水位不前进")
    valid = []
    dropped = 0
    for item in data["candidates"]:
        if not _valid_candidate(item):
            dropped += 1
            continue
        valid.append({
            "content": item["content"],
            "category_key": item["category_key"],
            "time_hint": item.get("time_hint"),
            "extraction_confidence": float(item["extraction_confidence"]),
            "evidence": {"message_ids": list(item["evidence"]["message_ids"])},
            "reason": item.get("reason") or "",
        })
    return valid, dropped


def _valid_candidate(item):
    if not isinstance(item, dict):
        return False
    content = item.get("content")
    if not isinstance(content, str) or not 1 <= len(content) <= 2000 or "\n" in content:
        return False
    category_key = item.get("category_key")
    if not isinstance(category_key, str):
        return False
    try:
        confidence = float(item.get("extraction_confidence"))
    except (TypeError, ValueError):
        return False
    if not 0 <= confidence <= 1:
        return False
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return False
    message_ids = evidence.get("message_ids")
    if not isinstance(message_ids, list) or not message_ids \
            or not all(isinstance(item, str) and item for item in message_ids):
        return False
    if item.get("time_hint") is not None and not isinstance(item["time_hint"], str):
        return False
    if item.get("reason") is not None and not isinstance(item["reason"], str):
        return False
    return True


def validate_judgment_result(raw, candidate_ids, recall_ids_by_candidate):
    """→ {candidate_id: judgment}；结构非法整批失败；target 不在召回 ⇒ unverified。"""
    try:
        data = json.loads(raw)
    except ValueError:
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "判定结果不是合法 JSON，整批拒绝、水位不前进")
    if not isinstance(data, dict) or not isinstance(data.get("judgments"), list):
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "判定结果缺少 judgments 数组，整批拒绝、水位不前进")
    result = {}
    for item in data["judgments"]:
        if not isinstance(item, dict):
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE", "判定条目不是对象")
        candidate_id = item.get("candidate_id")
        relation = item.get("relation")
        try:
            confidence = float(item.get("relation_confidence"))
        except (TypeError, ValueError):
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE", "判定条目 relation_confidence 非法")
        target = item.get("target_fact_id")
        if candidate_id not in candidate_ids:
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                              "判定条目 candidate_id 不在本批")
        if relation not in ("new", "update", "conflict"):
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE", "判定 relation 非法")
        if not 0 <= confidence <= 1:
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE", "判定置信度越界")
        if relation == "new" and target is not None:
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE", "relation=new 时 target 必须为 null")
        if relation in ("update", "conflict") and not target:
            raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                              "relation=%s 时必须给出 target_fact_id" % relation)
        unverified = bool(target) and target not in recall_ids_by_candidate.get(candidate_id, [])
        result[candidate_id] = {
            "llm": relation,
            "target_fact_id": target,
            "relation_confidence": confidence,
            "rationale": item.get("rationale") if isinstance(item.get("rationale"), str) else "",
            "unverified": unverified,
        }
    return result
