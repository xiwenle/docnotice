"""资料模块端到端测试（项目规范 §1/§8/§9/§10/§12/§13/§25/§26、API.md §6）。

覆盖：CRUD、搜索筛选（关键字/分类/标签/状态）、四类型真实文件上传（中文名）、
替换（旧文件进回收站 + status=recycled + rev_count）、归档→回收站、恢复、
预览（PDF/DOCX/XLSX/XLS 解析成功）、下载原名+内容一致（RFC5987）、
归档后普通用户 403。
"""
from __future__ import annotations

from urllib.parse import quote

import pytest

from app.db.session import SessionLocal
from app.models.document import FileRecord
from files.generators import (
    DOCX_FILENAME,
    PDF_FILENAME,
    XLS_FILENAME,
    XLSX_FILENAME,
    make_docx,
    make_pdf,
    make_xls,
    make_xlsx,
    sha256_of,
)
from helpers import create_document, upload_document_file


def _upload(client, headers, doc_id, filename, content):
    resp = upload_document_file(client, headers, doc_id, filename=filename, content=content)
    assert resp.status_code == 200, f"上传失败 {resp.status_code}: {resp.text}"
    return resp.json()


def _file_record(record_id: int) -> FileRecord:
    session = SessionLocal()
    try:
        rec = session.get(FileRecord, record_id)
        session.expunge(rec)
        return rec
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
class TestDocumentCrud:
    def test_create_and_get(self, client, admin_headers):
        doc = create_document(client, admin_headers, title="员工手册 2026",
                              effective_from="2026-01-01", effective_to="2026-12-31")
        assert doc["title"] == "员工手册 2026"
        # 2026-11-03 产品决策：资料编号已彻底移除，出站对象不再含 doc_no
        assert "doc_no" not in doc
        assert doc["archived"] is False
        assert doc["rev_count"] == 0  # 首次上传文件后 +1
        assert doc["has_file"] is False

        detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
        assert detail["id"] == doc["id"]
        assert detail["category"] is None
        assert detail["current_file"] is None

    def test_update_title(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="旧标题",
                                 effective_from="2026-01-01")["id"]
        resp = client.put(f"/api/v1/documents/{doc_id}", headers=admin_headers,
                          json={"title": "新标题", "summary": "说明"})
        assert resp.status_code == 200, resp.text
        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["title"] == "新标题"
        assert detail["summary"] == "说明"

    def test_create_requires_title(self, client, admin_headers):
        resp = client.post("/api/v1/documents", headers=admin_headers,
                           json={"title": "", "effective_from": "2026-01-01"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_create_missing_effective_from(self, client, admin_headers):
        resp = client.post("/api/v1/documents", headers=admin_headers,
                           json={"title": "缺日期"})
        assert resp.status_code == 400

    def test_not_found_document(self, client, admin_headers):
        resp = client.get("/api/v1/documents/99999", headers=admin_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 搜索 / 筛选（§1、§9）
# ---------------------------------------------------------------------------
class TestDocumentSearch:
    def _setup(self, client, admin_headers):
        cat1 = client.post("/api/v1/categories", headers=admin_headers, json={"name": "规章制度"}).json()
        cat2 = client.post("/api/v1/categories", headers=admin_headers, json={"name": "财务制度"}).json()
        tag_a = client.post("/api/v1/tags", headers=admin_headers, json={"name": "薪酬"}).json()
        tag_b = client.post("/api/v1/tags", headers=admin_headers, json={"name": "考勤"}).json()
        create_document(client, admin_headers, title="薪酬管理制度",
                        category_id=cat2["id"], tag_ids=[tag_a["id"]],
                        effective_from="2026-01-01", effective_to="2026-06-30")
        create_document(client, admin_headers, title="考勤管理办法",
                        category_id=cat1["id"], tag_ids=[tag_b["id"]],
                        effective_from="2026-01-01")
        return cat1, cat2, tag_a, tag_b

    def test_keyword_title(self, client, admin_headers):
        self._setup(client, admin_headers)
        resp = client.get("/api/v1/documents?keyword=考勤", headers=admin_headers).json()
        titles = [d["title"] for d in resp["items"]]
        assert "考勤管理办法" in titles and "薪酬管理制度" not in titles

    def test_keyword_summary(self, client, admin_headers):
        """编号移除后，关键词仍匹配标题与说明（搜索面不缩水）。"""
        create_document(client, admin_headers, title="报销流程",
                        summary="差旅与招待费", effective_from="2026-01-01")
        create_document(client, admin_headers, title="考勤办法",
                        summary="打卡规则", effective_from="2026-01-01")
        resp = client.get("/api/v1/documents?keyword=差旅", headers=admin_headers).json()
        assert [d["title"] for d in resp["items"]] == ["报销流程"]
    def test_keyword_summary_after_update_is_visible_to_user(self, client, admin_headers, user_headers):
        """管理员粘贴检索摘要后，普通用户可立即按摘要关键词搜到资料。"""
        doc = create_document(client, admin_headers, title="手册",
                              summary="旧说明", effective_from="2026-01-01")
        before = client.get("/api/v1/documents?keyword=紫藤花标记", headers=user_headers).json()
        assert before["items"] == []

        resp = client.put(f"/api/v1/documents/{doc['id']}", headers=admin_headers,
                          json={"summary": "内容关键词：紫藤花标记\n适用范围：全公司"})
        assert resp.status_code == 200, resp.text
        after = client.get("/api/v1/documents?keyword=紫藤花标记", headers=user_headers).json()
        assert [d["id"] for d in after["items"]] == [doc["id"]]
        assert after["items"][0]["summary"] == "内容关键词：紫藤花标记\n适用范围：全公司"

    def test_summary_max_length_rejected(self, client, admin_headers):
        """说明 / 检索摘要后端也限制 10000 字，避免超长文本拖慢 LIKE 与详情页。"""
        resp = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "超长说明", "summary": "字" * 10001,
            "effective_from": "2026-01-01",
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_filter_category(self, client, admin_headers):
        _, cat2, _, _ = self._setup(client, admin_headers)
        resp = client.get(f"/api/v1/documents?category_id={cat2['id']}", headers=admin_headers).json()
        assert [d["title"] for d in resp["items"]] == ["薪酬管理制度"]

    def test_filter_tag(self, client, admin_headers):
        _, _, _, tag_b = self._setup(client, admin_headers)
        resp = client.get(f"/api/v1/documents?tag_id={tag_b['id']}", headers=admin_headers).json()
        assert [d["title"] for d in resp["items"]] == ["考勤管理办法"]

    def test_filter_status(self, client, admin_headers):
        self._setup(client, admin_headers)
        resp = client.get("/api/v1/documents?status=expired&as_of_date=2027-01-01",
                          headers=admin_headers).json()
        titles = [d["title"] for d in resp["items"]]
        assert titles == ["薪酬管理制度"]
        resp = client.get("/api/v1/documents?status=pending&as_of_date=2025-06-01",
                          headers=admin_headers).json()
        titles = {d["title"] for d in resp["items"]}
        assert titles == {"薪酬管理制度", "考勤管理办法"}

    def test_invalid_status_rejected(self, client, admin_headers):
        resp = client.get("/api/v1/documents?status=hacked", headers=admin_headers)
        assert resp.status_code == 400

    def test_pagination(self, client, admin_headers):
        for i in range(3):
            create_document(client, admin_headers, title=f"分页资料{i}",
                            effective_from="2026-01-01")
        page1 = client.get("/api/v1/documents?page=1&page_size=2", headers=admin_headers).json()
        page2 = client.get("/api/v1/documents?page=2&page_size=2", headers=admin_headers).json()
        assert page1["total"] == 3 and len(page1["items"]) == 2
        assert len(page2["items"]) == 1
        ids1 = [d["id"] for d in page1["items"]]
        ids2 = [d["id"] for d in page2["items"]]
        assert len(set(ids1) & set(ids2)) == 0


# ---------------------------------------------------------------------------
# 四类型真实文件上传（中文文件名，§10/§32）
# ---------------------------------------------------------------------------
class TestRealFileUpload:
    @pytest.mark.parametrize(
        "make,filename,expected_type",
        [
            (make_pdf, PDF_FILENAME, "pdf"),
            (make_docx, DOCX_FILENAME, "docx"),
            (make_xlsx, XLSX_FILENAME, "xlsx"),
            (make_xls, XLS_FILENAME, "xls"),
        ],
    )
    def test_upload_four_types(self, client, admin_headers, tmp_path, make, filename, expected_type):
        path = make(tmp_path / filename)
        data = path.read_bytes()
        doc_id = create_document(client, admin_headers, title=f"真实文件-{expected_type}",
                                 effective_from="2026-01-01")["id"]
        body = _upload(client, admin_headers, doc_id, filename=filename, content=data)
        cur = body["current_file"]
        assert cur["file_type"] == expected_type
        assert cur["original_filename"] == filename  # 中文文件名完整保留
        assert cur["size_bytes"] == len(data)
        assert cur["sha256"] == sha256_of(path)
        assert body["rev_count"] == 1
        # 存储名是服务端生成的 uuid hex + ext（§10/§11）
        from app.db.session import SessionLocal

        session = SessionLocal()
        try:
            rec = session.get(FileRecord, cur["id"])
            internal = rec.internal_name
        finally:
            session.close()
        assert internal.endswith(f".{expected_type}")
        assert not internal.startswith(filename)  # 不用原始文件名做唯一标识

    def test_upload_empty_file_rejected(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="空文件",
                                 effective_from="2026-01-01")["id"]
        resp = upload_document_file(client, admin_headers, doc_id,
                                    filename="empty.pdf", content=b"")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 替换：旧文件进回收站（§8 不覆盖不物理删、§26）
# ---------------------------------------------------------------------------
class TestFileReplace:
    def test_replace_old_file_goes_recycle_bin(self, client, admin_headers, tmp_path):
        doc_id = create_document(client, admin_headers, title="版本管理",
                                 effective_from="2026-01-01")["id"]
        v1 = make_pdf(tmp_path / "旧版.pdf").read_bytes()
        v2 = make_pdf(tmp_path / "新版.pdf").read_bytes()

        r1 = _upload(client, admin_headers, doc_id, "旧版.pdf", v1)
        assert r1["rev_count"] == 1
        r2 = _upload(client, admin_headers, doc_id, "新版.pdf", v2)
        assert r2["rev_count"] == 2
        assert r2["recycled_file_id"] == r1["current_file"]["id"]

        # 旧 file_record.status == recycled（§8）
        old_rec = _file_record(r1["current_file"]["id"])
        assert old_rec.status == "recycled"
        new_rec = _file_record(r2["current_file"]["id"])
        assert new_rec.status == "active"

        # 回收站 API 有记录（reason=replaced）
        items = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"]
        assert len(items) == 1
        assert items[0]["reason"] == "replaced"
        assert items[0]["original_filename"] == "旧版.pdf"

        # 下载返回的是新版内容（当前文件指向新文件）
        resp = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert resp.content == v2

        # 审计 replace_file
        logs = client.get("/api/v1/audit-logs?action=replace_file", headers=admin_headers).json()
        assert logs["total"] == 2  # 两次上传都记录 replace_file


# ---------------------------------------------------------------------------
# 预览：四类型解析成功（§12）
# ---------------------------------------------------------------------------
class TestPreview:
    def test_preview_pdf_inline(self, client, admin_headers, tmp_path):
        pdf = make_pdf(tmp_path / PDF_FILENAME)
        doc_id = create_document(client, admin_headers, title="PDF预览",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, PDF_FILENAME, pdf.read_bytes())
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert "inline" in resp.headers.get("content-disposition", "")
        assert resp.content == pdf.read_bytes()  # 预览即原文件（不加工）

    def test_preview_docx_extracts_chinese(self, client, admin_headers, tmp_path):
        docx = make_docx(tmp_path / DOCX_FILENAME)
        doc_id = create_document(client, admin_headers, title="DOCX预览",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, DOCX_FILENAME, docx.read_bytes())
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/docx", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content_type"] == "text"
        assert "本制度适用于公司全体员工" in body["text"]
        assert "[表]" in body["text"]  # 表格转文本一并提取
        assert body["truncated"] is False
        # 结构化 blocks：标题 + 段落 + 表格（前端按 DOM 渲染，不接收 HTML）
        assert body["blocks"][0]["type"] == "heading"
        assert body["blocks"][0]["level"] == 1
        assert body["blocks"][0]["text"] == "公司制度"
        table_block = next(b for b in body["blocks"] if b["type"] == "table")
        assert table_block["rows"][0][0] == "单元格1-1"

    def test_preview_docx_structured_runs_lists_tables(self, client, admin_headers, tmp_path):
        from docx import Document

        path = tmp_path / "结构化预览.docx"
        doc = Document()
        doc.add_heading("第一章 总则", level=1)
        paragraph = doc.add_paragraph()
        bold_run = paragraph.add_run("加粗条款")
        bold_run.bold = True
        paragraph.add_run("：普通文本")
        doc.add_paragraph("列表一", style="List Bullet")
        doc.add_paragraph("列表二", style="List Bullet")
        doc.add_paragraph("编号一", style="List Number")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "姓名"
        table.cell(0, 1).text = "部门"
        table.cell(1, 0).text = "张三"
        table.cell(1, 1).text = "研发"
        doc.save(path)

        doc_id = create_document(client, admin_headers, title="结构化DOCX",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, path.name, path.read_bytes())
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/docx", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        blocks = resp.json()["blocks"]

        assert blocks[0]["type"] == "heading"
        assert blocks[0]["level"] == 1
        para = next(b for b in blocks if b["type"] == "paragraph")
        assert para["runs"][0]["text"] == "加粗条款"
        assert para["runs"][0].get("bold") is True
        assert para["runs"][1]["text"] == "：普通文本"

        lists = [b for b in blocks if b["type"] == "list"]
        assert lists[0]["ordered"] is False
        assert [item["text"] for item in lists[0]["items"]] == ["列表一", "列表二"]
        assert lists[1]["ordered"] is True
        assert [item["text"] for item in lists[1]["items"]] == ["编号一"]

        table_block = next(b for b in blocks if b["type"] == "table")
        assert table_block["rows"] == [["姓名", "部门"], ["张三", "研发"]]

    def test_preview_docx_truncates_blocks(self, client, admin_headers, tmp_path, monkeypatch):
        from docx import Document
        from app.services import preview_service

        monkeypatch.setattr(preview_service.settings, "PREVIEW_DOCX_MAX_PARAS", 2)
        path = tmp_path / "截断预览.docx"
        doc = Document()
        for i in range(5):
            doc.add_paragraph(f"段落{i}")
        doc.save(path)

        doc_id = create_document(client, admin_headers, title="截断DOCX",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, path.name, path.read_bytes())
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/docx", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["truncated"] is True
        assert len(body["blocks"]) == 2

    def test_preview_xlsx_table(self, client, admin_headers, tmp_path):
        xlsx = make_xlsx(tmp_path / XLSX_FILENAME, sheet_names=["费用明细", "汇总"], rows=4)
        doc_id = create_document(client, admin_headers, title="XLSX预览",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, XLSX_FILENAME, xlsx.read_bytes())
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/xlsx", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content_type"] == "table"
        assert body["sheet_names"] == ["费用明细", "汇总"]
        assert body["headers"] == ["数据1-1", "数据1-2", "数据1-3", "数据1-4"]
        assert len(body["rows"]) == 3
        assert "数据3-2" in body["rows"][1]

    def test_preview_xls_table(self, client, admin_headers, tmp_path):
        xls = make_xls(tmp_path / XLS_FILENAME, sheet_names=["工资表"], rows=3, cols=2)
        doc_id = create_document(client, admin_headers, title="XLS预览",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, XLS_FILENAME, xls.read_bytes())
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/xlsx", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content_type"] == "table"
        assert body["sheet_names"] == ["工资表"]
        # 契约：首行为表头，total_rows 计数据行数（3 行表 = 1 表头 + 2 数据）
        assert body["headers"] == ["数据1-1", "数据1-2"]
        assert len(body["rows"]) == 2
        assert body["rows"][0] == ["数据2-1", "数据2-2"]
        assert body["total_rows"] == 2

    def test_preview_wrong_type_rejected(self, client, admin_headers, tmp_path):
        pdf = make_pdf(tmp_path / PDF_FILENAME)
        doc_id = create_document(client, admin_headers, title="类型不符",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, PDF_FILENAME, pdf.read_bytes())
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/docx", headers=admin_headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# 下载：原名 + 内容一致 + RFC5987 中文文件名（§12、§33）
# ---------------------------------------------------------------------------
class TestDownload:
    def test_download_original_bytes_and_rfc5987(self, client, admin_headers, tmp_path):
        docx = make_docx(tmp_path / DOCX_FILENAME)
        data = docx.read_bytes()
        doc_id = create_document(client, admin_headers, title="下载测试",
                                 effective_from="2026-01-01")["id"]
        _upload(client, admin_headers, doc_id, DOCX_FILENAME, data)

        resp = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.content == data  # 下载内容与原始文件逐字节一致
        cd = resp.headers.get("content-disposition", "")
        assert cd.startswith("attachment")
        assert "filename*=utf-8''" in cd  # RFC5987
        assert quote(DOCX_FILENAME) in cd  # 中文文件名百分号编码

    def test_download_without_file_404(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="无文件",
                                 effective_from="2026-01-01")["id"]
        resp = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 归档 / 恢复 + 普通用户可见性（§26、§33）
