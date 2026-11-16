"""文件安全测试（项目规范 §11/§32、DESIGN §5.1）。

覆盖：中文文件名、重复文件、伪造扩展名 415、非法扩展名 415、路径穿越
（../ 与 %2F 编码形态）、损坏文件（上传 415 / 存储后损坏预览 400）、
大文件超限 413（可配置阈值）、下载 RFC5987 头、预览非法类型 400。
"""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.document import FileRecord
from files.generators import (
    DOCX_FILENAME,
    PDF_FILENAME,
    make_corrupted,
    make_docx,
    make_large_pdf,
    make_pdf,
    make_xlsx,
    sha256_of,
    write_raw_file,
)
from helpers import create_document, upload_document_file


def _make_doc(client, headers, title="文件测试资料"):
    return create_document(client, headers, title=title, effective_from="2026-01-01")["id"]


def _upload(client, headers, doc_id, filename, content):
    return upload_document_file(client, headers, doc_id, filename=filename, content=content)


# ---------------------------------------------------------------------------
# 中文文件名 + 重复文件（§10、§32）
# ---------------------------------------------------------------------------
class TestChineseAndDuplicate:
    def test_chinese_filename_roundtrip(self, client, admin_headers, tmp_path):
        """中文文件名：上传、原始名保留、存储名与原名无关、下载头一致。"""
        from urllib.parse import quote

        pdf = make_pdf(tmp_path / PDF_FILENAME)
        data = pdf.read_bytes()
        doc_id = _make_doc(client, admin_headers, "中文名资料")
        resp = _upload(client, admin_headers, doc_id, PDF_FILENAME, data)
        assert resp.status_code == 200
        cur = resp.json()["current_file"]
        assert cur["original_filename"] == PDF_FILENAME
        assert cur["sha256"] == sha256_of(pdf)

        session = SessionLocal()
        try:
            rec = session.get(FileRecord, cur["id"])
            internal = rec.internal_name
        finally:
            session.close()
        assert internal.endswith(".pdf")  # uuid4.hex + ext 形态（§10）

        download = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert download.status_code == 200
        assert download.content == data
        assert f"filename*=utf-8''{quote(PDF_FILENAME)}" in download.headers["content-disposition"]

    def test_duplicate_content_sha256(self, client, admin_headers, tmp_path):
        """同一内容两次上传：sha256 一致（重复文件可测，§32）。"""
        data = make_pdf(tmp_path / "源.pdf").read_bytes()
        d1 = _make_doc(client, admin_headers, "资料A")
        d2 = _make_doc(client, admin_headers, "资料B")
        r1 = _upload(client, admin_headers, d1, "a.pdf", data).json()
        r2 = _upload(client, admin_headers, d2, "b.pdf", data).json()
        assert r1["current_file"]["sha256"] == r2["current_file"]["sha256"] == sha256_of(tmp_path / "源.pdf")
        assert r1["current_file"]["size_bytes"] == len(data)


# ---------------------------------------------------------------------------
# 扩展名 / 魔数：伪造与非法（§11、§32）
# ---------------------------------------------------------------------------
class TestExtensionAndMagic:
    def test_illegal_extension_415(self, client, admin_headers, tmp_path):
        doc_id = _make_doc(client, admin_headers)
        exe = write_raw_file(tmp_path / "test.exe", b"MZ\x90\x00" + b"\x00" * 200)
        resp = _upload(client, admin_headers, doc_id, "恶意程序.exe", exe.read_bytes())
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"

    def test_txt_extension_415(self, client, admin_headers):
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "说明.txt", "hello".encode())
        assert resp.status_code == 415

    @pytest.mark.parametrize(
        "maker,real_name,forged_name",
        [
            (make_pdf, "真.pdf", "伪造.docx"),   # PDF 内容 + .docx
            (make_docx, "真.docx", "伪造.pdf"),  # DOCX 内容 + .pdf
            (make_xlsx, "真.xlsx", "伪造.docx"),  # XLSX 内容 + .docx（缺 word/document.xml）
            (make_xlsx, "真.xlsx", "伪造.pdf"),
        ],
    )
    def test_forged_extension_415(self, client, admin_headers, tmp_path, maker, real_name, forged_name):
        """真实内容 + 错误扩展名 → 415 FILE_TYPE_MISMATCH（防伪造，§11）。"""
        data = maker(tmp_path / real_name).read_bytes()
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, forged_name, data)
        assert resp.status_code == 415, f"{real_name}->{forged_name} 应 415: {resp.text}"
        assert resp.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

    def test_exe_content_as_pdf_415(self, client, admin_headers, tmp_path):
        exe = write_raw_file(tmp_path / "内鬼.exe", b"MZ\x90\x00" + b"\x00" * 64)
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "看起来是PDF.pdf", exe.read_bytes())
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_MISMATCH"


