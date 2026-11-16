"""迁移执行器：按序执行 scripts/migrations/ 下未应用的幂等迁移。

用法：
    python scripts/migrate.py            # 执行未应用迁移
    python scripts/migrate.py --check    # 仅列出待执行

约定（最小机制，适配 SQLite 小系统）：
- 每个迁移文件 `NNN_描述.py`，提供：
    is_applied(conn) -> bool    # 幂等检测（已应用返回 True，跳过）
    migrate(conn)               # 执行变更（conn 为 autocommit 连接，
                                #   需自行 BEGIN/COMMIT，参考 001 内 _rebuild）
- 由文件名序号保证顺序；检测式幂等，不引入版本表
- 真实库与演示库共用：测试库走 Base.metadata.create_all（模型即最新 schema），
  迁移只负责"既有库的演进"，两者通过 conftest 隔离保证互不干扰
- 分工（2026-10-25 固化，评审 A1/M1）：**所有结构演进**（加列/新表/改约束）
  只进本目录，不进 app/db/init_db.py 的内联迁移（该清单已冻结，只收纯数据修复）
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "scripts" / "migrations"
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings  # noqa: E402


def _load_migrations() -> list[tuple[str, object]]:
    if not MIGRATIONS_DIR.exists():
        return []
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9]*_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        out.append((path.stem, mod))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="按序执行未应用的幂等迁移")
    parser.add_argument("--check", action="store_true", help="仅检查不执行")
    args = parser.parse_args()

    db_path = get_settings().data_dir / "database" / "app.db"
    if not db_path.exists():
        print(f"数据库不存在: {db_path}（新库由 create_all 建表，无需迁移）")
        return

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        pending = []
        for stem, mod in _load_migrations():
            applied = bool(mod.is_applied(conn))
            status = "已应用" if applied else "待执行"
            print(f"  [{status}] {stem}")
            if not applied:
                pending.append((stem, mod))
        if not pending:
            print("✅ 全部迁移已应用")
            return
        if args.check:
            print(f"待执行 {len(pending)} 个迁移（用 --check 查看）")
            return
        print("\n⚠️  建议先备份 data/database/ 再继续")
        for stem, mod in pending:
            print(f"  → 执行 {stem} ...")
            mod.migrate(conn)
            print(f"  ✓ {stem} 完成")
        print("✅ 迁移完成")
    finally:
        conn.close()


if __name__ == "__main__":
    main()