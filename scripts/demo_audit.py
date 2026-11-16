"""演示库只读审计：找出验收前需要清理/停用的内容，不删除任何数据。

用法（默认读取当前 DATA_DIR）：
    .venv/bin/python scripts/demo_audit.py
    .venv/bin/python scripts/demo_audit.py --check   # 有需清理项时 exit 1
    DATA_DIR=/srv/fms-demo-data .venv/bin/python scripts/demo_audit.py

判定口径：
- 种子内容：标题以 seed_demo.py 的已知前缀开头（DEMO-/GUI验证/浏览器实测-/EXTREME-/巡检-）
- 非种子内容：不在上述前缀内，--clean 不会清理，需要人工决定保留/归档
- 测试字样：标题、说明、正文中的 测试/hahsi/asdf/test
- 演示账号：manifest 用户、用户名含 demo、显示名含“演示”、仍使用演示密码的账号
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import verify_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.category import Category, Tag  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.notice import Notice  # noqa: E402
from app.models.user import Department, User  # noqa: E402

MANIFEST_FILE = PROJECT_ROOT / "scripts" / "seed_demo_manifest.json"
SEED_PREFIXES = ("DEMO-", "GUI验证", "浏览器实测-", "EXTREME-", "巡检-")
TEST_RE = re.compile(r"测试|hahsi|asdf|\btest\b", re.IGNORECASE)
TEST_NAME_RE = re.compile(r"测试|GUI验证|浏览器实测|EXTREME|巡检|hahsi|asdf|\btest\b", re.IGNORECASE)
DEMO_PASSWORD = "Demo@123456"


def _is_seeded(title: str) -> bool:
    return any((title or "").startswith(prefix) for prefix in SEED_PREFIXES)


def _test_hits(*values: Any) -> list[str]:
    hits = []
    for value in values:
        if not value:
            continue
        text = str(value)
        if TEST_RE.search(text):
            hits.append(text.strip().replace("\n", " ")[:80])
    return hits


def _load_manifest() -> dict[str, list[int]]:
    if not MANIFEST_FILE.exists():
        return {}
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _manifest_stale(manifest: dict[str, list[int]], actual: dict[str, set[int]]) -> list[str]:
    problems = []
    for key, ids in actual.items():
        expected = set(manifest.get(key) or [])
        if expected and not expected.issubset(ids):
            missing = sorted(expected - ids)
            problems.append(f"{key}: manifest 中 {len(missing)} 个 id 已不存在（如 {missing[:5]}）")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="演示库只读审计（不修改数据）")
    parser.add_argument("--check", action="store_true", help="存在需清理项时返回非零退出码")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于 CI 消费")
    args = parser.parse_args()

    settings = get_settings()
    db = SessionLocal()
    try:
        docs = db.execute(select(Document).order_by(Document.id)).scalars().all()
        notices = db.execute(select(Notice).order_by(Notice.id)).scalars().all()
        users = db.execute(select(User).order_by(User.id)).scalars().all()
        categories = db.execute(select(Category).order_by(Category.id)).scalars().all()
        tags = db.execute(select(Tag).order_by(Tag.id)).scalars().all()
        departments = db.execute(select(Department).order_by(Department.id)).scalars().all()
        actual_ids = {
            "documents": {d.id for d in docs},
            "notices": {n.id for n in notices},
            "users": {u.id for u in users},
            "categories": {c.id for c in categories},
            "tags": {t.id for t in tags},
            "departments": {d.id for d in departments},
        }
        manifest = _load_manifest()

        non_seed_docs = [d for d in docs if not _is_seeded(d.title)]
        non_seed_notices = [n for n in notices if not _is_seeded(n.title)]
        test_content: list[dict[str, Any]] = []
        for d in docs:
            hits = _test_hits(d.title, d.summary)
            if hits:
                test_content.append({"kind": "document", "id": d.id, "title": d.title, "hits": hits})
        for n in notices:
            hits = _test_hits(n.title, n.content)
            if hits:
                test_content.append({"kind": "notice", "id": n.id, "title": n.title, "hits": hits})

        test_taxonomies = []
        for kind, rows in (("category", categories), ("tag", tags), ("department", departments)):
            for row in rows:
                if TEST_NAME_RE.search(row.name or ""):
                    test_taxonomies.append({"kind": kind, "id": row.id, "name": row.name})

        manifest_user_ids = set(manifest.get("users") or [])
        demo_accounts = []
        for u in users:
            is_demo = (
                u.id in manifest_user_ids
                or "demo" in (u.username or "").lower()
                or "演示" in (u.display_name or "")
            )
            uses_demo_password = bool(
                u.password_hash and verify_password(DEMO_PASSWORD, u.password_hash)
            )
            if is_demo or uses_demo_password:
                demo_accounts.append({
                    "id": u.id,
                    "username": u.username,
                    "display_name": u.display_name,
                    "role": u.role,
                    "is_active": bool(u.is_active),
                    "uses_demo_password": uses_demo_password,
                })

        stale = _manifest_stale(manifest, actual_ids)
        problems = bool(
            non_seed_docs or non_seed_notices or test_content
            or test_taxonomies or demo_accounts
        )
        payload = {
            "data_dir": str(settings.data_dir),
            "database": str(settings.database_path),
            "counts": {
                "documents": len(docs),
                "documents_active": sum(1 for d in docs if d.archived_at is None),
                "notices": len(notices),
                "users": len(users),
                "categories": len(categories),
                "tags": len(tags),
                "departments": len(departments),
            },
            "non_seed_documents": [
                {"id": d.id, "title": d.title, "archived": d.archived_at is not None}
                for d in non_seed_docs
            ],
            "non_seed_notices": [
                {"id": n.id, "title": n.title, "status": n.status} for n in non_seed_notices
            ],
            "test_content": test_content,
            "test_taxonomies": test_taxonomies,
            "demo_accounts": demo_accounts,
            "manifest_stale": stale,
            "needs_cleanup": problems,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("=" * 68)
            print("演示库只读审计")
            print("=" * 68)
            print(f"数据目录: {payload['data_dir']}")
            print("数量:", payload["counts"])
            print()

            if non_seed_docs or non_seed_notices:
                print("[非种子内容] --clean 不会清理，需要人工决定保留/归档：")
                for d in payload["non_seed_documents"]:
                    print(f"  - 资料 #{d['id']}「{d['title']}」 archived={d['archived']}")
                for n in payload["non_seed_notices"]:
                    print(f"  - 通知 #{n['id']}「{n['title']}」 status={n['status']}")
                print()
            if test_content:
                print("[测试字样] 标题/说明/正文命中 测试|hahsi|asdf|test：")
                for item in test_content:
                    print(f"  - {item['kind']} #{item['id']}「{item['title']}」 -> {item['hits']}")
                print()
            if test_taxonomies:
                print("[测试分类/标签/部门] 名称含测试/验证等字样：")
                for item in test_taxonomies:
                    print(f"  - {item['kind']} #{item['id']}「{item['name']}」")
                print()
            if demo_accounts:
                print("[演示账号] 建议验收前停用或重置密码：")
                for u in demo_accounts:
                    flags = []
                    if u["is_active"]:
                        flags.append("启用中")
                    if u["uses_demo_password"]:
                        flags.append("仍用演示密码")
                    print(
                        f"  - #{u['id']} {u['username']}（{u['display_name']}，{u['role']}）"
                        f" {' / '.join(flags) or '已处理'}"
                    )
                print()
            if stale:
                print("[manifest 提示] 种子清单与当前库不一致（清理以标题前缀为准，不作为失败项）：")
                for line in stale:
                    print(f"  - {line}")
                print()
            if not problems:
                print("[OK] 未发现非种子内容、测试字样或演示账号。")
            else:
                print("[FAIL] 存在需清理/停用项，验收前请先处理。")
            print("=" * 68)
        return 1 if (args.check and problems) else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
