"""内存 SQLite 声明式约束校验（v2 读取模型，schema_version 2）。

把折叠结果载入 `:memory:` SQLite 执行 `database-schema.sql`（随包副本，与
artifacts/database-schema.sql 逐字节相同），用 PK/FK/UNIQUE/CHECK 做校验；
**不落地任何 .db 文件**（ADR-009）。
"""

import os
import sqlite3

from . import errors
from . import store


def schema_path():
    """database-schema.sql 的定位：FASTI_SCHEMA_PATH → 随包副本。"""
    env_path = os.environ.get("FASTI_SCHEMA_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "schema", "database-schema.sql")


def load_schema_sql():
    path = schema_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise errors.make("IO_READ_FAILED", "无法读取 database-schema.sql：%s（%s）" % (path, exc),
                          location=path)


def check_sql_constraints(model):
    """把折叠结果载入内存 SQLite 执行 DDL 约束。返回 (ok, detail)。"""
    try:
        sql = load_schema_sql()
    except errors.CoreError as exc:
        return False, exc.message
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(sql)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        conn.execute("PRAGMA defer_foreign_keys = ON")

        for event in model.events:
            conn.execute(
                "INSERT INTO event (event_seq, event_type, occurred_at, payload) VALUES (?,?,?,?)",
                (event["seq"], event["type"], event["at"], store.canonical_json(event["data"])),
            )

        for category_key in model.category_order:
            category = model.categories[category_key]
            conn.execute(
                "INSERT INTO category (category_key, display_name, origin, template_order,"
                " enabled, created_at, deleted_at, deleted_handling, migrate_to_category_key)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (category["key"], category["display_name"], category["origin"],
                 category["template_order"], 1 if category["enabled"] else 0,
                 category["created_at"], category["deleted_at"],
                 category["deleted_handling"], category["migrate_to_category_key"]),
            )
        for line_id in model.line_order:
            line = model.lines[line_id]
            conn.execute(
                "INSERT INTO fact_line (line_id, category_key, created_at) VALUES (?,?,?)",
                (line["line_id"], line["category_key"], line["created_at"]),
            )
        for fact_id in model.fact_order:
            fact = model.facts[fact_id]
            conn.execute(
                "INSERT INTO fact (fact_id, line_id, valid_from, valid_to, change_type,"
                " previous_fact_id, source, recorded_at, deleted_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (fact["fact_id"], fact["line_id"], fact["valid_from"], fact["valid_to"],
                 fact["change_type"], fact["previous_fact_id"], fact["source"],
                 fact["recorded_at"], fact["deleted_at"]),
            )
        for fact_id in model.fact_order:
            for revision in model.revisions_of(fact_id):
                conn.execute(
                    "INSERT INTO fact_revision (fact_id, seq, content, revision_type, note,"
                    " recorded_at) VALUES (?,?,?,?,?,?)",
                    (revision["fact_id"], revision["seq"], revision["content"],
                     revision["revision_type"], revision["note"], revision["recorded_at"]),
                )
        conn.execute("COMMIT")
        return True, "内存 SQLite 执行 database-schema.sql（schema_version 3）约束通过"
    except sqlite3.IntegrityError as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return False, "SQLite 约束校验失败：%s" % exc
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return False, "SQLite 执行失败：%s" % exc
    finally:
        conn.close()
