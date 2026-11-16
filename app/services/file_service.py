"""文件服务（docs/DESIGN.md §5、项目规范 §8/§10/§11/§26）。

- 保存：校验通过后写入 data/documents/ 或 data/notices/（随机内部名，客户端不能指定路径）
- 替换：旧文件**移动**到 data/recycle_bin/（不覆盖、不物理删除，§8），FileRecord.status='recycled'，
  写 recycle_bin 记录（reason=replaced），rev_count+1
- 归档/删除资料：当前文件一并进回收站（reason=archived/deleted）
- 路径全部派生自配置 DATA_DIR，防穿越（§11）
"""
from __future__ import annotations

import io
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, cast

from PIL import Image, ImageSequence
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.file_safety import clean_filename, safe_join, validate_upload
from app.models.backup import RecycleBin
from app.models.document import Document, FileRecord
from app.models.user import User
from app.services import audit_service

logger = logging.getLogger(__name__)

settings = get_settings()

# owner_type → 数据目录
OWNER_DIRS = {
    "document": settings.documents_dir,
    "notice": settings.notices_dir,
}


def _shard_subdir(internal_name: str) -> str:
    """分片子目录：按内部文件名（uuid4().hex）前 2 位（如 ab/）。

    存储路径 = 纯函数（owner + internal_name）→ 目录永不靠人工维护；
    文件再多单目录也不会膨胀，且将来调整规则无需迁移（旧路径在库里）。
    """
    return internal_name[:2]


def storage_rel_path(owner_type: str, internal_name: str) -> str:
    """文件相对 DATA_DIR 的存储路径（documents/ab/<name>，纯函数派生）。"""
    return f"{OWNER_DIRS[owner_type].name}/{_shard_subdir(internal_name)}/{internal_name}"


def save_target_path(owner_type: str, internal_name: str) -> Path:
    """save_upload 将要落盘的绝对路径（纯函数派生，供失败补偿清理孤儿文件）。

    2026-09-29 新增：分片规则只应有一处实现。原先 replace_document_file 的补偿
    分支自己内联拼了一遍「OWNER_DIRS[..] / _shard_subdir(..) / name」，任何一处
    改动分片算法都会让另一处找不到文件 —— 统一走本函数。
    """
    return OWNER_DIRS[owner_type] / _shard_subdir(internal_name) / internal_name


def abs_path(rel_path: str | Path) -> Path:
    """按 FileRecord.storage_rel_path（相对 DATA_DIR）取绝对路径（防穿越）。"""
    return safe_join(settings.data_dir, rel_path)


def file_record_to_dict(rec: FileRecord) -> dict:
    """文件记录出站对象（API.md 详情/上传响应用）。"""
    return {
        "id": rec.id,
        "original_filename": rec.original_filename,
        "file_type": rec.file_type,
        "extension": rec.extension,
        "mime_type": rec.mime_type,
        "size_bytes": rec.size_bytes,
        "sha256": rec.sha256,
        "status": rec.status,
        "uploaded_by": rec.uploaded_by,
        "uploaded_at": rec.uploaded_at,
    }


