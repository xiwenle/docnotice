"""010：清理管理员的历史阅读快照（notice_reads）。

背景（2026-11-03 产品决策）：管理员不进阅读对象——
- 他能看全部通知是管理权限，不等于「被要求阅读」；
- 把他算进阅读对象会让工作台挂一条永远清不掉的「未读」，
  也会让统计分母失真。

代码层已经改了（build_audience / backfill / _ensure_snapshot 都不再为
管理员建行），但**存量库里已有管理员快照**，不清理的话工作台仍显示未读。

本迁移只删「管理员名下的 notice_reads 行」，不动通知本身，
也不动普通用户的阅读记录。

幂等：删除后该查询自然返回 0 行。
"""
from __future__ import annotations


def is_applied(conn) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM notice_reads "
        "WHERE user_id IN (SELECT id FROM users WHERE role = 'admin')"
    ).fetchone()
    return int(row[0] or 0) == 0


def migrate(conn) -> None:
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute(
        "DELETE FROM notice_reads "
        "WHERE user_id IN (SELECT id FROM users WHERE role = 'admin')"
    )
    removed = cur.rowcount or 0
    conn.execute("COMMIT")
    print(f"    清理管理员阅读快照 {removed} 条")
