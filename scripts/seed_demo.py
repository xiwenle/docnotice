"""种子数据脚本：灌入覆盖所有边界的真实感数据（幂等、可回滚）。

用法：
    python scripts/seed_demo.py          # 灌入数据
    python scripts/seed_demo.py --clean  # 清除本次种子数据

设计原则：
- 所有种子实体打统一标记（标题/名称前缀 "DEMO-"），便于识别和清除
- 记录创建的 id 清单到 scripts/seed_demo_manifest.json
- 使用服务层 API，不绕过业务校验
- 可重复执行不产生脏数据（检查存在性后跳过）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.file_safety import FILE_MIME_TYPES  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.category import Category, Tag, document_tags  # noqa: E402
from app.models.document import Document, FileRecord  # noqa: E402
from app.models.notice import Notice, NoticeRead, NoticeTarget, notice_documents  # noqa: E402
from app.models.user import Department, User, user_departments  # noqa: E402

# 种子数据标记前缀
SEED_PREFIX = "DEMO-"
MANIFEST_FILE = PROJECT_ROOT / "scripts" / "seed_demo_manifest.json"

# ---------------------------------------------------------------------------
# 真实可解码的图片字节（2026-11-04 修正）
#
# ⚠️ 教训：最初这里只写了「文件头正确」的最小字节（如 FFD8FF...FFD9），
# 服务端的魔数校验能过、上传也成功，但**浏览器解不出来**
# （img.naturalWidth === 0，预览区一片空白）。
# 「魔法数字对」≠「有效图像」。
#
# 现在四种都是**真正可解码**的：
# - PNG：用标准库 zlib + struct 现场生成 8×8 纯色图（下方 _make_png）
# - GIF / WEBP / JPEG：最小有效样本的 base64 常量（已验证 file/sips 可识别）
#
# 仍不引入 Pillow（AGENTS §6 技术克制）。
# ---------------------------------------------------------------------------
_MIN_GIF_B64 = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
_MIN_WEBP_B64 = "UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA=="
_MIN_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwcJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPDs0NDT/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


def _make_png(width: int = 8, height: int = 8, color: tuple[int, int, int] = (43, 93, 140)) -> bytes:
    """用标准库生成真实可解码的 PNG（真彩、无压缩过滤）。"""
    import struct
    import zlib

    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes(color) * width  # 每行 filter=none + 像素

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _image_bytes(ext: str) -> bytes:
    import base64

    if ext == "png":
        return _make_png()
    if ext in ("jpg", "jpeg"):
        return base64.b64decode(_MIN_JPEG_B64)
    if ext == "gif":
        return base64.b64decode(_MIN_GIF_B64)
    if ext == "webp":
        return base64.b64decode(_MIN_WEBP_B64)
    raise ValueError(f"不支持的图片类型: {ext}")

# ---------------------------------------------------------------------------
# 分类树（2026-11-03 扩充到 4 级）
#
# 格式：(名称, [子节点]) —— 叶子节点的子树为空列表。
# 旧的 5 个一级与 6 个二级**名称与父级保持不变**，已有资料引用不受影响；
# 新增的三/四级挂在旧二级下面，另有 1 个新根「质量管理」。
#
# 深度上限来自 app/services/category_service.MAX_DEPTH = 4，
# 递归时断言，防止以后改树时越界。
# ---------------------------------------------------------------------------
CATEGORY_TREE: list[tuple[str, list]] = [
    ("制度规范", [
        ("公司规章", [
            ("劳动合同", [("正式员工合同", []), ("实习生协议", [])]),
            ("保密制度", [("保密协议", [])]),
        ]),
        ("流程规范", [
            ("报销流程", [("差旅报销", []), ("日常费用报销", [])]),
            ("采购流程", [("固定资产采购", [])]),
        ]),
    ]),
    ("技术标准", [
        ("开发规范", [
            ("前端规范", [("命名与目录", []), ("组件与样式", [])]),
            ("后端规范", [("接口与错误码", [])]),
        ]),
        ("安全标准", [
            ("账号安全", [("密码与令牌", [])]),
            ("数据安全", [("备份与恢复", [])]),
        ]),
    ]),
    ("培训资料", [
        ("入职培训", [("新员工指引", [("第一天清单", [])])]),
        ("技能培训", [("安全培训", [("消防演练", [])])]),
    ]),
    ("行政管理", [
        ("会议管理", [("会议室预定", [])]),
        ("资产管理", [("办公用品", [])]),
    ]),
    ("质量管理", [
        ("内审", [("审计记录", [("年度内审", [])])]),
    ]),
]


def get_or_create_department(db: Any, name: str) -> int:
    """获取或创建部门（幂等）。

    2026-11-03：部门编码列已按产品决策删除，本函数不再接收 code。
    """
    from sqlalchemy import select

    dept = db.execute(
        select(Department).where(Department.name == name)
    ).scalar_one_or_none()
    if dept:
        return dept.id

    dept = Department(name=name, description=f"{SEED_PREFIX}{name} 部门")
    db.add(dept)
    db.flush()
    return dept.id


def get_or_create_user(
    db: Any,
    username: str,
    display_name: str,
    role: str,
    department_ids: list[int],
    is_active: bool = True,
) -> int:
    """获取或创建用户（幂等）。"""
    from sqlalchemy import select

    user = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if user:
        return user.id

    user = User(
        username=username,
        password_hash=hash_password("Demo@123456"),
        display_name=display_name,
        email=f"{username}@demo.com",
        phone=f"138{len(username):04d}{len(username):04d}",
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.flush()

    # 关联部门
    for dept_id in department_ids:
        assoc = user_departments.insert().values(user_id=user.id, department_id=dept_id)
        db.execute(assoc)

    return user.id


def get_or_create_category(
    db: Any, name: str, parent_id: int | None = None, sort_order: int = 0
) -> int:
    """获取或创建分类（幂等）。"""
    from sqlalchemy import select

    cat = db.execute(
        select(Category).where(
            Category.name == name,
            Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id,
        )
    ).scalar_one_or_none()
    if cat:
        return cat.id

    cat = Category(
        name=name,
        parent_id=parent_id,
        sort_order=sort_order,
        is_active=1,
    )
    db.add(cat)
    db.flush()
    return cat.id


def create_category_tree(
    db: Any, tree: list[tuple[str, list]], parent_id: int | None = None, depth: int = 1
) -> dict[str, int]:
    """递归创建分类树，返回 {全路径名: id}（路径用 " / " 连接）。

    2026-11-03：支持到 MAX_DEPTH=4。每个节点都走 get_or_create_category，
    因此可重复执行而不产生重复行。
    """
    from app.services.category_service import MAX_DEPTH

    if depth > MAX_DEPTH:
        raise ValueError(f"分类树深度超过上限 {MAX_DEPTH}（出现在第 {depth} 层）")

    created: dict[str, int] = {}
    for sort_order, (name, children) in enumerate(tree):
        full_name = f"{SEED_PREFIX}{name}"
        cat_id = get_or_create_category(
            db, full_name, parent_id=parent_id, sort_order=sort_order
        )
        # 路径键用不带前缀的短名，便于调用处书写
        created[name] = cat_id
        if children:
            sub = create_category_tree(db, children, parent_id=cat_id, depth=depth + 1)
            for k, v in sub.items():
                created[f"{name} / {k}"] = v
                # 同时记录短名（叶子节点都用短名引用，避免写死完整路径）
                created.setdefault(k, v)
    return created


def get_or_create_notice_type(db: Any, name: str) -> int:
    """获取或创建通知类型（幂等）。内置类型已由 init_db 预置。"""
    from sqlalchemy import select

    from app.models.notice import NoticeType

    nt = db.execute(
        select(NoticeType).where(NoticeType.name == name)
    ).scalar_one_or_none()
    if nt:
        if not nt.is_active:
            nt.is_active = True
        return nt.id

    nt = NoticeType(name=name, sort_order=99, is_active=True)
    db.add(nt)
    db.flush()
    return nt.id


def get_or_create_tag(db: Any, name: str) -> int:
    """获取或创建标签（幂等）。"""
    from sqlalchemy import select

    tag = db.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
    if tag:
        return tag.id

    tag = Tag(name=name)
    db.add(tag)
    db.flush()
    return tag.id


_PPTX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>"""

