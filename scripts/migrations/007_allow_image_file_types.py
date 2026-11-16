"""007：file_records.file_type 的 CHECK 约束扩容，允许图片类型。

背景（2026-11-03 产品决策）：资料主文件支持图片（jpg/jpeg/png/gif/webp）。
Python 层白名单（app/core/file_safety.py）与模型 CHECK 都需同步扩容，
只改一边会导致「上传校验通过但落库 500」。

**这是本次三个迁移里唯一必须做的一个**：
0005/0006 删列后旧库仍能跑，但 file_type 的库级 CHECK 不扩容，
已部署的库一上传图片就 500。

方法：SQLite 改 CHECK 约束只能重建表（建新表→拷贝→换名）。
迁移期间 PRAGMA foreign_keys=OFF + legacy_alter_table=ON。

幂等：检测 sqlite_master 里 file_records 的建表语句是否已含 'webp'。
"""
from __future__ import annotations


CREATE_SQL = """
    CREATE TABLE file_records_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        owner_type VARCHAR(16) NOT NULL,
        owner_id INTEGER NOT NULL,
        original_filename VARCHAR(255) NOT NULL,
        internal_name VARCHAR(64) NOT NULL,
        file_type VARCHAR(16) NOT NULL,
        extension VARCHAR(16) NOT NULL,
        mime_type VARCHAR(128) NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        storage_rel_path VARCHAR(512) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        uploaded_by INTEGER,
        uploaded_at DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        CONSTRAINT ck_file_records_owner
            CHECK (owner_type IN ('document','notice')),
        CONSTRAINT ck_file_records_type
            CHECK (file_type IN ('pdf','docx','xlsx','xls',
                                 'jpg','jpeg','png','gif','webp')),
        CONSTRAINT ck_file_records_status
            CHECK (status IN ('active','recycled')),
        CONSTRAINT uq_file_records_internal_name UNIQUE (internal_name),
        FOREIGN KEY(uploaded_by) REFERENCES users (id)
    )
"""

COLUMNS = (
    "id, owner_type, owner_id, original_filename, internal_name, file_type, "
    "extension, mime_type, size_bytes, sha256, storage_rel_path, status, "
    "uploaded_by, uploaded_at, created_at"
)

INDEX_SQLS = [
    "CREATE INDEX ix_file_records_owner ON file_records (owner_type, owner_id)",
    "CREATE INDEX ix_file_records_sha256 ON file_records (sha256)",
]


def is_applied(conn) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='file_records'"
    ).fetchone()
    return bool(row and row[0] and "webp" in row[0])


def migrate(conn) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("ALTER TABLE file_records RENAME TO file_records_old_migrate")
    conn.execute(CREATE_SQL)
    conn.execute(
        f"INSERT INTO file_records_new ({COLUMNS}) SELECT {COLUMNS} "
        "FROM file_records_old_migrate"
    )
    conn.execute("DROP TABLE file_records_old_migrate")
    conn.execute("ALTER TABLE file_records_new RENAME TO file_records")
    for sql in INDEX_SQLS:
        conn.execute(sql)
    conn.execute("COMMIT")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
    conn.execute("PRAGMA foreign_keys=ON")
