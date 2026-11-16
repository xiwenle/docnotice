"""测试文件生成器（t23；项目规范 §32 文件测试）。

全部生成 **真实可解析** 的文件（不是伪造字节）：
- PDF ：pypdf（PdfWriter + DecodedStreamObject 原始流），页面可被 PdfReader 解析、文本可提取
- DOCX：python-docx（含中文标题/段落/表格）
- XLSX：openpyxl（含中文表头与数据行）
- XLS ：xlwt 写真实 OLE2/BIFF8（xlrd 可读）

文件名固定使用中文（项目规范 §10、§32“中文文件名”），
每个生成函数返回 Path；`sha256_of` 提供哈希断言；`make_large_pdf` 支持可配置目标大小；
`make_corrupted` 生成“保留魔数头但内容损坏”的文件（魔数校验可通过、解析必然失败）。
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Iterable, Optional

# 中文文件名样例（上传/下载/替换测试复用）
PDF_FILENAME = "员工手册2026.pdf"
DOCX_FILENAME = "公司制度.docx"
XLSX_FILENAME = "费用报销表.xlsx"
XLS_FILENAME = "工资明细表.xls"
LARGE_FILENAME = "大文件测试.pdf"
# pptx 只允许作附件（主文件白名单不含它，无法预览）
PPTX_FILENAME = "季度汇报.pptx"


def _pdf_escape(text: str) -> str:
    """PDF 字符串字面量转义（反斜杠与圆括号）。"""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_content_stream(page_no: int, text: str) -> bytes:
    """单页内容流（ASCII 文本，Helvetica 字体，便于 PdfReader.extract_text）。"""
    line = f"BT /F1 14 Tf 72 760 Td ({_pdf_escape(text)} - Page {page_no}) Tj ET"
    return line.encode("latin-1")


def make_pdf(
    path: Path | str,
    *,
    title: str = "员工手册 2026",
    pages: int = 2,
    body: str = "Company Internal Document Test",
) -> Path:
    """pypdf 生成真实 PDF：中文进文档元数据（UTF-16），页面内容为可提取文本。"""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    path = Path(path)
    writer = PdfWriter()
    for i in range(pages):
        page = writer.add_blank_page(width=595.28, height=841.89)  # A4
        fonts = DictionaryObject(
            {
                NameObject("/F1"): DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }
                )
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): fonts}
        )
        stream = DecodedStreamObject()
        stream.set_data(_pdf_content_stream(i + 1, body))
        page[NameObject("/Contents")] = stream
    writer.add_metadata({"/Title": title, "/Author": "doc-notice-tests"})
    with open(path, "wb") as f:
        writer.write(f)
    return path


def make_large_pdf(path: Path | str, target_bytes: int, *, min_bytes: int = 3000) -> Path:
    """生成大小**可配置**的真实 PDF（解析器可正常读取）。

    原理：单页内容流使用未压缩的 DecodedStreamObject，文件体积 ≈ 载荷 + 固定开销
    （约 0.6KB），通过 2–3 次迭代把最终体积逼近 target_bytes（误差 < 2KB）。
    target_bytes 至少为 min_bytes。
    """
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    target_bytes = max(int(target_bytes), min_bytes)
    path = Path(path)
    payload_len = max(1024, target_bytes - 4096)  # 预扣开销
    for _ in range(4):
        writer = PdfWriter()
        page = writer.add_blank_page(width=595.28, height=841.89)
        fonts = DictionaryObject(
            {
                NameObject("/F1"): DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }
                )
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): fonts}
        )
        # 大载荷：BT ... (aaaa...) Tj ET —— 纯 ASCII 可解析文本流
        payload = b"BT /F1 14 Tf 72 760 Td (" + b"a" * payload_len + b") Tj ET"
        stream = DecodedStreamObject()
        stream.set_data(payload)
        page[NameObject("/Contents")] = stream
        with open(path, "wb") as f:
            writer.write(f)
        size = path.stat().st_size
        delta = target_bytes - size
        if abs(delta) <= 2048:
            break
        payload_len = max(1024, payload_len + delta)
    return path


def make_docx(
    path: Path | str,
    *,
    title: str = "公司制度",
    paragraphs: Optional[Iterable[str]] = None,
    table_rows: int = 3,
    table_cols: int = 3,
) -> Path:
    """python-docx 生成真实 DOCX：中文标题 + 中文段落 + 表格。"""
    from docx import Document

    path = Path(path)
    paragraphs = list(paragraphs) if paragraphs is not None else [
        "第一条：本制度适用于公司全体员工。",
        "第二条：本制度自发布之日起施行，解释权归综合管理部。",
    ]
    doc = Document()
    doc.add_heading(title, level=1)
    for para in paragraphs:
        doc.add_paragraph(para)
    table = doc.add_table(rows=table_rows, cols=table_cols)
    for r in range(table_rows):
        for c in range(table_cols):
            table.cell(r, c).text = f"单元格{r + 1}-{c + 1}"
    doc.save(str(path))
    return path


def make_xlsx(
    path: Path | str,
    *,
    sheet_names: Optional[Iterable[str]] = None,
    rows: int = 6,
    cols: int = 4,
    values: Optional[Iterable[Iterable[str]]] = None,
) -> Path:
    """openpyxl 生成真实 XLSX：中文表头 + 数据行 + 多个工作表。"""
    from openpyxl import Workbook

    path = Path(path)
    sheet_names = list(sheet_names) if sheet_names is not None else ["费用明细", "汇总"]
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_names[0]
    if values is None:
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                ws.cell(row=r, column=c, value=f"数据{r}-{c}")
    else:
        for r, row_vals in enumerate(values, start=1):
            for c, v in enumerate(row_vals, start=1):
                ws.cell(row=r, column=c, value=v)
    for name in sheet_names[1:]:
        extra = wb.create_sheet(title=name)
        extra.cell(row=1, column=1, value=f"{name}工作表示例")
    wb.save(str(path))
    return path


def make_xls(
    path: Path | str,
    *,
    sheet_names: Optional[Iterable[str]] = None,
    rows: int = 6,
    cols: int = 4,
) -> Path:
    """xlwt 生成真实 XLS（BIFF8/OLE2，xlrd 可读）：中文表头与数据。"""
    import xlwt

    path = Path(path)
    sheet_names = list(sheet_names) if sheet_names is not None else ["工资表", "考勤表"]
    book = xlwt.Workbook(encoding="utf-8")
    for name in sheet_names:
        ws = book.add_sheet(name)
        for r in range(rows):
            for c in range(cols):
                ws.write(r, c, f"数据{r + 1}-{c + 1}")
    book.save(str(path))
    return path


def make_large_xlsx(path: Path | str, target_bytes: int) -> Path:
    """大小可配置的真实 XLSX（按估算行数写入，可被 openpyxl 重新读取）。"""
    from openpyxl import Workbook

    path = Path(path)
    # 每行约 6 列中文（≈180B 未压缩），压缩后按行数线性增长
    rows_target = max(10, int(target_bytes / 200))
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="大数据表")
    ws.append(["列1", "列2", "列3", "列4", "列5", "列6"])
    for i in range(rows_target):
        ws.append([f"填充数据行{i}-列{j}-中文内容" for j in range(1, 7)])
    wb.save(str(path))
    return path


def make_corrupted(path: Path | str, src: Path | str, *, keep_header: bool = True) -> Path:
    """生成“损坏”文件：截断 + 中段字节翻转（保留魔数头）。

    用途：魔数校验可通过（上传成功），但解析（预览/打开）必然失败 ——
    对应 项目规范 §32“文件损坏”场景。
    """
    path = Path(path)
    src = Path(src)
    data = src.read_bytes()
    if len(data) < 16:
        raise ValueError("源文件过小，无法构造损坏样本")
    # 保留魔数头（前 8 字节不动），中段翻转、尾部截断
    mid = len(data) // 2
    flipped = bytes(b ^ 0xFF for b in data[mid : mid + 64])
    out = data[: mid + 64] + flipped + data[mid + 64 + 64 : mid + 128]
    path.write_bytes(out)
    return path


def make_duplicate(src: Path | str, dst: Path | str) -> Path:
    """复制文件（重复文件测试：同一内容两份，SHA-256 相同）。"""
    dst = Path(dst)
    dst.write_bytes(Path(src).read_bytes())
    return dst


CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>"""

ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>"""

PRESENTATION_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>"""

