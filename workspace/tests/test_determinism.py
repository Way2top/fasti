"""AC-2-3/2-4/5-2/5-3：确定性折叠与渲染（v2 = UUIDv7 标识方案）。"""

import os
import shutil
import unittest

from _base import BaseStoreTest, _canonical_body, _sha
from fasti.core import api, fold, store


class TestDeterminism(BaseStoreTest):
    def test_fold_twice_identical(self):
        api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        events = store.read_events(self.data_dir)
        first = fold.fold_events(events)
        second = fold.fold_events(events)
        self.assertEqual(api.render_profile_body(first), api.render_profile_body(second))
        self.assertEqual(api.render_host_body(first), api.render_host_body(second))
        self.assertEqual(api.render_history_body(first), api.render_history_body(second))

    def test_recompute_idempotent_and_rederivable(self):
        api.add_fact(self.data_dir, "tech", "主力语言：Python", "2025-01")
        api.add_fact(self.data_dir, "projects", "当前项目：Fasti", "2026-11")
        api.recompute(self.data_dir)
        profile = os.path.join(self.data_dir, "profile")
        first = (_sha(os.path.join(profile, "current.md")),
                 _sha(os.path.join(profile, "history.md")))
        api.recompute(self.data_dir)
        second = (_sha(os.path.join(profile, "current.md")),
                  _sha(os.path.join(profile, "history.md")))
        self.assertEqual(first, second)
        shutil.rmtree(profile)  # AC-2-4：删除派生结果后重算逐字节还原
        api.recompute(self.data_dir)
        third = (_sha(os.path.join(profile, "current.md")),
                 _sha(os.path.join(profile, "history.md")))
        self.assertEqual(first, third)

    def test_render_idempotent_and_canonical_body_equal(self):
        api.add_fact(self.data_dir, "tech", "主力语言：C++", "2026-11")
        api.add_fact(self.data_dir, "communication", "回答风格：先结论后细节", "2026-11")
        api.render_hosts(self.data_dir, "all")
        dist = os.path.join(self.data_dir, "dist")
        files = [os.path.join(dist, "AGENTS.md"),
                 os.path.join(dist, "CLAUDE.md"),
                 os.path.join(dist, ".cursor", "rules", "fasti.mdc")]
        first = [_sha(path) for path in files]
        bodies = [_canonical_body(path) for path in files]
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[0], bodies[2])
        self.assertIn("- 主力语言：C++", bodies[0])
        api.render_hosts(self.data_dir, "all")
        self.assertEqual(first, [_sha(path) for path in files])

    def test_manual_edit_overwritten(self):
        api.add_fact(self.data_dir, "tech", "主力语言：C++", "2026-11")
        api.render_hosts(self.data_dir, "all")
        agents = os.path.join(self.data_dir, "dist", "AGENTS.md")
        with open(agents, "w", encoding="utf-8") as handle:
            handle.write("# 手工改坏\n")
        api.render_hosts(self.data_dir, "all")
        with open(agents, encoding="utf-8") as handle:
            self.assertIn("- 主力语言：C++", handle.read())


if __name__ == "__main__":
    unittest.main()
