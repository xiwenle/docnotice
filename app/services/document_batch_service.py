"""可扩展的资料批量动作服务。

预览和执行共用同一套选择条件；执行前再次核对选择集合，避免预览后数据变化
导致误操作。仅由 admin 路由调用。
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import date as date_type
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.cache import invalidate_lists
from app.core.errors import AppError
from app.models.category import Category, Tag
from app.models.document import Document
from app.models.user import User
from app.services import audit_service, document_service, document_visibility

ACTION_KEYS = {
    "set_category",
    "clear_category",
    "add_tags",
    "remove_tags",
    "set_audience",
    "archive",
    "restore",
}

_SELECTION_KEYS = {
    "keyword", "category_id", "tag_id", "status", "as_of_date",
    "cover_from", "cover_to", "uncategorized", "untagged",
    "audience_scope", "has_attachment", "file_type", "missing_main_file",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _params_for_audit(params: dict) -> dict:
    return {k: v for k, v in params.items() if v is not None}


def _load_docs(db: Session, ids: list[int]) -> dict[int, Document]:
    if not ids:
        return {}
    docs = (
        db.execute(
            select(Document)
            .options(
                selectinload(Document.tags),
                selectinload(Document.targets),
                selectinload(Document.current_file),
                selectinload(Document.attachment_file),
                selectinload(Document.category).selectinload(Category.parent),
            )
            .where(Document.id.in_(ids))
        )
        .scalars()
        .all()
    )
    return {doc.id: doc for doc in docs}


def _resolve_selection(
    db: Session,
    *,
    user: User,
    selection: dict,
    archived_only: bool,
) -> list[int]:
    mode = selection.get("mode")
    if mode == "ids":
        ids = sorted({int(x) for x in (selection.get("ids") or [])})
        if not ids:
            return []
        conds = [Document.id.in_(ids)]
        if archived_only:
            conds.append(Document.archived_at.isnot(None))
        else:
            conds.append(Document.archived_at.is_(None))
        return list(db.execute(select(Document.id).where(*conds)).scalars().all())

    if mode != "filter":
        raise AppError("VALIDATION_ERROR", "selection.mode 仅支持 ids/filter")

    filters = selection.get("filters") or {}
    def _date(value):
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                return date_type.fromisoformat(value)
            except ValueError as exc:
                raise AppError("VALIDATION_ERROR", f"日期格式应为 YYYY-MM-DD: {value}") from exc
        return value
    unknown = set(filters) - _SELECTION_KEYS
    if unknown:
        raise AppError("VALIDATION_ERROR", f"不支持的筛选字段: {sorted(unknown)}")
    conds, _ = document_service.build_document_conditions(
        db,
        user=user,
        keyword=filters.get("keyword") or None,
        category_id=filters.get("category_id"),
        tag_id=filters.get("tag_id"),
        status_filter=filters.get("status"),
        as_of_date=_date(filters.get("as_of_date")),
        cover_from=_date(filters.get("cover_from")),
        cover_to=_date(filters.get("cover_to")),
        uncategorized=bool(filters.get("uncategorized")),
        untagged=bool(filters.get("untagged")),
        audience_scope=filters.get("audience_scope"),
        has_attachment=filters.get("has_attachment"),
        file_type=filters.get("file_type"),
        missing_main_file=bool(filters.get("missing_main_file")),
        archived_only=archived_only,
    )
    exclude = {int(x) for x in (selection.get("exclude_ids") or [])}
    ids = list(db.execute(select(Document.id).where(*conds).order_by(Document.id)).scalars().all())
    return [i for i in ids if i not in exclude]


def _validate_params(db: Session, action_key: str, params: dict) -> None:
    if action_key not in ACTION_KEYS:
        raise AppError("VALIDATION_ERROR", "不支持的批量动作")

    if action_key == "set_category":
        category_id = params.get("category_id")
        if not isinstance(category_id, int):
            raise AppError("VALIDATION_ERROR", "set_category 需要 category_id")
        document_service._load_category(db, category_id)
    elif action_key == "clear_category":
        return
    elif action_key in ("add_tags", "remove_tags"):
        ids = params.get("tag_ids")
        if not isinstance(ids, list) or not ids:
            raise AppError("VALIDATION_ERROR", "标签动作至少需要一个 tag_ids")
        found = set(db.execute(select(Tag.id).where(Tag.id.in_(ids))).scalars().all())
        missing = set(ids) - found
        if missing:
            raise AppError("VALIDATION_ERROR", f"标签不存在: {sorted(missing)}")
    elif action_key == "set_audience":
        scope = str(params.get("scope") or "")
        document_service._validate_audience(
            db, scope, params.get("department_ids") or [], params.get("user_ids") or []
        )
    elif action_key == "archive":
        return
    elif action_key == "restore":
        return


def _old_category_label(doc: Document) -> str:
    if not doc.category_id:
        return "未分类"
    path: list[str] = []
    cat = doc.category
    while cat is not None:
        path.insert(0, cat.name)
        cat = cat.parent
    return " / ".join(path) or "未分类"


def _impact_for_docs(
    docs: list[Document], action_key: str, params: dict
) -> tuple[int, list[dict], list[dict], dict[str, int]]:
    skipped: list[dict] = []
    warnings: list[dict] = []
    affected = 0
    old_counts: dict[str, int] = {}

    tag_ids = {int(x) for x in (params.get("tag_ids") or [])}
    new_scope = params.get("scope")
    for doc in docs:
        reason = None
        if action_key in ("set_category", "clear_category", "add_tags", "remove_tags", "set_audience", "archive"):
            if doc.archived_at is not None:
                reason = "已删除（回收站中），请先恢复"
        if action_key == "restore" and doc.archived_at is None:
            reason = "资料未归档，无需恢复"
        if reason:
            skipped.append({"id": doc.id, "title": doc.title, "reason": reason})
            continue

        if action_key == "set_category":
            old_counts[_old_category_label(doc)] = old_counts.get(_old_category_label(doc), 0) + 1
            if doc.category_id != params.get("category_id"):
                affected += 1
        elif action_key == "clear_category":
            old_counts["已分类" if doc.category_id else "未分类"] = old_counts.get("已分类" if doc.category_id else "未分类", 0) + 1
            if doc.category_id is not None:
                affected += 1
        elif action_key == "add_tags":
            if not tag_ids.issubset({t.id for t in doc.tags}):
                affected += 1
        elif action_key == "remove_tags":
            if tag_ids.intersection({t.id for t in doc.tags}):
                affected += 1
        elif action_key == "set_audience":
            old_counts[doc.audience_scope] = old_counts.get(doc.audience_scope, 0) + 1
            if doc.audience_scope != new_scope:
                affected += 1
            if new_scope == "all" and doc.audience_scope != "all":
                warnings.append({"code": "audience_expand", "message": f"{doc.title} 将从受限范围扩大为全体可见"})
            elif new_scope != "all" and doc.audience_scope == "all":
                warnings.append({"code": "audience_shrink", "message": f"{doc.title} 将从全体可见缩小为受限范围"})
        elif action_key in ("archive", "restore"):
            affected += 1
    return affected, skipped, warnings, old_counts


def preview(
    db: Session,
    *,
    current_user: User,
    action_key: str,
    selection: dict,
    params: dict,
    expected_count: Optional[int] = None,
) -> dict:
    if current_user.role != User.ROLE_ADMIN:
        raise AppError("FORBIDDEN", "只有管理员可以预览批量动作")
    _validate_params(db, action_key, params)
    archived_only = action_key == "restore"
    ids = _resolve_selection(db, user=current_user, selection=selection, archived_only=archived_only)
    docs_map = _load_docs(db, ids)
    docs = [docs_map[i] for i in ids if i in docs_map]
    affected, skipped, warnings, old_counts = _impact_for_docs(docs, action_key, params)
    if expected_count is not None and expected_count != affected:
        raise AppError(
            "CONFLICT",
            "预览结果与当前数据不一致，请重新预览",
            detail={"expected_count": expected_count, "affected_count": affected},
        )
    token = secrets.token_urlsafe(24)
    now = time.time()
    _PREVIEWS[token] = {
        "expires_at": now + 300,
        "user_id": current_user.id,
        "action_key": action_key,
        "selection": selection,
        "params": params,
        "matched_ids": ids,
        "affected_count": affected,
    }
    return {
        "preview_token": token,
        "action_key": action_key,
        "matched_count": len(ids),
        "affected_count": affected,
        "skipped_count": len(skipped),
        "skipped": skipped[:50],
        "warnings": warnings[:50],
        "old_counts": old_counts,
        "expires_in": 300,
    }


_PREVIEWS: dict[str, dict[str, Any]] = {}


def _require_preview(
    db: Session,
    *,
    current_user: User,
    action_key: str,
    selection: dict,
    params: dict,
    expected_count: Optional[int],
    preview_token: str,
) -> dict:
    cached = _PREVIEWS.get(preview_token)
    if not cached:
        raise AppError("CONFLICT", "预览已失效，请重新预览")
    if cached["expires_at"] < time.time():
        _PREVIEWS.pop(preview_token, None)
        raise AppError("CONFLICT", "预览已过期，请重新预览")
    if (
        cached["user_id"] != current_user.id
        or cached["action_key"] != action_key
        or cached["selection"] != selection
        or _canonical(cached["params"]) != _canonical(params)
    ):
        raise AppError("CONFLICT", "预览上下文不匹配，请重新预览")
    if expected_count is not None and expected_count != cached["affected_count"]:
        raise AppError("CONFLICT", "预览数量已变化，请重新预览")
    ids = _resolve_selection(
        db,
        user=current_user,
        selection=selection,
        archived_only=action_key == "restore",
    )
    if ids != cached["matched_ids"]:
        raise AppError("CONFLICT", "预览后资料集合发生变化，请重新预览")
    return cached


def execute(
    db: Session,
    *,
    current_user: User,
    action_key: str,
    selection: dict,
    params: dict,
    expected_count: Optional[int],
    preview_token: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    if current_user.role != User.ROLE_ADMIN:
        raise AppError("FORBIDDEN", "只有管理员可以执行批量动作")
    _validate_params(db, action_key, params)
    cached = _require_preview(
        db,
        current_user=current_user,
        action_key=action_key,
        selection=selection,
        params=params,
        expected_count=expected_count,
        preview_token=preview_token,
    )
    ids = list(cached["matched_ids"])
    docs_map = _load_docs(db, ids)
    docs = [docs_map[i] for i in ids if i in docs_map]
    matched_count = len(docs)
    skipped: list[dict] = []
    changes: list[dict] = []

    category_id = params.get("category_id")
    tag_ids = [int(x) for x in (params.get("tag_ids") or [])]
    scope = str(params.get("scope") or "")

    if action_key == "restore":
        restored = 0
        for doc in docs:
            try:
                document_service.restore_document(
                    db, current_user=current_user, doc_id=doc.id,
                    reason=params.get("reason"), ip=ip, user_agent=user_agent,
                )
                restored += 1
                changes.append({"id": doc.id, "title": doc.title, "old": "archived", "new": "active"})
            except AppError as exc:
                skipped.append({"id": doc.id, "title": doc.title, "reason": exc.message})
        _PREVIEWS.pop(preview_token, None)
        return {
            "batch_id": preview_token,
            "action_key": action_key,
            "matched_count": matched_count,
            "affected_count": restored,
            "skipped": skipped,
            "failures": [],
        }

    if action_key == "archive":
        archived = 0
        for doc in docs:
            try:
                document_service.archive_document(
                    db, current_user=current_user, doc_id=doc.id,
                    reason=params.get("reason") or "批量归档", ip=ip, user_agent=user_agent,
                )
                archived += 1
                changes.append({"id": doc.id, "title": doc.title, "old": "active", "new": "archived"})
            except AppError as exc:
                skipped.append({"id": doc.id, "title": doc.title, "reason": exc.message})
        _PREVIEWS.pop(preview_token, None)
        return {
            "batch_id": preview_token,
            "action_key": action_key,
            "matched_count": matched_count,
            "affected_count": archived,
            "skipped": skipped,
            "failures": [],
        }
    affected = 0
    for doc in docs:
        old: dict[str, Any] = {}
        if action_key == "set_category":
            old["category_id"] = doc.category_id
            if doc.category_id != category_id:
                doc.category_id = category_id
                doc.updated_by = current_user.id
                affected += 1
        elif action_key == "clear_category":
            old["category_id"] = doc.category_id
            if doc.category_id is not None:
                doc.category_id = None
                doc.updated_by = current_user.id
                affected += 1
        elif action_key == "add_tags":
            existing = {t.id for t in doc.tags}
            missing = [tid for tid in tag_ids if tid not in existing]
            old["tag_ids"] = sorted(existing)
            if missing:
                for tid in missing:
                    tag = db.get(Tag, tid)
                    if tag is not None:
                        doc.tags.append(tag)
                doc.updated_by = current_user.id
                affected += 1
        elif action_key == "remove_tags":
            existing = {t.id for t in doc.tags}
            old["tag_ids"] = sorted(existing)
            if existing.intersection(tag_ids):
                doc.tags[:] = [t for t in doc.tags if t.id not in tag_ids]
                doc.updated_by = current_user.id
                affected += 1
        elif action_key == "set_audience":
            old["audience_scope"] = doc.audience_scope
            old["audience_department_ids"] = [t.department_id for t in doc.targets if t.target_type == "department"]
            old["audience_user_ids"] = [t.user_id for t in doc.targets if t.target_type == "user"]
            if doc.audience_scope != scope:
                document_visibility.apply_targets(
                    db, doc, scope,
                    department_ids=params.get("department_ids") or [],
                    user_ids=params.get("user_ids") or [],
                )
                doc.updated_by = current_user.id
                affected += 1
        if old:
            changes.append({"id": doc.id, "title": doc.title, "old": old})

    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_DOCUMENT,
        user=current_user,
        detail={
            "bulk": True,
            "batch_id": preview_token,
            "action_key": action_key,
            "selection": selection,
            "params": _params_for_audit(params),
            "requested_count": len(ids),
            "matched_count": matched_count,
            "affected_count": affected,
            "skipped": skipped,
            "changes": changes[:200],
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_lists()
    _PREVIEWS.pop(preview_token, None)
    return {
        "batch_id": preview_token,
        "action_key": action_key,
        "matched_count": matched_count,
        "affected_count": affected,
        "skipped": skipped,
        "failures": [],
    }