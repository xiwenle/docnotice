"""数据库初始化：建表 + 初始管理员 + 增量迁移。

迁移分工（2026-10-25 固化，评审 A1/M1）——两套机制不合并，按下述规则分流：
- 新库：Base.metadata.create_all（模型即最新 schema），不写迁移。
- 既有库的**结构演进**（加列/新表/改约束）：一律进 `scripts/migrations/NNN_*.py`
  （幂等契约见 scripts/migrate.py），init_db **不再新增**结构迁移。
- 本文件的内联迁移到此**冻结**（现存 11 个服务于存量库的启动自动升级，勿删勿改语义）；
  此后 init_db 只允许「纯数据修复/对账」类启动迁移，docstring 必须写明背景与幂等口径。
- 判据：需要 ALTER/建表/重建 → scripts/migrations/；只改行数据 → init_db。
"""
from __future__ import annotations

import logging
import shutil

from typing import Optional, cast

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import Base, Session, SessionLocal, engine
from app.models.category import Category
from app.models.document import Document
from app.models.user import User

logger = logging.getLogger(__name__)


def _bind_engine(db: Session) -> Engine:
    """Session.get_bind() 标注为 Engine | Connection，会话实际绑定的是 Engine。"""
    return cast(Engine, db.get_bind())


def create_tables() -> None:
    """按当前模型元数据建表（幂等）。"""
    import app.models  # noqa: F401  确保全部表已注册

    Base.metadata.create_all(bind=engine)


def seed_initial_admin(db: Session) -> Optional[User]:
    """写入初始管理员（仅当该用户名不存在时；幂等）。"""
    settings = get_settings()
    username = settings.INITIAL_ADMIN_USERNAME.strip()
    password = settings.INITIAL_ADMIN_PASSWORD

    if not username or not password:
        raise RuntimeError("INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD 未配置")

    existing = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if existing is not None:
        return None

    admin = User(
        username=username,
        password_hash=hash_password(password),
        display_name="系统管理员",
        role="admin",
        is_active=True,
        auth_source="local",
        # 2026-10-29 产品决策（产品决策）：初始管理员**不再**强制改密。
        # 理由：该账号由部署者本人创建、密码由本人掌握，强制跳转只是多一步操作；
        # 且未来接入统一登录平台后本地账号将退居备用。
        # 注意：管理员「重置他人密码」后的强制改密**保留**（见 user_service.reset_password），
        # 那是防止管理员长期知晓他人口令的真实风险，与首登场景不同。
        must_change_password=False,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    logger.info("已创建初始管理员: %s", username)
    return admin


def _migrate_users_role(db: Session) -> None:
    """一次性迁移（权限模型 v3）：superadmin 并入 admin。

    ① UPDATE users SET role='admin' WHERE role='superadmin'；
    ② 重建 users 表以更新 ck_users_role CHECK（SQLite 无法 ALTER 约束，
       沿用 _migrate_notices_table 的原生连接重建模式）。
    幂等：表定义不再含 'superadmin' 且无 superadmin 数据则跳过。
    """
    import sqlite3

    engine = _bind_engine(db)
    db_path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if db_path is None:
        logger.warning("非 SQLite 数据库，跳过 users 角色合并迁移")
        return
    raw = sqlite3.connect(db_path)
    try:
        table_sql = raw.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if table_sql is None:
            return  # 表不存在（首次建库由 create_tables 按新模型建表）
        n_super = raw.execute(
            "SELECT COUNT(*) FROM users WHERE role='superadmin'"
        ).fetchone()[0]
        if "superadmin" not in table_sql[0] and n_super == 0:
            return  # 已迁移
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("UPDATE users SET role='admin' WHERE role='superadmin'")
        raw.executescript(
            """
            DROP TABLE IF EXISTS users_new;
            CREATE TABLE users_new (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(64) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                display_name VARCHAR(128) NOT NULL,
                email VARCHAR(128),
                phone VARCHAR(32),
                role VARCHAR(16) NOT NULL DEFAULT 'user',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                auth_source VARCHAR(16) NOT NULL DEFAULT 'local',
                external_uid VARCHAR(128),
                must_change_password BOOLEAN NOT NULL DEFAULT 0,
                last_login_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT ck_users_role CHECK (role IN ('user','admin')),
                CONSTRAINT uq_users_username UNIQUE (username),
                CONSTRAINT uq_users_auth_ext UNIQUE (auth_source, external_uid)
            );
            INSERT INTO users_new
                (id, username, password_hash, display_name, email, phone, role,
                 is_active, auth_source, external_uid, must_change_password,
                 last_login_at, created_at, updated_at)
                SELECT id, username, password_hash, display_name, email, phone, role,
                       is_active, auth_source, external_uid, must_change_password,
                       last_login_at, created_at, updated_at
                FROM users;
            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;
            """
        )
        raw.execute("CREATE INDEX ix_users_role ON users (role)")
        raw.execute("CREATE INDEX ix_users_active ON users (is_active)")
        raw.execute(
            "UPDATE sqlite_sequence SET name='users', "
            "seq=(SELECT COALESCE(max(id),0) FROM users) "
            "WHERE name IN ('users','users_new')"
        )
        raw.commit()
        logger.info("已迁移 users：superadmin 并入 admin（%d 个账号），CHECK 约束已收紧", n_super)
    finally:
        raw.close()


def _migrate_notices(db: Session) -> None:
    """增量迁移：向 notices 表补加 auto_expired_docs 列（L-1 废止回滚用）。"""
    engine = _bind_engine(db)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(
            text("PRAGMA table_info(notices)")
        ).fetchall()}
        if "auto_expired_docs" not in cols:
            conn.execute(text(
                "ALTER TABLE notices ADD COLUMN auto_expired_docs TEXT"
            ))
            conn.commit()