_PPTX_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>"""

_PPTX_PRESENTATION = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>"""

_PPTX_PRESENTATION_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>"""

_PPTX_SLIDE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:txBody>
      <a:bodyPr/><a:lstStyle/>
      <a:p><a:r><a:t>DEMO 演示幻灯片</a:t></a:r></a:p>
    </p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>"""


def _write_real_file(
    file_type: str, internal_name: str, title: str, owner: str = "documents"
) -> tuple[int, str]:
    """生成真实可解析的演示文件并写入 data/<owner>/，返回 (size_bytes, sha256)。

    此前种子只建元数据不落盘，导致演示资料预览/下载必然失败（"文件已损坏"，
    实为文件缺失）。现按类型生成真实文件（PDF 用 pypdf、DOCX 用 python-docx、
    XLSX 用 openpyxl、XLS 用 xlwt、图片用真实可解码字节），可被预览链路解析。

    2026-11-04 修正 `owner` 参数：**通知附件必须写进 `notices/`**。
    此前函数硬编码 `documents/`，但 FileRecord.storage_rel_path 记的是
    `notices/...`，于是通知附件预览/下载全部 404（文件根本不在那个位置）。
    """
    import hashlib

    from app.core.config import get_settings

    base = get_settings().data_dir / owner / internal_name[:2]
    base.mkdir(parents=True, exist_ok=True)
    path = base / internal_name

    if file_type in ("png", "jpg", "jpeg", "gif", "webp"):
        # 2026-11-04：写**真实可解码**的图片字节（早期只写文件头会
        # 导致浏览器 naturalWidth=0，预览空白）。服务端预览不转码。
        path.write_bytes(_image_bytes(file_type))
    elif file_type == "pdf":
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=595.28, height=841.89)
        stream = DecodedStreamObject()
        # PDF 内容流仅限 latin-1：页面放 ASCII 文本，中文标题进元数据（UTF-16）
        stream.set_data(b"BT /F1 14 Tf 72 760 Td (DEMO Document - Internal Use Only) Tj ET")
        page[NameObject("/Contents")] = stream
        writer.add_metadata({"/Title": f"{SEED_PREFIX}{title}"})
        with open(path, "wb") as f:
            writer.write(f)
    elif file_type == "docx":
        from docx import Document as DocxDocument

        d = DocxDocument()
        d.add_heading(f"{title}", level=1)
        d.add_paragraph(f"{SEED_PREFIX}演示文档：{title}。本文件由种子脚本生成，用于演示预览与下载。")
        d.add_paragraph("这是第二段演示文字，验证 DOCX 在线预览的段落渲染。")
        d.save(str(path))
    elif file_type == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "演示数据"
        ws.append(["项目", "数值", "说明"])
        ws.append(["示例项 A", 1, f"{SEED_PREFIX}演示行"])
        ws.append(["示例项 B", 2, f"{SEED_PREFIX}演示行"])
        wb.save(str(path))
    elif file_type == "xls":
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("演示数据")
        ws.write(0, 0, "项目")
        ws.write(0, 1, "数值")
        ws.write(1, 0, "示例项 A")
        ws.write(1, 1, 1)
        wb.save(str(path))
    elif file_type == "pptx":
        # 2026-11-06：附件专用格式。标准库 zipfile 打最小合法 OPC 包，
        # 与 tests/files/generators.make_pptx 同构，不引入 python-pptx。
        import zipfile

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", _PPTX_CONTENT_TYPES)
            zf.writestr("_rels/.rels", _PPTX_ROOT_RELS)
            zf.writestr("ppt/presentation.xml", _PPTX_PRESENTATION)
            zf.writestr("ppt/_rels/presentation.xml.rels", _PPTX_PRESENTATION_RELS)
            zf.writestr("ppt/slides/slide1.xml", _PPTX_SLIDE)
            zf.writestr(
                "docProps/core.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties '
                'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f"<dc:title>{SEED_PREFIX}{title}</dc:title></cp:coreProperties>",
            )
    else:
        raise ValueError(f"种子脚本不支持的文件类型: {file_type}")

    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def create_document(
    db: Any,
    title: str,
    category_id: int | None,
    tag_ids: list[int],
    effective_from: date,
    effective_to: date | None,
    allow_download: bool = False,
    archived: bool = False,
    file_type: str | None = None,
    filename: str | None = None,
    attachment_type: str | None = None,
    attachment_filename: str | None = None,
    audience_scope: str = "all",
    audience_department_ids: list[int] | None = None,
    audience_user_ids: list[int] | None = None,
) -> int:
    """创建资料（可选带文件、归档、可见范围）。

    2026-11-03：新增 audience_* 参数。落库走与生产同一条路径
    （document_visibility.apply_targets），不手写 document_targets 行，
    保证种子数据的可见范围语义与线上完全一致。
    """
    from datetime import datetime

    from app.services import document_visibility

    doc = Document(
        title=title,
        summary=f"{SEED_PREFIX}测试资料：{title}",
        category_id=category_id,
        effective_from=effective_from,
        effective_to=effective_to,
        # allow_download 已于 2026-11-04 移除（下载权限 = 查看权限），
        # 模型不再接受该字段；参数保留是为兼容既有调用点签名。
        archived_at=datetime.now() if archived else None,
        created_by=1,  # admin
    )
    db.add(doc)
    db.flush()

    document_visibility.apply_targets(
        db, doc, audience_scope,
        department_ids=audience_department_ids,
        user_ids=audience_user_ids,
    )

    # 关联标签
    for tag_id in tag_ids:
        assoc = document_tags.insert().values(document_id=doc.id, tag_id=tag_id)
        db.execute(assoc)

    # 创建文件记录并落盘真实可解析的演示文件（预览/下载可用）
    if file_type and filename:
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        internal_name = f"demo_{unique_id}_{file_type}.{file_type}"
        size_bytes, sha256 = _write_real_file(file_type, internal_name, title)
        fr = FileRecord(
            owner_type="document",
            owner_id=doc.id,
            original_filename=filename,
            internal_name=internal_name,
            file_type=file_type,
            extension=f".{file_type}",
            mime_type=FILE_MIME_TYPES[file_type],
            size_bytes=size_bytes,
            sha256=sha256,
            storage_rel_path=f"documents/{internal_name[:2]}/{internal_name}",
            status="active",
            uploaded_by=1,
        )
        db.add(fr)
        db.flush()
        doc.current_file_id = fr.id
        db.flush()

    # 附件（2026-11-06）：与主文件独立的第二个文件槽，永不预览。
    # 演示数据用真实可下载的字节，保证「下载原稿附件」链路可直接验收。
    if attachment_type and attachment_filename:
        import uuid

        att_uid = uuid.uuid4().hex[:8]
        att_internal = f"demo_{att_uid}_{attachment_type}.{attachment_type}"
        att_size, att_sha = _write_real_file(
            attachment_type, att_internal, title
        )
        afr = FileRecord(
            owner_type="document",
            owner_id=doc.id,
            original_filename=attachment_filename,
            internal_name=att_internal,
            file_type=attachment_type,
            extension=f".{attachment_type}",
            mime_type=FILE_MIME_TYPES[attachment_type],
            size_bytes=att_size,
            sha256=att_sha,
            storage_rel_path=f"documents/{att_internal[:2]}/{att_internal}",
            status="active",
            uploaded_by=1,
        )
        db.add(afr)
        db.flush()
        doc.attachment_file_id = afr.id
        db.flush()

    return doc.id


