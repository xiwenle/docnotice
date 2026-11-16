"""M71 管理员资料批量治理：双选择模式、筛选、动作预览/执行与响应式。"""
from __future__ import annotations

import time

from harness import REPORT, case, http


PREFIX = ""


def _docs(app):
    return app.state.get("batch_governance_doc_ids") or []


def _ensure_docs(app) -> list[int]:
    global PREFIX
    ids = _docs(app)
    if ids:
        return ids
    stamp = int(time.time() * 1000)
    PREFIX = f"E2E批量治理{stamp}"
    app.state["batch_governance_prefix"] = PREFIX
    for index in range(17):
        result = http("POST", "/api/v1/documents", app.admin.token, {
            "title": f"{PREFIX}-{index + 1:02d}",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "audience_scope": "all",
        })
        body = result.get("body") or {}
        if result.get("status") not in (200, 201) or not body.get("id"):
            REPORT.fail(f"创建批量治理样本失败：{result.get('status')} {str(body)[:120]}")
            return ids
        ids.append(int(body["id"]))
    app.state["batch_governance_doc_ids"] = ids
    REPORT.check(len(ids) == 17, f"已创建 {len(ids)} 条专用资料")
    return ids


def _prefix(app) -> str:
    return app.state.get("batch_governance_prefix") or PREFIX


def _open_filtered(app):
    _ensure_docs(app)
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody .doc-check", timeout=15000)
    s.page.fill("#f-keyword", _prefix(app))
    s.page.click("#btn-search")
    s.page.wait_for_function(
        "() => !document.querySelector('#doc-tbody .skeleton')",
        timeout=15000,
    )
    s.page.wait_for_timeout(500)
    return s


def _row_ids(page) -> list[int]:
    return page.eval_on_selector_all(
        "#doc-tbody .doc-check",
        "els => els.map(e => Number(e.dataset.id))",
    )


def _go_page(page, number: int) -> None:
    button = page.locator("#doc-pagination button", has_text=str(number)).first
    if button.count() == 0:
        page.get_by_role("button", name=str(number), exact=True).first.click()
    else:
        button.click()
    page.wait_for_function(
        "() => !document.querySelector('#doc-tbody .skeleton')",
        timeout=15000,
    )
    page.wait_for_timeout(500)


def _bulk_number(page, selector: str) -> int:
    return int((page.inner_text(selector) or "0").strip() or "0")


def _select_action(page, key: str) -> None:
    page.select_option("#batch-action-key", key)
    page.wait_for_timeout(150)


@case("BG01", "批量选择：当前页全选/半选、翻页保留隐藏选择、清空与筛选变化清空")
def bg01(app):
    s = _open_filtered(app)
    page = s.page
    ids = _docs(app)
    REPORT.check(len(ids) == 17, f"筛选结果样本数 {len(ids)}")

    first_box = page.locator("#doc-tbody .doc-check").first
    first_box.check()
    page.wait_for_timeout(250)
    REPORT.check(_bulk_number(page, "#bulk-count-num") == 1, "勾选 1 条后总选择数为 1")
    REPORT.check(page.eval_on_selector("#doc-check-all", "e => e.indeterminate") is True,
                 "部分选择时当前页全选框为半选")

    page.click("#doc-check-all")
    page.wait_for_timeout(300)
    page_count = len(_row_ids(page))
    checked = page.eval_on_selector_all("#doc-tbody .doc-check", "els => els.filter(e => e.checked).length")
    REPORT.check(checked == page_count == 15, f"当前页全选：{checked}/{page_count}")
    REPORT.check(_bulk_number(page, "#bulk-count-num") == 15, "全选本页只选择本页 15 条")
    REPORT.check(_bulk_number(page, "#bulk-page-count") == 15 and
                 _bulk_number(page, "#bulk-other-count") == 0,
                 "批量条显示本页 15、其他页 0")

    _go_page(page, 2)
    REPORT.check(len(_row_ids(page)) == 2, "第 2 页显示剩余 2 条")
    REPORT.check(_bulk_number(page, "#bulk-count-num") == 15 and
                 _bulk_number(page, "#bulk-page-count") == 0 and
                 _bulk_number(page, "#bulk-other-count") == 15,
                 "翻页后保留隐藏选择：本页 0、其他页 15")
    REPORT.check(page.eval_on_selector_all(
        "#doc-tbody .doc-check", "els => els.every(e => !e.checked)"), "第 2 页未选行保持未勾选")

    page.locator("#doc-tbody .doc-check").first.check()
    page.click("#doc-check-all")
    page.wait_for_timeout(300)
    REPORT.check(_bulk_number(page, "#bulk-count-num") == 17, "两页全选后共 17 条")

    page.click("#btn-bulk-clear")
    page.wait_for_timeout(250)
    REPORT.check(page.is_hidden("#doc-bulk-bar"), "清空选择后批量条隐藏")

    page.locator("#doc-tbody .doc-check").first.check()
    page.wait_for_timeout(200)
    page.fill("#f-keyword", _prefix(app) + "-不存在")
    page.click("#btn-search")
    page.wait_for_timeout(900)
    REPORT.check(page.is_hidden("#doc-bulk-bar"), "筛选变化后选择被清空")
    page.click("#btn-reset")
    page.wait_for_timeout(900)


