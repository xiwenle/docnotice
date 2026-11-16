"""006：删除「资料编号」（documents.doc_no）及其索引。

背景（2026-11-03 产品决策）：资料编号是可选字段，实际使用中没有形成编号
体系，反而在表单、列表、搜索、通知关联里各占一处，增加录入负担。
决策：彻底移除（含数据库字段、搜索匹配、界面展示）。

**数据不可恢复**：已有编号会随列一起删除。升级前请先做一次备份。

方法：SQLite 删列走「建新表→拷贝→换名」，保留 documents 的全部其他列、
CHECK 约束与索引。迁移期间 PRAGMA foreign_keys=OFF + legacy_alter_table=ON。

幂等：检测 documents 是否已无 doc_no 列。
"""
from __future__ import annotations


CREATE_SQL = """
    CREATE TABLE documents_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        title VARCHAR(255) NOT NULL,
        summary TEXT,
        category_id INTEGER,
        effective_from DATE NOT NULL,
        effective_to DATE,
        effective_note VARCHAR(255),
        allow_download BOOLEAN NOT NULL DEFAULT 0,
        current_file_id INTEGER,
        rev_count INTEGER NOT NULL DEFAULT 1,
        archived_at DATETIME,
        archived_by INTEGER,
        archive_reason VARCHAR(255),
        created_by INTEGER,
        updated_by INTEGER,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(category_id) REFERENCES categories (id),
        FOREIGN KEY(current_file_id) REFERENCES file_records (id),
        FOREIGN KEY(archived_by) REFERENCES users (id),
        FOREIGN KEY(created_by) REFERENCES users (id),
        FOREIGN KEY(updated_by) REFERENCES users (id)
    )
"""

COLUMNS = (
    "id, title, summary, category_id, effective_from, effective_to, "
    "effective_note, allow_download, current_file_id, rev_count, archived_at, "
    "archived_by, archive_reason, created_by, updated_by, created_at, updated_at"
)

INDEX_SQLS = [
    "CREATE INDEX ix_documents_category_id ON documents (category_id)",
    "CREATE INDEX ix_documents_effective_from ON documents (effective_from)",
    "CREATE INDEX ix_documents_effective_to ON documents (effective_to)",
    "CREATE INDEX ix_documents_archived ON documents (archived_at)",
]


def is_applied(conn) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    return "doc_no" not in cols


def migrate(conn) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("ALTER TABLE documents RENAME TO documents_old_migrate")
    conn.execute(CREATE_SQL)
    conn.execute(
        f"INSERT INTO documents_new ({COLUMNS}) SELECT {COLUMNS} "
        "FROM documents_old_migrate"
    )
    conn.execute("DROP TABLE documents_old_migrate")
    conn.execute("ALTER TABLE documents_new RENAME TO documents")
    for sql in INDEX_SQLS:
        conn.execute(sql)
    conn.execute("COMMIT")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"迁移后外键校验失败: {violations}")
    conn.execute("PRAGMA foreign_keys=ON")

    # doc_no 索引随表重建已消失；如历史库残留同名索引则一并清理
    conn.execute("DROP INDEX IF EXISTS ix_documents_doc_no")