def _migrate_recycle_bin(db: Session) -> None:
    """增量迁移：recycle_bin.file_record_id 改为可空（无文件资料删除也要留档）。

    SQLite 不支持 ALTER 修改列约束，采用标准"重建表"流程：
    新建同构表（file_record_id 可空）→ 拷贝数据 → 换名 → 重建索引。
    幂等：检测列已可空则跳过（PRAGMA 列结构：cid,name,type,notnull,dflt_value,pk）。
    """
    engine = _bind_engine(db)
    with engine.connect() as conn:
        cols = {row[1]: row for row in conn.execute(
            text("PRAGMA table_info(recycle_bin)")
        ).fetchall()}
        if "file_record_id" not in cols:
            return  # 表不存在（首次建库由 create_tables 按新模型建表）
        if not cols["file_record_id"][3]:  # notnull=0 → 已迁移过
            return
        # SQLAlchemy 2.x 无 executescript，逐条执行（SQLite DDL 支持事务）
        conn.execute(text(
            "CREATE TABLE recycle_bin_new ("
            "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
            "document_id INTEGER, "
            "file_record_id INTEGER, "
            "reason VARCHAR(16) NOT NULL, "
            "origin_rel_path VARCHAR(255), "
            "deleted_by INTEGER, "
            "deleted_at DATETIME NOT NULL, "
            "restored_at DATETIME, "
            "restored_by INTEGER, "
            "purged_at DATETIME, "
            "purged_by INTEGER, "
            "CONSTRAINT ck_recycle_bin_reason CHECK (reason IN ('replaced','archived','deleted')), "
            "FOREIGN KEY(document_id) REFERENCES documents (id), "
            "FOREIGN KEY(file_record_id) REFERENCES file_records (id), "
            "FOREIGN KEY(deleted_by) REFERENCES users (id), "
            "FOREIGN KEY(restored_by) REFERENCES users (id), "
            "FOREIGN KEY(purged_by) REFERENCES users (id))"
        ))
        conn.execute(text(
            "INSERT INTO recycle_bin_new "
            "(id, document_id, file_record_id, reason, origin_rel_path, "
            " deleted_by, deleted_at, restored_at, restored_by, purged_at, purged_by) "
            "SELECT id, document_id, file_record_id, reason, origin_rel_path, "
            " deleted_by, deleted_at, restored_at, restored_by, purged_at, purged_by "
            "FROM recycle_bin"
        ))
        conn.execute(text("DROP TABLE recycle_bin"))
        conn.execute(text("ALTER TABLE recycle_bin_new RENAME TO recycle_bin"))
        conn.execute(text(
            "CREATE INDEX ix_recycle_bin_document_id ON recycle_bin (document_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_recycle_bin_status ON recycle_bin (deleted_at)"
        ))
        conn.execute(
            text(
                "UPDATE sqlite_sequence SET name='recycle_bin', "
                "seq=(SELECT COALESCE(max(id),0) FROM recycle_bin) "
                "WHERE name IN ('recycle_bin','recycle_bin_new')"
            )
        )
        conn.commit()
        logger.info("已迁移 recycle_bin：file_record_id 可空（无文件资料删除留档）")