@case("BG02", "批量选择：当前筛选全部 filter 模式与逐条排除自洽")
def bg02(app):
    s = _open_filtered(app)
    page = s.page
    page.locator("#doc-tbody .doc-check").first.check()
    page.wait_for_timeout(200)
    REPORT.check(page.is_visible("#btn-bulk-select-all"), "逐条选择后出现「选择当前筛选全部」")
    page.click("#btn-bulk-select-all")
    page.wait_for_timeout(300)

    REPORT.check(_bulk_number(page, "#bulk-count-num") == 17, "filter 模式选中当前筛选全部 17 条")
    REPORT.check(_bulk_number(page, "#bulk-page-count") == 15 and
                 _bulk_number(page, "#bulk-other-count") == 2,
                 "filter 模式显示本页 15、其他页 2")
    REPORT.check(page.is_visible("#bulk-mode-label"), "filter 模式标签可见")
    REPORT.check(page.eval_on_selector_all(
        "#doc-tbody .doc-check", "els => els.every(e => e.checked)"), "filter 模式下当前页全部勾选")

    _go_page(page, 2)
    REPORT.check(page.eval_on_selector_all(
        "#doc-tbody .doc-check", "els => els.every(e => e.checked)"), "filter 模式跨页仍全部勾选")
    page.locator("#doc-tbody .doc-check").first.uncheck()
    page.wait_for_timeout(300)
    REPORT.check(_bulk_number(page, "#bulk-count-num") == 16 and
                 _bulk_number(page, "#bulk-page-count") == 1 and
                 _bulk_number(page, "#bulk-other-count") == 15,
                 "filter 模式逐条排除后计数正确")
    page.click("#btn-bulk-clear")
    page.wait_for_timeout(200)
    REPORT.check(page.is_hidden("#doc-bulk-bar"), "filter 模式可一次清空")


@case("BG03", "管理员筛选：保留标签/有效期覆盖/可见范围/排序/每页")
def bg03(app):
    s = app.admin
    s.goto("/documents")
    page = s.page
    page.wait_for_selector("#doc-tbody tr", timeout=15000)

    for selector in [
        "#f-tag", "#f-cover-from", "#f-cover-to",
        "#f-audience-scope", "#f-sort", "#f-page-size",
    ]:
        REPORT.check(page.locator(selector).count() == 1, f"管理员筛选控件存在：{selector}")

    for selector in [
        "#f-asof", "#f-has-attachment", "#f-file-type",
        "#f-missing-main-file", "#f-expiring",
    ]:
        REPORT.check(page.locator(selector).count() == 0, f"已移除旧筛选控件：{selector}")

    REPORT.check(page.locator("#doc-tbody .doc-meta-audience").count() > 0,
                 "资料标题下显示可见范围徽章")

    page.select_option("#f-audience-scope", "all")
    page.wait_for_timeout(800)
    REPORT.check("audience_scope=all" in page.url, "可见范围筛选写入 URL")
    page.select_option("#f-sort", "updated_desc")
    page.wait_for_timeout(800)
    REPORT.check("sort=updated_desc" in page.url, "最近更新排序写入 URL")
    page.select_option("#f-page-size", "30")
    page.wait_for_timeout(900)
    REPORT.check("page_size=30" in page.url, "每页大小写入 URL")
    page.click("#btn-reset")
    page.wait_for_timeout(900)

    normal = app.user
    normal.goto("/documents")
    normal.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    REPORT.check(normal.page.locator("#f-audience-scope").count() == 0,
                 "普通用户界面不出现管理员可见范围筛选")
    REPORT.check(normal.page.locator("#f-sort").count() == 1,
                 "普通用户界面保留排序")
    REPORT.check(normal.page.locator("#f-page-size").count() == 1,
                 "普通用户界面保留每页")
    REPORT.check(normal.page.locator("#doc-bulk-bar").count() == 0,
                 "普通用户界面不出现批量操作条")


