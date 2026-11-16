"""FastAPI 应用入口。

- lifespan：确保 data/ 目录存在 + init_db() 建表并创建初始管理员（全新部署必须有表）
- 挂载 /static（仅 app/web/static；data/ 绝不静态暴露，§11/§19）+ Jinja2 模板
- 统一异常处理器（DESIGN §8.1 错误契约）
- 注册路由：页面（pages.py，含工作台首页）+ 全部 REST 模块
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.compression import SelectiveGZipMiddleware

from app.core.config import get_settings
from app.core.errors import AppError, error_payload
from app.core.logging_config import configure_logging
from app.core.security_middleware import SecurityMiddleware
from app.core import maintenance
from app.db.init_db import init_db
from app.routers import (
    audits,
    auth,
    backups,
    categories,
    dashboard,
    departments,
    documents,
    notices,
    notice_types,
    pages,
    recycle_bin,
    tags,
    users,
)
from app.schemas.common import PasswordStrengthError
from app.web.templating import STATIC_DIR

logger = logging.getLogger(__name__)

settings = get_settings()

# 日志装配（2026-10-16 修复：此前 INFO 全丢、无文件日志）：root + app.* → INFO，
# 双写 stderr 与 data/logs/app.log（10MB×5 轮转）。幂等，测试环境写临时 DATA_DIR。
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动钩子：建目录 + 建表 + 初始管理员（幂等）+ 自动备份调度。"""
    settings.ensure_dirs()
    init_db()
    logger.info("数据库初始化完成: %s", settings.database_path)
    from app.core import scheduler

    # 磁盘健康巡检（项目规范 §28 配套）：空间不足只告警不阻止启动——
    # 磁盘紧张时系统仍应可访问（查看/下载/导出），真正的硬闸门在备份入口
    # （backup_service.create_backup → assert_disk_headroom）。
    from app.core.disk_health import log_startup_disk_health

    log_startup_disk_health()

    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


# 生产环境关闭交互式 API 文档（/docs、/redoc、/openapi.json）。
# 理由：本项目是公司内部系统（项目规范 §2 非目标第 10 条「不对外公开访问」），
# 完整 API 契约无需对未登录访客开放；开发环境保留以便联调。
# 注意：只影响文档三件套，不影响任何业务路由。
#
# 2026-10-29 修复：此前用 `settings.APP_ENV != "production"` 原始字符串比较，
# 而 check_secret_key 用归一化值判定 —— `APP_ENV=Production` / `PRODUCTION` /
# `' production '` 会被判为「非生产」，于是生产加固生效了、文档却仍对外开放。
# 现统一走 settings.is_production（归一化），不再有两套判定标准。
_DOCS_ENABLED = not settings.is_production

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="公司内部资料与通知管理系统",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)

# ---------------------------------------------------------------
# 统一异常处理器（DESIGN §8.1）
# ---------------------------------------------------------------
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, exc.detail),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    detail = {"fields": exc.errors()}
    return JSONResponse(
        status_code=400,
        content=error_payload("VALIDATION_ERROR", "请求参数校验失败", detail),
    )


@app.exception_handler(PasswordStrengthError)
async def password_strength_error_handler(
    request: Request, exc: PasswordStrengthError
):
    """密码强度不足 → 400 VALIDATION_ERROR（而非 500）。"""
    return JSONResponse(
        status_code=400,
        content=error_payload("VALIDATION_ERROR", str(exc)),
    )


@app.exception_handler(SQLAlchemyTimeoutError)
async def database_pool_timeout_handler(
    request: Request, exc: SQLAlchemyTimeoutError
):
    """连接池排队超时 → 503（可重试），不把容量问题伪装成 500 内部错误。"""
    logger.error(
        "数据库连接池繁忙: %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=503,
        content=error_payload("DB_BUSY", "系统繁忙，请稍后重试"),
        headers={"Retry-After": "5"},
    )



def _contains_pool_timeout(exc: BaseException) -> bool:
    """识别 anyio/FastAPI 包装后的连接池超时，避免容量问题返回 500。"""
    stack: list[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, SQLAlchemyTimeoutError):
            return True
        nested = getattr(current, "exceptions", None)
        if nested:
            stack.extend(nested)
    return False

@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    # detail 可能携带上游/中间件内部细节：5xx 一律固定文案，原始信息进日志
    message = (
        str(exc.detail)
        if exc.status_code < 500
        else "服务器内部错误"
    )
    if exc.status_code >= 500:
        logger.error("HTTP %s 于 %s %s: %s", exc.status_code, request.method, request.url.path, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload("HTTP_ERROR", message),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    if _contains_pool_timeout(exc):
        logger.error(
            "数据库连接池繁忙（异常组包装）: %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=503,
            content=error_payload("DB_BUSY", "系统繁忙，请稍后重试"),
            headers={"Retry-After": "5"},
        )
    logger.exception("未捕获异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", "服务器内部错误"),
    )


# ---------------------------------------------------------------
# 安全中间件：CSRF Origin 校验（L-2）
# ---------------------------------------------------------------
app.add_middleware(SecurityMiddleware)
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024, compresslevel=6)


@app.middleware("http")
async def _maintenance_gate(request: Request, call_next):
    """恢复期间对业务 API 快速失败，健康检查始终放行。

    返回 503 + Retry-After，让客户端明确知道这是暂时状态，而不是权限或
    数据错误。恢复请求本身在进入维护状态前已经通过这里，因此不会被自锁。
    """
    is_health = request.url.path.rstrip("/") == "/health"
    if not is_health and maintenance.is_maintenance() and request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=503,
            content=error_payload(
                "MAINTENANCE_MODE",
                "系统正在恢复备份，请稍后重试",
                {"retry_after_seconds": 5},
            ),
            headers={"Retry-After": "5"},
        )
    return await call_next(request)

# ---------------------------------------------------------------

# ---------------------------------------------------------------
# 静态资源与路由
# ---------------------------------------------------------------
# 仅挂载程序内静态资源；data/ 绝不通过 StaticFiles 暴露
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def _static_revalidate_cache(request, call_next):
    """静态资源协商缓存（Cache-Control: no-cache）：

    浏览器每次使用缓存前必须向服务器校验（ETag/304）——文件更新立即生效，
    未更新走 304 零流量。修复"发新版 JS 后用户页面仍是旧行为"的问题。
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(departments.router)
app.include_router(categories.router)
app.include_router(tags.router)
app.include_router(documents.router)
app.include_router(notices.router)
app.include_router(notice_types.router)
app.include_router(recycle_bin.router)
app.include_router(backups.router)
app.include_router(audits.router)
app.include_router(dashboard.router)


@app.get("/health", include_in_schema=False)
async def health():
    """健康检查（部署探活用）。"""
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}
