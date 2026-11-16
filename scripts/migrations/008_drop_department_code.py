"""008：删除「部门编码」（departments.code）及其唯一索引。

背景（2026-11-03 产品决策）：部门编码是可选字段，实际使用中没有形成
编码体系，列表/表单/搜索各占一处，反而增加录入负担。决策：彻底移除。

**数据不可恢复**：已有部门编码会随列一起删除。

方法：SQLite 删列走「建新表→拷贝→换名」，保留其他列与索引。
迁移期间 PRAGMA foreign_keys=OFF + legacy_alter_table=ON。

幂等：检测 departments 是否已无 code 列。
"""
from __future__ import annotations


CREATE_SQL = """
    CREATE TABLE departments_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(128) NOT NULL,
        description VARCHAR(255),
        is_active BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
"""

COLUMNS = "id, name, description, is_active, created_at, updated_at"

INDEX_SQLS = [
    "CREATE INDEX ix_departments_is_active ON departments (is_active)",
    # 名称唯一（仅对未删除行生效）；与模型一致
    "CREATE UNIQUE INDEX uq_departments_name_active ON departments (name) "
    "WHERE is_active = 1",
]


def is_applied(conn) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(departments)")}
    return "code" not in cols


def migrate(conn) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("ALTER TABLE departments RENAME TO departments_old_migrate")
    conn.execute(CREATE_SQL)
    conn.execute(
        f"INSERT INTO departments_new ({COLUMNS}) SELECT {COLUMNS} "
        "FROM departments_old_migrate"
    )
    conn.execute("DROP TABLE departments_old_migrate")
    conn.execute("ALTER TABLE departments_new RENAME TO departments")
    for sql in INDEX_SQLS:
        conn.execute(sql)
    conn.execute("COMMIT")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.execute("DROP INDEX IF EXISTS uq_departments_code_active")
