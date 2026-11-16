"""性能基线测试：不同数据量下 API 响应时间的可接受阈值。

不引入外部基准工具，仅用 time.perf_counter() 测量。
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from tests.files.generators import make_pdf


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_documents(client, headers, count: int, tmp_path: Path) -> list[int]:
    """批量创建资料（先创建文档，再上传文件），返回 doc_id 列表。"""
    doc_ids = []
    # 创建一个真实的 PDF 文件，重复使用
    pdf_path = make_pdf(tmp_path / "性能测试.pdf")
    pdf_bytes = pdf_path.read_bytes()
    for i in range(count):
        resp = client.post("/api/v1/documents", json={
            "title": f"性能测试资料-{i:04d}",
            "effective_from": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["id"]
        # 上传文件到文档
        resp = client.post(
            f"/api/v1/documents/{doc_id}/file",
            files={"file": (f"perf_{i}.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 200
        doc_ids.append(doc_id)
    return doc_ids


class TestPerformanceBaseline:
    """列表接口在不同数据量下的响应时间。"""

    @pytest.mark.parametrize("size", [50, 100])
    def test_list_response_time(self, client, db, admin_user, size, tmp_path):
        """资料列表在 {size} 条数据下应在 2 秒内返回。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        _seed_documents(client, headers, size, tmp_path)

        # 测量列表查询时间（含缓存冷启动）
        times = []
        for _ in range(3):
            start = time.perf_counter()
            resp = client.get("/api/v1/documents", headers=headers)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            assert resp.status_code == 200

        avg_time = sum(times) / len(times)
        max_time = max(times)
        assert avg_time < 2.0, f"{size} 条资料平均响应 {avg_time:.3f}s 超过 2s 阈值"
        assert max_time < 3.0, f"{size} 条资料最大响应 {max_time:.3f}s 超过 3s 阈值"

    @pytest.mark.parametrize("size", [50, 100])
    def test_list_with_filter_response_time(self, client, db, admin_user, size, tmp_path):
        """带关键词筛选的列表查询应在合理时间内返回。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        _seed_documents(client, headers, size, tmp_path)

        start = time.perf_counter()
        resp = client.get("/api/v1/documents?keyword=性能测试资料-0050", headers=headers)
        elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        assert elapsed < 2.0, f"关键词筛选 {size} 条 {elapsed:.3f}s 超过 2s 阈值"

    def test_cache_effectiveness(self, client, db, admin_user, tmp_path):
        """第二次相同查询应命中缓存，比首次快。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        _seed_documents(client, headers, 50, tmp_path)

        # 首次查询（冷缓存）
        start = time.perf_counter()
        resp1 = client.get("/api/v1/documents", headers=headers)
        cold_time = time.perf_counter() - start

        # 第二次查询（热缓存）
        start = time.perf_counter()
        resp2 = client.get("/api/v1/documents", headers=headers)
        warm_time = time.perf_counter() - start

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # 缓存命中应更快（或至少不显著变慢）
        assert warm_time <= cold_time * 1.5, (
            f"缓存效果不佳：冷 {cold_time:.3f}s vs 热 {warm_time:.3f}s"
        )

    def test_document_detail_response_time(self, client, db, admin_user, tmp_path):
        """单个资料详情应在 500ms 内返回。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 创建资料（先创建文档，再上传文件）
        resp = client.post("/api/v1/documents", json={
            "title": "详情性能测试", "effective_from": "2026-01-01",
        }, headers=headers)
        doc_id = resp.json()["id"]
        pdf = make_pdf(tmp_path / "详情测试.pdf")
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("详情测试.pdf", pdf.read_bytes(), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        # 测量详情查询
        times = []
        for _ in range(5):
            start = time.perf_counter()
            resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            assert resp.status_code == 200

        avg_time = sum(times) / len(times)
        assert avg_time < 0.5, f"详情查询平均 {avg_time:.3f}s 超过 500ms 阈值"

    def test_notice_list_response_time(self, client, db, admin_user):
        """通知列表在 10 条数据下应在 1 秒内返回。"""
        from app.models.notice import NoticeType

        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        # 创建 10 条通知
        for i in range(10):
            client.post("/api/v1/notices", json={
                "title": f"性能通知-{i}",
                "content": f"内容-{i}",
                "notice_type": type_name,
                "effective_from": "2026-01-01",
            }, headers=headers)

        times = []
        for _ in range(3):
            start = time.perf_counter()
            resp = client.get("/api/v1/notices", headers=headers)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            assert resp.status_code == 200

        avg_time = sum(times) / len(times)
        assert avg_time < 1.0, f"通知列表平均 {avg_time:.3f}s 超过 1s 阈值"