def save_upload(
    db: Session,
    *,
    owner_type: str,
    owner_id: int,
    original_filename: str,
    data: bytes,
    uploaded_by: Optional[User],
    meta: Optional[dict] = None,
) -> FileRecord:
    """校验并落盘新文件，创建 FileRecord（active）。

    - 校验失败（扩展名/魔数/大小/文件名）→ AppError，不落盘
    - 存储目录由 owner_type 决定，用户不可指定路径
    - 不 commit：由调用方事务统一提交
    - meta：可选，调用方预先校验的结果（含 internal_name），复用同一内部名
      —— 供 replace_document_file 的失败补偿按同一名字清理孤儿文件
    """
    if owner_type not in OWNER_DIRS:
        raise AppError("BAD_REQUEST", f"未知文件归属类型: {owner_type}")

    meta = meta or validate_upload(original_filename, data)
    # 分片目录：documents/ab/xxx（二级子目录按内部名前 2 位，纯函数派生，§11）
    target_dir = OWNER_DIRS[owner_type] / _shard_subdir(meta["internal_name"])
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / meta["internal_name"]

    # 先落盘，再建记录
    with open(dest, "wb") as f:
        f.write(data)

    rec = FileRecord(
        owner_type=owner_type,
        owner_id=owner_id,
        original_filename=meta["original_filename"],
        internal_name=meta["internal_name"],
        file_type=meta["file_type"],
        extension=meta["extension"],
        mime_type=meta["mime_type"],
        size_bytes=meta["size_bytes"],
        sha256=meta["sha256"],
        storage_rel_path=storage_rel_path(owner_type, meta["internal_name"]),
        status="active",
        uploaded_by=uploaded_by.id if uploaded_by else None,
        uploaded_at=datetime.now(),
    )
    db.add(rec)
    db.flush()
    return rec


# ---------------------------------------------------------------------------
# 多图上传：合成一份多页 PDF（2026-11-04 产品决策）
# ---------------------------------------------------------------------------
MAX_MERGE_IMAGES = 30


def _normalize_image(data: bytes) -> Image.Image:
    """解码任意受支持图片并转为可放进 PDF 的模式。

    - 统一转 RGB（PDF 不支持 RGBA/P/CMYK 直接嵌入）
    - 动图取第一帧（多帧塞进一页会错位）
    - 超过 MAX_IMAGE_EDGE 的长边等比缩小：手机原图 4000px 会让 PDF 暴涨
    """
    try:
        img: Image.Image = cast(Image.Image, Image.open(io.BytesIO(data)))
        if getattr(img, "is_animated", False):
            img = next(ImageSequence.Iterator(img)).copy()
        img = img.convert("RGB")
    except Exception as exc:  # noqa: BLE001 —— 任何解码失败都归为"图片已损坏"
        raise AppError("VALIDATION_ERROR", f"图片无法解析（可能已损坏）：{exc}") from exc

    max_edge = 2400
    w, h = img.size
    if max(w, h) > max_edge:
        ratio = max_edge / float(max(w, h))
        img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.Resampling.LANCZOS)
    return img


def merge_images_to_pdf(
    files: Sequence[tuple[str, bytes]],
    *,
    title: Optional[str] = None,
) -> tuple[bytes, str, str]:
    """把多张图片合成一份多页 PDF（§8：一份资料仍只有一个当前文件）。

    返回 (pdf_bytes, 建议文件名, 警告文本)。
    - 一页一张图，按上传顺序排列，页面尺寸 = 图片尺寸（保持原比例、不裁剪）
    - 图片过大时等比缩小到长边 2400px（预览清晰度足够，体积可控）
    - 文件名优先用资料标题（`标题（3张）.pdf`，2026-11-06）；标题为空或
      清洗后无有效字符时回退到首图名，避免出现「（）.pdf」这种怪名
    """
    if not files:
        raise AppError("VALIDATION_ERROR", "没有可用的图片")
    if len(files) > MAX_MERGE_IMAGES:
        raise AppError(
            "VALIDATION_ERROR",
            f"一次最多合并 {MAX_MERGE_IMAGES} 张图片（本次 {len(files)} 张），请分批上传",
        )

    images: list[Image.Image] = []
    for name, data in files:
        if not data:
            raise AppError("VALIDATION_ERROR", f"文件为空：{clean_filename(name)}")
        images.append(_normalize_image(data))

    # 写 PDF：每张图单独一页，页面尺寸跟随图片，不缩放排版（不修改画面内容）
    out = io.BytesIO()
    first, rest = images[0], images[1:]
    first.save(out, format="PDF", resolution=150.0, save_all=True, append_images=rest)
    pdf_bytes = out.getvalue()

    base = ""
    if title:
        try:
            base = clean_filename(title).strip(" .")
        except AppError:
            base = ""
    if not base:
        base = Path(clean_filename(files[0][0])).stem
    # original_filename 列是 VARCHAR(255)：留出「（NN张）.pdf」约 12 字符的余量
    suggested = f"{base[:240]}（{len(files)}张）.pdf"

    warnings = []
    if len(files) > 1:
        warnings.append(f"已把 {len(files)} 张图片按顺序合成一份 PDF（每张一页）")
    return pdf_bytes, suggested, "；".join(warnings)