PRESENTATION_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>"""

SLIDE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:txBody>
      <a:bodyPr/><a:lstStyle/>
      <a:p><a:r><a:t>测试幻灯片</a:t></a:r></a:p>
    </p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>"""


def make_pptx(path: Path | str, *, title: str = "汇报材料") -> Path:
    """用标准库 zipfile 生成最小合法 PPTX（OPC 包，含 ppt/presentation.xml）。

    不引入 python-pptx：本系统对 pptx 只做「存 + 下载」，从不解析内容，
    校验只需确认它是真正的 PPTX 而不是改了扩展名的 zip（项目规范 §11）。
    """
    import zipfile

    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", ROOT_RELS_XML)
        zf.writestr("ppt/presentation.xml", PRESENTATION_XML)
        zf.writestr("ppt/_rels/presentation.xml.rels", PRESENTATION_RELS_XML)
        zf.writestr("ppt/slides/slide1.xml", SLIDE_XML)
        zf.writestr("docProps/core.xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<cp:coreProperties '
                    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    f"<dc:title>{title}</dc:title></cp:coreProperties>")
    return path


def make_fake_pptx(path: Path | str) -> Path:
    """伪造 PPTX：合法的 ZIP 包，但缺 ppt/presentation.xml → 魔数校验必须拒绝。"""
    import zipfile

    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        zf.writestr("word/document.xml", "<w:document/>")
    return path


def write_raw_file(path: Path | str, content: bytes) -> Path:
    """写入任意字节（伪造扩展名测试：如 exe 内容改名 .pdf）。"""
    path = Path(path)
    path.write_bytes(content)
    return path


def sha256_of(path: Path | str) -> str:
    """计算文件 SHA-256（与 file_safety.compute_sha256 同口径）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_name(ext: str) -> str:
    """生成唯一内部文件名（uuid4.hex + ext，与生产 internal_name 同形态）。"""
    return f"{uuid.uuid4().hex}{ext}"