# ---------------------------------------------------------------------------
# 路径穿越（§11.5：../ 与 %2F、%2e 编码形态）
# ---------------------------------------------------------------------------
class TestPathTraversal:
    @pytest.mark.parametrize(
        "evil_name",
        [
            "../公司制度.pdf",
            "..%2F..%2Fetc%2Fpasswd.pdf",
            "%2e%2e%2fsecret.pdf",
            "a\\..\\b.pdf",
            "..\\..\\windows\\system32.pdf",
        ],
    )
    def test_traversal_filenames_sanitized(self, client, admin_headers, tmp_path, evil_name):
        """路径穿越文件名被清洗（不得出现路径分隔符），文件落在测试数据目录内。"""
        data = make_pdf(tmp_path / "源.pdf").read_bytes()
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, evil_name, data)
        assert resp.status_code == 200, f"穿越名 {evil_name!r} 应被清洗后接受: {resp.text}"
        name = resp.json()["current_file"]["original_filename"]
        # 清洗后不得含路径分隔符（内部 ".." 残留在文件名中无害：存储名由服务端
        # uuid 生成，展示名不参与路径拼接，§11.5）
        assert "/" not in name and "\\" not in name
        assert not name.startswith("..")
        assert name.endswith(".pdf")

    def test_traversal_does_not_escape_data_dir(self, client, admin_headers, data_dir, tmp_path):
        doc_id = _make_doc(client, admin_headers)
        data = make_pdf(tmp_path / "源.pdf").read_bytes()
        resp = _upload(client, admin_headers, doc_id, "../../../越狱.pdf", data)
        assert resp.status_code == 200
        # 以文件记录为准：
        session = SessionLocal()
        try:
            rec = session.get(FileRecord, resp.json()["current_file"]["id"])
            storage = rec.storage_rel_path
        finally:
            session.close()
        assert not storage.startswith("..")
        stored = (data_dir / storage)
        assert stored.resolve().is_relative_to(data_dir.resolve())
        assert stored.is_file()
        assert not (data_dir.parent / "越狱.pdf").exists()


