"""前端 JS ↔ api-spec 字段名契约测试（防「网页下拉 undefined」类 bug 复发）。

背景（用户实测阻断 bug）：`api-spec.yaml` 的 `Category` 对象身份字段是 `key`，
但 `app.js` 曾把类别对象写成 `item.category_key`（类别对象无此字段 ⇒ `undefined`），
导致 `POST /api/facts` 报 `NOT_FOUND_CATEGORY 类别不存在：'undefined'`。
QA 只测了 API 层，因此需要一层「前端访问字段 ⊆ 契约 properties」的静态守卫。

实现：
1. 从 `artifacts/api-spec.yaml` 的 `domain_model.$defs.Category` / `$defs.Fact` 读取 properties 字段名
   （不引入 PyYAML：按 4/6/8 空格缩进解析该两个定义的 properties 段）；
2. 扫描 `web_static/app.js` 中形如 `<obj>.<field>` 的访问，obj ∈ {item, category, fact}；
3. 断言：类别对象访问的字段必须 ∈ Category.properties；事实对象访问的字段必须 ∈ Fact.properties；
4. 仅允许 3 条显式白名单（见 WHITELIST，逐条给理由）——白名单之外出现未知字段即 fail。

若 `artifacts/`（内部文档，gitignore）不可用则 skip 并标记（不静默通过）。
"""

import os
import re
import unittest

from _base import ROOT

SPEC_PATH = os.path.join(ROOT, "..", "artifacts", "api-spec.yaml")
APP_JS = os.path.join(ROOT, "fasti", "adapters", "web_static", "app.js")

# 显式白名单：(对象名, 字段名) → 理由
WHITELIST = {
    ("category", "value"):
        "form.category 是 HTMLFormElement 的具名表单控件（DOM 元素），不是 Category 对象",
    ("item", "path"):
        "渲染产物列表项 {path, bytes, sha256}（api/render 的 artifacts），不是 Category 对象",
    ("item", "bytes"):
        "渲染产物列表项 {path, bytes, sha256}（api/render 的 artifacts），不是 Category 对象",
}
WHITELIST_REASONS = "\n".join(
    "  - %s.%s：%s" % (obj, field, reason)
    for (obj, field), reason in sorted(WHITELIST.items())
)


def _parse_def_properties(text, def_name):
    """从 api-spec.yaml 文本中提取 $defs.<def_name>.properties 的字段名列表（缩进解析）。"""
    lines = text.split("\n")
    header = "    %s:" % def_name
    try:
        start = lines.index(header)
    except ValueError:
        raise AssertionError("api-spec.yaml 缺少 domain_model.$defs.%s 定义" % def_name)
    properties = []
    in_properties = False
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= 4:
            break  # 下一个 $defs 定义（4 空格缩进）
        if line.strip() == "properties:":
            in_properties = True
            continue
        if not in_properties or not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 6:
            break  # properties 段结束（如 example:）
        match = re.match(r"^\s{8}([A-Za-z_][A-Za-z0-9_]*):", line)
        if match:
            properties.append(match.group(1))
    if not properties:
        raise AssertionError("无法解析 %s.properties（api-spec.yaml 结构变化？）" % def_name)
    return set(properties)


class TestWebStaticFieldContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SPEC_PATH):
            raise unittest.SkipTest(
                "artifacts/api-spec.yaml 不可用（内部文档目录缺失）：跳过前端字段契约测试")
        with open(SPEC_PATH, encoding="utf-8") as handle:
            spec_text = handle.read()
        cls.category_properties = _parse_def_properties(spec_text, "Category")
        cls.fact_properties = _parse_def_properties(spec_text, "Fact")

    def _app_js(self):
        with open(APP_JS, encoding="utf-8") as handle:
            return handle.read()

    def test_parsed_spec_fields_are_sane(self):
        """解析结果自检：类别身份是 key（无 category_id），事实对象有 category_key。"""
        self.assertIn("key", self.category_properties)
        self.assertIn("display_name", self.category_properties)
        self.assertNotIn("category_id", self.category_properties)
        self.assertNotIn("category_key", self.category_properties)
        self.assertIn("category_key", self.fact_properties)
        self.assertIn("fact_id", self.fact_properties)

    def test_app_js_object_fields_match_contract(self):
        """app.js 中类别/事实对象的字段访问必须在 api-spec 的 properties 内。"""
        text = self._app_js()
        accesses = re.findall(r"\b(item|category|fact)\.([A-Za-z_][A-Za-z0-9_]*)", text)
        self.assertTrue(accesses, "未在 app.js 中扫描到任何 item./category./fact. 访问")

        allowed = {"item": self.category_properties, "category": self.category_properties,
                   "fact": self.fact_properties}
        violations = []
        for obj, field in accesses:
            if (obj, field) in WHITELIST:
                continue
            if field not in allowed[obj]:
                violations.append("%s.%s" % (obj, field))
        if violations:
            self.fail(
                "app.js 访问了 api-spec 契约中不存在的字段（对象/字段不在对应 $defs.properties）：\n"
                "  " + sorted(set(violations)).__str__() + "\n"
                "允许的白名单（仅以下 3 条）：\n" + WHITELIST_REASONS
            )

    def test_category_identity_is_key_in_app_js(self):
        """回归锁定：类别对象只能用 key；不得再出现 item.category_key。"""
        text = self._app_js()
        if "item.category_key" in text:
            self.fail("app.js 不得访问 item.category_key（类别对象身份字段是 key，"
                      "否则下拉 value=undefined）；应改为 item.key")
        if "item.key" not in text:
            self.fail("app.js 未找到 item.key：类别对象应使用 key（api-spec Category.properties）")
        # 事实对象的 category_key 是正确的（勿误改）
        if "fact.category_key" not in text:
            self.fail("app.js 未找到 fact.category_key：事实对象应使用 category_key（Fact.properties）")


if __name__ == "__main__":
    unittest.main()
