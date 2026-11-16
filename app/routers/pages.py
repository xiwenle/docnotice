"""页面路由（docs/API.md 附录 A；DESIGN.md §14）。

页面与 REST API 共用同一套权限依赖：
- 匿名访问受保护页面 → 302 /login
- 普通用户访问 /admin/* 页面 → 302 /（权限由后端 REST 强制，§33）
- 已登录访问 /login → 302 /

页面本身只渲染壳，动态数据由页面内 JS fetch REST 契约端点获取。
"""
from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.db.session import get_db
from app.models.user import User
from app.services import user_service
from app.web.templating import STATIC_DIR, templates

router = APIRouter(tags=["pages"])

settings = get_settings()


# ---------------------------------------------------------------------------
# 页面上下文与鉴权辅助
# ---------------------------------------------------------------------------
def _resolve_user(request: Request, db: Session) -> User | None:
    """尝试从 Cookie/Bearer 解析当前用户；失败返回 None（不抛错）。"""
    try:
        return get_current_user(request, db)
    except AppError:
        return None


_NAV_BY_PATH = {
    "/": "dashboard",
    "/documents": "documents",
    "/notices": "notices",
    "/admin/documents/new": "admin-doc-new",
    "/admin/notices/new": "admin-notice-new",
    "/admin/categories": "admin-categories",
    "/admin/tags": "admin-tags",
    "/admin/notice-types": "admin-notice-types",
    "/admin/recycle-bin": "admin-recycle-bin",
    "/admin/users": "admin-users",
    "/admin/departments": "admin-departments",
    "/admin/backups": "admin-backups",
    "/admin/audit-logs": "admin-audit-logs",
}


def _nav_key(path: str) -> str:
    """把请求路径映射为左侧栏需要高亮的那一项。

    2026-09-14 换肤：导航由顶栏「管理」下拉改为左侧栏平铺，需要逐项高亮；
    而各管理页统一传 active_page="admin"（无区分度），故此处按路径派生。
    只影响导航高亮观感，不参与任何鉴权判断。
    """
    if path in _NAV_BY_PATH:
        return _NAV_BY_PATH[path]
    # /admin/documents/{id}/edit 归到「资料」；/admin/notices/{id}/edit 归到「通知」
    if path.startswith("/admin/documents/"):
        return "documents"
    if path.startswith("/admin/notices/"):
        return "admin-notice-stats" if path.endswith("/stats") else "notices"
    if path.startswith("/documents/"):
        return "documents"
    if path.startswith("/notices/"):
        return "notices"
    return ""


_NAV_SECTION = {
    "documents": ("资料", "/documents"),
    "notices": ("通知", "/notices"),
}


def _nav_section(nav_key: str, path: str) -> dict | None:
    """面包屑顶条的第二级（区段）。

    2026-09-14 换肤：顶条成为唯一面包屑，页面内自带的 .breadcrumb 一律隐藏
    （否则同一条路径会出现两遍）。管理段没有落地页，只作不可点文字。
    当前页就是区段列表页时返回 None，避免出现「资料 / 资料列表」这种重复。
    """
    if nav_key in _NAV_SECTION:
        label, href = _NAV_SECTION[nav_key]
        return None if href == path else {"label": label, "href": href}
    if nav_key.startswith("admin-"):
        return {"label": "管理", "href": None}
    return None


def _page_context(
    request: Request,
    user: User | None = None,
    *,
    active_page: str = "",
    page_title: str = "",
) -> dict:
    nav_key = _nav_key(request.url.path)
    ctx: dict[str, object] = {
        "request": request,
        "app_name": settings.APP_NAME,
        "active_page": active_page,
        "active_nav": nav_key,
        "nav_section": _nav_section(nav_key, request.url.path),
        "page_title": page_title,
    }
    if user is not None:
        ud = user_service.user_to_dict(user)
        ctx["current_user"] = ud
        # 权限模型 v3：is_admin 语义 = 管理视图，仅管理员
        ctx["is_admin"] = user.role == User.ROLE_ADMIN
        ctx["current_user_json"] = json.dumps(ud, ensure_ascii=False, default=str).replace(
            "</", "<\\/"
        )
    return ctx


def _redirect_login(request: Request) -> RedirectResponse:
    """未登录页面守卫：带上原始路径，登录后可回到深链接。"""
    next_path = request.url.path
    if request.url.query:
        next_path += "?" + request.url.query
    return RedirectResponse("/login?next=" + quote(next_path, safe=""), status_code=302)


def _redirect_home() -> RedirectResponse:
    return RedirectResponse("/", status_code=302)


