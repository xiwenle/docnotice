"""大表压测：5 万条资料下的资料列表 SQL 分页性能（48c7764 分页化后的验证）。

隔离设计：与 conftest 同套路——DATA_DIR/DATABASE_URL 指向 /tmp 临时目录，
在任何 app.* 导入之前设置，真实 data/ 零污染。数据用 executemany 直插
（不走上传 API），文件只造元数据行（列表接口只读元数据，不触碰磁盘）。

用法：python scripts/bench_pagination.py [N]
  N 为资料条数，默认 50000。

每个场景测两遍：第一遍为缓存冷调用（真实 SQL 成本），第二遍为 5 秒 TTL
缓存热调用。另打印 COUNT 与深翻页查询的 EXPLAIN QUERY PLAN 供索引核对。
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# 0) 环境隔离 —— 必须位于任何 app.* 导入之前
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_BENCH_TMP = Path(tempfile.mkdtemp(prefix="docnotice-bench-"))
_DATA_DIR = _BENCH_TMP / "data"
os.environ["DATA_DIR"] = str(_DATA_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///{(_DATA_DIR / 'database' / 'app.db').as_posix()}"
os.environ["SECRET_KEY"] = "bench-secret-key-for-benchmark-only"
os.environ["APP_ENV"] = "test"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.cache import cache  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.db.session import Base, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
import app.models  # noqa: E402,F401

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
PAGE_SIZE = 20
AS_OF = date(2026, 8, 30)


def iso(d: date) -> str:
    return d.isoformat()


def seed() -> dict:
    """直插 分类/标签/文件元数据/资料/标签关联，返回统计。"""
    from app.core.security import hash_password

    t0 = time.perf_counter()
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (username, password_hash, display_name, role,"
                " is_active, created_at, updated_at)"
                " VALUES ('bench_admin', :pw, '压测管理员', 'admin', 1,"
                " datetime('now'), datetime('now'))"
            ),
            {"pw": hash_password("Bench@12345")},
        )
        categories = [
            (1, "制度文件"), (2, "技术规范"), (3, "行政通知"), (4, "子文件夹"),
        ]
        conn.execute(
            text("INSERT INTO categories (id, name, parent_id, sort_order,"
                 " is_active, created_at, updated_at)"
                 " VALUES (:id, :name, :parent_id, 0, 1, datetime('now'), datetime('now'))"),
            [{"id": cid, "name": name, "parent_id": 1 if cid == 4 else None}
             for cid, name in categories],
        )
        conn.execute(
            text("INSERT INTO tags (id, name, created_at)"
                 " VALUES (:id, :name, datetime('now'))"),
            [{"id": i, "name": f"标签{i:03d}"} for i in range(1, 201)],
        )

        # 文件元数据：与资料 1:1（id 对齐），不入磁盘（列表接口只读元数据）
        files = []
        for i in range(1, TOTAL + 1):
            files.append({
                "id": i,
                "owner_type": "document",
                "owner_id": i,
                "original_filename": f"压测文件_{i:06d}.pdf",
                "internal_name": f"bench-{i:08d}-"
                + hashlib.sha256(f"bench-{i}".encode()).hexdigest()[:32],
                "file_type": "pdf",
                "extension": ".pdf",
                "mime_type": "application/pdf",
                "size_bytes": 1024 + (i % 4096),
                "sha256": hashlib.sha256(f"file-{i}".encode()).hexdigest(),
                "storage_rel_path": f"documents/bench-{i:08d}.pdf",
                "uploaded_by": 1,
            })
        conn.execute(
            text(
                "INSERT INTO file_records (id, owner_type, owner_id,"
                " original_filename, internal_name, file_type, extension,"
                " mime_type, size_bytes, sha256, storage_rel_path,"
                " status, uploaded_by, uploaded_at, created_at)"
                " VALUES (:id, :owner_type, :owner_id, :original_filename,"
                " :internal_name, :file_type, :extension, :mime_type,"
                " :size_bytes, :sha256, :storage_rel_path, 'active',"
                " :uploaded_by, datetime('now'), datetime('now'))"
            ),
            files,
        )

        # 资料：70% 有效 / 20% 已失效 / 10% 待生效；约 14% 未分类；标题含稀有关键词 3 条
        docs = []
        rare = {1000, 2000, 3000}
        for i in range(1, TOTAL + 1):
            m = i % 10
            if m < 7:  # active
                eff_from = date(2025, 1, 1) + timedelta(days=i % 300)
                eff_to = None if i % 2 == 0 else date(2027, 6, 30)
            elif m < 9:  # expired
                eff_from = date(2024, 1, 1) + timedelta(days=i % 300)
                eff_to = date(2025, 12, 31)
            else:  # pending
                eff_from = date(2027, 1, 1) + timedelta(days=i % 300)
                eff_to = None
            title = ("ZZZ稀有词资料" if i in rare else "压测资料") + f"-{i:06d}"
            docs.append({
                "id": i,
                "title": title,
                "summary": "性能压测生成的摘要文本",
                "category_id": None if i % 7 == 0 else (i % 3) + 1,
                "effective_from": iso(eff_from),
                "effective_to": iso(eff_to) if eff_to else None,
                "current_file_id": i,
                "rev_count": 1,
                "created_by": 1,
            })
        conn.execute(
            text(
                "INSERT INTO documents (id, title, summary, category_id,"
                " effective_from, effective_to, current_file_id,"
                " rev_count, created_by, created_at, updated_at)"
                " VALUES (:id, :title, :summary, :category_id,"
                " :effective_from, :effective_to,"
                " :current_file_id, :rev_count, :created_by,"
                " datetime('now'), datetime('now'))"
            ),
            docs,
        )

        # 标签关联：90% 资料各挂 2 个标签（10% 留作未打标签）
        pairs = set()
        for i in range(1, TOTAL + 1):
            if i % 10 == 0:
                continue
            pairs.add((i, (i * 7) % 200 + 1))
            pairs.add((i, (i * 13) % 200 + 1))
        conn.execute(
            text("INSERT INTO document_tags (document_id, tag_id)"
                 " VALUES (:d, :t)"),
            [{"d": d, "t": t} for d, t in sorted(pairs)],
        )

    # 分析统计信息，让查询计划器有据可依（生产迁移与 VACUUM/ANALYZE 运维同口径）
    with engine.begin() as conn:
        conn.execute(text("ANALYZE"))
    return {"seed_seconds": time.perf_counter() - t0}


SCENARIOS = [
    ("无筛选 · 第 1 页", "/api/v1/documents?page=1"),
    ("无筛选 · 深页 2500（OFFSET 49980）", f"/api/v1/documents?page={TOTAL // PAGE_SIZE}"),
    ("status=active（SQL 区间）", "/api/v1/documents?status=active&page=1"),
    ("status=expired（SQL 区间）", "/api/v1/documents?status=expired&page=1"),
    ("status=pending（SQL 区间）", "/api/v1/documents?status=pending&page=1"),
    ("关键词命中 3 条", "/api/v1/documents?keyword=ZZZ稀有词&page=1"),
    ("关键词命中 1 条", f"/api/v1/documents?keyword=压测资料-{TOTAL - 1:06d}&page=1"),
    ("关键词零命中", "/api/v1/documents?keyword=不存在的词xyz&page=1"),
    ("分类过滤（含子分类）", "/api/v1/documents?category_id=1&page=1"),
    ("标签过滤（EXISTS）", "/api/v1/documents?tag_id=8&page=1"),
    ("未打标签（NOT EXISTS）", "/api/v1/documents?untagged=true&page=1"),
    ("未分类", "/api/v1/documents?uncategorized=true&page=1"),
    ("有效期覆盖查询", "/api/v1/documents?cover_from=2026-06-01&cover_to=2026-06-30&page=1"),
]


def explain(conn, sql: str, label: str) -> None:
    print(f"\n  查询计划 · {label}")
    for row in conn.execute(text("EXPLAIN QUERY PLAN " + sql)):
        print(f"    {row[3]}")


def main() -> None:
    stats = seed()
    print(f"\n造数完成：{TOTAL} 资料 + {TOTAL} 文件元数据，耗时 {stats['seed_seconds']:.1f}s")
    print(f"临时库：{_BENCH_TMP}（会话结束由 OS 清理，真实 data/ 零接触）")

    token = create_access_token(user_id=1, username="bench_admin", role="admin")
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(fastapi_app)

    # 预热请求：排除首次请求的路由编译/连接建立等一次性开销
    warmup = client.get("/api/v1/documents?page=1", headers=headers)
    assert warmup.status_code == 200
    cache.clear()  # 预热数据不参与测量，保证各场景首调均为缓存冷调用

    print(f"\n{'场景':<28}{'总数':>8}{'冷调用(ms)':>12}{'热调用(ms)':>12}")
    print("-" * 60)
    for name, qs in SCENARIOS:
        url = qs
        t0 = time.perf_counter()
        r1 = client.get(url, headers=headers)
        cold = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2 = client.get(url, headers=headers)
        warm = (time.perf_counter() - t0) * 1000
        assert r1.status_code == 200 and r2.status_code == 200, f"{name}: {r1.status_code}"
        body = r1.json()
        assert len(body["items"]) <= PAGE_SIZE
        total = body["total"]
        flag = " ⚠️>1s" if cold > 1000 else ""
        print(f"{name:<28}{total:>8}{cold:>12.1f}{warm:>12.1f}{flag}")

    with engine.connect() as conn:
        explain(
            conn,
            f"SELECT COUNT(*) FROM documents WHERE archived_at IS NULL"
            f" AND effective_from <= '{AS_OF}'"
            f" AND (effective_to IS NULL OR effective_to >= '{AS_OF}')",
            "status=active 的 COUNT",
        )
        explain(
            conn,
            f"SELECT id FROM documents WHERE archived_at IS NULL"
            f" AND effective_from <= '{AS_OF}'"
            f" AND (effective_to IS NULL OR effective_to >= '{AS_OF}')"
            f" ORDER BY id DESC LIMIT 20 OFFSET {TOTAL - PAGE_SIZE}",
            "status=active 的深翻页",
        )

    print("\n说明：冷调用=真实 SQL 成本（缓存 5s TTL 未命中）；热调用=缓存命中。")
    print("关键词走 LIKE %..% 全表扫描，属 SQLite 预期；其余场景应走索引。")


if __name__ == "__main__":
    main()
