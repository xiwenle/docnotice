"""资料路由（docs/API.md §6）。

- 列表/详情/状态/关联通知：登录用户
- 新建/修改/归档/恢复/上传文件：admin+
- 下载/预览：登录用户 + 可见性校验（归档仅 admin+）
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_current_user, require_roles
from app.core.errors import AppError
from app.core.file_safety import (
    MAIN_FILE_EXTENSIONS,
    IMAGE_TYPES,
    extension_of,
    validate_upload,
)
from app.db.session import get_db
from app.models.document import Document
from app.models.user import User
from app.schemas.document import (
    ArchiveRequest,
    DocumentBatchExecuteRequest,
    DocumentBatchPreviewRequest,
    DocumentBulkUpdate,
    DocumentCreate,
    DocumentUpdate,
    RestoreRequest,
)
from app.services import auth_service, document_batch_service, document_service, file_service, preview_service

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员

# 上传上限统一走配置 MAX_UPLOAD_SIZE_MB（§29：不硬编码业务参数；file_safety 再次按配置校验）
MAX_UPLOAD_BYTES = get_settings().MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _parse_date(value: Optional[str]) -> date_type:
    if value is None:
        return datetime.now().date()
    try:
        return date_type.fromisoformat(value)
    except ValueError:
        raise AppError("VALIDATION_ERROR", f"日期格式应为 YYYY-MM-DD: {value}")


def _parse_optional_date(value: Optional[str]) -> Optional[date_type]:
    if value in (None, ""):
        return None
    return _parse_date(value)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
@router.get("")
def list_documents(
    keyword: Optional[str] = Query(default=None),
    category_id: Optional[int] = Query(default=None),
    tag_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    as_of_date: Optional[str] = Query(default=None, description="YYYY-MM-DD，按该日重算状态"),
    cover_from: Optional[str] = Query(default=None, description="YYYY-MM-DD，要求资料有效期包含该起始日"),
    cover_to: Optional[str] = Query(default=None, description="YYYY-MM-DD，要求资料有效期包含该结束日"),
    uncategorized: bool = Query(default=False, description="仅看未分类（整理视角）"),
    untagged: bool = Query(default=False, description="仅看未打标签（整理视角）"),
    audience_scope: Optional[str] = Query(default=None, description="all/department/user"),
    has_attachment: Optional[bool] = Query(default=None, description="是否有附件"),
    file_type: Optional[str] = Query(default=None, description="主文件类型"),
    missing_main_file: bool = Query(default=False, description="仅看无主文件"),
    sort: str = Query(default="id_desc", description="排序白名单"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """资料列表/搜索（§1、§14、§15）。

    cover_from/cover_to：资料有效期完整覆盖所填起止日期；只填一端时按单日包含判断。
    """
    if status and status not in ("pending", "active", "expired", "archived", "expiring"):
        raise AppError(
            "VALIDATION_ERROR",
            f"status 仅支持 pending/active/expired/archived/expiring: {status}",
        )
    if audience_scope and audience_scope not in ("all", "department", "user"):
        raise AppError("VALIDATION_ERROR", "audience_scope 仅支持 all/department/user")
    if sort not in (
        "id_desc", "created_desc", "updated_desc", "title_asc",
        "effective_from_desc", "effective_to_asc",
    ):
        raise AppError("VALIDATION_ERROR", "不支持的排序方式")
    if file_type and file_type not in (
        "pdf", "docx", "xlsx", "xls", "jpg", "jpeg", "png", "gif", "webp"
    ):
        raise AppError("VALIDATION_ERROR", "不支持的主文件类型筛选")
    cover_from_date = _parse_optional_date(cover_from)
    cover_to_date = _parse_optional_date(cover_to)
    if cover_from_date and cover_to_date and cover_from_date > cover_to_date:
        raise AppError("VALIDATION_ERROR", "生效开始不能晚于生效结束")
    return document_service.list_documents(
        db,
        user=current_user,
        keyword=keyword,
        category_id=category_id,
        tag_id=tag_id,
        status_filter=status,
        as_of_date=_parse_date(as_of_date),
        cover_from=cover_from_date,
        cover_to=cover_to_date,
        uncategorized=uncategorized,
        untagged=untagged,
        audience_scope=audience_scope,
        has_attachment=has_attachment,
        file_type=file_type,
        missing_main_file=missing_main_file,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.post("/batch-actions/{action_key}/preview")
def preview_batch_action(
    action_key: str,
    payload: DocumentBatchPreviewRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """预览批量动作的影响范围；不写业务数据。"""
    ip, ua = auth_service.client_info(request)
    return document_batch_service.preview(
        db,
        current_user=current_user,
        action_key=action_key,
        selection=payload.selection.model_dump(),
        params=payload.params,
        expected_count=payload.expected_count,
    )


@router.post("/batch-actions/{action_key}/execute")
def execute_batch_action(
    action_key: str,
    payload: DocumentBatchExecuteRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """执行已预览的批量动作。"""
    ip, ua = auth_service.client_info(request)
    return document_batch_service.execute(
        db,
        current_user=current_user,
        action_key=action_key,
        selection=payload.selection.model_dump(),
        params=payload.params,
        expected_count=payload.expected_count,
        preview_token=payload.preview_token,
        ip=ip,
        user_agent=ua,
    )

@router.get("/{doc_id}")
def get_document(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """资料详情（§1、§8、§19；归档资料仅 admin+）。"""
    return document_service.get_document(db, user=current_user, doc_id=doc_id)


@router.get("/{doc_id}/status")
def get_document_status(
    doc_id: int,
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD，默认今天"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """指定日期时效状态（§14、§15）。"""
    return document_service.get_document_status(
        db, user=current_user, doc_id=doc_id, as_of_date=_parse_date(date)
    )


@router.get("/{doc_id}/notices")
def get_document_notices(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """关联通知（§19）。"""
    return document_service.get_linked_notices(db, user=current_user, doc_id=doc_id)


# ---------------------------------------------------------------------------
# 文件：下载 / 预览（可见性由 service 校验）
# ---------------------------------------------------------------------------
def _load_file_for_current(db: Session, user: User, doc_id: int) -> Document:
    """加载资料并执行可见性校验，返回带 current_file 的 Document。"""
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select

    doc = db.execute(
        select(Document)
        .options(selectinload(Document.current_file))
        .where(Document.id == doc_id)
    ).scalar_one_or_none()
    if doc is None:
        raise AppError("NOT_FOUND", "资料不存在")
    if doc.archived_at is not None and user.role != "admin":
        raise AppError("FORBIDDEN", "资料已归档，无权访问")
    # 可见范围（2026-11-03）：详情页能看 ≠ 能取文件，必须一并校验，
    # 否则知道 id 就能绕过列表直接下载。
    from app.services import document_visibility

    if not document_visibility.is_document_visible(db, doc, user):
        raise AppError("NOT_FOUND", "资料不存在或无权查看")
    return doc


def _download_target(doc: Document):
    """「下载」按钮到底下哪个文件（2026-11-06 产品决策）。

    有 active 附件 → 下附件（那才是用户要的原件）；
    否则 → 下主文件。无附件时行为与改动前完全一致，存量资料零影响。
    """
    att = doc.attachment_file
    if att is not None and att.status == "active":
        return att
    cur = doc.current_file
    if cur is not None and cur.status == "active":
        return cur
    return None


@router.get("/{doc_id}/file")
def download_file(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载默认文件（§12、§33；附件 + RFC5987 中文文件名）。

    下载权限 = 查看权限（2026-11-04 产品决策）：
    能打开这份资料的人就能下载默认文件（有附件时为原稿附件，无附件时为当前主文件）。此前另有 allow_download 开关，
    由管理员逐条控制「仅预览」，现随「阅读对象」精确到人而整体移除。

    2026-10-16：async def → def。本端点无 await 点，同步定义由 FastAPI
    丢进线程池执行，文件 IO/DB 查询不再占用事件循环（原 async 写法里
    这些同步调用反而会阻塞全部请求）。
    """
    doc = _load_file_for_current(db, current_user, doc_id)
    target = _download_target(doc)
    if target is None:
        raise AppError("NOT_FOUND", "资料暂无文件")
    path = file_service.abs_path(target.storage_rel_path)
    if not path.exists():
        raise AppError("NOT_FOUND", "文件不存在（可能已损坏）")
    return FileResponse(
        path=path,
        filename=target.original_filename,
        media_type=target.mime_type,
        content_disposition_type="attachment",  # RFC5987 filename* 由 Starlette 生成
    )