def _safe_return_to(raw: str | None, fallback: str) -> str:
    """校验列表回跳地址。

    只接受本站相对路径 /documents、/notices，并原样保留 query；拒绝绝对 URL、
    协议相对 URL、路径穿越式别名和 fragment，无效时回落到调用方指定列表页。
    """
    if not raw or len(raw) > 2048:
        return fallback
    if not raw.startswith("/") or raw.startswith("//"):
        return fallback
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return fallback
    try:
        target = urlsplit(raw)
    except ValueError:
        return fallback
    if target.scheme or target.netloc or target.fragment:
        return fallback
    if target.path not in {"/documents", "/notices"}:
        return fallback
    return target.path + (("?" + target.query) if target.query else "")


# ---------------------------------------------------------------------------
# 工作台别名（2026-09-15）：工作台实际挂在 "/"，但 "/dashboard" 是外部自然猜法，
# 且登录页 next 参数、书签、同事间口口相传都会带上它。此前直接落到 404，
# 用户看到一整屏裸露 JSON 且无处可退。这里 301 永久指向 "/"，不改变任何行为，
# 也不产生新的页面实现（仍由 dashboard_page 单点承接）。
# ---------------------------------------------------------------------------
@router.get("/dashboard", include_in_schema=False)
def dashboard_alias():
    """别名 → 永久重定向到工作台 "/"。"""
    return RedirectResponse("/", status_code=301)


# ---------------------------------------------------------------------------
# 站点图标（消除浏览器默认 /favicon.ico 404 噪声）
# ---------------------------------------------------------------------------
@router.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    """兜底：浏览器默认路径 /favicon.ico 返回站内图标（200，杜绝 404 控制台噪声）。"""
    return FileResponse(str(STATIC_DIR / "favicon.ico"), media_type="image/x-icon")


# ---------------------------------------------------------------------------
# 认证页
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, db: Session = Depends(get_db)):
    """登录页（已登录用户直接回首页）。

    """
    if _resolve_user(request, db) is not None:
        return _redirect_home()
    ctx = _page_context(request, page_title="登录")
    return templates.TemplateResponse(request, "login.html", ctx)


@router.get("/change-password", response_class=HTMLResponse, include_in_schema=False)
def change_password_page(request: Request, db: Session = Depends(get_db)):
    """修改密码页（含首登强制改密）。"""
    user = _resolve_user(request, db)
    if user is None:
        return _redirect_login(request)
    ctx = _page_context(request, user, page_title="修改密码") | {
        "force_password_page": True
    }
    return templates.TemplateResponse(request, "change_password.html", ctx)


# ---------------------------------------------------------------------------
# 用户端页面（登录）
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """工作台：统计卡 + 最近资料 + 未读通知（匿名直接跳登录页）。"""
    user = _resolve_user(request, db)
    if user is None:
        return _redirect_login(request)
    return templates.TemplateResponse(
        request,
        "index.html",
        _page_context(request, user, active_page="dashboard", page_title="工作台"),
    )


@router.get("/documents", response_class=HTMLResponse, include_in_schema=False)
def documents_page(request: Request, db: Session = Depends(get_db)):
    """资料列表页（搜索/分类标签筛选/状态/按日期查状态）。"""
    user = _resolve_user(request, db)
    if user is None:
        return _redirect_login(request)
    return templates.TemplateResponse(
        request,
        "documents.html",
        _page_context(request, user, active_page="documents", page_title="资料列表"),
    )


@router.get("/documents/{doc_id}", response_class=HTMLResponse, include_in_schema=False)
def document_detail_page(request: Request, doc_id: int, db: Session = Depends(get_db)):
    """资料详情页（归档资料仅 admin+，接口层强制校验）。"""
    user = _resolve_user(request, db)
    if user is None:
        return _redirect_login(request)
    return templates.TemplateResponse(
        request,
        "document_detail.html",
        _page_context(
            request, user, active_page="documents", page_title="资料详情"
        )
        | {
            "doc_id": doc_id,
            "max_upload_mb": settings.MAX_UPLOAD_SIZE_MB,
            "return_to": _safe_return_to(
                request.query_params.get("return_to"), "/documents"
            ),
        },
    )


@router.get("/notices", response_class=HTMLResponse, include_in_schema=False)
def notices_page(request: Request, db: Session = Depends(get_db)):
    """通知列表页（普通用户仅本人可见；admin 可见全部）。"""
    user = _resolve_user(request, db)
    if user is None:
        return _redirect_login(request)
    return templates.TemplateResponse(
        request,
        "notices.html",
        _page_context(request, user, active_page="notices", page_title="通知列表"),
    )


@router.get("/notices/{notice_id}", response_class=HTMLResponse, include_in_schema=False)
def notice_detail_page(request: Request, notice_id: int, db: Session = Depends(get_db)):
    """通知详情页（只记录未查看/已查看阅读状态）。"""
    user = _resolve_user(request, db)
    if user is None:
        return _redirect_login(request)
    return templates.TemplateResponse(
        request,
        "notice_detail.html",
        _page_context(
            request, user, active_page="notices", page_title="通知详情"
        )
        | {
            "notice_id": notice_id,
            "return_to": _safe_return_to(
                request.query_params.get("return_to"), "/notices"
            ),
        },
    )


