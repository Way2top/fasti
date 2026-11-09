"""C-08 相似检测算法（ADR-027 / §14.2-14.3）：归一化、两级规则、标定对子、令牌、候选范围。"""

import re
import unittest

from _base import BaseStoreTest
from fasti.core import api, errors, fold, similarity, store

REAL_A = "最熟悉的还是Python，但是工作所需，现在需要主力使用C++了。"
REAL_B_FULL = (
    "最熟悉的语言是Python，但是目前工作需要使用C++。Django和Flask都有一定了解，但不深入。"
    "数据库了解MySql，Redis了解但未使用过。AI相关，使用Langchain开发过一个GaoKaoAgent。")
REAL_B_TRUNCATED = (
    "最熟悉的语言是Python，但是目前工作需要使用C++。Django和Flask都有一定了解，但不深入。")

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


class TestSimilarityAlgorithm(unittest.TestCase):
    def test_real_duplicate_pair_tier2(self):
        """用户真实重复对（全量 B，含 MySql/Redis 句）：sim=0.307 + 共享 python/c++ ⇒ Tier2 命中。"""
        value, shared, hit = similarity.similarity_hit(REAL_A, REAL_B_FULL)
        self.assertEqual(value, 0.307)
        self.assertEqual(shared, ["c++", "python"])
        self.assertTrue(hit)
        # 截断版 B（Lead 实测 0.519）同样应命中
        truncated = similarity.similarity_hit(REAL_A, REAL_B_TRUNCATED)
        self.assertEqual(truncated[0], 0.519)
        self.assertTrue(truncated[2])

    def test_tier1_high_similarity(self):
        value, shared, hit = similarity.similarity_hit("主力语言：C++", "主力语言是C++")
        self.assertEqual(value, 0.851)
        self.assertTrue(hit)   # >= 0.60 ⇒ Tier1

    def test_single_entity_negative(self):
        """共享 1 个实体词：不满足 Tier2 门槛（架构标定负例）。"""
        value, shared, hit = similarity.similarity_hit("夯实C++基础", "学习C++设计模式")
        self.assertEqual(shared, ["c++"])
        self.assertFalse(hit)

    def test_zero_entity_negative(self):
        for left, right in (("C++", "Python"), ("C++", "Java"), ("MySQL", "Redis"),
                            ("C++", "数据库：了解 MySQL")):
            value, shared, hit = similarity.similarity_hit(left, right)
            self.assertFalse(hit, (left, right, value, shared))

    def test_normalization_nfkc_and_symbols(self):
        self.assertEqual(similarity.text_similarity("Ｃ＋＋", "C++"), 1.0)
        self.assertEqual(similarity.normalize_content("主力语言：C++！"), "主力语言c++")
        self.assertEqual(similarity.text_similarity("a", "ab"), 0.0)
        self.assertEqual(similarity.text_similarity("", "x"), 0.0)

    def test_entities_definition(self):
        entities = similarity.ascii_entities("Python C++ C# django MySQL fasti 中文")
        self.assertEqual(entities, {"python", "c++", "c#", "django", "mysql", "fasti"})

    def test_token_format_and_binding(self):
        token = similarity.confirm_token("fact.add", "tech", "主力语言是C++", ["b", "a"])
        self.assertRegex(token, TOKEN_RE)
        self.assertEqual(len(similarity.token_sha256("fact.add", "tech", "主力语言是C++",
                                                     ["b", "a"])), 64)
        # 候选顺序不影响（升序后再哈希）；内容/类别/候选变化 ⇒ 令牌变化
        self.assertEqual(token, similarity.confirm_token(
            "fact.add", "tech", "主力语言是C++", ["a", "b"]))
        self.assertNotEqual(token, similarity.confirm_token(
            "fact.add", "tech", "主力语言是Java", ["b", "a"]))
        self.assertNotEqual(token, similarity.confirm_token(
            "fact.add", "goals", "主力语言是C++", ["b", "a"]))
        self.assertNotEqual(token, similarity.confirm_token(
            "fact.add", "tech", "主力语言是C++", ["b"]))


class TestFindSimilarScope(BaseStoreTest):
    def _model(self):
        return fold.fold_events(store.read_events(self.data_dir))

    def _add(self, content, category="tech"):
        """新增（若命中相似冲突则自动带确认令牌重试）。"""
        try:
            return api.add_fact(self.data_dir, category, content, "2026-11")["fact"]
        except errors.CoreError as exc:
            if exc.code != "CONFLICT_SIMILAR_FACT":
                raise
            token = exc.details["confirm_token"]
            return api.add_fact(self.data_dir, category, content, "2026-11",
                                confirm_token=token)["fact"]

    def test_find_similar_scope_and_shape(self):
        first = api.add_fact(self.data_dir, "tech", "主力语言：C++", "2026-11")["fact"]
        goal = self._add("主力语言是C++", "goals")   # 跨类别不参与 tech 的候选
        tech_hits = similarity.find_similar(self._model(), "tech", "主力语言是C++")
        self.assertEqual(len(tech_hits), 1)
        self.assertEqual(tech_hits[0]["fact_id"], first["fact_id"])
        self.assertEqual(tech_hits[0]["similarity"], 0.851)
        self.assertIn("c++", tech_hits[0]["shared_entities"])
        goal_hits = similarity.find_similar(self._model(), "goals", "主力语言是C++")
        self.assertEqual([hit["fact_id"] for hit in goal_hits], [goal["fact_id"]])

    def test_find_similar_ignores_old_segments(self):
        created = api.add_fact(self.data_dir, "tech", "主力语言：C++", "2026-11")["fact"]
        api.change_fact(self.data_dir, created["fact_id"], "evolve", "数据库：了解 MySQL", "2026-12")
        # 旧段已不是 head ⇒ 与旧段相似的新增不触发
        self.assertEqual(similarity.find_similar(self._model(), "tech", "主力语言是C++"), [])

    def test_candidates_cap_and_sort(self):
        contents = ["主力语言：C++", "主力语言是C++", "主力语言：C++ 深入", "熟悉C++语言",
                    "工作使用C++", "C++开发"]
        for content in contents:
            self._add(content)
        hits = similarity.find_similar(self._model(), "tech", "主力语言：C++")
        self.assertLessEqual(len(hits), 5)
        values = [hit["similarity"] for hit in hits]
        self.assertEqual(values, sorted(values, reverse=True))


if __name__ == "__main__":
    unittest.main()