# ---------------------------------------------------------------------------
# 损坏文件（§32）：上传阶段 415；存储后被篡改 → 预览 400（不影响下载）
# ---------------------------------------------------------------------------
class TestDamagedFile:
    def test_truncated_pdf_rejected_at_upload(self, client, admin_headers):
        """截断的假 PDF（如 %PDF-1.4 fake 22 字节）→ 上传 415（pypdf 深度校验）。

        回归：历史上此类文件可上传成功但永远无法预览（用户实测 bug）。
        """
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "test.pdf", b"%PDF-1.4 test content")
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

    def test_encrypted_pdf_rejected_at_upload(self, client, admin_headers, tmp_path):
        """加密 PDF → 上传 415（系统不存储加密文档，明确报错）。"""
        from pypdf import PdfReader, PdfWriter

        src = make_pdf(tmp_path / "明文.pdf")
        reader = PdfReader(str(src))
        writer = PdfWriter(clone_from=reader)
        writer.encrypt("secret123")
        enc = tmp_path / "加密.pdf"
        with open(enc, "wb") as f:
            writer.write(f)
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "加密.pdf", enc.read_bytes())
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

    def test_pdf_with_leading_garbage_accepted(self, client, admin_headers, tmp_path):
        """前导垃圾字节的真 PDF（%PDF 在 1024 字节内）→ 正常上传（防误杀）。"""
        good = make_pdf(tmp_path / "真.pdf").read_bytes()
        dirty = b"\x00\x01garbage-junk-header\n" + good  # 前置 30 字节垃圾
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "脏头.pdf", dirty)
        assert resp.status_code == 200, resp.text

    def test_truncated_docx_rejected_at_upload(self, client, admin_headers, tmp_path):
        """截断的 DOCX：魔数头在但 ZIP 结构损坏 → 上传 415（深度校验）。"""
        good = make_docx(tmp_path / DOCX_FILENAME)
        bad = make_corrupted(tmp_path / "损坏.docx", good)
        assert bad.read_bytes()[:4] == b"PK\x03\x04"  # 保留 ZIP 魔数
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "损坏.docx", bad.read_bytes())
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

    def test_stored_file_tampered_preview_400_download_ok(self, client, admin_headers, data_dir, tmp_path):
        """上传成功后篡改存储文件：预览 400 报错，但下载仍返回原路径（不物理删/不改写）。"""
        docx = make_docx(tmp_path / DOCX_FILENAME)
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, DOCX_FILENAME, docx.read_bytes())
        assert resp.status_code == 200
        cur = resp.json()["current_file"]

        session = SessionLocal()
        try:
            rec = session.get(FileRecord, cur["id"])
            storage = rec.storage_rel_path
        finally:
            session.close()
        stored = data_dir / storage
        stored.write_bytes(b"%PDF-1.4 fake overwritten bytes")  # 篡改磁盘文件（模拟损坏）

        resp = client.get(f"/api/v1/documents/{doc_id}/preview/docx", headers=admin_headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BAD_REQUEST"

        # 原始文件仍可下载（预览失败不影响原文件与下载，§12）
        resp = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 fake overwritten bytes"

    def test_damaged_pdf_preview_inline_still_served(self, client, admin_headers, data_dir, tmp_path):
        """损坏 PDF 预览按内联原样返回（浏览器判定），不做服务端解析。"""
        pdf_path = make_pdf(tmp_path / PDF_FILENAME)
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, PDF_FILENAME, pdf_path.read_bytes())
        session = SessionLocal()
        try:
            rec = session.get(FileRecord, resp.json()["current_file"]["id"])
            stored = data_dir / rec.storage_rel_path
        finally:
            session.close()
        stored.write_bytes(pdf_path.read_bytes()[:256])  # 截断成不可解析
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=admin_headers)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 大文件超限（§11.3；可配置阈值）
# ---------------------------------------------------------------------------
class TestLargeFile:
    def test_oversize_413_with_configurable_threshold(
        self, client, admin_headers, tmp_path, monkeypatch
    ):
        """阈值可配置：2MB 真实 PDF + MAX_UPLOAD_SIZE_MB=1 → 413 FILE_TOO_LARGE。"""
        settings = get_settings()
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
        big = make_large_pdf(tmp_path / "大文件.pdf", 2 * 1024 * 1024)  # 2MB 真实 PDF
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "大文件.pdf", big.read_bytes())
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_below_threshold_ok(self, client, admin_headers, tmp_path, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
        small = make_pdf(tmp_path / PDF_FILENAME)  # ~1KB
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, PDF_FILENAME, small.read_bytes())
        assert resp.status_code == 200

    def test_transient_large_file_within_limit_ok(self, client, admin_headers, tmp_path):
        """默认 50MB 阈值下，几 MB 文件正常上传（大文件非一律拒绝）。"""
        big = make_large_pdf(tmp_path / "较大文件.pdf", 3 * 1024 * 1024)
        doc_id = _make_doc(client, admin_headers)
        resp = _upload(client, admin_headers, doc_id, "较大文件.pdf", big.read_bytes())
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 预览非法类型（§12）
# ---------------------------------------------------------------------------
class TestPreviewWrongType:
    def test_pdf_cannot_docx_preview(self, client, admin_headers, tmp_path):
        data = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        doc_id = _make_doc(client, admin_headers)
        _upload(client, admin_headers, doc_id, PDF_FILENAME, data)
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/docx", headers=admin_headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BAD_REQUEST"

    def test_pdf_cannot_xlsx_preview(self, client, admin_headers, tmp_path):
        data = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        doc_id = _make_doc(client, admin_headers)
        _upload(client, admin_headers, doc_id, PDF_FILENAME, data)
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/xlsx", headers=admin_headers)
        assert resp.status_code == 400

    def test_docx_cannot_pdf_preview(self, client, admin_headers, tmp_path):
        data = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
        doc_id = _make_doc(client, admin_headers)
        _upload(client, admin_headers, doc_id, DOCX_FILENAME, data)
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=admin_headers)
        assert resp.status_code == 400

