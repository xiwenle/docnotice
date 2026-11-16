"""分类服务（docs/API.md §4、DATABASE.md §4、项目规范 §9）。

- 树：parent_id 自引用；深度上限 MAX_DEPTH=4（一级~四级，2026-08-30 产品决策
  由 §9 原二级约束放宽；资料表单/批量整理/分类筛选均已开到四级）
- 筛选与计数为文件夹语义：某分类的范围 = 自身 + 全部子孙（subtree_ids）
- 同级重名检查（数据库部分唯一索引 parent+name+is_active=1 兜底；service 校验）
- 重挂 parent 不得成环（不能挂到自身或其子孙之下）
- 删除分类 = 删除整棵子树；树内资料（含已归档）category_id 置空为未分类（2026-11-09 用户确认）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.category import Category
from app.models.document import Document
from app.services import audit_service
from app.core.cache import invalidate_lists


def _invalidate_caches() -> None:
    """分类/标签变更影响资料列表与工作台计数，写后即失效（统一口径）。"""
    invalidate_lists()

MAX_DEPTH = 4  # 一级 ~ 四级（2026-09 产品决策：为三级/四级预留）


def _root_filter(name: str) -> str:
    return name


def subtree_ids(db: Session, category_id: int) -> list[int]:
    """分类自身 + 全部子孙 id（深度 ≤ MAX_DEPTH，逐层批量查询）。"""
    ids = [category_id]
    seen = {category_id}
    frontier = [category_id]
    while frontier:
        children = db.execute(
            select(Category.id).where(Category.parent_id.in_(frontier))
        ).scalars().all()
        frontier = [c for c in children if c not in seen]
        seen.update(frontier)
        ids.extend(frontier)
    return ids


def _children_map(categories: Sequence[Category]) -> dict[int, list[Category]]:
    children_map: dict[int, list[Category]] = {}
    for c in categories:
        if c.parent_id is not None:
            children_map.setdefault(c.parent_id, []).append(c)
    return children_map


def list_categories(
    db: Session,
    *,
    parent_id: Optional[int] = None,
    with_document_count: bool = False,
    user=None,
) -> dict:
    """分类列表：parent_id 省略=全部（树形）；指定=该父级下的平级列表。

    响应：{"items": [ {id,name,parent_id,sort_order,children:[...],document_count?} ]}

    user（2026-11-04 新增）：传了普通用户时，document_count 只统计该用户
    **可见**的资料——与 `GET /documents?category_id=` 的结果严格同口径。
    此前是全库计数，导致普通用户看到「1 份」点进去却是空列表。
    """
    categories = db.execute(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.id)
    ).scalars().all()
    children_map = _children_map(categories)
    direct_counts = (
        _direct_document_counts(db, user=user) if with_document_count else {}
    )

    def node(c: Category, *, with_children: bool) -> dict:
        item = {
            "id": c.id,
            "name": c.name,
            "parent_id": c.parent_id,
            "sort_order": c.sort_order,
            "is_active": bool(c.is_active),
        }
        if with_children:
            item["children"] = [
                node(ch, with_children=True) for ch in children_map.get(c.id, [])
            ]
        if with_document_count:
            item["document_count"] = _subtree_document_count(
                c.id, children_map, direct_counts
            )
        return item

    if parent_id is not None:
        # 平级列表（指定父级）：不展开 children，但计数仍为完整子树口径
        return {
            "items": [
                node(c, with_children=False)
                for c in categories
                if c.parent_id == parent_id
            ]
        }

    # 全部：递归组装树（一级 ~ 四级）
    return {
        "items": [node(c, with_children=True) for c in categories if c.parent_id is None]
    }


def _direct_document_counts(db: Session, *, user=None) -> dict[int, int]:
    """各分类「直接挂载」的未归档资料数，一次分组查询。

    user 为普通用户时附加可见范围条件（admin/None 保持全库口径）。
    """
    from app.services.document_visibility import visibility_condition

    where = [Document.category_id.isnot(None), Document.archived_at.is_(None)]
    if user is not None:
        vis = visibility_condition(user)
        if vis is not None:
            where.append(vis)

    rows = db.execute(
        select(Document.category_id, func.count(Document.id))
        .where(*where)
        .group_by(Document.category_id)
    ).all()
    return {cid: n for cid, n in rows}


def _subtree_document_count(
    category_id: int,
    children_map: dict[int, list[Category]],
    direct_counts: dict[int, int],
) -> int:
    """文件夹语义：自身 + 全部子孙分类下的未归档资料数。

    与资料列表的 category_id 筛选（subtree_ids）同口径——树上的数字
    必须等于点进该分类能看到的篇数，否则管理员会误判资料丢失。
    """
    total = direct_counts.get(category_id, 0)
    for ch in children_map.get(category_id, []):
        total += _subtree_document_count(ch.id, children_map, direct_counts)
    return total


def _unique_check(
    db: Session, name: str, parent_id: Optional[int], exclude_id: Optional[int] = None
) -> None:
    """同级重名检查（排除自身；只看未删除的分类——已删除分类不可见、不占名）。"""
    if parent_id is None:
        stmt = select(Category.id).where(
            Category.name == name, Category.parent_id.is_(None),
            Category.is_active.is_(True),
        )
    else:
        stmt = select(Category.id).where(
            Category.name == name, Category.parent_id == parent_id,
            Category.is_active.is_(True),
        )
    if exclude_id is not None:
        stmt = stmt.where(Category.id != exclude_id)
    if db.execute(stmt).scalar_one_or_none() is not None:
        raise AppError("DUPLICATE", f"同级下已存在同名分类: {name}")


def _node_depth(db: Session, category_id: Optional[int]) -> int:
    """某分类的深度（根分类 = 1）。None → 0。链不完整视为数据异常。"""
    if category_id is None:
        return 0
    depth = 1
    cur = db.get(Category, category_id)
    if cur is None:
        raise AppError("VALIDATION_ERROR", f"分类不存在: {category_id}")
    seen = {cur.id}
    while cur.parent_id is not None:
        nxt: Optional[Category] = db.get(Category, cur.parent_id)
        if nxt is None:
            raise AppError("VALIDATION_ERROR", "父分类链不完整（数据异常）")
        if nxt.id in seen:  # 数据异常（成环）时不能死循环
            raise AppError("VALIDATION_ERROR", "分类树存在环（数据异常）")
        seen.add(nxt.id)
        cur = nxt
        depth += 1
    return depth


def _subtree_height(db: Session, category_id: int) -> int:
    """以该分类为根的子树高度（无子分类 = 1）。"""
    height = 1  # 自身
    seen = {category_id}
    frontier = [category_id]
    while frontier:
        next_frontier: list[int] = []
        for cid in frontier:
            for child_id in db.execute(
                select(Category.id).where(Category.parent_id == cid)
            ).scalars().all():
                if child_id in seen:
                    raise AppError("VALIDATION_ERROR", "分类树存在环（数据异常）")
                seen.add(child_id)
                next_frontier.append(child_id)
        if next_frontier:
            height += 1
            frontier = next_frontier
        else:
            break
    return height


def _check_parent(db: Session, parent_id: Optional[int], *, exclude_id: Optional[int] = None) -> None:
    """校验新父分类合法：深度不超上限、且移动后**整棵子树**仍在 MAX_DEPTH 内、不成环。"""
    if parent_id is None:
        return
    parent = db.get(Category, parent_id)
    if parent is None:
        raise AppError("VALIDATION_ERROR", f"父分类不存在: {parent_id}")

    depth = _node_depth(db, parent_id)
    if depth > MAX_DEPTH - 1:
        raise AppError("VALIDATION_ERROR", f"分类深度最多 {MAX_DEPTH} 级")

    # 成环检查：目标分类（若存在）不能是 parent 自身或 parent 的祖先
    if exclude_id is not None:
        if exclude_id == parent_id:
            raise AppError("VALIDATION_ERROR", "不能把分类挂到自己之下")
        anc: Optional[Category] = parent
        while anc is not None:
            if anc.id == exclude_id:
                raise AppError("VALIDATION_ERROR", "不能把分类挂到自己的子孙之下")
            anc = db.get(Category, anc.parent_id) if anc.parent_id is not None else None

    # 2026-10-29 修复：只校验被移动节点自身的祖先链是不够的——
    # 把「带 3 级子树」的节点挂到 3 级分类下，该子树最深节点会变成 7 级，
    # 直接违反 AGENTS §9「上限 4 级」。必须校验移动后整棵子树的最深深度。
    #   移动后自身深度 = depth(parent) + 1
    #   子树最深深度 = 自身新深度 + subtree_height - 1
    if exclude_id is not None:
        new_depth = depth + 1
        deepest = new_depth + _subtree_height(db, exclude_id) - 1
        if deepest > MAX_DEPTH:
            raise AppError(
                "VALIDATION_ERROR",
                f"移动后该分类子树最深将达到 {deepest} 级，超过上限 {MAX_DEPTH} 级；"
                "请先调整其子分类层级",
            )


def create_category(
    db: Session,
    *,
    current_user,
    name: str,
    parent_id: Optional[int] = None,
    sort_order: int = 0,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Category:
    name = name.strip()
    if not name:
        raise AppError("VALIDATION_ERROR", "分类名不能为空")
    _check_parent(db, parent_id)
    _unique_check(db, name, parent_id)

    cat = Category(name=name, parent_id=parent_id, sort_order=sort_order, is_active=True)
    db.add(cat)
    db.flush()
    audit_service.log(
        db,
        action=audit_service.ACTION_CREATE_CATEGORY,
        user=current_user,
        object_type="category",
        object_id=cat.id,
        object_label=cat.name,
        detail={"parent_id": parent_id, "sort_order": sort_order},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    _invalidate_caches()
    db.refresh(cat)
    return cat


def update_category(
    db: Session,
    *,
    current_user,
    category_id: int,
    name: Optional[str] = None,
    parent_id: Optional[int] = None,
    sort_order: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Category:
    cat = db.get(Category, category_id)
    if cat is None:
        raise AppError("NOT_FOUND", "分类不存在")

    new_parent = parent_id if parent_id is not None else cat.parent_id
    _check_parent(db, new_parent, exclude_id=category_id)
    # 成环：不能挂到自己的子孙下（MOVE semantics only if parent_id explicitly given）
    if parent_id is not None:
        child_ids = set(db.execute(
            select(Category.id).where(Category.parent_id == category_id)
        ).scalars().all())
        if parent_id in child_ids:
            raise AppError("VALIDATION_ERROR", "不能把分类挂到自己的子分类之下")

    if name is not None and name.strip() and name.strip() != cat.name:
        _unique_check(db, name.strip(), new_parent, exclude_id=category_id)
        cat.name = name.strip()
    if parent_id is not None:
        cat.parent_id = parent_id
    if sort_order is not None:
        cat.sort_order = sort_order

    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_CATEGORY,
        user=current_user,
        object_type="category",
        object_id=cat.id,
        object_label=cat.name,
        detail={"name": cat.name, "parent_id": cat.parent_id, "sort_order": cat.sort_order},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    _invalidate_caches()
    db.refresh(cat)
    return cat


def delete_category(
    db: Session,
    *,
    current_user,
    category_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """删除分类 = 物理删除整棵子树（2026-11-09 用户确认）。

    语义：
    - 删除选中分类及其全部子孙分类（含历史停用行），不提升子分类；
    - 树内所有资料（含已归档）的 category_id 置空为「未分类」；
    - 资料、文件、附件、标签、通知、通知类型、通知关联均不删除；
    - 不做历史分类名快照；不做二次确认（AGENTS §26.1）。

    事务顺序：先记录数量并置空资料分类，再从深层到浅层删分类，最后写审计并提交。
    """
    cat = db.get(Category, category_id)
    if cat is None:
        raise AppError("NOT_FOUND", "分类不存在")

    name = cat.name
    parent_id = cat.parent_id
    ids = subtree_ids(db, category_id)
    nodes = db.execute(
        select(Category).where(Category.id.in_(ids))
    ).scalars().all()
    by_id = {node.id: node for node in nodes}

    def _depth(node: Category) -> int:
        depth = 0
        seen: set[int] = set()
        cur = node
        while cur.id not in seen and cur.parent_id in by_id:
            seen.add(cur.id)
            depth += 1
            cur = by_id[cur.parent_id]
        return depth

    doc_count = db.execute(
        select(func.count(Document.id)).where(Document.category_id.in_(ids))
    ).scalar_one()

    if doc_count:
        db.execute(
            update(Document)
            .where(Document.category_id.in_(ids))
            .values(
                category_id=None,
                updated_by=current_user.id,
                updated_at=datetime.now(),
            )
        )

    # 先删深层子分类，再删父分类，避免 parent_id 外键悬空。
    for node in sorted(nodes, key=lambda n: (_depth(n), n.id), reverse=True):
        db.execute(delete(Category).where(Category.id == node.id))

    detail = {
        "root_id": category_id,
        "root_name": name,
        "parent_id": parent_id,
        "deleted_category_ids": ids,
        "deleted_category_count": len(ids),
        "unclassified_document_count": doc_count,
    }
    audit_service.log(
        db,
        action=audit_service.ACTION_DELETE_CATEGORY,
        user=current_user,
        object_type="category",
        object_id=category_id,
        object_label=name,
        detail=detail,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    _invalidate_caches()
    return {
        "ok": True,
        "id": category_id,
        "name": name,
        "deleted_category_ids": ids,
        "deleted_category_count": len(ids),
        "unclassified_document_count": doc_count,
    }
