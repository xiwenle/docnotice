"""M40 回收站：文件条目恢复/永久删除、已删除通知恢复/彻底删除、清空回收站。

对应 项目规范 §26（默认不物理删除，清除须二次确认）、§26.1（用户/部门/分类物理删除免二次
确认，但回收站的「文件/通知」清除不在该豁免范围内，仍要求 confirm=true）。
"""
from __future__ import annotations

import re
import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # _paths.py
from harness import REPORT, SHOT_DIR, case, http

from _paths import E2E_FILES_DIR as FILES  # noqa: E402
PDF = FILES / "员工手册2026.pdf"


def _open(app):
    s = app.admin
    s.goto("/admin/recycle-bin")
    s.page.wait_for_selector("#rb-tbody tr", timeout=15000)
    s.page.wait_for_timeout(1200)
    return s


def _rb_items(app, reason: str = "") -> list:
    q = "/api/v1/recycle-bin?page=1&page_size=100"
    if reason:
        q += f"&reason={reason}"
    return (http("GET", q, app.admin.token)["body"] or {}).get("items") or []


def _create_doc_with_file(app, title: str) -> str:
    s = app.admin
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=12000)
    s.page.fill("#f-title", title)
    s.page.fill("#f-effective-from", "2026-01-01")
    s.page.set_input_files("#upload-input", str(PDF))
    s.page.click("#btn-submit")
    s.page.wait_for_url(re.compile(r".*/documents/\d+"), timeout=25000)
    s.page.wait_for_load_state("networkidle")
    return re.search(r"/documents/(\d+)", s.page.url).group(1)


def _archive_doc(app, did: str) -> None:
    s = app.admin
    s.goto(f"/documents/{did}")
    s.page.wait_for_selector("#btn-archive", timeout=12000)
    s.page.click("#btn-archive")
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=8000)
    inp = s.page.query_selector("#modal-root #confirm-input")
    if inp:
        s.page.fill("#modal-root #confirm-input", "E2E 自动化归档")
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2500)


def _click_row_btn(s, tbody, action, name_hint: str = ""):
    """在指定表体里按 data-action 定位按钮（可按可见文本二次过滤）。"""
    rows = s.page.query_selector_all(f"{tbody} tr")
    for row in rows:
        btn = row.query_selector(f'button[data-action="{action}"]')
        if not btn:
            continue
        if name_hint and name_hint not in row.inner_text():
            continue
        return btn
    return None


@case("K01", "回收站列表：渲染 + 原因类型筛选一致")
def k01(app):
    s = _open(app)
    total = len(_rb_items(app))
    rows = s.page.query_selector_all("#rb-tbody tr")
    empty = "回收站是空的" in s.page.inner_text("#rb-tbody")
    REPORT.check(total == 0 or len(rows) > 0,
                 f"接口 {total} 条 / 页面 {len(rows)} 行（空态：{empty}）")
    if total == 0:
        REPORT.note("回收站当前为空，后续用例会先制造条目")
        return

    # 逐个筛选值比对：页面行数 <= 接口筛选结果，且每条都带对应原因标签
    for reason, label in (("replaced", "文件被替换"), ("archived", "资料归档"), ("deleted", "资料删除")):
        s.page.select_option("#f-reason", reason)
        s.page.click("#btn-search")
        s.page.wait_for_timeout(1500)
        page_rows = s.page.query_selector_all("#rb-tbody tr")
        api_n = len(_rb_items(app, reason))
        texts = s.page.inner_text("#rb-tbody")
        if api_n == 0:
            REPORT.check("回收站是空的" in texts or len(page_rows) == 0,
                         f"筛选 {reason}：空态展示正确")
        else:
            REPORT.check(len(page_rows) == min(api_n, 15),
                         f"筛选 {reason}：页面 {len(page_rows)} 行 vs 接口 {api_n} 条")
            REPORT.check(label in texts, f"筛选 {reason}：存在「{label}」标签")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1200)
    s.page.screenshot(path=str(SHOT_DIR / "K01_recycle_bin.png"))


