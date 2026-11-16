"""M11 资料新建：表单校验、文件上传链路、文件安全（项目规范 §11/§32）。"""
from __future__ import annotations

import re
import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # _paths.py
from harness import REPORT, SHOT_DIR, case, http

from _paths import E2E_FILES_DIR as FILES  # noqa: E402
PDF = FILES / "员工手册2026.pdf"
DOCX = FILES / "公司制度.docx"
XLSX = FILES / "费用报销表.xlsx"
XLS = FILES / "工资明细表.xls"
FAKE_PDF = FILES / "伪装成PDF的可执行文件.pdf"
EXE = FILES / "木马.exe"


def _new_page(app):
    s = app.admin
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=10000)
    return s


def _fill(s, title, frm="2026-01-01", to=""):
    s.page.fill("#f-title", title)
    s.page.fill("#f-effective-from", frm)
    if to:
        s.page.fill("#f-effective-to", to)
    else:
        s.page.fill("#f-effective-to", "")


def _wait_created(s) -> str:
    """等跳转详情页（必须 /documents/<数字>，避免误匹配新建页）并返回 id。"""
    s.page.wait_for_url(re.compile(r".*/documents/\d+"), timeout=25000)
    s.page.wait_for_load_state("networkidle")
    return re.search(r"/documents/(\d+)", s.page.url).group(1)


@case("F01", "新建资料：必填校验（空标题 / 无生效日期）拦截在本地")
def f01(app):
    s = _new_page(app)
    calls = []
    s.page.on("request", lambda r: calls.append(r.url) if "/api/v1/documents" in r.url and r.method == "POST" else None)
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(1000)
    REPORT.check(not calls, f"未发起创建请求（{len(calls)} 次）")
    cls = s.page.evaluate(
        "() => Array.from(document.querySelectorAll('.form-group.has-error')).length")
    REPORT.check(cls >= 2, f"标题与生效日期分组标红（{cls} 处）")
    toast = s.toast_text(3000)
    REPORT.check("必填" in toast or "请完善" in toast, f"提示文案：{toast[:40]}")
    REPORT.check("/admin/documents/new" in s.page.url, "仍停留在新建页")


@case("F02", "新建资料：失效日期早于生效日期 → 拒绝保存")
def f02(app):
    s = _new_page(app)
    _fill(s, f"E2E日期倒挂{int(time.time())}", frm="2026-06-01", to="2026-01-01")
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(1500)
    url = s.page.url
    toast = s.toast_text(3000)
    REPORT.check("/admin/documents/new" in url, f"未跳转到详情（仍在新建页：{url}）")
    REPORT.check(bool(toast), f"给出提示：{toast[:60]}")


@case("F03", "新建资料 + 中文文件名 PDF：创建成功且文件元数据正确")
def f03(app):
    s = _new_page(app)
    title = f"E2E员工手册{int(time.time())}"
    _fill(s, title, frm="2026-01-01")
    # 2026-11-04 起 allow_download 开关已整体移除（下载权限 = 查看权限），
    # 表单里没有该复选框，此前这里 check 一个不存在的元素导致 F03 直接抛错，
    # 连带 G/H 系列全部报「依赖 F03」。
    s.page.set_input_files("#upload-input", str(PDF))
    s.page.wait_for_timeout(500)
    shown = s.page.inner_text("#upload-name").strip()
    REPORT.check("员工手册2026.pdf" in shown, f"上传区回显原始文件名：{shown[:40]}")
    s.page.click("#btn-submit")
    did = _wait_created(s)
    REPORT.check("/documents/" + did in s.page.url and "upload_failed" not in s.page.url,
                 f"跳转到详情页且无上传失败标记：{s.page.url}")
    app.state["doc_pdf_id"] = did
    txt = s.page.inner_text("#detail-root") if s.page.query_selector("#detail-root") else s.page.inner_text("body")
    REPORT.check("员工手册2026.pdf" in txt, "详情页展示原始文件名")
    REPORT.check("PDF" in txt.upper(), "详情页展示文件类型")
    s.page.screenshot(path=str(SHOT_DIR / "F03_created.png"))


