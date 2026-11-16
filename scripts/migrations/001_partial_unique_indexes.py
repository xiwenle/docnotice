"""001：categories/departments 唯一约束部分化（已删除行不占名）。

背景：删除分类/部门是软删除（is_active=0）。旧全行 UNIQUE 约束让已删除的
行继续占用名称——服务层放行后二级分类同名重建会撞约束 500（一级因 SQLite
NULL 语义侥幸放行）。本迁移把全行 UNIQUE 替换为部分唯一索引
（WHERE is_active = 1）。

方法：SQLite 不支持删除表内约束，采用「建新表→拷贝→换名」流程；
迁移期间 PRAGMA foreign_keys=OFF + legacy_alter_table=ON（RENAME 不改写
引用方 FK 文本），结束后 foreign_key_check 校验。

幂等：检测部分唯一索引是否已存在（此前手动 migrate_partial_unique.py
可能已应用过）。
"""
from __future__ import annotations

INDEX_MARKERS = {
    "categories": "uq_categories_parent_name_active",
    "departments": "uq_departments_name_active",
}

REBUILD_SQL = {
    "categories": """
        CREATE TABLE categories_new (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            parent_id INTEGER,
            sort_order INTEGER DEFAULT '0' NOT NULL,
            is_active INTEGER DEFAULT '1' NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(parent_id) REFERENCES categories (id)
        )
    """,
    "departments": """
        CREATE TABLE departments_new (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            code VARCHAR(64),
            description VARCHAR(255),
            is_active BOOLEAN DEFAULT 1 NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
}

COLUMNS = {
    "categories": "id, name, parent_id, sort_order, is_active, created_at, updated_at",
    "departments": "id, name, code, description, is_active, created_at, updated_at",
}

CREATE_INDEX_SQL = {
    "categories": [
        "CREATE INDEX ix_categories_parent_id ON categories (parent_id)",
        "CREATE UNIQUE INDEX uq_categories_parent_name_active "
        "ON categories (parent_id, name) WHERE is_active = 1",
    ],
    "departments": [
        "CREATE INDEX ix_departments_is_active ON departments (is_active)",
        "CREATE UNIQUE INDEX uq_departments_name_active "
        "ON departments (name) WHERE is_active = 1",
        "CREATE UNIQUE INDEX uq_departments_code_active "
        "ON departments (code) WHERE is_active = 1",
    ],
}


def _rebuild(conn, table: str) -> None:
    # autocommit 连接 + 显式 BEGIN IMMEDIATE；legacy_alter_table 防止
    # RENAME 改写 documents 等引用方的 FK 文本
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old_migrate")
    conn.execute(REBUILD_SQL[table])
    conn.execute(
        f"INSERT INTO {table}_new ({COLUMNS[table]}) "
        f"SELECT {COLUMNS[table]} FROM {table}_old_migrate"
    )
    conn.execute(f"DROP TABLE {table}_old_migrate")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    for sql in CREATE_INDEX_SQL[table]:
        conn.execute(sql)
    conn.execute("COMMIT")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
    conn.execute("PRAGMA foreign_keys=ON")


def is_applied(conn) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%WHERE%'"
    ).fetchall()
    have = {r[0] for r in rows}
    return all(idx in have for idx in INDEX_MARKERS.values())


def migrate(conn) -> None:
    for table in ("categories", "departments"):
        _rebuild(conn, table)