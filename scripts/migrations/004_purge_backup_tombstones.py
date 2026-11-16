"""004：清理备份记录的历史 purged 墓碑行（真删除语义，2026-08-30 拍板）。

背景：备份删除曾为软标记（status='purged'，列表仍显示「已删除」行）。
2026-08-30 产品决策改为真删除：删除即记录行物理删除、列表不再显示。
本迁移一次性清除历史墓碑行；此后代码不再产生 purged 状态。
幂等：不存在 purged 行即视为已应用。
"""
from __future__ import annotations


def is_applied(conn) -> bool:
    rows = conn.execute(
        "SELECT COUNT(*) FROM backup_records WHERE status = 'purged'"
    ).fetchone()
    return rows[0] == 0


def migrate(conn) -> None:
    conn.execute("DELETE FROM backup_records WHERE status = 'purged'")
