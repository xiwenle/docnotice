"""前端无障碍 DOM 专项检查（纯 Python + Playwright DOM 查询，零新依赖）。

检查五类可机械判定的问题（对照 WCAG）：
1. 表单控件缺关联标签（label[for]/包裹/aria-label/title）——WCAG 3.3.2/1.3.1
2. 图片缺 alt 属性（空 alt 合法=装饰图）——WCAG 1.1.1
3. 按钮/链接无可访问名称（空文本且无 aria-label/title）——WCAG 4.1.2
4. 表格无表头单元格 th——WCAG 1.3.1
5. 页面 h1 数量 != 1——文档结构

用法：先启动 uvicorn（127.0.0.1:8000），再
  python scripts/a11y_dom_check.py          # 扫描并报告
首次运行会列出存量问题；修复后此脚本作为回归守卫（发现=非零退出）。
"""
from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
ADMIN = {"username": "admin_demo", "password": "Demo@123456"}
STATE = Path(__file__).parent.parent / ".a11y_session.json"

# (名称, 路径模板, 是否登录态)；{doc}/{notice} 运行时取真实第一条
PAGES = [
    ("登录页", "/login", False),
    ("工作台", "/", True),
    ("资料列表", "/documents", True),
    ("资料详情", "/documents/{doc}", True),
    ("通知列表", "/notices", True),
    ("通知详情", "/notices/{notice}", True),
    ("修改密码", "/change-password", True),
    ("用户管理", "/admin/users", True),
    ("部门管理", "/admin/departments", True),
    ("分类管理", "/admin/categories", True),
    ("标签管理", "/admin/tags", True),
    ("通知类型", "/admin/notice-types", True),
    ("回收站", "/admin/recycle-bin", True),
    ("审计日志", "/admin/audit-logs", True),
    ("备份恢复", "/admin/backups", True),
    ("新建资料", "/admin/documents/new", True),
    ("新建通知", "/admin/notices/new", True),
]

PROBE = """
() => {
  const out = { unlabeled: [], noAlt: [], unnamed: [], noTh: [], h1: 0 };
  const desc = (el) => {
    const bits = [el.tagName.toLowerCase()];
    if (el.id) bits.push('#' + el.id);
    if (el.name) bits.push('[name=' + el.name + ']');
    if (el.type && el.tagName === 'INPUT') bits.push('[type=' + el.type + ']');
    const ph = el.getAttribute('placeholder');
    if (ph) bits.push('placeholder="' + ph.slice(0, 20) + '"');
    const txt = (el.textContent || '').trim().slice(0, 20);
    if (txt) bits.push('"' + txt + '"');
    return bits.join('');
  };
  document.querySelectorAll(
    'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select'
  ).forEach((el) => {
    const id = el.id;
    const ok = (id && document.querySelector('label[for="' + CSS.escape(id) + '"]'))
      || el.closest('label')
      || el.hasAttribute('aria-label')
      || el.hasAttribute('aria-labelledby')
      || el.hasAttribute('title');
    if (!ok) out.unlabeled.push(desc(el));
  });
  document.querySelectorAll('img').forEach((el) => {
    if (!el.hasAttribute('alt')) out.noAlt.push(el.getAttribute('src') || '(无src)');
  });
  document.querySelectorAll('button, a[href]').forEach((el) => {
    const name = (el.innerText || el.textContent || '').trim()
      || el.getAttribute('aria-label') || el.getAttribute('title')
      || (el.querySelector('img[alt]') ? '[img-alt]' : '');
    if (!name) out.unnamed.push(desc(el));
  });
  document.querySelectorAll('table').forEach((t) => {
    if (!t.querySelector('th')) {
      const head = t.querySelector('tr') ? t.querySelector('tr').textContent.trim().slice(0, 24) : '';
      out.noTh.push(head);
    }
  });
  out.h1 = document.querySelectorAll('h1').length;
  return out;
}
"""


def main() -> int:
    findings: dict[str, list] = defaultdict(list)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True,
                                    args=["--no-sandbox", "--disable-setuid-sandbox"])
        reuse = STATE.exists()
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900}, locale="zh-CN",
            **({"storage_state": str(STATE)} if reuse else {}),
        )
        page = ctx.new_page()
        if reuse:
            # 单点登录（拍板#8）会使已保存会话的 token 失效：先验证，失效则重新表单登录
            page.goto(BASE_URL + "/")
            page.wait_for_load_state("networkidle")
            if "/login" in page.url:
                reuse = False
        if not reuse:
            page.goto(BASE_URL + "/login")
            page.wait_for_load_state("networkidle")
            page.fill('input[name="username"]', ADMIN["username"])
            page.fill('input[name="password"]', ADMIN["password"])
            page.click('button[type="submit"]')
            page.wait_for_url(lambda u: "/login" not in u, timeout=8000)
            ctx.storage_state(path=str(STATE))

        page.goto(BASE_URL + "/")
        page.wait_for_load_state("networkidle")
        doc_id = page.evaluate(
            "async () => (await (await fetch('/api/v1/documents?page=1')).json()).items[0].id"
        )
        notice_id = page.evaluate(
            "async () => (await (await fetch('/api/v1/notices?page=1')).json()).items[0].id"
        )

        for name, path, _ in PAGES:
            url = BASE_URL + path.replace("{doc}", str(doc_id)).replace(
                "{notice}", str(notice_id)
            )
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            r = page.evaluate(PROBE)
            for item in r["unlabeled"]:
                findings["表单控件缺关联标签"].append(f"{name}: {item}")
            for item in r["noAlt"]:
                findings["图片缺 alt"].append(f"{name}: {item}")
            for item in r["unnamed"]:
                findings["按钮/链接无可访问名称"].append(f"{name}: {item}")
            for item in r["noTh"]:
                findings["表格无表头 th"].append(f"{name}: 表头行「{item}…」")
            if r["h1"] != 1:
                findings["h1 数量异常"].append(f"{name}: {r['h1']} 个")
        browser.close()

    total = sum(len(v) for v in findings.values())
    print(f"\n扫描 {len(PAGES)} 个页面，发现 {total} 项：")
    for kind, items in findings.items():
        print(f"\n【{kind}】{len(items)} 项")
        seen = set()
        for item in items:
            if item not in seen:  # 同组件跨页面重复只报一次
                seen.add(item)
                print(f"  - {item}")
    if total == 0:
        print("✅ 无问题")
        return 0
    print("\n（同类组件跨页面重复已去重展示）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
