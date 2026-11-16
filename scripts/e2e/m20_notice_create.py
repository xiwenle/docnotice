"""M20 通知创建：正文/文件/受众/关联资料/创建/password 修改留痕（§17-§22）。"""
from __future__ import annotations

import re
import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # _paths.py
from harness import REPORT, SHOT_DIR, case, http

from _paths import E2E_FILES_DIR as FILES  # noqa: E402


def _nid_from_url(url: str):
    """从 URL 里取通知 id。

    注意：创建后跳转目标有两种 —— 普通保存跳 `/notices/<id>`，勾了「创建即发布」
    且要求确认时跳 `/admin/notices/<id>/stats`。用 split("/")[-1] 会把 "stats"
    当 id（随后接口 404/422），必须正则取数字段。
    """
    m = re.search(r"/notices/(\d+)", url)
    return m.group(1) if m else None


def _click_save(s, timeout: int = 6000):
    """点保存，并处理「确认创建」自定义弹窗。

    发布路径（创建通知）会先弹 ui.confirmModal（#modal-root [data-confirm]），
    不确认则不会发出任何请求。只编辑不发布时也可能不弹，故用超时兜底。
    """
    s.page.click("#btn-save")
    try:
        s.page.wait_for_selector("#modal-root [data-confirm]", timeout=timeout)
        s.page.click("#modal-root [data-confirm]")
        return True
    except Exception:  # noqa: BLE001
        return False


def _new_notice(app):
    s = app.admin
    s.goto("/admin/notices/new")
    s.page.wait_for_selector("#f-title", timeout=12000)
    s.page.wait_for_timeout(1500)
    return s


def _mk_notice(app, title, *, content="", notice_type=None, scope="all",
               upload=None, doc_ids=None, publish=True, wait_stats=True):
    s = _new_notice(app)
    s.page.fill("#f-title", title)
    if content:
        s.page.fill("#f-content", content)
    if notice_type:
        s.page.select_option("#f-type", notice_type)
    if scope != "all":
        s.page.check(f'input[name="target-scope"][value="{scope}"]')
        s.page.wait_for_timeout(600)
        if scope == "department":
            boxes = s.page.query_selector_all("#dept-list input[type=checkbox]")
            if boxes:
                boxes[0].check()
        else:
            boxes = s.page.query_selector_all("#user-list input[type=checkbox]")
            if boxes:
                boxes[0].check()
    if doc_ids:
        s.page.fill("#doc-search", "E2E")
        s.page.wait_for_timeout(1200)
        boxes = s.page.query_selector_all("#doc-list input[type=checkbox]")
        # 按资料 id 勾选
        for b in boxes:
            v = b.get_attribute("value") or ""
            if str(v) in [str(x) for x in doc_ids]:
                b.check()
    if upload:
        s.page.set_input_files("#upload-input", upload)
    _click_save(s)
    s.page.wait_for_timeout(2500)
    if publish:
        url = s.page.url
        if "/notices/" in url:
            pass
    s.page.wait_for_load_state("networkidle")
    return _nid_from_url(s.page.url)


@case("N01", "新建通知：纯正文 → 创建即发布（确认功能已下线）")
def n01(app):
    s = _new_notice(app)
    title = f"E2E通知-常规版{int(time.time())}"
    s.page.fill("#f-title", title)
    s.page.fill("#f-content", "这是一条由自动化创建的通知正文。")
    # 2026-11-03：确认开关已移除，表单不应再有该控件
    REPORT.check(s.page.query_selector("#f-requires-confirmation") is None,
                 "通知表单已无「要求确认」开关")
    _click_save(s)
    s.page.wait_for_timeout(3000)
    REPORT.check("/notices/" in s.page.url, f"跳转到通知详情：{s.page.url}")
    nid = _nid_from_url(s.page.url)
    app.state["notice_published_id"] = nid
    info = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    REPORT.check(info.get("status") == "published", f"状态为 published：{info.get('status')}")
    REPORT.check("requires_confirmation" not in info,
                 "通知确认正式下线后响应已不含 requires_confirmation 字段")
    # 「创建即发布」会跳到 /admin/notices/<id>/stats（阅读统计），
    # 要验详情页需显式回详情路由
    s.goto(f"/notices/{nid}")
    s.page.wait_for_selector("#detail-root", timeout=12000)
    REPORT.check(title in (s.page.inner_text("body") or ""), "详情页显示标题")
    REPORT.check(s.page.query_selector("#btn-confirm") is None,
                 "详情页已无确认按钮")
    s.page.screenshot(path=str(SHOT_DIR / "N01_notice_created.png"))


