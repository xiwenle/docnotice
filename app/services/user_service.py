"""用户管理服务（docs/API.md §3、§23、§33）。

- 权限模型 v3（2026-08-29）：两角色 user / admin；superadmin 已并入 admin
- 管理操作仅 admin（路由层 require_roles("admin") 强制）
- 保护规则：不能停用自己；不能停用/降级最后一个启用的 admin（防系统锁死）；
  不能改自己角色（防误操作自降后无人可恢复）
- 创建/重置密码走强度校验；所有操作审计 manage_user
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import total_count
from app.core.security import hash_password
from app.models.user import Department, User, user_departments
from app.schemas.common import page_meta, validate_password_strength
from app.services import audit_service
from app.services.notice_audience import backfill_notice_reads

ROLE_RANK = {"user": 0, "admin": 1}
ROLES = ("user", "admin")


def user_to_dict(user: User, *, include_departments: bool = False) -> dict:
    """用户出站对象（API.md §0.5）。"""
    data = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "is_active": bool(user.is_active),
        "department_ids": [d.id for d in user.departments],
        "must_change_password": bool(user.must_change_password),
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }
    if include_departments:
        data["departments"] = [{"id": d.id, "name": d.name} for d in user.departments]
    return data


def _load_departments(db: Session, department_ids: list[int]) -> list[Department]:
    if not department_ids:
        return []
    deps = db.execute(
        select(Department).where(Department.id.in_(department_ids))
    ).scalars().all()
    found = {d.id for d in deps}
    missing = [i for i in department_ids if i not in found]
    if missing:
        raise AppError("VALIDATION_ERROR", f"部门不存在: {missing}")
    return list(deps)


def _assert_last_admin_safe(db: Session, target_id: int, *, would_disable: bool, new_role: Optional[str]) -> None:
    """保护规则：不能停用/降级最后一个启用的 admin（防止系统失去全部管理者）。"""
    if new_role is not None and new_role not in ROLES:
        raise AppError("VALIDATION_ERROR", f"角色必须为 {'/'.join(ROLES)}")
    target = db.get(User, target_id)
    if target is None:
        raise AppError("NOT_FOUND", "用户不存在")
    if target.role != "admin":
        return
    if not target.is_active:
        return
    # 目标当前是启用的 admin
    if new_role is not None and ROLE_RANK[new_role] < ROLE_RANK["admin"]:
        downgrade = True
    else:
        downgrade = False
    if would_disable or downgrade:
        active_admins = db.execute(
            select(func.count(User.id)).where(
                User.role == User.ROLE_ADMIN, User.is_active.is_(True)
            )
        ).scalar_one()
        if active_admins <= 1:
            raise AppError(
                "CONFLICT", "不能停用/降级最后一个启用的管理员"
            )


def list_users(
    db: Session,
    *,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    department_id: Optional[int] = None,
    ids: Optional[list[int]] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """用户列表（分页 + 可选筛选）。"""
    stmt = select(User)
    if ids is not None:
        stmt = stmt.where(User.id.in_(ids))
    if role:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if department_id is not None:
        stmt = stmt.join(
            user_departments, user_departments.c.user_id == User.id
        ).where(user_departments.c.department_id == department_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(User.username.like(like), User.display_name.like(like))
        )

    total = total_count(db, stmt)
    items = db.execute(
        stmt.order_by(User.id).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    return {
        "items": [user_to_dict(u) for u in items],
        **page_meta(total, page, page_size),
    }


def create_user(
    db: Session,
    *,
    current_user: User,
    username: str,
    password: str,
    display_name: str,
    role: str = "user",
    email: Optional[str] = None,
    department_ids: Optional[list[int]] = None,
    must_change_password: bool = True,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> User:
    """新建用户；username 重复 → 409 DUPLICATE。"""
    if role not in ROLES:
        raise AppError("VALIDATION_ERROR", f"角色必须为 {'/'.join(ROLES)}")
    validate_password_strength(password)

    exists = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if exists is not None:
        if not exists.is_active:
            # 用户名被已删除账号占用（语义保留：防止重建同名账号绕过停用与审计）
            raise AppError(
                "DUPLICATE",
                f"用户名已被已删除账号「{exists.display_name}」占用，请先在用户列表恢复该账号，或改用其他用户名",
            )
        raise AppError("DUPLICATE", f"用户名已存在: {username}")

    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        email=email,
        role=role,
        is_active=True,
        auth_source="local",
        must_change_password=must_change_password,
    )
    user.departments = _load_departments(db, department_ids or [])
    db.add(user)
    db.flush()

    audit_service.log(
        db,
        action=audit_service.ACTION_MANAGE_USER,
        user=current_user,
        object_type="user",
        object_id=user.id,
        object_label=user.username,
        detail={
            "op": "create",
            "role": role,
            "department_ids": department_ids or [],
            "forced_password_change": must_change_password,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(user)
    # 第一道防线：发布后才创建的用户立即补建缺失的阅读快照
    # （否则"全体用户"通知对其不可见、阅读统计漏计，直到下次登录/访问）
    backfill_notice_reads(db, user=user)
    return user


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "用户不存在")
    return user


def update_user(
    db: Session,
    *,
    current_user: User,
    target_id: int,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    department_ids: Optional[list[int]] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> User:
    """修改用户；含全部保护规则并审计。"""
    target = get_user(db, target_id)
    # 捕获修改前状态（供身份失效与"启用/换部门 → 补建阅读快照"判断）
    prev_role = target.role
    prev_is_active = bool(target.is_active)
    prev_dept_ids = {d.id for d in target.departments}

    # 不能修改自己的身份/角色（API.md §3）
    if target.id == current_user.id:
        if role is not None and role != current_user.role:
            raise AppError("CONFLICT", "不能修改自己的角色")
        if is_active is False:
            raise AppError("CONFLICT", "不能停用自己的账号")

    if role is not None:
        if role not in ROLES:
            raise AppError("VALIDATION_ERROR", f"角色必须为 {'/'.join(ROLES)}")
        _assert_last_admin_safe(
            db, target.id, would_disable=False, new_role=role
        )
        target.role = role

    if is_active is not None:
        _assert_last_admin_safe(
            db, target.id, would_disable=not is_active, new_role=None
        )
        target.is_active = is_active

    if display_name is not None:
        target.display_name = display_name
    if username is not None and username != target.username:
        # 检查用户名唯一性
        existing = db.query(User).filter(User.username == username, User.id != target.id).first()
        if existing:
            raise AppError("CONFLICT", "用户名已存在")
        target.username = username
    if email is not None:
        target.email = email
    if phone is not None:
        target.phone = phone
    if department_ids is not None:
        target.departments = _load_departments(db, department_ids)

    # 角色或启用状态改变属于身份边界变化：旧会话必须永久失效。
    # 停用后再次启用也不能让停用前签发的 token 复活。
    if target.role != prev_role or bool(target.is_active) != prev_is_active:
        target.token_version = (target.token_version or 0) + 1

    audit_service.log(
        db,
        action=audit_service.ACTION_MANAGE_USER,
        user=current_user,
        object_type="user",
        object_id=target.id,
        object_label=target.username,
        detail={
            "op": "update",
            "role": role,
            "is_active": is_active,
            "department_ids": department_ids,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(target)
    
    # 用户状态变更后失效缓存（dashboard统计、资料列表等）
    from app.core.cache import invalidate_lists
    invalidate_lists()
    
    # 第二道防线：停用→启用（发布时不在场）或调入阅读部门后，立即补建
    # 缺失的阅读快照（幂等；与登录/列表访问补建互为冗余兜底）
    reactivated = is_active is True and not prev_is_active
    dept_changed = (
        department_ids is not None and prev_dept_ids != set(department_ids)
    )
    if reactivated or dept_changed:
        backfill_notice_reads(db, user=target)
    return target


def disable_user(
    db: Session,
    *,
    current_user: User,
    target_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> User:
    """停用用户（软删除，§26）：置 is_active=false，含保护规则。"""
    if target_id == current_user.id:
        raise AppError("CONFLICT", "不能停用自己的账号")
    return update_user(
        db,
        current_user=current_user,
        target_id=target_id,
        is_active=False,
        ip=ip,
        user_agent=user_agent,
    )


def reset_password(
    db: Session,
    *,
    current_user: User,
    target_id: int,
    new_password: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> User:
    """重置密码：置 must_change_password=1 并审计。

    单点登录：自增 token_version 使该用户所有已有会话失效（密码被管理员
    重置后，旧设备一律重新登录）。
    """
    validate_password_strength(new_password)
    target = get_user(db, target_id)
    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    target.token_version = (target.token_version or 0) + 1

    audit_service.log(
        db,
        action=audit_service.ACTION_MANAGE_USER,
        user=current_user,
        object_type="user",
        object_id=target.id,
        object_label=target.username,
        detail={"op": "reset_password"},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(target)
    return target


def purge_user(
    db: Session,
    *,
    current_user: User,
    target_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """删除用户（物理删除，产品决策 2026-09：无需二次确认）。

    边界（2026-09 产品决策）：
    - 内容所有权引用（创建/编辑/归档过资料、创建/编辑过通知）→ 409 拦截：
      created_by 等责任链 FK 置空会令资料失去操作责任人，此类账号只能停用
    - 通知阅读/确认快照 → 随账号删除（个人数据消亡；已发布通知的历史确认
      统计将回溯减少——删除前由前端文案告知）
    - 操作日志（§25）→ 行保留、user_id 置 NULL、username 文本保留，留痕完整可读
    - 用户-部门归属关联 → 清除
    删除后用户名即释放，可重建同名。
    """
    target = get_user(db, target_id)
    self_check = target.id == current_user.id
    if self_check:
        raise AppError("CONFLICT", "不能删除自己的账号")

    reasons: list[str] = []
    from app.models.document import Document
    from app.models.notice import Notice, NoticeRead, NoticeTarget
    from app.models.audit import AuditLog

    doc_refs = db.execute(
        select(func.count(Document.id)).where(
            or_(Document.created_by == target.id,
                Document.updated_by == target.id,
                Document.archived_by == target.id)
        )
    ).scalar_one()
    if doc_refs:
        reasons.append(f"创建或编辑过 {doc_refs} 份资料")
    notice_refs = db.execute(
        select(func.count(Notice.id)).where(
            or_(Notice.created_by == target.id, Notice.updated_by == target.id)
        )
    ).scalar_one()
    if notice_refs:
        reasons.append(f"创建或编辑过 {notice_refs} 条通知")

    if reasons:
        raise AppError(
            "CONFLICT",
            "该账号创建过内容，删除会让资料/通知失去操作责任人，只能保持停用状态："
            + "；".join(reasons),
        )

    # 1) 阅读/确认快照随账号删除（统计回溯减少为已知行为）
    db.execute(delete(NoticeRead).where(NoticeRead.user_id == target.id))
    db.execute(delete(NoticeTarget).where(NoticeTarget.user_id == target.id))
    # 2) 操作日志保留，user_id 置 NULL（username 文本列仍在，留痕可读）
    db.execute(
        update(AuditLog).where(AuditLog.user_id == target.id).values(user_id=None)
    )
    # 2b) 2026-10-29 修复：运维历史里的 user 外键同样要解除，
    #     否则删行时 FK NO ACTION 直接 IntegrityError → 500。
    #     这些都**不是内容责任链**（不产生资料/通知），置 NULL 即可，
    #     备份集与回收站条目本身必须保留（§28 备份、§26 不物理删除）。
    from app.models.backup import BackupRecord, RecycleBin
    from app.models.document import FileRecord

    db.execute(
        update(BackupRecord)
        .where(or_(BackupRecord.created_by == target.id,
                   BackupRecord.restored_by == target.id))
        .values(created_by=None, restored_by=None)
    )
    db.execute(
        update(RecycleBin)
        .where(or_(RecycleBin.deleted_by == target.id,
                   RecycleBin.restored_by == target.id,
                   RecycleBin.purged_by == target.id))
        .values(deleted_by=None, restored_by=None, purged_by=None)
    )
    db.execute(
        update(FileRecord)
        .where(FileRecord.uploaded_by == target.id)
        .values(uploaded_by=None)
    )
    # 2c) 2026-11-16 修复：资料可见范围（document_targets）也持有 user 外键。
    #     这些行是「谁能看这份资料」的授权，不是内容责任链；账号删除后授权对象
    #     已不存在，删除该行即可（资料本身保留，其余可见对象不受影响）。
    #     不清理会 FK IntegrityError → 500。
    from app.models.document import DocumentTarget

    db.execute(delete(DocumentTarget).where(DocumentTarget.user_id == target.id))
    # 3) 用户-部门归属关联
    db.execute(
        user_departments.delete().where(user_departments.c.user_id == target.id)
    )
    # 4) 删行
    username = target.username
    db.delete(target)
    audit_service.log(
        db,
        action=audit_service.ACTION_MANAGE_USER,
        user=current_user,
        object_type="user",
        object_id=target_id,
        object_label=username,
        detail={"op": "purge"},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return {"ok": True, "id": target_id, "username": username}