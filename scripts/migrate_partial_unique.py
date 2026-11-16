"""一次性迁移：categories / departments 的唯一约束改为「仅对未删除行生效」。

背景
----
删除分类/部门是软删除（is_active=0，§26）。旧的全行 UNIQUE 约束让已删除的
分类/部门继续占用名称，且在页面上不可见、无入口改名或恢复——创建同名被
DUPLICATE 拒绝、父分类被 IN_USE 死锁阻挡，形成逻辑死角。

本迁移把全行 UNIQUE 约束替换为部分唯一索引（partial unique index，
WHERE is_active = 1）：未删除行保持唯一，已删除行不再占用名称。

用法
----
    python scripts/migrate_partial_unique.py            # 执行迁移
    python scripts/migrate_partial_unique.py --check    # 仅检查不执行

说明
----
- 幂等：已存在部分唯一索引时跳过。
- SQLite 不支持删除表内约束，采用「建新表 → 拷贝 → 换名」标准流程；
  迁移期间 PRAGMA foreign_keys=OFF，结束后 foreign_key_check 校验。
- 执行前建议备份 data/database/（数据安全 > 一切，项目规范 §3）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402

TARGET_INDEXES = {
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


def _existing_partial_indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%WHERE%'"
    ).fetchall()
    return {r[0] for r in rows}


def _rebuild(conn: sqlite3.Connection, table: str) -> None:
    # isolation_level=None（autocommit）+ 显式 BEGIN IMMEDIATE，避免隐式事务干扰。
    # legacy_alter_table=ON：RENAME 时不改写其他表 FK 引用文本（官方对"仅重建
    # 单表"的建议流程）——否则 documents.category_id 会被跟随改写到旧表名，
    # DROP 旧表后外键悬空。
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
    # 外键完整性校验（不通过则直接暴露问题）
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
    conn.execute("PRAGMA foreign_keys=ON")


def main() -> None:
    parser = argparse.ArgumentParser(description="唯一约束部分化迁移（categories/departments）")
    parser.add_argument("--check", action="store_true", help="仅检查是否需要迁移")
    args = parser.parse_args()

    db_path = get_settings().data_dir / "database" / "app.db"
    if not db_path.exists():
        print(f"数据库不存在: {db_path}（首次启动建表时将直接使用新约束）")
        return

    conn = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit：显式控制事务
    try:
        existing = _existing_partial_indexes(conn)
        pending = [t for t, idx in TARGET_INDEXES.items() if idx not in existing]
        if not pending:
            print("✅ 无需迁移：部分唯一索引已存在")
            return
        print(f"待迁移表: {pending}")
        if args.check:
            return
        print("⚠️  建议先备份 data/database/ 再继续（数据安全优先）")
        for table in pending:
            print(f"  → 重建 {table} ...")
            _rebuild(conn, table)
            print(f"  ✓ {table} 完成")
        conn.commit()
        # 迁移后数据量核对
        for table in pending:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  ✓ {table} 行数: {n}")
        print("✅ 迁移完成")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