def create_notice(
    db: Any,
    title: str,
    content: str | None,
    notice_type: str,
    status: str,
    target_users: list[int] | None = None,
    target_departments: list[int] | None = None,
    target_all: bool = False,
    document_ids: list[int] | None = None,
    published_at: datetime | None = None,
    deadline_at: datetime | None = None,
    read_users: list[int] | None = None,
    file_type: str | None = None,
    filename: str | None = None,
    apply_linkage: bool = False,
) -> int:
    """创建通知（含目标、阅读快照、可选附件与联动）。

    2026-11-03 增强：
    - `file_type` + `filename`：给通知挂真实附件（预览链路可用）；
    - `apply_linkage`：类型属「废止/替换」且已发布时，按生产同一函数
      reconcile_auto_expire_linkage 截断关联资料的 effective_to。
    - 快照展开**排除管理员**：管理员不是阅读对象（与生产口径一致）。
    """
    notice = Notice(
        title=title,
        content=content,
        notice_type=notice_type,
        status=status,
        published_at=published_at,
        effective_at=published_at,
        deadline_at=deadline_at,
        created_by=1,  # admin
    )
    db.add(notice)
    db.flush()

    # 设置目标
    if target_all:
        nt = NoticeTarget(notice_id=notice.id, target_type="all")
        db.add(nt)
    if target_users:
        for uid in target_users:
            nt = NoticeTarget(notice_id=notice.id, target_type="user", user_id=uid)
            db.add(nt)
    if target_departments:
        for did in target_departments:
            nt = NoticeTarget(notice_id=notice.id, target_type="department", department_id=did)
            db.add(nt)

    # 关联资料
    if document_ids:
        for doc_id in document_ids:
            assoc = notice_documents.insert().values(notice_id=notice.id, document_id=doc_id)
            db.execute(assoc)

    # 通知附件（2026-11-03）：真实落盘，preview/* 端点可用
    if file_type and filename:
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        internal_name = f"notice_{unique_id}_{file_type}.{file_type}"
        size_bytes, sha256 = _write_real_file(
            file_type, internal_name, title, owner="notices"
        )
        nf = FileRecord(
            owner_type="notice",
            owner_id=notice.id,
            original_filename=filename,
            internal_name=internal_name,
            file_type=file_type,
            extension=f".{file_type}",
            mime_type=FILE_MIME_TYPES[file_type],
            size_bytes=size_bytes,
            sha256=sha256,
            storage_rel_path=f"notices/{internal_name[:2]}/{internal_name}",
            status="active",
            uploaded_by=1,
        )
        db.add(nf)
        db.flush()
        notice.file_id = nf.id
        db.flush()

    # 创建阅读快照（发布时物化）
    if status == "published":
        all_target_users = set()
        if target_all:
            from sqlalchemy import select
            users = db.execute(
                select(User).where(
                    User.is_active.is_(True),
                    User.role != User.ROLE_ADMIN,   # 2026-11-03：管理员不进阅读对象
                )
            ).scalars().all()
            all_target_users.update(u.id for u in users)
        if target_users:
            all_target_users.update(target_users)
        if target_departments:
            from sqlalchemy import select
            for did in target_departments:
                rows = db.execute(
                    select(user_departments.c.user_id).where(
                        user_departments.c.department_id == did
                    )
                ).scalars().all()
                all_target_users.update(rows)

        # 统一在出口排除管理员（2026-11-04 修正）：
        # 之前只改了 target_all 分支，target_departments / target_users
        # 仍会把管理员（admin_demo / admin2_demo 都在研发部）加进来，
        # 导致管理员工作台出现永远清不掉的未读。
        from sqlalchemy import select as _select

        admin_ids = set(
            db.execute(
                _select(User.id).where(User.role == User.ROLE_ADMIN)
            ).scalars().all()
        )
        all_target_users -= admin_ids

        for uid in all_target_users:
            nr = NoticeRead(
                notice_id=notice.id,
                user_id=uid,
                read_at=datetime.now() if uid in (read_users or []) else None,
            )
            db.add(nr)

    # 废止/替换联动（2026-11-03）：与生产同一函数，幂等
    if apply_linkage and status == "published" and document_ids:
        from sqlalchemy import select

        from app.services.notice_linkage import AUTO_EXPIRE_TYPES, reconcile_auto_expire_linkage

        if notice_type in AUTO_EXPIRE_TYPES:
            admin_user = db.execute(
                select(User).where(User.role == User.ROLE_ADMIN)
            ).scalars().first()
            db.flush()
            reconcile_auto_expire_linkage(
                db,
                notice=notice,
                current_user=admin_user,
                via="seed_demo",
                reason="种子脚本模拟废止/替换联动",
            )

    return notice.id


