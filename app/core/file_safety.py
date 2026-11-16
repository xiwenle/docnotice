"""上传文件安全校验管线（docs/DESIGN.md §5.1、项目规范 §10/§11/§32）。

流程：扩展名白名单 → 魔数/真实类型校验 → 大小限制 → 文件名清洗 → 服务端生成内部名 → SHA-256。

- 扩展名与魔数不一致 → 拒绝（FILE_TYPE_NOT_ALLOWED / FILE_TYPE_MISMATCH）
- 存储名只含 hex+扩展名（服务器唯一标识，用户不可指定存储路径）
- 所有目录拼接基于配置的 DATA_DIR 并做防穿越断言
"""
from __future__ import annotations

import hashlib
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Optional
from urllib.parse import unquote

from app.core.config import get_settings
from app.core.errors import AppError

# 主文件白名单（小写、含点）。pptx 刻意不在内：系统无法预览它，
# 允许作主文件只会得到「上传成功但详情页一片空白」的坏体验（2026-11-06）。
MAIN_FILE_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".xls",
    # 图片（2026-11-03 产品决策：资料主文件可以是图片）
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
}

# 附件白名单：附件永不预览，只是可下载的额外文件槽，所以可以放宽到 pptx。
ATTACHMENT_EXTENSIONS = MAIN_FILE_EXTENSIONS | {".pptx"}

# 兼容旧名：默认白名单 = 主文件白名单（现有调用点行为不变）
ALLOWED_EXTENSIONS = MAIN_FILE_EXTENSIONS

# 归一化类型 → MIME
FILE_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}

# 图片类型集合（预览分派用；jpg/jpeg 同族）
IMAGE_TYPES = frozenset({"jpg", "jpeg", "png", "gif", "webp"})

# 魔数
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GIF_MAGICS = (b"GIF87a", b"GIF89a")
WEBP_RIFF = b"RIFF"
WEBP_TAG = b"WEBP"

# 文件名清洗：剔除路径分隔符与危险字符，仅保留基础名（中文正常，§10）
_UNSAFE_NAME_RE = re.compile(r"[/\\:<>\"|?*\x00-\x1f]")


def clean_filename(raw: str) -> str:
    """清洗原始文件名：URL 解码→取基础名→剔除分隔符与危险字符；为空则拒绝。

    同时处理原始路径穿越（../etc）与百分号编码形态（%2e%2e%2f、%2F 等）。
    """
    # 先 URL 解码一次，消除 %2F %5C %2E %00 等编码分隔符（仅用于清洗判断，
    # 不改变展示名中合法的中文/空格等）
    decoded = unquote(raw or "", errors="ignore")
    name = Path(decoded).name
    name = _UNSAFE_NAME_RE.sub("_", name).strip()
    name = name.strip(". \t")
    if not name:
        raise AppError("VALIDATION_ERROR", "文件名无效")
    return name


def extension_of(filename: str) -> str:
    """取小写扩展名（含点），如 '.pdf'。"""
    return Path(filename).suffix.lower()


def check_extension(ext: str, allowed: Optional[set[str]] = None) -> str:
    """扩展名白名单校验；非法 → 415 FILE_TYPE_NOT_ALLOWED。

    allowed 为空时用主文件白名单（保持现有调用点行为不变）；
    附件路径显式传 ATTACHMENT_EXTENSIONS。
    """
    allow = ALLOWED_EXTENSIONS if allowed is None else allowed
    if ext not in allow:
        raise AppError(
            "FILE_TYPE_NOT_ALLOWED",
            "不支持的文件类型，仅允许 "
            + " / ".join(sorted(e.lstrip(".").upper() for e in allow)),
        )
    return ext


