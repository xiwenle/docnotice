"""002：初始化 SQLite 优化器统计（ANALYZE → sqlite_stat1）。

背景：SQLite 查询计划器在无统计信息时可能选错索引，数据量增长后查询退化。
app/db/session.py 已按 SQLite 官方建议在运行期定期执行 PRAGMA optimize
（增量、通常 no-op）；本迁移只对既有库做一次全量种子，让统计立即存在。

幂等：sqlite_stat1 已存在且包含 documents 的统计行即视为已应用。
"""
from __future__ import annotations


def is_applied(conn) -> bool:
    has_stat_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'"
    ).fetchone()
    if not has_stat_table:
        return False
    rows = conn.execute("SELECT DISTINCT tbl FROM sqlite_stat1").fetchall()
    return any(r[0] == "documents" for r in rows)


def migrate(conn) -> None:
    conn.execute("ANALYZE")