def seed_data():
    """灌入种子数据。"""
    db = SessionLocal()
    manifest: dict[str, list[int]] = {
        "users": [],
        "departments": [],
        "categories": [],
        "tags": [],
        "documents": [],
        "notices": [],
        "file_records": [],
    }

    try:
        print("=" * 60)
        print("开始灌入 DEMO 种子数据...")
        print("=" * 60)

        # 1. 部门
        print("\n[1/7] 创建部门...")
        depts = {
            "研发部": get_or_create_department(db, f"{SEED_PREFIX}研发部"),
            "财务部": get_or_create_department(db, f"{SEED_PREFIX}财务部"),
            "人事部": get_or_create_department(db, f"{SEED_PREFIX}人事部"),
            "行政部": get_or_create_department(db, f"{SEED_PREFIX}行政部"),
        }
        manifest["departments"].extend(depts.values())
        print(f"  ✓ 创建/确认 {len(depts)} 个部门")

        # 2. 用户
        print("\n[2/7] 创建用户...")
        # 管理员
        admin1 = get_or_create_user(db, "admin_demo", "演示管理员", "admin", [depts["研发部"]])
        admin2 = get_or_create_user(db, "admin2_demo", "副管理员", "admin", [depts["财务部"]])
        manifest["users"].extend([admin1, admin2])

        # 普通用户（不同部门）
        users = []
        users.append(get_or_create_user(db, "user_rnd_1", "研发张三", "user", [depts["研发部"]]))
        users.append(get_or_create_user(db, "user_rnd_2", "研发李四", "user", [depts["研发部"]]))
        users.append(get_or_create_user(db, "user_fin_1", "财务王五", "user", [depts["财务部"]]))
        users.append(get_or_create_user(db, "user_hr_1", "人事赵六", "user", [depts["人事部"]]))
        users.append(get_or_create_user(db, "user_adm_1", "行政孙七", "user", [depts["行政部"]]))
        # 停用账号
        users.append(get_or_create_user(db, "user_inactive", "停用账号", "user", [depts["研发部"]], is_active=False))
        manifest["users"].extend(users)
        print(f"  ✓ 创建/确认 {len(users) + 2} 个用户（含 2 管理员、1 停用）")

        # 3. 分类（2026-11-03：扩充到 4 级）
        print("\n[3/7] 创建分类...")
        from sqlalchemy import update

        # 递归建树，返回 {"制度规范": id, "公司规章": id, "公司规章 / 劳动合同": id, ...}
        path_ids = create_category_tree(db, CATEGORY_TREE)

        # 停用分类（保留自旧版，用于验证"停用分类不可选"）
        stopped_id = get_or_create_category(
            db, f"{SEED_PREFIX}停用分类", sort_order=99
        )
        db.execute(update(Category).where(Category.id == stopped_id).values(is_active=0))

        # 同一分类在 path_ids 里有多个键（完整路径 + 短名），去重后记录
        manifest["categories"].extend(sorted(set(path_ids.values())))
        manifest["categories"].append(stopped_id)

        # 统计层级分布（验收时要断言"最深 4 级"）
        depth_count: dict[int, int] = {}
        for cid in set(path_ids.values()):
            c = db.get(Category, cid)
            d, cur = 1, c
            while cur.parent_id is not None:
                d += 1
                cur = db.get(Category, cur.parent_id)
            depth_count[d] = depth_count.get(d, 0) + 1
        print(
            f"  ✓ 创建/确认 {len(set(path_ids.values()))} 个分类，层级分布："
            + "、".join(f"{d}级×{n}" for d, n in sorted(depth_count.items()))
            + "（另加 1 个停用分类）"
        )

        # 4. 标签
        print("\n[4/7] 创建标签...")
        tags = {}
        tag_names = ["财务", "人事", "安全", "合规", "保密", "流程", "模板", "归档"]
        for name in tag_names:
            tags[name] = get_or_create_tag(db, f"{SEED_PREFIX}{name}")
        manifest["tags"].extend(tags.values())
        print(f"  ✓ 创建/确认 {len(tags)} 个标签")

        # 5. 资料（各种状态）
        print("\n[5/7] 创建资料...")
        # 兼容旧引用：cats 取一级分类，sub_cats 取「末段名 → id」
        cats = {k: v for k, v in path_ids.items() if "/" not in k}
        sub_cats = {k.split(" / ")[-1]: v for k, v in path_ids.items() if "/" in k}
        docs = []
        today = date.today()

        # 待生效（未来生效）
        docs.append(create_document(
            db, f"{SEED_PREFIX}待生效制度",
            sub_cats["公司规章"], [tags["流程"]],
            effective_from=today + timedelta(days=30),
            effective_to=None,
            file_type="pdf", filename="待生效制度.pdf",
        ))

        # 生效中（暂无失效日期）
        docs.append(create_document(
            db, f"{SEED_PREFIX}员工手册",
            sub_cats["公司规章"], [tags["人事"], tags["合规"]],
            effective_from=today - timedelta(days=365),
            effective_to=None,
            allow_download=True,
            file_type="docx", filename="员工手册.docx",
            # 演示附件：主文件 DOCX 也能挂一份 PPT 原稿
            attachment_type="pptx", attachment_filename="员工手册宣讲稿.pptx",
        ))

        # 生效中（有失效日期）
        docs.append(create_document(
            db, f"{SEED_PREFIX}开发规范v2",
            sub_cats["开发规范"], [tags["安全"]],
            effective_from=today - timedelta(days=180),
            effective_to=today + timedelta(days=180),
            file_type="pdf", filename="开发规范v2.pdf",
            # 最典型的用法：预览 PDF、下载 Word 原稿
            attachment_type="docx", attachment_filename="开发规范v2（修订稿）.docx",
        ))

        # 即将到期（≤30天）
        docs.append(create_document(
            db, f"{SEED_PREFIX}临时安全指引",
            sub_cats["安全标准"], [tags["安全"], tags["合规"]],
            effective_from=today - timedelta(days=60),
            effective_to=today + timedelta(days=20),
            file_type="xlsx", filename="临时安全指引.xlsx",
        ))

        # 已过期
        docs.append(create_document(
            db, f"{SEED_PREFIX}旧版报销流程",
            sub_cats["流程规范"], [tags["财务"], tags["流程"]],
            effective_from=today - timedelta(days=400),
            effective_to=today - timedelta(days=30),
            file_type="docx", filename="旧版报销流程.docx",
        ))

        # 无文件资料
        docs.append(create_document(
            db, f"{SEED_PREFIX}纯文字通知模板",
            None, [tags["模板"]],
            effective_from=today - timedelta(days=100),
            effective_to=None,
        ))

        # 未分类资料
        docs.append(create_document(
            db, f"{SEED_PREFIX}未分类文档",
            None, [tags["归档"]],
            effective_from=today - timedelta(days=50),
            effective_to=None,
            file_type="xls", filename="未分类.xls",
        ))

        # 未打标签资料
        docs.append(create_document(
            db, f"{SEED_PREFIX}无标签文档",
            cats["行政管理"], [],
            effective_from=today - timedelta(days=50),
            effective_to=None,
            file_type="pdf", filename="无标签.pdf",
        ))

        # 不允许下载（仅预览）
        docs.append(create_document(
            db, f"{SEED_PREFIX}保密协议模板",
            sub_cats["公司规章"], [tags["保密"]],
            effective_from=today - timedelta(days=200),
            effective_to=None,
            allow_download=False,
            file_type="pdf", filename="保密协议模板.pdf",
        ))

        # 中文文件名
        docs.append(create_document(
            db, f"{SEED_PREFIX}中文文件名测试",
            cats["培训资料"], [],
            effective_from=today - timedelta(days=10),
            effective_to=None,
            file_type="pdf", filename="这是一个很长的中文文件名测试文档.pdf",
        ))

        # 归档资料
        docs.append(create_document(
            db, f"{SEED_PREFIX}已归档旧制度",
            sub_cats["公司规章"], [tags["归档"]],
            effective_from=today - timedelta(days=500),
            effective_to=today - timedelta(days=100),
            archived=True,
            file_type="pdf", filename="已归档.pdf",
        ))

        # ---- 2026-11-03 新增：图片资料 + 三种可见范围 ----
        # 用户反馈「资料类型不全」：补 5 种图片格式。
        # 同时补齐 audience_scope 三种取值，用于交叉验证可见性。
        path = path_ids  # 简写
        docs.append(create_document(
            db, f"{SEED_PREFIX}安全通道示意图",
            path["办公用品"], [tags["安全"]],
            effective_from=today - timedelta(days=5), effective_to=None,
            file_type="png", filename="安全通道示意图.png",
            audience_scope="all",
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}组织架构图",
            path["公司规章"], [tags["人事"]],
            effective_from=today - timedelta(days=90), effective_to=None,
            file_type="jpg", filename="组织架构图.jpg",
            audience_scope="all",
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}会议室布局",
            path["会议室预定"], [],
            effective_from=today - timedelta(days=20), effective_to=None,
            file_type="jpeg", filename="会议室布局.jpeg",
            audience_scope="department",
            audience_department_ids=[depts["行政部"]],
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}报销操作动图",
            path["差旅报销"], [tags["财务"], tags["流程"]],
            effective_from=today - timedelta(days=40), effective_to=None,
            file_type="gif", filename="报销操作动图.gif",
            audience_scope="department",
            # 多部门并集：财务部 + 人事部
            audience_department_ids=[depts["财务部"], depts["人事部"]],
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}首页截图",
            path["组件与样式"], [],
            effective_from=today - timedelta(days=7), effective_to=None,
            file_type="webp", filename="首页截图.webp",
            audience_scope="user",
            audience_user_ids=[users[0]],  # user_rnd_1
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}研发规范附件",
            path["接口与错误码"], [tags["安全"]],
            effective_from=today - timedelta(days=120),
            effective_to=today + timedelta(days=15),  # 即将到期
            file_type="pdf", filename="研发规范附件.pdf",
            audience_scope="department",
            audience_department_ids=[depts["研发部"]],
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}年度内审纪要",
            path["年度内审"], [tags["归档"]],
            effective_from=today - timedelta(days=30), effective_to=None,
            file_type="docx", filename="年度内审纪要.docx",
            audience_scope="user",
            audience_user_ids=[users[2], users[3]],  # user_fin_1, user_hr_1
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}消防演练签到表",
            path["消防演练"], [tags["安全"]],
            effective_from=today - timedelta(days=60), effective_to=None,
            file_type="xlsx", filename="消防演练签到表.xlsx",
            audience_scope="department",
            audience_department_ids=[depts["人事部"]],
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}实习生协议模板",
            path["实习生协议"], [tags["人事"], tags["模板"]],
            effective_from=today + timedelta(days=15), effective_to=None,
            file_type="xls", filename="实习生协议模板.xls",
            audience_scope="all",
        ))
        docs.append(create_document(
            db, f"{SEED_PREFIX}密码令牌规范",
            path["密码与令牌"], [tags["安全"], tags["保密"]],
            effective_from=today - timedelta(days=200), effective_to=None,
            file_type="pdf", filename="密码令牌规范.pdf",
            audience_scope="user",
            audience_user_ids=[users[1]],  # user_rnd_2
        ))

        # 专供「废止/替换」联动演示的资料（2026-11-03）：
        # 联动会把 effective_to 截断成通知生效日，因此不能用上面那些
        # 需要保持「生效中」状态的资料，否则演示数据的状态分布会失真。
        doc_for_replace = create_document(
            db, f"{SEED_PREFIX}待替换的旧版操作手册",
            path["公司规章"], [tags["流程"]],
            effective_from=today - timedelta(days=300), effective_to=None,
            file_type="pdf", filename="旧版操作手册.pdf",
        )
        doc_for_abolish = create_document(
            db, f"{SEED_PREFIX}待废止的旧版考勤办法",
            path["公司规章"], [tags["人事"]],
            effective_from=today - timedelta(days=400), effective_to=None,
            file_type="docx", filename="旧版考勤办法.docx",
        )
        docs.extend([doc_for_replace, doc_for_abolish])

        manifest["documents"].extend(docs)
        print(f"  ✓ 创建 {len(docs)} 个资料（覆盖 9 种文件类型 + 3 种可见范围 + 联动专用）")

        # 6. 通知（各种类型和阅读状态）
        print("\n[6/7] 创建通知...")
        # 词表守卫：管理页可删除通知类型（如 GUI 测试删过「替换」），而本脚本
        # 直接写 notice_type 字符串——先确保内置六类齐全，避免词表与数据漂移
        from sqlalchemy import select

        from app.models.notice import DEFAULT_NOTICE_TYPES, NoticeType

        existing_type_names = {
            name
            for (name,) in db.execute(select(NoticeType.name)).all()
        }
        for i, type_name in enumerate(DEFAULT_NOTICE_TYPES):
            if type_name not in existing_type_names:
                db.add(NoticeType(name=type_name, sort_order=i, is_active=True))
                print(f"  + 补建内置通知类型「{type_name}」")
        db.commit()

        notices = []
        now = datetime.now()

        # 草稿状态已于 2026-11-04 下线（迁移 011 收窄 CHECK），
        # 演示数据不能再造 draft 行；保留一条普通已发布通知占位。
        notices.append(create_notice(
            db, f"{SEED_PREFIX}简明通知", "这是纯文字通知，无附件",
            "发布", "published",
            target_all=True,
        ))

        # 已发布（部分已读）
        n2 = create_notice(
            db, f"{SEED_PREFIX}重要制度发布", "请阅读新发布的员工手册",
            "发布", "published",
            target_users=users[:5],  # 前5个用户
            document_ids=[docs[1]],  # 关联员工手册
            published_at=now - timedelta(days=5),
            deadline_at=now + timedelta(days=10),
            read_users=users[:3],  # 前3个已读
        )
        notices.append(n2)

        # 已发布（部门范围）
        notices.append(create_notice(
            db, f"{SEED_PREFIX}一般通知", "仅供参考",
            "其他", "published",
            target_departments=[depts["研发部"]],
            published_at=now - timedelta(days=2),
            read_users=users[:2],
        ))

        # 全体通知
        notices.append(create_notice(
            db, f"{SEED_PREFIX}全体公告", "全员须知",
            "发布", "published",
            target_all=True,
            published_at=now - timedelta(days=10),
            deadline_at=now + timedelta(days=5),
            read_users=users[:4],
        ))

        # 指定用户通知
        notices.append(create_notice(
            db, f"{SEED_PREFIX}定向通知", "仅发给特定人员",
            "修订", "published",
            target_users=[users[0], users[2]],
            published_at=now - timedelta(days=1),
        ))

        # 已过截止时间的通知
        notices.append(create_notice(
            db, f"{SEED_PREFIX}过期通知", "已过期",
            "废止", "published",
            target_all=True,
            published_at=now - timedelta(days=30),
            deadline_at=now - timedelta(days=10),
            read_users=users[:3],
        ))

        # 纯文字通知
        notices.append(create_notice(
            db, f"{SEED_PREFIX}纯文字通知", "这是一条纯文字通知，没有附件",
            "其他", "published",
            target_all=True,
            published_at=now - timedelta(hours=12),
        ))

        # 关联多资料的通知
        notices.append(create_notice(
            db, f"{SEED_PREFIX}多资料关联通知", "涉及多个资料",
            "替换", "published",
            target_users=users[:3],
            document_ids=[docs[0], docs[2], docs[3]],
            published_at=now - timedelta(days=3),
            read_users=[users[0]],
        ))

        # ---- 2026-11-03 新增：通知矩阵（类型 × scope × 状态 × 阅读进度） ----
        # 用户反馈「通知数量太少」。以下补齐到 22+ 条，覆盖：
        #   6 种内置类型 + 2 种自定义 / 3 种 scope（含多部门并集）
        #   3 种状态 / 3 档阅读进度 / 4 种附件 / 废止·替换联动
        for custom_name in ("紧急通报", "年度检查"):
            get_or_create_notice_type(db, custom_name)
            manifest.setdefault("notice_types", []).append(custom_name)

        # —— 发布类型：4 条，覆盖 0 人已读 / 部分 / 全部 / 带 png 附件 ——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}发布-全员无已读", "全体可见，尚无人阅读",
            "发布", "published", target_all=True,
            published_at=now - timedelta(hours=6),
        ))
        n_pub_dept = create_notice(
            db, f"{SEED_PREFIX}发布-研发部（带PNG）", "研发部门通知，含图片附件",
            "发布", "published", target_departments=[depts["研发部"]],
            published_at=now - timedelta(days=1),
            read_users=users[:1],
            file_type="png", filename="通知配图.png",
        )
        notices.append(n_pub_dept)
        notices.append(create_notice(
            db, f"{SEED_PREFIX}发布-研发+财务并集", "多部门并集通知",
            "发布", "published",
            target_departments=[depts["研发部"], depts["财务部"]],
            published_at=now - timedelta(days=4),
            read_users=users[:3],  # 部分已读
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}发布-全部已读", "所有人都读过了",
            "发布", "published", target_users=users[:3],
            published_at=now - timedelta(days=8),
            read_users=users[:3],  # 全部已读
        ))

        # —— 修订类型：覆盖 published / cancelled（draft 已下线）——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}修订-面向全体", "修订说明，面向全体用户",
            "修订", "published", target_all=True,
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}修订-定向已发布", "发给指定两人",
            "修订", "published", target_users=[users[0], users[2]],
            published_at=now - timedelta(days=2),
            read_users=[users[0]],
            file_type="docx", filename="修订说明.docx",
        ))
        cancelled_id = create_notice(
            db, f"{SEED_PREFIX}修订-已取消", "已取消，用户不可见",
            "修订", "cancelled", target_all=True,
            published_at=now - timedelta(days=20),
        )
        notices.append(cancelled_id)

        # —— 替换类型：3 条，关联资料触发截断联动 ——
        n_replace_all = create_notice(
            db, f"{SEED_PREFIX}替换-全员（触发截断）", "新版替换旧版，旧版即刻失效",
            "替换", "published", target_all=True,
            document_ids=[doc_for_replace],  # 专供替换联动的资料
            published_at=now - timedelta(days=3),
            read_users=users[:2],
            apply_linkage=True,
        )
        notices.append(n_replace_all)
        notices.append(create_notice(
            db, f"{SEED_PREFIX}替换-财务部", "财务流程替换",
            "替换", "published", target_departments=[depts["财务部"]],
            document_ids=[doc_for_replace],  # 同上（第二次替换仍幂等）
            published_at=now - timedelta(days=6),
            read_users=[],
            apply_linkage=True,
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}替换-定向", "定向替换说明",
            "替换", "published", target_users=[users[0], users[1]],
            published_at=now - timedelta(days=9),
            read_users=[users[0]],
        ))

        # —— 废止类型：3 条，含 deadline 已过 ——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}废止-全员（触发截断）", "旧制度即刻废止",
            "废止", "published", target_all=True,
            document_ids=[doc_for_abolish],  # 专供废止联动的资料
            published_at=now - timedelta(days=25),
            read_users=users[:3],
            apply_linkage=True,
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}废止-定向", "定向废止通知",
            "废止", "published", target_users=[users[2]],
            published_at=now - timedelta(days=12),
            read_users=[],
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}废止-已过截止", "截止时间已过，仍可查看",
            "废止", "published", target_departments=[depts["人事部"]],
            published_at=now - timedelta(days=45),
            deadline_at=now - timedelta(days=10),
            read_users=[],
        ))

        # —— 延期类型：2 条，不触发截断 ——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}延期-全员", "有效期延长，不截断任何资料",
            "延期", "published", target_all=True,
            document_ids=[doc_for_replace],  # 延期不截断，验证资料保持有效
            published_at=now - timedelta(days=5),
            read_users=users[:2],
            apply_linkage=True,  # 延期不在 AUTO_EXPIRE_TYPES 内，不会截断
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}延期-行政部", "行政物品领用延期",
            "延期", "published", target_departments=[depts["行政部"]],
            published_at=now - timedelta(days=11),
            read_users=[],
        ))

        # —— 其他类型：3 条 ——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}其他-全员未读", "杂项通知",
            "其他", "published", target_all=True,
            published_at=now - timedelta(hours=2),
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}其他-定向全读", "定向杂项，全部已读",
            "其他", "published", target_users=users[:2],
            published_at=now - timedelta(days=15),
            read_users=users[:2],
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}其他-带XLSX附件", "附件为 Excel 的通知",
            "其他", "published", target_all=True,
            file_type="xlsx", filename="附表.xlsx",
        ))

        # —— 附件齐全：补一条 PDF 附件的通知（其余三种已在上方）——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}其他-带PDF附件", "通知附件为 PDF 的文件通知",
            "其他", "published", target_all=True,
            published_at=now - timedelta(days=2),
            read_users=users[:1],
            file_type="pdf", filename="通知正文.pdf",
        ))

        # —— 自定义类型：2 条 ——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}紧急通报-全员", "自定义类型：紧急通报",
            "紧急通报", "published", target_all=True,
            published_at=now - timedelta(days=1),
            read_users=users[:4],
        ))
        notices.append(create_notice(
            db, f"{SEED_PREFIX}年度检查-定向", "自定义类型：年度检查",
            "年度检查", "published", target_users=[users[0], users[1]],
            published_at=now - timedelta(days=7),
        ))

        # —— 已取消的发布通知 ——
        notices.append(create_notice(
            db, f"{SEED_PREFIX}发布-已取消", "已取消，仅管理员可见",
            "发布", "cancelled", target_all=True,
            published_at=now - timedelta(days=30),
        ))

        manifest["notices"].extend(notices)
        print(
            f"  ✓ 创建 {len(notices)} 个通知"
            "（6 内置类型 + 2 自定义 / 3 scope / 3 状态 / 3 档阅读进度 / 4 种附件）"
        )

        # 7. 制造回收站记录（文件替换痕迹）
        print("\n[7/7] 制造回收站记录...")
        from app.models.backup import RecycleBin

        # 回收站模拟旧文件：同样真实落盘（否则回收站「恢复」会报文件不存在）
        import uuid

        def _demo_recycled_file(doc_id: int, filename: str, file_type: str,
                                title: str) -> FileRecord:
            mime = FILE_MIME_TYPES[file_type]
            unique_id = uuid.uuid4().hex[:8]
            internal_name = f"demo_old_{unique_id}_{file_type}.{file_type}"
            size_bytes, sha256 = _write_real_file(file_type, internal_name, title)
            fr = FileRecord(
                owner_type="document",
                owner_id=doc_id,
                original_filename=filename,
                internal_name=internal_name,
                file_type=file_type,
                extension=f".{file_type}",
                mime_type=mime,
                size_bytes=size_bytes,
                sha256=sha256,
                storage_rel_path=f"documents/{internal_name[:2]}/{internal_name}",
                status="recycled",
                uploaded_by=admin1,
            )
            db.add(fr)
            db.flush()
            return fr

        fr_old1 = _demo_recycled_file(docs[1], "员工手册_v1.docx", "docx", "员工手册")
        fr_old2 = _demo_recycled_file(docs[2], "开发规范v1.pdf", "pdf", "开发规范")
        fr_old3 = _demo_recycled_file(docs[4], "旧版报销流程_old.docx", "docx", "旧版报销流程")

        manifest["file_records"].extend([fr_old1.id, fr_old2.id, fr_old3.id])

        # 模拟文件被替换的回收站记录
        rb1 = RecycleBin(
            document_id=docs[1],  # 员工手册
            file_record_id=fr_old1.id,
            reason="replaced",
            origin_rel_path=fr_old1.storage_rel_path,
            deleted_by=admin1,
            deleted_at=now - timedelta(days=10),
        )
        db.add(rb1)

        # 已恢复的记录
        rb2 = RecycleBin(
            document_id=docs[2],
            file_record_id=fr_old2.id,
            reason="replaced",
            origin_rel_path=fr_old2.storage_rel_path,
            deleted_by=admin1,
            deleted_at=now - timedelta(days=20),
            restored_at=now - timedelta(days=15),
            restored_by=admin1,
        )
        db.add(rb2)

        # 已永久删除的记录
        rb3 = RecycleBin(
            document_id=docs[4],
            file_record_id=fr_old3.id,
            reason="deleted",
            origin_rel_path=fr_old3.storage_rel_path,
            deleted_by=admin1,
            deleted_at=now - timedelta(days=30),
            purged_at=now - timedelta(days=25),
            purged_by=admin1,
        )
        db.add(rb3)

        db.commit()
        print("  ✓ 创建 3 条回收站记录（含已恢复、已永久删除）")

        # 保存清单
        with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        print("\n" + "=" * 60)
        print("✅ 种子数据灌入完成！")
        print("=" * 60)
        print("\n数据概况：")
        print(f"  - 部门：{len(manifest['departments'])} 个")
        print(f"  - 用户：{len(manifest['users'])} 个（含 2 管理员、1 停用）")
        print(f"  - 分类：{len(manifest['categories'])} 个（含 1 停用）")
        print(f"  - 标签：{len(manifest['tags'])} 个")
        print(f"  - 资料：{len(manifest['documents'])} 个")
        print(f"  - 通知：{len(manifest['notices'])} 个")
        print(f"\n清单已保存到：{MANIFEST_FILE}")
        print("\n登录信息：")
        print("  - 管理员：admin_demo / Demo@123456")
        print("  - 普通用户：user_rnd_1 / Demo@123456")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 种子数据灌入失败：{e}", file=sys.stderr)
        raise
    finally:
        db.close()


