"""预览服务（docs/DESIGN.md §6、项目规范 §12）。

原则：预览不替代原始文件；一律提供原始文件下载；不做会改动原始文件的操作。

返回形态（注意：**不返回 HTML**）：
- PDF：浏览器内联（Content-Type: application/pdf + inline），服务端不渲染
- 图片：原始字节内联，服务端不转码
- DOCX：python-docx 解析为结构化 JSON（`blocks`：heading/paragraph/list/table，
  附 `text` 纯文本兜底），前端用 DOM + textContent 渲染
- XLSX：openpyxl 只读（read_only=True）→ JSON 表格数据
- XLS：xlrd（xlrd>=2.0 仅支持 .xls）→ JSON 表格数据

预览失败（文件损坏）→ 抛可读错误（前端可显示"下载原文件"按钮）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.file_safety import IMAGE_TYPES, PDF_MAGIC
from app.models.document import FileRecord
from app.services import file_service

settings = get_settings()

logger = logging.getLogger(__name__)


def _abs_path(rec: FileRecord) -> Path:
    return file_service.abs_path(rec.storage_rel_path)


def preview_pdf(rec: FileRecord) -> dict:
    """PDF 内联预览参数（媒体类型与文件路径由路由组织响应）。"""
    if rec.file_type != "pdf":
        raise AppError("BAD_REQUEST", "该文件不是 PDF，无法以 PDF 预览")
    path = _abs_path(rec)
    if not path.exists():
        raise AppError("NOT_FOUND", "文件不存在（可能已损坏）")
    
    # 验证 PDF 魔数（允许前 1024 字节内的前导垃圾，兼容真实 PDF；服务端不做深度解析，
    # 渲染交由浏览器判定——与 test_files.py「损坏 PDF 预览内联 200 返回」契约一致）
    try:
        with open(path, 'rb') as f:
            head = f.read(1024)
            if PDF_MAGIC not in head:
                raise AppError("BAD_REQUEST", "文件不是有效的 PDF 格式")
    except AppError:
        raise
    except Exception as exc:
        logger.warning("PDF 预览读取失败 file=%s", rec.storage_rel_path, exc_info=True)
        raise AppError("BAD_REQUEST", "PDF 文件损坏或无法读取") from exc
    
    return {"path": path, "media_type": "application/pdf",
            "filename": rec.original_filename}


def preview_image(rec: FileRecord) -> dict:
    """图片内联预览参数（浏览器直接渲染，服务端不做转码）。

    AGENTS §12：预览不替代原始文件——按原样返回，不缩放/裁剪/转格式，
    避免"预览与原件不一致"的歧义。
    """
    if rec.file_type not in IMAGE_TYPES:
        raise AppError("BAD_REQUEST", "该文件不是图片，无法以图片预览")
    path = _abs_path(rec)
    if not path.exists():
        raise AppError("NOT_FOUND", "文件不存在（可能已损坏）")
    return {"path": path, "media_type": rec.mime_type,
            "filename": rec.original_filename}


_HEADING_RE = re.compile(r"^(?:heading|标题)\s*([1-6])$", re.IGNORECASE)
_LIST_STYLE_RE = re.compile(
    r"^(?:list\s*(?:bullet|number|paragraph)|列表段落|列表)\s*([2-6])?$",
    re.IGNORECASE,
)


def _docx_runs(paragraph) -> list[dict]:
    runs = []
    for run in paragraph.runs:
        if not run.text:
            continue
        item = {"text": run.text}
        if run.bold:
            item["bold"] = True
        if run.italic:
            item["italic"] = True
        if run.underline:
            item["underline"] = True
        runs.append(item)
    if not runs and paragraph.text:
        runs = [{"text": paragraph.text}]
    return runs


def _docx_paragraph_block(paragraph) -> dict:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    runs = _docx_runs(paragraph)
    level = 0
    match = _HEADING_RE.match(style)
    if match:
        level = int(match.group(1))
    elif style.lower() == "title":
        level = 1
    elif style.lower() == "subtitle":
        level = 2
    if level:
        return {
            "type": "heading",
            "level": level,
            "text": paragraph.text,
            "runs": runs,
        }
    return {"type": "paragraph", "text": paragraph.text, "runs": runs}


def _docx_num_fmt(paragraph) -> Optional[str]:
    """读取段落编号定义的 numFmt（Word 默认编号列表常是 List Paragraph + numPr）。"""
    p_pr = paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None else None
    if num_pr is None or num_pr.numId is None:
        return None
    num_id = str(num_pr.numId.val)
    ilvl = str(num_pr.ilvl.val) if num_pr.ilvl is not None else "0"
    try:
        from docx.oxml.ns import qn

        numbering = paragraph.part.numbering_part.element
    except Exception:  # noqa: BLE001 - 编号部件缺失时退回样式判断
        return None
    if numbering is None:
        return None
    for num in numbering.findall(qn("w:num")):
        if num.get(qn("w:numId")) != num_id:
            continue
        abstract_ref = num.find(qn("w:abstractNumId"))
        if abstract_ref is None:
            return None
        abstract_id = abstract_ref.get(qn("w:val"))
        for abstract in numbering.findall(qn("w:abstractNum")):
            if abstract.get(qn("w:abstractNumId")) != abstract_id:
                continue
            for lvl in abstract.findall(qn("w:lvl")):
                if lvl.get(qn("w:ilvl")) != ilvl:
                    continue
                fmt = lvl.find(qn("w:numFmt"))
                return fmt.get(qn("w:val")) if fmt is not None else None
    return None


def _docx_list_info(paragraph) -> Optional[dict]:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    p_pr = paragraph._p.pPr
    has_num = p_pr is not None and p_pr.numPr is not None
    if not has_num and not _LIST_STYLE_RE.match(style):
        return None
    num_fmt = _docx_num_fmt(paragraph)
    if num_fmt:
        ordered = num_fmt != "bullet"
    else:
        ordered = bool(re.search(r"number|编号|数字", style, re.IGNORECASE))
    level = 0
    match = _LIST_STYLE_RE.match(style)
    if match and match.group(1):
        level = int(match.group(1)) - 1
    return {"ordered": ordered, "level": min(level, 5)}


def preview_docx(rec: FileRecord) -> dict:
    """DOCX 预览：结构化 blocks + 纯文本兜底（截断；不返回 HTML）。

    blocks 只包含文本与结构（标题级别、runs、列表、表格），前端用 DOM +
    textContent 渲染；这样既能保留标题/加粗/表格，又不把 DOCX 内容当 HTML 信任。
    """
    if rec.file_type != "docx":
        raise AppError("BAD_REQUEST", "该文件不是 DOCX，无法以文本预览")
    path = _abs_path(rec)
    if not path.exists():
        raise AppError("NOT_FOUND", "文件不存在（可能已损坏）")
    try:
        from docx import Document as DocxDocument
    except Exception as exc:  # noqa: BLE001
        raise AppError("INTERNAL_ERROR", "DOCX 解析组件不可用") from exc

    max_paras = settings.PREVIEW_DOCX_MAX_PARAS
    parts: list[str] = []
    blocks: list[dict] = []
    pending_list: Optional[dict] = None
    count = 0
    truncated = False

    def flush_list() -> None:
        nonlocal pending_list
        if pending_list is not None:
            blocks.append(pending_list)
            pending_list = None

    try:
        doc = DocxDocument(str(path))
        # 按文档顺序提取段落与表格文本
        from docx.document import Document as _Doc  # noqa: F401
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        def iter_block_items(parent):
            from docx.oxml.ns import qn
            for child in parent.element.body.iterchildren():
                if child.tag == qn("w:p"):
                    yield Paragraph(child, parent)
                elif child.tag == qn("w:tbl"):
                    yield Table(child, parent)

        for block in iter_block_items(doc):
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue
                parts.append(text)
                list_info = _docx_list_info(block)
                if list_info:
                    if (
                        pending_list is None
                        or pending_list["ordered"] != list_info["ordered"]
                        or pending_list["level"] != list_info["level"]
                    ):
                        flush_list()
                        pending_list = {
                            "type": "list",
                            "ordered": list_info["ordered"],
                            "level": list_info["level"],
                            "items": [],
                        }
                    pending_list["items"].append(
                        {"text": text, "runs": _docx_runs(block)}
                    )
                else:
                    flush_list()
                    blocks.append(_docx_paragraph_block(block))
                count += 1
            else:  # Table：文本兜底保持 [表] 行，同时输出结构化行
                flush_list()
                rows = block.rows
                if rows:
                    parts.append("")
                    table_rows = []
                    for row in rows[:100]:  # 每表最多 100 行（防御）
                        cells = [c.text.strip() for c in row.cells[:50]]
                        table_rows.append(cells)
                        parts.append("[表] " + " | ".join(cells))
                    parts.append("")
                    blocks.append({"type": "table", "rows": table_rows})
                    count += 1
            if count >= max_paras:
                truncated = True
                break
        flush_list()
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("DOCX 预览解析失败 file=%s", rec.storage_rel_path, exc_info=True)
        raise AppError("BAD_REQUEST", "DOCX 文件损坏或无法解析") from exc

    # 保留 text 兜底；blocks 供前端结构化渲染。两者都不是 HTML。
    return {
        "content_type": "text",
        "text": "\n".join(parts),
        "blocks": blocks,
        "truncated": truncated,
    }


def preview_xlsx(rec: FileRecord, *, sheet: int = 0, max_rows: Optional[int] = None) -> dict:
    """XLSX/XLS 只读预览（API.md §6 preview/xlsx 契约）。"""
    if rec.file_type not in ("xlsx", "xls"):
        raise AppError("BAD_REQUEST", "该文件不是 Excel，无法以表格预览")
    path = _abs_path(rec)
    if not path.exists():
        raise AppError("NOT_FOUND", "文件不存在（可能已损坏）")

    limit = max_rows or settings.PREVIEW_XLSX_MAX_ROWS
    max_sheets = settings.PREVIEW_XLSX_MAX_SHEETS
    if rec.file_type == "xlsx":
        try:
            import openpyxl  # type: ignore[import-untyped]

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("XLSX 预览解析失败 file=%s", rec.storage_rel_path, exc_info=True)
            raise AppError("BAD_REQUEST", "XLSX 文件损坏或无法解析") from exc
        sheet_names = wb.sheetnames
        # 解压炸弹对冲：只解析/暴露前 max_sheets 个 sheet（§2.1-2 评审改进点）
        if len(sheet_names) > max_sheets:
            logger.info("XLSX sheet 数超上限（%d > %d），仅解析前 %d 个",
                        len(sheet_names), max_sheets, max_sheets)
            sheet_names = sheet_names[:max_sheets]
        if sheet < 0 or sheet >= len(sheet_names):
            wb.close()
            raise AppError("VALIDATION_ERROR", f"sheet 越界（共 {len(sheet_names)} 个）")
        ws = wb[sheet_names[sheet]]
    else:
        try:
            import xlrd  # type: ignore[import-untyped]

            book = xlrd.open_workbook(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("XLS 预览解析失败 file=%s", rec.storage_rel_path, exc_info=True)
            raise AppError("BAD_REQUEST", "XLS 文件损坏或无法解析") from exc
        sheet_names = book.sheet_names()
        if len(sheet_names) > max_sheets:
            logger.info("XLS sheet 数超上限（%d > %d），仅解析前 %d 个",
                        len(sheet_names), max_sheets, max_sheets)
            sheet_names = sheet_names[:max_sheets]
        if sheet < 0 or sheet >= len(sheet_names):
            raise AppError("VALIDATION_ERROR", f"sheet 越界（共 {len(sheet_names)} 个）")
        ws = book.sheet_by_index(sheet)

    def _cell_str(v) -> str:
        if v is None:
            return ""
        return str(v)

    if rec.file_type == "xlsx":
        rows_iter = ws.iter_rows(values_only=True)
        headers_row = next(rows_iter, None)
        headers = [_cell_str(v) for v in (headers_row or [])]
        rows: list[list[str]] = []
        truncated = False
        for i, row in enumerate(rows_iter):
            if i >= limit:
                truncated = True
                break
            rows.append([_cell_str(v) for v in row])
        total_rows = max(ws.max_row - 1, 0) if ws.max_row else 0
        wb.close()
    else:
        headers = [_cell_str(ws.cell_value(0, c)) for c in range(ws.ncols)]
        rows = []
        truncated = False
        for r in range(1, ws.nrows):
            if (r - 1) >= limit:
                truncated = True
                break
            rows.append([_cell_str(ws.cell_value(r, c)) for c in range(ws.ncols)])
        total_rows = max(ws.nrows - 1, 0)

    return {
        "content_type": "table",
        "sheet_names": sheet_names,
        "active_sheet": sheet,
        "headers": headers,
        "rows": rows,
        "total_rows": total_rows,
        "truncated": truncated,
    }