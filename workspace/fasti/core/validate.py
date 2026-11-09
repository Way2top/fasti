"""校验层门面（C4）：re-export 字段/事件/不变量/SQL 四组校验函数。

实现位于 validate_fields / validate_events / validate_invariants / validate_sql（§11.3.2）；
调用点（importer.py、services/facts.py）保持不变。
"""

from .validate_fields import (
    require_category_key,
    require_category_name,
    require_content,
    require_month,
    require_note,
)
from .validate_events import validate_event_record
from .validate_invariants import check_invariants
from .validate_sql import check_sql_constraints, load_schema_sql, schema_path

__all__ = [
    "require_content",
    "require_note",
    "require_month",
    "require_category_name",
    "require_category_key",
    "validate_event_record",
    "check_invariants",
    "schema_path",
    "load_schema_sql",
    "check_sql_constraints",
]