# ---------------------------------------------------------------------------
# 管理端页面（admin+）
# ---------------------------------------------------------------------------
def _admin_guard(request: Request, db: Session) -> RedirectResponse | None:
    """管理页守卫：未登录 → 302 /login；非 admin → 302 / 并带原因（权限模型 v3）。

    2026-09-15：非 admin 此前是**静默**跳回首页——用户点了同事发来的链接、
    或从旧书签进入，只会看到"页面自己跳走了"，无法分辨是权限问题还是系统坏了。
    现在带上 `?denied=forbidden`，由首页给出一次性提示（后端仍是唯一裁判：
    无论有没有这条跳转，管理 API 都独立校验角色）。
    """
    user = _resolve_user(request, db)
    if user is None:
        return _redirect_login(request)
    if user.role != "admin":
        return RedirectResponse("/?denied=forbidden", status_code=302)
    return None


@router.get("/admin/documents/new", response_class=HTMLResponse, include_in_schema=False)
def admin_document_new_page(request: Request, db: Session = Depends(get_db)):
    guard = _admin_guard(request, db)
    if guard is not None:
        return guard
    user = _resolve_user(request, db)
    return templates.TemplateResponse(
        request,
        "admin/document_form.html",
        _page_context(request, user, active_page="admin", page_title="新建资料")
        | {"edit_id": None},
    )


@router.get(
    "/admin/documents/{doc_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def admin_document_edit_page(request: Request, doc_id: int, db: Session = Depends(get_db)):
    guard = _admin_guard(request, db)
    if guard is not None:
        return guard
    user = _resolve_user(request, db)
    return templates.TemplateResponse(
        request,
        "admin/document_form.html",
        _page_context(request, user, active_page="admin", page_title="编辑资料")
        | {"edit_id": doc_id},
    )


@router.get("/admin/notices/new", response_class=HTMLResponse, include_in_schema=False)
def admin_notice_new_page(request: Request, db: Session = Depends(get_db)):
    guard = _admin_guard(request, db)
    if guard is not None:
        return guard
    user = _resolve_user(request, db)
    return templates.TemplateResponse(
        request,
        "admin/notice_form.html",
        _page_context(request, user, active_page="admin", page_title="新建通知")
        | {"edit_id": None},
    )


@router.get(
    "/admin/notices/{notice_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def admin_notice_edit_page(request: Request, notice_id: int, db: Session = Depends(get_db)):
    guard = _admin_guard(request, db)
    if guard is not None:
        return guard
    user = _resolve_user(request, db)
    return templates.TemplateResponse(
        request,
        "admin/notice_form.html",
        _page_context(request, user, active_page="admin", page_title="编辑通知")
        | {"edit_id": notice_id},
    )


@router.get(
    "/admin/notices/{notice_id}/stats",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def admin_notice_stats_page(request: Request, notice_id: int, db: Session = Depends(get_db)):
    guard = _admin_guard(request, db)
    if guard is not None:
        return guard
    user = _resolve_user(request, db)
    return templates.TemplateResponse(
        request,
        "admin/notice_stats.html",
        _page_context(request, user, active_page="admin", page_title="阅读统计")
        | {"notice_id": notice_id},
    )


def _admin_page(request: Request, db: Session, template: str, title: str):
    """通用管理页渲染守卫。"""
    guard = _admin_guard(request, db)
    if guard is not None:
        return guard
    user = _resolve_user(request, db)
    return templates.TemplateResponse(
        request,
        template,
        _page_context(request, user, active_page="admin", page_title=title),
    )


@router.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/users.html", "用户管理")


@router.get("/admin/departments", response_class=HTMLResponse, include_in_schema=False)
def admin_departments_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/departments.html", "部门管理")


@router.get("/admin/categories", response_class=HTMLResponse, include_in_schema=False)
def admin_categories_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/categories.html", "分类管理")


@router.get("/admin/tags", response_class=HTMLResponse, include_in_schema=False)
def admin_tags_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/tags.html", "标签管理")


@router.get("/admin/notice-types", response_class=HTMLResponse, include_in_schema=False)
def admin_notice_types_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/notice_types.html", "通知类型")


@router.get("/admin/recycle-bin", response_class=HTMLResponse, include_in_schema=False)
def admin_recycle_bin_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/recycle_bin.html", "回收站")


@router.get("/admin/audit-logs", response_class=HTMLResponse, include_in_schema=False)
def admin_audit_logs_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/audit_logs.html", "操作日志")


@router.get("/admin/backups", response_class=HTMLResponse, include_in_schema=False)
def admin_backups_page(request: Request, db: Session = Depends(get_db)):
    return _admin_page(request, db, "admin/backups.html", "备份恢复")