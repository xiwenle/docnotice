"""M69 资料附件：主文件预览 PDF + 下载拿附件原稿（2026-11-06 新能力）。

背景：用户的工作流是自己把 Word/PPT 转成 PDF 当主文件上传，原稿作为附件。
本模块验证三件只有真实运行才知道的事：

1. 详情页「附件」区块真的渲染出来，且下载按钮指向附件端点；
2. 下载到的**字节确实是附件**（Word/PPT），不是眼前预览的 PDF；
3. 预览区仍然是 PDF iframe —— 附件不能把预览顶掉。

另附一条反向断言：`GET /notices/{id}` 的 `documents` 数组不含任何附件字段，
防止以后有人把附件事顺手加到通知序列化里（用户明确要求附件只挂资料）。
"""
from __future__ import annotations

import io
import urllib.request
import zipfile

from harness import REPORT, case, http

# ---------------------------------------------------------------------------
# 真实可解析的测试文件（不依赖 tests/ 包，E2E 独立运行）
# ---------------------------------------------------------------------------
def _pptx_bytes() -> bytes:
    """最小合法 PPTX（OPC 包），与 Seed/单测用同一结构。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sldIdLst/><p:sldSz cx="12192000" cy="6858000"/></p:presentation>',
        )
    return buf.getvalue()


def _docx_bytes() -> bytes:
    """最小合法 DOCX：zip 头 + [Content_Types].xml + word/document.xml。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>E2E 附件原稿</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def _pdf_bytes() -> bytes:
    """最小合法 PDF（pypdf 可解析、浏览器可预览）。"""
    from pypdf import PdfWriter

    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=595.28, height=841.89)
    w.add_metadata({"/Title": "E2E 附件主文件"})
    w.write(buf)
    return buf.getvalue()