def _migrate_file_sharding(db: Session) -> None:
    """一次性迁移：data/documents|notices 下扁平文件改为二级分片目录（内部名前 2 位）。

    旧布局：documents/<uuid>.pdf（storage_rel_path 单层目录）
    新布局：documents/ab/<uuid>.pdf（路径由 file_service.storage_rel_path 纯函数派生）
    同时把回收站记录的 origin_rel_path 同步为分片路径（恢复时文件回到分片目录）。
    幂等：已分片（3 段）记录跳过；磁盘文件缺失时仅更新引用（容错）。
    """
    from sqlalchemy import or_

    from app.models.backup import RecycleBin
    from app.models.document import FileRecord
    from app.services.file_service import abs_path

    rows = db.execute(
        select(FileRecord).where(
            or_(
                FileRecord.storage_rel_path.like("documents/%"),
                FileRecord.storage_rel_path.like("notices/%"),
            )
        )
    ).scalars().all()

    moved = 0
    for rec in rows:
        parts = rec.storage_rel_path.split("/")
        if len(parts) != 2 or parts[0] not in ("documents", "notices"):
            continue  # 空/异常/已分片（3 段）不处理
        name = parts[1]
        new_rel = f"{parts[0]}/{name[:2]}/{name}"
        old = abs_path(rec.storage_rel_path)
        new = abs_path(new_rel)
        if old.exists() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        rec.storage_rel_path = new_rel
        moved += 1

    rb_rows = db.execute(select(RecycleBin)).scalars().all()
    for e in rb_rows:
        if not e.origin_rel_path:
            continue
        parts = e.origin_rel_path.split("/")
        if len(parts) == 2 and parts[0] in ("documents", "notices"):
            e.origin_rel_path = f"{parts[0]}/{parts[1][:2]}/{parts[1]}"

    if moved:
        db.commit()
        logger.info("已迁移 %d 个文件到分片目录（documents|notices/<2位>/<名>）", moved)
    elif rb_rows:
        db.commit()


