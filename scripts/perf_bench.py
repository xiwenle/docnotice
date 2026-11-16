"""容量压测：1000 份资料（含附件）下的列表/详情/下载/预览响应时间。

用法：
    python scripts/perf_bench.py                 # 1000 份，临时库，跑完即删
    python scripts/perf_bench.py --docs 300      # 自定义规模
    python scripts/perf_bench.py --keep          # 保留临时库以便手工排查

为什么单独写而不是塞进 pytest：
本脚本要灌 1000 份资料（每份还有真实文件落盘），几十秒起步；
放进测试套件会把单测从秒级拖成分钟级。它属于「容量体检」，按需运行。

度量口径：
- 走**真实 HTTP 端点**（TestClient），含鉴权、序列化、SQL 全链路；
  不是直接调 service，避免测出一个线上不存在的乐观值。
- 每种请求跑 N 次取 P50/P95；首次请求单独标注（含缓存冷启动）。
- 输出与 README/HANDOFF 里的容量结论一一对应，不凭印象说话。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 必须在 import app.* 之前设置 DATA_DIR（engine 在导入期绑定配置）
_TMP = Path(tempfile.mkdtemp(prefix="fms-perf-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'data' / 'database' / 'app.db').as_posix()}"
os.environ["SECRET_KEY"] = "perf-bench-secret-key-not-for-production"
os.environ["APP_ENV"] = "test"
os.environ["INITIAL_ADMIN_USERNAME"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin1234"

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models import Document, FileRecord, User  # noqa: E402


def _seed(n_docs: int, with_attachment_ratio: float = 0.5) -> int:
    """直接建表 + 灌数据（绕过 API，灌库速度优先）。"""
    from datetime import date, timedelta

    from app.core.file_safety import FILE_MIME_TYPES

    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    settings.ensure_dirs()

    db = SessionLocal()
    try:
        admin = User(
            username="admin",
            password_hash=hash_password("admin1234"),
            display_name="压测管理员",
            role=User.ROLE_ADMIN,
        )
        db.add(admin)
        db.flush()

        # 一份真实 PDF 字节反复复用（内容不参与度量，只测 IO 与序列化）
        from pypdf import PdfWriter

        buf = io.BytesIO()
        w = PdfWriter()
        w.add_blank_page(width=595.28, height=841.89)
        w.write(buf)
        pdf_bytes = buf.getvalue()

        today = date.today()
        for i in range(n_docs):
            doc = Document(
                title=f"PERF-资料-{i:04d}",
                summary="容量压测样本",
                effective_from=today - timedelta(days=30),
                effective_to=today + timedelta(days=335),
                created_by=admin.id,
            )
            db.add(doc)
            db.flush()

            # 主文件
            main_name = f"perf{i:04d}main.pdf"
            main_path = settings.documents_dir / main_name[:2] / main_name
            main_path.parent.mkdir(parents=True, exist_ok=True)
            main_path.write_bytes(pdf_bytes)
            main = FileRecord(
                owner_type="document",
                owner_id=doc.id,
                original_filename=f"资料{i:04d}.pdf",
                internal_name=main_name,
                file_type="pdf",
                extension=".pdf",
                mime_type=FILE_MIME_TYPES["pdf"],
                size_bytes=len(pdf_bytes),
                sha256="0" * 64,
                storage_rel_path=f"documents/{main_name[:2]}/{main_name}",
                status="active",
                uploaded_by=admin.id,
            )
            db.add(main)
            db.flush()
            doc.current_file_id = main.id

            # 一半资料带附件
            if i % 2 == 0 and with_attachment_ratio:
                att_name = f"perf{i:04d}att.pdf"
                att_path = settings.documents_dir / att_name[:2] / att_name
                att_path.parent.mkdir(parents=True, exist_ok=True)
                att_path.write_bytes(pdf_bytes)
                att = FileRecord(
                    owner_type="document",
                    owner_id=doc.id,
                    original_filename=f"资料{i:04d}附件.pdf",
                    internal_name=att_name,
                    file_type="pdf",
                    extension=".pdf",
                    mime_type=FILE_MIME_TYPES["pdf"],
                    size_bytes=len(pdf_bytes),
                    sha256="1" * 64,
                    storage_rel_path=f"documents/{att_name[:2]}/{att_name}",
                    status="active",
                    uploaded_by=admin.id,
                )
                db.add(att)
                db.flush()
                doc.attachment_file_id = att.id

        db.commit()
        return admin.id
    finally:
        db.close()


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
    return ordered[idx]


def _bench(label: str, fn, rounds: int = 30) -> dict:
    # 先跑一次丢弃（缓存/连接池冷启动）
    fn()
    samples = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return {
        "label": label,
        "p50": _pct(samples, 0.50),
        "p95": _pct(samples, 0.95),
        "mean": statistics.fmean(samples),
        "n": rounds,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="容量压测（临时库，默认 1000 份资料）")
    ap.add_argument("--docs", type=int, default=1000)
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--keep", action="store_true", help="保留临时数据目录")
    args = ap.parse_args()

    print(f"数据目录：{_TMP / 'data'}")
    t0 = time.perf_counter()
    _seed(args.docs)
    print(f"灌数据完成：{args.docs} 份资料，耗时 {time.perf_counter() - t0:.1f}s\n")

    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    client = TestClient(fastapi_app)
    r = client.post("/api/v1/auth/login",
                    json={"username": "admin", "password": "admin1234"})
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    # page_size 上限 100（路由层校验），翻页找一份带附件的资料
    doc_id = client.get("/api/v1/documents?page_size=1", headers=h).json()["items"][0]["id"]
    att_doc_id = None
    for page in range(1, 6):
        items = client.get(
            f"/api/v1/documents?page={page}&page_size=100", headers=h
        ).json()["items"]
        att_doc_id = next((d["id"] for d in items if d.get("has_attachment")), None)
        if att_doc_id is not None:
            break
    if att_doc_id is None:
        print("⚠️  没找到带附件的资料，跳过「详情（含附件）」场景")
        att_doc_id = doc_id

    rows = [
        _bench("列表 20 条/页", lambda: client.get("/api/v1/documents?page_size=20", headers=h), args.rounds),
        _bench("列表 100 条/页", lambda: client.get("/api/v1/documents?page_size=100", headers=h), args.rounds),
        _bench("关键词搜索", lambda: client.get("/api/v1/documents?keyword=资料-05&page_size=20", headers=h), args.rounds),
        _bench("详情（无附件）", lambda: client.get(f"/api/v1/documents/{doc_id}", headers=h), args.rounds),
        _bench("详情（含附件）", lambda: client.get(f"/api/v1/documents/{att_doc_id}", headers=h), args.rounds),
        _bench("下载主文件", lambda: client.get(f"/api/v1/documents/{doc_id}/file", headers=h), args.rounds),
        _bench("预览 PDF", lambda: client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=h), args.rounds),
    ]

    print(f"{'场景':<18}{'P50(ms)':>10}{'P95(ms)':>10}{'均值(ms)':>11}")
    print("-" * 52)
    for row in rows:
        print(f"{row['label']:<18}{row['p50']:>10.1f}{row['p95']:>10.1f}{row['mean']:>11.1f}")
    print()

    # 阈值提醒（超过即为需要关注的信号，不代表失败）
    warn = [r for r in rows if r["p95"] > 300]
    if warn:
        print("⚠️  以下场景 P95 超过 300ms，值得进一步分析：")
        for r in warn:
            print(f"   - {r['label']}: {r['p95']:.1f}ms")
    else:
        print("✅ 全部场景 P95 < 300ms")

    if not args.keep:
        shutil.rmtree(_TMP, ignore_errors=True)
    else:
        print(f"\n临时数据保留在：{_TMP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