def _multipart(path: str, token: str, filename: str, data: bytes, mime: str,
               field: str = "file"):
    boundary = "----E2EAttachmentBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        http.__globals__["BASE_URL"] + path,
        method="POST", data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def _get_raw(path: str, token: str) -> tuple[int, bytes]:
    req = urllib.request.Request(
        http.__globals__["BASE_URL"] + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


@case("Y01", "资料：PDF 主文件 + DOCX 附件，下载拿到 DOCX、预览仍是 PDF")
def a01(app):
    r = http("POST", "/api/v1/documents", app.admin.token, {
        "title": "E2E附件-预览PDF下载Word", "effective_from": "2026-01-01",
    })
    assert r["status"] in (200, 201), f"建资料失败 {r['status']}"
    did = r["body"]["id"]

    pdf = _pdf_bytes()
    docx = _docx_bytes()

    st, _ = _multipart(f"/api/v1/documents/{did}/file", app.admin.token,
                       "制度正文.pdf", pdf, "application/pdf")
    REPORT.check(st == 200, f"主文件 PDF 上传成功（{st}）")

    st, body = _multipart(f"/api/v1/documents/{did}/attachment", app.admin.token,
                          "制度原稿.docx", docx,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    REPORT.check(st == 200, f"附件 DOCX 上传成功（{st}, {body[:80]!r}）")

    # 1) /file 必须给附件字节
    st, dl = _get_raw(f"/api/v1/documents/{did}/file", app.admin.token)
    REPORT.check(st == 200 and dl == docx, "下载 /file 返回的是附件 DOCX 原稿")

    # 2) /attachment 也必须给附件
    st, dl2 = _get_raw(f"/api/v1/documents/{did}/attachment", app.admin.token)
    REPORT.check(st == 200 and dl2 == docx, "下载 /attachment 返回 DOCX 原稿")

    # 3) 预览必须仍是 PDF
    st, pv = _get_raw(f"/api/v1/documents/{did}/preview/pdf", app.admin.token)
    REPORT.check(st == 200 and pv.startswith(b"%PDF-"),
                 "预览 /preview/pdf 仍返回 PDF 主文件（未被附件顶掉）")

    # 4) 序列化里附件字段齐全
    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"]
    att = detail.get("attachment_file") or {}
    REPORT.check(att.get("file_type") == "docx", "详情 attachment_file.file_type = docx")
    REPORT.check(att.get("original_filename") == "制度原稿.docx", "附件原始文件名保留")
    REPORT.check(detail.get("has_attachment") is True, "详情 has_attachment = true")

    app.state["att_doc"] = did


@case("Y02", "资料：PPTX 只能作附件，不能作主文件")
def a02(app):
    did = app.state.get("att_doc")
    if not did:
        REPORT.fail("前置用例未建立资料")
        return

    pptx = _pptx_bytes()
    # 作主文件 → 415
    st, body = _multipart(f"/api/v1/documents/{did}/file", app.admin.token,
                          "汇报.pptx", pptx,
                          "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    REPORT.check(st == 415, f"pptx 作主文件被拒（{st}）")
    REPORT.note(f"拒绝原因：{body[:100]!r}")

    # 作附件 → 200（替换掉原来的 docx）
    st, body = _multipart(f"/api/v1/documents/{did}/attachment", app.admin.token,
                          "汇报.pptx", pptx,
                          "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    REPORT.check(st == 200, f"pptx 作附件成功（{st}）")

    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"]
    REPORT.check((detail.get("attachment_file") or {}).get("file_type") == "pptx",
                 "附件已替换为 pptx")


@case("Y03", "浏览器：详情页渲染「附件」区块，下载按钮指向附件端点")
def a03(app):
    did = app.state.get("att_doc")
    if not did:
        REPORT.fail("前置用例未建立资料")
        return

    s = app.admin
    s.goto(f"/documents/{did}")
    try:
        s.page.wait_for_selector(".attachment-card", timeout=15000)
        info = s.page.evaluate(
            "() => {"
            " const card = document.querySelector('.attachment-card');"
            " const link = card && card.querySelector('a[href$=\"/attachment\"]');"
            " const name = card && card.querySelector('.attachment-name');"
            " const badge = card && card.querySelector('.preview-file-type');"
            " const frame = document.querySelector('#preview-body iframe.pdf-frame');"
            " const src = frame && frame.getAttribute('src') || '';"
            " return {hasCard: !!card, link: !!link, href: link && link.getAttribute('href'),"
            "         name: name && name.textContent.trim(),"
            "         badge: badge && badge.textContent.trim(),"
            "         pdfFrame: !!frame, pdfBlob: src.indexOf('blob:') === 0};"
            "}")
        REPORT.check(info["hasCard"], "附件区块已渲染")
        REPORT.check(info["link"], "附件区有下载按钮")
        REPORT.check(
            bool(info["href"]) and info["href"].endswith("/attachment"),
            f"下载按钮指向 /attachment（实际 {info['href']}）",
        )
        REPORT.check(info["badge"] and "PPTX" in info["badge"].upper(),
                     f"类型徽章显示 PPTX（实际 {info['badge']}）")
        REPORT.check(info["name"] and "汇报" in info["name"],
                     f"附件文件名显示正确（实际 {info['name']}）")
        REPORT.check(info["pdfFrame"] and info["pdfBlob"],
                     "预览区仍是 PDF iframe（blob 源）")

        # 工具栏主下载按钮文案要说明默认下载拿到的是原稿附件（方案 A）
        toolbar = s.page.evaluate(
            "() => {const t=document.querySelector('.preview-toolbar');"
            " const a=t&&t.querySelector('a[href$=\"/file\"]');"
            " return a ? a.textContent.trim() : '';}")
        REPORT.check("默认下载" in toolbar and "原稿附件" in toolbar,
                     f"主下载按钮注明默认下载的是原稿附件（实际「{toolbar}」）")
    except Exception as e:  # noqa: BLE001
        REPORT.fail(f"附件区块渲染失败：{str(e)[:120]}")
    s.shot("M69_attachment_detail.png")


@case("Y04", "浏览器：普通用户能看附件区块但看不到管理按钮")
def a04(app):
    did = app.state.get("att_doc")
    if not did:
        REPORT.fail("前置用例未建立资料")
        return

    s = app.user
    s.goto(f"/documents/{did}")
    try:
        s.page.wait_for_selector(".attachment-card", timeout=15000)
        info = s.page.evaluate(
            "() => ({"
            " canDownload: !!document.querySelector('.attachment-card a[href$=\"/attachment\"]'),"
            " canReplace: !!document.getElementById('btn-replace-attachment'),"
            " canDelete: !!document.getElementById('btn-delete-attachment'),"
            " canAdd: !!document.getElementById('btn-upload-attachment')"
            "})")
        REPORT.check(info["canDownload"], "普通用户可见「下载此附件」入口")
        REPORT.check(not info["canReplace"], "普通用户看不到「替换」")
        REPORT.check(not info["canDelete"], "普通用户看不到「删除」")
        REPORT.check(not info["canAdd"], "普通用户看不到「添加附件」")
    except Exception as e:  # noqa: BLE001
        REPORT.fail(f"普通用户附件区异常：{str(e)[:120]}")
    s.shot("M69_attachment_user.png")


@case("Y05", "资料：删除附件后进回收站，资料本身状态不变")
def a05(app):
    did = app.state.get("att_doc")
    if not did:
        REPORT.fail("前置用例未建立资料")
        return

    before = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"]
    r = http("DELETE", f"/api/v1/documents/{did}/attachment", app.admin.token)
    REPORT.check(r["status"] == 200, f"删除附件成功（{r['status']}）")

    after = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"]
    REPORT.check(after.get("attachment_file") is None, "附件已从资料上摘下")
    REPORT.check(after.get("archived") == before.get("archived"),
                 "资料归档状态未变（删附件不得影响资料）")

    bin_body = http("GET", "/api/v1/recycle-bin?page_size=50", app.admin.token)["body"]
    items = (bin_body or {}).get("items") or []
    entry = next(
        (i for i in items
         if i.get("reason") == "attachment_deleted" and i.get("document_id") == did),
        None,
    )
    REPORT.check(entry is not None, "回收站出现 attachment_deleted 条目")
    if entry:
        REPORT.check(bool(entry.get("attachment_filename")), "回收站条目带附件文件名")


@case("Y06", "反向断言：通知序列化里不含任何附件字段")
def a06(app):
    """附件只挂资料（用户 2026-11-06 明确）。加这条防回归。"""
    body = http("GET", "/api/v1/notices?page_size=5", app.admin.token)["body"]
    items = (body or {}).get("items") or []
    if not items:
        REPORT.note("跳过：无通知数据")
        return
    nid = items[0]["id"]
    detail = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    leaked = [k for k in detail if "attachment" in k.lower()]
    REPORT.check(not leaked, f"通知详情不含附件字段（实际出现 {leaked}）")
    for doc in detail.get("documents") or []:
        bad = [k for k in doc if "attachment" in k.lower()]
        if bad:
            REPORT.fail(f"通知关联资料里混入附件字段：{bad}")
            return
    REPORT.check(True, "通知关联资料条目也不含附件字段")