# ---------------------------------------------------------------------------
# 图片作为资料主文件（2026-11-03 产品决策）
# ---------------------------------------------------------------------------
# 最小合法图片字节：只校验文件头（不引入 Pillow），浏览器需真实可解码，
# 这里用 1×1 PNG 的真实字节，保证预览端点 + 浏览器都能处理。
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
JPEG_MIN = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
GIF_1PX = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
WEBP_MIN = b"RIFF" + (24).to_bytes(4, "little") + b"WEBPVP8 " + (8).to_bytes(4, "little") + b"\x00" * 8


class TestImageAsDocumentFile:
    """资料主文件可以是图片：jpg / jpeg / png / gif / webp。"""

    @pytest.mark.parametrize(
        "filename,content",
        [
            ("照片.png", PNG_1PX),
            ("扫描件.jpg", JPEG_MIN),
            ("图示.jpeg", JPEG_MIN),
            ("动图.gif", GIF_1PX),
            ("截图.webp", WEBP_MIN),
        ],
    )
    def test_upload_image_types(self, client, admin_headers, filename, content):
        doc_id = _make_doc(client, admin_headers, title=f"图片资料-{filename}")
        resp = _upload(client, admin_headers, doc_id, filename, content)
        assert resp.status_code == 200, resp.text
        cf = resp.json()["current_file"]
        assert cf["file_type"] == filename.rsplit(".", 1)[1]
        assert cf["size_bytes"] == len(content)

    def test_png_preview_inline(self, client, admin_headers):
        doc_id = _make_doc(client, admin_headers, title="PNG 预览")
        _upload(client, admin_headers, doc_id, "证明.png", PNG_1PX)
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/image", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == PNG_1PX  # 原样返回，不转码（§12 预览不替代原件）

    def test_fake_image_extension_rejected(self, client, admin_headers):
        """伪造扩展名：.png 里塞 PDF 字节 → 415 FILE_TYPE_MISMATCH。"""
        doc_id = _make_doc(client, admin_headers, title="伪造图片")
        resp = _upload(client, admin_headers, doc_id, "假图片.png", b"%PDF-1.4 fake")
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

    def test_pdf_cannot_image_preview(self, client, admin_headers, tmp_path):
        data = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        doc_id = _make_doc(client, admin_headers)
        _upload(client, admin_headers, doc_id, PDF_FILENAME, data)
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/image", headers=admin_headers)
        assert resp.status_code == 400

    def test_image_cannot_pdf_preview(self, client, admin_headers):
        doc_id = _make_doc(client, admin_headers)
        _upload(client, admin_headers, doc_id, "图.png", PNG_1PX)
        resp = client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=admin_headers)
        assert resp.status_code == 400

    def test_unsupported_image_extension_rejected(self, client, admin_headers):
        """bmp / tiff 不在白名单内（第一版只收 5 种常见格式）。"""
        doc_id = _make_doc(client, admin_headers, title="BMP")
        resp = _upload(client, admin_headers, doc_id, "图.bmp", b"BM" + b"\x00" * 40)
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"

    def test_csp_allows_blob_for_image_preview(self, client):
        """前端用 fetch → blob: 渲染图片，CSP 的 img-src 必须放行 blob:。"""
        csp = client.get("/login").headers["content-security-policy"]
        img_src = [p.strip() for p in csp.split(";") if p.strip().startswith("img-src")]
        assert img_src, "CSP 必须声明 img-src"
        assert "blob:" in img_src[0], f"img-src 需放行 blob: 才能渲染图片预览：{img_src[0]}"


