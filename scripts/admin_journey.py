"""管理员视角走查 - 真实浏览器操作 + 视口截图。

与 user_journey.py 同套路，区别：
- 账号 admin_demo（管理员视角）
- headless 下 full_page 遇 sticky 元素会超时，一律用视口截图 + animations="disabled"
- 关键步骤与 /api/v1 的地面真值自动比对（同库同账号，读接口不影响页面状态）

用法：先启动 uvicorn（127.0.0.1:8000），再 python scripts/admin_journey.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _paths import ADMIN_JOURNEY_DIR  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
ADMIN = {"username": "admin_demo", "password": "Demo@123456"}
SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)
TMP_DIR = ADMIN_JOURNEY_DIR
TMP_DIR.mkdir(exist_ok=True)

RESULTS: list[tuple[str, str]] = []  # (✅/⚠️, 说明)


def record(ok: bool, msg: str) -> None:
    RESULTS.append(("✅" if ok else "⚠️", msg))
    print(f"  {'✅' if ok else '⚠️'} {msg}")


def shot(page, filename: str) -> str:
    page.screenshot(path=str(SCREENSHOTS_DIR / filename), animations="disabled")
    print(f"  📷 截图: {filename}")
    return filename


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    """直连 API 取地面真值（只读用途）。"""
    req = urllib.request.Request(
        BASE_URL + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def goto(page, url: str) -> None:
    page.goto(BASE_URL + url)
    page.wait_for_load_state("networkidle")


def wait_resp(page, path_part: str, action):
    """执行 action 并等待匹配 path_part 的响应返回。"""
    with page.expect_response(lambda r: path_part in r.url) as ri:
        action()
    return ri.value


def card_value(page, key: str) -> str:
    return page.locator(f'[data-todo="{key}"] .stat-value').inner_text().strip()


def console_watch(page, bucket: list):
    page.on("console", lambda m: bucket.append(f"[console.{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: bucket.append(f"[pageerror] {e}"))


# ---------------------------------------------------------------------------
def make_pdf(path: Path, text: str) -> None:
    """pypdf 生成真实可解析 PDF（上传走魔数+深度校验，伪造字节会被 415 拒绝）。

    写法与 tests/files/generators.make_pdf 一致（同一套校验器必须能解析）。
    """
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=595.28, height=841.89)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 14 Tf 72 760 Td ({text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = stream
    writer.add_metadata({"/Title": text})
    with open(path, "wb") as f:
        writer.write(f)


def main() -> None:
    pdf1 = TMP_DIR / "journey_v1.pdf"
    pdf2 = TMP_DIR / "journey_v2.pdf"
    make_pdf(pdf1, "journey file v1")
    make_pdf(pdf2, "journey file v2")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN")
        page = ctx.new_page()
        console_bucket: list[str] = []
        console_watch(page, console_bucket)

        # ---------------- 登录 ----------------
        # 单点登录（2026-09-03 拍板#8）：每次登录 token_version 自增并使旧会话失效，
        # 「浏览器表单登录 + 此处 API 二次登录」会互相踢下线（#admin-todo 永不出现）。
        # 改为 API 登录一次并注入 Cookie，浏览器与后续 API 比对共用同一 token。
        print("\n[0] 登录 admin_demo（API 登录 + Cookie 注入，适配单点登录）")
        token = api("POST", "/api/v1/auth/login", "", ADMIN)["access_token"]  # type: ignore[arg-type]
        ctx.add_cookies([{"name": "access_token", "value": token, "url": BASE_URL}])
        goto(page, "/")
        page.wait_for_selector("#dashboard-main", timeout=15000)
        record(True, "登录（token 注入）后工作台加载: /")

        # ---------------- 步骤1 工作台（2026-11-11 当前产品语义） ----------------
        print("\n[1] 工作台：搜索、最近资料、最新通知")
        goto(page, "/")
        page.wait_for_selector("#dashboard-main", timeout=15000)
        page.wait_for_timeout(1200)
        record(page.locator("#global-search").count() == 1, "工作台搜索卡存在")
        record(page.locator("#recent-docs-card").count() == 1, "最近资料区块存在")
        record(page.locator("#recent-notices-card").count() == 1, "最新通知区块存在")
        record(page.locator("#todo-banner").count() == 1, "未查看通知条幅容器存在")
        shot(page, "admin_01_dashboard.png")
        # ---------------- 步骤2 新建资料→上传→替换→回收站 ----------------
        print("\n[2] 新建资料→上传→编辑替换→回收站(reason=replaced)")
        goto(page, "/admin/documents/new")
        title = "DEMO-走查替换测试"
        today = time.strftime("%Y-%m-%d")
        page.fill("#f-title", title)
        page.fill("#f-effective-from", today)
        page.set_input_files("#upload-input", pdf1)
        import re as _re
        with page.expect_response(lambda r: "/api/v1/documents" in r.url and r.request.method == "POST"):
            page.click("#btn-submit")
        page.wait_for_url(_re.compile(r"/documents/\d+$"), timeout=8000)
        page.wait_for_load_state("networkidle")
        doc_url = page.url
        doc_id = doc_url.rstrip("/").split("/")[-1]
        record(True, f"新建成功并跳详情: {doc_url.replace(BASE_URL, '')} (id={doc_id})")
        body = api("GET", f"/api/v1/documents/{doc_id}", token)
        record(bool(body.get("current_file")), f"创建时已带文件: {body.get('current_file', {}).get('original_name', body.get('current_file'))}")
        shot(page, "admin_02a_new_document.png")

        goto(page, f"/admin/documents/{doc_id}/edit")
        page.wait_for_selector("#upload-submit")
        page.set_input_files("#upload-input", pdf2)
        page.wait_for_selector("#upload-submit:not([disabled])")
        wait_resp(page, "/file", lambda: page.click("#upload-submit"))
        page.wait_for_load_state("networkidle")
        detail = api("GET", f"/api/v1/documents/{doc_id}", token)
        new_name = detail["current_file"]["original_filename"]
        record("v2" in new_name, f"替换后当前文件: {new_name}")
        shot(page, "admin_02b_after_replace.png")

        goto(page, "/admin/recycle-bin")
        row = page.locator(f"#rb-tbody tr:has-text('{title}')").first
        row_text = row.inner_text() if row.count() else ""
        record("文件被替换" in row_text, f"回收站出现旧文件且原因=文件被替换: {row_text[:80]}")
        shot(page, "admin_02c_recycle_replaced.png")

        # ---------------- 步骤3 新建通知三种对象→创建即发布→阅读统计 ----------------
        print("\n[3] 新建通知（全体+关联资料 / 指定部门 / 指定用户）→创建即发布→阅读统计")
        created_notice_ids = []

        def create_notice(n_title: str, scope: str, *,
                          dept: str | None = None, user: str | None = None,
                          link_doc: str | None = None) -> None:
            goto(page, "/admin/notices/new")
            page.fill("#f-title", n_title)
            page.fill("#f-content", f"走查用通知内容：{n_title}")
            # 通知确认功能已下线：表单不再有 requires_confirmation 开关。
            # radio 是可见原生控件，用高层 check()（带自动重试）；
            # evaluate(el.click()) 在候选列表异步初始化期间存在静默丢点击的时序问题
            page.check(f'input[name="target-scope"][value="{scope}"]')
            def click_candidate(list_sel: str, text: str) -> None:
                """勾选候选并轮询确认生效。

                候选列表输入有 300ms 防抖 + 服务端搜索重渲染：固定延时点击可能
                打在被 innerHTML 替换瞬间的旧 DOM 上（change 静默丢失），故
                点击后校验勾选态，未生效则重试（重渲染后 checked 由选中集驱动）。
                """
                for _ in range(10):
                    page.fill(list_sel + "-filter", text)
                    page.wait_for_timeout(400)
                    box = page.locator(f"{list_sel}-list label:has-text('{text}') input").first
                    box.evaluate("el => el.click()")
                    page.wait_for_timeout(300)
                    if page.locator(f"{list_sel}-list input:checked").count() > 0:
                        return
                raise RuntimeError(f"候选勾选未生效: {list_sel} {text}")

            if scope == "department" and dept:
                click_candidate("#dept", dept)
            if scope == "user" and user:
                click_candidate("#user", user)
            if link_doc:
                page.fill("#doc-search", link_doc)
                page.wait_for_timeout(400)
                page.locator(f"#doc-list label:has-text('{link_doc}') input").first.evaluate("el => el.click()")
            page.click('button[type="submit"]')
            modal = page.locator(".modal-overlay [data-confirm]")
            try:
                modal.wait_for(timeout=5000)
            except Exception:  # noqa: BLE001  等不到弹窗时抓诊断现场，走查继续
                diag = page.evaluate(
                    "() => ({path: location.pathname,"
                    " scope: (document.querySelector('input[name=\"target-scope\"]:checked')||{value:null}).value,"
                    " deptChecked: document.querySelectorAll('#dept-list input:checked').length,"
                    " userChecked: document.querySelectorAll('#user-list input:checked').length,"
                    " toasts: Array.from(document.querySelectorAll('.toast,[class*=toast]')).map(t=>t.textContent)})"
                )
                shot(page, "admin_03x_modal_timeout.png")
                print("失败时控制台日志（近 40 条）:")
                for line in console_bucket[-40:]:
                    print("  [console]", line)
                raise RuntimeError(f"发布确认弹窗未出现，现场: {diag}")
            shot(page, f"admin_03a_publish_confirm_{scope}.png")
            modal.click()
            page.wait_for_url(lambda u: "/admin/notices/new" not in u, timeout=10000)
            page.wait_for_load_state("networkidle")
            # 发布后跳列表页（不带 id），按唯一标题反查
            items = api("GET", f"/api/v1/notices?keyword={urllib.parse.quote(n_title)}&page_size=100", token)["items"]
            nid = str(next(n["id"] for n in items if n["title"] == n_title))
            created_notice_ids.append(nid)
            record(True, f"通知「{n_title}」已发布 id={nid}（跳转 {page.url.replace(BASE_URL, '')}）")

        create_notice("DEMO-走查通知-全体", "all", link_doc="DEMO-员工手册")
        create_notice("DEMO-走查通知-指定部门", "department", dept="研发部")
        create_notice("DEMO-走查通知-指定用户", "user", user="研发张三")

        nid_all = created_notice_ids[0]
        stats = api("GET", f"/api/v1/notices/{nid_all}/statistics", token)
        active_users = api("GET", "/api/v1/users?is_active=1&role=user&page_size=100", token)["total"]
        record(stats["target_user_count"] == active_users,
               f"全体通知应阅读数={stats['target_user_count']} 与启用普通用户数({active_users})一致")

        goto(page, "/notices")
        row = page.locator("tr, .notice-item").filter(has_text="DEMO-走查通知-全体").first
        row.wait_for(timeout=8000)
        bar = row.locator(".rate-wrap")
        bar_text = bar.inner_text() if bar.count() else ""
        record(bar_text.startswith("查看") and "确认" not in bar_text,
               f"行内进度条只按已查看口径显示（实际「{bar_text}」）")
        shot(page, "admin_03b_notice_list_rate.png")

        goto(page, f"/admin/notices/{nid_all}/stats")
        page.wait_for_selector(".stat-num-card .num", timeout=10000)
        page.wait_for_load_state("networkidle")
        stats_text = page.locator("#stats-root").inner_text()
        record(str(stats["target_user_count"]) in stats_text and "未查看" in stats_text,
               f"统计明细页包含 应阅读={stats['target_user_count']} 与未查看等字段")
        shot(page, "admin_03c_notice_stats.png")

        # 种子通知核对：DEMO-重要制度发布 target=5；确认字段已下线
        # 按标题精确查找，不要翻列表：journey 自己刚建了 3 条通知，加上上一轮
        # 的残留，种子通知会被挤出 page_size 的第一页（列表接口上限 100，
        # 这里原来用 50，库里有 55 条时就找不到种子通知了）。
        seed_title = "DEMO-重要制度发布"
        seed_query = urllib.parse.urlencode({"keyword": seed_title, "page_size": 100})
        seed_items = api("GET", f"/api/v1/notices?{seed_query}", token)["items"]
        seed_notice = next(n for n in seed_items if n["title"] == seed_title)
        seed_stats = api("GET", f"/api/v1/notices/{seed_notice['id']}/statistics", token)
        record(seed_stats["target_user_count"] == 5 and "confirmed_count" not in seed_stats,
               f"种子通知「DEMO-重要制度发布」统计: 应阅读={seed_stats['target_user_count']}(期望5)，且无确认字段")
        goto(page, f"/admin/notices/{seed_notice['id']}/stats")
        page.wait_for_load_state("networkidle")
        shot(page, "admin_03d_seed_notice_stats.png")

        # ---------------- 步骤4 批量整理 ----------------
        print("\n[4] 批量整理：勾选多条→打开动作面板→批量分类")
        goto(page, "/documents?uncategorized=1")
        page.wait_for_selector("#doc-tbody .doc-check", timeout=15000)
        boxes = page.locator("#doc-tbody .doc-check")
        n_pick = min(2, boxes.count())
        ids = []
        for i in range(n_pick):
            cb = boxes.nth(i)
            ids.append(int(cb.get_attribute("data-id")))
            cb.check()
        page.wait_for_selector("#doc-bulk-bar:not([hidden])")
        record(True, f"勾选 {n_pick} 条后批量条出现: {page.locator('#bulk-count-num').inner_text()}")
        page.click("#btn-bulk-open")
        page.wait_for_selector("#batch-action-key", timeout=8000)
        page.select_option("#batch-action-key", "set_category")
        options = page.eval_on_selector_all(
            "#batch-category-id option",
            "els => els.map(e => ({value:e.value,text:e.textContent.trim()}))",
        )
        target = next((o for o in options if o["value"] and "DEMO-行政管理" in o["text"]), None)
        if not target:
            target = next((o for o in options if o["value"]), None)
        record(bool(target), f"批量分类目标可选：{target['text'] if target else '无'}")
        page.select_option("#batch-category-id", target["value"])
        preview = api("POST", "/api/v1/documents/batch-actions/set_category/preview", token, {
            "selection": {"mode": "ids", "ids": ids},
            "params": {"category_id": int(target["value"])},
        })
        record(preview.get("affected_count") == len(ids), "批量分类预览影响数正确")
        executed = api("POST", "/api/v1/documents/batch-actions/set_category/execute", token, {
            "selection": {"mode": "ids", "ids": ids},
            "params": {"category_id": int(target["value"])},
            "expected_count": preview["affected_count"],
            "preview_token": preview["preview_token"],
        })
        record(executed.get("affected_count") == len(ids), "批量分类执行完成")
        page.click("[data-batch-close]")
        page.wait_for_timeout(800)
        for did in ids:
            detail = api("GET", f"/api/v1/documents/{did}", token)
            record(detail.get("category_id") == int(target["value"]),
                   f"资料 {did} 已归入目标分类 {target['text']}")
        shot(page, "admin_04_after_bulk.png")
        # ---------------- 步骤5 回收站恢复/永久删除 ----------------
        print("\n[5] 回收站：恢复一条 + 永久删除一条（单次确认红线）")
        goto(page, "/admin/recycle-bin")
        file_rows = page.locator("#rb-tbody tr")
        file_rows.first.wait_for()
        first_name = file_rows.first.locator("button[data-action=restore]").get_attribute("data-name")
        file_rows.first.locator("button[data-action=restore]").click()
        page.locator(".modal-overlay [data-confirm]").wait_for(timeout=5000)
        shot(page, "admin_05a_restore_confirm.png")
        page.locator(".modal-overlay [data-confirm]").click()
        page.wait_for_load_state("networkidle")
        record(True, f"恢复「{first_name}」成功")

        # 走查需自洽：存量条目被历史走查消耗后，恢复完回收站可能已空。
        # 注意空态本身也渲染为一行 tr（「回收站是空的」），须按操作按钮判断；
        # 列表刷新是异步的，先等重渲染稳定再判断。
        page.wait_for_timeout(800)
        has_entries = page.locator(
            "#rb-tbody tr button[data-action=restore], #rb-tbody tr button[data-action=purge]"
        ).count() > 0
        if not has_entries:
            tmp_doc = api("POST", "/api/v1/documents", token, {
                "title": "DEMO-走查-待永久删除", "effective_from": "2026-01-01",
            })
            api("DELETE", f"/api/v1/documents/{tmp_doc['id']}", token)
            goto(page, "/admin/recycle-bin")
            record(True, "回收站已空：自建一条走查条目用于永久删除验证")

        file_rows = page.locator("#rb-tbody tr")
        purge_btn = file_rows.first.locator("button[data-action=purge]")
        purge_btn.wait_for()
        purge_id = purge_btn.get_attribute("data-id")
        purge_name = purge_btn.get_attribute("data-name")
        purge_btn.click()
        page.locator(".modal-overlay [data-confirm]").wait_for(timeout=5000)
        modal_count = page.locator(".modal-overlay").count()
        record(modal_count == 1, f"永久删除确认弹窗数量={modal_count}（§26 红线：一次确认，不得多步）")
        shot(page, "admin_05a_purge_confirm.png")
        page.locator(".modal-overlay [data-confirm]").click()
        page.wait_for_load_state("networkidle")
        # 列表刷新是异步的，轮询等该条目真正消失
        gone = False
        deadline = time.time() + 8
        while time.time() < deadline:
            if page.locator(f"button[data-action=purge][data-id='{purge_id}']").count() == 0:
                gone = True
                break
            time.sleep(0.5)
        record(gone, f"永久删除「{purge_name}」(条目id={purge_id}) 后该条不再显示")
        shot(page, "admin_05b_after_restore_purge.png")

        # ---------------- 步骤6 用户停用→统计分母→恢复 ----------------
        print("\n[6] 用户管理：停用 user_rnd_2 → 通知统计分母同步 → 恢复")
        stats_before = api("GET", f"/api/v1/notices/{seed_notice['id']}/statistics", token)["target_user_count"]
        goto(page, "/admin/users")
        row = page.locator("#user-tbody tr:has-text('研发李四')")
        row.wait_for()
        row.locator("button[data-action=disable]").click()
        page.locator(".modal-overlay [data-confirm]").wait_for(timeout=5000)
        wait_resp(page, "/users/", lambda: page.locator(".modal-overlay [data-confirm]").click())
        page.wait_for_load_state("networkidle")
        row = page.locator("#user-tbody tr:has-text('研发李四')")
        stats_after = api("GET", f"/api/v1/notices/{seed_notice['id']}/statistics", token)["target_user_count"]
        record(stats_after == stats_before - 1,
               f"停用后统计分母 {stats_before} → {stats_after}（通知页面同步）")
        shot(page, "admin_06a_user_disabled.png")
        # 停用后默认列表（仅启用）不再显示该用户 → 切到「停用」筛选再恢复
        page.select_option("#f-active", "0")
        page.click("#btn-search")
        page.wait_for_load_state("networkidle")
        row = page.locator("#user-tbody tr:has-text('研发李四')")
        row.wait_for(timeout=8000)
        with page.expect_response(lambda r: "/users/" in r.url):
            row.locator("button[data-action=enable]").click()  # 恢复不弹确认框，直接 PUT
        page.wait_for_load_state("networkidle")
        stats_restored = api("GET", f"/api/v1/notices/{seed_notice['id']}/statistics", token)["target_user_count"]
        record(stats_restored == stats_before, f"重新启用后分母恢复为 {stats_restored}")
        shot(page, "admin_06b_user_restored.png")

        # ---------------- 步骤7 备份 + 审计 ----------------
        print("\n[7] 备份页执行备份 → 审计日志核对留痕")
        goto(page, "/admin/backups")
        page.select_option("#backup-type", "full")
        wait_resp(page, "/api/v1/backups", lambda: page.click("#btn-create-backup"))
        page.wait_for_timeout(1500)
        page.wait_for_load_state("networkidle")
        first_row = page.locator("#backup-tbody tr").first.inner_text()
        record(("进行中" in first_row) or ("完成" in first_row), f"备份记录: {first_row[:80]}")
        shot(page, "admin_07a_backup.png")

        goto(page, "/admin/audit-logs")
        page.wait_for_selector("#audit-tbody tr, tbody tr", timeout=8000)
        page.wait_for_load_state("networkidle")
        audit_text = page.locator("tbody").first.inner_text()
        hit = [kw for kw in ("备份", "创建", "发布", "整理", "恢复", "删除", "用户") if kw in audit_text]
        record(len(hit) >= 3, f"审计日志首页命中操作类型: {hit}")
        shot(page, "admin_07b_audit_logs.png")

        # ---------------- 汇总 ----------------
        print("\n" + "=" * 60)
        print("管理员走查完成，结果汇总：")
        ok_n = sum(1 for m, _ in RESULTS if m == "✅")
        for m, msg in RESULTS:
            print(f"  {m} {msg}")
        print(f"\n共 {len(RESULTS)} 项：✅ {ok_n} / ⚠️ {len(RESULTS) - ok_n}")
        if console_bucket:
            print(f"\n浏览器控制台 error/warning（去重后 {len(set(console_bucket))} 条）:")
            for line in sorted(set(console_bucket))[:20]:
                print("  " + line)
        browser.close()


if __name__ == "__main__":
    main()