@router.get("/{doc_id}/attachment")
def download_attachment(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """附件下载（2026-11-06）：与「下载」按钮共用权限口径，但强制取附件本身。"""
    doc = _load_file_for_current(db, current_user, doc_id)
    target = doc.attachment_file
    if target is None or target.status != "active":
        raise AppError("NOT_FOUND", "该资料没有附件")
    path = file_service.abs_path(target.storage_rel_path)
    if not path.exists():
        raise AppError("NOT_FOUND", "附件不存在（可能已损坏）")
    return FileResponse(
        path=path,
        filename=target.original_filename,
        media_type=target.mime_type,
        content_disposition_type="attachment",
    )


@router.post("/{doc_id}/attachment")
async def upload_attachment(
    doc_id: int,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """上传/替换资料附件（2026-11-06）：单文件，允许 .pptx，永不预览。"""
    ip, ua = auth_service.client_info(request)
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    doc = db.execute(
        select(Document)
        .options(
            selectinload(Document.current_file),
            selectinload(Document.attachment_file),
        )
        .where(Document.id == doc_id)
    ).scalar_one_or_none()
    if doc is None:
        raise AppError("NOT_FOUND", "资料不存在")

    name = (file.filename or "").strip()
    if not name:
        raise AppError("VALIDATION_ERROR", "上传文件为空")
    data = await _read_limited(file, budget=MAX_UPLOAD_BYTES)
    if len(data) == 0:
        raise AppError("VALIDATION_ERROR", f"上传文件为空：{name}")

    return await run_in_threadpool(
        file_service.set_document_attachment,
        db,
        document=doc,
        original_filename=name,
        data=data,
        user=current_user,
        ip=ip,
        user_agent=ua,
    )


@router.delete("/{doc_id}/attachment")
def delete_attachment(
    doc_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除资料附件（2026-11-06）：进回收站（attachment_deleted），资料状态不变。"""
    ip, ua = auth_service.client_info(request)
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    doc = db.execute(
        select(Document)
        .options(selectinload(Document.attachment_file))
        .where(Document.id == doc_id)
    ).scalar_one_or_none()
    if doc is None:
        raise AppError("NOT_FOUND", "资料不存在")
    return file_service.remove_document_attachment(
        db, document=doc, user=current_user, ip=ip, user_agent=ua
    )


@router.get("/{doc_id}/preview/pdf")
def preview_pdf(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PDF 浏览器内联预览（§12）。

    2026-10-16：async def → def（无 await 点，同步定义走线程池，不占事件循环）。
    """
    doc = _load_file_for_current(db, current_user, doc_id)
    if doc.current_file is None or doc.current_file.status != "active":
        raise AppError("NOT_FOUND", "资料暂无文件")
    info = preview_service.preview_pdf(doc.current_file)
    return FileResponse(
        path=info["path"],
        media_type=info["media_type"],
        filename=info["filename"],
        content_disposition_type="inline",
    )


@router.get("/{doc_id}/preview/image")
def preview_image(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """图片内联预览（§12）：原样返回，浏览器直接渲染。"""
    doc = _load_file_for_current(db, current_user, doc_id)
    if doc.current_file is None or doc.current_file.status != "active":
        raise AppError("NOT_FOUND", "资料暂无文件")
    info = preview_service.preview_image(doc.current_file)
    return FileResponse(
        path=info["path"],
        media_type=info["media_type"],
        filename=info["filename"],
        content_disposition_type="inline",
    )


@router.get("/{doc_id}/preview/docx")
def preview_docx(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """DOCX 文本预览（§12）。"""
    doc = _load_file_for_current(db, current_user, doc_id)
    if doc.current_file is None or doc.current_file.status != "active":
        raise AppError("NOT_FOUND", "资料暂无文件")
    return preview_service.preview_docx(doc.current_file)


@router.get("/{doc_id}/preview/xlsx")
def preview_xlsx(
    doc_id: int,
    sheet: int = Query(default=0, ge=0),
    max_rows: Optional[int] = Query(default=None, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """XLSX/XLS 只读预览（§12；xlrd 2.x 仅支持 .xls，openpyxl 处理 .xlsx）。"""
    doc = _load_file_for_current(db, current_user, doc_id)
    if doc.current_file is None or doc.current_file.status != "active":
        raise AppError("NOT_FOUND", "资料暂无文件")
    return preview_service.preview_xlsx(doc.current_file, sheet=sheet, max_rows=max_rows)


# ---------------------------------------------------------------------------
# 管理操作（admin+）
# ---------------------------------------------------------------------------
@router.post("/bulk")
def bulk_update_documents(
    payload: DocumentBulkUpdate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """批量整理资料（分类/标签/归档），面向数百份存量资料的归纳整理。"""
    ip, ua = auth_service.client_info(request)
    return document_service.bulk_update_documents(
        db,
        current_user=current_user,
        doc_ids=payload.document_ids,
        set_category_id=payload.set_category_id,
        clear_category=payload.clear_category,
        add_tag_id=payload.add_tag_id,
        archive=payload.archive,
        reason=payload.reason,
        ip=ip,
        user_agent=ua,
    )


@router.post("", status_code=201)
def create_document(
    payload: DocumentCreate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建资料（元数据，可稍后传文件；R1 校验）。"""
    ip, ua = auth_service.client_info(request)
    return document_service.create_document(
        db,
        current_user=current_user,
        title=payload.title,
        category_id=payload.category_id,
        summary=payload.summary,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        tag_ids=payload.tag_ids,
        audience_scope=payload.audience_scope,
        audience_department_ids=payload.audience_department_ids,
        audience_user_ids=payload.audience_user_ids,
        ip=ip,
        user_agent=ua,
    )


@router.put("/{doc_id}")
def update_document(
    doc_id: int,
    payload: DocumentUpdate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """修改资料元数据（时效改动走 R2 confirm_overwrite；审计 modify_validity）。"""
    ip, ua = auth_service.client_info(request)
    # M-1 fix：传 null 表示清除失效日（schema §13 承诺）；不传则保持原值
    return document_service.update_document(
        db,
        current_user=current_user,
        doc_id=doc_id,
        title=payload.title,
        category_id=payload.category_id,
        summary=payload.summary,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        clear_effective_to="effective_to" in payload.model_fields_set and payload.effective_to is None,
        tag_ids=payload.tag_ids,
        audience_scope=payload.audience_scope,
        audience_department_ids=payload.audience_department_ids,
        audience_user_ids=payload.audience_user_ids,
        confirm_overwrite=payload.confirm_overwrite,
        modify_reason=payload.modify_reason,
        ip=ip,
        user_agent=ua,
    )


async def _read_limited(f: UploadFile, *, budget: int) -> bytes:
    """流式读取单个上传文件，超过预算立即拒绝（不整读内存）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await f.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > budget:
            raise AppError(
                "FILE_TOO_LARGE",
                f"文件大小超过限制（最大 {budget // 1024 // 1024}MB）",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/{doc_id}/file")
async def upload_file(
    doc_id: int,
    request: Request,
    file: list[UploadFile] = File(...),
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """上传/替换资料文件（§8、§10、§11、§25；旧文件进回收站不覆盖）。

    - 单文件：直接替换（原行为）。
    - 多文件（2026-11-04）：只允许**全部是图片**，服务端合成一份多页 PDF
      作为当前文件——一份资料仍然只有一个当前文件（§8），图片按顺序每张一页。
    - 混合类型或非图片多选 → 400，不做隐式拼接。

    M-2 修复：先校验大小再读入内存，避免超限文件整读 OOM。
    """
    ip, ua = auth_service.client_info(request)
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    doc = db.execute(
        select(Document).options(selectinload(Document.current_file)).where(Document.id == doc_id)
    ).scalar_one_or_none()
    if doc is None:
        raise AppError("NOT_FOUND", "资料不存在")

    files = [f for f in file if f and (f.filename or "").strip()]
    if not files:
        raise AppError("VALIDATION_ERROR", "上传文件为空")

    # 每个文件单独不超过上限；总量给同样的上限（合并后还要再算一次）
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        data = await _read_limited(f, budget=MAX_UPLOAD_BYTES)
        if len(data) == 0:
            raise AppError("VALIDATION_ERROR", f"上传文件为空：{f.filename or '未命名文件'}")
        payloads.append((f.filename or "未命名文件", data))

    original_filename = payloads[0][0]
    data = payloads[0][1]
    merge_warning = ""

    if len(payloads) > 1:
        exts = {extension_of(n).lstrip(".") for n, _ in payloads}
        if not exts <= IMAGE_TYPES:
            raise AppError(
                "VALIDATION_ERROR",
                "一次上传多个文件仅支持图片（会按顺序合成一份多页 PDF）；"
                "PDF/DOCX/Excel 请逐个上传",
            )
        # 每张图先走一遍同样的魔数/大小校验，再合并，错误信息更精确
        for name, blob in payloads:
            validate_upload(name, blob, allowed=MAIN_FILE_EXTENSIONS)
        pdf_bytes, original_filename, merge_warning = await run_in_threadpool(
            file_service.merge_images_to_pdf, payloads, title=doc.title
        )
        if len(pdf_bytes) > MAX_UPLOAD_BYTES:
            raise AppError(
                "FILE_TOO_LARGE",
                f"合并后的 PDF 超过大小限制（最大 {MAX_UPLOAD_BYTES // 1024 // 1024}MB），"
                "请减少图片数量或压缩后再试",
            )
        data = pdf_bytes

    # 2026-10-16：流式读取保留 async（await file.read），但其后的同步处理段
    # （魔数校验 + DB 事务 + shutil.move 旧文件进回收站）包进线程池，
    # 避免大文件替换时阻塞事件循环、拖住同期所有请求
    result = await run_in_threadpool(
        file_service.replace_document_file,
        db,
        document=doc,
        original_filename=original_filename,
        data=data,
        user=current_user,
        ip=ip,
        user_agent=ua,
    )
    if merge_warning:
        result = dict(result or {})
        result["merged"] = True
        result["merge_note"] = merge_warning
        result["merged_count"] = len(payloads)
    return result


@router.delete("/{doc_id}")
def archive_document(
    doc_id: int,
    request: Request,
    payload: Optional[ArchiveRequest] = None,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """归档/删除资料（软删，§26；文件进回收站）。"""
    ip, ua = auth_service.client_info(request)
    reason = payload.reason if payload else None
    return document_service.archive_document(
        db, current_user=current_user, doc_id=doc_id, reason=reason, ip=ip, user_agent=ua
    )


@router.post("/{doc_id}/restore")
def restore_document(
    doc_id: int,
    request: Request,
    payload: Optional[RestoreRequest] = None,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """恢复归档资料（§26）。"""
    ip, ua = auth_service.client_info(request)
    return document_service.restore_document(
        db,
        current_user=current_user,
        doc_id=doc_id,
        reason=payload.reason if payload else None,
        ip=ip,
        user_agent=ua,
    )