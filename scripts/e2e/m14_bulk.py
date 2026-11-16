"""M14 资料列表批量操作：全选自洽、批量设置分类/追加标签、批量删除入回收站。

对应 项目规范 §23（管理员能力）与 §26（删除进回收站，可恢复）。
"""
from __future__ import annotations

import re
import time

from harness import REPORT, SHOT_DIR, case, http


def _open(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    s.page.wait_for_timeout(1200)
    return s


def _check_rows(s, n: int) -> list[int]:
    boxes = s.page.query_selector_all("#doc-tbody .doc-check")[:n]
    ids = [int(b.get_attribute("data-id")) for b in boxes]
    for b in boxes:
        b.click()
    s.page.wait_for_timeout(600)
    return ids


@case("X01", "批量条：勾选计数准确，全选框状态与行勾选自洽")
def x01(app):
    s = _open(app)
    REPORT.check(s.page.is_hidden("#doc-bulk-bar"), "初始批量条隐藏")
    boxes = s.page.query_selector_all("#doc-tbody .doc-check")
    if len(boxes) < 2:
        REPORT.note("本页不足 2 条资料，跳过")
        return
    boxes[0].click()
    s.page.wait_for_timeout(500)
    REPORT.check(s.page.inner_text("#bulk-count-num").strip() == "1", "勾选 1 条计数为 1")
    REPORT.check(s.page.is_visible("#doc-bulk-bar"), "批量条已显示")

    s.page.click("#doc-check-all")
    s.page.wait_for_timeout(700)
    n = len(s.page.query_selector_all("#doc-tbody .doc-check"))
    checked = s.page.eval_on_selector_all(
        "#doc-tbody .doc-check", "els => els.filter(e => e.checked).length")
    REPORT.check(checked == n, f"全选后所有行勾选：{checked}/{n}")
    REPORT.check(s.page.inner_text("#bulk-count-num").strip() == str(n),
                 f"计数与行数一致：{s.page.inner_text('#bulk-count-num').strip()}")
    # documents.js 的 syncBulkBar 按行勾选派生，状态自洽（与 audit_logs.js 的旧实现不同）
    REPORT.check(s.page.eval_on_selector("#doc-check-all", "e => e.checked") is True,
                 "「全选本页」保持勾选状态")

    s.page.click("#doc-check-all")
    s.page.wait_for_timeout(700)
    REPORT.check(s.page.is_hidden("#doc-bulk-bar"), "取消全选后批量条隐藏")
    s.page.screenshot(path=str(SHOT_DIR / "X01_bulk_bar.png"))


@case("X02", "批量整理：批量追加多个标签并持久化")
def x02(app):
    s = _open(app)
    ids = _check_rows(s, 2)
    if len(ids) < 2:
        REPORT.note("资料不足 2 条，跳过")
        return
    s.page.click("#btn-bulk-open")
    s.page.wait_for_selector("#batch-action-key", timeout=8000)
    s.page.select_option("#batch-action-key", "add_tags")
    tag_opts = s.page.eval_on_selector_all(
        "#batch-tag-ids input[type=checkbox]",
        "els => els.map(e => ({v:e.value,t:e.parentElement.textContent.trim()}))")
    tag = next((o for o in tag_opts if o["v"] and o["v"] != ""), None)
    if not tag:
        REPORT.note("无可用标签，跳过")
        return
    s.page.check(f'#batch-tag-ids input[value="{tag["v"]}"]')
    s.page.click("#btn-batch-preview")
    s.page.wait_for_selector("#batch-preview:not([hidden])", timeout=8000)
    REPORT.check("预览摘要" in s.page.inner_text("#batch-preview"), "批量标签预览摘要可见")
    s.page.click("#btn-batch-execute")
    s.page.wait_for_selector(".batch-result-card", timeout=10000)
    REPORT.check("已完成" in s.page.inner_text(".batch-result-card"), "批量标签执行完成")
    s.page.click("[data-batch-close]")
    for did in ids:
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        names = [t.get("name") for t in (info.get("tags") or [])]
        if names and tag["t"] in names:
            REPORT.check(True, f"资料 {did} 已带上标签「{tag['t']}」")
            break
    else:
        REPORT.check(False, f"未观察到标签「{tag['t']}」落到所选资料上")
    s.page.screenshot(path=str(SHOT_DIR / "X02_bulk_apply.png"))

@case("X03", "批量删除：一次移入回收站且可恢复（软删除语义）")
def x03(app):
    s = _open(app)
    made = []
    for i in range(2):
        s.goto("/admin/documents/new")
        s.page.wait_for_selector("#f-title", timeout=12000)
        s.page.fill("#f-title", f"E2E批量删除{i}-{int(time.time())}")
        s.page.fill("#f-effective-from", "2026-01-01")
        s.page.click("#btn-submit")
        s.page.wait_for_url(re.compile(r".*/documents/\d+"), timeout=25000)
        s.page.wait_for_load_state("networkidle")
        made.append(int(re.search(r"/documents/(\d+)", s.page.url).group(1)))
    REPORT.check(len(made) == 2, f"已创建 {len(made)} 条专用资料")
    s = _open(app)
    s.page.fill("#f-keyword", "E2E批量删除")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1500)
    for did in made:
        cb = s.page.query_selector(f'#doc-tbody .doc-check[data-id="{did}"]')
        if cb:
            cb.click()
    s.page.wait_for_timeout(400)
    REPORT.check(s.page.inner_text("#bulk-count-num").strip() == "2", "已勾选 2 条")
    s.page.click("#btn-bulk-open")
    s.page.wait_for_selector("#batch-action-key", timeout=8000)
    s.page.select_option("#batch-action-key", "archive")
    s.page.fill("#batch-archive-reason", "E2E 批量删除软删除回归")
    s.page.click("#btn-batch-preview")
    s.page.wait_for_selector("#batch-preview:not([hidden])", timeout=8000)
    s.page.click("#btn-batch-execute")
    s.page.wait_for_selector(".batch-result-card", timeout=10000)
    REPORT.check("已完成" in s.page.inner_text(".batch-result-card"), "批量删除执行完成")
    s.page.click("[data-batch-close]")
    s.page.wait_for_timeout(2000)
    archived = []
    for did in made:
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        if info.get("archived"):
            archived.append(did)
    REPORT.check(len(archived) == 2, f"{len(archived)}/2 条已进入删除态")
    rb = (http("GET", "/api/v1/recycle-bin?page=1&page_size=100", app.admin.token)["body"] or {}).get("items") or []
    in_bin = [e for e in rb if str(e.get("document_id")) in {str(d) for d in made}]
    REPORT.check(bool(in_bin), f"回收站出现对应条目（{len(in_bin)} 条）")
    for e in in_bin:
        eid = e.get("entry_id") or e.get("id")
        http("POST", f"/api/v1/recycle-bin/{eid}/restore", app.admin.token, {})
    back = 0
    for did in made:
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        if not info.get("archived"):
            back += 1
    REPORT.check(back == len(made), f"恢复 {back}/{len(made)} 条，软删除可回滚")
