"""应用日志装配（2026-10-16 修复：此前全仓只有 getLogger、零配置）。

问题背景：Python 的 logging 在无 handler 配置时，INFO 及以下记录被直接丢弃
（lastResort 只输出 WARNING+ 到 stderr 且无格式），uvicorn 默认配置也只管
`uvicorn.*` 自己的 logger——应用所有 `logger.info(...)` 全部丢失，生产环境
没有文件日志、没有轮转、没有分级。

本模块在 app.main 导入时装配一次：
- root logger：INFO，双 handler（stderr 控制台 + data/logs/app.log 轮转文件）
- app.* logger：显式 INFO（propagate 到 root，与控制台/文件双写）
- 不碰 uvicorn 系 logger：uvicorn/uvicorn.access 自带 handler 且
  propagate=False（不会重复）；uvicorn.error 无自有 handler、靠传播到
  root——正好落入我们的 handler，单份不重样。

文件位置走 settings.data_dir/logs（测试环境即临时 DATA_DIR，不碰真实 data/）。
"""
from __future__ import annotations

import logging
import logging.config
import logging.handlers
from pathlib import Path

from app.core.config import get_settings

_CONFIGURED = False


def configure_logging() -> None:
    """装配 root + app logger（幂等：重复调用仅首次生效）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    settings = get_settings()
    log_dir: Path = settings.data_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "app.log"
    except OSError:
        # 日志目录不可写（只读盘/权限）时退化为纯控制台，不阻断启动
        log_file = None

    handlers: dict = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stderr",
        },
    }
    root_handlers = ["console"]
    if log_file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "standard",
            "filename": str(log_file),
            "maxBytes": 10 * 1024 * 1024,  # 10MB
            "backupCount": 5,
            "encoding": "utf-8",
        }
        root_handlers.append("file")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": handlers,
            "root": {"handlers": root_handlers, "level": "INFO"},
            "loggers": {
                # 应用日志显式 INFO；第三方库按 root 的 INFO 即可，
                #  noisy 依赖（如 urllib3 调试）不会主动开
                "app": {"level": "INFO"},
            },
        }
    )