@case("F04", "新建资料 + DOCX / XLSX / XLS：四种格式均可入库")
def f04(app):
    s = app.admin
    for label, path in (("DOCX", DOCX), ("XLSX", XLSX), ("XLS", XLS)):
        s.goto("/admin/documents/new")
        s.page.wait_for_selector("#f-title", timeout=10000)
        _fill(s, f"E2E{label}{int(time.time())}")
        s.page.set_input_files("#upload-input", str(path))
        s.page.click("#btn-submit")
        did = _wait_created(s)
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        REPORT.check(bool(info.get("has_file")),
                     f"{label}：入库成功（id={did}，has_file={info.get('has_file')}，type={info.get('file_type')}）")
        app.state[f"doc_{label.lower()}_id"] = did


@case("F05", "文件安全：伪造扩展名（exe 内容改名 .pdf）被魔数校验拒绝")
def f05(app):
    s = app.admin
    app.allow_noise()  # 服务端返回 415 属预期：我们要的就是拒绝
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=10000)
    _fill(s, f"E2E伪装文件{int(time.time())}")
    s.page.set_input_files("#upload-input", str(FAKE_PDF))
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(2500)
    if "/documents/" in s.page.url and "/admin/" not in s.page.url:
        did = re.search(r"/documents/(\d+)", s.page.url).group(1)
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        REPORT.check(not info.get("has_file"), f"伪装文件未被写入（id={did}，has_file={info.get('has_file')}）")
        body = s.page.inner_text("body")
        REPORT.check("上传失败" in body or "无法在线预览" in body or "暂未上传文件" in body,
                     f"页面明示未成功：{('上传失败' in body, '暂未上传文件' in body)}")
        app.state["doc_fake_id"] = did
    else:
        toast = s.toast_text(4000)
        REPORT.check(bool(toast), f"未创建成功（停在表单）：{toast[:50]}")


@case("F06", "文件安全：非法扩展名 .exe 不得进入上传管线")
def f06(app):
    s = app.admin
    app.allow_noise()
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=10000)
    accept = s.page.get_attribute("#upload-input", "accept")
    REPORT.check(accept and "exe" not in accept, f"文件输入框限定扩展名：{accept}")
    _fill(s, f"E2E非法扩展名{int(time.time())}")
    s.page.set_input_files("#upload-input", str(EXE))
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(3000)
    url = s.page.url
    if re.search(r"/documents/\d+", url):
        did = re.search(r"/documents/(\d+)", url).group(1)
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        REPORT.check(not info.get("has_file"),
                     f".exe 未被写入（id={did}，has_file={info.get('has_file')}）")
        app.state["doc_exe_id"] = did
    else:
        REPORT.check(True, f"未创建成功（停在表单）：{url}")


@case("F07", "新建资料：标签即时创建 + 分类下拉可选")
def f07(app):
    s = _new_page(app)
    tag_name = f"E2E标签{int(time.time())}"
    s.page.fill("#f-new-tag", tag_name)
    s.page.click("#btn-new-tag")
    s.page.wait_for_timeout(1200)
    selected = s.page.evaluate(
        "() => Array.from(document.querySelectorAll('#f-tags .tag-option.checked'))"
        ".map(b => b.textContent.trim()).join(',')")
    REPORT.check(tag_name in selected, f"新标签被创建并选中：{selected[:40]}")
    cats = s.page.evaluate(
        "() => Array.from(document.querySelectorAll('#f-category option')).length")
    REPORT.check(cats > 1, f"分类下拉已加载 {cats} 个选项")
    s.goto("/documents")


@case("F08", "新建资料：允许同名（无编号后不再有唯一性约束）")
def f08(app):
    """2026-11-03：资料编号已移除，同名资料可并存（不设唯一约束）。

    此用例保留为「无编号」的回归：表单里不应再出现 #f-docno，
    同名提交应成功而不是报错。
    """
    s = app.admin
    lst = http("GET", "/api/v1/documents?page=1&page_size=1", app.admin.token)["body"] or {}
    items = lst.get("items") or []
    if not items:
        REPORT.fail("无现成资料可用于同名校验")
        return
    exist_title = items[0].get("title")
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=10000)
    REPORT.check(s.page.query_selector("#f-docno") is None,
                 "新建表单已无资料编号输入框")
    _fill(s, f"{exist_title}-同名校验{int(time.time())}")
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(2500)
    REPORT.check("/documents/" in s.page.url,
                 f"同风格标题可正常创建（落点 {s.page.url[-30:]}）")
    s.goto("/documents")


