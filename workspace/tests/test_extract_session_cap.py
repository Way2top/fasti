"""C-10 M3：会话级 buffer / summary 去重 / 5 条上限 / 确定性。"""

import json
import os
import unittest

from _base import BaseStoreTest
from fasti.core import api, constants, extract_config, extract_llm, extract_state
from fasti.core.services import extract_sessionops as ops
from test_extract_run import ExtractRuntimeBase, _write_session, SESSION_ID


def _item(content, granularity="atomic", subject=None, confidence=0.9,
          ordinals=(1,)):
    return {"content": content, "category_key": "tech", "granularity": granularity,
            "subject": subject, "summary_subject_key": ops.summary_subject_key(subject),
            "time_hint": None, "extraction_confidence": confidence,
            "message_ids": ["m%d" % ordinals[0]],
            "message_ordinals": list(ordinals), "chunk_id": "chunk-1",
            "prompt_version": constants.EXTRACT_PROMPT_EXTRACTION}


class TestPostprocessSession(unittest.TestCase):
    def test_summary_dedup_keeps_highest_confidence(self):
        buffer = [
            _item("项目进展 A", "summary", "fasti", 0.6, (1,)),
            _item("项目进展 B", "summary", "fasti", 0.9, (2,)),
            _item("项目进展 C", "summary", "Fasti", 0.7, (3,)),
            _item("偏好：喜欢猫", "atomic", None, 0.9, (4,)),
        ]
        kept, stats = ops.postprocess_session(buffer)
        summaries = [item for item in kept if item["granularity"] == "summary"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["content"], "项目进展 B")   # 保留最高置信
        self.assertEqual(stats["dropped_duplicate_summary"], 2)
        self.assertEqual(stats["candidates_after_dedup"], 2)
        self.assertEqual(stats["candidates_kept"], 2)

    def test_cap_mixed_summary_atomic(self):
        buffer = [_item("summary", "summary", "p", 0.5)]
        buffer += [_item("atomic %02d" % i, confidence=0.9 - i * 0.01)
                   for i in range(8)]
        kept, stats = ops.postprocess_session(buffer)
        self.assertEqual(len(kept), constants.EXTRACT_SESSION_CANDIDATE_CAP)
        self.assertEqual(stats["dropped_by_session_cap"], 4)      # 9-5
        self.assertEqual(kept[0]["granularity"], "summary")        # summary 优先

    def test_single_chunk_no_false_truncation(self):
        buffer = [_item("atomic %d" % i) for i in range(5)]
        kept, stats = ops.postprocess_session(buffer)
        self.assertEqual(len(kept), 5)
        self.assertEqual(stats["dropped_by_session_cap"], 0)
        self.assertEqual(stats["dropped_duplicate_summary"], 0)

    def test_deterministic_selection(self):
        buffer = [_item("same subject", "summary", "p", 0.8, (1,)),
                  _item("same subject 2", "summary", "P", 0.8, (2,))]
        buffer += [_item("atomic %02d" % i, confidence=0.7, ordinals=(i + 3,))
                   for i in range(6)]
        first, stats_a = ops.postprocess_session(list(reversed(buffer)))
        second, stats_b = ops.postprocess_session(list(buffer))
        self.assertEqual([item["content"] for item in first],
                         [item["content"] for item in second])
        self.assertEqual(stats_a, stats_b)


class TestSessionBufferRuntime(ExtractRuntimeBase):
    def test_three_chunks_nine_candidates_truncated(self):
        messages = ["消息 %02d" % i for i in range(45)]
        _write_session(self.dsh_home, messages)
        self._enable(auto_write=False)

        class Transport(object):
            def __call__(inner, url, headers, body, timeout):
                payload = json.loads(body.decode("utf-8"))
                system = payload["messages"][0]["content"]
                if "分流" in system:
                    return 200, self._envelope({"decision": "extract",
                                                "reason": "项目进展"})
                if "可用类别" in system:
                    block = json.loads(payload["messages"][1]["content"])
                    message_id = block[0]["message_id"]
                    chunk = block[0]["ordinal"] // 20
                    candidates = [{
                        "content": "项目状态：第 %d 块进展" % chunk,
                        "category_key": "tech", "granularity": "summary",
                        "subject": "fasti", "time_hint": None,
                        "extraction_confidence": 0.70 + chunk * 0.01,
                        "evidence": {"message_ids": [message_id]}}]
                    for index in range(2):
                        candidates.append({
                            "content": "事实 块%d-%d" % (chunk, index),
                            "category_key": "tech", "granularity": "atomic",
                            "subject": None, "time_hint": None,
                            "extraction_confidence": 0.90,
                            "evidence": {"message_ids": [message_id]}})
                    return 200, self._envelope({"candidates": candidates})
                return 200, self._envelope({"judgments": []})
        extract_llm.set_transport(Transport())
        run = api.extract_run(self.data_dir, trigger="manual")
        counters = run["counters"]
        self.assertEqual(counters["candidates_extracted"], 9)
        self.assertEqual(counters["candidates_after_dedup"], 7)
        self.assertEqual(counters["candidates_kept"], 5)
        self.assertEqual(counters["dropped_duplicate_summary"], 2)
        self.assertEqual(counters["dropped_by_session_cap"], 2)
        self.assertEqual(counters["candidates_created"], 5)
        record = run["sessions"][0]
        self.assertEqual(record["candidates_extracted"], 9)
        self.assertEqual(record["candidates_after_dedup"], 7)
        self.assertEqual(record["candidates_kept"], 5)
        self.assertEqual(record["dropped_by_session_cap"], 2)
        self.assertEqual(record["candidates"]["extracted"], 9)
        self.assertEqual(record["candidates"]["kept"], 5)
        self.assertEqual(record["candidates"]["dropped_by_session_cap"], 2)
        items = api.list_extract_candidates(self.data_dir, "pending")["items"]
        self.assertEqual(len(items), 5)

    @staticmethod
    def _envelope(payload):
        return json.dumps({"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False)}}]}).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
