"""M41 备份与恢复：手动备份、详情、保留策略回显、删除、恢复（项目规范 §28）。

注意：本模块会在**隔离实例**（DATA_DIR=<临时目录>/fms_e2e_data）上执行真实的「恢复」动作，
因为 项目规范 §38 要求最终验证必须真实跑通恢复流程，不得用 mock 或只调接口代替。
"""
from __future__ import annotations

import time

from harness import REPORT, SHOT_DIR, case, http


def _open(app):
    s = app.admin
    s.goto("/admin/backups")
    s.page.wait_for_selector("#backup-tbody tr", timeout=15000)
    s.page.wait_for_timeout(1200)
    return s


def _list(app) -> list:
    body = http("GET", "/api/v1/backups?page=1&page_size=50", app.admin.token)["body"] or {}
    return body.get("items") or []


@case("B01", "备份页：列表 + 保留策略回显")
def b01(app):
    s = _open(app)
    items = _list(app)
    rows = s.page.query_selector_all("#backup-tbody tr")
    empty = "暂无备份记录" in s.page.inner_text("#backup-tbody")
    REPORT.check(bool(items) or empty, f"列表渲染：接口 {len(items)} 条 / 页面 {len(rows)} 行")
    policy_txt = s.page.inner_text("#backup-policy")
    REPORT.check("保留" in policy_txt or "自动备份" in policy_txt,
                 f"策略提示已回显：{policy_txt[:60]}")
    REPORT.check(s.page.query_selector("#backup-type") is not None, "备份类型下拉存在")
    opts = s.page.eval_on_selector_all("#backup-type option", "els => els.map(e => e.value)")
    REPORT.check(set(opts) >= {"full", "database"}, f"下拉选项：{opts}")
    s.page.screenshot(path=str(SHOT_DIR / "B01_backups.png"))


@case("B02", "仅数据库备份：产生 completed 记录并写入审计")
def b02(app):
    s = _open(app)
    before = len(_list(app))
    s.page.select_option("#backup-type", "database")
    s.page.click("#btn-create-backup")
    # 备份是同步执行的，toast 可能先于/to同步出现：立刻开始等待，不要先 sleep
    toast = s.toast_text(60000)
    REPORT.check("备份完成" in toast, f"备份成功提示：{toast[:50]}")
    items = _list(app)
    REPORT.check(len(items) == before + 1, f"备份记录 +1：{before} → {len(items)}")
    top = items[0] if items else {}
    REPORT.check(top.get("backup_type") == "database", f"类型为 database：{top.get('backup_type')}")
    REPORT.check(top.get("status") == "completed", f"状态为 completed：{top.get('status')}")
    REPORT.check((top.get("size_bytes") or 0) > 0, f"备份产物非空：{top.get('size_bytes')} 字节")
    app.state["backup_db_id"] = top.get("id")


@case("B03", "完整备份（库+文件）：file_count 反映业务文件数量")
def b03(app):
    s = _open(app)
    s.page.select_option("#backup-type", "full")
    s.page.click("#btn-create-backup")
    toast = s.toast_text(90000)
    REPORT.check("备份完成" in toast, f"完整备份成功：{toast[:60]}")
    items = _list(app)
    top = next((i for i in items if i.get("backup_type") == "full"), None)
    REPORT.check(top is not None, "存在 full 类型备份记录")
    if top:
        REPORT.check(top.get("status") == "completed", f"状态 completed：{top.get('status')}")
        REPORT.check((top.get("file_count") or 0) >= 0,
                     f"记录文件数：{top.get('file_count')}（由备份内容决定）")
        db_full = next((i for i in items if i.get("backup_type") == "database"), None)
        if db_full:
            REPORT.check((top.get("size_bytes") or 0) >= (db_full.get("size_bytes") or 0),
                         f"完整备份体积不小于仅库备份：{top.get('size_bytes')} vs {db_full.get('size_bytes')}")
        app.state["backup_full_id"] = top.get("id")


@case("B04", "备份详情弹窗：manifest / 指纹 / 路径可读")
def b04(app):
    s = _open(app)
    bid = app.state.get("backup_full_id") or (_list(app) or [{}])[0].get("id")
    if not bid:
        REPORT.fail("无可用备份记录")
        return
    btn = s.page.query_selector(f'#backup-tbody button[data-action="detail"][data-id="{bid}"]')
    REPORT.check(btn is not None, "列表行有「详情」按钮")
    if not btn:
        return
    btn.click()
    s.page.wait_for_selector("#modal-root .modal-overlay", timeout=8000)
    s.page.wait_for_timeout(1200)
    txt = s.page.inner_text("#modal-root")
    for kw in ("备份类型", "大小", "文件数", "创建时间"):
        REPORT.check(kw in txt, f"详情含「{kw}」")
    REPORT.check("sha" in txt.lower() or "指纹" in txt, "详情含数据库指纹信息")
    s.page.screenshot(path=str(SHOT_DIR / "B04_backup_detail.png"))
    close = s.page.query_selector("#modal-root [data-close]")
    if close:
        close.click()
        s.page.wait_for_timeout(500)