def _sweep_demo_prefix_content(db: Any) -> None:
    """全量清扫测试前缀的内容实体（资料/通知及从属数据，不依赖 manifest）。

    背景：seed 曾被重复执行，manifest 只记录最后一批，前批内容残留；
    另有 GUI 验证/浏览器实测产物引用种子用户，不清则用户删除触发外键失败。
    本清扫处理标题以测试前缀开头的资料与通知及其从属数据；物理文件只删
    internal_name 以 demo_ 开头的种子演示文件，绝不触碰其他业务数据（§26）。
    """
    from sqlalchemy import delete, or_, select

    title_prefixes = [
        f"{SEED_PREFIX}%",
        "GUI验证%",
        "浏览器实测-%",
        "EXTREME-%",  # extreme_samples_check 预演产物（资料无物理删除，逐轮残留）
        "巡检-%",     # 人工巡检产物
    ]

    demo_doc_ids = db.execute(
        select(Document.id).where(
            or_(*[Document.title.like(p) for p in title_prefixes])
        )
    ).scalars().all()
    demo_notice_ids = db.execute(
        select(Notice.id).where(
            or_(*[Notice.title.like(p) for p in title_prefixes])
        )
    ).scalars().all()
    demo_file_ids: list[int] = []
    if demo_doc_ids:
        demo_file_ids = db.execute(
            select(FileRecord.id).where(
                FileRecord.owner_type == "document",
                FileRecord.owner_id.in_(demo_doc_ids),
            )
        ).scalars().all()
    if demo_notice_ids:
        # 通知附件也是种子产物：只清文档侧会残留 uploaded_by 引用， users 删除即外键失败
        notice_file_ids = db.execute(
            select(FileRecord.id).where(
                FileRecord.owner_type == "notice",
                FileRecord.owner_id.in_(demo_notice_ids),
            )
        ).scalars().all()
        demo_file_ids = list(demo_file_ids) + list(notice_file_ids)

    # 物理清理种子演示文件（仅 internal_name 以 demo_ 开头的）
    from app.core.config import get_settings
    removed_files = 0
    if demo_file_ids:
        for rel in db.execute(
            select(FileRecord.storage_rel_path).where(FileRecord.id.in_(demo_file_ids))
        ).scalars().all():
            if rel and Path(rel).name.startswith("demo_"):
                p = get_settings().data_dir / rel
                if p.is_file():
                    p.unlink()
                    removed_files += 1

    if demo_notice_ids:
        db.execute(delete(NoticeRead).where(NoticeRead.notice_id.in_(demo_notice_ids)))
        db.execute(delete(NoticeTarget).where(NoticeTarget.notice_id.in_(demo_notice_ids)))
        db.execute(delete(notice_documents).where(
            notice_documents.c.notice_id.in_(demo_notice_ids)))
        # 2026-11-03：通知附件的 file_id 是外键，置空后才能删文件记录
        from sqlalchemy import update as sa_update
        db.execute(
            sa_update(Notice)
            .where(Notice.id.in_(demo_notice_ids))
            .values(file_id=None)
        )
    if demo_doc_ids:
        db.execute(delete(document_tags).where(
            document_tags.c.document_id.in_(demo_doc_ids)))
        # 反向关联也要拆：非 DEMO 前缀的残留通知（如 GUI 验证/浏览器实测产物）
        # 可能引用 DEMO 资料，不拆则下方删文档触发外键失败
        db.execute(delete(notice_documents).where(
            notice_documents.c.document_id.in_(demo_doc_ids)))
        # 可见范围目标行（2026-11-03 新增）：document_targets.document_id
        # 外键引用 documents，不先拆则删资料必然外键失败
        from app.models.document import DocumentTarget
        db.execute(delete(DocumentTarget).where(
            DocumentTarget.document_id.in_(demo_doc_ids)))
        from app.models.backup import RecycleBin
        rb_cond = RecycleBin.document_id.in_(demo_doc_ids)
        if demo_file_ids:
            rb_cond = or_(
                rb_cond,
                RecycleBin.file_record_id.in_(demo_file_ids),
                # 2026-11-06：附件单独删除/替换产生的条目只填
                # attachment_file_record_id，漏掉这里会残留外键引用，
                # 后续删 file_records 直接 IntegrityError。
                RecycleBin.attachment_file_record_id.in_(demo_file_ids),
            )
        db.execute(delete(RecycleBin).where(rb_cond))
    # 顺序约束：documents.current_file_id 外键引用 file_records，
    # 必须先删资料、后删文件记录
    if demo_doc_ids:
        db.execute(delete(Document).where(Document.id.in_(demo_doc_ids)))
    if demo_file_ids:
        db.execute(delete(FileRecord).where(FileRecord.id.in_(demo_file_ids)))
    if demo_notice_ids:
        db.execute(delete(Notice).where(Notice.id.in_(demo_notice_ids)))
        # 通知删完，其附件文件记录才能删（file_id 已置空）
        if demo_file_ids:
            db.execute(delete(FileRecord).where(FileRecord.id.in_(demo_file_ids)))
    print(f"  ✓ 前缀清扫：资料 {len(demo_doc_ids)}、通知 {len(demo_notice_ids)}、"
          f"文件记录 {len(demo_file_ids)}（物理删除演示文件 {removed_files} 个）")


