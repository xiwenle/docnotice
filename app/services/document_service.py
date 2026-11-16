"""资料服务（docs/API.md §6、DESIGN.md §7、项目规范 §8–§16）。

- 状态动态计算（validity_service），不存库
- 归档资料仅 admin+ 可见/可读（§33）
- R1/R2 时效冲突校验（§16）
- 删除=归档（软删，§26）；文件进回收站（file_service）
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.cache import cache, cache_key, invalidate_lists
from app.db.session import total_count
from app.core.errors import AppError
from app.models.backup import RecycleBin
from app.models.category import Category, Tag, document_tags
from app.models.document import Document, FileRecord
from app.models.notice import Notice, notice_documents
from app.models.user import User
from app.schemas.common import page_meta
from app.services.notice_audience import is_user_in_target, user_department_ids
from app.services import (
    audit_service,
    category_service,
    document_visibility,
    file_service,
    validity_service,
)

ADMIN_ROLES = ("admin",)

# 「即将到期」窗口天数：status=expiring 筛选与工作台待办卡共用同一真值
EXPIRING_WITHIN_DAYS = 30


# ---------------------------------------------------------------------------
# 出站对象
# ---------------------------------------------------------------------------
def _category_path(db: Session, doc: Document) -> list[str]:
    path: list[str] = []
    cat = doc.category
    while cat is not None:
        path.insert(0, cat.name)
        cat = cat.parent
    return path


def document_to_dict(
    db: Session,
    doc: Document,
    *,
    as_of_date: date_type,
    detailed: bool = False,
    viewer: Optional[User] = None,
) -> dict:
    """资料出站对象（API.md §6 列表项 / 详情）。"""
    current_file = doc.current_file
    status = validity_service.compute_status(
        doc.effective_from, doc.effective_to, as_of_date,
        archived=doc.archived_at is not None,
    )
    data = {
        "id": doc.id,
        "title": doc.title,
        "summary": doc.summary,
        "category_id": doc.category_id,
        "category_path": _category_path(db, doc),
        "tag_ids": [t.id for t in doc.tags],
        "tags": [{"id": t.id, "name": t.name} for t in doc.tags],
        "effective_from": doc.effective_from.isoformat(),
        "effective_to": doc.effective_to.isoformat() if doc.effective_to else None,
        "has_file": current_file is not None,
        "file_type": current_file.file_type if current_file else None,
        "file_size_bytes": current_file.size_bytes if current_file else None,
        # 附件（2026-11-06）：列表页只用这一列判空，绝不加载 attachment_file
        # 关系——否则每页 20 条就多 20 次查询，列表页会被拖慢。
        "has_attachment": doc.attachment_file_id is not None,
        "status": status,
        "as_of_date": as_of_date.isoformat(),
        "rev_count": doc.rev_count,
        "archived": doc.archived_at is not None,
        "created_by": doc.created_by,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        # 可见范围（2026-11-03）：列表/详情都要带，前端据此显示徽章
        "audience_scope": doc.audience_scope,
        "audience_label": document_visibility.scope_label(doc),
    }
    if detailed:
        data["audience_department_ids"] = [
            t.department_id for t in doc.targets if t.target_type == "department"
        ]
        data["audience_user_ids"] = [
            t.user_id for t in doc.targets if t.target_type == "user"
        ]
        data["category"] = (
            {
                "id": doc.category.id,
                "name": doc.category.name,
                "parent": (
                    {"id": doc.category.parent.id, "name": doc.category.parent.name}
                    if doc.category.parent
                    else None
                ),
            }
            if doc.category
            else None
        )
        data["tags"] = [{"id": t.id, "name": t.name} for t in doc.tags]
        data["current_file"] = (
            file_service.file_record_to_dict(current_file) if current_file else None
        )
        attachment = doc.attachment_file
        data["attachment_file"] = (
            file_service.file_record_to_dict(attachment) if attachment else None
        )
        data["linked_notices"] = _linked_notices(db, doc.id, viewer=viewer)
    return data


def _linked_notices(
    db: Session, document_id: int, *, viewer: Optional[User] = None
) -> list[dict]:
    """资料关联通知（§19），统一可见性口径（与通知列表/回收站规则一致）：

    - 已删除（cancelled）通知一律不显示——已删除通知统一在回收站管理，
      不再出现在资料详情/最近通知等任何业务列表中；
    - **阅读对象过滤（2026-09-29 修复）**：非管理员只能看到自己属于阅读对象的
      通知。原先只挡 cancelled / draft，普通用户在资料详情或关联通知列表里
      能读到与己无关通知的标题、类型、状态、生效时间——属跨阅读对象的元数据泄露，
      与 /notices 列表口径不一致。
    - 2026-11-04：草稿状态下线，`draft` 分支已移除（通知只有 published/cancelled）。
    """
    rows = db.execute(
        select(Notice)
        .options(selectinload(Notice.targets))
        .join(notice_documents, notice_documents.c.notice_id == Notice.id)
        .where(notice_documents.c.document_id == document_id)
        .order_by(Notice.id.desc())
    ).scalars().all()
    is_admin = viewer is not None and viewer.role == User.ROLE_ADMIN
    # 部门集合只查一次（逐条判定时复用，避免 N+1）
    dept_ids: Optional[set[int]] = None
    if viewer is not None and not is_admin:
        dept_ids = user_department_ids(db, viewer.id)
    now = datetime.now()
    items = []
    for n in rows:
        if n.status == "cancelled":
            continue
        if viewer is not None and not is_admin:
            # 未到生效时间 = 尚未发布给用户看（与通知列表口径一致，2026-11-04 补齐：
            # 此前只挡了 cancelled/draft，普通用户能从资料详情提前看到未来生效的通知）
            if n.effective_at is not None and n.effective_at > now:
                continue
            if not is_user_in_target(db, n, viewer, dept_ids=dept_ids):
                continue
        items.append(
            {
                "id": n.id,
                "title": n.title,
                "notice_type": n.notice_type,
                "status": n.status,
                "effective_at": n.effective_at,
            }
        )
    return items


def _load_document(db: Session, doc_id: int) -> Document:
    doc = db.execute(
        select(Document)
        .options(selectinload(Document.current_file), selectinload(Document.tags))
        .where(Document.id == doc_id)
    ).scalar_one_or_none()
    if doc is None:
        raise AppError("NOT_FOUND", "资料不存在")
    return doc


def _require_visible(user: User, doc: Document, db: Optional[Session] = None) -> None:
    """普通用户不可访问归档资料，也不可访问可见范围外的资料。

    返回 404 而非 403：不泄露"确实存在这份资料但你没权限"的信息
    （与通知的 403 不同——通知是主动推送给你的，资料是检索出来的，
    用 404 更符合"查不到"的语义）。
    """
    if doc.archived_at is not None and user.role != "admin":
        raise AppError("FORBIDDEN", "资料已归档，无权访问")
    if db is not None and not document_visibility.is_document_visible(db, doc, user):
        raise AppError("NOT_FOUND", "资料不存在或无权查看")


def _load_category(db: Session, category_id: int) -> Category:
    """加载分类并校验是否停用（前后端一致拦截）。"""
    cat = db.get(Category, category_id)
    if cat is None:
        raise AppError("VALIDATION_ERROR", f"分类不存在: {category_id}")
    if not cat.is_active:
        raise AppError("VALIDATION_ERROR", f"分类「{cat.name}」已停用，请选择其他分类")
    return cat


def _load_tags(db: Session, tag_ids: list[int]) -> list[Tag]:
    if not tag_ids:
        return []
    tags = db.execute(select(Tag).where(Tag.id.in_(tag_ids))).scalars().all()
    found = {t.id for t in tags}
    missing = [i for i in tag_ids if i not in found]
    if missing:
        raise AppError("VALIDATION_ERROR", f"标签不存在: {missing}")
    return list(tags)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def build_document_conditions(
    db: Session,
    *,
    user: User,
    keyword: Optional[str] = None,
    category_id: Optional[int] = None,
    tag_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    as_of_date: Optional[date_type] = None,
    cover_from: Optional[date_type] = None,
    cover_to: Optional[date_type] = None,
    uncategorized: bool = False,
    untagged: bool = False,
    audience_scope: Optional[str] = None,
    has_attachment: Optional[bool] = None,
    file_type: Optional[str] = None,
    missing_main_file: bool = False,
    archived_only: bool = False,
) -> tuple[list, date_type]:
    """构造资料列表和批量筛选共用的 SQL 条件。"""
    from datetime import date as date_cls

    if as_of_date is None:
        as_of_date = date_cls.today()
    conds: list = []

    vis = document_visibility.visibility_condition(user)
    if vis is not None:
        conds.append(vis)

    if archived_only:
        conds.append(Document.archived_at.isnot(None))
    elif user.role == User.ROLE_ADMIN and status_filter == "archived":
        conds.append(Document.archived_at.isnot(None))
    else:
        conds.append(Document.archived_at.is_(None))

    if keyword:
        like = f"%{keyword}%"
        conds.append(or_(Document.title.like(like), Document.summary.like(like)))
    if category_id is not None:
        conds.append(
            Document.category_id.in_(category_service.subtree_ids(db, category_id))
        )
    if tag_id is not None:
        conds.append(
            exists().where(
                and_(
                    document_tags.c.document_id == Document.id,
                    document_tags.c.tag_id == tag_id,
                )
            )
        )
    if uncategorized:
        conds.append(Document.category_id.is_(None))
    if untagged:
        conds.append(~exists().where(document_tags.c.document_id == Document.id))
    # 包含关系：资料有效期 D 覆盖查询边界 Q（Q ⊆ D）。
    # - 只填 cover_from：该日期必须落在 D 内；
    # - 只填 cover_to：该日期必须落在 D 内；
    # - 两端都填：D 必须覆盖整个 [cover_from, cover_to] 区间。
    # effective_to 为空表示暂无失效日期，计算上视为没有已知右边界。
    if cover_from is not None and cover_to is not None:
        conds.append(
            and_(
                Document.effective_from <= cover_from,
                or_(
                    Document.effective_to.is_(None),
                    Document.effective_to >= cover_to,
                ),
            )
        )
    elif cover_from is not None:
        conds.append(
            and_(
                Document.effective_from <= cover_from,
                or_(
                    Document.effective_to.is_(None),
                    Document.effective_to >= cover_from,
                ),
            )
        )
    elif cover_to is not None:
        conds.append(
            and_(
                Document.effective_from <= cover_to,
                or_(
                    Document.effective_to.is_(None),
                    Document.effective_to >= cover_to,
                ),
            )
        )
    if status_filter and status_filter != "archived":
        if status_filter == "pending":
            conds.append(Document.effective_from > as_of_date)
        elif status_filter == "expired":
            conds.append(
                and_(Document.effective_to.isnot(None), Document.effective_to < as_of_date)
            )
        elif status_filter == "active":
            conds.append(
                and_(
                    Document.effective_from <= as_of_date,
                    or_(
                        Document.effective_to.is_(None),
                        Document.effective_to >= as_of_date,
                    ),
                )
            )
        elif status_filter == "expiring":
            conds.append(
                and_(
                    Document.effective_from <= as_of_date,
                    Document.effective_to.isnot(None),
                    Document.effective_to >= as_of_date,
                    Document.effective_to <= as_of_date
                    + timedelta(days=EXPIRING_WITHIN_DAYS),
                )
            )

    if audience_scope:
        conds.append(Document.audience_scope == audience_scope)
    if has_attachment is True:
        conds.append(Document.attachment_file_id.isnot(None))
    elif has_attachment is False:
        conds.append(Document.attachment_file_id.is_(None))
    if file_type:
        conds.append(
            exists().where(
                and_(
                    FileRecord.id == Document.current_file_id,
                    FileRecord.file_type == file_type,
                )
            )
        )
    if missing_main_file:
        conds.append(Document.current_file_id.is_(None))
    return conds, as_of_date


def list_documents(
    db: Session,
    *,
    user: User,
    keyword: Optional[str] = None,
    category_id: Optional[int] = None,
    tag_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    as_of_date: Optional[date_type] = None,
    cover_from: Optional[date_type] = None,
    cover_to: Optional[date_type] = None,
    uncategorized: bool = False,
    untagged: bool = False,
    audience_scope: Optional[str] = None,
    has_attachment: Optional[bool] = None,
    file_type: Optional[str] = None,
    missing_main_file: bool = False,
    sort: str = "id_desc",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """资料列表/搜索（§1、§14、§15）。"""
    conds, as_of_date = build_document_conditions(
        db,
        user=user,
        keyword=keyword,
        category_id=category_id,
        tag_id=tag_id,
        status_filter=status_filter,
        as_of_date=as_of_date,
        cover_from=cover_from,
        cover_to=cover_to,
        uncategorized=uncategorized,
        untagged=untagged,
        audience_scope=audience_scope,
        has_attachment=has_attachment,
        file_type=file_type,
        missing_main_file=missing_main_file,
    )
    key = cache_key(
        "doc_list", user.id, user.role, keyword, category_id, tag_id, status_filter,
        as_of_date, cover_from, cover_to, uncategorized, untagged,
        audience_scope, has_attachment, file_type, missing_main_file, sort,
        page, page_size,
    )
    hit = cache.get(key)
    if hit is not None:
        return hit

    total = total_count(db, select(Document.id).where(*conds))
    order_by: Any
    if sort == "created_desc":
        order_by = Document.created_at.desc()
    elif sort == "updated_desc":
        order_by = Document.updated_at.desc()
    elif sort == "title_asc":
        order_by = Document.title.asc()
    elif sort == "effective_from_desc":
        order_by = Document.effective_from.desc()
    elif sort == "effective_to_asc":
        order_by = Document.effective_to.asc()
    else:
        order_by = Document.id.desc()
    stmt = (
        select(Document)
        .options(
            selectinload(Document.current_file),
            selectinload(Document.tags),
            selectinload(Document.category).selectinload(Category.parent),
        )
        .where(*conds)
        .order_by(order_by)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    docs = db.execute(stmt).scalars().all()
    items = [document_to_dict(db, doc, as_of_date=as_of_date) for doc in docs]
    result = {"items": items, **page_meta(total, page, page_size)}
    cache.set(key, result)
    return result

def bulk_update_documents(
    db: Session,
    *,
    current_user: User,
    doc_ids: list[int],
    set_category_id: Optional[int] = None,
    clear_category: bool = False,
    add_tag_id: Optional[int] = None,
    archive: bool = False,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """批量整理资料（仅管理员，路由层强制）。

    - archive：逐条复用 archive_document（进回收站、留档、审计），失败逐条记录
    - 其余：同事务内批量更新分类/追加标签，一条审计记录整体留痕
    """
    ids = sorted(set(doc_ids))
    if not ids:
        raise AppError("VALIDATION_ERROR", "document_ids 不能为空")
    if set_category_id is not None and clear_category:
        raise AppError("VALIDATION_ERROR", "set_category_id 与 clear_category 不能同时使用")
    if archive and (set_category_id is not None or clear_category or add_tag_id is not None):
        raise AppError("VALIDATION_ERROR", "archive 不能与分类或标签操作同时使用")
    if not archive and set_category_id is None and not clear_category and add_tag_id is None:
        raise AppError("VALIDATION_ERROR", "至少提供一项批量操作（分类/标签/删除）")

    skipped: list[dict] = []

    if archive:
        archived = 0
        for doc_id in ids:
            try:
                archive_document(
                    db,
                    current_user=current_user,
                    doc_id=doc_id,
                    reason=(reason or "").strip() or "批量删除",
                    ip=ip,
                    user_agent=user_agent,
                )
                archived += 1
            except AppError as exc:
                skipped.append({"id": doc_id, "reason": exc.message})
        return {"archived": archived, "skipped": skipped}

    if set_category_id is not None:
        _load_category(db, set_category_id)  # 校验存在且未停用
    add_tag = None
    if add_tag_id is not None:
        add_tag = db.get(Tag, add_tag_id)
        if add_tag is None:
            raise AppError("VALIDATION_ERROR", "目标标签不存在")

    docs = (
        db.execute(
            select(Document)
            .options(selectinload(Document.tags))
            .where(Document.id.in_(ids))
        )
        .scalars()
        .all()
    )
    found = {d.id for d in docs}
    for doc_id in ids:
        if doc_id not in found:
            skipped.append({"id": doc_id, "reason": "资料不存在"})

    updated = 0
    for doc in docs:
        if doc.archived_at is not None:
            skipped.append({"id": doc.id, "reason": "已删除（回收站中），请先恢复"})
            continue
        touched = False
        if set_category_id is not None:
            doc.category_id = set_category_id
            touched = True
        if clear_category:
            doc.category_id = None
            touched = True
        if add_tag is not None and all(t.id != add_tag.id for t in doc.tags):
            doc.tags.append(add_tag)
            touched = True
        if touched:
            doc.updated_by = current_user.id
            updated += 1

    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_DOCUMENT,
        user=current_user,
        detail={
            "bulk": True,
            "ids": ids,
            "set_category_id": set_category_id,
            "clear_category": clear_category,
            "add_tag_id": add_tag_id,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_lists()
    return {"updated": updated, "skipped": skipped}


def get_document(db: Session, *, user: User, doc_id: int) -> dict:
    doc = _load_document(db, doc_id)
    _require_visible(user, doc, db)
    return document_to_dict(
        db, doc, as_of_date=datetime.now().date(), detailed=True, viewer=user
    )


def get_document_status(
    db: Session, *, user: User, doc_id: int, as_of_date: date_type
) -> dict:
    doc = _load_document(db, doc_id)
    _require_visible(user, doc, db)
    status = validity_service.compute_status(
        doc.effective_from, doc.effective_to, as_of_date,
        archived=doc.archived_at is not None,
    )
    return {
        "status": status,
        "effective_from": doc.effective_from.isoformat(),
        "effective_to": doc.effective_to.isoformat() if doc.effective_to else None,
        "as_of_date": as_of_date.isoformat(),
    }


def get_linked_notices(db: Session, *, user: User, doc_id: int) -> dict:
    doc = _load_document(db, doc_id)
    _require_visible(user, doc, db)
    return {"items": _linked_notices(db, doc_id, viewer=user)}


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------
def _validate_audience(
    db: Session,
    scope: str,
    department_ids: Optional[list[int]],
    user_ids: Optional[list[int]],
) -> None:
    """校验可见范围参数（与通知的 target 校验同口径）。"""
    from app.models.user import Department

    if scope == "department":
        ids = department_ids or []
        if not ids:
            raise AppError("VALIDATION_ERROR", "可见范围为「指定部门」时必须至少选一个部门")
        found = set(
            db.execute(
                select(Department.id).where(
                    Department.id.in_(ids), Department.is_active.is_(True)
                )
            ).scalars().all()
        )
        missing = set(ids) - found
        if missing:
            raise AppError("VALIDATION_ERROR", f"部门不存在或已停用: {sorted(missing)}")
    elif scope == "user":
        ids = user_ids or []
        if not ids:
            raise AppError("VALIDATION_ERROR", "可见范围为「指定人员」时必须至少选一个人")
        found = set(
            db.execute(
                select(User.id).where(
                    User.id.in_(ids),
                    User.is_active.is_(True),
                    User.role != User.ROLE_ADMIN,
                )
            ).scalars().all()
        )
        missing = set(ids) - found
        if missing:
            raise AppError(
                "VALIDATION_ERROR",
                "指定人员只能是启用的普通用户；管理员默认可见全部资料，"
                f"无需也不能作为可见目标: {sorted(missing)}",
            )
    elif scope != "all":
        raise AppError("VALIDATION_ERROR", f"非法可见范围: {scope}")


def create_document(
    db: Session,
    *,
    current_user: User,
    title: str,
    category_id: Optional[int],
    summary: Optional[str],
    effective_from: date_type,
    effective_to: Optional[date_type],
    tag_ids: list[int],
    audience_scope: str = "all",
    audience_department_ids: Optional[list[int]] = None,
    audience_user_ids: Optional[list[int]] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """新建资料（元数据，可稍后传文件；R1 校验，§13/§14/§16）。"""
    validity_service.check_validity_r1(effective_from, effective_to)
    if category_id is not None:
        _load_category(db, category_id)  # 校验存在且未停用
    _validate_audience(db, audience_scope, audience_department_ids, audience_user_ids)

    doc = Document(
        title=title.strip(),
        summary=summary,
        category_id=category_id,
        effective_from=effective_from,
        effective_to=effective_to,
        rev_count=0,  # 尚未上传文件；上传时 +1（API.md rev_count 语义）
        created_by=current_user.id,
    )
    doc.tags = _load_tags(db, tag_ids)
    document_visibility.apply_targets(
        db, doc, audience_scope,
        department_ids=audience_department_ids,
        user_ids=audience_user_ids,
    )
    db.add(doc)
    db.flush()

    audit_service.log(
        db,
        action=audit_service.ACTION_CREATE_DOCUMENT,
        user=current_user,
        object_type="document",
        object_id=doc.id,
        object_label=doc.title,
        detail={
            "category_id": category_id,
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat() if effective_to else None,
            "tag_ids": tag_ids,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_lists()
    return get_document(db, user=current_user, doc_id=doc.id)


def update_document(
    db: Session,
    *,
    current_user: User,
    doc_id: int,
    title: Optional[str] = None,
    category_id: Optional[int] = None,
    summary: Optional[str] = None,
    effective_from: Optional[date_type] = None,
    effective_to: Optional[date_type] = None,
    clear_effective_to: bool = False,
    tag_ids: Optional[list[int]] = None,
    audience_scope: Optional[str] = None,
    audience_department_ids: Optional[list[int]] = None,
    audience_user_ids: Optional[list[int]] = None,
    confirm_overwrite: bool = False,
    modify_reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """修改资料元数据（§14、§16 R2、§25）。

    clear_effective_to=True 时把 effective_to 显式置 null（清除失效日）；
    不传则保留原值。这是 schema "传 null 表示清除" 的实现（通过 router 传入）。
    """
    doc = _load_document(db, doc_id)
    if doc.archived_at is not None:
        raise AppError("CONFLICT", "已归档资料不能修改")

    if category_id is not None:
        _load_category(db, category_id)  # 校验存在且未停用

    # ---- 时效改动：R1 + R2（§16）----
    # clear_effective_to=True → 显式清除失效日期；effective_from/effective_to 为 None 且未清除 → 保持原值
    # 2026-09-29 修复：原先判据只认 effective_from / clear_effective_to，于是
    # PUT {"effective_to": "2027-01-01"}（schema §13 明确允许「可部分」）会整个跳过
    # 时效分支 —— 新失效日被**静默丢弃**（不改库也不报错）。补上 effective_to。
    effective_to_explicit = effective_to if clear_effective_to else None
    validity_changed = (
        (effective_from is not None) or (effective_to is not None) or clear_effective_to
    )
    if validity_changed:
        new_from = effective_from if effective_from is not None else doc.effective_from
        new_to = effective_to_explicit if clear_effective_to else (effective_to if effective_to is not None else doc.effective_to)
        # 语义：new == 旧值视为未改动
        actually_changed = new_from != doc.effective_from or new_to != doc.effective_to
        if actually_changed:
            validity_service.check_validity_r1(new_from, new_to)
            validity_service.require_confirm_overwrite(
                old_from=doc.effective_from,
                old_to=doc.effective_to,
                new_from=new_from,
                new_to=new_to,
                confirm_overwrite=confirm_overwrite,
            )
            if confirm_overwrite:
                if not (modify_reason or "").strip():
                    raise AppError(
                        "VALIDATION_ERROR",
                        "覆盖有效期必须填写修改原因（modify_reason）",
                    )
                audit_service.log(
                    db,
                    action=audit_service.ACTION_MODIFY_VALIDITY,
                    user=current_user,
                    object_type="document",
                    object_id=doc.id,
                    object_label=doc.title,
                    detail={
                        "old": {
                            "effective_from": doc.effective_from.isoformat(),
                            "effective_to": (
                                doc.effective_to.isoformat() if doc.effective_to else None
                            ),
                        },
                        "new": {
                            "effective_from": new_from.isoformat(),
                            "effective_to": (
                                new_to.isoformat() if new_to else None
                            ),
                        },
                        "reason": modify_reason,
                    },
                    ip=ip,
                    user_agent=user_agent,
                )
            doc.effective_from = new_from
            doc.effective_to = new_to

    # ---- 普通字段 ----
    if title is not None:
        doc.title = title.strip()
    if category_id is not None:
        doc.category_id = category_id
    if summary is not None:
        doc.summary = summary
    if tag_ids is not None:
        doc.tags = _load_tags(db, tag_ids)
    if audience_scope is not None:
        _validate_audience(
            db, audience_scope, audience_department_ids, audience_user_ids
        )
        document_visibility.apply_targets(
            db, doc, audience_scope,
            department_ids=audience_department_ids,
            user_ids=audience_user_ids,
        )

    doc.updated_by = current_user.id
    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_DOCUMENT,
        user=current_user,
        object_type="document",
        object_id=doc.id,
        object_label=doc.title,
        detail={"changed_fields": {
            "title": title is not None,
            "category_id": category_id is not None,
            "summary": summary is not None,
            "tag_ids": tag_ids is not None,
            "audience_scope": audience_scope,
            "validity": validity_changed,
        }},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_lists()
    return get_document(db, user=current_user, doc_id=doc.id)


def archive_document(
    db: Session,
    *,
    current_user: User,
    doc_id: int,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """归档/删除资料（软删，§26）：置 archived_at；当前文件进回收站（reason='archived'）。"""
    doc = _load_document(db, doc_id)
    if doc.archived_at is not None:
        raise AppError("CONFLICT", "资料已归档")

    note = (reason or "").strip() or "管理员归档"
    doc.archived_at = datetime.now()
    doc.archived_by = current_user.id
    doc.archive_reason = note
    doc.updated_by = current_user.id
    entry = file_service.archive_document_files(
        db, document=doc, reason="archived", actor=current_user
    )
    if entry is None:
        # 无文件资料：同样在回收站留档（file_record_id 可空），
        # 保证"删除的资料都能在回收站找到并可恢复"（回收站找不到 bug 修复）
        db.add(
            RecycleBin(
                document_id=doc.id,
                file_record_id=None,
                reason="archived",
                origin_rel_path=None,
                deleted_by=current_user.id,
                deleted_at=datetime.now(),
            )
        )

    audit_service.log(
        db,
        action=audit_service.ACTION_ARCHIVE_DOCUMENT,
        user=current_user,
        object_type="document",
        object_id=doc.id,
        object_label=doc.title,
        detail={"reason": note},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_lists()
    return {"ok": True, "id": doc.id, "status": "archived"}


def restore_document(
    db: Session,
    *,
    current_user: User,
    doc_id: int,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """恢复归档资料（§26）：清 archived_at；文件移回 data/documents/ 并置 active。"""
    doc = _load_document(db, doc_id)
    if doc.archived_at is None:
        raise AppError("CONFLICT", "资料未归档，无需恢复")

    # 主文件与附件各恢复各的（2026-11-06：归档时两个一起进一条回收站记录，
    # 恢复时也必须两个一起回来）。附件原路径不存库，按存储约定反推。
    current_file = doc.current_file
    if current_file is not None and current_file.status == "recycled":
        origin = current_file.storage_rel_path
        # 从回收站记录取进入回收站前的路径
        entry = (
            db.execute(
                select(RecycleBin).where(
                    RecycleBin.file_record_id == current_file.id,
                    RecycleBin.restored_at.is_(None),
                ).order_by(RecycleBin.id.desc())
            )
            .scalars()
            .first()
        )
        origin = (entry.origin_rel_path or origin) if entry else origin
        file_service.restore_recycled_file(
            db, file_record=current_file, origin_rel_path=origin, actor=current_user
        )

    attachment = doc.attachment_file
    if attachment is not None and attachment.status == "recycled":
        file_service.restore_recycled_file(
            db,
            file_record=attachment,
            origin_rel_path=file_service.storage_rel_path(
                "document", attachment.internal_name
            ),
            actor=current_user,
        )

    doc.archived_at = None
    doc.archived_by = None
    doc.archive_reason = None
    doc.updated_by = current_user.id

    # 隐藏残存防护：归档期间其分类可能已被删除（分类删除只校验未归档资料，
    # §26 口径）。已删除分类在分类树中不可见、无入口管理，恢复后资料会挂在
    # 一个永远找不到的分类下——置空归入「未分类」，由管理待办引导重新归类。
    category_cleared = False
    if doc.category_id is not None:
        cat = db.get(Category, doc.category_id)
        if cat is None or not cat.is_active:
            doc.category_id = None
            category_cleared = True

    audit_service.log(
        db,
        action=audit_service.ACTION_RESTORE_DOCUMENT,
        user=current_user,
        object_type="document",
        object_id=doc.id,
        object_label=doc.title,
        detail={"category_cleared": True, "reason": (reason or "").strip() or None} if category_cleared else {"reason": (reason or "").strip() or None},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_lists()
    return {"ok": True, "id": doc.id, "status": "active"}