def _verify_magic(file_type: str, data: bytes) -> None:
    """按归一化类型做魔数/真实类型校验；不符 → 415 FILE_TYPE_MISMATCH。"""
    head = data[:8]
    if file_type == "pdf":
        # %PDF 头允许出现在前 1024 字节内（PDF 规范允许前导垃圾字节，防误杀真文件）
        if PDF_MAGIC not in data[:1024]:
            raise AppError("FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）")
        # 深度校验：截断/加密/结构损坏的 PDF 在上传时即拒绝，
        # 避免"上传成功但永远无法预览"的坏状态（§12 预览必须可用）
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise AppError(
                    "FILE_TYPE_MISMATCH", "PDF 已加密，系统不支持上传加密文档"
                )
            if len(reader.pages) < 1:
                raise AppError("FILE_TYPE_MISMATCH", "PDF 无有效页面（空文档或损坏）")
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                "FILE_TYPE_MISMATCH", "PDF 文件损坏或无法解析，请重新导出后上传"
            ) from exc
        return
    if file_type == "xls":
        if not head.startswith(OLE2_MAGIC):
            raise AppError("FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）")
        # 用 xlrd 打开验证（只读）；失败视为损坏/伪造
        try:
            import xlrd  # type: ignore[import-untyped]

            xlrd.open_workbook(file_contents=data)
        except Exception as exc:  # noqa: BLE001
            raise AppError("FILE_TYPE_MISMATCH", "XLS 文件无法解析（损坏或伪造）") from exc
        return
    if file_type in IMAGE_TYPES:
        # 图片：先比对文件头。单图上传仍只做浅校验（浏览器内联展示，
        # 展示失败会走「下载原文件」兜底）；多图合并时才由
        # file_service.merge_images_to_pdf 做完整解码（2026-11-04）。
        if file_type in ("jpg", "jpeg"):
            ok = data[:3] == JPEG_MAGIC
        elif file_type == "png":
            ok = data[:8] == PNG_MAGIC
        elif file_type == "gif":
            ok = data[:6] in GIF_MAGICS
        else:  # webp：RIFF....WEBP
            ok = data[:4] == WEBP_RIFF and data[8:12] == WEBP_TAG
        if not ok:
            raise AppError("FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）")
        return

    # docx / xlsx / pptx：ZIP 容器 + [Content_Types].xml + 对应 OOXML 部件
    if not head.startswith(ZIP_MAGIC):
        raise AppError("FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）")
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" not in names:
                raise AppError(
                    "FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）"
                )
            if file_type == "docx" and "word/document.xml" not in names:
                raise AppError(
                    "FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）"
                )
            if file_type == "xlsx" and "xl/workbook.xml" not in names:
                raise AppError(
                    "FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）"
                )
            if file_type == "pptx" and "ppt/presentation.xml" not in names:
                raise AppError(
                    "FILE_TYPE_MISMATCH", "文件内容与扩展名不符（伪造扩展名）"
                )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "FILE_TYPE_MISMATCH", "压缩包文件无法解析（损坏或伪造）"
        ) from exc


def generate_internal_name(ext: str) -> str:
    """服务端生成内部存储名：uuid4().hex + 合法扩展名（§11.6）。"""
    return f"{uuid.uuid4().hex}{ext}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    """分块计算流的 SHA-256（大文件不整读内存）。"""
    h = hashlib.sha256()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def validate_upload(
    original_filename: str,
    data: bytes,
    *,
    max_size_mb: Optional[int] = None,
    allowed: Optional[set[str]] = None,
) -> dict:
    """完整上传校验管线。

    返回（后续 file_service 存盘所需）：
        original_filename / file_type / extension / mime_type
        size_bytes / sha256 / internal_name / storage_rel_path(storage_dir 传入后)
    校验失败抛 AppError（415/413/400）。

    allowed：显式白名单。默认（None）= 主文件白名单，现有调用点行为不变；
    附件上传传 ATTACHMENT_EXTENSIONS（多一个 .pptx）。
    """
    settings = get_settings()
    limit_mb = max_size_mb or settings.MAX_UPLOAD_SIZE_MB

    original_filename = clean_filename(original_filename)
    ext = extension_of(original_filename)
    check_extension(ext, allowed)
    file_type = ext.lstrip(".")  # 归一化类型：pdf/docx/xlsx/xls

    if len(data) > limit_mb * 1024 * 1024:
        raise AppError(
            "FILE_TOO_LARGE",
            f"文件超过大小限制（{limit_mb}MB）",
            detail={"max_size_mb": limit_mb, "actual_bytes": len(data)},
        )

    _verify_magic(file_type, data)

    return {
        "original_filename": original_filename,
        "file_type": file_type,
        "extension": ext,
        "mime_type": FILE_MIME_TYPES[file_type],
        "size_bytes": len(data),
        "sha256": sha256_bytes(data),
        "internal_name": generate_internal_name(ext),
    }


def safe_join(data_dir: Path, rel_parts: str | Path) -> Path:
    """防路径穿越：将相对路径拼接到 DATA_DIR 下并断言仍在 DATA_DIR 内。"""
    base = data_dir.resolve()
    candidate = (base / rel_parts).resolve()
    # 使用 pathlib 对象比较，兼容 Windows（\）和 Unix（/）路径分隔符
    try:
        candidate.relative_to(base)
    except ValueError:
        raise AppError("VALIDATION_ERROR", "非法路径")
    return candidate