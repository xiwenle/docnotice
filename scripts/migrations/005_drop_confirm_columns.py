"""005：删除「确认」功能相关的三列。

背景（2026-11-03 产品决策）：通知的「确认」与「查看」两套状态并存，
统计页要同时看两个口径，实际使用中判断成本高于收益。
决策：彻底下线确认功能，只保留「已查看 / 未查看」。

删除的列（**数据不可恢复**，升级前请先做一次备份）：
- notices.requires_confirmation         通知级开关
- notice_reads.confirmed_at             每人的确认时间
- notice_reads.confirm_ip               每人的确认来源 IP

方法：SQLite 不支持 DROP COLUMN 的旧版本兼容性考量 + 需要同时删索引，
统一走「建新表→拷贝→换名」。迁移期间 PRAGMA foreign_keys=OFF +
legacy_alter_table=ON（防止 RENAME 改写引用方 FK 文本），结束后 foreign_key_check。

幂等：检测上述三列是否都已不存在。
"""
from __future__ import annotations


NOTICES_SQL = """
    CREATE TABLE notices_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        title VARCHAR(255) NOT NULL,
        content TEXT,
        notice_type VARCHAR(32),
        status VARCHAR(16) NOT NULL DEFAULT 'draft',
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
        CONSTRAINT ck_notices_status
            CHECK (status IN ('draft','published','cancelled')),
        FOREIGN KEY(file_id) REFERENCES file_records (id),
        FOREIGN KEY(created_by) REFERENCES users (id),
        FOREIGN KEY(updated_by) REFERENCES users (id)
    )
"""

NOTICES_COLS = (
    "id, title, content, notice_type, status, published_at, effective_at, "
    "deadline_at, file_id, created_by, updated_by, updated_at, modified_reason, "
    "cancel_reason, auto_expired_docs, created_at"
)

NOTICE_READS_SQL = """
    CREATE TABLE notice_reads_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        notice_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        read_at DATETIME,
        read_ip VARCHAR(64),
        CONSTRAINT uq_notice_reads UNIQUE (notice_id, user_id),
        FOREIGN KEY(notice_id) REFERENCES notices (id),
        FOREIGN KEY(user_id) REFERENCES users (id)
    )
"""

NOTICE_READS_COLS = "id, notice_id, user_id, read_at, read_ip"


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def is_applied(conn) -> bool:
    notices = _columns(conn, "notices")
    reads = _columns(conn, "notice_reads")
    return (
        "requires_confirmation" not in notices
        and "confirmed_at" not in reads
        and "confirm_ip" not in reads
    )


def _rebuild(conn, old_table: str, new_table: str, create_sql: str,
             cols: str, index_sqls: list[str]) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(f"ALTER TABLE {old_table} RENAME TO {old_table}_old_migrate")
    conn.execute(create_sql)
    conn.execute(
        f"INSERT INTO {new_table} ({cols}) SELECT {cols} FROM {old_table}_old_migrate"
    )
    conn.execute(f"DROP TABLE {old_table}_old_migrate")
    conn.execute(f"ALTER TABLE {new_table} RENAME TO {old_table}")
    for sql in index_sqls:
        conn.execute(sql)
    conn.execute("COMMIT")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
    conn.execute("PRAGMA foreign_keys=ON")


def migrate(conn) -> None:
    _rebuild(
        conn, "notices", "notices_new", NOTICES_SQL, NOTICES_COLS,
        [
            "CREATE INDEX ix_notices_status ON notices (status)",
            "CREATE INDEX ix_notices_type ON notices (notice_type)",
            "CREATE INDEX ix_notices_published_at ON notices (published_at)",
            "CREATE INDEX ix_notices_effective_at ON notices (effective_at)",
        ],
    )
    _rebuild(
        conn, "notice_reads", "notice_reads_new", NOTICE_READS_SQL, NOTICE_READS_COLS,
        [
            "CREATE INDEX ix_notice_reads_user_id ON notice_reads (user_id)",
        ],
    )
