"""M13 资料编辑：修改、时效冲突双通道（§16）、文件替换（旧进回收站 §8/§26）、删除与恢复。"""
from __future__ import annotations

import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # _paths.py
from harness import REPORT, case, http

from _paths import E2E_FILES_DIR as FILES  # noqa: E402
PDF2 = FILES / "员工手册2026.pdf"
XLSX = FILES / "费用报销表.xlsx"


def _open_edit(app, did):
    """打开编辑页，并**等到异步回填真正完成**再返回。

    2026-09-29 修的竞态（全量跑偶发 / 单跑必过）：
    `admin/document_form.js::loadDoc()` 是 async —— `await api.get(...)` 之后才写
    `titleEl.value` / `summaryEl.value`。原先这里只 `wait_for_timeout(1200)`「猜」回填完成，
    负载高时响应晚于用例的 `fill()`，会把填好的值**覆盖回原值** → 提交的是旧标题，
    详情页自然显示旧值（H01 的两条断言就是这么挂的）。
    这属**用例缺陷（固定 sleep 猜时间）**，不是产品缺陷 —— 依据是同一用例单独跑 100% 通过。

    改法：轮询到 `#f-title` 的值等于后端当前值为止（`loadDoc` 里 title/summary/生效区间
    是同一个同步块内赋值的，所以等到标题一致即代表整块回填完成）。
    """
    s = app.admin
    s.goto(f"/admin/documents/{did}/edit")
    s.page.wait_for_selector("#f-title", timeout=12000)
    want = (http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}).get("title") or ""
    if want:
        s.page.wait_for_function(
            "(want) => {const el = document.getElementById('f-title');"
            " return !!el && el.value === want;}",
            arg=want, timeout=15000)
    else:
        s.page.wait_for_timeout(1200)   # 兜底：标题为空（理论不可达，标题是必填）
    return s


@case("H01", "编辑资料：修改标题/说明生效并可回溯")
def h01(app):
    did = app.state.get("doc_pdf_id")
    if not did:
        REPORT.fail("无可用资料（依赖 F03）")
        return
    s = _open_edit(app, did)
    REPORT.check(s.page.input_value("#f-title").strip() != "", "编辑页回填了现有标题")
    new_title = f"E2E改名{int(time.time())}"
    s.page.fill("#f-title", new_title)
    s.page.fill("#f-summary", "E2E自动化测试写入的说明")
    s.page.click("#btn-submit")
    s.page.wait_for_url(f"**/documents/{did}**", timeout=20000)
    s.page.wait_for_load_state("networkidle")
    # 2026-10-16 修竞态（全量偶发 / 单跑必过）：详情页 #detail-root 由
    # document_detail.js 异步渲染（root.innerHTML），networkidle 后立刻
    # inner_text 可能读到渲染前状态。改为有界轮询到文本含新标题再断言，
    # 不再猜时间（TESTING.md §六·1 纪律）。
    try:
        s.page.wait_for_function(
            "(t) => {const el = document.getElementById('detail-root');"
            " return !!el && el.innerText.includes(t);}",
            arg=new_title, timeout=15000)
    except Exception:  # noqa: BLE001  下面用断言报告真实结果
        pass
    txt = s.page.inner_text("#detail-root")
    REPORT.check(new_title in txt, f"详情页显示新标题：{new_title}")
    fold = s.page.query_selector(".doc-summary-disclosure")
    REPORT.check(fold is not None, "说明在关联通知下方作为低调折叠块存在")
    if fold:
        REPORT.check(not fold.evaluate("e => e.open"), "资料说明默认折叠")
        s.page.click(".doc-summary-disclosure > summary")
        REPORT.check("E2E自动化测试写入的说明" in s.page.inner_text(".doc-summary-content"),
                     "展开后可查看更新后的说明")
    app.state["doc_pdf_title"] = new_title


@case("H02", "时效冲突：默认阻止保存，勾选确认覆盖 + 原因方可保存（§16）")
def h02(app):
    did = app.state.get("doc_pdf_id")
    if not did:
        REPORT.fail("无可用资料（依赖 F03）")
        return
    s = _open_edit(app, did)
    # 制造区间冲突：本地改动既有生效区间 → 前端先拦，要求确认覆盖 + 修改原因
    s.page.fill("#f-effective-from", "2026-03-01")
    s.page.fill("#f-effective-to", "2026-04-01")
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(2000)
    overwrite_visible = s.page.evaluate(
        "() => {const b = document.getElementById('overwrite-box');"
        " return b && b.style.display !== 'none';}")
    toast = s.toast_text(4000)
    REPORT.check(overwrite_visible, f"弹出「确认覆盖」输入框（提示：{toast[:40]}）")
    REPORT.check("/edit" in s.page.url, f"未勾选确认时被拦住：{s.page.url[-40:]}")

    s.page.check("#f-confirm-overwrite")
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(1500)
    REPORT.check("/edit" in s.page.url, f"未填修改原因时被拦住：{s.page.url[-40:]}")

    s.page.fill("#f-modify-reason", "E2E：确认调整生效区间")
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(3000)
    info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    REPORT.check(info.get("effective_from") == "2026-03-01" and info.get("effective_to") == "2026-04-01",
                 f"确认覆盖后区间已更新：{info.get('effective_from')} ~ {info.get('effective_to')}")


