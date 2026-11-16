"""009：资料可见范围（document_targets 表 + documents.audience_scope 列）。

背景（2026-11-03 产品决策）：新建资料可指定哪些人/部门能看，与通知的
阅读对象同构。存量资料一律回填为 `all`（全体可见）——保证升级后普通
用户不会突然看不到任何东西。

本迁移是**纯增量**：只加列、加表、回填默认值，不删任何数据。

幂等：检测 documents.audience_scope 列与 document_targets 表是否都已存在。
"""
from __future__ import annotations


def is_applied(conn) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_targets'"
    ).fetchone()
    return "audience_scope" in cols and has_table is not None


def migrate(conn) -> None:
    conn.execute("BEGIN IMMEDIATE")

    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "audience_scope" not in cols:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN "
            "audience_scope VARCHAR(16) NOT NULL DEFAULT 'all'"
        )
    # 回填：任何历史 NULL 都视为全体可见（防御旧库的半迁移状态）
    conn.execute(
        "UPDATE documents SET audience_scope = 'all' "
        "WHERE audience_scope IS NULL OR audience_scope = ''"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_targets (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            target_type VARCHAR(16) NOT NULL,
            department_id INTEGER,
            user_id INTEGER,
            created_at DATETIME NOT NULL,
            CONSTRAINT ck_document_targets_type
                CHECK (target_type IN ('all','department','user')),
            CONSTRAINT uq_document_targets
                UNIQUE (document_id, target_type, department_id, user_id),
            FOREIGN KEY(document_id) REFERENCES documents (id),
            FOREIGN KEY(department_id) REFERENCES departments (id),
            FOREIGN KEY(user_id) REFERENCES users (id)
        )
        """
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_document_targets_document_id "
        "ON document_targets (document_id)",
        "CREATE INDEX IF NOT EXISTS ix_document_targets_department "
        "ON document_targets (department_id)",
        "CREATE INDEX IF NOT EXISTS ix_document_targets_user "
        "ON document_targets (user_id)",
    ):
        conn.execute(sql)

    conn.execute("COMMIT")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
