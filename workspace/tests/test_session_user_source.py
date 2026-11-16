"""修复：DSH 注入的 user/message（source.kind != user）不得计入用户消息。"""

import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import session_source


def _write(dsh_home, events, name="s1"):
    path = os.path.join(dsh_home, "sessions", "ws", name, "session.v4.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def _header():
    return {"type": "session", "data": {"version": 4, "delegationDepth": 0,
                                        "isSeeded": False, "session_id": "s1"}}


def _msg(seq, message_id, text, kind="user"):
    data = {"id": message_id, "role": "user",
            "content": [{"type": "text", "text": text}]}
    if kind is not None:
        data["source"] = {"kind": kind}
    return {"seq": seq, "type": "user/message", "data": data}


class TestUserSourceFilter(BaseStoreTest):
    def test_only_user_kind_counted(self):
        events = [_header(),
                  _msg(1, "m1", "真正的第一条"),
                  _msg(2, "x1", "<system-reminder> 环境注入", "runtime-context"),
                  _msg(3, "x2", "技能清单", "skill-catalog"),
                  _msg(4, "x3", "2026-11-15 时间戳", "time-context"),
                  _msg(5, "x4", "子 agent 完成", "subagent-settled"),
                  _msg(6, "x5", "无来源旧消息", None),
                  _msg(7, "m2", "真正的第二条")]
        session = session_source.load_session(_write(self.tmp, events), allow_plain=True)
        self.assertEqual([message["message_id"] for message in session["messages"]],
                         ["m1", "m2"])
        divert = session["divert_input"]
        self.assertEqual(divert["user_message_count"], 2)
        self.assertEqual(divert["first_user_message"], "真正的第一条")
        counts = divert["user_message_source_counts"]
        self.assertEqual(counts["user"], 2)
        self.assertEqual(counts["runtime-context"], 1)
        self.assertEqual(counts["<missing>"], 1)

    def test_first_message_skips_injected_lookalike(self):
        events = [_header(),
                  _msg(1, "x1", "<system-reminder> 伪装注入"),
                  _msg(2, "x2", "Current runtime context: cwd=/tmp"),
                  _msg(3, "m1", "真正的用户消息")]
        session = session_source.load_session(_write(self.tmp, events), allow_plain=True)
        self.assertEqual(session["divert_input"]["first_user_message"], "真正的用户消息")
        self.assertEqual(session["divert_input"]["user_message_count"], 3)

    def test_old_session_without_source_excluded(self):
        events = [_header(), _msg(1, "m1", "旧格式无 source", None)]
        session = session_source.load_session(_write(self.tmp, events), allow_plain=True)
        self.assertEqual(session["messages"], [])
        self.assertEqual(session["divert_input"]["user_message_count"], 0)
        self.assertEqual(session["divert_input"]["user_message_source_counts"],
                         {"<missing>": 1})


if __name__ == "__main__":
    unittest.main()
