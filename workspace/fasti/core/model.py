"""读取模型（ReadModel）：事件折叠的产物，纯内存对象（v2 = UUIDv7 标识方案）。

设计约束（architecture.md §1.5 / ADR-002/008/023）：
- 本模块不读时钟、不读网络、不读文件、不读上一次派生结果；
- 所有派生字段（content / valid_to / latest_change / revision_count / segment_count）
  都在 fold 的 finalize 阶段一次性算出，之后只读；
- v2 标识：类别身份 = 语义 `category_key`；`line_id` / `fact_id` = UUIDv7；
  修订无独立 ID，以 `(fact_id, seq)` 复合定位。
"""

from . import clock


class ReadModel(object):
    """事件 → 当前状态。字段名与 api-spec domain_model（v2）一致。"""

    def __init__(self):
        self.events = []
        self.categories = {}        # category_key -> Category dict
        self.category_order = []    # 创建顺序（事件顺序；复活不重复追加）
        self.lines = {}             # line_id(UUIDv7) -> FactLine dict
        self.line_order = []        # 创建顺序
        self.facts = {}             # fact_id(UUIDv7) -> Fact dict
        self.fact_order = []        # 创建顺序
        self.revisions = {}         # fact_id -> [FactRevision dict]（按 seq 升序）

    # ------------------------------------------------------------------ 类别
    def category(self, category_key):
        return self.categories.get(category_key)

    def alive_categories(self):
        return [self.categories[key] for key in self.category_order
                if self.categories[key]["deleted_at"] is None]

    def ordered_categories(self, include_deleted=False):
        """排序规则（conventions.ordering.categories）：
        template 且未删除按 template_order 升序 → custom 且未删除按 (created_at, category_key)；
        未删除类别之后（include_deleted 时）追加已删除类别，按 (created_at, category_key)。
        """
        alive = self.alive_categories()
        templates = sorted(
            [category for category in alive if category["origin"] == "template"],
            key=lambda category: (category["template_order"], category["key"]),
        )
        customs = sorted(
            [category for category in alive if category["origin"] == "custom"],
            key=lambda category: (category["created_at"], category["key"]),
        )
        ordered = templates + customs
        if include_deleted:
            deleted = sorted(
                [self.categories[key] for key in self.category_order
                 if self.categories[key]["deleted_at"] is not None],
                key=lambda category: (category["created_at"], category["key"]),
            )
            ordered = ordered + deleted
        return ordered

    def enabled_categories(self):
        return [category for category in self.ordered_categories() if category["enabled"]]

    # --------------------------------------------------------------- 命题线
    def ordered_lines(self, category_key=None):
        """顺序：按 line_id 升序（UUIDv7 字典序 = 创建时间序）。"""
        lines = [self.lines[line_id] for line_id in self.line_order]
        if category_key is not None:
            lines = [line for line in lines if line["category_key"] == category_key]
        return sorted(lines, key=lambda line: line["line_id"])

    def line_facts(self, line_id):
        """同一 line 的事实段，按 (生效瞬间, fact_id) 升序（C-07：按瞬间比较）。"""
        facts = [self.facts[fact_id] for fact_id in self.fact_order
                 if self.facts[fact_id]["line_id"] == line_id]
        return sorted(facts, key=lambda fact: (clock.instant(fact["valid_from"]),
                                               fact["fact_id"]))

    def alive_line_facts(self, line_id):
        return [fact for fact in self.line_facts(line_id) if fact["deleted_at"] is None]

    def head_fact(self, line_id):
        """当前 head = 未删除段中生效瞬间最大者（与系统时钟无关；同瞬间取较大 fact_id）。"""
        alive = self.alive_line_facts(line_id)
        return alive[-1] if alive else None

    def head_fact_of_category(self, category_key):
        out = []
        for line in self.ordered_lines(category_key):
            head = self.head_fact(line["line_id"])
            if head is not None:
                out.append((line, head))
        return out

    # ----------------------------------------------------------------- 事实
    def fact(self, fact_id):
        return self.facts.get(fact_id)

    def revisions_of(self, fact_id):
        return self.revisions.get(fact_id, [])

    def latest_revision(self, fact_id):
        revisions = self.revisions_of(fact_id)
        return revisions[-1] if revisions else None

    def all_facts(self):
        return [self.facts[fact_id] for fact_id in self.fact_order]

    # ----------------------------------------------------------------- 计数
    def counts(self):
        facts = self.all_facts()
        return {
            "events": len(self.events),
            "categories_total": len(self.categories),
            "categories_enabled": len([category for category in self.alive_categories()
                                       if category["enabled"]]),
            "lines": len(self.lines),
            "facts": len([fact for fact in facts if fact["deleted_at"] is None]),
            "revisions": sum(len(value) for value in self.revisions.values()),
            "deleted_facts": len([fact for fact in facts if fact["deleted_at"] is not None]),
        }


def empty_model():
    return ReadModel()


def count_host_body_bytes(model):
    """host_body 的字节数（容量预算用；不写文件）。"""
    from .profile import render_host_body  # 延迟 import，避免循环依赖
    return len(render_host_body(model).encode("utf-8"))


def host_body_warn(model):
    size = count_host_body_bytes(model)
    if size > 16384:
        return (
            "宿主产物正文 %d B 超过告警阈值 16384 B；建议用 fasti category disable/delete 降载"
            % size
        )
    return None
