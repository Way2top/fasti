"""C-11 ADR-036：候选会话覆盖统计（本地只读，零 LLM）。"""

import math
import os

from . import session_source


def compute_coverage(dsh_home, allow_plain, watermarks, max_sessions_per_run,
                     session_cwd=None):
    """→ coverage dict；total 仅计有 >=1 条用户消息的根会话。"""
    paths = session_source.discover_sessions(dsh_home, allow_plain=allow_plain)
    marks = (watermarks or {}).get("sessions") or {}
    total = scanned = unreadable = no_new = 0
    for path in paths:
        try:
            session = session_source.load_session(path, allow_plain=allow_plain)
        except Exception:  # noqa: BLE001 - 覆盖统计不因坏文件中断
            unreadable += 1
            continue
        if session is None:
            continue
        if session_cwd and session.get("cwd") != session_cwd:
            continue
        messages = session.get("messages") or []
        if not messages:
            continue
        total += 1
        watermark = marks.get(session["session_id"]) or {}
        last_seq = int(watermark.get("last_seq") or 0)
        max_seq = max(message["seq"] for message in messages)
        if max_seq <= last_seq:
            scanned += 1
        elif watermark:
            no_new += 1
    unscanned = max(0, total - scanned)
    per_run = max(1, int(max_sessions_per_run or 10))
    return {
        "total_candidate_sessions": total,
        "sessions_attempted": unscanned,
        "sessions_scanned_this_run": 0,
        "unscanned_sessions": unscanned,
        "estimated_runs_remaining": int(math.ceil(unscanned / float(per_run))),
        "unreadable_sessions": unreadable,
        "no_new_messages_sessions": no_new,
    }