def clean_seed_data():
    """清除本次种子数据。"""
    if not MANIFEST_FILE.exists():
        print(f"⚠️  未找到清单文件：{MANIFEST_FILE}")
        print("   可能尚未灌入种子数据，或已被清除。")
        return

    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    db = SessionLocal()
    try:
        print("=" * 60)
        print("开始清除 DEMO 种子数据...")
        print("=" * 60)

        from sqlalchemy import delete

        # 阶段 0：DEMO- 前缀全扫（覆盖 manifest 之外的历批残留，如重复批次）
        _sweep_demo_prefix_content(db)

        # 按依赖逆序删除
        # 1. 通知阅读快照
        if manifest.get("notices"):
            stmt = delete(NoticeRead).where(NoticeRead.notice_id.in_(manifest["notices"]))
            db.execute(stmt)
            print(f"  ✓ 删除 {len(manifest['notices'])} 个通知的阅读快照")

        # 2. 通知目标
        if manifest.get("notices"):
            stmt = delete(NoticeTarget).where(NoticeTarget.notice_id.in_(manifest["notices"]))
            db.execute(stmt)

        # 3. 通知-资料关联
        if manifest.get("notices"):
            stmt = delete(notice_documents).where(
                notice_documents.c.notice_id.in_(manifest["notices"])
            )
            db.execute(stmt)

        # 4. 通知
        if manifest.get("notices"):
            stmt = delete(Notice).where(Notice.id.in_(manifest["notices"]))
            db.execute(stmt)
            print(f"  ✓ 删除 {len(manifest['notices'])} 个通知")

        # 5. 资料-标签关联
        if manifest.get("documents"):
            stmt = delete(document_tags).where(
                document_tags.c.document_id.in_(manifest["documents"])
            )
            db.execute(stmt)

        # 5b. 资料可见范围目标行（2026-11-03 新增，外键引用 documents）
        if manifest.get("documents"):
            from app.models.document import DocumentTarget
            stmt = delete(DocumentTarget).where(
                DocumentTarget.document_id.in_(manifest["documents"])
            )
            db.execute(stmt)

        # 6. 回收站记录（与种子资料关联的，必须先删，因为它引用 file_record_id）
        if manifest.get("documents"):
            from app.models.backup import RecycleBin
            stmt = delete(RecycleBin).where(
                RecycleBin.document_id.in_(manifest["documents"]),
            )
            result = db.execute(stmt)
            deleted_rb = result.rowcount if hasattr(result, 'rowcount') else 0
            print(f"  ✓ 删除 {deleted_rb} 条关联的回收站记录")

        # 7. 资料（须先于文件记录删除：documents.current_file_id 引用 file_records）
        if manifest.get("documents"):
            stmt = delete(Document).where(Document.id.in_(manifest["documents"]))
            db.execute(stmt)
            print(f"  ✓ 删除 {len(manifest['documents'])} 个资料")

        # 7b. 通知附件外键置空（2026-11-03）
        # 通知可能在步骤 4 已删；但 manifest 之外的残留通知若仍引用种子文件，
        # 直接删 file_records 会外键失败。先统一置空再删。
        if manifest.get("file_records"):
            from sqlalchemy import update as sa_update
            seed_fids = list(manifest["file_records"])
            db.execute(
                sa_update(Notice)
                .where(Notice.file_id.in_(seed_fids))
                .values(file_id=None)
            )

        # 8. 文件记录（文档已删，current_file_id 引用解除）
        if manifest.get("file_records"):
            stmt = delete(FileRecord).where(FileRecord.id.in_(manifest["file_records"]))
            db.execute(stmt)

        # 9. 用户-部门关联
        if manifest.get("users"):
            stmt = delete(user_departments).where(
                user_departments.c.user_id.in_(manifest["users"])
            )
            db.execute(stmt)

        # 10. NoticeTarget（user_id 引用用户）- 删除所有指向种子用户的目标
        if manifest.get("users"):
            stmt = delete(NoticeTarget).where(NoticeTarget.user_id.in_(manifest["users"]))
            db.execute(stmt)

        # 10b. DocumentTarget（user_id 引用用户，2026-11-06 补）
        # 此前只按 document_id 删（步骤 7 之前），「资料可见范围 = 指定用户」
        # 且资料不在 manifest 里时，user_id 外键残留 → 删用户 IntegrityError。
        if manifest.get("users"):
            from app.models.document import DocumentTarget
            stmt = delete(DocumentTarget).where(
                DocumentTarget.user_id.in_(manifest["users"])
            )
            db.execute(stmt)

        # 11. NoticeRead（user_id 引用用户，应该在步骤1已删除，但再确认一次）
        if manifest.get("users"):
            stmt = delete(NoticeRead).where(NoticeRead.user_id.in_(manifest["users"]))
            db.execute(stmt)

        # 12. 审计日志（引用用户）
        if manifest.get("users"):
            from app.models.audit import AuditLog
            stmt = delete(AuditLog).where(AuditLog.user_id.in_(manifest["users"]))
            db.execute(stmt)

        # 13. 备份记录（created_by/restored_by 引用用户）
        if manifest.get("users"):
            from app.models.backup import BackupRecord
            stmt = delete(BackupRecord).where(
                BackupRecord.created_by.in_(manifest["users"])
            )
            db.execute(stmt)

        # 14. 回收站的 deleted_by/restored_by/purged_by（引用用户）- 删除所有由种子用户操作的记录
        if manifest.get("users"):
            from app.models.backup import RecycleBin
            from sqlalchemy import update
            stmt = update(RecycleBin).where(
                RecycleBin.deleted_by.in_(manifest["users"]) |
                RecycleBin.restored_by.in_(manifest["users"]) |
                RecycleBin.purged_by.in_(manifest["users"])
            ).values(
                deleted_by=None, restored_by=None, purged_by=None
            )
            db.execute(stmt)

        # 15. 文件记录的 uploaded_by（引用用户）
        if manifest.get("file_records"):
            from sqlalchemy import update
            stmt = update(FileRecord).where(FileRecord.id.in_(manifest["file_records"])).values(
                uploaded_by=None
            )
            db.execute(stmt)
        # 也处理非种子文件记录但由种子用户上传的
        if manifest.get("users"):
            from sqlalchemy import update
            stmt = update(FileRecord).where(FileRecord.uploaded_by.in_(manifest["users"])).values(
                uploaded_by=None
            )
            db.execute(stmt)

        # 16. 通知的 created_by/updated_by（引用用户）
        # 同步骤 17：不能只清 manifest 内的通知，否则「非种子通知、但由种子用户
        # 创建」的行仍持有外键 → 删用户直接 IntegrityError。
        if manifest.get("users"):
            from sqlalchemy import or_, update

            seed_users = list(manifest["users"])
            db.execute(
                update(Notice)
                .where(
                    or_(
                        Notice.created_by.in_(seed_users),
                        Notice.updated_by.in_(seed_users),
                    )
                )
                .values(created_by=None, updated_by=None)
            )
        if manifest.get("notices"):
            from sqlalchemy import update
            stmt = update(Notice).where(Notice.id.in_(manifest["notices"])).values(
                created_by=None, updated_by=None
            )
            db.execute(stmt)

        # 17. 资料的 created_by/updated_by/archived_by（引用用户）
        # 2026-11-06 修复：原先只清 manifest["documents"] 范围内的资料，
        # 漏掉了「非种子资料、但由种子用户创建」的行（例如手工用 admin_demo
        # 建过资料）。删用户时这些行仍持有外键 → IntegrityError → 整个 --clean 崩掉。
        if manifest.get("users"):
            from sqlalchemy import or_, update

            seed_users = list(manifest["users"])
            db.execute(
                update(Document)
                .where(
                    or_(
                        Document.created_by.in_(seed_users),
                        Document.updated_by.in_(seed_users),
                        Document.archived_by.in_(seed_users),
                    )
                )
                .values(created_by=None, updated_by=None, archived_by=None)
            )
        if manifest.get("documents"):
            from sqlalchemy import update
            stmt = update(Document).where(Document.id.in_(manifest["documents"])).values(
                created_by=None, updated_by=None, archived_by=None
            )
            db.execute(stmt)

        # 18. 用户
        if manifest.get("users"):
            stmt = delete(User).where(User.id.in_(manifest["users"]))
            db.execute(stmt)
            print(f"  ✓ 删除 {len(manifest['users'])} 个用户")

        # 19. 部门（先清理 NoticeTarget 中的 department 引用）
        if manifest.get("departments"):
            stmt = delete(NoticeTarget).where(
                NoticeTarget.department_id.in_(manifest["departments"])
            )
            db.execute(stmt)

        if manifest.get("departments"):
            stmt = delete(Department).where(Department.id.in_(manifest["departments"]))
            db.execute(stmt)
            print(f"  ✓ 删除 {len(manifest['departments'])} 个部门")

        # 12. 标签（可能被非种子资料引用，谨慎删除）
        # 只删除未被其他资料使用的种子标签
        if manifest.get("tags"):
            from sqlalchemy import select
            used_tags = db.execute(
                select(document_tags.c.tag_id).distinct()
            ).scalars().all()
            safe_to_delete = [tid for tid in manifest["tags"] if tid not in used_tags]
            if safe_to_delete:
                stmt = delete(Tag).where(Tag.id.in_(safe_to_delete))
                db.execute(stmt)
                print(f"  ✓ 删除 {len(safe_to_delete)} 个未使用的标签")

        # 20. 分类（可能被非种子资料引用，谨慎删除）
        if manifest.get("categories"):
            from sqlalchemy import select
            # 先清除所有引用这些分类的资料的 category_id（包括已归档和种子资料）
            from sqlalchemy import update
            stmt = update(Document).where(
                Document.category_id.in_(manifest["categories"])
            ).values(category_id=None)
            db.execute(stmt)
            
            # 现在可以安全删除所有种子分类
            safe_to_delete = manifest["categories"]

            if safe_to_delete:
                # 前置：把挂在待删种子分类下的**非种子子分类**提升为一级
                # （用户自建分类若挂在种子分类下，直接删会 FK 失败；升级保留用户数据）
                from sqlalchemy import update as sa_update
                affected_children = db.execute(
                    select(Category.id).where(
                        Category.parent_id.in_(safe_to_delete),
                        Category.id.notin_(safe_to_delete),
                    )
                ).scalars().all()
                if affected_children:
                    db.execute(
                        sa_update(Category)
                        .where(Category.id.in_(affected_children))
                        .values(parent_id=None)
                    )
                    db.flush()

                # 先删所有子分类（parent_id 不为 NULL）
                child_cats = db.execute(
                    select(Category.id).where(
                        Category.id.in_(safe_to_delete),
                        Category.parent_id.is_not(None),
                    )
                ).scalars().all()
                if child_cats:
                    stmt = delete(Category).where(Category.id.in_(child_cats))
                    db.execute(stmt)
                    db.flush()

                # 最后删一级分类
                root_cats = [cid for cid in safe_to_delete if cid not in child_cats]
                if root_cats:
                    stmt = delete(Category).where(Category.id.in_(root_cats))
                    db.execute(stmt)

                total_deleted = len(child_cats) + len(root_cats)
                print(f"  ✓ 删除 {total_deleted} 个未使用的分类")

        db.commit()

        # 删除清单文件
        MANIFEST_FILE.unlink(missing_ok=True)

        print("\n" + "=" * 60)
        print("✅ 种子数据清除完成！")
        print("=" * 60)

    except Exception as e:
        db.rollback()
        print(f"\n❌ 种子数据清除失败：{e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="种子数据脚本（幂等、可回滚）")
    parser.add_argument("--clean", action="store_true", help="清除本次种子数据")
    args = parser.parse_args()

    if args.clean:
        clean_seed_data()
    else:
        seed_data()