def _move_physical(src: Path, dst_dir: Path, internal_name: str) -> Path:
    """移动文件到目标目录（同卷 rename；失败时给出明确异常）。"""
    src = src.resolve()
    dst_dir = dst_dir.resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / internal_name
    if not src.exists():
        raise AppError("NOT_FOUND", "原文件不存在，无法移动")
    try:
        shutil.move(str(src), str(dst))
    except OSError as exc:  # noqa: BLE001
        raise AppError("INTERNAL_ERROR", f"文件移动失败: {exc}") from exc
    return dst


def recycle_physical(file_record: FileRecord) -> str:
    """把文件本体移入回收站并翻转状态，返回移动前的相对路径。

    只做物理动作、不创建 RecycleBin 记录——归档资料时主文件与附件要
    **共用一个**回收站条目，必须能把「移动」与「建条目」拆开（2026-11-06）。
    """
    if file_record.status != "active":
        raise AppError("CONFLICT", f"文件状态为 {file_record.status}，不能再次回收")

    origin = abs_path(file_record.storage_rel_path)
    recycle_dir = settings.recycle_bin_dir
    _move_physical(origin, recycle_dir, file_record.internal_name)

    origin_rel_path = file_record.storage_rel_path
    file_record.storage_rel_path = f"recycle_bin/{file_record.internal_name}"
    file_record.status = "recycled"
    return origin_rel_path


def recycle_file(
    db: Session,
    *,
    file_record: FileRecord,
    reason: str,
    deleted_by: Optional[User],
    document_id: Optional[int] = None,
    as_attachment: bool = False,
) -> RecycleBin:
    """把文件放入回收站（§26）：物理移动 + 一条独立记录。

    reason ∈ {'replaced','archived','deleted','attachment_deleted'}
    as_attachment=True：这是附件，记录落在 attachment_file_record_id。
    """
    origin_rel_path = recycle_physical(file_record)

    entry = RecycleBin(
        document_id=document_id,
        file_record_id=None if as_attachment else file_record.id,
        attachment_file_record_id=file_record.id if as_attachment else None,
        reason=reason,
        origin_rel_path=origin_rel_path,
        deleted_by=deleted_by.id if deleted_by else None,
        deleted_at=datetime.now(),
    )
    db.add(entry)
    db.flush()
    return entry