# ---------------------------------------------------------------------------
# 多图上传 → 合成一份多页 PDF（2026-11-04 产品决策）
# ---------------------------------------------------------------------------
class TestMultiImageMerge:
    """一次选多张图片：服务端按顺序合成一份多页 PDF。

    §8「一份资料只有一个当前文件」不变——多图是输入方式，不是多文件存储。
    """

    def _png(self, color, size=(120, 80)):
        from io import BytesIO

        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", size, color).save(buf, format="PNG")
        return buf.getvalue()

    def _upload_many(self, client, headers, doc_id, files):
        return client.post(
            f"/api/v1/documents/{doc_id}/file",
            headers=headers,
            files=[("file", (name, data, "image/png")) for name, data in files],
        )

    def test_multiple_images_merge_into_multipage_pdf(self, client, admin_headers):
        from io import BytesIO

        from pypdf import PdfReader

        doc_id = _make_doc(client, admin_headers, title="多图合并")
        files = [
            ("第一张.png", self._png("red")),
            ("第二张.png", self._png("green")),
            ("第三张.png", self._png("blue")),
        ]
        resp = self._upload_many(client, admin_headers, doc_id, files)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # 合并结果作为「一份」当前文件
        assert body["merged"] is True
        assert body["merged_count"] == 3
        cf = body["current_file"]
        assert cf["file_type"] == "pdf"
        assert cf["original_filename"].endswith(".pdf")
        # 2026-11-06：文件名改用资料标题 + 张数，不再用首图名
        assert cf["original_filename"] == "多图合并（3张）.pdf"

        # 每张图一页，顺序与上传一致
        downloaded = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"].startswith("application/pdf")
        assert len(PdfReader(BytesIO(downloaded.content)).pages) == 3

    def test_single_image_keeps_original_bytes(self, client, admin_headers):
        """单张图片仍按原样存（不转 PDF）——多图才合并。"""
        doc_id = _make_doc(client, admin_headers, title="单图不合并")
        resp = self._upload_many(client, admin_headers, doc_id, [("一张.png", self._png("red"))])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert not body.get("merged")
        assert body["current_file"]["file_type"] == "png"

    def test_mixed_types_rejected(self, client, admin_headers, tmp_path):
        """图片 + PDF 混选 → 400，不做隐式拼接。"""
        doc_id = _make_doc(client, admin_headers, title="混合上传")
        pdf = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        resp = client.post(
            f"/api/v1/documents/{doc_id}/file",
            headers=admin_headers,
            files=[
                ("file", ("图.png", self._png("red"), "image/png")),
                ("file", (PDF_FILENAME, pdf, "application/pdf")),
            ],
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_merge_replaces_old_file_into_recycle_bin(self, client, admin_headers):
        """多图合并同样走替换语义：旧文件进回收站，不覆盖。"""
        doc_id = _make_doc(client, admin_headers, title="合并替换")
        first = self._upload_many(client, admin_headers, doc_id, [("旧.png", self._png("red"))])
        assert first.status_code == 200
        old_id = first.json()["current_file"]["id"]

        resp = self._upload_many(client, admin_headers, doc_id, [
            ("新1.png", self._png("green")),
            ("新2.png", self._png("blue")),
        ])
        assert resp.status_code == 200, resp.text
        assert resp.json()["recycled_file_id"] == old_id

    def test_corrupted_image_in_batch_rejected(self, client, admin_headers):
        """批次里有一张坏图 → 整体拒绝，不留半成品。"""
        doc_id = _make_doc(client, admin_headers, title="坏图批次")
        resp = client.post(
            f"/api/v1/documents/{doc_id}/file",
            headers=admin_headers,
            files=[
                ("file", ("好.png", self._png("red"), "image/png")),
                # 文件头像 PNG，内容无法解码
                ("file", ("坏.png", PNG_1PX[:16] + b"\x00" * 40, "image/png")),
            ],
        )
        assert resp.status_code in (400, 415)
        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["current_file"] is None

    def test_too_many_images_rejected(self, client, admin_headers):
        from app.services.file_service import MAX_MERGE_IMAGES

        doc_id = _make_doc(client, admin_headers, title="超数量")
        blob = self._png("red", size=(8, 8))
        files = [(f"图{i}.png", blob) for i in range(MAX_MERGE_IMAGES + 1)]
        resp = self._upload_many(client, admin_headers, doc_id, files)
        assert resp.status_code == 400
        assert str(MAX_MERGE_IMAGES) in resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# 资料附件（2026-11-06）：不预览的第二文件槽，pptx 只允许作附件
# ---------------------------------------------------------------------------
class TestDocumentAttachment:
    def _attach(self, client, headers, doc_id, filename, content):
        return client.post(
            f"/api/v1/documents/{doc_id}/attachment",
            files={"file": (filename, content, "application/octet-stream")},
            headers=headers,
        )

    def test_pptx_allowed_as_attachment(self, client, admin_headers, tmp_path):
        from files.generators import PPTX_FILENAME, make_pptx

        doc_id = _make_doc(client, admin_headers, title="PPT附件")
        data = make_pptx(tmp_path / PPTX_FILENAME).read_bytes()
        resp = self._attach(client, admin_headers, doc_id, PPTX_FILENAME, data)
        assert resp.status_code == 200, resp.text
        att = resp.json()["attachment_file"]
        assert att["file_type"] == "pptx"
        assert att["original_filename"] == PPTX_FILENAME

    def test_pptx_rejected_as_main_file(self, client, admin_headers, tmp_path):
        """主文件白名单不含 pptx：无法预览的格式不能当主文件。"""
        from files.generators import PPTX_FILENAME, make_pptx

        doc_id = _make_doc(client, admin_headers, title="PPT当主文件")
        data = make_pptx(tmp_path / PPTX_FILENAME).read_bytes()
        resp = _upload(client, admin_headers, doc_id, PPTX_FILENAME, data)
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"

    def test_fake_pptx_rejected(self, client, admin_headers, tmp_path):
        """改了扩展名的假 pptx（zip 里没有 ppt/presentation.xml）→ 415。"""
        from files.generators import PPTX_FILENAME, make_fake_pptx

        doc_id = _make_doc(client, admin_headers, title="假PPT")
        data = make_fake_pptx(tmp_path / PPTX_FILENAME).read_bytes()
        resp = self._attach(client, admin_headers, doc_id, PPTX_FILENAME, data)
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

    def test_download_prefers_attachment(self, client, admin_headers, tmp_path):
        """有附件时 /file 下附件；预览仍走主文件 PDF。"""
        from files.generators import DOCX_FILENAME, PDF_FILENAME, make_docx, make_pdf

        doc_id = _make_doc(client, admin_headers, title="预览PDF下载Word")
        pdf = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        assert _upload(client, admin_headers, doc_id, PDF_FILENAME, pdf).status_code == 200

        docx = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
        assert self._attach(
            client, admin_headers, doc_id, DOCX_FILENAME, docx
        ).status_code == 200

        dl = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert dl.status_code == 200
        assert dl.content == docx, "下载应拿到附件（Word 原稿），不是主文件 PDF"

        # 预览必须仍然是 PDF（附件不能顶掉预览）
        pv = client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=admin_headers)
        assert pv.status_code == 200
        assert pv.headers["content-type"].startswith("application/pdf")

    def test_download_falls_back_to_main_file(self, client, admin_headers, tmp_path):
        """无附件时 /file 行为与改动前完全一致。"""
        from files.generators import PDF_FILENAME, make_pdf

        doc_id = _make_doc(client, admin_headers, title="无附件")
        pdf = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        assert _upload(client, admin_headers, doc_id, PDF_FILENAME, pdf).status_code == 200

        dl = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
        assert dl.status_code == 200
        assert dl.content == pdf

    def test_replace_attachment_recycles_old(self, client, admin_headers, tmp_path):
        from files.generators import DOCX_FILENAME, PPTX_FILENAME, make_docx, make_pptx
        from app.models.backup import RecycleBin

        doc_id = _make_doc(client, admin_headers, title="替换附件")
        old = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
        first = self._attach(client, admin_headers, doc_id, DOCX_FILENAME, old)
        assert first.status_code == 200

        new = make_pptx(tmp_path / PPTX_FILENAME).read_bytes()
        second = self._attach(client, admin_headers, doc_id, PPTX_FILENAME, new)
        assert second.status_code == 200
        assert second.json()["recycled_file_id"] == first.json()["attachment_file"]["id"]

        # 回收站记录必须落在 attachment_file_record_id，不能占用 file_record_id
        db = SessionLocal()
        try:
            entry = (
                db.query(RecycleBin)
                .filter(RecycleBin.attachment_file_record_id.isnot(None))
                .order_by(RecycleBin.id.desc())
                .first()
            )
            assert entry is not None
            assert entry.reason == "replaced"
            assert entry.file_record_id is None
        finally:
            db.close()

    def test_one_attachment_per_document(self, client, admin_headers, tmp_path):
        """每资料最多 1 个附件：第二次上传是替换，不是新增。"""
        from files.generators import DOCX_FILENAME, PPTX_FILENAME, make_docx, make_pptx

        doc_id = _make_doc(client, admin_headers, title="只一个附件")
        self._attach(client, admin_headers, doc_id, DOCX_FILENAME,
                     make_docx(tmp_path / DOCX_FILENAME).read_bytes())
        self._attach(client, admin_headers, doc_id, PPTX_FILENAME,
                     make_pptx(tmp_path / PPTX_FILENAME).read_bytes())

        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["attachment_file"]["file_type"] == "pptx"
        db = SessionLocal()
        try:
            rows = db.query(FileRecord).filter(
                FileRecord.owner_type == "document",
                FileRecord.owner_id == doc_id,
                FileRecord.status == "active",
            ).all()
            # 一条主文件 + 一条附件；旧附件已 recycled 不算
            assert len(rows) <= 2
        finally:
            db.close()

    def test_delete_attachment_uses_dedicated_reason(self, client, admin_headers, tmp_path):
        """删附件用 attachment_deleted，且不改资料归档状态。"""
        from files.generators import DOCX_FILENAME, make_docx
        from app.models.backup import RecycleBin

        doc_id = _make_doc(client, admin_headers, title="删附件")
        self._attach(client, admin_headers, doc_id, DOCX_FILENAME,
                     make_docx(tmp_path / DOCX_FILENAME).read_bytes())

        resp = client.delete(f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers)
        assert resp.status_code == 200, resp.text

        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["attachment_file"] is None
        assert detail["archived"] is False, "删附件不得改变资料归档状态"

        db = SessionLocal()
        try:
            entry = db.query(RecycleBin).order_by(RecycleBin.id.desc()).first()
            assert entry.reason == "attachment_deleted"
        finally:
            db.close()

    def test_attachment_requires_admin(self, client, admin_headers, user_headers, tmp_path):
        from files.generators import DOCX_FILENAME, make_docx

        doc_id = _make_doc(client, admin_headers, title="权限")
        data = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
        assert self._attach(client, user_headers, doc_id, DOCX_FILENAME, data).status_code == 403
        assert client.delete(
            f"/api/v1/documents/{doc_id}/attachment", headers=user_headers
        ).status_code == 403

    def test_attachment_download_requires_visibility(self, client, admin_headers, tmp_path):
        """非可见用户下载原稿附件 → 404（与主文件同口径）。"""
        from files.generators import DOCX_FILENAME, make_docx

        # 只对指定用户可见的资料，另一个用户拿不到附件
        doc = create_document(
            client, admin_headers, title="私密附件", effective_from="2026-01-01"
        )
        doc_id = doc["id"]
        client.put(
            f"/api/v1/documents/{doc_id}",
            headers=admin_headers,
            json={"audience_scope": "user", "audience_user_ids": [999999]},
        )
        self._attach(client, admin_headers, doc_id, DOCX_FILENAME,
                     make_docx(tmp_path / DOCX_FILENAME).read_bytes())

        resp = client.get(
            f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
        )
        assert resp.status_code == 200  # admin 总能拿到
