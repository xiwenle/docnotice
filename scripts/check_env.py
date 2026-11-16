#!/usr/bin/env python
"""部署前 .env 配置预检（上线前跑一次，避免"启动才发现配错"）。

用法：
    python scripts/check_env.py                # 读 .env，按 APP_ENV 判定
    python scripts/check_env.py --env-file .env.production
    python scripts/check_env.py --strict       # 开发环境也按生产标准要求

退出码：
    0 = 全部通过
    1 = 存在【错误】项（阻塞上线）
    2 = 脚本自身异常

检查项与判据
------------
| 项 | 级别 | 判据 |
|---|---|---|
| .env 存在 | 错误 | 文件存在且可读 |
| APP_ENV 合法 | 错误 | development / production / test |
| SECRET_KEY 非占位 | 错误(prod) / 警告(dev) | 不是 change-me-to-a-long-random-string 等占位值 |
| SECRET_KEY 长度 | 错误(prod) / 警告(dev) | >= 32 字节（HS256 推荐，PyJWT>=2.15 会对短密钥告警） |
| INITIAL_ADMIN_PASSWORD | 错误(prod) | 非占位值，且 >=8 位含字母数字 |
| COOKIE_SECURE 与 APP_ENV 匹配 | 警告 | production 建议 true；内网 http 需显式 false（否则浏览器静默拒存 Cookie） |
| DATA_DIR 可写 | 错误 | 目录可创建且可写 |
| 数据库路径可写 | 错误 | 父目录可写 |
| BACKUP_SCHEDULE 格式 | 错误 | "sat 03:00" / "daily 03:00" / 空 |
| TRUSTED_HOSTS CSRF 白名单 | 错误(prod/strict) / 警告(dev) | 必须显式包含正式访问域名或 IP；通配 * 禁用 |

本脚本**只读**，不修改任何配置、不连接数据库、不启动应用。
它与 `app/core/config.py::check_secret_key` 互补：后者是"启动时的最后一道
防线"（配错直接拒绝启动），本脚本是"部署前的体检"（提前说清哪里没配好）。
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 占位值（与 app/core/config.py check_secret_key 保持一致）
SECRET_PLACEHOLDERS = {
    "change-me-to-a-long-random-string",
    "dev-insecure-secret",
    "secret",
    "changeme",
}
ADMIN_PASSWORD_PLACEHOLDERS = {
    "please-change-me",
    "admin1234",
    "password",
    "12345678",
}
MIN_SECRET_BYTES = 32

OK, WARN, ERR = "OK", "WARN", "ERROR"

_results: list[tuple[str, str, str]] = []


def _record(level: str, item: str, message: str) -> None:
    _results.append((level, item, message))


def _parse_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，忽略空行与 # 注释，不做变量展开。

    不复用 pydantic-settings，是因为本脚本要在【配置非法时也能跑完并给出
    全部问题】，而 Settings 一旦遇到非法 APP_ENV 就会抛异常中断。

    注意行尾注释：`.env` 常见 `KEY=value   # 注释` 写法（本仓库 .env 即如此），
    必须剥掉注释部分，否则 APP_ENV 会变成 "development   # ..." 而被判非法。
    但**不能**简单按第一个 # 切分——引号内的 # 属于值的一部分。
    """
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        env[key] = _strip_inline_comment(value.strip())
    return env


def _strip_inline_comment(value: str) -> str:
    """剥掉行尾注释，保留引号内的 # 与引号本身。

    - `"abc#def"  # 注释` → `abc#def`（引号内的 # 属于值；引号后的注释剥离）
    - `abc#def`            → `abc#def`（# 前无空白，不视为注释）
    - `abc # 注释`          → `abc`（# 前有空白，视为注释）
    """
    if not value:
        return value
    if value[0] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            # 引号闭合：引号内的内容整体为值，其后（含注释）一律丢弃
            return value[1:end]
        # 引号未闭合：整体当作裸值处理
    hash_pos = value.find(" #")
    if hash_pos != -1:
        value = value[:hash_pos].strip()
    return value


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


