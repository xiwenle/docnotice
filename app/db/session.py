"""数据库引擎与会话管理（SQLAlchemy 2.0 + SQLite）。

- 数据库路径可配置（app.core.config.get_settings().database_url），默认位于 data/database/app.db
- SQLite 实用 PRAGMA：外键强制、WAL 日志、busy_timeout、定期 optimize（docs/DATABASE.md §0、DESIGN.md §12）
- 数据库只存元数据与关系，不存文件二进制（项目规范 §27）
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# check_same_thread=False：FastAPI 多线程处理请求，SQLite 连接跨线程使用
# timeout=30：sqlite3 驱动层的默认锁等待；**实际生效值以下方 PRAGMA
# busy_timeout=5000（5 秒）为准**（PRAGMA 在连接建立时覆盖驱动参数）。
# 如需更长等待，改 PRAGMA 那行，不要只改这里（2026-10-16 注释勘误）。
_engine_options = {
    "connect_args": {"check_same_thread": False, "timeout": 30},
    "pool_pre_ping": True,
}
# 文件型 SQLite 使用 QueuePool。连接数必须覆盖 FastAPI 的并发工作线程，
# 否则 100 用户突发冷读会在 15 个连接处排队并最终抛 QueuePool timeout。
# 内存库由测试/工具使用，由 SQLAlchemy 自行选择池类型，不强行注入 QueuePool 参数。
if ":memory:" not in settings.database_url:
    _engine_options.update(
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
    )

engine: Engine = create_engine(settings.database_url, **_engine_options)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """连接建立时启用 SQLite 安全/并发 PRAGMA。"""
    if engine.url.get_backend_name() != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    try:
        # busy_timeout 必须最先设置：journal_mode=WAL 在非 WAL 库或 checkpoint
        # 竞争时可能等待数据库锁，先设置 5 秒上限，避免连接建立被长时间卡住。
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


# PRAGMA optimize 限流：连接归还时按需刷新优化器统计（sqlite_stat1），
# 通常为 no-op，仅在表规模较上次统计增长明显时才真正执行 ANALYZE
# （SQLite 官方建议的定期维护方式）。限流每小时最多一次/每进程。
_OPTIMIZE_INTERVAL_SECONDS = 3600
_last_optimize_monotonic = [0.0]


@event.listens_for(engine, "checkin")
def _sqlite_periodic_optimize(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    now = time.monotonic()
    if now - _last_optimize_monotonic[0] < _OPTIMIZE_INTERVAL_SECONDS:
        return
    _last_optimize_monotonic[0] = now
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA optimize")
        finally:
            cursor.close()
    except Exception:  # noqa: BLE001  理由见下：统计刷新失败不得影响业务请求
        # 统计刷新失败不应影响业务请求；记录后下个周期自动重试
        _last_optimize_monotonic[0] = 0.0
        logger.warning("PRAGMA optimize 执行失败，将在下个周期重试", exc_info=True)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类（app/models/* 继承本类）。"""


def get_db():
    """FastAPI 依赖：请求级数据库会话，请求结束自动关闭。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def total_count(db: Session, stmt) -> int:
    """分页 COUNT 统一实现：对已含过滤/连接条件的查询取总数（各服务共用）。"""
    from sqlalchemy import func, select

    return db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()