"""C-10 第 0 层：会话分流（ADR-032，M1 范围）。

输入仅会话标题/标题来源/首条用户消息/消息数；不接触数据库；
输出严格 `{"decision":"extract"|"skip","reason":"<=120字"}`；
失败由调用方走降级（保守 skip、不推进水位、下次重试）。
"""

import json

from . import constants
from . import errors
from . import extract_llm
from . import extract_privacy

SYSTEM_DIVERT = (
    "你是「会话分流器」。判断给定会话是否值得抽取长期个人画像事实。"
    "你只看到标题、首条用户消息与用户消息数，不接触任何记忆库，不要臆造事实。\n\n"
    "【值得提取 extract】\n"
    "- 用户本人的身份、偏好、经历、目标、当前状态；\n"
    "- 用户正在做/关注的事项，**包括项目与工作进展**（例如「我在做 Fasti 项目，P0 完成了」"
    "会产出 1 条项目 summary）；\n"
    "- 用户的学习、兴趣、生活动态。\n\n"
    "【不值得提取 skip】\n"
    "- 纯技术排障问答（报错怎么修、依赖/配置怎么调）；\n"
    "- 命令/语法/API 用法查询；\n"
    "- 纯代码审查、文件操作、工具执行流水；\n"
    "- 子 agent 角色就绪确认、任务分配；\n"
    "- 无任何「用户是谁/在做什么」信息的会话。\n\n"
    "关键区分：\n"
    "❌ 「pnpm build 报错怎么修？」 ⇒ skip（纯排障，无用户信息）\n"
    "✅ 「我最近在做 Fasti 这个项目，P0 做完了」 ⇒ extract（项目进展，产出 1 条 summary）\n\n"
    "输出严格 JSON：{\"decision\":\"extract\"或\"skip\",\"reason\":\"<=120字\"}。"
)


def build_divert_messages(divert_input, sensitive_filter=True):
    """只发标题/标题来源/首条消息/消息数（打码后）；严禁完整会话/id/路径/库数据。"""
    title = divert_input.get("title")
    if title:
        title, _kinds = extract_privacy.redact_text(title, sensitive_filter)
    first, _kinds = extract_privacy.redact_text(
        divert_input.get("first_user_message") or "", sensitive_filter)
    payload = {
        "title": title,
        "title_source": divert_input.get("title_source"),
        "first_user_message": first,
        "user_message_count": int(divert_input.get("user_message_count") or 0),
    }
    return [
        {"role": "system", "content": SYSTEM_DIVERT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def validate_divert_result(raw):
    """严格校验：结构/枚举/reason 非法 ⇒ EXTRACT_LLM_INVALID_RESPONSE（调用方降级）。"""
    try:
        data = extract_llm.parse_json_object(raw)
    except ValueError:
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "分流结果不是合法 JSON（保守跳过；下次重试）")
    if not isinstance(data, dict) or set(data) != {"decision", "reason"}:
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "分流结果字段不合法（保守跳过；下次重试）")
    if data["decision"] not in ("extract", "skip"):
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "分流 decision 非法（保守跳过；下次重试）")
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise errors.make("EXTRACT_LLM_INVALID_RESPONSE",
                          "分流 reason 为空（保守跳过；下次重试）")
    return {"decision": data["decision"], "reason": reason[:120]}


def divert_session(cfg, divert_input, sensitive_filter=True):
    """→ {decision, reason, prompt_version}；失败抛 EXTRACT_LLM_*。"""
    messages = build_divert_messages(divert_input, sensitive_filter=sensitive_filter)
    content = extract_llm.chat_completion(
        cfg, messages, max_tokens=constants.EXTRACT_DIVERT_MAX_TOKENS,
        disable_thinking=constants.EXTRACT_DIVERT_DISABLE_THINKING)
    result = validate_divert_result(content)
    result["prompt_version"] = constants.EXTRACT_PROMPT_DIVERT
    return result
