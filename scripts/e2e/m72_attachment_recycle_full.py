"""M72 资料附件 + 回收站全功能真浏览器专项。

覆盖 2026-11-06 附件槽与 2026-08-30 回收站契约，并把管理员操作与普通用户
看到的页面/下载/预览做交叉核对。所有数据由本模块创建，API 作为地面真值，
浏览器负责验证真实按钮、确认弹窗、页面恢复和不可见性。

不依赖 tests/ 包；真实文件字节由本模块生成。
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile

from harness import REPORT, SHOT_DIR, case, http


def _pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595.28, height=841.89)
    writer.add_metadata({"/Title": "M72 attachment recycle fixture"})
    writer.write(buf)
    return buf.getvalue()


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, value in files.items():
            zf.writestr(name, value)
    return buf.getvalue()


def _docx_bytes(marker: str = "M72 DOCX attachment") -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{marker}</w:t></w:r></w:p></w:body></w:document>"
        ),
    })


def _pptx_bytes() -> bytes:
    return _zip_bytes({
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            "</Relationships>"
        ),
        "ppt/presentation.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sldIdLst/><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'
        ),
    })


def _json(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "null")
    except Exception:  # noqa: BLE001
        return None


def _multipart(path: str, token: str, filename: str, data: bytes, mime: str):
    boundary = f"----M72Boundary{time.time_ns()}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        http.__globals__["BASE_URL"] + path,
        method="POST",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode(), {}


def _get_raw(path: str, token: str):
    req = urllib.request.Request(
        http.__globals__["BASE_URL"] + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode(), {}


def _create_doc(app, title: str):
    r = http("POST", "/api/v1/documents", app.admin.token, {
        "title": title,
        "summary": "M72 附件回收站专项合成资料",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "audience_scope": "all",
    })
    if r["status"] not in (200, 201):
        REPORT.fail(f"创建资料失败：{r['status']} {r['body']}")
        return None
    return (r.get("body") or {}).get("id")


def _upload_main(app, did: int, filename: str, data: bytes):
    st, body, headers = _multipart(
        f"/api/v1/documents/{did}/file", app.admin.token,
        filename, data, "application/pdf",
    )
    if st != 200:
        REPORT.fail(f"上传主文件失败：{st} {body[:120]!r}")
    return st, body, headers


def _upload_attachment(app, did: int, filename: str, data: bytes, mime: str):
    st, body, headers = _multipart(
        f"/api/v1/documents/{did}/attachment", app.admin.token,
        filename, data, mime,
    )
    if st != 200:
        REPORT.fail(f"上传附件失败：{st} {body[:120]!r}")
    return st, _json(body), headers


def _base_doc(app, key: str, label: str):
    title = f"E2E-M72-{label}-{time.time_ns()}"
    did = _create_doc(app, title)
    if not did:
        return None
    pdf = _pdf_bytes()
    docx = _docx_bytes(f"M72 {label}")
    if _upload_main(app, did, f"{label}-主文件.pdf", pdf)[0] != 200:
        return None
    if _upload_attachment(
        app, did, f"{label}-原稿.docx", docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )[0] != 200:
        return None
    app.state[key] = {"id": did, "title": title, "pdf": pdf, "docx": docx}
    return app.state[key]


def _rb_items(app, reason: str = "") -> list:
    path = "/api/v1/recycle-bin?page=1&page_size=100"
    if reason:
        path += f"&reason={reason}"
    body = http("GET", path, app.admin.token).get("body") or {}
    return body.get("items") or []


def _notice_items(app) -> list:
    body = http("GET", "/api/v1/recycle-bin/notices", app.admin.token).get("body") or {}
    return body.get("items") or []


def _open_recycle(app):
    s = app.admin
    s.goto("/admin/recycle-bin")
    try:
        s.page.wait_for_selector("#rb-tbody tr", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    s.page.wait_for_timeout(1200)
    return s


def _find_row(s, tbody: str, hint: str):
    try:
        rows = s.page.query_selector_all(f"{tbody} tr")
    except Exception:  # noqa: BLE001
        return None
    for row in rows:
        if hint in (row.inner_text() or ""):
            return row
    return None


def _row_button(s, tbody: str, action: str, hint: str):
    row = _find_row(s, tbody, hint)
    if not row:
        return None
    return row.query_selector(f'button[data-action="{action}"]')


def _confirm_modal(s, expected: str = ""):
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=8000)
    text = s.page.inner_text("#modal-root")
    if expected:
        REPORT.check(expected in text, f"确认弹窗含「{expected}」：{text[:120]!r}")
    input_box = s.page.query_selector("#modal-root #confirm-input")
    if input_box:
        s.page.fill("#modal-root #confirm-input", "M72 自动化回收站操作")
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(1200)


def _archive_ui(app, did: int):
    s = app.admin
    s.goto(f"/documents/{did}")
    s.page.wait_for_selector("#btn-archive", timeout=15000)
    s.page.click("#btn-archive")
    _confirm_modal(s)


def _notice_create(app, title: str):
    r = http("POST", "/api/v1/notices", app.admin.token, {
        "title": title,
        "content": "M72 回收站通知专项",
        "notice_type": "发布",
        "target": {"scope": "all"},
    })
    if r["status"] not in (200, 201):
        REPORT.fail(f"创建通知失败：{r['status']} {r['body']}")
        return None
    return (r.get("body") or {}).get("id")


def _notice_cancel(app, nid: int):
    r = http("POST", f"/api/v1/notices/{nid}/cancel", app.admin.token,
             {"reason": "M72 回收站专项取消"})
    return r["status"] in (200, 204)


def _audit_total(app, action: str) -> int:
    r = http("GET", f"/api/v1/audit-logs?page=1&page_size=100&action={action}",
             app.admin.token)
    return int((r.get("body") or {}).get("total") or 0)


def _user_detail(app, did: int):
    app.user.goto(f"/documents/{did}")
    try:
        app.user.page.wait_for_selector("#detail-root", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    return app.user.page
@case("Z01", "附件主文件+附件方案A：用户预览主PDF、默认下载附件、单一下载CTA")
def z01(app):
    fixture = _base_doc(app, "z01", "方案A")
    if not fixture:
        return
    did = fixture["id"]
    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token)
    att = (detail.get("body") or {}).get("attachment_file") or {}
    REPORT.check(detail["status"] == 200, f"资料详情可读：{detail['status']}")
    REPORT.check(att.get("file_type") == "docx", "附件类型为 DOCX")
    REPORT.check((detail.get("body") or {}).get("current_file", {}).get("file_type") == "pdf",
                 "主文件仍为 PDF")

    page = _user_detail(app, did)
    page.wait_for_selector(".attachment-card", timeout=15000)
    layout = page.evaluate(
        "() => ({"
        " defaultCount: document.querySelectorAll('.default-download').length,"
        " attLinks: document.querySelectorAll('.attachment-card a[href$=\"/attachment\"]').length,"
        " attCard: !!document.querySelector('.attachment-card'),"
        " mainText: document.querySelector('.default-download')?.innerText || '',"
        " frame: !!document.querySelector('#preview-body iframe.pdf-frame'),"
        " noExpiry: (document.body.innerText || '').includes('暂无失效日期'),"
        " forbiddenExpiry: /长期|永久有效|暂无已知失效日/.test(document.body.innerText || '')"
        "})")
    REPORT.check(layout["defaultCount"] == 1, f"默认下载主 CTA 数量=1：{layout['defaultCount']}")
    REPORT.check(layout["attLinks"] == 1, f"附件卡次级附件链接数量=1：{layout['attLinks']}")
    REPORT.check(layout["attCard"] and "原稿附件" in layout["mainText"],
                 f"默认下载明确标注原稿附件：{layout['mainText'][:80]!r}")
    REPORT.check(layout["noExpiry"] and not layout["forbiddenExpiry"],
                 "空失效日统一显示「暂无失效日期」且无禁用文案")
    REPORT.check(layout["frame"], "附件不参与预览，页面仍渲染 PDF 预览区")

    st, file_bytes, headers = _get_raw(f"/api/v1/documents/{did}/file", app.user.token)
    REPORT.check(st == 200 and file_bytes == fixture["docx"],
                 f"普通用户 /file 下载附件 DOCX：status={st}")
    cd = next((v for k, v in headers.items() if k.lower() == "content-disposition"), "")
    REPORT.check("attachment" in cd.lower(), "默认下载响应为 attachment")
    st, att_bytes, _ = _get_raw(f"/api/v1/documents/{did}/attachment", app.user.token)
    REPORT.check(st == 200 and att_bytes == fixture["docx"], "附件直链返回同一 DOCX")
    st, preview, _ = _get_raw(f"/api/v1/documents/{did}/preview/pdf", app.user.token)
    REPORT.check(st == 200 and preview.startswith(b"%PDF-"), "预览端点仍返回主 PDF")
    page.screenshot(path=str(SHOT_DIR / "Z01_attachment_scheme_a.png"))


@case("Z02", "附件替换为 PPTX：旧附件进回收站，用户下载/预览仍按方案A")
def z02(app):
    fixture = app.state.get("z01")
    if not fixture:
        REPORT.fail("前置 Z01 未建立资料")
        return
    did = fixture["id"]
    pptx = _pptx_bytes()
    st, body, _ = _upload_attachment(
        app, did, "M72-汇报.pptx", pptx,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    REPORT.check(st == 200, f"PPTX 作附件替换成功：{st}")
    result = body if isinstance(body, dict) else _json(body)
    REPORT.check(bool(result and result.get("recycled_file_id")),
                 "替换响应带回旧附件回收站 id")
    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token).get("body") or {}
    REPORT.check((detail.get("attachment_file") or {}).get("file_type") == "pptx",
                 "详情附件已切换为 PPTX")

    replaced = [i for i in _rb_items(app, "replaced")
                if str(i.get("document_id")) == str(did)]
    REPORT.check(bool(replaced), "回收站出现 replaced 条目")
    REPORT.check(any("制度" in (i.get("attachment_filename") or "") or
                     "原稿" in (i.get("attachment_filename") or "") for i in replaced),
                 "回收站条目保留旧附件文件名")
    app.state["z02"] = {"pptx": pptx, "name": "M72-汇报.pptx", "replaced_entry_id": replaced[0].get("id") if replaced else None}

    page = _user_detail(app, did)
    page.wait_for_selector(".default-download", timeout=15000)
    page.wait_for_timeout(400)
    main_text = page.inner_text(".default-download")
    REPORT.check("汇报.pptx" in main_text, f"用户默认下载文案指向新附件：{main_text[:100]!r}")
    st, file_bytes, _ = _get_raw(f"/api/v1/documents/{did}/file", app.user.token)
    REPORT.check(st == 200 and file_bytes == pptx, "用户 /file 得到新 PPTX")
    st, preview, _ = _get_raw(f"/api/v1/documents/{did}/preview/pdf", app.user.token)
    REPORT.check(st == 200 and preview.startswith(b"%PDF-"), "替换附件不改变主 PDF 预览")
    page.screenshot(path=str(SHOT_DIR / "Z02_attachment_replaced.png"))


@case("Z03", "删除附件：进 attachment_deleted，用户默认下载回落主文件")
def z03(app):
    fixture = app.state.get("z01")
    if not fixture:
        REPORT.fail("前置 Z01 未建立资料")
        return
    did = fixture["id"]
    s = app.admin
    s.goto(f"/documents/{did}")
    s.page.wait_for_selector("#btn-delete-attachment", timeout=15000)
    s.page.click("#btn-delete-attachment")
    _confirm_modal(s, "回收站")
    s.page.wait_for_timeout(1800)
    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token).get("body") or {}
    REPORT.check(detail.get("attachment_file") is None, "删除后资料不再挂附件")
    REPORT.check(detail.get("archived") is not True, "删除附件不改变资料归档状态")
    deleted = [i for i in _rb_items(app, "attachment_deleted")
               if str(i.get("document_id")) == str(did)]
    REPORT.check(bool(deleted), "回收站出现 attachment_deleted 条目")
    if deleted:
        REPORT.check(bool(deleted[0].get("attachment_filename")),
                     "attachment_deleted 条目带附件文件名")
        app.state["z03_entry_id"] = deleted[0].get("id")

    st, file_bytes, _ = _get_raw(f"/api/v1/documents/{did}/file", app.user.token)
    REPORT.check(st == 200 and file_bytes == fixture["pdf"],
                 "无附件后普通用户默认下载回落主 PDF")
    st, att_bytes, _ = _get_raw(f"/api/v1/documents/{did}/attachment", app.user.token)
    REPORT.check(st == 404, f"删除后附件直链返回 404：{st}")
    page = _user_detail(app, did)
    page.wait_for_selector(".default-download", timeout=15000)
    REPORT.check("主文件" in page.inner_text(".default-download"),
                 "用户详情默认下载文案切换为主文件")
    REPORT.check(not page.query_selector(".attachment-card"), "普通用户页面不再显示附件卡")
    page.screenshot(path=str(SHOT_DIR / "Z03_attachment_deleted.png"))


@case("Z04", "恢复被删附件：文件回库并重新挂回资料，默认下载回到附件")
def z04(app):
    entry_id = app.state.get("z03_entry_id")
    fixture = app.state.get("z01")
    if not entry_id or not fixture:
        REPORT.fail("前置 Z03 未产生附件删除回收站条目")
        return
    did = fixture["id"]
    restored_bytes = (app.state.get("z02") or {}).get("pptx") or fixture["docx"]
    restored_name = (app.state.get("z02") or {}).get("name") or "M72-汇报.pptx"
    s = _open_recycle(app)
    btn = None
    rows = s.page.query_selector_all("#rb-tbody tr")
    for row in rows:
        if f"/documents/{did}" in (row.inner_html() or ""):
            btn = row.query_selector('button[data-action="restore"]')
            if btn:
                break
    REPORT.check(btn is not None, "回收站找到附件删除条目的恢复按钮")
    if btn:
        btn.click()
        _confirm_modal(s, "恢复")
        s.page.wait_for_timeout(1800)
    remaining = [i for i in _rb_items(app, "attachment_deleted")
                 if str(i.get("document_id")) == str(did)]
    REPORT.check(not remaining, "附件删除条目已从回收站移除")
    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token).get("body") or {}
    REPORT.check(detail.get("attachment_file") is not None, "恢复后附件重新挂回资料")
    page = _user_detail(app, did)
    page.wait_for_selector(".attachment-card", timeout=15000)
    page.wait_for_timeout(400)
    REPORT.check(restored_name in page.inner_text(".default-download"),
                 f"恢复后用户默认下载重新指向附件：{page.inner_text('.default-download')[:100]!r}")
    st, file_bytes, _ = _get_raw(f"/api/v1/documents/{did}/file", app.user.token)
    REPORT.check(st == 200 and file_bytes == restored_bytes, "恢复后默认下载返回恢复的附件字节")
    st, att_bytes, _ = _get_raw(f"/api/v1/documents/{did}/attachment", app.user.token)
    REPORT.check(st == 200 and att_bytes == restored_bytes, "恢复后附件直链可下载同一附件")
    s.page.screenshot(path=str(SHOT_DIR / "Z04_attachment_restored.png"))


@case("Z05", "归档主文件+附件：回收站只有一条 archived 记录，用户不可见")
def z05(app):
    fixture = _base_doc(app, "z05", "归档主附")
    if not fixture:
        return
    did = fixture["id"]
    _archive_ui(app, did)
    app.admin.page.wait_for_timeout(1500)
    items = [i for i in _rb_items(app, "archived")
             if str(i.get("document_id")) == str(did)]
    REPORT.check(len(items) == 1, f"归档只生成一条回收站记录：{len(items)}")
    if not items:
        return
    item = items[0]
    REPORT.check(bool(item.get("file_record_id")) and bool(item.get("attachment_file_record_id")),
                 "归档记录同时保存主文件与附件 id")
    REPORT.check(item.get("reason") == "archived", "归档原因稳定为 archived")
    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token).get("body") or {}
    REPORT.check(detail.get("archived") is True or detail.get("status") == "archived",
                 f"管理员看到资料已归档：{detail.get('archived')}")
    r = http("GET", f"/api/v1/documents/{did}", app.user.token)
    REPORT.check(r["status"] in (403, 404), f"普通用户不能读取归档资料：{r['status']}")
    r = http("GET", f"/api/v1/documents/{did}/file", app.user.token)
    REPORT.check(r["status"] in (403, 404), f"普通用户不能下载归档资料：{r['status']}")
    app.state["z05_entry_id"] = item.get("id")
    app.admin.page.screenshot(path=str(SHOT_DIR / "Z05_archive_single_entry.png"))


@case("Z06", "从回收站恢复整条归档资料：主/附一起回来，用户下载再变附件")
def z06(app):
    fixture = app.state.get("z05")
    entry_id = app.state.get("z05_entry_id")
    if not fixture or not entry_id:
        REPORT.fail("前置 Z05 未产生归档回收站记录")
        return
    did = fixture["id"]
    s = _open_recycle(app)
    row = _find_row(s, "#rb-tbody", fixture["title"])
    btn = row.query_selector('button[data-action="restore"]') if row else None
    REPORT.check(btn is not None, "归档资料在回收站页面有恢复按钮")
    if btn:
        btn.click()
        _confirm_modal(s, "恢复")
        s.page.wait_for_timeout(2200)
    remaining = [i for i in _rb_items(app, "archived")
                 if str(i.get("document_id")) == str(did)]
    REPORT.check(not remaining, "归档恢复后回收站条目消失")
    detail = http("GET", f"/api/v1/documents/{did}", app.admin.token).get("body") or {}
    REPORT.check(detail.get("archived") is not True, "资料归档标记已清除")
    REPORT.check(bool(detail.get("current_file")), "恢复后主文件回库")
    REPORT.check(bool(detail.get("attachment_file")), "恢复后附件回库")
    page = _user_detail(app, did)
    page.wait_for_selector(".default-download", timeout=15000)
    page.wait_for_timeout(400)
    REPORT.check("原稿.docx" in page.inner_text(".default-download"),
                 f"恢复后用户默认下载重新指向附件：{page.inner_text('.default-download')[:100]!r}")
    st, file_bytes, _ = _get_raw(f"/api/v1/documents/{did}/file", app.user.token)
    REPORT.check(st == 200 and file_bytes == fixture["docx"], "恢复后 /file 返回附件 DOCX")
    st, preview, _ = _get_raw(f"/api/v1/documents/{did}/preview/pdf", app.user.token)
    REPORT.check(st == 200 and preview.startswith(b"%PDF-"), "恢复后预览仍为主 PDF")
    app.state["z06_doc"] = did
    page.screenshot(path=str(SHOT_DIR / "Z06_archive_restored_both.png"))


@case("Z07", "永久删除归档资料：缺 confirm 拒绝；页面确认后条目消失且文件不可访问")
def z07(app):
    fixture = _base_doc(app, "z07", "永久删除")
    if not fixture:
        return
    did = fixture["id"]
    _archive_ui(app, did)
    app.admin.page.wait_for_timeout(1200)
    entries = [i for i in _rb_items(app) if str(i.get("document_id")) == str(did)]
    if not entries:
        REPORT.fail("未产生归档回收站条目，无法验证永久删除")
        return
    eid = entries[0].get("id")
    no_confirm = http("DELETE", f"/api/v1/recycle-bin/{eid}", app.admin.token)
    REPORT.check(no_confirm["status"] >= 400, f"缺 confirm 被拒绝：{no_confirm['status']}")
    REPORT.check(bool([i for i in _rb_items(app) if str(i.get("document_id")) == str(did)]),
                 "缺 confirm 拒绝后条目仍在")

    s = _open_recycle(app)
    row = _find_row(s, "#rb-tbody", fixture["title"])
    btn = row.query_selector('button[data-action="purge"]') if row else None
    REPORT.check(btn is not None, "回收站找到永久删除按钮")
    if btn:
        btn.click()
        _confirm_modal(s, "不可恢复")
        s.page.wait_for_timeout(2200)
    REPORT.check(not [i for i in _rb_items(app) if str(i.get("document_id")) == str(did)],
                 "永久删除后回收站条目消失")
    REPORT.check(_get_raw(f"/api/v1/documents/{did}/file", app.admin.token)[0] == 404,
                 "永久删除后主文件端点返回 404")
    REPORT.check(_get_raw(f"/api/v1/documents/{did}/file", app.user.token)[0] in (403, 404),
                 "永久删除后用户文件访问被拒绝")
    REPORT.check(_audit_total(app, "recycle_purge") > 0, "永久删除写入 recycle_purge 审计")
@case("Z08", "通知取消→回收站通知区→页面恢复，阅读统计保留")
def z08(app):
    title = f"E2E-M72-通知恢复-{time.time_ns()}"
    nid = _notice_create(app, title)
    if not nid:
        return
    REPORT.check(_notice_cancel(app, nid), "通知已取消进入回收站")
    s = _open_recycle(app)
    try:
        s.page.wait_for_selector("#notice-rb-tbody tr", timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    REPORT.check(title in s.page.inner_text("#notice-rb-tbody"), "页面通知回收站出现该通知")
    btn = _row_button(s, "#notice-rb-tbody", "n-restore", title)
    REPORT.check(btn is not None, "通知回收站行有恢复按钮")
    if btn:
        btn.click()
        _confirm_modal(s, "恢复通知")
        s.page.wait_for_timeout(1800)
    REPORT.check(title not in s.page.inner_text("#notice-rb-tbody"), "恢复后通知从回收站移除")
    detail = http("GET", f"/api/v1/notices/{nid}", app.admin.token)
    REPORT.check(detail["status"] == 200, f"恢复后通知详情可访问：{detail['status']}")
    user_list = http("GET", "/api/v1/notices?page=1&page_size=100", app.user.token).get("body") or {}
    REPORT.check(any(row.get("id") == nid for row in (user_list.get("items") or [])),
                 "普通用户交叉复核：恢复通知出现在用户通知列表")
    app.state["z08_notice"] = nid
    s.page.screenshot(path=str(SHOT_DIR / "Z08_notice_restored.png"))


@case("Z09", "通知彻底删除：页面危险确认、API 不可再访问、审计留痕")
def z09(app):
    title = f"E2E-M72-通知彻底删除-{time.time_ns()}"
    nid = _notice_create(app, title)
    if not nid:
        return
    REPORT.check(_notice_cancel(app, nid), "通知已取消进入回收站")
    s = _open_recycle(app)
    s.page.wait_for_timeout(800)
    btn = _row_button(s, "#notice-rb-tbody", "n-purge", title)
    REPORT.check(btn is not None, "通知回收站行有永久删除按钮")
    if not btn:
        return
    btn.click()
    _confirm_modal(s, "不可恢复")
    s.page.wait_for_timeout(1800)
    REPORT.check(title not in s.page.inner_text("#notice-rb-tbody"),
                 "彻底删除后通知从回收站移除")
    REPORT.check(http("GET", f"/api/v1/notices/{nid}", app.admin.token)["status"] == 404,
                 "彻底删除后通知详情 404")
    REPORT.check(_audit_total(app, "recycle_purge") > 0, "通知彻底删除写入回收站审计")


@case("Z10", "通知回收站批量恢复：勾选、确认、成功结果和详情复核")
def z10(app):
    ids = []
    for suffix in ("A", "B"):
        title = f"E2E-M72-通知批量恢复-{suffix}-{time.time_ns()}"
        nid = _notice_create(app, title)
        if nid and _notice_cancel(app, nid):
            ids.append((nid, title))
    REPORT.check(len(ids) == 2, f"已准备两条已取消通知：{len(ids)}")
    if len(ids) != 2:
        return
    s = _open_recycle(app)
    s.page.wait_for_timeout(900)
    for nid, _ in ids:
        cb = s.page.query_selector(f'.notice-rb-check[data-id="{nid}"]')
        REPORT.check(cb is not None, f"回收站有通知 {nid} 的复选框")
        if cb:
            cb.check()
    REPORT.check(s.page.is_visible("#notice-rb-bulk-bar"), "勾选后批量恢复条出现")
    restore = s.page.query_selector("#btn-notice-rb-bulk-restore")
    REPORT.check(restore is not None, "批量恢复按钮存在")
    if restore:
        restore.click()
        _confirm_modal(s, "批量恢复")
        s.page.wait_for_timeout(2200)
        result = s.page.inner_text("#notice-rb-bulk-result") if s.page.query_selector("#notice-rb-bulk-result") else ""
        REPORT.check("成功 2" in result, f"批量恢复结果提示成功 2 条：{result[:100]!r}")
    for nid, title in ids:
        r = http("GET", f"/api/v1/notices/{nid}", app.admin.token)
        REPORT.check(r["status"] == 200, f"批量恢复通知 {title} 可访问：{r['status']}")
    s.page.screenshot(path=str(SHOT_DIR / "Z10_notice_bulk_restore.png"))


@case("Z11", "清空回收站：危险弹窗、文件/通知一并清除、purge_all 审计")
def z11(app):
    fixture = _base_doc(app, "z11", "清空回收站")
    if not fixture:
        return
    did = fixture["id"]
    st, body, _ = _multipart(
        f"/api/v1/documents/{did}/attachment", app.admin.token,
        "M72-清空前附件.docx", _docx_bytes("for purge all"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    REPORT.check(st == 200, f"准备可删除附件：{st}")
    REPORT.check(http("DELETE", f"/api/v1/documents/{did}/attachment",
                      app.admin.token)["status"] == 200, "制造附件删除条目")
    title = f"E2E-M72-清空前通知-{time.time_ns()}"
    nid = _notice_create(app, title)
    if nid:
        REPORT.check(_notice_cancel(app, nid), "制造已取消通知条目")
    s = _open_recycle(app)
    try:
        s.page.wait_for_selector("#btn-purge-all", state="visible", timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    REPORT.check(s.page.is_visible("#btn-purge-all"), "有回收站内容时清空按钮可见")
    if s.page.is_visible("#btn-purge-all"):
        s.page.click("#btn-purge-all")
        _confirm_modal(s, "永久删除")
        s.page.wait_for_timeout(2600)
    REPORT.check(not _rb_items(app), "清空后文件回收站为空")
    REPORT.check(not _notice_items(app), "清空后通知回收站为空")
    audit = http("GET", "/api/v1/audit-logs?page=1&page_size=100&action=recycle_purge",
                 app.admin.token).get("body") or {}
    labels = [str(i.get("object_label") or "") for i in (audit.get("items") or [])]
    REPORT.check("purge_all" in labels, "清空动作写 object_label=purge_all 审计")
    s.page.screenshot(path=str(SHOT_DIR / "Z11_recycle_purge_all.png"))
@case("Z12", "权限：普通用户不能访问回收站/恢复/永久删除/附件上传删除")
def z12(app):
    app.allow_noise()
    fixture = app.state.get("z06")
    if not fixture:
        fixture = _base_doc(app, "z12", "权限")
    if not fixture:
        return
    did = fixture["id"] if isinstance(fixture, dict) else fixture
    checks = [
        ("GET", "/api/v1/recycle-bin", None),
        ("GET", "/api/v1/recycle-bin/notices", None),
        ("POST", "/api/v1/recycle-bin/purge-all", {"confirm": True}),
    ]
    for method, path, payload in checks:
        r = http(method, path, app.user.token, payload)
        REPORT.check(r["status"] in (401, 403), f"普通用户 {method} {path} → {r['status']}")
    items = _rb_items(app)
    if items:
        eid = items[0].get("id")
        r = http("POST", f"/api/v1/recycle-bin/{eid}/restore", app.user.token, {})
        REPORT.check(r["status"] in (401, 403), f"普通用户恢复回收站 → {r['status']}")
        r = http("DELETE", f"/api/v1/recycle-bin/{eid}", app.user.token, {"confirm": True})
        REPORT.check(r["status"] in (401, 403), f"普通用户永久删除 → {r['status']}")
    st, _, _ = _multipart(
        f"/api/v1/documents/{did}/attachment", app.user.token,
        "M72-user.docx", _docx_bytes("denied"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    REPORT.check(st in (401, 403), f"普通用户上传附件被拒：{st}")
    r = http("DELETE", f"/api/v1/documents/{did}/attachment", app.user.token)
    REPORT.check(r["status"] in (401, 403), f"普通用户删除附件被拒：{r['status']}")
    r = http("DELETE", f"/api/v1/documents/{did}", app.user.token,
             {"reason": "越权"})
    REPORT.check(r["status"] in (401, 403), f"普通用户归档资料被拒：{r['status']}")

    app.user.goto("/admin/recycle-bin", ensure_session=True)
    app.user.page.wait_for_timeout(1200)
    REPORT.check("/admin/recycle-bin" not in app.user.page.url,
                 f"普通用户被页面守卫拦截：{app.user.page.url}")
    REPORT.check(app.user.page.query_selector("#rb-tbody") is None,
                 "普通用户落地页不含回收站表格 DOM")
    REPORT.check(http("GET", f"/api/v1/documents/{did}", app.admin.token)["status"] == 200,
                 "越权探测未改变资料状态")


@case("Z13", "反向锁定：无确认阅读、无第二主下载、空失效日文案正确")
def z13(app):
    did = app.state.get("z06_doc")
    if not did:
        fixture = app.state.get("z05")
        did = fixture["id"] if isinstance(fixture, dict) else None
    if not did:
        REPORT.fail("前置 Z05/Z06 未建立可复核资料")
        return
    page = _user_detail(app, did)
    page.wait_for_selector(".default-download", timeout=15000)
    locked = page.evaluate(
        "() => ({"
        " defaultCount: document.querySelectorAll('.default-download').length,"
        " secondMain: document.querySelectorAll('.default-download[href$=\"/attachment\"]').length,"
        " attLinks: document.querySelectorAll('.attachment-card a[href$=\"/attachment\"]').length,"
        " confirmControls: document.querySelectorAll('#btn-confirm-read, .confirm-read-btn, [data-confirm-read], #confirm-read').length,"
        " bodyText: document.body.innerText || ''"
        "})")
    REPORT.check(locked["defaultCount"] == 1, "详情页只有一个默认下载主 CTA")
    REPORT.check(locked["secondMain"] == 0, "附件直链没有伪装成第二个主下载 CTA")
    REPORT.check(locked["attLinks"] == 1, "附件卡保留一个明确次级附件入口")
    REPORT.check(locked["confirmControls"] == 0, "详情页不存在阅读确认控件")
    REPORT.check("确认阅读" not in locked["bodyText"] and "确认率" not in locked["bodyText"],
                 "页面没有确认阅读/确认率文案")
    REPORT.check("暂无失效日期" in locked["bodyText"], "空失效日显示「暂无失效日期」")
    REPORT.check(not re.search(r"长期|永久有效|暂无已知失效日", locked["bodyText"]),
                 "空失效日没有禁用替代文案")

    detail = http("GET", f"/api/v1/documents/{did}", app.user.token).get("body") or {}
    REPORT.check(detail.get("effective_to") is None, "API 仍以 null 表示暂无失效日")
    notices = http("GET", "/api/v1/notices?page=1&page_size=50", app.user.token).get("body") or {}
    notice_id = (notices.get("items") or [{}])[0].get("id")
    if notice_id:
        app.user.goto(f"/notices/{notice_id}")
        app.user.page.wait_for_timeout(900)
        text = app.user.page.inner_text("body")
        REPORT.check("确认阅读" not in text and "待确认" not in text,
                     "通知详情没有恢复确认功能入口")
        stats = http("GET", f"/api/v1/notices/{notice_id}/statistics", app.admin.token)
        REPORT.check("confirmed_at" not in str(stats.get("body") or {}),
                     "统计 API 不含 confirmed_at")
    page.screenshot(path=str(SHOT_DIR / "Z13_locked_reverse.png"))


@case("Z14", "回收站筛选与审计：attachment_deleted/archived 可筛选且删除动作可追溯")
def z14(app):
    fixture = _base_doc(app, "z14", "筛选审计")
    if not fixture:
        return
    did = fixture["id"]
    st, _, _ = _multipart(
        f"/api/v1/documents/{did}/attachment", app.admin.token,
        "M72-筛选附件.docx", _docx_bytes("filter audit"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    REPORT.check(st == 200, f"准备筛选附件：{st}")
    REPORT.check(http("DELETE", f"/api/v1/documents/{did}/attachment",
                      app.admin.token)["status"] == 200, "附件删除成功")
    _archive_ui(app, did)
    app.admin.page.wait_for_timeout(1000)
    deleted = [i for i in _rb_items(app, "attachment_deleted")
               if str(i.get("document_id")) == str(did)]
    archived = [i for i in _rb_items(app, "archived")
                if str(i.get("document_id")) == str(did)]
    REPORT.check(bool(deleted), "API 筛选 attachment_deleted 命中")
    REPORT.check(bool(archived), "API 筛选 archived 命中")

    s = _open_recycle(app)
    s.page.select_option("#f-reason", "attachment_deleted")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(900)
    REPORT.check("删除附件" in s.page.inner_text("#rb-tbody"),
                 "页面筛选 attachment_deleted 显示「删除附件」标签")
    s.page.select_option("#f-reason", "archived")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(900)
    REPORT.check("资料归档" in s.page.inner_text("#rb-tbody"),
                 "页面筛选 archived 显示「资料归档」标签")

    audit = http("GET", "/api/v1/audit-logs?page=1&page_size=100&action=delete_document_attachment",
                 app.admin.token).get("body") or {}
    REPORT.check(int(audit.get("total") or 0) > 0, "删除附件动作在操作日志可查")
    s.page.select_option("#f-reason", "")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(500)
    if s.page.is_visible("#btn-purge-all"):
        s.page.click("#btn-purge-all")
        _confirm_modal(s, "永久删除")
        s.page.wait_for_timeout(1600)
    s.page.screenshot(path=str(SHOT_DIR / "Z14_recycle_filter_audit.png"))