def replace_document_file(
    db: Session,
    *,
    document: Document,
    original_filename: str,
    data: bytes,
    user: User,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """上传/替换资料文件（API.md §6 POST /documents/{id}/file，S-1 两阶段原子修复）。

    顺序（任一步失败 → DB 回滚 + 物理补偿，不留孤儿、不卡死）：
      0. 上传预校验（扩展名/魔数/大小/文件名）——先于任何物理副作用；
      1. 旧文件磁盘存在性预检：缺失 → 409 明确业务错误（提示走回收站/恢复流程修复，
         绝不先写新文件产生孤儿，修复后可从“永久卡死”变为“可明确处理”）；
      2. 旧文件移入回收站（move 成功才继续）；
      3. 新文件落盘 + FileRecord + 文档关联/rev_count + 审计；
      4. commit；任何异常 → rollback + 删除新文件（孤儿清理）+ 旧文件移回原位置。
    """
    if document.archived_at is not None:
        raise AppError("CONFLICT", "已归档资料不能上传文件")

    # 阶段 0：预校验（不落盘；内部名在此固定，补偿清理按同一名字进行）
    meta = validate_upload(original_filename, data)

    old = document.current_file
    old_active = old if (old is not None and old.status == "active") else None
    old_origin: Optional[Path] = None
    if old_active is not None:
        old_origin = abs_path(old_active.storage_rel_path)
        if not old_origin.exists():
            # 已存在的损坏状态：旧文件磁盘缺失 → 明确业务错误，不产生孤儿（S-1 修复点③）
            raise AppError(
                "CONFLICT",
                "当前文件在磁盘上缺失，无法替换。请先通过回收站/资料恢复流程修复"
                "该文件，或归档本资料后再处理；替换流程不会产生新文件",
                detail={
                    "file_record_id": old_active.id,
                    "storage_rel_path": old_active.storage_rel_path,
                },
            )

    recycled_id: Optional[int] = None
    new_rec: Optional[FileRecord] = None
    try:
        # 阶段 1：旧文件先移入回收站（move 成功才继续；此时新文件尚未写盘）
        if old_active is not None:
            recycle_file(
                db,
                file_record=old_active,
                reason="replaced",
                deleted_by=user,
                document_id=document.id,
            )
            recycled_id = old_active.id

        # 阶段 2：新文件落盘（复用阶段 0 预校验 meta，同一 internal_name）
        new_rec = save_upload(
            db,
            owner_type="document",
            owner_id=document.id,
            original_filename=original_filename,
            data=data,
            uploaded_by=user,
            meta=meta,
        )

        # 阶段 3：DB 更新 + 审计 + 提交
        document.current_file_id = new_rec.id
        document.rev_count += 1
        document.updated_by = user.id

        audit_service.log(
            db,
            action=audit_service.ACTION_REPLACE_FILE,
            user=user,
            object_type="document",
            object_id=document.id,
            object_label=document.title,
            detail={
                "new_file_id": new_rec.id,
                "new_sha256": new_rec.sha256,
                "new_size_bytes": new_rec.size_bytes,
                "recycled_file_id": recycled_id,
                "rev_count": document.rev_count,
            },
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        # 替换成功：资料列表（含 file 信息）需刷新
        from app.core.cache import invalidate_lists
        invalidate_lists()
    except Exception:
        db.rollback()
        # ---- 物理补偿（S-1 修复点②：孤儿文件清理 + 旧文件回位）----
        if meta is not None:
            # 与 save_upload 落盘路径同构：documents/<分片>/<内部名>（分片派生必须
            # 走同一纯函数，扁平路径在分片目录布局下永远找不到文件）
            orphan = save_target_path("document", meta["internal_name"])
            try:
                if orphan.exists():
                    orphan.unlink()  # 已写盘但未提交 → 删除孤儿，不留无主文件
                    logger.info("替换失败补偿：已删除孤儿文件 %s", orphan.name)
            except OSError:
                logger.exception("孤儿文件清理失败: %s", orphan)
        if old_active is not None and old_origin is not None:
            try:
                # 旧文件可能已被移入回收站（DB 已回滚，按 internal_name 找回收站副本移回）
                recycle_path = settings.recycle_bin_dir / old_active.internal_name
                if not old_origin.exists() and recycle_path.exists():
                    _move_physical(recycle_path, old_origin.parent, old_origin.name)
                    logger.info("替换失败补偿：旧文件已移回 %s", old_origin)
            except Exception:  # noqa: BLE001
                logger.exception("替换失败补偿：旧文件回位失败")
        raise

    db.refresh(document)
    return {
        "current_file": file_record_to_dict(new_rec),
        "rev_count": document.rev_count,
        "recycled_file_id": recycled_id,
    }


def archive_document_files(
    db: Session,
    *,
    document: Document,
    reason: str,
    actor: User,
) -> Optional[RecycleBin]:
    """归档/删除资料时把主文件与附件一起放入回收站（reason='archived'|'deleted'）。

    2026-11-06 产品决策：**只写一条** RecycleBin（两个 file id 同填），
    恢复时也必须两个一起回来，不提供单独恢复其中一个的入口。
    没有文件的资料返回 None，由调用方决定是否补一条空记录。
    """
    main = document.current_file
    attachment = document.attachment_file
    main = main if (main is not None and main.status == "active") else None
    attachment = (
        attachment if (attachment is not None and attachment.status == "active") else None
    )
    if main is None and attachment is None:
        return None

    entry: Optional[RecycleBin] = None

    if main is not None:
        entry = RecycleBin(
            document_id=document.id,
            file_record_id=main.id,
            reason=reason,
            origin_rel_path=recycle_physical(main),
            deleted_by=actor.id if actor else None,
            deleted_at=datetime.now(),
        )
        db.add(entry)
        db.flush()

    if attachment is not None:
        # 原路径不必存：恢复时按 internal_name 反推与落盘时同构的分片路径
        # （storage_rel_path("document", name)），与主文件共用同一条记录。
        recycle_physical(attachment)
        if entry is None:
            entry = RecycleBin(
                document_id=document.id,
                file_record_id=None,
                reason=reason,
                origin_rel_path=None,
                deleted_by=actor.id if actor else None,
                deleted_at=datetime.now(),
            )
            db.add(entry)
            db.flush()
        entry.attachment_file_record_id = attachment.id
        db.flush()

    return entry


def set_document_attachment(
    db: Session,
    *,
    document: Document,
    original_filename: str,
    data: bytes,
    user: User,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """上传/替换资料附件（2026-11-06）。

    与 replace_document_file 同构的两阶段补偿：预校验 → 旧附件回收 →
    新附件落盘 → DB 更新 → 异常回滚 + 孤儿清理 + 旧附件回位。
    区别：附件限制 ATTACHMENT_EXTENSIONS（多一个 .pptx），
    且旧附件走 as_attachment=True 的回收站记录。
    """
    from app.core.file_safety import ATTACHMENT_EXTENSIONS

    if document.archived_at is not None:
        raise AppError("CONFLICT", "已归档资料不能上传附件")

    meta = validate_upload(original_filename, data, allowed=ATTACHMENT_EXTENSIONS)

    old = document.attachment_file
    old_active = old if (old is not None and old.status == "active") else None
    old_origin: Optional[Path] = None
    if old_active is not None:
        old_origin = abs_path(old_active.storage_rel_path)
        if not old_origin.exists():
            raise AppError(
                "CONFLICT",
                "当前附件在磁盘上缺失，无法替换。请先在回收站中彻底删除该记录后再上传",
                detail={
                    "file_record_id": old_active.id,
                    "storage_rel_path": old_active.storage_rel_path,
                },
            )

    recycled_id: Optional[int] = None
    new_rec: Optional[FileRecord] = None
    try:
        if old_active is not None:
            recycle_file(
                db,
                file_record=old_active,
                reason="replaced",
                deleted_by=user,
                document_id=document.id,
                as_attachment=True,
            )
            recycled_id = old_active.id

        new_rec = save_upload(
            db,
            owner_type="document",
            owner_id=document.id,
            original_filename=original_filename,
            data=data,
            uploaded_by=user,
            meta=meta,
        )

        document.attachment_file_id = new_rec.id
        document.updated_by = user.id

        audit_service.log(
            db,
            action=audit_service.ACTION_SET_ATTACHMENT,
            user=user,
            object_type="document",
            object_id=document.id,
            object_label=document.title,
            detail={
                "new_file_id": new_rec.id,
                "new_sha256": new_rec.sha256,
                "new_size_bytes": new_rec.size_bytes,
                "recycled_file_id": recycled_id,
            },
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        from app.core.cache import invalidate_lists

        invalidate_lists()
    except Exception:
        db.rollback()
        if meta is not None:
            orphan = save_target_path("document", meta["internal_name"])
            try:
                if orphan.exists():
                    orphan.unlink()
                    logger.info("附件上传失败补偿：已删除孤儿文件 %s", orphan.name)
            except OSError:
                logger.exception("附件孤儿文件清理失败: %s", orphan)
        if old_active is not None and old_origin is not None:
            try:
                recycle_path = settings.recycle_bin_dir / old_active.internal_name
                if not old_origin.exists() and recycle_path.exists():
                    _move_physical(recycle_path, old_origin.parent, old_origin.name)
                    logger.info("附件上传失败补偿：旧附件已移回 %s", old_origin)
            except Exception:  # noqa: BLE001
                logger.exception("附件上传失败补偿：旧附件回位失败")
        raise

    db.refresh(document)
    return {
        "attachment_file": file_record_to_dict(new_rec),
        "recycled_file_id": recycled_id,
    }


def remove_document_attachment(
    db: Session,
    *,
    document: Document,
    user: User,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """删除资料附件（2026-11-06）：进回收站（reason=attachment_deleted）。

    故意不复用 reason='deleted'：recycle_service.restore 的 deleted 分支会
    连带取消资料归档，删个附件把整份资料恢复出来是个事故。
    """
    rec = document.attachment_file
    if rec is None or rec.status != "active":
        raise AppError("NOT_FOUND", "该资料没有可删除的附件")
    if document.archived_at is not None:
        raise AppError("CONFLICT", "已归档资料的附件请通过恢复流程处理")

    entry = recycle_file(
        db,
        file_record=rec,
        reason="attachment_deleted",
        deleted_by=user,
        document_id=document.id,
        as_attachment=True,
    )
    document.attachment_file_id = None
    document.updated_by = user.id

    audit_service.log(
        db,
        action=audit_service.ACTION_DELETE_ATTACHMENT,
        user=user,
        object_type="document",
        object_id=document.id,
        object_label=document.title,
        detail={"file_record_id": rec.id, "recycle_entry_id": entry.id},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    from app.core.cache import invalidate_lists

    invalidate_lists()
    return {"ok": True, "recycle_entry_id": entry.id}


def restore_recycled_file(
    db: Session,
    *,
    file_record: FileRecord,
    origin_rel_path: str,
    actor: User,
) -> None:
    """恢复回收站文件到原目录（data/documents/ 或 data/notices/）。"""
    if file_record.status != "recycled":
        raise AppError("CONFLICT", "文件不在回收站中")
    recycle_entry = (
        db.query(RecycleBin)
        .filter(
            RecycleBin.file_record_id == file_record.id,
            RecycleBin.restored_at.is_(None),
        )
        .order_by(RecycleBin.id.desc())
        .first()
    )
    src = abs_path(file_record.storage_rel_path)
    # 目标目录由 origin_rel_path 决定（只允许 documents/、notices/ 子目录内）
    origin = safe_join(settings.data_dir, origin_rel_path)
    origin.parent.mkdir(parents=True, exist_ok=True)
    _move_physical(src, origin.parent, origin.name)

    file_record.storage_rel_path = origin_rel_path
    file_record.status = "active"
    # 附件走 attachment_file_record_id，上面的 file_record_id 过滤找不到它；
    # 这里补查一次，保证附件恢复也能把回收站条目标记为已恢复。
    if recycle_entry is None:
        recycle_entry = (
            db.query(RecycleBin)
            .filter(
                RecycleBin.attachment_file_record_id == file_record.id,
                RecycleBin.restored_at.is_(None),
            )
            .order_by(RecycleBin.id.desc())
            .first()
        )
    if recycle_entry is not None:
        recycle_entry.restored_at = datetime.now()
        recycle_entry.restored_by = actor.id if actor else None

def purge_file(file_record: FileRecord) -> None:
    """物理删除文件（仅 admin 二次确认后的回收站 purge 入口，§26）。"""
    path = abs_path(file_record.storage_rel_path)
    if path.exists():
        path.unlink()