# 本脚本认识的配置键（只有这些才从进程环境里取，避免把无关变量卷进来）
_KNOWN_KEYS = (
    "APP_NAME", "APP_ENV", "HOST", "PORT", "DATA_DIR", "DATABASE_URL",
    "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS",
    "DB_POOL_RECYCLE_SECONDS",
    "SECRET_KEY", "JWT_ALGORITHM", "JWT_EXPIRE_MINUTES", "COOKIE_SECURE",
    "BCRYPT_ROUNDS", "MAX_UPLOAD_SIZE_MB", "PREVIEW_DOCX_MAX_PARAS",
    "PREVIEW_XLSX_MAX_ROWS", "PREVIEW_XLSX_MAX_SHEETS", "LOGIN_RATE_WINDOW",
    "LOGIN_RATE_MAX_ATTEMPTS", "TRUST_REVERSE_PROXY_HEADERS", "TRUSTED_HOSTS",
    "INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD",
    "BACKUP_SCHEDULE", "BACKUP_KEEP_DAYS", "BACKUP_KEEP_COUNT",
    "BACKUP_PRE_KEEP_COUNT", "DISK_MIN_FREE_MB", "DISK_MIN_FREE_PERCENT",
)


def _merge_process_env(env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """把进程环境变量合并进 .env 结果（进程环境优先）。

    与 pydantic-settings 的优先级一致：真实启动时，systemd 的
    `Environment=` / 容器 env / shell export 都**压过** .env 文件。
    脚本若不合并，就会对"实际生效的配置"给出错误结论。

    返回 (合并结果, 被覆盖的键名列表)。
    """
    merged = dict(env)
    overridden: list[str] = []
    for key in _KNOWN_KEYS:
        value = os.environ.get(key)
        if value is None:
            continue
        if key in env and env[key] != value:
            overridden.append(key)
        elif key not in env:
            overridden.append(key)
        merged[key] = value
    return merged, overridden


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _validate_schedule(text: str) -> None:
    """校验备份调度串，非法抛 ValueError。

    判据与 `app/core/scheduler.py::parse_schedule` 一致，但**不导入该模块**
    ——它导入时会实例化 Settings 并建目录，破坏本脚本的只读承诺。
    两处格式如有变更需同步（tests/test_check_env.py 有对照用例）。
    """
    parts = (text or "").strip().lower().split()
    if len(parts) != 2:
        raise ValueError(f"调度格式应为 'sat 03:00' 或 'daily 03:00'，实际: {text!r}")
    day, clock = parts
    if day != "daily" and day not in _WEEKDAYS:
        raise ValueError(f"未知星期: {day!r}")
    hh, _, mm = clock.partition(":")
    if not (hh.isdigit() and mm.isdigit()):
        raise ValueError(f"时间格式应为 HH:MM，实际: {clock!r}")
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间非法: {clock!r}")


def _check_writable(target: Path, item: str, label: str) -> None:
    """检查目标目录【或其最近的已存在祖先】是否可写。

    只读纪律：不创建任何目录。目标不存在时向上找已存在的祖先——
    应用启动时 `ensure_dirs()` 会创建缺失目录，故"尚不存在"本身不是错误。
    """
    probe_dir = target
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    if not probe_dir.exists():
        _record(ERR, item, f"{label} → 找不到可检查的已存在祖先目录")
        return
    if not probe_dir.is_dir():
        _record(ERR, item, f"{label} → {probe_dir} 不是目录")
        return
    probe = probe_dir / f".check_env_write_probe_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("probe", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _record(ERR, item, f"{label} → 不可写：{probe_dir}（{exc}）")
        return
    finally:
        probe.unlink(missing_ok=True)
    suffix = "" if probe_dir == target else f"（目标尚不存在，其祖先 {probe_dir} 可写）"
    _record(OK, item, f"可写：{target}{suffix}")


def check_config(env: dict[str, str], env_file: Path, strict: bool) -> None:
    """执行全部检查项。"""
    app_env = (env.get("APP_ENV") or "development").strip().lower()
    is_prod = app_env == "production"
    # 严格模式：development 也按生产标准要求（仅提示 SECRET_KEY 等）
    treat_as_prod = is_prod or strict

    # --- APP_ENV 合法性（与 config.check_secret_key 同口径）---
    if app_env not in ("development", "production", "test"):
        _record(ERR, "APP_ENV", f"取值非法 {app_env!r}（仅支持 development/production/test）")
        # 后续判定依赖 app_env，非法时按最严格处理
        treat_as_prod = True
    else:
        _record(OK, "APP_ENV", f"{app_env}")

    # --- SECRET_KEY ---
    secret = (env.get("SECRET_KEY") or "").strip()
    if not secret:
        level = ERR if treat_as_prod else WARN
        _record(level, "SECRET_KEY", "未配置（必须设置：python -c \"import secrets;print(secrets.token_urlsafe(48))\"）")
    elif secret in SECRET_PLACEHOLDERS:
        level = ERR if treat_as_prod else WARN
        _record(level, "SECRET_KEY", "仍是占位值，必须替换为随机密钥")
    elif len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        level = ERR if treat_as_prod else WARN
        _record(
            level,
            "SECRET_KEY",
            f"长度 {len(secret.encode('utf-8'))} 字节 < {MIN_SECRET_BYTES}（HS256 推荐 >=32 字节）",
        )
    else:
        _record(OK, "SECRET_KEY", f"已配置，长度 {len(secret.encode('utf-8'))} 字节")

    # --- 初始管理员密码（仅首次引导时用）---
    admin_pwd = (env.get("INITIAL_ADMIN_PASSWORD") or "").strip()
    if not admin_pwd:
        _record(WARN, "INITIAL_ADMIN_PASSWORD", "未配置（已完成引导可忽略，否则首次启动无法建管理员；注意该账号不再强制改密，密码强度需自行保证）")
    elif admin_pwd in ADMIN_PASSWORD_PLACEHOLDERS:
        level = ERR if treat_as_prod else WARN
        _record(level, "INITIAL_ADMIN_PASSWORD", "仍是占位/弱密码，必须替换（初始管理员不再强制改密，此密码会被长期使用）")
    elif len(admin_pwd) < 8 or not (
        any(c.isalpha() for c in admin_pwd) and any(c.isdigit() for c in admin_pwd)
    ):
        level = ERR if treat_as_prod else WARN
        _record(level, "INITIAL_ADMIN_PASSWORD", "强度不足（>=8 位且同时含字母与数字）")
    else:
        _record(OK, "INITIAL_ADMIN_PASSWORD", "强度达标")

    # --- COOKIE_SECURE 与 APP_ENV 的匹配 ---
    cookie_secure_raw = (env.get("COOKIE_SECURE") or "").strip().lower()
    if cookie_secure_raw in ("", "none", "null"):
        # 未显式配置：production 默认 true，其余默认 false（与 config.cookie_secure 一致）
        expected = is_prod
        _record(
            OK,
            "COOKIE_SECURE",
            f"未显式配置 → 按 APP_ENV 自动为 {str(expected).lower()}",
        )
    elif cookie_secure_raw in ("true", "false"):
        actual = cookie_secure_raw == "true"
        if is_prod and not actual:
            _record(
                WARN,
                "COOKIE_SECURE",
                "production 下为 false：Cookie 不带 Secure 标记，明文 HTTP 下有被窃听风险",
            )
        elif not is_prod and actual:
            _record(
                WARN,
                "COOKIE_SECURE",
                "非 production 下为 true：内网 http:// 部署时浏览器会【静默不保存 Cookie】，"
                "表现为「登录成功却立刻退回登录页」",
            )
        else:
            _record(OK, "COOKIE_SECURE", cookie_secure_raw)
    else:
        _record(WARN, "COOKIE_SECURE", f"取值 {cookie_secure_raw!r} 无法识别（应为 true/false/留空）")

    # --- DATA_DIR 可写 ---
    # 本脚本是【只读体检】：绝不创建目录。写探针只做在**已存在**的目录里；
    # 若 DATA_DIR 尚不存在，则只检查其最近一个已存在的祖先是否可写
    # （目录尚不存在不算错——应用启动时 ensure_dirs() 会建）。
    data_dir_raw = (env.get("DATA_DIR") or "./data").strip()
    data_dir = Path(data_dir_raw)
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    _check_writable(data_dir, "DATA_DIR", f"数据根目录：{data_dir}")

    # --- 数据库路径可写 ---
    database_url = (env.get("DATABASE_URL") or "").strip()
    if database_url:
        raw_path = database_url.split("sqlite:///")[-1] if "sqlite:///" in database_url else ""
        if raw_path:
            _check_writable(Path(raw_path).parent, "DATABASE_URL", f"数据库所在目录：{Path(raw_path).parent}")
        else:
            _record(WARN, "DATABASE_URL", f"非 sqlite:/// 路径，未检查可写性：{database_url}")
    else:
        _check_writable(data_dir / "database", "DATABASE_URL", f"未显式配置 → 派生自 DATA_DIR：{data_dir / 'database' / 'app.db'}")

    # --- BACKUP_SCHEDULE 格式 ---
    # ⚠️ 只读纪律（2026-10-29 修复）：不能 `from app.core.scheduler import
    # parse_schedule` —— 该模块在**导入时**就执行 `settings = get_settings()`，
    # 而 get_settings() 内部调用 ensure_dirs()，会在磁盘上**创建 6 个目录**。
    # 那样本脚本就不再是"只读"（原承诺不成立，实测确实建了目录）。
    # 改为内联等价校验，判据与 scheduler.parse_schedule 保持一致。
    schedule = (env.get("BACKUP_SCHEDULE") or "").strip()
    if not schedule:
        _record(WARN, "BACKUP_SCHEDULE", "为空 = 自动备份未启用（AGENTS §28 建议至少开起来）")
    else:
        try:
            _validate_schedule(schedule)
            _record(OK, "BACKUP_SCHEDULE", f"{schedule}")
        except ValueError as exc:
            _record(ERR, "BACKUP_SCHEDULE", f"格式非法：{exc}")

    # --- TRUSTED_HOSTS：CSRF Host / Origin 白名单 ---
    # 本机默认值足够开发/测试，但生产浏览器写请求会被 403，除非把正式域名/IP 加进来。
    trusted_raw = (env.get("TRUSTED_HOSTS") or "").strip()
    entries = [part.strip().lower().rstrip(".") for part in trusted_raw.split(",") if part.strip()]
    has_wildcard = "*" in entries
    missing_explicit = not trusted_raw or all(
        item in {"localhost", "*.localhost", "127.0.0.1", "::1", "testserver"} for item in entries
    )
    if has_wildcard:
        _record(ERR, "TRUSTED_HOSTS", "包含通配 *；会放开 CSRF Host 校验，禁止用于部署")
    elif not trusted_raw:
        level = ERR if treat_as_prod else WARN
        _record(level, "TRUSTED_HOSTS", "未配置；应用默认值仅允许本机与 *.localhost，生产域名/IP 的写请求会被 403")
    elif missing_explicit:
        level = ERR if treat_as_prod else WARN
        _record(
            level,
            "TRUSTED_HOSTS",
            f"仍为开发/测试默认或仅含本机项（{trusted_raw}）；正式访问域名/IP 必须显式加入，否则浏览器带 Origin 的写请求会被 403",
        )
    else:
        _record(OK, "TRUSTED_HOSTS", f"已配置 {len(entries)} 项：{trusted_raw}")


    # --- 端口 / 主机 ---
    port = (env.get("PORT") or "8000").strip()
    if port.isdigit() and 1 <= int(port) <= 65535:
        _record(OK, "PORT", port)
    else:
        _record(ERR, "PORT", f"取值非法：{port!r}")

    # --- 磁盘健康（项目规范 §28 磁盘巡检纪律的部署前落地）---
    # 这里只报"当前"余量；备份时的**硬闸门**在 backup_service.create_backup。
    # 只读纪律：显式传阈值，避免 check_disk_health 走 get_settings() → ensure_dirs()。
    try:
        from app.core.disk_health import check_disk_health

        def _num(key: str, default):
            raw = (env.get(key) or "").strip()
            if not raw:
                return default
            try:
                return type(default)(raw)
            except ValueError:
                return default

        health = check_disk_health(
            data_dir,
            min_free_mb=_num("DISK_MIN_FREE_MB", 512),
            min_free_percent=_num("DISK_MIN_FREE_PERCENT", 5.0),
        )
        if health.ok:
            _record(OK, "DISK", health.describe())
        else:
            _record(
                ERR,
                "DISK",
                f"{health.describe()} —— 备份将被拒绝执行，请先清理磁盘",
            )
    except Exception as exc:  # noqa: BLE001
        _record(WARN, "DISK", f"无法采集磁盘余量：{exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="部署前 .env 配置预检（只读）")
    parser.add_argument("--env-file", default=None, help="要检查的 env 文件（默认 .env）")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：development 环境也按生产标准要求 SECRET_KEY 等",
    )
    args = parser.parse_args()

    # 每次运行必须从空结果开始：_results 是模块级全局，
    # 同一进程内重复调用 main()（例如被测试反复调用）会累积上一次的结果，
    # 导致结论基于陈旧数据。
    _results.clear()

    env_file = Path(args.env_file) if args.env_file else PROJECT_ROOT / ".env"
    if not env_file.is_absolute():
        env_file = PROJECT_ROOT / env_file

    print("=" * 68)
    print("部署前配置预检（只读，不修改任何文件）")
    print(f"配置文件：{env_file}")
    print("=" * 68)

    if not env_file.exists():
        _record(ERR, ".env", f"文件不存在：{env_file}（可从 .env.example 复制）")
    else:
        try:
            env = _parse_env_file(env_file)
        except Exception as exc:  # noqa: BLE001
            _record(ERR, ".env", f"解析失败：{exc}")
            env = {}
        # 环境变量优先级高于 .env（与 pydantic-settings 一致）。
        # 2026-10-29 修复：此前注释这么写但**代码根本没读 os.environ**，
        # 于是运维用 systemd `Environment=` 注入 APP_ENV/SECRET_KEY 时，
        # 脚本只看 .env 会给出与实际启动相反的结论（判定通过、真实启动拒绝）。
        merged, overridden = _merge_process_env(env)
        if overridden:
            _record(
                OK,
                "环境变量",
                f"{len(overridden)} 项被进程环境覆盖（优先于 .env）：{', '.join(overridden)}",
            )
        if merged:
            check_config(merged, env_file, args.strict)
        else:
            _record(ERR, ".env", "未解析出任何配置项")

    icons = {OK: "✅", WARN: "⚠️ ", ERR: "❌"}
    for level, item, message in _results:
        print(f"{icons[level]} [{level:5s}] {item:26s} {message}")

    errors = [r for r in _results if r[0] == ERR]
    warns = [r for r in _results if r[0] == WARN]
    print("-" * 68)
    print(f"合计：{len(_results)} 项 —— 错误 {len(errors)}，警告 {len(warns)}")
    if errors:
        print("结论：❌ 存在阻塞项，修正后重新执行本脚本。")
        return 1
    if warns:
        print("结论：⚠️  无阻塞项，但有警告项，请逐条确认。")
        return 0
    print("结论：✅ 全部通过，可以部署。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 预检脚本自身异常：{exc}", file=sys.stderr)
        sys.exit(2)
