"""前端 JS ↔ api-spec 字段名契约测试（防「网页下拉 undefined」类 bug 复发）。

背景（用户实测阻断 bug）：`api-spec.yaml` 的 `Category` 对象身份字段是 `key`，
但 `app.js` 曾把类别对象写成 `item.category_key`（类别对象无此字段 ⇒ `undefined`），
导致 `POST /api/facts` 报 `NOT_FOUND_CATEGORY 类别不存在：'undefined'`。

为让守卫在**公共仓库**（`artifacts/` 被 gitignore、不存在）里也不静默失效：
- 字段契约快照固化在 `tests/fixtures/api_contract_fields.json`（随仓库提交，fixture 为准）；
- 若 `artifacts/api-spec.yaml` 存在，额外做一次**交叉校验**：fixture 必须与真实 spec 一致，否则 fail；
- 两者都不可用只可能是 fixture 丢失 ⇒ 显式 fail（不 skip、不静默通过）。

覆盖的前端对象类型：Category、Fact、StatusReport（counts/freshness/host_access）、
RenderResult、RenderArtifactItem、RecomputeResult、ResponseEnvelope、Error。
"""

import json
import os
import re
import unittest

from _base import ROOT

FIXTURE_PATH = os.path.join(ROOT, "tests", "fixtures", "api_contract_fields.json")
SPEC_PATH = os.path.join(ROOT, "..", "artifacts", "api-spec.yaml")
APP_JS = os.path.join(ROOT, "fasti", "adapters", "web_static", "app.js")

# 扫描的别名：只覆盖「响应/领域对象」；form.* / body.* 是 DOM/请求体构造，不在此列
SCAN_ALIASES = ("item", "category", "fact", "counts", "freshness", "entry",
                "payload", "err", "data", "candidate", "details")

# 别名 → fixture 里的对象类型
DEFAULT_OBJECT = {
    "item": "Category",                      # 类别循环里的 item（渲染产物列表另见白名单）
    "category": "Category",
    "fact": "Fact",
    "counts": "StatusReportCounts",
    "freshness": "StatusReportFreshness",
    "entry": "StatusReportHostAccessItem",   # data.host_access.map(function (entry) {...})
    "payload": "ResponseEnvelope",           # fetch 响应封套 / 本地请求体（后者见白名单）
    "err": "Error",
    "candidate": "SimilarityCandidate",   # C-08 409 details.candidates[i]
    "details": "ConflictDetails",         # C-08 409 error.details
    # "data"：端点返回的 data 包装层，字段逐个白名单（见下），未登记即 fail
}

# 显式白名单：(别名, 字段) → (fixture 对象名或 None, 理由)
# 白名单必须逐条写明理由；登记了对象名的还会断言该字段存在于对应 fixture 对象。
WHITELIST = {
    ("category", "value"):
        (None, "form.category 是 HTMLFormElement 的具名表单控件（DOM 元素），不是 Category 对象"),
    ("item", "path"):
        ("RenderArtifactItem", "渲染产物列表项 {path,bytes,sha256}（/api/render data.artifacts[i]），不是 Category"),
    ("item", "bytes"):
        ("RenderArtifactItem", "渲染产物列表项 {path,bytes,sha256}（/api/render data.artifacts[i]），不是 Category"),
    ("payload", "migrate_to"):
        (None, "本地请求体 payload（deleteCategory 的 {handling, migrate_to}），不是 HTTP 响应封套"),
    ("data", "artifacts"):
        ("RenderResult", "render/recompute 返回 data.artifacts（RenderResult）"),
    ("data", "host_body_bytes"):
        ("RenderResult", "render 返回 data.host_body_bytes（RenderResult）"),
    ("data", "host_pending"):
        ("RecomputeResult", "recompute 返回 data.host_pending（RecomputeResult）"),
    ("data", "warnings"):
        ("StatusReport", "status/render 返回 data.warnings（StatusReport / RenderResult 同名字段）"),
    ("data", "counts"):
        ("StatusReport", "status 返回 data.counts（StatusReport）"),
    ("data", "freshness"):
        ("StatusReport", "status 返回 data.freshness（StatusReport）"),
    ("data", "host_access"):
        ("StatusReport", "status 返回 data.host_access（StatusReport）"),
    ("data", "category"):
        (None, "类别写操作返回 data.category（Category 对象；其字段由 item./category. 扫描覆盖）"),
    ("data", "fact"):
        (None, "事实写操作返回 data.fact（Fact 对象；其字段由 fact. 扫描覆盖）"),
    ("data", "auto_bumped"):
        (None, "fact.add / fact.change 返回 data.auto_bumped（本工具响应扩展字段，非领域对象）"),
    ("data", "change_type"):
        (None, "fact.change 返回 data.change_type（FactChangeType）"),
    ("data", "text"):
        (None, "web 端点 data.text（核心层渲染文本，web_adapter 契约）"),
    ("data", "items"):
        (None, "列表端点 data.items（Category[] / Fact[]；元素字段由 item./fact. 扫描覆盖）"),
}