@case("K02", "资料删除 → 进回收站 → 从页面恢复整条资料")
def k02(app):
    title = f"E2E回收站资料{int(time.time())}"
    did = _create_doc_with_file(app, title)
    _archive_doc(app, did)

    entries = [e for e in _rb_items(app) if str(e.get("document_id")) == str(did)]
    REPORT.check(bool(entries), f"资料已删除并进入回收站（doc={did}，条目 {len(entries)} 条）")
    if not entries:
        return
    info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    REPORT.check(info.get("archived") is True or info.get("status") in ("archived", "deleted"),
                 f"资料状态标记删除：archived={info.get('archived')} status={info.get('status')}")

    s = _open(app)
    btn = _click_row_btn(s, "#rb-tbody", "restore", title)
    REPORT.check(btn is not None, "回收站找到该资料的「恢复」按钮")
    if not btn:
        return
    btn.click()
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2500)

    info2 = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    REPORT.check(not info2.get("archived"),
                 f"恢复后资料回到正常状态：archived={info2.get('archived')} status={info2.get('status')}")
    app.state["rb_doc_id"] = did


@case("K03", "恢复被替换的文件：仅文件回库，不顶替当前文件")
def k03(app):
    items = _rb_items(app, "replaced")
    target = next((e for e in items if not e.get("restored_at")), None)
    if not target:
        REPORT.note("无未恢复的 replaced 条目，跳过")
        return
    did = str(target.get("document_id"))
    before = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    cur_before = before.get("current_file", {}).get("original_filename")

    s = _open(app)
    btn = _click_row_btn(s, "#rb-tbody", "restore", str(target.get("original_filename")))
    REPORT.check(btn is not None, f"找到「{target.get('original_filename')}」的恢复按钮")
    if not btn:
        return
    btn.click()
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2500)

    after = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    cur_after = after.get("current_file", {}).get("original_filename")
    REPORT.check(cur_after == cur_before,
                 f"当前文件未被顶替：{cur_before} → {cur_after}")


@case("K04", "永久删除：缺 confirm → 400；确认后条目消失且不可再恢复")
def k04(app):
    title = f"E2E永久删除资料{int(time.time())}"
    did = _create_doc_with_file(app, title)
    _archive_doc(app, did)
    entries = [e for e in _rb_items(app) if str(e.get("document_id")) == str(did)]
    if not entries:
        REPORT.fail("未产生回收站条目，无法验证永久删除")
        return
    eid = entries[0].get("entry_id") or entries[0].get("id")

    # §26：唯一物理删除入口，缺省 confirm → 拒绝
    no_confirm = http("DELETE", f"/api/v1/recycle-bin/{eid}", app.admin.token)
    REPORT.check(no_confirm["status"] >= 400,
                 f"未携带 confirm 时拒绝物理删除：{no_confirm['status']}")
    still = [e for e in _rb_items(app) if str(e.get("document_id")) == str(did)]
    REPORT.check(bool(still), "被拒后条目仍在回收站（未静默删除）")

    s = _open(app)
    btn = _click_row_btn(s, "#rb-tbody", "purge", title)
    REPORT.check(btn is not None, "找到「永久删除」按钮")
    if not btn:
        return
    btn.click()
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2500)
    gone = [e for e in _rb_items(app) if str(e.get("document_id")) == str(did)]
    REPORT.check(not gone, f"条目已从回收站物理移除（剩余 {len(_rb_items(app))} 条）")


@case("K05", "已删除通知区块：取消通知后入回收站并可恢复")
def k05(app):
    """2026-11-04 起删除通知统一走 /cancel；草稿下线后 DELETE /notices/{id} 已移除。"""
    title = f"E2E回收站通知{int(time.time())}"
    created = http("POST", "/api/v1/notices", app.admin.token, {
        "title": title,
        "content": "E2E：用于验证回收站已删除通知区块",
        "notice_type": "发布",
        "target": {"scope": "all"},
    })
    REPORT.check(created["status"] in (200, 201), f"通知已创建：{created['status']}")
    if created["status"] not in (200, 201):
        return
    nid = (created["body"] or {}).get("id")
    dele = http("POST", f"/api/v1/notices/{nid}/cancel", app.admin.token,
                {"reason": "E2E 回收站验证"})
    REPORT.check(dele["status"] in (200, 204), f"通知已取消（进回收站）：{dele['status']}")

    s = _open(app)
    s.page.wait_for_timeout(1200)
    n_rows = s.page.query_selector_all("#notice-rb-tbody tr")
    body_txt = s.page.inner_text("#notice-rb-tbody")
    REPORT.check(title in body_txt, f"回收站通知区出现该通知（{len(n_rows)} 行）")

    btn = _click_row_btn(s, "#notice-rb-tbody", "n-restore", title)
    REPORT.check(btn is not None, "通知行有「恢复」按钮")
    if btn:
        btn.click()
        s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
        s.page.click("#modal-root [data-confirm]")
        s.page.wait_for_timeout(2500)
        after = s.page.inner_text("#notice-rb-tbody")
        REPORT.check(title not in after, "恢复后已从回收站通知区移除")
        detail = http("GET", f"/api/v1/notices/{nid}", app.admin.token)
        REPORT.check(detail["status"] == 200, f"通知可再次访问：{detail['status']}")
        app.state["rb_notice_id"] = nid
    s.page.screenshot(path=str(SHOT_DIR / "K05_recycle_notices.png"))