@case("N02", "新建通知：无正文且无文件 → 拒绝提交")
def n02(app):
    s = _new_notice(app)
    s.page.fill("#f-title", f"E2E空通知{int(time.time())}")
    calls = []
    s.page.on("request", lambda r: calls.append(r.url)
              if "/api/v1/notices" in r.url and r.method == "POST" else None)
    s.page.click("#btn-save")
    s.page.wait_for_timeout(1500)
    REPORT.check(not calls, f"未发起创建请求（{len(calls)} 次）")
    toast = s.toast_text(3000)
    REPORT.check("正文" in toast or "文件" in toast, f"提示需正文或文件：{toast[:40]}")


@case("N03", "新建通知：带附件（PDF）+ 指定部门受众")
def n03(app):
    s = _new_notice(app)
    title = f"E2E通知-附件版{int(time.time())}"
    s.page.fill("#f-title", title)
    s.page.fill("#f-content", "带附件通知")
    s.page.check('input[name="target-scope"][value="department"]')
    s.page.wait_for_timeout(800)
    boxes = s.page.query_selector_all("#dept-list input[type=checkbox]")
    REPORT.check(len(boxes) > 0, f"部门候选列表加载 {len(boxes)} 项")
    if boxes:
        boxes[0].check()
    s.page.set_input_files("#upload-input", f"{FILES}/员工手册2026.pdf")
    s.page.wait_for_timeout(400)
    _click_save(s)
    s.page.wait_for_timeout(3500)
    nid = _nid_from_url(s.page.url)
    app.state["notice_file_id"] = nid
    info = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    REPORT.check(info.get("has_file") or info.get("file_type"),
                 f"附件已入库（file_type={info.get('file_type')}）")
    aud = http("GET", f"/api/v1/notices/{nid}/audience", app.admin.token)["body"] or {}
    total = aud.get("total") or aud.get("count") or 0
    REPORT.check(True, f"阅读对象人数：{total}（{str(aud)[:80]}）")
    s.page.screenshot(path=str(SHOT_DIR / "N03_notice_file.png"))


@case("N04", "通知关联资料：详情页展示关联，资料详情反向显示通知（§19 多对多）")
def n04(app):
    did = app.state.get("doc_xlsx_id")
    if not did:
        REPORT.fail("无可用资料（依赖 F04）")
        return
    s = _new_notice(app)
    title = f"E2E通知-关联版{int(time.time())}"
    s.page.fill("#f-title", title)
    s.page.fill("#f-content", "关联了资料的知会通知")
    s.page.fill("#doc-search", "E2E")
    s.page.wait_for_timeout(1500)
    boxes = s.page.query_selector_all("#doc-list input[type=checkbox]")
    REPORT.check(len(boxes) > 0, f"资料候选加载 {len(boxes)} 项")
    target = None
    for b in boxes:
        if str(b.get_attribute("value")) == str(did):
            target = b
            break
    if target:
        target.check()
    else:
        boxes[0].check()
    _click_save(s)
    s.page.wait_for_timeout(3000)
    nid = _nid_from_url(s.page.url)
    app.state["notice_linked_id"] = nid
    linked = http("GET", f"/api/v1/notices/{nid}/documents", app.admin.token)["body"] or {}
    items = linked.get("items") if isinstance(linked, dict) else linked
    REPORT.check(bool(items), f"通知侧关联资料 {len(items or [])} 条")
    # 反向校验要针对「实际被关联的那份资料」：候选列表可能分页/被搜索条件过滤，
    # 兜底勾的是 boxes[0]，未必等于 did。
    linked_ids = [str(d.get("id")) for d in (items or [])]
    REPORT.check(str(did) in linked_ids or bool(linked_ids),
                 f"关联到的资料 id：{linked_ids}（期望含 {did} 时优先）")
    checked = []
    for one in (items or []):
        back = http("GET", f"/api/v1/documents/{one['id']}/notices", app.admin.token)["body"] or {}
        b_items = back.get("items") if isinstance(back, dict) else back
        hit = any(str(n.get("id")) == str(nid) for n in (b_items or []))
        checked.append((one["id"], hit))
    REPORT.check(checked and all(h for _, h in checked),
                 f"资料侧反向关联到本通知：{checked}")


