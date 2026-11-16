"""通知路由（docs/API.md §7）。

- 创建/修改支持 JSON 或 multipart（含可选通知文件，走同一文件安全管线）
- 发布/取消/删除草稿/阅读/确认/统计/应阅读名单/关联资料/文件下载预览
"""
from __future__ import annotations

import json as json_lib
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from app.core.config import get_settings
from app.core.deps import get_current_user, require_roles
from app.core.errors import AppError
from app.db.session import get_db
from app.models.user import User
from app.schemas.notice import (
    CancelRequest,
    NoticeCreate,
    NoticeTarget,
    NoticeUpdate,
)
from app.services import (
    auth_service,
    file_service,
    notice_read,
    notice_recycle,
    notice_service,
    preview_service,
)

router = APIRouter(prefix="/api/v1/notices", tags=["notices"])


class BulkCancelRequest(BaseModel):
    notice_ids: list[int]
    reason: Optional[str] = Field(default=None, max_length=255)


admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员

MAX_UPLOAD_BYTES = get_settings().MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _parse_datetime(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise AppError("VALIDATION_ERROR", f"时间格式应为 ISO8601: {value}")


def _parse_target(raw) -> NoticeTarget:
    if raw in (None, ""):
        return NoticeTarget(scope="all")
    try:
        return NoticeTarget.model_validate(json_lib.loads(raw))
    except Exception as exc:  # noqa: BLE001
        raise AppError("VALIDATION_ERROR", f"target 格式错误: {exc}")


def _parse_ids(raw, field: str) -> list[int]:
    if raw in (None, ""):
        return []
    try:
        data = json_lib.loads(raw) if isinstance(raw, str) else raw
        return [int(x) for x in data]
    except Exception as exc:  # noqa: BLE001
        raise AppError("VALIDATION_ERROR", f"{field} 格式错误: {exc}")


def _is_upload_file(obj) -> bool:
    """request.form() 返回的是 starlette 的 UploadFile（fastapi.UploadFile 判断不到）。"""
    return hasattr(obj, "read") and hasattr(obj, "filename")


def _upload_of(form: FormData) -> Optional[UploadFile]:
    """取 file 字段且确为上传文件（isinstance 收窄，避免鸭子类型误判）。"""
    value = form.get("file")
    return value if isinstance(value, UploadFile) else None


def _form_str(form: FormData, key: str, default: Optional[str] = None) -> Optional[str]:
    """multipart 文本字段只接受 str；文件误投到文本位 → 400（原先由 pydantic 拒绝）。"""
    value = form.get(key, default)
    if value is None or isinstance(value, str):
        return value
    raise AppError("VALIDATION_ERROR", f"{key} 必须是文本字段")


def _form_str_or(form: FormData, key: str, default: str) -> str:
    """同 _form_str，但字段缺失/为空串时返回 default，恒为 str（必填字段用）。"""
    value = form.get(key, default)
    if isinstance(value, str):
        return value
    raise AppError("VALIDATION_ERROR", f"{key} 必须是文本字段")


async def _read_upload_limited(file: UploadFile) -> bytes:
    """分块读取上传文件，边读边限大小（与资料上传流式口径一致，防整读进内存）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise AppError(
                "FILE_TOO_LARGE",
                f"文件大小超过限制（最大 {MAX_UPLOAD_BYTES // 1024 // 1024}MB）",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _fix_filename(name: Optional[str]) -> Optional[str]:
    """multipart 原始 filename 常为 latin-1 误码（RFC 2045 头），尝试还原 UTF-8。"""
    if not name:
        return name
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


async def _read_payload(request: Request):
    """解析通知请求体：优先 multipart（含 file），否则 JSON。"""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        file_upload = _upload_of(form)
        file_data = (
            await _read_upload_limited(file_upload) if file_upload is not None else None
        )
        file_name = _fix_filename(file_upload.filename) if file_upload is not None else None
        try:
            payload = NoticeCreate(
                title=_form_str_or(form, "title", ""),
                content=_form_str(form, "content"),
                notice_type=_form_str(form, "notice_type") or None,
                effective_at=_parse_datetime(_form_str(form, "effective_at")),
                deadline_at=_parse_datetime(_form_str(form, "deadline_at")),
                document_ids=_parse_ids(_form_str(form, "document_ids"), "document_ids"),
                target=_parse_target(_form_str(form, "target")),
            )
        except Exception as exc:  # pydantic 校验失败 → 400 VALIDATION_ERROR（契约 §7）
            raise AppError("VALIDATION_ERROR", f"请求参数校验失败: {exc}") from exc
        return payload, file_data, file_name
    body = await request.json()
    try:
        payload = NoticeCreate.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        raise AppError("VALIDATION_ERROR", f"请求参数校验失败: {exc}") from exc
    return payload, None, None


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
@router.get("")
def list_notices(
    type: Optional[str] = Query(default=None, alias="type"),
    unread_only: bool = Query(default=False),
    status: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通知列表：user 仅见已发布且生效且 target 含本人的；admin 全部。

    date_from/date_to：按发布时间范围过滤（date_to 含当日全天）。
    """
    return notice_read.list_notices(
        db,
        user=current_user,
        notice_type=type,
        unread_only=unread_only,
        status_filter=status,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@router.get("/{notice_id}")
def get_notice(
    notice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通知详情（不自动置读，§20；user 仅 target 内可见）。"""
    return notice_read.get_notice(db, user=current_user, notice_id=notice_id)


@router.get("/{notice_id}/statistics")
def get_statistics(
    notice_id: int,
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """阅读统计（§21，admin+）。"""
    return notice_read.get_statistics(db, user=_, notice_id=notice_id)


@router.get("/{notice_id}/audience")
def get_audience(
    notice_id: int,
    read_status: Optional[str] = Query(default="all"),
    keyword: Optional[str] = Query(default=None, max_length=64),
    department_id: Optional[int] = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """应阅读名单（admin+）。keyword 按姓名/账号/部门搜索（名单可能上百人）。"""
    return notice_read.get_audience(
        db, user=_, notice_id=notice_id, read_status=read_status,
        keyword=keyword, department_id=department_id,
        page=page, page_size=page_size,
    )


@router.get("/{notice_id}/documents")
def get_documents(
    notice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """关联资料（§19）。"""
    return notice_read.get_linked_documents(
        db, user=current_user, notice_id=notice_id
    )


# ---------------------------------------------------------------------------
# 文件下载 / 预览（通知可见性校验）
# ---------------------------------------------------------------------------
@router.get("/{notice_id}/file")
def download_file(
    notice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载通知原始文件（§17、§12、§33）。

    2026-10-16：async def → def（无 await 点，同步定义走线程池，不占事件循环）。
    """
    _, rec = notice_read.notice_file_visible(
        db, user=current_user, notice_id=notice_id
    )
    path = file_service.abs_path(rec.storage_rel_path)
    if not path.exists():
        raise AppError("NOT_FOUND", "文件不存在（可能已损坏）")
    return FileResponse(
        path=path, filename=rec.original_filename, media_type=rec.mime_type,
        content_disposition_type="attachment",
    )


@router.get("/{notice_id}/preview/pdf")
def preview_pdf(
    notice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通知 PDF 内联预览（§12）。

    2026-10-16：async def → def（无 await 点，同步定义走线程池，不占事件循环）。
    """
    _, rec = notice_read.notice_file_visible(
        db, user=current_user, notice_id=notice_id
    )
    info = preview_service.preview_pdf(rec)
    return FileResponse(
        path=info["path"], media_type=info["media_type"],
        filename=info["filename"], content_disposition_type="inline",
    )


@router.get("/{notice_id}/preview/image")
def preview_image(
    notice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通知图片内联预览（§12）。"""
    _, rec = notice_read.notice_file_visible(
        db, user=current_user, notice_id=notice_id
    )
    info = preview_service.preview_image(rec)
    return FileResponse(
        path=info["path"], media_type=info["media_type"],
        filename=info["filename"], content_disposition_type="inline",
    )


@router.get("/{notice_id}/preview/docx")
def preview_docx(
    notice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, rec = notice_read.notice_file_visible(
        db, user=current_user, notice_id=notice_id
    )
    return preview_service.preview_docx(rec)


@router.get("/{notice_id}/preview/xlsx")
def preview_xlsx(
    notice_id: int,
    sheet: int = Query(default=0, ge=0),
    max_rows: Optional[int] = Query(default=None, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, rec = notice_read.notice_file_visible(
        db, user=current_user, notice_id=notice_id
    )
    return preview_service.preview_xlsx(rec, sheet=sheet, max_rows=max_rows)


# ---------------------------------------------------------------------------
# 管理操作（admin+）
# ---------------------------------------------------------------------------
@router.post("", status_code=201)
async def create_notice(
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建通知（草稿）：JSON 或 multipart；正文/文件至少其一。"""
    payload, file_data, file_name = await _read_payload(request)
    ip, ua = auth_service.client_info(request)
    # 2026-10-16：请求体读取保留 async（await request.form/read），
    # 同步处理段（校验 + DB + 文件落盘）包进线程池，不占事件循环
    return await run_in_threadpool(
        notice_service.create_notice,
        db,
        current_user=current_user,
        title=payload.title,
        content=payload.content,
        notice_type=payload.notice_type,
        effective_at=payload.effective_at,
        deadline_at=payload.deadline_at,
        document_ids=payload.document_ids,
        target=payload.target,
        file_data=file_data,
        file_name=file_name,
        ip=ip,
        user_agent=ua,
    )


@router.put("/{notice_id}")
async def update_notice(
    notice_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """修改通知（§22：已发布且已读 → confirm_modify + modified_reason）。"""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        file_upload = _upload_of(form)
        file_data = (
            await _read_upload_limited(file_upload) if file_upload is not None else None
        )
        file_name = _fix_filename(file_upload.filename) if file_upload is not None else None
        document_ids_raw = _form_str(form, "document_ids")
        target_raw = _form_str(form, "target")
        try:
            payload = NoticeUpdate(
                title=_form_str(form, "title") or None,
                content=_form_str(form, "content"),
                notice_type=_form_str(form, "notice_type") or None,
                effective_at=_parse_datetime(_form_str(form, "effective_at")),
                deadline_at=_parse_datetime(_form_str(form, "deadline_at")),
                document_ids=(
                    _parse_ids(document_ids_raw, "document_ids")
                    if document_ids_raw not in (None, "")
                    else None
                ),
                target=(
                    _parse_target(target_raw)
                    if target_raw not in (None, "")
                    else None
                ),
                confirm_modify=(_form_str(form, "confirm_modify", "false") or "false").lower() in ("true", "1"),
                modified_reason=_form_str(form, "modified_reason") or None,
            )
        except Exception as exc:  # pydantic 校验失败 → 400 VALIDATION_ERROR（契约 §7）
            raise AppError("VALIDATION_ERROR", f"请求参数校验失败: {exc}") from exc
    else:
        body = await request.json()
        try:
            payload = NoticeUpdate.model_validate(body)
        except Exception as exc:  # noqa: BLE001
            raise AppError("VALIDATION_ERROR", f"请求参数校验失败: {exc}") from exc
        file_data, file_name = None, None

    ip, ua = auth_service.client_info(request)
    # 2026-10-16：请求体读取保留 async，同步处理段包进线程池（同 create_notice）
    return await run_in_threadpool(
        notice_service.update_notice,
        db,
        current_user=current_user,
        notice_id=notice_id,
        title=payload.title,
        content=payload.content,
        notice_type=payload.notice_type,
        effective_at=payload.effective_at,
        deadline_at=payload.deadline_at,
        clear_deadline=(
            "deadline_at" in payload.model_fields_set and payload.deadline_at is None
        ),
        document_ids=payload.document_ids,
        target=payload.target,
        confirm_modify=payload.confirm_modify,
        modified_reason=payload.modified_reason,
        file_data=file_data,
        file_name=file_name,
        ip=ip,
        user_agent=ua,
    )


# 2026-11-04：POST /{id}/publish 已移除——创建即发布（POST /notices 单事务完成），
# 不存在「先建草稿再发布」的两步流程，因此也没有可发布的中间态。


@router.post("/bulk-cancel")
def bulk_cancel_notices(
    payload: BulkCancelRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """批量软删除通知（仅管理员；逐条返回成功/跳过原因 + 汇总审计）。"""
    ip, ua = auth_service.client_info(request)
    return notice_recycle.bulk_cancel_notices(
        db,
        current_user=current_user,
        notice_ids=payload.notice_ids,
        reason=payload.reason,
        ip=ip,
        user_agent=ua,
    )


@router.post("/{notice_id}/cancel")
def cancel_notice(
    notice_id: int,
    payload: CancelRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """取消通知（软取消，保留统计证据）。"""
    ip, ua = auth_service.client_info(request)
    return notice_recycle.cancel_notice(
        db, current_user=current_user, notice_id=notice_id,
        reason=payload.reason, ip=ip, user_agent=ua,
    )


# 2026-11-04：DELETE /{id}（原「删除草稿」）已移除——草稿下线后，
# 通知删除统一走 POST /{id}/cancel（进回收站，可恢复）。


@router.post("/{notice_id}/read")
def mark_read(
    notice_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记已查看（§20：置 read_at，首次覆盖、幂等）。"""
    ip, ua = auth_service.client_info(request)
    return notice_read.mark_read(
        db, user=current_user, notice_id=notice_id, ip=ip, user_agent=ua
    )


# 2026-11-03 产品决策：/confirm 接口已移除（确认功能整体下线，只保留 /read）。