"""012：资料附件槽 + file_records 允许 pptx + recycle_bin 新增 attachment_deleted。

背景（2026-11-06 产品决策）：资料 = 1 个主文件 + 最多 1 个附件。
附件永不预览，只是可下载的额外文件槽，因此可以放宽到 pptx
（主文件白名单仍不含 pptx，由 app/core/file_safety.MAIN_FILE_EXTENSIONS 拦）。

本迁移做四件事：

1. 重建 ``file_records``：``file_type`` CHECK 扩容加 ``pptx``。
   Python 层白名单与库级 CHECK 必须同步，只改一边会「校验通过但落库 500」。
2. 重建 ``recycle_bin``：``reason`` CHECK 加 ``attachment_deleted``。
   刻意不复用 ``deleted``——recycle_service.restore 的 deleted 分支会连同
   取消资料归档，删单个附件时误走该分支会把整份资料恢复出来。
3. ``ALTER TABLE documents ADD COLUMN attachment_file_id``
4. ``ALTER TABLE recycle_bin ADD COLUMN attachment_file_record_id``

方法：SQLite 改 CHECK 约束只能重建表（建新表→拷贝→换名）。
迁移期间 PRAGMA foreign_keys=OFF + legacy_alter_table=ON，事后 foreign_key_check。

幂等：检测三处特征同时满足才认为已应用。
"""
from __future__ import annotations

CREATE_FILE_RECORDS_SQL = """
    CREATE TABLE file_records_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        owner_type VARCHAR(16) NOT NULL,
        owner_id INTEGER NOT NULL,
        original_filename VARCHAR(255) NOT NULL,
        internal_name VARCHAR(64) NOT NULL,
        file_type VARCHAR(8) NOT NULL,
        extension VARCHAR(8) NOT NULL,
        mime_type VARCHAR(128) NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        storage_rel_path VARCHAR(255) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        uploaded_by INTEGER,
        uploaded_at DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        CONSTRAINT ck_file_records_owner
            CHECK (owner_type IN ('document','notice')),
        CONSTRAINT ck_file_records_type
            CHECK (file_type IN ('pdf','docx','xlsx','xls','pptx',
                                 'jpg','jpeg','png','gif','webp')),
        CONSTRAINT ck_file_records_status
            CHECK (status IN ('active','recycled')),
        CONSTRAINT uq_file_records_internal_name UNIQUE (internal_name),
        FOREIGN KEY(uploaded_by) REFERENCES users (id)
    )
"""

FILE_RECORD_COLUMNS = (
    "id, owner_type, owner_id, original_filename, internal_name, file_type, "
    "extension, mime_type, size_bytes, sha256, storage_rel_path, status, "
    "uploaded_by, uploaded_at, created_at"
)

FILE_RECORD_INDEXES = [
    "CREATE INDEX ix_file_records_owner ON file_records (owner_type, owner_id)",
    "CREATE INDEX ix_file_records_sha256 ON file_records (sha256)",
]

# 新增列必须先建好，否则拷贝语句带不上；这里 recycle_bin 要重建，
# 直接把 attachment_file_record_id 写进建表语句，最后再单独 ALTER documents。
CREATE_RECYCLE_BIN_SQL = """
    CREATE TABLE recycle_bin_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER,
        file_record_id INTEGER,
        attachment_file_record_id INTEGER,
        reason VARCHAR(16) NOT NULL,
        origin_rel_path VARCHAR(255),
        deleted_by INTEGER,
        deleted_at DATETIME NOT NULL,
        restored_at DATETIME,
        restored_by INTEGER,
        purged_at DATETIME,
        purged_by INTEGER,
        CONSTRAINT ck_recycle_bin_reason
            CHECK (reason IN ('replaced','archived','deleted','attachment_deleted')),
        FOREIGN KEY(document_id) REFERENCES documents (id),
        FOREIGN KEY(file_record_id) REFERENCES file_records (id),
        FOREIGN KEY(attachment_file_record_id) REFERENCES file_records (id),
        FOREIGN KEY(deleted_by) REFERENCES users (id),
        FOREIGN KEY(restored_by) REFERENCES users (id),
        FOREIGN KEY(purged_by) REFERENCES users (id)
    )
"""

RECYCLE_BIN_COLUMNS_OLD = (
    "id, document_id, file_record_id, reason, origin_rel_path, deleted_by, "
    "deleted_at, restored_at, restored_by, purged_at, purged_by"
)

RECYCLE_BIN_INDEXES = [
    "CREATE INDEX ix_recycle_bin_document_id ON recycle_bin (document_id)",
    "CREATE INDEX ix_recycle_bin_status ON recycle_bin (deleted_at)",
]


def _table_sql(conn, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return (row[0] or "") if row else ""


def _has_column(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def is_applied(conn) -> bool:
    return (
        "pptx" in _table_sql(conn, "file_records")
        and "attachment_deleted" in _table_sql(conn, "recycle_bin")
        and _has_column(conn, "documents", "attachment_file_id")
        and _has_column(conn, "recycle_bin", "attachment_file_record_id")
    )


def migrate(conn) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("BEGIN IMMEDIATE")

    # --- file_records：CHECK 扩容加 pptx ---
    conn.execute("ALTER TABLE file_records RENAME TO file_records_old_migrate")
    conn.execute(CREATE_FILE_RECORDS_SQL)
    conn.execute(
        f"INSERT INTO file_records_new ({FILE_RECORD_COLUMNS}) "
        f"SELECT {FILE_RECORD_COLUMNS} FROM file_records_old_migrate"
    )
    conn.execute("DROP TABLE file_records_old_migrate")
    conn.execute("ALTER TABLE file_records_new RENAME TO file_records")
    for sql in FILE_RECORD_INDEXES:
        conn.execute(sql)

    # --- recycle_bin：CHECK 扩容加 attachment_deleted + 新列 ---
    conn.execute("ALTER TABLE recycle_bin RENAME TO recycle_bin_old_migrate")
    conn.execute(CREATE_RECYCLE_BIN_SQL)
    conn.execute(
        f"INSERT INTO recycle_bin_new ({RECYCLE_BIN_COLUMNS_OLD}) "
        f"SELECT {RECYCLE_BIN_COLUMNS_OLD} FROM recycle_bin_old_migrate"
    )
    conn.execute("DROP TABLE recycle_bin_old_migrate")
    conn.execute("ALTER TABLE recycle_bin_new RENAME TO recycle_bin")
    for sql in RECYCLE_BIN_INDEXES:
        conn.execute(sql)

    # --- documents：新列 ---
    conn.execute("ALTER TABLE documents ADD COLUMN attachment_file_id INTEGER")

    conn.execute("COMMIT")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
    conn.execute("PRAGMA foreign_keys=ON")
