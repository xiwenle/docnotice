"""003：备份记录增加 source 字段（manual=手动 / auto=调度自动备份）。

背景：自动备份调度上线后，备份页需区分来源徽章（自动/手动）。
幂等：PRAGMA table_info 检测列是否已存在。
"""
from __future__ import annotations


def is_applied(conn) -> bool:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(backup_records)")]
    return "source" in cols


def migrate(conn) -> None:
    conn.execute(
        "ALTER TABLE backup_records "
        "ADD COLUMN source VARCHAR(8) NOT NULL DEFAULT 'manual'"
    )