# --------------------------------------------------------------- YAML 子集解析
# 不引入 PyYAML：api-spec.yaml 是固定缩进风格，这里只解析需要的 properties/required 结构。
def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _child_block(lines, index):
    """返回 lines[index] 的子块（缩进严格更大的连续行）。"""
    base = _indent(lines[index])
    block = []
    for line in lines[index + 1:]:
        if not line.strip():
            block.append(line)
            continue
        if _indent(line) <= base:
            break
        block.append(line)
    return block


def _find_key(lines, key):
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == key + ":" or stripped.startswith(key + ": "):
            return index
    return None


def _find_block(lines, key):
    index = _find_key(lines, key)
    return None if index is None else _child_block(lines, index)


def _direct_keys(lines):
    """当前块中最小缩进层的键名集合（用于 json_shape / properties）。"""
    indents = [_indent(line) for line in lines if line.strip()]
    if not indents:
        return set()
    base = min(indents)
    keys = set()
    for line in lines:
        if not line.strip() or _indent(line) != base:
            continue
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", line)
        if match:
            keys.add(match.group(1))
    return keys


def _properties_under(lines, path=()):
    """按 key 路径下钻后取 properties 的直接字段名集合。"""
    block = lines
    for key in path:
        block = _find_block(block, key)
        if block is None:
            raise AssertionError("api-spec.yaml 中找不到路径 %r" % (path,))
    properties = _find_block(block, "properties")
    if properties is None:
        raise AssertionError("api-spec.yaml 路径 %r 下没有 properties" % (path,))
    return _direct_keys(properties)


def _def_block(lines, def_name):
    header = "    %s:" % def_name
    for index, line in enumerate(lines):
        if line == header:
            return _child_block(lines, index)
    raise AssertionError("api-spec.yaml 缺少 domain_model.$defs.%s" % def_name)


def _command_block(lines, command_id):
    pattern = re.compile(r"^\s{2}- id: %s\s*$" % re.escape(command_id))
    for index, line in enumerate(lines):
        if pattern.match(line):
            return _child_block(lines, index)
    raise AssertionError("api-spec.yaml 缺少 core_api 命令 %s" % command_id)


def _error_envelope_shape(lines):
    block = _find_block(lines, "error_envelope")
    if block is None:
        raise AssertionError("api-spec.yaml 缺少 conventions.error_envelope")
    shape = _find_block(block, "json_shape")
    return _direct_keys(shape)


def _spec_expected_fields(spec_text):
    """返回 {fixture 对象名: (spec 字段集合, 比较模式)}；模式 eq=完全相等，subset=fixture 为子集。"""
    lines = spec_text.split("\n")
    render_data = ("output_json", "properties", "data")
    expected = {
        "Category": (_properties_under(_def_block(lines, "Category")), "eq"),
        "Fact": (_properties_under(_def_block(lines, "Fact")), "eq"),
        "StatusReport": (_properties_under(_def_block(lines, "StatusReport")), "subset"),
        "StatusReportCounts": (_properties_under(_def_block(lines, "StatusReport"), ("counts",)), "eq"),
        "StatusReportFreshness": (_properties_under(_def_block(lines, "StatusReport"), ("freshness",)), "eq"),
        "StatusReportHostAccessItem": (
            _properties_under(_def_block(lines, "StatusReport"), ("host_access", "items")), "eq"),
        "RenderResult": (_properties_under(_command_block(lines, "render"), render_data), "eq"),
        "RenderArtifactItem": (
            _properties_under(_command_block(lines, "render"), render_data + ("artifacts", "items")),
            "eq"),
        "RecomputeResult": (_properties_under(_command_block(lines, "recompute"), render_data), "eq"),
    }
    shape = _error_envelope_shape(lines)
    error_fields = _direct_keys(_find_block(_find_block(lines, "error_envelope"), "error"))
    expected["ResponseEnvelope"] = (shape | {"data"}, "eq")
    expected["Error"] = (error_fields, "eq")
    return expected