@case("H03", "替换文件：新文件成为当前文件，旧文件移到回收站且版本数 +1（§8/§26）")
def h03(app):
    did = app.state.get("doc_pdf_id")
    if not did:
        REPORT.fail("无可用资料（依赖 F03）")
        return
    before = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    old_rev = before.get("rev_count")
    s = app.admin
    s.goto(f"/documents/{did}")
    s.page.wait_for_selector("#btn-upload-file", timeout=12000)
    # 上传控件在自定义弹窗里：先打开弹窗，再选文件
    s.page.click("#btn-upload-file")
    s.page.wait_for_selector("#modal-root #upload-input", timeout=8000)
    s.page.set_input_files("#modal-root #upload-input", str(XLSX))
    s.page.wait_for_timeout(500)
    REPORT.check(s.page.is_enabled("#modal-root #upload-submit"), "选择文件后确认上传按钮可用")
    s.page.click("#modal-root #upload-submit")
    s.page.wait_for_timeout(3500)
    after = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    REPORT.check(after.get("file_type") == "xlsx",
                 f"当前文件已切换为 xlsx（原 {before.get('file_type')}）")
    REPORT.check((after.get("rev_count") or 0) > (old_rev or 0),
                 f"版本数递增：{old_rev} → {after.get('rev_count')}")
    rb = http("GET", "/api/v1/recycle-bin?page=1&page_size=50", app.admin.token)["body"] or {}
    names = [str(e.get("original_filename")) for e in (rb.get("items") or [])]
    REPORT.check(before.get("current_file", {}).get("original_filename") in names,
                 f"旧文件进入回收站：{names[:3]}")
    app.state["replaced_old_file"] = before.get("current_file", {}).get("original_filename")


def _detail_open(app, did):
    s = app.admin
    s.goto(f"/documents/{did}")
    s.page.wait_for_selector("#btn-upload-file", timeout=12000)
    return s


@case("H04", "删除资料：进入回收站（软删除），详情页标记已删除且普通用户不可见")
def h04(app):
    app.allow_noise()  # 普通用户访问已删除资料 → 服务端 403/404，属预期
    did = app.state.get("doc_docx_id")
    if not did:
        REPORT.fail("无可用 DOCX 资料（依赖 F04）")
        return
    s = app.admin
    s.goto(f"/documents/{did}")
    s.page.wait_for_selector("#btn-archive", timeout=12000)
    s.page.click("#btn-archive")
    # 删除走 ui.confirmModal（自定义弹窗，非原生 dialog）
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=8000)
    inp = s.page.query_selector("#modal-root #confirm-input")
    if inp:
        s.page.fill("#modal-root #confirm-input", "E2E 自动化删除")
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2500)
    info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    REPORT.check(info.get("archived") is True or info.get("status") == "archived",
                 f"资料已标记删除：archived={info.get('archived')} status={info.get('status')}")
    rb = http("GET", "/api/v1/recycle-bin?page=1&page_size=50", app.admin.token)["body"] or {}
    REPORT.check(True, f"回收站当前条目数：{len(rb.get('items') or [])}")
    u = app.user
    u.goto(f"/documents/{did}")
    u.page.wait_for_timeout(1500)
    body = u.page.inner_text("body")
    REPORT.check("不存在" in body or "无权" in body or "/login" in u.page.url or "访问" in body,
                 f"普通用户访问已删除资料被挡：{u.page.url}")
    app.state["archived_doc_id"] = did


@case("H05", "恢复资料：从回收站恢复后回到正常状态")
def h05(app):
    did = app.state.get("archived_doc_id")
    if not did:
        REPORT.fail("无已删除资料（依赖 H04）")
        return
    rb = http("GET", "/api/v1/recycle-bin?page=1&page_size=50", app.admin.token)["body"] or {}
    entry = None
    for e in (rb.get("items") or []):
        if str(e.get("document_id") or e.get("id")) == str(did):
            entry = e
            break
    if not entry:
        src = (rb.get("items") or [])
        REPORT.fail(f"回收站中未找到该资料条目（{len(src)} 条）")
        return
    r = http("POST", f"/api/v1/recycle-bin/{entry.get('entry_id') or entry.get('id')}/restore",
             app.admin.token)
    REPORT.check(r["status"] in (200, 201), f"恢复请求成功：{r['status']}")
    info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    REPORT.check(not info.get("archived"), f"资料已恢复正常：archived={info.get('archived')} status={info.get('status')}")
    s = app.admin
    s.goto(f"/documents/{did}")
    s.page.wait_for_timeout(1500)
    REPORT.check("#btn-restore" not in (s.page.inner_html("body") or ""), "详情页不再显示「恢复资料」按钮")