def _migrate_notices_table(db: Session) -> None:
    """一次性迁移：notices 表去掉 notice_type 固定六类 CHECK 约束 + 新增 deadline_at 列。

    SQLite 无法 ALTER 删除约束，采用"重建表"（原生 sqlite3 连接，临时关闭外键，
    规避 SQLAlchemy 事务内 PRAGMA 失效与 DROP 被引用表失败；notices_new 结构含
    全部既有列 + deadline_at，不含 ck_notices_type CHECK，索引按原样重建）。
    幂等：table_info 已含 deadline_at 则跳过。
    """
    import sqlite3

    engine = _bind_engine(db)
    db_path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if db_path is None:
        logger.warning("非 SQLite 数据库，跳过 notices 重建迁移")
        return
    raw = sqlite3.connect(db_path)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        cols = {row[1] for row in raw.execute("PRAGMA table_info(notices)").fetchall()}
        if "deadline_at" in cols:
            return  # 已迁移
        raw.executescript(
            """
            DROP TABLE IF EXISTS notices_new;
            CREATE TABLE notices_new (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                title VARCHAR(255) NOT NULL,
                content TEXT,
                notice_type VARCHAR(32) NOT NULL,
                -- 2026-11-04：草稿状态下线，中间态默认值跟着改；紧随其后的
                -- migrations/011 会把整表 CHECK 收窄为 published/cancelled。
                status VARCHAR(16) NOT NULL DEFAULT 'published',
                requires_confirmation BOOLEAN NOT NULL DEFAULT 0,
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
                    CHECK (status IN ('published','cancelled')),
                FOREIGN KEY(file_id) REFERENCES file_records (id),
                FOREIGN KEY(created_by) REFERENCES users (id),
                FOREIGN KEY(updated_by) REFERENCES users (id)
            );
            INSERT INTO notices_new
                (id, title, content, notice_type, status, requires_confirmation,
                 published_at, effective_at, file_id, created_by, updated_by,
                 updated_at, modified_reason, cancel_reason, auto_expired_docs,
                 created_at)
                SELECT id, title, content, notice_type, status, requires_confirmation,
                       published_at, effective_at, file_id, created_by, updated_by,
                       updated_at, modified_reason, cancel_reason, auto_expired_docs,
                       created_at
                FROM notices;
            DROP TABLE notices;
            ALTER TABLE notices_new RENAME TO notices;
            CREATE INDEX ix_notices_status ON notices (status);
            CREATE INDEX ix_notices_type ON notices (notice_type);
            CREATE INDEX ix_notices_published_at ON notices (published_at);
            CREATE INDEX ix_notices_effective_at ON notices (effective_at);
            """
        )
        raw.execute(
            "UPDATE sqlite_sequence SET name='notices', "
            "seq=(SELECT COALESCE(max(id),0) FROM notices) "
            "WHERE name IN ('notices','notices_new')"
        )
        raw.commit()
        logger.info("已迁移 notices：notice_type 去固定六类 CHECK + 新增 deadline_at")
    finally:
        raw.close()


def _seed_notice_types(db: Session) -> None:
    """首次初始化 seeds 默认通知类型（仅当 notice_types 表为空时）。"""
    from app.models.notice import DEFAULT_NOTICE_TYPES, NoticeType

    existing = db.execute(select(NoticeType)).scalars().all()
    if existing:
        return
    for i, name in enumerate(DEFAULT_NOTICE_TYPES):
        db.add(NoticeType(name=name, sort_order=i, is_active=True))
    db.commit()
    logger.info("已种子默认通知类型: %s", ", ".join(DEFAULT_NOTICE_TYPES))


def _migrate_allow_download(db: Session) -> None:
    """增量迁移：documents 表补加 allow_download 列（普通用户下载开关，默认仅预览）。"""
    engine = _bind_engine(db)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(
            text("PRAGMA table_info(documents)")
        ).fetchall()}
        if "allow_download" not in cols:
            conn.execute(text(
                "ALTER TABLE documents ADD COLUMN allow_download BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
            logger.info("已迁移 documents：新增 allow_download 列")


def _migrate_users_token_version(db: Session) -> None:
    """增量迁移：users 表补加 token_version 列（单点登录，2026-09-03 产品决策）。

    登录时自增 → 旧令牌 payload.ver 与列值不等即 401，实现「一处登录，
    多端下线」。默认 0 兼容存量行。
    """
    engine = _bind_engine(db)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(
            text("PRAGMA table_info(users)")
        ).fetchall()}
        if "token_version" not in cols:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"
            ))
            conn.commit()
            logger.info("已迁移 users：新增 token_version 列（单点登录）")


