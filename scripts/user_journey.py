"""普通用户视角走流程 - 真实浏览器操作 + 截图"""
import os
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from pathlib import Path

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
USER_CREDENTIALS = {"username": "user_rnd_1", "password": "Demo@123456"}
SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def take_screenshot(page, filename):
    """截图并打印信息"""
    filepath = SCREENSHOTS_DIR / filename
    page.screenshot(path=str(filepath), full_page=True)
    print(f"✓ 截图: {filename}")
    return filepath


def user_journey():
    """完整用户视角走流程"""
    print("=" * 60)
    print("开始普通用户视角走流程...")
    print("=" * 60)

    with sync_playwright() as p:
        # 使用系统 Chrome（已验证可用）
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN"
        )
        page = context.new_page()

        try:
            # === 步骤1: 登录 → 工作台 ===
            print("\n[1/9] 登录 → 工作台")
            page.goto(f"{BASE_URL}/login")
            page.wait_for_load_state("networkidle")
            take_screenshot(page, "user_01_login.png")

            # 填写登录表单
            page.fill('input[name="username"]', USER_CREDENTIALS["username"])
            page.fill('input[name="password"]', USER_CREDENTIALS["password"])
            page.click('button[type="submit"]')
            # 2026-10-26 修正（用例竞态，非产品缺陷）：
            # 登录成功是「JSON 登录响应 → window.location.href 编码跳转」，
            # networkidle 早于跳转完成，原实现读到还在 /login，导致后续所有步骤
            # 因「未登录/未加载完」空转出 ⚠️。改为等待 URL 真正离开 /login
            # （与 admin_journey.py / m01_auth.py 同款 wait_for_url 用法）。
            try:
                page.wait_for_url(
                    lambda u: "/login" not in u, timeout=15000
                )
            except PlaywrightTimeoutError:
                pass  # 超时则由下面的 URL 断言给出明确告警，不静默吞掉
            page.wait_for_load_state("networkidle")

            # 验证跳转
            current_url = page.url
            if "/dashboard" in current_url or current_url.rstrip("/").endswith(BASE_URL):
                print(f"✓ 登录成功，跳转到: {current_url}")
            else:
                print(f"⚠️  未跳转到工作台，当前URL: {current_url}")

            take_screenshot(page, "user_02_dashboard.png")

            # 检查待办条幅
            todo_banner = page.locator(".todo-banner")
            if todo_banner.count() > 0:
                print(f"✓ 待办条幅存在: {todo_banner.text_content()[:100]}")
            else:
                print("ℹ️  无待办条幅（没有未读通知）")

            # === 步骤2: 全局搜索 ===
            print("\n[2/9] 全局搜索")
            search_inputs = page.locator('input[placeholder*="搜索"], input[name="keyword"], input#search')
            if search_inputs.count() > 0:
                search_inputs.first.fill("员工手册")
                page.press('input[placeholder*="搜索"], input[name="keyword"], input#search', "Enter")
                page.wait_for_load_state("networkidle")
                take_screenshot(page, "user_03_search.png")
                print("✓ 搜索完成")
            else:
                print("⚠️  未找到全局搜索框")

            # === 步骤3: 分类树浏览 ===
            print("\n[3/9] 分类树浏览")
            page.goto(f"{BASE_URL}/documents")
            page.wait_for_load_state("networkidle")
            take_screenshot(page, "user_04_documents.png")

            category_nodes = page.locator("#category-tree .tree-node-content[data-cat]")
            if category_nodes.count() > 0:
                print(f"✓ 找到 {category_nodes.count()} 个分类节点")
                category_nodes.first.click()
                page.wait_for_load_state("networkidle")
                take_screenshot(page, "user_05_category_filtered.png")
            else:
                print("⚠️  未找到分类树节点")

            # === 步骤4: 资料详情与预览 ===
            print("\n[4/9] 资料详情与预览")
            
            # 先清除分类过滤，显示所有资料
            page.goto(f"{BASE_URL}/documents")
            page.wait_for_load_state("networkidle")
            
            doc_links = page.locator("a[href*='/documents/']")
            if doc_links.count() > 0:
                print(f"✓ 找到 {doc_links.count()} 个资料链接")
                doc_links.first.click()
                page.wait_for_load_state("networkidle")
                take_screenshot(page, "user_06_document_detail.png")

                # 检查预览区域
                preview_areas = page.locator(".preview-body, .pdf-frame, iframe, object[data]")
                if preview_areas.count() > 0:
                    print("✓ 预览区域存在")
                else:
                    print("ℹ️  无预览区域（可能该资料无文件）")
            else:
                print("⚠️  未找到资料链接，尝试点击'全部'tab")
                # 尝试点击全部tab
                all_tab = page.locator(".filter-tab:has-text('全部'), button:has-text('全部')")
                if all_tab.count() > 0:
                    all_tab.first.click()
                    page.wait_for_load_state("networkidle")
                    doc_links = page.locator("a[href*='/documents/']")
                    if doc_links.count() > 0:
                        doc_links.first.click()
                        page.wait_for_load_state("networkidle")
                        take_screenshot(page, "user_06_document_detail.png")
                        print("✓ 通过'全部'tab进入详情页")
                    else:
                        print("⚠️  仍然没有资料链接")

            # === 步骤5: 下载权限测试 ===
            print("\n[5/9] 下载 vs 仅预览")
            page.goto(f"{BASE_URL}/documents")
            page.wait_for_load_state("networkidle")
            
            # 进入第一个有文件的资料详情
            doc_links = page.locator("a[href*='/documents/']")
            if doc_links.count() > 0:
                doc_links.first.click()
                page.wait_for_load_state("networkidle")
                
                download_btns = page.locator("a:has-text('下载'), button:has-text('下载')")
                if download_btns.count() > 0:
                    print(f"✓ 找到 {download_btns.count()} 个下载按钮")
                else:
                    print("ℹ️  未找到下载按钮（可能该资料设置为仅预览或无文件）")
            else:
                print("⚠️  未找到资料链接")

            take_screenshot(page, "user_07_download_check.png")

            # === 步骤6: 通知三tab ===
            print("\n[6/9] 通知三tab")
            page.goto(f"{BASE_URL}/notices")
            page.wait_for_load_state("networkidle")
            take_screenshot(page, "user_08_notices_all.png")
            print("✓ 全部通知")

            # 切换到未读
            unread_tabs = page.locator(".filter-tab:has-text('未读'), button:has-text('未读'), a:has-text('未读')")
            if unread_tabs.count() > 0:
                unread_tabs.first.click()
                page.wait_for_load_state("networkidle")
                take_screenshot(page, "user_09_notices_unread.png")
                print("✓ 未读通知")

            # 通知确认已下线：只保留全部/未读两个 tab
            confirm_tabs = page.locator(".filter-tab:has-text('待确认'), button:has-text('待确认'), a:has-text('待确认')")
            if confirm_tabs.count() > 0:
                take_screenshot(page, "user_10_notices_pending_unexpected.png")
                print("⚠️  通知确认已下线，但仍发现「待确认」tab")
            else:
                print("✓ 待确认 tab 已移除")

            # === 步骤7: 阅读通知（打开详情即已查看） ===
            print("\n[7/9] 阅读通知（打开详情即视为已查看）")
            notice_links = page.locator("a[href*='/notices/']")
            if notice_links.count() > 0:
                notice_links.first.click()
                page.wait_for_load_state("networkidle")
                take_screenshot(page, "user_11_notice_detail.png")

                take_screenshot(page, "user_12_notice_viewed.png")
                detail_text = page.locator("body").inner_text()
                if "已查看" in detail_text or "查看时间" in detail_text:
                    print("✓ 打开详情后已记录查看状态")
                else:
                    print("ℹ️  未在当前页面文本中看到查看状态")
                confirm_btns = page.locator("button:has-text('我已阅读并确认')")  # 反向守卫：该按钮应不存在
                if confirm_btns.count() == 0:
                    print("✓ 通知确认已下线：无确认按钮")
                else:
                    print("⚠️  通知确认已下线，但仍发现确认按钮")
            else:
                print("ℹ️  没有可点击的通知")

            # === 步骤8: 返回工作台验证 ===
            print("\n[8/9] 返回工作台验证")
            page.goto(f"{BASE_URL}/dashboard")
            page.wait_for_load_state("networkidle")
            take_screenshot(page, "user_13_dashboard_after_confirm.png")

            # 检查待办数字
            stat_numbers = page.locator(".stat-number, .todo-count")
            if stat_numbers.count() > 0:
                counts = [stat_numbers.nth(i).text_content().strip() for i in range(min(3, stat_numbers.count()))]
                print(f"ℹ️  统计数字: {counts}")

            # === 步骤9: 权限测试 ===
            # v3 守卫行为：非 admin 访问 /admin/* 页面 → 302 重定向首页（未登录 → /login）
            print("\n[9/9] 尝试访问管理页面")
            admin_paths = ["/admin/users", "/admin/backups", "/admin/audit-logs"]
            blocked_count = 0

            for path in admin_paths:
                page.goto(f"{BASE_URL}{path}")
                page.wait_for_load_state("networkidle")

                current_url = page.url.rstrip("/")

                if current_url.endswith("/login") or current_url == BASE_URL:
                    dest = page.url.replace(BASE_URL, "") or "/"
                    print(f"✓ {path} - 正确被拒绝（重定向至 {dest}）")
                    blocked_count += 1
                else:
                    print(f"⚠️  {path} - 未被拒绝！当前URL: {current_url}")
                    take_screenshot(page, f"user_14_admin_fail_{path.replace('/', '_')}.png")

            print(f"\n权限测试: {blocked_count}/{len(admin_paths)} 个路径正确被拒绝")

            print("\n" + "=" * 60)
            print("✅ 普通用户视角走流程完成！")
            print("=" * 60)
            print(f"\n截图保存在: {SCREENSHOTS_DIR}")

        except Exception as e:  # noqa: BLE001  走查脚本顶层兜底：失败要打印现场而非静默
            print(f"\n❌ 走流程失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()


if __name__ == "__main__":
    user_journey()
