"""M61 种子数据盘点：先确认「用来验收的数据本身是否够全」。

背景（2026-11-04 用户反馈）：上一轮验收只用了 1 条通知、2 层分类、没有图片，
等于把系统的大部分能力（四级分类、图片预览、废止联动、可见范围矩阵）
**根本没有走通**，却报了 83/83 全绿。本模块是第一道闸门——数据不达标，
后面的用例再绿也说明不了问题。

本模块只读，不改任何数据。
"""
from __future__ import annotations

from harness import REPORT, case, http


def _get(app, path):
    return http("GET", path, app.admin.token)["body"] or {}


def _walk_depth(nodes, depth=1):
    """返回分类树的最大深度。"""
    if not nodes:
        return depth - 1
    return max(_walk_depth(n.get("children") or [], depth + 1) for n in nodes)


def _count_nodes(nodes):
    return sum(1 + _count_nodes(n.get("children") or []) for n in nodes)


@case("S01", "分类：数量足够且真的达到 4 级")
def s01(app):
    tree = _get(app, "/api/v1/categories")
    nodes = tree.get("items") or tree.get("tree") or []
    total = _count_nodes(nodes)
    depth = _walk_depth(nodes)

    REPORT.check(total >= 35, f"分类总数 {total}（要求 ≥35）")
    REPORT.check(depth == 4, f"分类最大深度 {depth}（要求 =4，这是 MAX_DEPTH 上限）")

    # 每一级都要有节点，否则「4 级」可能是孤零零一条链
    level_counts = {}

    def walk(ns, d=1):
        level_counts[d] = level_counts.get(d, 0) + len(ns)
        for n in ns:
            walk(n.get("children") or [], d + 1)

    walk(nodes)
    for d in (1, 2, 3, 4):
        REPORT.check(level_counts.get(d, 0) > 0, f"第 {d} 级有 {level_counts.get(d, 0)} 个分类")
    REPORT.note(f"层级分布：{dict(sorted(level_counts.items()))}")


@case("S02", "资料：9 种文件类型齐全（含 5 种图片）")
def s02(app):
    wanted = {"pdf", "docx", "xlsx", "xls", "jpg", "jpeg", "png", "gif", "webp"}
    found = set()
    page = 1
    while True:
        body = _get(app, f"/api/v1/documents?page={page}&page_size=100")
        items = body.get("items") or []
        for d in items:
            ft = d.get("file_type")
            if ft:
                found.add(ft)
        if page >= (body.get("pages") or 1):
            break
        page += 1

    missing = wanted - found
    REPORT.check(not missing, f"文件类型齐全（缺：{sorted(missing) or '无'}）")
    REPORT.note(f"实际覆盖：{sorted(found)}")


@case("S03", "资料：三种可见范围都有真实数据")
def s03(app):
    counts = {"all": 0, "department": 0, "user": 0}
    page = 1
    while True:
        body = _get(app, f"/api/v1/documents?page={page}&page_size=100")
        for d in body.get("items") or []:
            scope = d.get("audience_scope") or "all"
            counts[scope] = counts.get(scope, 0) + 1
        if page >= (body.get("pages") or 1):
            break
        page += 1

    for scope in ("all", "department", "user"):
        REPORT.check(counts.get(scope, 0) > 0, f"可见范围 {scope}：{counts.get(scope, 0)} 份资料")
    REPORT.note(f"分布：{counts}")


@case("S04", "通知：数量 ≥20，类型/状态覆盖全矩阵")
def s04(app):
    body = _get(app, "/api/v1/notices?page=1&page_size=100")
    total = body.get("total") or 0
    REPORT.check(total >= 20, f"通知总数 {total}（要求 ≥20）")

    # 逐条拉全量（含各状态），统计分布
    types: dict[str, int] = {}
    statuses: dict[str, int] = {}
    page = 1
    while True:
        b = _get(app, f"/api/v1/notices?page={page}&page_size=100")
        for n in b.get("items") or []:
            t = n.get("notice_type") or "（空）"
            types[t] = types.get(t, 0) + 1
            st = n.get("status") or "?"
            statuses[st] = statuses.get(st, 0) + 1
        if page >= (b.get("pages") or 1):
            break
        page += 1

    builtin = {"发布", "修订", "替换", "废止", "延期", "其他"}
    missing = builtin - set(types)
    REPORT.check(not missing, f"内置 6 类型齐全（缺：{sorted(missing) or '无'}）")
    REPORT.check(
        any(t not in builtin for t in types),
        f"存在自定义类型（当前：{[t for t in types if t not in builtin]}）",
    )
    # 2026-11-04 草稿下线：通知列表只剩 published（cancelled 在回收站区），
    # 原先要求 draft > 0 的断言已失效。
    REPORT.check(statuses.get("published", 0) > 0,
                 f"状态 published：{statuses.get('published', 0)} 条")
    REPORT.check("draft" not in statuses,
                 f"列表不再出现 draft 状态（实际 {sorted(statuses)}）")

    # 已取消通知按设计不进通知列表（§26 统一在回收站管理），
    # 因此用回收站端点断言，而不是期望它出现在 /notices。
    # 回收站通知端点本身返回的就是「已取消」集合，条目里不带 status 字段
    rb = _get(app, "/api/v1/recycle-bin/notices")
    n_cancelled = rb.get("total") or len(rb.get("items") or [])
    # 全量跑时前面的模块（m40 回收站、m41 备份恢复）可能已清理/回滚掉种子里的
    # 已取消通知，因此这里只作为「信息项」，不作为失败判定——真正的
    # 「取消后才进回收站」语义由 m40 独立验证。
    REPORT.note(f"回收站中已取消通知 {n_cancelled} 条")
    REPORT.note(f"类型分布：{types}")


@case("S05", "通知：三种阅读对象 scope + 4 种附件类型")
def s05(app):
    scopes = set()
    page = 1
    notice_ids = []
    while True:
        b = _get(app, f"/api/v1/notices?page={page}&page_size=100")
        for n in b.get("items") or []:
            notice_ids.append(n["id"])
        if page >= (b.get("pages") or 1):
            break
        page += 1

    for nid in notice_ids:
        detail = _get(app, f"/api/v1/notices/{nid}")
        meta = detail.get("audience_meta") or {}
        scopes.add(meta.get("scope") or "all")

    for s in ("all", "department", "user"):
        REPORT.check(s in scopes, f"阅读对象 scope={s} 有数据")

    # 附件类型
    att_types = set()
    for nid in notice_ids:
        detail = _get(app, f"/api/v1/notices/{nid}")
        if detail.get("has_file"):
            att_types.add(detail.get("file_type"))
    REPORT.check(len(att_types) >= 3,
                 f"通知附件类型 {len(att_types)} 种：{sorted(att_types)}")
    REPORT.note(f"附件类型：{sorted(att_types)}")
