"""Jinja2 模板与静态资源定位（app/web/）。

独立模块以避免 main.py ↔ routers 循环导入。
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

# 程序代码目录（app/）
APP_DIR = Path(__file__).resolve().parent.parent

# 静态资源：app/web/static -> /static
STATIC_DIR = APP_DIR / "web" / "static"
# Jinja2 模板：app/web/templates
TEMPLATES_DIR = APP_DIR / "web" / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))