# ------------------------------------------------------------------- 测试
class TestWebStaticFieldContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FIXTURE_PATH):
            raise AssertionError(
                "缺少字段契约快照 %s：它是公共仓库里守卫前端字段契约的唯一来源" % FIXTURE_PATH)
        with open(FIXTURE_PATH, encoding="utf-8") as handle:
            fixture = json.load(handle)
        cls.fixture = fixture
        cls.objects = fixture["objects"]

    def _app_js(self):
        with open(APP_JS, encoding="utf-8") as handle:
            return handle.read()

    # --- fixture 自身 -----------------------------------------------------
    def test_fixture_schema_is_complete(self):
        required = {"Category", "Fact", "StatusReport", "StatusReportCounts",
                    "StatusReportFreshness", "StatusReportHostAccessItem",
                    "RenderResult", "RenderArtifactItem", "RecomputeResult",
                    "ResponseEnvelope", "Error", "SimilarityCandidate",
                    "ConflictDetails"}
        missing = sorted(required - set(self.objects))
        if missing:
            self.fail("fixture 缺少对象类型：%s" % missing)
        for name, fields in self.objects.items():
            if not isinstance(fields, list) or not fields:
                self.fail("fixture 对象 %s 的字段列表为空" % name)
        self.assertIn("key", self.objects["Category"])
        self.assertNotIn("category_key", self.objects["Category"])
        self.assertIn("category_key", self.objects["Fact"])

    # --- fixture ↔ artifacts/api-spec 交叉校验 ----------------------------
    def test_fixture_matches_api_spec(self):
        if not os.path.isfile(SPEC_PATH):
            # 公共仓库（artifacts/ 被 gitignore）：交叉校验不可用，但 fixture 仍生效，
            # 这里打印显式说明并继续做 fixture 自洽断言（**不 skip、不静默通过**）。
            print("note: artifacts/api-spec.yaml 不存在；使用仓库内 fixture 契约快照，"
                  "交叉校验不可用（守卫仍生效）")
            self.assertIn("Category", self.objects)
            return
        with open(SPEC_PATH, encoding="utf-8") as handle:
            expected = _spec_expected_fields(handle.read())
        for name, (spec_fields, mode) in expected.items():
            fixture_fields = set(self.objects.get(name, []))
            if mode == "eq":
                if fixture_fields != spec_fields:
                    self.fail("fixture 与 api-spec 不一致（%s）：fixture-only=%s spec-only=%s"
                              % (name, sorted(fixture_fields - spec_fields),
                                 sorted(spec_fields - fixture_fields)))
            else:  # subset：fixture 只能少于 spec（前端只消费子集），多出即漂移
                extra = fixture_fields - spec_fields
                if extra:
                    self.fail("fixture 含 api-spec 中不存在的字段（%s）：%s"
                              % (name, sorted(extra)))

    # --- app.js 字段访问 ⊆ fixture 契约 -----------------------------------
    def test_app_js_object_fields_match_contract(self):
        text = self._app_js()
        pattern = re.compile(r"\b(%s)\.([A-Za-z_][A-Za-z0-9_]*)" % "|".join(SCAN_ALIASES))
        accesses = pattern.findall(text)
        self.assertTrue(accesses, "未在 app.js 中扫描到任何目标对象字段访问")

        violations = []
        for alias, field in accesses:
            key = (alias, field)
            if key in WHITELIST:
                object_name, _reason = WHITELIST[key]
                if object_name is not None and field not in self.objects[object_name]:
                    violations.append("%s.%s（白名单映射到 %s，但该字段不在 fixture）"
                                      % (alias, field, object_name))
                continue
            object_name = DEFAULT_OBJECT.get(alias)
            if object_name is None:
                violations.append("%s.%s（未登记的 data 字段：请在 WHITELIST 里写明理由）"
                                  % (alias, field))
            elif field not in self.objects[object_name]:
                violations.append("%s.%s（不在 fixture %s 字段中）"
                                  % (alias, field, object_name))
        if violations:
            reasons = "\n".join("  - %s.%s：%s" % (key[0], key[1], value[1])
                                for key, value in sorted(WHITELIST.items()))
            self.fail("app.js 字段访问与契约快照不一致：\n  " + "\n  ".join(sorted(set(violations)))
                      + "\n允许的白名单（逐条理由）：\n" + reasons)

    # --- 回归锁定 ---------------------------------------------------------
    def test_category_identity_is_key_in_app_js(self):
        text = self._app_js()
        if "item.category_key" in text:
            self.fail("app.js 不得访问 item.category_key（类别对象身份字段是 key，"
                      "否则下拉 value=undefined）；应改为 item.key")
        if "item.key" not in text:
            self.fail("app.js 未找到 item.key：类别对象应使用 key（fixture Category）")
        if "fact.category_key" not in text:
            self.fail("app.js 未找到 fact.category_key：事实对象应使用 category_key（fixture Fact）")


if __name__ == "__main__":
    unittest.main()
