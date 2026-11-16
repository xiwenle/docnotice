"""备份/恢复服务（docs/DESIGN.md §10、API.md §10、项目规范 §28）。

- 备份（full）：data/backups/backup_<ts>/ 下 app.db（SQLite backup API 一致快照）
  + files/{documents,notices,recycle_bin,uploads}/ + manifest.json（db_sha256 + 每文件 sha256）
- 备份目录**不包含自身**（只复制这四个业务目录，绝不复制 backups/）
- 恢复：先自动生成"当前状态"安全备份 → 校验 manifest + sha256 全量 → 临时目录就绪
  → 数据库在线备份回写（不换文件 inode）+ 业务目录切换（旧目录保留为 *.pre_<ts>）
  → 记录 restored
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import maintenance
from app.core.config import get_settings
from app.core.disk_health import DiskSpaceError, assert_disk_headroom
from app.db.session import total_count
from app.core.errors import AppError
from app.models.backup import BackupRecord
from app.models.user import User
from app.schemas.common import page_meta
from app.services import audit_service

logger = logging.getLogger(__name__)

settings = get_settings()

SCHEMA_VERSION = 1
# 参与备份的业务目录（不含 backups/ 自身）
BACKUP_SUBDIRS = ("documents", "notices", "recycle_bin", "uploads")

# ---- 备份/恢复互斥锁（2026-10-26 对抗性审计发现）----
# 恢复是「换库 + 切业务目录」的多步破坏性操作。此前**没有任何互斥**：
# 实测两个并发 restore 都返回 200 —— 两个恢复交错执行（各切各的目录、
# 各写各的文件），最终数据处于混合状态（部分来自 A、部分来自 B）。
# 这是本模块最危险的一类缺陷：静默数据损坏，且事后难以归因。
#
# 为什么用【非阻塞】锁（acquire(blocking=False)）而不是排队：
# 排队执行的第二个恢复醒来时，面对的是"备份已被第一个恢复消费"的现实
# （恢复后该备份目录与记录行已被删除），语义上已无意义，且会让 HTTP 请求
# 长时间挂起。直接拒绝（409 CONFLICT）语义更清晰：一次只能恢复一个。
#
# 作用域说明：这是**进程内**锁，依赖"单进程部署"这一既有前提
# （DEPLOY.md §4 已说明：进程内 TTL 缓存与自动备份调度都假定每份数据只有
# 一个进程，多 worker 会导致缓存失效失灵与重复备份调度）。若将来要改多
# worker 部署，本锁必须换成跨进程的文件锁，否则互斥会失效。
_restore_lock = threading.Lock()


def _acquire_restore_lock() -> None:
    """获取恢复互斥锁；已有恢复在执行则立即拒绝（409）。"""
    if not _restore_lock.acquire(blocking=False):
        raise AppError(
            "CONFLICT",
            "已有恢复操作正在执行，请等待其完成后再试（恢复为破坏性操作，不支持并发）",
        )


def _release_restore_lock() -> None:
    """释放恢复互斥锁（即使恢复失败也必须释放，否则系统永久无法恢复）。"""
    try:
        _restore_lock.release()
    except RuntimeError:  # pragma: no cover - 未持有时释放，防御性
        pass

# 保留清理的目录名白名单（与 _new_backup_dir 命名严格对应，其余路径一律不碰）
# pre 时间戳形如 20260830_153045123456（含下划线）
_BACKUP_DIR_RE = re.compile(r"backup_\d{8}_\d{6}(_\d+)?$")
# 可选 -wal/-shm 尾缀：中断残留的 sidecar 与主快照同组计数、整组清理
# （2026-10-21；恢复已改为在线备份，不再产生此类文件）
_PRE_DIR_RE = re.compile(r"^(.+)\.pre_(\d{8}_\d{6,})(?:-wal|-shm)?$")
# 备份目录名内的时间戳（用于从目录名还原创建时间）
_BACKUP_TS_RE = re.compile(r"backup_(\d{8})_(\d{6})")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def reconcile_orphan_backups(
    db: Session,
    *,
    current_user: Optional[User] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """认领孤儿备份目录（2026-09-15 缺陷修复）。

    背景（实测复现）：恢复备份会把**整个 app.db 覆盖回快照时点**，因此"快照之后
    创建的备份"其记录行会随回滚消失，但磁盘目录仍在 → 变成列表里看不见、保留
    策略也管不到的孤儿目录（演示库已积累 2 个，其中 1 个还是完整可恢复的备份）。
    这不是删除逻辑的问题，是"目录与库记录两份真相"必然的漂移。

    处置口径：**认领，不删除**（§28「宁多留不误删」）。只要目录里有可用的
    app.db，就从 manifest.json 重建一条 completed 记录，使其重新出现在备份列表、
    重新受保留策略管理、也可再次用于恢复。

    安全边界（只做加法，绝不删数据）：
    - 只扫描 backups/ 下严格匹配 backup_<ts> 白名单的目录
    - 已被任何记录引用的目录不动
    - manifest.json 缺失/损坏 → 跳过（保守：宁可少认领）
    - app.db 缺失或为空 → 跳过（无法用于恢复，认领只会误导出「可恢复」假象）
    - 认领动作写审计 backup_reconcile，可追溯
    """
    from app.models.backup import BackupRecord

    expected_root = (settings.data_dir / "backups").resolve()
    if not expected_root.is_dir():
        return {"reclaimed": [], "skipped": []}

    known: set[str] = {
        Path(p).name
        for p in db.execute(select(BackupRecord.backup_path)).scalars() if p
    }

    reclaimed: list[dict] = []
    skipped: list[dict] = []
    for d in sorted(expected_root.iterdir()):
        if not d.is_dir() or d.name in known:
            continue
        if _BACKUP_DIR_RE.fullmatch(d.name) is None:
            continue  # 白名单外（junk/stage 等）一律不碰

        # app.db 必须存在且非空，否则认领出来也不能恢复
        db_file = d / "app.db"
        if not db_file.is_file() or db_file.stat().st_size == 0:
            skipped.append({"dir": d.name, "why": "缺少可用 app.db"})
            continue

        # manifest 可选但必须可解析；缺失时按"仅库"降级登记（不谎报文件数）
        manifest: dict = {}
        manifest_file = d / "manifest.json"
        if manifest_file.is_file():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                skipped.append({"dir": d.name, "why": "manifest.json 损坏"})
                continue
        btype = manifest.get("backup_type")
        if btype not in ("full", "database"):
            # 无 manifest 无法判断类型；有 files/ 目录即有业务文件 → full
            btype = "full" if (d / "files").is_dir() else "database"

        created_at = None
        m = _BACKUP_TS_RE.match(d.name)
        if m:
            try:
                created_at = datetime.strptime(
                    f"{m.group(1)}{m.group(2)}", "%Y%m%d%H%M%S"
                )
            except ValueError:
                created_at = None

        rel = f"backups/{d.name}"
        row = BackupRecord(
            backup_type=btype,
            backup_path=rel,
            manifest_path=f"{rel}/manifest.json" if manifest_file.is_file() else None,
            size_bytes=sum(p.stat().st_size for p in d.rglob("*") if p.is_file()),
            db_sha256=manifest.get("db_sha256") or _sha256_file(db_file),
            file_count=manifest.get("file_count"),
            status="completed",
            source="manual",
            note="（自动认领：恢复回滚导致记录丢失、目录仍在）",
            created_by=current_user.id if current_user else None,
            created_at=created_at or datetime.now(),
        )
        db.add(row)
        db.flush()
        reclaimed.append({"id": row.id, "path": rel})

    if reclaimed:
        audit_service.log(
            db,
            action=audit_service.ACTION_BACKUP_RECONCILE,
            user=current_user,
            object_type="backup",
            object_label="reconcile",
            detail={
                "reclaimed": reclaimed,
                "skipped": skipped,
                "auto_cleanup": True,
            },
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        logger.info("认领孤儿备份目录 %d 个: %s", len(reclaimed), reclaimed)
    return {"reclaimed": reclaimed, "skipped": skipped}


def _new_backup_dir(db: Session) -> Path:
    """生成不冲突的备份目录（同秒并发/重试时追加序号，绝不覆盖已有备份）。

    查重必须同时看磁盘与 backup_records 全部记录（含 purged 墓碑）：
    保留清理删除目录后若允许复用其名字，purged 记录的 backup_path 会
    指向新备份的活目录（发现于保留策略测试）。
    """
    backup_root = settings.backups_dir
    backup_root.mkdir(parents=True, exist_ok=True)
    taken = {
        Path(p).name
        for p in db.execute(select(BackupRecord.backup_path)).scalars()
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = backup_root / f"backup_{ts}"
    n = 1
    while candidate.exists() or candidate.name in taken:
        candidate = backup_root / f"backup_{ts}_{n}"
        n += 1
    candidate.mkdir()
    return candidate


def _check_manifest_rel(rel: str) -> None:
    """校验 manifest 相对路径：必须位于业务子目录内且防穿越（§11/§28）。

    防止被篡改的 manifest 在恢复时把文件写出 DATA_DIR（如 ../../ 路径）。
    """
    p = rel.replace("\\", "/")
    parts = p.split("/")
    if (
        len(parts) < 2
        or parts[0] not in BACKUP_SUBDIRS
        or ".." in parts
        or any(not seg for seg in parts)
        or "\x00" in rel
    ):
        raise AppError("INTERNAL_ERROR", f"备份清单包含非法路径: {rel}")


def _manifest_paths(manifest: dict) -> list[str]:
    """取 manifest 文件清单相对路径（dict 形如 {rel_path: sha256}）。"""
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        raise AppError("INTERNAL_ERROR", "备份清单 files 字段格式非法")
    rels = []
    for rel in files:
        if not isinstance(rel, str):
            raise AppError("INTERNAL_ERROR", "备份清单包含非法文件条目")
        _check_manifest_rel(rel)
        rels.append(rel)
    return rels


def _copy_tree(src: Path, dst: Path) -> list[tuple[str, str]]:
    """复制目录内容到 dst，返回 [(相对路径, sha256)]。"""
    files: list[tuple[str, str]] = []
    if not src.exists():
        return files
    for p in sorted(src.rglob("*")):
        if p.is_file():
            rel = p.relative_to(src).as_posix()
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            files.append((rel, _sha256_file(p)))
    return files


def _copy_db_snapshot(src_db: Path, dst_db: Path) -> str:
    """用 SQLite backup API 复制一致快照，返回**快照文件** sha256。

    注意：WAL 模式下源库主文件可能落后于 -wal 中已提交的事务，backup API
    读取的是“主文件 + WAL”的一致视图；因此指纹必须取快照文件（dst）而非源文件，
    否则恢复时自校验必然失败（发现并修复于 t28 评审）。
    """
    src_conn = sqlite3.connect(str(src_db))
    dst_conn = sqlite3.connect(str(dst_db))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    return _sha256_file(dst_db)


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------
def _restore_db_in_place(src_db: Path, dst_db: Path) -> None:
    """用 SQLite online backup 把备份库原地写回运行中的数据库。

    不能删除/改名/替换 app.db 及其 -wal/-shm：Windows 上只要还有连接持有句柄，
    unlink 抛 WinError 32、os.replace 抛 WinError 5。backup API 在 SQLite 层
    复制页并复用现有 inode/WAL，已有连接在新事务中读到恢复后的内容。
    """
    src_conn = sqlite3.connect(str(src_db))
    dst_conn = sqlite3.connect(str(dst_db), timeout=30)
    try:
        dst_conn.execute("PRAGMA busy_timeout=30000")
        src_conn.backup(dst_conn)
    except sqlite3.Error as exc:
        raise AppError("INTERNAL_ERROR", f"数据库恢复失败（SQLite）: {exc}") from exc
    finally:
        dst_conn.close()
        src_conn.close()


def _post_restore_db_check(db_path: Path) -> None:
    """恢复后初始化库文件：强制 WAL 模式（与应用连接 PRAGMA 一致）+ quick_check 自检。"""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            head = conn.execute("PRAGMA quick_check").fetchone()
            if not head or head[0] != "ok":
                raise AppError(
                    "INTERNAL_ERROR", "数据库恢复后完整性校验失败（quick_check）"
                )
        finally:
            conn.close()
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError("INTERNAL_ERROR", f"数据库恢复后初始化失败: {exc}") from exc


def create_backup(
    db: Session,
    *,
    current_user: User,
    backup_type: str = "full",
    note: Optional[str] = None,
    source: str = "manual",
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    _inside_restore: bool = False,
) -> BackupRecord:
    """创建备份（full=库+文件；database=仅库）。失败登记 status='failed'。"""
    if backup_type not in ("full", "database"):
        raise AppError("VALIDATION_ERROR", "backup_type 仅支持 full / database")
    if source not in ("manual", "auto"):
        raise AppError("VALIDATION_ERROR", "source 仅支持 manual / auto")

    # 2026-10-29 修复：备份此前**不与恢复互斥**。恢复正在跑时发起备份，
    # 快照会读到"半个恢复"的中间状态（业务目录已被改名/部分拷贝），
    # 产出的备份集不可用于恢复，却显示成功——静默污染备份链。
    # 复用同一把锁：恢复在跑时备份直接 409（不排队——恢复完成后备份已无意义）。
    #
    # `_inside_restore=True` 是恢复流程自己的安全备份（在锁内调用），
    # 必须放行——否则恢复永远无法开始（自锁）。
    if not _inside_restore and _restore_lock.locked():
        raise AppError(
            "CONFLICT",
            "恢复操作正在执行，此时不能创建备份（会拍到中间状态）。请等待恢复完成",
        )

    settings.ensure_dirs()

    # 磁盘闸门：空间不足时**在创建备份目录之前**拦下。
    # 顺序很关键——备份是写放大操作，若先建目录再检查，写到一半失败会留下
    # 半个备份目录（占空间、又不是可用的恢复点），把"快满了"变成"彻底满了"。
    try:
        disk = assert_disk_headroom()
    except DiskSpaceError as exc:
        logger.error("备份被拒绝：%s", exc)
        raise AppError(
            "VALIDATION_ERROR",
            f"磁盘可用空间不足，已拒绝创建备份（{exc.health.free_mb:.0f}MB 可用）。"
            "请清理磁盘后重试",
        ) from exc
    logger.info("备份前磁盘巡检：%s", disk.describe())

    backup_dir = _new_backup_dir(db)
    backup_path = f"backups/{backup_dir.name}"

    record = BackupRecord(
        backup_type=backup_type,
        backup_path=backup_path,
        status="completed",
        source=source,
        note=note,
        created_by=current_user.id,
    )
    try:
        files_dir = backup_dir / "files"
        # 即使没有任何业务文件也必须创建 files/ 目录（否则 restore 的
        # copytree 对缺失目录崩溃 —— 船长抽查发现的边界 Bug）
        files_dir.mkdir(parents=True, exist_ok=True)
        manifest_files: dict[str, str] = {}

        # 1) 数据库快照
        db_sha = _copy_db_snapshot(settings.database_path, backup_dir / "app.db")
        record.db_sha256 = db_sha

        # 2) 业务文件（绝不复制 backups/ 自身）
        if backup_type == "full":
            for sub in BACKUP_SUBDIRS:
                src = settings.data_dir / sub
                for rel, sha in _copy_tree(src, files_dir / sub):
                    manifest_files[f"{sub}/{rel}"] = sha
        record.file_count = len(manifest_files)

        # 3) manifest.json
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "backup_type": backup_type,
            "created_at": record.created_at.isoformat() if record.created_at else datetime.now().isoformat(),
            "created_by": current_user.username,
            "db_sha256": db_sha,
            "file_count": len(manifest_files),
            "files": manifest_files,
            "note": note,
        }
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        record.manifest_path = f"backups/{backup_dir.name}/manifest.json"
        record.size_bytes = sum(
            p.stat().st_size for p in backup_dir.rglob("*") if p.is_file()
        )
        db.add(record)
        db.flush()

        audit_service.log(
            db,
            action=audit_service.ACTION_BACKUP_CREATE,
            user=current_user,
            object_type="backup",
            object_id=record.id,
            object_label=backup_dir.name,
            detail={
                "backup_type": backup_type,
                "file_count": record.file_count,
                "size_bytes": record.size_bytes,
                "db_sha256": db_sha,
            },
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        db.refresh(record)
        return record
    except Exception as exc:  # noqa: BLE001
        # 失败也必须留痕（API.md §10：失败 → status='failed' + 审计 backup_create）
        record.status = "failed"
        db.add(record)
        db.flush()
        audit_service.log(
            db,
            action=audit_service.ACTION_BACKUP_CREATE,
            user=current_user,
            object_type="backup",
            object_id=record.id,
            object_label=backup_dir.name,
            detail={
                "backup_type": backup_type,
                "status": "failed",
                "error": str(exc)[:500],
            },
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        shutil.rmtree(backup_dir, ignore_errors=True)
        logger.exception("备份失败")
        raise AppError("INTERNAL_ERROR", f"备份失败: {exc}")


def list_backups(
    db: Session,
    *,
    backup_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = select(BackupRecord).order_by(BackupRecord.id.desc())
    if backup_type:
        stmt = stmt.where(BackupRecord.backup_type == backup_type)
    total = total_count(db, stmt)
    rows = db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    usernames = _usernames_map(db, list(rows))
    return {
        "items": [_record_to_dict(r, usernames) for r in rows],
        **page_meta(total, page, page_size),
    }


def _usernames_map(db: Session, records: "list[BackupRecord]") -> dict[int, str]:
    """批量解析 created_by/restored_by 用户 id → 用户名（前端展示用；
    用户被删除时无对应行，前端回退显示原 id）。"""
    ids = set()
    for r in records:
        if r.created_by:
            ids.add(r.created_by)
        if r.restored_by:
            ids.add(r.restored_by)
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.username).where(User.id.in_(ids))).all()
    return {int(uid): str(uname) for uid, uname in rows}


def _record_to_dict(r: BackupRecord, usernames: Optional[dict[int, str]] = None) -> dict:
    names = usernames or {}
    return {
        "id": r.id,
        "backup_type": r.backup_type,
        "backup_path": r.backup_path,
        "manifest_path": r.manifest_path,
        "size_bytes": r.size_bytes,
        "db_sha256": r.db_sha256,
        "file_count": r.file_count,
        "status": r.status,
        "source": r.source,
        "note": r.note,
        "created_by": r.created_by,
        "created_by_username": names.get(r.created_by) if r.created_by else None,
        "created_at": r.created_at,
        "restored_at": r.restored_at,
        "restored_by": r.restored_by,
        "restored_by_username": names.get(r.restored_by) if r.restored_by else None,
    }


def get_backup(db: Session, backup_id: int) -> dict:
    r = db.get(BackupRecord, backup_id)
    if r is None:
        raise AppError("NOT_FOUND", "备份记录不存在")
    data = _record_to_dict(r, _usernames_map(db, [r]))
    # manifest 摘要
    if r.manifest_path:
        manifest_file = settings.data_dir / r.manifest_path
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                data["manifest"] = {
                    "schema_version": manifest.get("schema_version"),
                    "backup_type": manifest.get("backup_type"),
                    "created_at": manifest.get("created_at"),
                    "created_by": manifest.get("created_by"),
                    "db_sha256": manifest.get("db_sha256"),
                    "file_count": manifest.get("file_count"),
                    "files": manifest.get("files", {}),
                }
            except Exception:  # noqa: BLE001
                data["manifest"] = None
    return data


def _resolve_backup_dir(record: BackupRecord) -> Path:
    """把记录中的相对路径解析到 backups/ 内，拒绝越界与非备份目录名。"""
    backup_dir = (settings.data_dir / record.backup_path).resolve()
    expected_root = settings.backups_dir.resolve()
    if (
        not backup_dir.is_relative_to(expected_root)
        or backup_dir == expected_root
        or _BACKUP_DIR_RE.fullmatch(backup_dir.name) is None
    ):
        raise AppError("VALIDATION_ERROR", "备份路径非法（路径遍历攻击）")
    return backup_dir


def _sqlite_integrity_check(db_path: Path) -> tuple[bool, str]:
    """只读执行 SQLite PRAGMA integrity_check，不修改备份数据库。"""
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
        values = [str(row[0]) for row in rows if row]
        text = "; ".join(values[:10]) or "无返回结果"
        return values == ["ok"], text
    except Exception as exc:  # noqa: BLE001
        return False, f"SQLite integrity_check 执行失败: {exc}"


def _inspect_backup(backup_dir: Path, record: BackupRecord) -> tuple[dict, dict]:
    """执行备份只读体检，返回 (结构化结果, manifest)。

    校验项固定为 manifest、数据库 sha256、清单文件 sha256、SQLite
    integrity_check。恢复与独立校验接口共用同一口径；本函数绝不写备份目录。
    """
    errors: list[str] = []
    manifest: dict = {}
    manifest_errors: list[str] = []
    manifest_file = backup_dir / "manifest.json"

    if not backup_dir.is_dir():
        manifest_errors.append("备份目录不存在")
    elif not manifest_file.is_file():
        manifest_errors.append("缺失 manifest.json")
    else:
        try:
            decoded = json.loads(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                manifest_errors.append("manifest.json 顶层必须是对象")
            else:
                manifest = decoded
        except Exception as exc:  # noqa: BLE001
            manifest_errors.append(f"manifest.json 解析失败: {exc}")

    if manifest:
        if manifest.get("schema_version") != SCHEMA_VERSION:
            manifest_errors.append(
                f"manifest schema_version 不支持: {manifest.get('schema_version')!r}"
            )
        if manifest.get("backup_type") not in ("full", "database"):
            manifest_errors.append("manifest backup_type 非法")
        elif manifest.get("backup_type") != record.backup_type:
            manifest_errors.append("manifest backup_type 与备份记录不一致")
        if not _SHA256_RE.fullmatch(str(manifest.get("db_sha256") or "")):
            manifest_errors.append("manifest db_sha256 缺失或格式非法")
        files = manifest.get("files")
        if not isinstance(files, dict):
            manifest_errors.append("manifest files 字段格式非法")
        else:
            file_count = manifest.get("file_count")
            if not isinstance(file_count, int) or file_count != len(files):
                manifest_errors.append("manifest file_count 与 files 数量不一致")

    checks: dict[str, dict] = {
        "manifest": {
            "ok": not manifest_errors,
            "message": "manifest 校验通过" if not manifest_errors else "manifest 校验失败",
            "errors": manifest_errors,
        }
    }
    errors.extend(manifest_errors)

    db_file = backup_dir / "app.db"
    expected_manifest = str(manifest.get("db_sha256") or "")
    expected_record = str(record.db_sha256 or "")
    actual_db_sha: Optional[str] = None
    db_errors: list[str] = []
    if not db_file.is_file():
        db_errors.append("备份缺失 app.db")
    else:
        try:
            actual_db_sha = _sha256_file(db_file)
        except Exception as exc:  # noqa: BLE001
            db_errors.append(f"数据库 sha256 计算失败: {exc}")
        if not expected_manifest:
            db_errors.append("manifest 未提供 db_sha256")
        elif actual_db_sha and actual_db_sha != expected_manifest:
            db_errors.append("数据库 sha256 与 manifest 不一致")
        if not expected_record:
            db_errors.append("备份记录未提供 db_sha256")
        elif actual_db_sha and actual_db_sha != expected_record:
            db_errors.append("数据库 sha256 与备份记录不一致")
    checks["db_sha256"] = {
        "ok": not db_errors,
        "message": "数据库 sha256 校验通过" if not db_errors else "数据库 sha256 校验失败",
        "actual": actual_db_sha,
        "expected_manifest": expected_manifest or None,
        "expected_record": expected_record or None,
        "errors": db_errors,
    }
    errors.extend(db_errors)

    files = manifest.get("files")
    file_errors: list[str] = []
    checked_files = 0
    if not isinstance(files, dict):
        file_errors.append("manifest files 字段格式非法，无法校验文件")
    else:
        for rel, expected_sha in files.items():
            checked_files += 1
            if not isinstance(rel, str) or not isinstance(expected_sha, str):
                file_errors.append(f"非法条目: {rel!r}")
                continue
            try:
                _check_manifest_rel(rel)
            except AppError as exc:
                file_errors.append(str(exc))
                continue
            p = backup_dir / "files" / rel
            if not p.is_file():
                file_errors.append(f"缺失: {rel}")
                continue
            if not _SHA256_RE.fullmatch(expected_sha):
                file_errors.append(f"sha256 格式非法: {rel}")
                continue
            try:
                if _sha256_file(p) != expected_sha:
                    file_errors.append(f"校验失败: {rel}")
            except Exception as exc:  # noqa: BLE001
                file_errors.append(f"读取失败: {rel} ({exc})")
    checks["file_sha256"] = {
        "ok": not file_errors,
        "message": "文件 sha256 校验通过" if not file_errors else "文件 sha256 校验失败",
        "checked": checked_files,
        "failed": file_errors,
    }
    errors.extend(file_errors)

    if db_file.is_file():
        integrity_ok, integrity_text = _sqlite_integrity_check(db_file)
    else:
        integrity_ok, integrity_text = False, "备份缺失 app.db"
    checks["sqlite_integrity_check"] = {
        "ok": integrity_ok,
        "message": "SQLite 完整性校验通过" if integrity_ok else "SQLite 完整性校验失败",
        "result": integrity_text,
    }
    if not integrity_ok:
        errors.append(f"SQLite integrity_check 失败: {integrity_text}")

    return {
        "ok": all(check["ok"] for check in checks.values()),
        "checks": checks,
        "errors": errors,
    }, manifest


def _verify_backup(backup_dir: Path, record: BackupRecord) -> dict:
    """恢复前校验；任一项失败即抛错，保证不进入写数据阶段。"""
    result, manifest = _inspect_backup(backup_dir, record)
    if not result["ok"]:
        raise AppError(
            "INTERNAL_ERROR",
            "备份校验失败，恢复中止：" + "; ".join(result["errors"][:10]),
        )
    return manifest


def verify_backup(
    db: Session,
    *,
    current_user: User,
    backup_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """独立校验备份（不恢复）：manifest + db/file sha256 + SQLite 完整性。"""
    record = db.get(BackupRecord, backup_id)
    if record is None:
        raise AppError("NOT_FOUND", "备份记录不存在")
    backup_dir = _resolve_backup_dir(record)
    result, _ = _inspect_backup(backup_dir, record)
    checked_at = datetime.now()
    response = {
        "id": record.id,
        "backup_type": record.backup_type,
        "backup_path": record.backup_path,
        "checked_at": checked_at,
        **result,
    }
    audit_service.log(
        db,
        action=audit_service.ACTION_BACKUP_VERIFY,
        user=current_user,
        object_type="backup",
        object_id=record.id,
        object_label=record.backup_path,
        detail={
            "ok": result["ok"],
            "errors": result["errors"],
            "checks": result["checks"],
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return response

def restore_backup(
    db: Session,
    *,
    current_user: User,
    backup_id: int,
    confirm: bool = False,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """恢复备份入口（互斥保护）。

    本函数只负责【互斥】与【委派】：真正的恢复流程在 `_restore_backup_impl`。
    分离的原因：恢复流程有 6 个步骤、多个 return/raise 点，锁必须在**最外层**
    用 try/finally 包住，才能保证任何失败路径都释放锁（否则一次失败的恢复
    会让系统永久无法恢复——比不允许恢复更糟）。
    """
    _acquire_restore_lock()
    try:
        maintenance.enter_maintenance()
        try:
            return _restore_backup_impl(
                db,
                current_user=current_user,
                backup_id=backup_id,
                confirm=confirm,
                reason=reason,
                ip=ip,
                user_agent=user_agent,
            )
        finally:
            maintenance.leave_maintenance()
    finally:
        _release_restore_lock()


def _restore_backup_impl(
    db: Session,
    *,
    current_user: User,
    backup_id: int,
    confirm: bool = False,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """恢复备份（DESIGN §10.2、API.md §10）：

    1. confirm 必填（缺省/false → 400）
    2. 先自动生成"当前状态"安全备份（防误恢复 / 留旧）
    3. 校验 manifest + 全部文件 sha256（失败不恢复，保持现状）
    4. 释放池化连接 → 在线备份回写数据库 + 切换业务目录（旧状态保留：
       安全备份 + *.pre_<ts>；数据库文件与 WAL 原地复用，不换 inode）
    5. 用全新会话在**恢复后的库**上记录 restored + 审计 + 补录安全备份记录（可见可管理）

    调用方必须已持有恢复互斥锁（见 `restore_backup`）。
    """
    if not confirm:
        raise AppError("BAD_REQUEST", "恢复将覆盖当前数据，必须传 confirm=true 二次确认")

    record = db.get(BackupRecord, backup_id)
    if record is None:
        raise AppError("NOT_FOUND", "备份记录不存在")
    if record.status != "completed":
        raise AppError("CONFLICT", f"备份状态为 {record.status}，不可恢复")

    backup_dir = _resolve_backup_dir(record)
    if not backup_dir.exists():
        raise AppError("NOT_FOUND", "备份目录不存在（可能已被删除）")

    # 1) 安全备份当前状态（先于任何改动；完整保留旧库 + 旧业务文件）
    #    _inside_restore=True：本调用在 _restore_lock 内，须绕过互斥检查（否则自锁）
    safety = create_backup(
        db, current_user=current_user, backup_type="full", note="restore-safety",
        _inside_restore=True,
    )

    # 2) 校验（全部通过才动数据；失败抛异常且不改任何文件）
    manifest = _verify_backup(backup_dir, record)
    file_rels = _manifest_paths(manifest)

    # 3) 就绪：stage 目录（backups/ 下临时目录，恢复完成后清理）
    stage_root = Path(tempfile.mkdtemp(prefix="restore_stage_", dir=settings.backups_dir))
    try:
        shutil.copy2(backup_dir / "app.db", stage_root / "app.db")
        if _sha256_file(stage_root / "app.db") != manifest.get("db_sha256"):
            raise AppError("INTERNAL_ERROR", "恢复副本校验失败，恢复中止")
        files_stage = stage_root / "files"
        if record.backup_type == "full":
            src_files = backup_dir / "files"
            if src_files.exists():
                # 正常路径：完整复制备份文件
                shutil.copytree(src_files, files_stage)
            else:
                # 零文件备份（或旧版备份）容错：缺失 files/ 视为无业务文件，
                # 仅恢复数据库（manifest 空清单已通过 _verify_backup 校验）
                files_stage.mkdir()
        else:
            files_stage.mkdir()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")
        db_path = settings.database_path

        # 4a) 释放池化连接，减少恢复期间的在途会话。engine.dispose() 只关闭
        #     池中空闲连接，不回收外部正在使用的连接；因此数据库恢复不能
        #     依赖“删除/替换文件”，必须走 SQLite 在线备份（4c）。
        from app.db.session import engine as app_engine

        db.close()
        app_engine.dispose()

        # 4b) 留旧快照：backup API 读"主文件 + WAL"的一致视图（shutil.copy2
        #     在 WAL 有未 checkpoint 事务时会漏数据），命名与 retention 对齐
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.exists() and db_path.stat().st_size > 0:
            _copy_db_snapshot(db_path, db_path.parent / f"{db_path.stem}.pre_{ts}")
        # 4c) 数据库原地恢复：不删除/替换 app.db 与 -wal/-shm（Windows 句柄锁，
        #     见 _restore_db_in_place）。恢复后强制 WAL + 完整性自检（§28）
        _restore_db_in_place(stage_root / "app.db", db_path)
        _post_restore_db_check(db_path)

        # 5) 业务目录切换（旧目录保留为 *.pre_<ts>，留旧；仅复制 manifest 清单内文件）
        if record.backup_type == "full":
            for sub in BACKUP_SUBDIRS:
                cur = settings.data_dir / sub
                if cur.exists() and any(cur.iterdir()):
                    pre = settings.data_dir / f"{sub}.pre_{ts}"
                    cur.rename(pre)
                cur.mkdir(parents=True, exist_ok=True)
            for rel in file_rels:
                src = files_stage / rel
                target = settings.data_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    # 6) 用全新会话在**恢复后的库**上：删除已用备份（记录行+目录）+ 审计 + 补录
    #    安全备份记录（2026-09-03 产品决策：恢复完成后该备份即被消费，无再次
    #    恢复价值，直接删除以免堆积；回滚依赖恢复前安全备份与 *.pre_<ts> 留旧组）
    from app.db.session import SessionLocal as NewSessionLocal

    session: Session = NewSessionLocal()
    try:
        used_dir = settings.data_dir / record.backup_path
        if used_dir.is_dir() and used_dir.name.startswith("backup_"):
            shutil.rmtree(used_dir, ignore_errors=True)
        used_row = session.get(BackupRecord, record.id)
        if used_row is not None:
            session.delete(used_row)  # 恢复库快照中恰好含该记录时一并删除

        safety_row = BackupRecord(
            backup_type=safety.backup_type,
            backup_path=safety.backup_path,
            manifest_path=safety.manifest_path,
            size_bytes=safety.size_bytes,
            db_sha256=safety.db_sha256,
            file_count=safety.file_count,
            status="completed",
            note="恢复前自动安全备份（旧库回滚后由恢复流程补录，便于管理与清理）",
            created_by=current_user.id,
        )
        session.add(safety_row)
        session.flush()

        audit_service.log(
            session,
            action=audit_service.ACTION_BACKUP_RESTORE,
            user=current_user,
            object_type="backup",
            object_id=record.id,
            object_label=record.backup_path,
            detail={
                "safety_backup_id": safety_row.id,
                "reason": reason,
                "db_sha256": manifest.get("db_sha256"),
                "file_count": manifest.get("file_count"),
                "used_backup_deleted": True,
            },
            ip=ip,
            user_agent=user_agent,
        )
        session.commit()
        # 恢复替换了整个数据库：进程内业务缓存全部失效，避免读到旧库数据
        from app.core.cache import cache as _cache
        _cache.clear()
        # 保留策略清理（本次安全备份/留旧组最新，不会被清；失败不影响恢复结果）
        try:
            apply_retention(session, current_user=current_user, ip=ip, user_agent=user_agent)
        except Exception:  # noqa: BLE001
            logger.warning("恢复后保留清理失败（不影响恢复结果）", exc_info=True)
        restored_at_value = datetime.now()
        safety_row_id = safety_row.id
    finally:
        session.close()

    return {
        "ok": True,
        "id": record.id,
        "status": "restored",
        "restored_at": restored_at_value,
        "safety_backup_id": safety_row_id,
    }


def delete_backup(
    db: Session,
    *,
    current_user: User,
    backup_id: int,
    confirm: bool = False,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """删除备份（admin + confirm；真删除：物理删目录 + 记录行删除，2026-08-30）。

    语义（产品决策）：删除即从列表彻底消失，不做 purged 软标记/隐藏。
    审计仍留痕（backup_delete），删除动作本身可追溯。
    """
    if not confirm:
        raise AppError("BAD_REQUEST", "必须传 confirm=true 二次确认")
    record = db.get(BackupRecord, backup_id)
    if record is None:
        raise AppError("NOT_FOUND", "备份记录不存在")

    backup_dir = _resolve_backup_dir(record)
    # L-3 安全校验：确认路径不会逃逸出 backups 目录
    resolved = backup_dir.resolve()
    expected_root = settings.data_dir.joinpath("backups").resolve()
    if not resolved.is_relative_to(expected_root):
        raise AppError("VALIDATION_ERROR", "备份路径非法（路径遍历攻击）")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    record_id, record_path = record.id, record.backup_path
    db.delete(record)
    audit_service.log(
        db,
        action=audit_service.ACTION_BACKUP_DELETE,
        user=current_user,
        object_type="backup",
        object_id=record_id,
        object_label=record_path,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return {"ok": True, "id": record_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# 保留策略（BACKUP_KEEP_DAYS / BACKUP_KEEP_COUNT / BACKUP_PRE_KEEP_COUNT）
# ---------------------------------------------------------------------------
def apply_retention(
    db: Session,
    *,
    current_user: User,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """保留策略清理（每次备份/恢复成功后由调用方触发；宁多留不误删）。

    - backup_records：completed/restored 的记录按 BACKUP_KEEP_DAYS（超期）与
      BACKUP_KEEP_COUNT（超出份数）判定清理——目录删除 + status='purged'，
      与手动删除同语义
    - *.pre_<时间戳> 留旧目录：按时间戳分组（一次恢复的一组目录），仅保留
      最近 BACKUP_PRE_KEEP_COUNT 组
    - 目录名正则白名单（backup_<数字时间戳> / *.pre_<数字时间戳>），其余一律不碰
    - rmtree 失败仅告警跳过：目录仍在，下轮清理自动重试（中断自愈）
    - 无删除不写审计；有删除写一条汇总审计（复用 backup_delete 动作）
    """
    now = datetime.now()
    purged_records: list[dict] = []
    pre_removed: list[str] = []

    eligible = db.execute(
        select(BackupRecord)
        .where(BackupRecord.status.in_(("completed", "restored")))
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
    ).scalars().all()

    victims: set[int] = set()
    if settings.BACKUP_KEEP_DAYS > 0:
        cutoff = now - timedelta(days=settings.BACKUP_KEEP_DAYS)
        victims.update(
            r.id for r in eligible if r.created_at and r.created_at < cutoff
        )
    if settings.BACKUP_KEEP_COUNT > 0:
        victims.update(r.id for r in eligible[settings.BACKUP_KEEP_COUNT:])

    expected_root = (settings.data_dir / "backups").resolve()
    for r in eligible:
        if r.id not in victims:
            continue
        d = settings.data_dir / r.backup_path
        name_ok = _BACKUP_DIR_RE.fullmatch(Path(r.backup_path).name) is not None
        if not name_ok or not d.resolve().is_relative_to(expected_root):
            logger.warning("保留清理跳过非法备份路径: %s", r.backup_path)
            continue
        if d.exists():
            try:
                shutil.rmtree(d)
            except OSError as exc:
                logger.warning("保留清理删除目录失败（下轮重试）: %s (%s)", d, exc)
                continue
        db.delete(r)
        purged_records.append({"id": r.id, "path": r.backup_path})

    # pre_<ts> 留旧目录组：data/ 下业务目录 + data/database/ 下旧库快照，同一
    # 时间戳为一次恢复演练的组，整组计数、整组清理
    groups: dict[str, list[Path]] = {}
    for p in settings.data_dir.glob("*.pre_*"):
        m = _PRE_DIR_RE.fullmatch(p.name)
        if m and m.group(1) in BACKUP_SUBDIRS:
            groups.setdefault(m.group(2), []).append(p)
    db_file = settings.database_path
    if db_file.parent.exists():
        # 以 stem 为准：app.db → app.pre_<ts>（与恢复落名一致，兼容存量孤儿）
        for p in db_file.parent.glob(f"{db_file.stem}.pre_*"):
            m = _PRE_DIR_RE.fullmatch(p.name)
            if m and m.group(1) == db_file.stem:
                groups.setdefault(m.group(2), []).append(p)
    keep_groups = max(0, settings.BACKUP_PRE_KEEP_COUNT)
    for ts in sorted(groups, reverse=True)[keep_groups:]:
        for p in groups[ts]:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError as exc:
                logger.warning("保留清理删除留旧目录失败（下轮重试）: %s (%s)", p, exc)
                continue
            pre_removed.append(p.name)

    if purged_records or pre_removed:
        audit_service.log(
            db,
            action=audit_service.ACTION_BACKUP_DELETE,
            user=current_user,
            object_type="backup",
            object_label="auto-retention",
            detail={
                "auto_cleanup": True,
                "purged_records": purged_records,
                "pre_removed": pre_removed,
                "keep_days": settings.BACKUP_KEEP_DAYS,
                "keep_count": settings.BACKUP_KEEP_COUNT,
                "pre_keep_count": settings.BACKUP_PRE_KEEP_COUNT,
            },
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
    return {"purged_records": purged_records, "pre_removed": pre_removed}


def retention_policy() -> dict:
    """备份页策略条数据（配置回显；schedule 为空表示自动备份未启用）。"""
    from app.core import scheduler  # 局部导入避免循环依赖

    schedule = (settings.BACKUP_SCHEDULE or "").strip()
    next_run_at = scheduler.current_next_run() if schedule else None
    return {
        "schedule": schedule or None,
        "next_run_at": next_run_at.isoformat() if next_run_at else None,
        "keep_days": settings.BACKUP_KEEP_DAYS or None,
        "keep_count": settings.BACKUP_KEEP_COUNT or None,
        "pre_keep_count": settings.BACKUP_PRE_KEEP_COUNT,
    }


def backup_stale_days() -> int:
    """工作台「距上次备份」卡报警阈值：调度周期 +1 天；未启用维持现状 7 天。"""
    schedule = (settings.BACKUP_SCHEDULE or "").strip()
    if not schedule:
        return 7
    try:
        from app.core.scheduler import parse_schedule  # 局部导入避免循环依赖

        return parse_schedule(schedule)[3] + 1
    except ValueError:
        return 7