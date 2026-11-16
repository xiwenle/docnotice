"""011：移除通知草稿（draft）状态。

背景（2026-11-04 产品决策）：草稿功能整体下线。
- 界面上从来没有「存为草稿」按钮（新建只有「创建通知」），
  草稿只在发布失败时残留，成了没人认领的半成品；
- 它承担的两个职责（发布失败可回退、恢复时区分是否发布过）
  改由「创建即发布、失败整体回滚」直接覆盖，不再需要中间态。

本迁移做两件事（用户明确要求：功能没了，数据也一起删）：
1. **删除**存量 draft 通知，连同其 notice_targets / notice_documents /
   notice_reads 一并清理。draft 从未发布，因此不会丢失任何阅读快照；
   其附件仍在 file_records 中（不物理删除业务文件，§26）。
2. 重建 notices 表，CHECK 从 `('draft','published','cancelled')` 收窄为
   `('published','cancelled')`。SQLite 不支持改 CHECK，只能换表。

幂等：库里已无 draft 行、且表定义已不含 'draft' 时视为已应用。
"""
from __future__ import annotations

# 与 migrate 前的真实 schema 保持逐列一致，只把 status 的默认值与 CHECK 收窄。
# 新增列时必须同步这里，否则重建会丢列。
_COLUMNS = (
    "id, title, content, notice_type, status, published_at, effective_at, "
    "deadline_at, file_id, created_by, updated_by, updated_at, modified_reason, "
    "cancel_reason, auto_expired_docs, created_at"
)

_CREATE_SQL = """
CREATE TABLE notices_new (
    id INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT,
    notice_type VARCHAR(32),
    status VARCHAR(16) DEFAULT 'published' NOT NULL,
    published_at DATETIME,
    effective_at DATETIME,
    deadline_at DATETIME,
    file_id INTEGER,
    created_by INTEGER,
    updated_by INTEGER,
    updated_at DATETIME NOT NULL,
    modified_reason VARCHAR(255),
    cancel_reason VARCHAR(255),
    auto_expired_docs TEXT,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_notices_status CHECK (status IN ('published','cancelled')),
    FOREIGN KEY(file_id) REFERENCES file_records (id),
    FOREIGN KEY(created_by) REFERENCES users (id),
    FOREIGN KEY(updated_by) REFERENCES users (id)
)
"""


def _table_sql(conn) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='notices'"
    ).fetchone()
    return (row[0] or "") if row else ""


def is_applied(conn) -> bool:
    if "draft" in _table_sql(conn):
        return False
    row = conn.execute("SELECT COUNT(*) FROM notices WHERE status = 'draft'").fetchone()
    return int(row[0] or 0) == 0


def migrate(conn) -> None:
    conn.execute("BEGIN IMMEDIATE")

    draft_ids = [
        r[0] for r in conn.execute("SELECT id FROM notices WHERE status='draft'")
    ]
    if draft_ids:
        placeholders = ",".join("?" * len(draft_ids))
        for table in ("notice_reads", "notice_targets", "notice_documents"):
            conn.execute(
                f"DELETE FROM {table} WHERE notice_id IN ({placeholders})", draft_ids
            )
        conn.execute(f"DELETE FROM notices WHERE id IN ({placeholders})", draft_ids)

    conn.execute(_CREATE_SQL)
    conn.execute(
        f"INSERT INTO notices_new ({_COLUMNS}) SELECT {_COLUMNS} FROM notices"
    )
    conn.execute("DROP TABLE notices")
    conn.execute("ALTER TABLE notices_new RENAME TO notices")

    # 索引随旧表一起被 DROP，重建
    conn.execute("CREATE INDEX ix_notices_status ON notices (status)")
    conn.execute("CREATE INDEX ix_notices_type ON notices (notice_type)")
    conn.execute("CREATE INDEX ix_notices_published_at ON notices (published_at)")
    conn.execute("CREATE INDEX ix_notices_effective_at ON notices (effective_at)")

    conn.execute("COMMIT")
    print(f"    删除草稿通知 {len(draft_ids)} 条；notices 状态约束已收窄")