@case("BG04", "批量操作面板：全部动作、参数、预览摘要、执行结果与逐条跳过区域")
def bg04(app):
    s = _open_filtered(app)
    page = s.page
    page.locator("#doc-tbody .doc-check").first.check()
    page.click("#btn-bulk-open")
    page.wait_for_selector("#doc-batch-panel", timeout=5000)

    action_values = page.eval_on_selector_all(
        "#batch-action-key option", "els => els.map(e => e.value)")
    REPORT.check(action_values == [
        "set_category", "clear_category", "add_tags", "remove_tags",
        "set_audience", "archive", "restore",
    ], f"动作面板包含全部动作：{action_values}")

    _select_action(page, "add_tags")
    REPORT.check(page.locator("#batch-tag-ids").count() == 1, "追加标签提供多选参数")
    _select_action(page, "remove_tags")
    REPORT.check(page.locator("#batch-tag-ids").count() == 1, "移除标签提供多选参数")
    _select_action(page, "set_audience")
    REPORT.check(page.locator("#batch-audience-scope").count() == 1, "可见范围提供参数")
    page.select_option("#batch-audience-scope", "department")
    page.wait_for_timeout(300)
    REPORT.check(page.locator("#batch-department-ids").count() == 1, "指定部门提供多选参数")
    page.select_option("#batch-audience-scope", "user")
    page.wait_for_selector("#batch-user-ids input[type=checkbox]", timeout=5000)
    admin_labels = page.locator("#batch-user-ids .batch-check-text").all_inner_texts()
    REPORT.check(not any("admin" in text for text in admin_labels),
                 f"目标人员候选不含管理员：{admin_labels[:3]}")
    user_checks = page.locator("#batch-user-ids input[type=checkbox]")
    if user_checks.count() >= 2:
        user_checks.nth(0).click()
        user_checks.nth(1).click()
        checked = page.locator("#batch-user-ids input[type=checkbox]:checked").count()
        REPORT.check(checked == 2,
                     f"目标人员普通单击即可多选：{checked}/2")
        REPORT.check("已选 2 项" in page.inner_text("#batch-params"),
                     "目标人员多选计数同步")
    else:
        REPORT.note("启用人员不足 2 名，未覆盖普通单击多选")
    _select_action(page, "archive")
    REPORT.check(page.locator("#batch-archive-reason").count() == 1, "归档提供原因输入")
    _select_action(page, "restore")
    REPORT.check("逐条跳过" in page.inner_text("#batch-params"), "恢复动作提示逐条跳过语义")

    _select_action(page, "set_category")
    options = page.eval_on_selector_all(
        "#batch-category-id option",
        "els => els.map(e => ({value: e.value, text: e.textContent.trim()})).filter(o => o.value)",
    )
    if not options:
        REPORT.fail("没有可用于批量设置分类的分类")
        return
    target_category = int(options[0]["value"])
    page.select_option("#batch-category-id", str(target_category))
    page.click("#btn-batch-preview")
    page.wait_for_selector("#batch-preview:not([hidden])", timeout=8000)
    preview_text = page.inner_text("#batch-preview")
    REPORT.check("预览摘要" in preview_text and "匹配" in preview_text,
                 f"预览摘要可见：{preview_text.replace(chr(10), ' ')[:80]}")
    REPORT.check("逐条跳过" in preview_text, "预览中包含逐条跳过区域")
    page.click("#btn-batch-execute")
    page.wait_for_selector("#doc-batch-panel .batch-result-card", timeout=10000)
    result_text = page.inner_text("#doc-batch-panel")
    REPORT.check("已完成" in result_text and "实际变更 1 条" in result_text,
                 f"执行结果弹窗显示实际变更：{result_text.replace(chr(10), ' ')[:80]}")
    REPORT.check("逐条跳过" in result_text, "执行结果保留逐条跳过区域")
    page.click("[data-batch-close]")
    page.wait_for_timeout(700)

    selected_id = _row_ids(page)[0]
    detail = (http("GET", f"/api/v1/documents/{selected_id}", app.admin.token).get("body") or {})
    REPORT.check(detail.get("category_id") == target_category,
                 f"批量分类已落库：document={selected_id} category={detail.get('category_id')}")


@case("BG05", "批量治理在 390/768/900/1024/1440 无页面横向溢出")
def bg05(app):
    s = _open_filtered(app)
    page = s.page
    page.locator("#doc-tbody .doc-check").first.check()
    page.wait_for_timeout(250)
    widths = [390, 768, 900, 1024, 1440]
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(250)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth")
        table_overflow = page.evaluate(
            "() => {const el = document.querySelector('.docs-table-wrap');"
            " return el ? el.scrollWidth - el.clientWidth : 0;}")
        REPORT.check(overflow <= 1, f"{width}px 页面无横向溢出（超出 {overflow}px）")
        REPORT.check(table_overflow <= 1,
                     f"{width}px 资料表容器无横向溢出（超出 {table_overflow}px）")

    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(250)
    page.click("#btn-bulk-open")
    page.wait_for_selector("#doc-batch-panel", timeout=5000)
    modal_overflow = page.evaluate(
        "() => {const m = document.querySelector('#modal-root .modal');"
        " if (!m) return 9999; const r = m.getBoundingClientRect();"
        " return Math.max(0, r.right - window.innerWidth, -r.left,"
        " document.documentElement.scrollWidth - window.innerWidth);}")
    REPORT.check(modal_overflow <= 1,
                 f"390px 批量操作弹窗无横向溢出（超出 {modal_overflow}px）")
    page.click("#modal-root .modal-close")
    page.wait_for_timeout(250)

    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(250)


@case("BG99", "清理 M71 专用资料：批量治理样本移入回收站")
def bg99(app):
    ids = _docs(app)
    if not ids:
        REPORT.note("本次未创建 M71 样本")
        return
    archived = 0
    for doc_id in ids:
        result = http("DELETE", f"/api/v1/documents/{doc_id}", app.admin.token, {
            "reason": "M71 E2E cleanup",
        })
        if result.get("status") in (200, 204):
            archived += 1
    REPORT.check(archived == len(ids), f"已清理 {archived}/{len(ids)} 条批量治理样本（软删除）")