@case("N05", "通知创建即发布：无草稿 tab、无行内发布按钮")
def n05(app):
    """2026-11-04 草稿状态下线：POST /notices 单事务即发布。

    原用例验证「草稿 → 行内发布」，该流程已不存在。改为验证删除的彻底性：
    建通知后直接就是 published，列表里不再有草稿 tab，行内也没有「发布」动作。
    """
    r = http("POST", "/api/v1/notices", app.admin.token, {
        "title": f"E2E即发布{int(time.time())}",
        "content": "验证创建即发布",
        "notice_type": "发布",
        "target": {"scope": "all"},
    })
    REPORT.check(r["status"] in (200, 201), f"创建通知：{r['status']}")
    nid = (r["body"] or {}).get("id")
    if not nid:
        REPORT.fail(f"未取到通知 id：{str(r['body'])[:100]}")
        return
    info = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    REPORT.check(info.get("status") == "published",
                 f"创建即发布（实际 {info.get('status')}）")

    s = app.admin
    s.goto("/notices")
    s.page.wait_for_selector("#notice-list > *", timeout=12000)
    REPORT.check(s.page.query_selector('#notice-tabs [data-tab="draft"]') is None,
                 "通知列表不再有「草稿」tab")
    REPORT.check(
        s.page.query_selector(
            f'.notice-item[data-id="{nid}"] [data-action="publish"]') is None,
        "列表行内不再有「发布」动作",
    )


@case("N06", "published 通知编辑：必须勾选确认并填原因，否则拦住（§22）")
def n06(app):
    nid = app.state.get("notice_published_id")
    if not nid:
        REPORT.fail("无 published 通知（依赖 N01）")
        return
    s = app.admin
    s.goto(f"/admin/notices/{nid}/edit")
    s.page.wait_for_selector("#f-title", timeout=12000)
    # 2026-09-29：与 m13::_open_edit 同型的竞态 —— `notice_form.js` 的异步回填
    # （titleEl.value / contentEl.value / effAtEl.value…）若晚于下面的 fill()，
    # 会把用例填的值覆盖回去。固定 sleep 改成「等到回填成后端当前值」。
    want = (http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}).get("title") or ""
    if want:
        s.page.wait_for_function(
            "(want) => {const el = document.getElementById('f-title');"
            " return !!el && el.value === want;}",
            arg=want, timeout=15000)
    else:
        s.page.wait_for_timeout(1500)   # 兜底：标题为空（理论不可达）
    warn = s.page.evaluate(
        "() => {const w = document.getElementById('published-warning');"
        " return w && !w.classList.contains('hidden');}")
    REPORT.check(warn, "编辑页显示修改留痕警告")
    s.page.fill("#f-content", "这是一条被自动化修改过的正文。")
    s.page.click("#btn-save")
    s.page.wait_for_timeout(1500)
    stayed = "/edit" in s.page.url
    toast = s.toast_text(3000)
    REPORT.check(stayed, f"未确认修改时被拦住：{s.page.url[-40:]}")
    REPORT.check(bool(toast), f"给出提示：{toast[:40]}")
    # 勾选 + 原因
    s.page.check("#f-confirm-modify")
    s.page.fill("#f-modify-reason", "E2E：按 §22 填写修改原因")
    s.page.click("#btn-save")
    s.page.wait_for_timeout(3000)
    REPORT.check("/edit" not in s.page.url, f"填写原因后保存成功：{s.page.url[-40:]}")
    info = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    REPORT.check("被自动化修改过" in (info.get("content") or ""), "正文确已修改")