def _migrate_purge_soft_deleted_categories(db: Session) -> None:
    """一次性数据迁移（2026-09-03 产品决策：分类删除=直接物理删）。

    清理旧「软删除」阶段遗留的 is_active=0 分类行：
    - 零引用（任何状态子分类/任何资料引用含归档均为零）→ 物理删除
    - 仍有引用的行无法安全删除（归档资料详情需显示历史分类名），保留为
      不可见行（列表已按 is_active=1 过滤），不占用名字（部分唯一索引
      仅对 is_active=1 生效）
    """
    dead = db.execute(
        select(Category).where(Category.is_active.is_(False))
    ).scalars().all()
    purged = 0
    kept = 0
    for cat in dead:
        child_count = db.execute(
            select(func.count(Category.id)).where(Category.parent_id == cat.id)
        ).scalar_one()
        doc_count = db.execute(
            select(func.count(Document.id)).where(Document.category_id == cat.id)
        ).scalar_one()
        if child_count == 0 and doc_count == 0:
            db.delete(cat)
            purged += 1
        else:
            kept += 1
    if purged or kept:
        db.commit()
        logger.info("已迁移 categories：清理历史软删除行 %d 条（保留有引用的 %d 条）", purged, kept)


def _migrate_delete_restored_backups(db: Session) -> None:
    """一次性数据迁移（2026-09-03 产品决策：恢复后直接删已用备份）。

    清理历史遗留 status="restored" 的备份：该备份已被消费、无再次恢复价值，
    记录行与备份目录（仅白名单 backups/backup_* 命名）一并删除。
    """
    from app.models.backup import BackupRecord

    rows = db.execute(
        select(BackupRecord).where(BackupRecord.status == "restored")
    ).scalars().all()
    if not rows:
        return
    removed = 0
    data_dir = get_settings().data_dir
    for r in rows:
        if r.backup_path:
            bdir = data_dir / r.backup_path
            if bdir.is_dir() and bdir.name.startswith("backup_"):
                shutil.rmtree(bdir, ignore_errors=True)
        db.delete(r)
        removed += 1
    db.commit()
    logger.info("已迁移 backup_records：清理已用于恢复的历史备份 %d 条（含目录）", removed)


def _migrate_reclaim_orphan_backups(db: Session) -> None:
    """一次性数据迁移（2026-09-15 缺陷修复：认领孤儿备份目录）。

    背景：恢复备份会覆盖整个 app.db，快照之后创建的备份记录随回滚消失、
    磁盘目录仍在 → 变成列表看不见、保留策略也管不到的孤儿（§28 数据安全）。
    处置口径为「认领，不删除」：重建记录使其重回列表并受保留策略管理。
    幂等——已登记的目录不会重复认领。
    """
    from app.services import backup_service

    try:
        result = backup_service.reconcile_orphan_backups(db)
    except Exception:  # noqa: BLE001  启动迁移失败不应阻断服务启动
        logger.warning("孤儿备份目录认领失败（下次启动重试）", exc_info=True)
        return
    if result["reclaimed"]:
        logger.info("已认领孤儿备份目录 %d 个: %s", len(result["reclaimed"]), result["reclaimed"])


def init_db() -> None:
    """完整初始化：建表 + 初始管理员 + 增量迁移。幂等，可安全重复调用。"""
    settings = get_settings()
    settings.ensure_dirs()
    create_tables()
    with SessionLocal() as db:
        _migrate_users_role(db)
        _migrate_notices(db)
        _migrate_recycle_bin(db)
        _migrate_file_sharding(db)
        _migrate_notices_table(db)
        _migrate_allow_download(db)
        _migrate_users_token_version(db)
        _migrate_purge_soft_deleted_categories(db)
        _migrate_delete_restored_backups(db)
        _migrate_reclaim_orphan_backups(db)
        _seed_notice_types(db)
        seed_initial_admin(db)


if __name__ == "__main__":
    init_db()
    print("数据库初始化完成")