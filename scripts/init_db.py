"""手动初始化脚本：建表 + 创建初始超级管理员（幂等）。

用法：python scripts/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.init_db import init_db  # noqa: E402


if __name__ == "__main__":
    init_db()
    print("数据库初始化完成")
