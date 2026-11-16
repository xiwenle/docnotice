"""应用配置（pydantic-settings）。

所有路径 / 地址 / 端口 / 密钥 / 密码一律来自环境变量或 .env 文件，
禁止在代码中硬编码绝对路径与密钥（项目规范 §29）。

优先级：环境变量 > .env 文件 > 默认值。
"""
from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：app/core/config.py -> 上三级
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """应用配置模型。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用基本信息 ----
    APP_NAME: str = "作业资料系统"
    APP_ENV: str = "development"          # development | production
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ---- 身份认证（登录扩展接缝，项目规范 §24） ----
    # 当前默认 local（系统自身账号体系）
    AUTH_PROVIDER: str = "local"          # local | (预留: company)

    # ---- 数据目录与数据库 ----
    # 业务数据根目录：相对路径基于项目根目录解析；也接受绝对路径（由运维配置）
    DATA_DIR: str = "./data"
    # 显式数据库 URL；为空时自动派生为 sqlite:///<DATA_DIR>/database/app.db
    DATABASE_URL: str = ""
    # 数据库连接池：按 100 个普通用户、单进程短请求场景留出并发余量。
    # FastAPI/AnyIO 默认工作线程约 40 个，因此默认池上限与线程上限匹配。
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT_SECONDS: int = 10
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # ---- 安全 ----
    # 生产环境必须通过环境变量 / .env 提供足够长的随机密钥；为空或占位值在生产环境直接拒绝启动
    SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720
    # bcrypt cost 因子（4–31，默认 12 安全默认）；低配服务器登录慢可降为 10~11，
    # 只影响新哈希，已有哈希自适应校验
    BCRYPT_ROUNDS: int = 12
    # 登录 Cookie 的 Secure 标志（2026-09-15 可配置）。
    # 默认 None=按 APP_ENV 自动判断（production → True，其余 False）。
    # 之所以可配：内网部署常为 http://，一旦强制 Secure 浏览器会**静默不保存**
    # Cookie，表现为"登录成功却立刻回到登录页"，极难排查。
    # 内网 http 部署显式设 COOKIE_SECURE=false；接入 https 后设 true。
    COOKIE_SECURE: Optional[bool] = None

    # ---- 文件上传与预览 ----
    MAX_UPLOAD_SIZE_MB: int = 50
    PREVIEW_DOCX_MAX_PARAS: int = 5000
    PREVIEW_XLSX_MAX_ROWS: int = 2000
    PREVIEW_XLSX_MAX_SHEETS: int = 20

    # ---- 安全（L-2/H2）----
    # 登录限速：窗口秒数 + 窗口内最大尝试次数
    LOGIN_RATE_WINDOW: int = 60
    LOGIN_RATE_MAX_ATTEMPTS: int = 10
    # 是否信任 X-Forwarded-For 头（仅在部署了可信反向代理时开启，S4）
    TRUST_REVERSE_PROXY_HEADERS: bool = False
    # CSRF Host/Origin allowlist (comma-separated). Safe loopback/test defaults;
    # production domains/IPs must be configured explicitly. No unbounded wildcard.
    TRUSTED_HOSTS: str = "localhost,*.localhost,127.0.0.1,::1,testserver"

    # ---- 初始管理员（init_db 首次引导使用）----
    INITIAL_ADMIN_USERNAME: str = "admin"
    INITIAL_ADMIN_PASSWORD: str = "please-change-me"

    # ---- 备份策略（README §8、DEPLOY.md §6；全部可在 .env 覆盖）----
    # 自动备份调度："sat 03:00"（每周六凌晨3点）/ "daily 03:00"（每天）；留空=关闭
    BACKUP_SCHEDULE: str = "sat 03:00"
    # 备份保留天数：超过该天数的备份集在下次备份/恢复后自动清理；0=不按时间清理
    BACKUP_KEEP_DAYS: int = 90
    # 备份保留份数上限（兜底磁盘）：超出保留最新 N 份；0=不限份数
    BACKUP_KEEP_COUNT: int = 0
    # 恢复演练留旧目录（*.pre_<时间戳>）按时间戳分组保留的组数
    BACKUP_PRE_KEEP_COUNT: int = 3

    # ---- 磁盘健康阈值（项目规范 §28 配套：本地备份方案下的磁盘巡检）----
    # 背景：§28 锁定"不做异地灾备，服务器磁盘健康纳入日常巡检"，但该纪律
    # 此前无自动化支撑。本组阈值驱动 app/core/disk_health.py：
    # - 启动时巡检：不足 → 打 ERROR 日志（不阻止启动，磁盘紧张时仍需可访问）
    # - 备份前闸门：不足 → 拒绝备份（避免写出半个备份目录把盘彻底写满）
    # 绝对下限：可用空间至少 DISK_MIN_FREE_MB MB
    DISK_MIN_FREE_MB: int = 512
    # 相对下限：可用空间至少占总空间的 DISK_MIN_FREE_PERCENT %
    DISK_MIN_FREE_PERCENT: float = 5.0

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        """业务数据根目录（绝对路径）。"""
        p = Path(self.DATA_DIR)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p.resolve()

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def database_path(self) -> Path:
        return self.database_dir / "app.db"

    @property
    def database_url(self) -> str:
        """SQLite 连接串（显式配置优先，否则派生自 DATA_DIR）。"""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def documents_dir(self) -> Path:
        """当前资料文件目录。"""
        return self.data_dir / "documents"

    @property
    def notices_dir(self) -> Path:
        """通知文件目录。"""
        return self.data_dir / "notices"

    @property
    def uploads_dir(self) -> Path:
        """上传暂存/校验区（临时，完成后移入 documents|notices）。"""
        return self.data_dir / "uploads"

    @property
    def recycle_bin_dir(self) -> Path:
        """回收站文件目录。"""
        return self.data_dir / "recycle_bin"

    @property
    def backups_dir(self) -> Path:
        """备份集目录。"""
        return self.data_dir / "backups"

    # ------------------------------------------------------------------
    # 行为
    # ------------------------------------------------------------------
    @property
    def app_env(self) -> str:
        """归一化后的运行环境（小写、去首尾空白）。

        **所有**环境判定都必须走这里，不能直接比 `self.APP_ENV`。
        历史缺陷（2026-10-29 修复）：`check_secret_key` 做了归一化，
        但 `main.py` 的 `/docs` 开关用原始字符串比较，
        于是 `APP_ENV=Production` / `PRODUCTION` / `' production '` 会被判为
        「非生产」→ 生产环境下 `/docs`、`/redoc`、`/openapi.json` 仍对未登录开放。
        """
        return (self.APP_ENV or "").strip().lower()

    @property
    def is_production(self) -> bool:
        """是否生产环境（归一化判定）。"""
        return self.app_env == "production"

    @property
    def cookie_secure(self) -> bool:
        """登录 Cookie 是否带 Secure 标志。

        显式配置 COOKIE_SECURE 优先；未配置时按 APP_ENV 自动：
        production → True，其余 → False。
        """
        if self.COOKIE_SECURE is not None:
            return bool(self.COOKIE_SECURE)
        return self.is_production

    def ensure_dirs(self) -> None:
        """服务启动时自动创建全部数据目录（database/documents/notices/uploads/recycle_bin/backups）。"""
        for d in (
            self.database_dir,
            self.documents_dir,
            self.notices_dir,
            self.uploads_dir,
            self.recycle_bin_dir,
            self.backups_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def check_secret_key(self) -> None:
        """生产环境安全校验：SECRET_KEY 缺失或为占位值时拒绝启动。

        开发环境允许缺省（便于本地快速体验），但打印警告。
        生产环境同时拒绝占位初始管理员密码（防止弱密码窗口，
        2026-08-30 code review 边界项）。

        ⚠️ 2026-10-29 起，初始管理员**不再强制改密**（产品决策），
        所以这里的密码强度校验不再是"第二道防线"，而是**唯一防线**：
        生产环境下初始密码若为占位值直接拒绝启动，部署时必须配强密码。

        2026-09-15 加固（失败安全）：`APP_ENV` 拼写错误（如 "prodction"、
        "Production"、带尾随空格）此前会落到 else 分支，**静默降级为开发模式**
        ——SECRET_KEY 校验、占位密码校验、Cookie Secure 全部失效，而运维以为
        已经开了生产加固。现改为：非法值直接拒绝启动，不再猜。
        """
        allowed = ("development", "production", "test")
        env = self.app_env
        if env not in allowed:
            raise RuntimeError(
                f"APP_ENV 取值非法: {self.APP_ENV!r}（仅支持 {'/'.join(allowed)}）。"
                "拼写错误会导致安全检查被静默跳过，故直接拒绝启动；"
                "请修正 .env 后重试"
            )

        placeholder = "change-me-to-a-long-random-string"
        if env == "production":
            if not self.SECRET_KEY or self.SECRET_KEY in (
                placeholder,
                "dev-insecure-secret",
            ):
                raise RuntimeError(
                    "生产环境禁止使用缺失/占位 SECRET_KEY，请通过环境变量或 .env 配置足够长的随机密钥"
                )
            if self.INITIAL_ADMIN_PASSWORD in ("please-change-me", "admin1234", ""):
                raise RuntimeError(
                    "生产环境禁止使用占位/弱 INITIAL_ADMIN_PASSWORD（首次引导密码），"
                    "请在 .env 设置强密码；完成引导后可删除该配置项"
                )
        elif env != "production" and (
            not self.SECRET_KEY or self.SECRET_KEY == placeholder
        ):
            warnings.warn(
                "SECRET_KEY 未配置或为占位值，仅限开发环境使用！",
                stacklevel=1,
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取应用配置单例（首次调用后缓存）。"""
    settings = Settings()
    settings.ensure_dirs()
    settings.check_secret_key()
    return settings