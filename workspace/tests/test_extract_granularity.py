"""C-10 M2：抽取 prompt 收紧（noise/粒度）与候选字段运行的回归测试。"""

import json
import unittest

from test_extract_run import ExtractRuntimeBase, _write_session, SESSION_ID
from fasti.core import api, extract_state


class TestGranularityRuntime(ExtractRuntimeBase):
    def test_granularity_drop_reasons_and_prompt_version(self):
        """M2：summary 缺 subject/非法粒度 ⇒ 丢弃+原因；atomic 保留并记录 prompt_version。"""
        _write_session(self.dsh_home, ["我喜欢猫"])
        self._enable(auto_write=False)
        extraction = {"candidates": [
            {"content": "缺 subject 的项目进展", "category_key": "tech",
             "granularity": "summary", "time_hint": None,
             "extraction_confidence": 0.9, "evidence": {"message_ids": ["m1"]}},
            {"content": "非法粒度", "category_key": "tech",
             "granularity": "project", "time_hint": None,
             "extraction_confidence": 0.9, "evidence": {"message_ids": ["m1"]}},
            {"content": "我喜欢猫", "category_key": "tech",
             "granularity": "atomic", "subject": None, "time_hint": None,
             "extraction_confidence": 0.9, "evidence": {"message_ids": ["m1"]}},
        ], "notes": ""}
        candidate_id = extract_state.candidate_id_for(
            SESSION_ID, ["m1"], "我喜欢猫", "tech", "new")
        judgments = {"judgments": [self._judgment(candidate_id, "new", 0.9)],
                     "notes": ""}
        run, mock = self._run(extraction, judgments)
        self.assertEqual(run["counters"]["candidates_created"], 1)
        self.assertEqual(run["counters"]["drop_reasons"]["missing_subject"], 1)
        self.assertEqual(run["counters"]["drop_reasons"]["invalid_granularity"], 1)
        self.assertEqual(run["llm"]["prompt_version"], "c10-extract-v2")
        candidate = api.list_extract_candidates(self.data_dir, "pending")["items"][0]
        self.assertEqual(candidate["granularity"], "atomic")
        self.assertEqual(candidate["prompt_version"], "c10-extract-v2")
        # prompt 含六类 noise，仍不含库数据
        extract_request = [item for item in mock.requests
                           if "可用类别" in item["messages"][0]["content"]][0]
        sent = json.dumps(extract_request, ensure_ascii=False)
        self.assertIn("禁止抽取的六类噪音", sent)
        self.assertNotIn("fact_id", sent)


if __name__ == "__main__":
    unittest.main()