# ---------------------------------------------------------------------------
class TestArchiveRestore:
    def test_archive_and_restore_flow(self, client, admin_headers, user_headers, tmp_path, data_dir):
        pdf = make_pdf(tmp_path / PDF_FILENAME)
        data = pdf.read_bytes()
        doc_id = create_document(client, admin_headers, title="归档资料",
                                 effective_from="2026-01-01")["id"]
        body = _upload(client, admin_headers, doc_id, PDF_FILENAME, data)
        internal = _file_record(body["current_file"]["id"]).internal_name
        # 分片布局：documents/<前2位>/<内部名>
        assert (data_dir / "documents" / internal[:2] / internal).is_file()

        # 归档（软删，§26）
        resp = client.request("DELETE", f"/api/v1/documents/{doc_id}",
                              headers=admin_headers, json={"reason": "不再使用"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "archived"

        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["archived"] is True
        assert (data_dir / "recycle_bin" / internal).is_file(), "文件应移入回收站"

        # 回收站条目 reason=archived
        items = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"]
        assert len(items) == 1 and items[0]["reason"] == "archived"

        # 普通用户不可见：列表不含、详情 403、下载 403、预览 403
        listed = client.get("/api/v1/documents", headers=user_headers).json()
        assert all(d["id"] != doc_id for d in listed["items"])
        assert client.get(f"/api/v1/documents/{doc_id}", headers=user_headers).status_code == 403
        assert client.get(f"/api/v1/documents/{doc_id}/file", headers=user_headers).status_code == 403
        assert client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=user_headers).status_code == 403

        # 普通用户 admin 过滤不出归档（status=archived 仅 admin 可见）
        listed = client.get("/api/v1/documents?status=archived", headers=user_headers).json()
        assert all(d["id"] != doc_id for d in listed["items"])

        # 已归档资料不能修改/上传（保护规则）
        assert client.put(f"/api/v1/documents/{doc_id}", headers=admin_headers,
                          json={"title": "改归档"}).status_code == 409
        assert upload_document_file(client, admin_headers, doc_id,
                                    filename="x.pdf", content=b"%PDF-1.4 x").status_code == 409

        # 恢复
        resp = client.post(f"/api/v1/documents/{doc_id}/restore", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["archived"] is False
        assert (data_dir / "documents" / internal[:2] / internal).is_file(), "恢复后文件回 documents/"
        # 普通用户重新可见
        assert client.get(f"/api/v1/documents/{doc_id}", headers=user_headers).status_code == 200

    def test_restore_non_archived_conflict(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="未归档",
                                 effective_from="2026-01-01")["id"]
        resp = client.post(f"/api/v1/documents/{doc_id}/restore", headers=admin_headers)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONFLICT"

    def test_archive_missing_document_404(self, client, admin_headers):
        resp = client.request("DELETE", "/api/v1/documents/99999", headers=admin_headers)
        assert resp.status_code == 404

# ---------------------------------------------------------------------------
# 附件生命周期（2026-11-06）：归档一条记录、一起恢复、一起 purge
# ---------------------------------------------------------------------------
class TestAttachmentLifecycle:
    def _doc_with_both(self, client, headers, tmp_path, title="主文件+附件"):
        from files.generators import DOCX_FILENAME, PDF_FILENAME, make_docx, make_pdf

        doc_id = create_document(client, headers, title=title,
                                 effective_from="2026-01-01")["id"]
        pdf = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        r = upload_document_file(client, headers, doc_id,
                                 filename=PDF_FILENAME, content=pdf)
        assert r.status_code == 200, r.text
        docx = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
        r = client.post(
            f"/api/v1/documents/{doc_id}/attachment",
            files={"file": (DOCX_FILENAME, docx, "application/octet-stream")},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        return doc_id, pdf, docx

    def test_archive_creates_single_entry_with_both_files(
        self, client, admin_headers, tmp_path
    ):
        doc_id, _pdf, _docx = self._doc_with_both(client, admin_headers, tmp_path)

        assert client.request(
            "DELETE", f"/api/v1/documents/{doc_id}",
            headers=admin_headers, json={"reason": "测试"},
        ).status_code == 200

        items = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"]
        # 关键：主文件 + 附件**只产生一条**记录
        assert len(items) == 1, f"应只有一条回收站记录，实际 {len(items)}"
        e = items[0]
        assert e["reason"] == "archived"
        assert e["file_record_id"] is not None
        assert e["attachment_file_record_id"] is not None
        assert e["attachment_filename"] is not None

    def test_restore_brings_back_both_files(self, client, admin_headers, tmp_path):
        doc_id, pdf, docx = self._doc_with_both(client, admin_headers, tmp_path)
        client.request("DELETE", f"/api/v1/documents/{doc_id}",
                       headers=admin_headers, json={"reason": "测试"})
        assert client.post(
            f"/api/v1/documents/{doc_id}/restore", headers=admin_headers
        ).status_code == 200

        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["current_file"]["original_filename"].endswith(".pdf")
        assert detail["attachment_file"] is not None
        # 两份文件都能真的下回来
        assert client.get(
            f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
        ).content == docx
        assert client.get(
            f"/api/v1/documents/{doc_id}/file", headers=admin_headers
        ).content == docx  # 有附件时 /file 给附件

    def test_purge_removes_both_physical_files(self, client, admin_headers, tmp_path):
        doc_id, _pdf, _docx = self._doc_with_both(client, admin_headers, tmp_path)
        client.request("DELETE", f"/api/v1/documents/{doc_id}",
                       headers=admin_headers, json={"reason": "测试"})

        items = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"]
        entry_id = items[0]["id"]
        resp = client.request(
            "DELETE", f"/api/v1/recycle-bin/{entry_id}",
            headers=admin_headers, json={"confirm": True},
        )
        assert resp.status_code == 200, resp.text

        from app.db.session import SessionLocal
        from app.models.document import Document, FileRecord
        from pathlib import Path

        db = SessionLocal()
        try:
            doc = db.get(Document, doc_id)
            ids = [i for i in (doc.current_file_id, doc.attachment_file_id) if i]
            paths = [db.get(FileRecord, i).storage_rel_path for i in ids]
        finally:
            db.close()
        from app.core.config import get_settings

        root = get_settings().data_dir
        for rel in paths:
            assert not (Path(root) / rel).exists(), f"文件未物理删除: {rel}"

    def test_restore_attachment_deleted_reattaches_without_changing_document_state(
        self, client, admin_headers, tmp_path
    ):
        """恢复被单独删除的附件：重新挂回资料，资料归档状态不受影响。"""
        doc_id, _pdf, docx = self._doc_with_both(
            client, admin_headers, tmp_path, title="删附件再恢复"
        )
        assert client.delete(
            f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
        ).status_code == 200

        items = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"]
        entry = next(i for i in items if i["reason"] == "attachment_deleted")
        assert client.post(
            f"/api/v1/recycle-bin/{entry['id']}/restore", headers=admin_headers
        ).status_code == 200

        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["archived"] is False, "恢复附件不得把资料状态改成其他"
        # 删除的附件恢复后应重新挂回资料；replaced 旧附件仍不顶替当前附件
        assert detail["attachment_file"] is not None