@case("K06", "权限：普通用户不得读写回收站（后端拦截，非仅隐藏按钮）")
def k06(app):
    app.allow_noise()
    paths = [
        ("GET", "/api/v1/recycle-bin"),
        ("GET", "/api/v1/recycle-bin/notices"),
        ("POST", "/api/v1/recycle-bin/purge-all"),
    ]
    for method, path in paths:
        r = http(method, path, app.user.token, {} if method == "POST" else None)
        REPORT.check(r["status"] in (401, 403), f"{method} {path} → {r['status']}（期望 401/403）")

    items = _rb_items(app)
    if items:
        eid = items[0].get("entry_id") or items[0].get("id")
        rp = http("POST", f"/api/v1/recycle-bin/{eid}/restore", app.user.token, {})
        rd = http("DELETE", f"/api/v1/recycle-bin/{eid}", app.user.token, {"confirm": True})
        REPORT.check(rp["status"] in (401, 403), f"普通用户恢复 → {rp['status']}")
        REPORT.check(rd["status"] in (401, 403), f"普通用户永久删除 → {rd['status']}")
        REPORT.check(bool(_rb_items(app)), "越权操作未改变回收站内容")

    # 页面级：app/routers/pages.py:257 _admin_guard —— 非 admin 一律 302 到首页
    u = app.user
    u.goto("/admin/recycle-bin", ensure_session=True)
    u.page.wait_for_timeout(1500)
    landed_on_admin = "/admin/recycle-bin" in u.page.url
    body = u.page.inner_text("body")
    REPORT.check(not landed_on_admin,
                 f"普通用户访问回收站页被重定向到：{u.page.url}")
    REPORT.check("#rb-tbody" not in (u.page.inner_html("body") or ""),
                 "落地页不含回收站表格 DOM")
    if "无权" not in body and "没有权限" not in body:
        REPORT.note("SSR 守卫只做静默重定向，未给用户「无权访问」提示（UX 观察项，非功能缺陷）")


@case("K07", "清空回收站：开关可见性与一次性清空（含审计留痕）")
def k07(app):
    s = _open(app)
    visible = s.page.is_visible("#btn-purge-all")
    n_files = len(_rb_items(app))
    n_notices = len((http("GET", "/api/v1/recycle-bin/notices", app.admin.token)["body"]
                     or {}).get("items") or [])
    REPORT.check(visible == (n_files + n_notices > 0),
                 f"「清空回收站」按钮可见={visible}（文件 {n_files} + 通知 {n_notices}）")
    if n_files + n_notices == 0:
        REPORT.note("回收站为空，跳过实际清空动作")
        return
    before_logs = (http("GET", "/api/v1/audit-logs?page=1&page_size=5&action=recycle_purge",
                        app.admin.token)["body"] or {}).get("total") or 0
    s.page.click("#btn-purge-all")
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
    msg = s.page.inner_text("#modal-root")
    REPORT.check("永久删除" in msg or "不可恢复" in msg, "清空弹窗给出危险提示")
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(3000)
    toast = s.toast_text(4000)
    REPORT.check("已清空" in toast, f"清空结果提示：{toast[:60]}")
    REPORT.check(len(_rb_items(app)) == 0, "清空后文件条目为 0")
    after_logs = (http("GET", "/api/v1/audit-logs?page=1&page_size=5&action=recycle_purge",
                       app.admin.token)["body"] or {}).get("total") or 0
    REPORT.check(after_logs > before_logs, f"清空动作写入审计日志：{before_logs} → {after_logs}")
    s.page.screenshot(path=str(SHOT_DIR / "K07_recycle_purged.png"))