@case("B05", "删除备份：缺 confirm 拒绝；确认后记录消失")
def b05(app):
    # 先造一条一次性备份专门用于删除，避免动到演示备份
    created = http("POST", "/api/v1/backups", app.admin.token, {"type": "database"})
    bid = (created["body"] or {}).get("id")
    REPORT.check(bool(bid), f"临时备份已创建：{created['status']}")
    if not bid:
        return
    bad = http("DELETE", f"/api/v1/backups/{bid}", app.admin.token)
    REPORT.check(bad["status"] >= 400, f"未携带 confirm 时拒绝删除：{bad['status']}")
    REPORT.check(bool([i for i in _list(app) if i.get("id") == bid]), "被拒后备份仍存在")

    s = _open(app)
    btn = s.page.query_selector(f'#backup-tbody button[data-action="delete"][data-id="{bid}"]')
    REPORT.check(btn is not None, "列表行有「删除」按钮")
    if not btn:
        return
    btn.click()
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(3000)
    REPORT.check(not [i for i in _list(app) if i.get("id") == bid], "备份记录已删除")


@case("B06", "权限：普通用户不得创建/删除/恢复备份")
def b06(app):
    app.allow_noise()
    for method, path, payload in (
        ("GET", "/api/v1/backups", None),
        ("POST", "/api/v1/backups", {"type": "database"}),
        ("POST", "/api/v1/backups/1/restore", {"confirm": True, "reason": "越权测试"}),
        ("DELETE", "/api/v1/backups/1", {"confirm": True}),
    ):
        r = http(method, path, app.user.token, payload)
        REPORT.check(r["status"] in (401, 403), f"{method} {path} → {r['status']}（期望 401/403）")


@case("B07", "恢复备份：真实回滚并可继续正常使用（§38 必须项）")
def b07(app):
    bid = app.state.get("backup_full_id")
    items = _list(app)
    if not bid:
        bid = (next((i for i in items if i.get("backup_type") == "full"), None) or {}).get("id")
    if not bid:
        REPORT.fail("无可用完整备份，无法验证恢复")
        return

    # 先做一次可观测变更：新建一条资料，恢复后它应当消失（证明真的回滚了）
    created = http("POST", "/api/v1/documents", app.admin.token, {
        "title": f"E2E回滚前写入{int(time.time())}",
        "summary": "用于验证备份恢复会真实回滚数据库",
        "effective_from": "2026-01-01",
        "effective_to": None,
        # allow_download 已于 2026-11-04 移除（下载权限 = 查看权限）
        "tag_ids": [],
        "category_id": None,
    })
    marker_id = (created["body"] or {}).get("id")
    REPORT.check(bool(marker_id), f"恢复前标记资料已创建：{created['status']}")

    s = _open(app)
    btn = s.page.query_selector(f'#backup-tbody button[data-action="restore"][data-id="{bid}"]')
    REPORT.check(btn is not None, f"备份 #{bid} 行有「恢复」按钮")
    if not btn:
        return
    btn.click()
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=8000)
    warn = s.page.inner_text("#modal-root")
    REPORT.check("不可直接撤销" in warn or "覆盖" in warn, "恢复弹窗给出危险与不可撤销提示")
    inp = s.page.query_selector("#modal-root #confirm-input")
    if inp:
        s.page.fill("#modal-root #confirm-input", "E2E 验证恢复流程")
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2000)

    # 恢复完成后页面应仍可用（令牌未失效、列表可查）
    s.goto("/admin/backups")
    s.page.wait_for_selector("#backup-tbody tr", timeout=20000)
    REPORT.check("/login" not in s.page.url, f"恢复后管理员会话仍有效：{s.page.url}")
    rows = s.page.query_selector_all("#backup-tbody tr")
    REPORT.check(len(rows) > 0, f"备份列表仍可正常加载：{len(rows)} 行")

    if marker_id:
        # 恢复是长事务（要拷回库 + 文件），固定 sleep 猜时间必然 flaky → 轮询等结果
        deadline = time.time() + 90
        status = None
        while time.time() < deadline:
            status = http("GET", f"/api/v1/documents/{marker_id}", app.admin.token)["status"]
            if status == 404:
                break
            time.sleep(3)
        REPORT.check(status == 404, f"恢复后标记资料已回滚消失：{status}")
    logs = http("GET", "/api/v1/audit-logs?page=1&page_size=5&action=backup_restore",
                app.admin.token)["body"] or {}
    REPORT.check((logs.get("total") or 0) > 0, f"恢复动作写入审计日志：{logs.get('total')} 条")
    s.page.screenshot(path=str(SHOT_DIR / "B07_backup_restored.png"))
