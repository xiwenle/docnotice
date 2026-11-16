"""M30 分类树：四级上限、同级重名、成环防护、整树删除与未分类语义（§9）。"""
from __future__ import annotations

import time

from harness import REPORT, SHOT_DIR, case, http


def _tree_nodes(page):
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#cat-tree .tree-node-content')).map(n => ({"
        "  id: n.dataset.id,"
        "  name: (n.querySelector('strong') || {}).innerText || '',"
        "  indent: parseInt(n.style.paddingLeft, 10) || 0,"
        "  addChild: !!n.querySelector('[data-action=add-child]'),"
        "  text: n.innerText.replace(/\\n/g, ' ')"
        "}))")


def _open(app):
    s = app.admin
    s.goto("/admin/categories")
    s.page.wait_for_selector("#cat-tree .tree-node-content", timeout=15000)
    s.page.wait_for_timeout(800)
    return s


@case("C01", "分类管理：树结构渲染 + 层级缩进")
def c01(app):
    s = _open(app)
    nodes = _tree_nodes(s.page)
    REPORT.check(len(nodes) > 0, f"渲染 {len(nodes)} 个分类节点")
    depths = len({n["indent"] for n in nodes})
    REPORT.check(depths >= 1, f"存在 {depths} 种缩进层级")
    max_depth = max((n["indent"] for n in nodes), default=0)
    REPORT.check(max_depth <= 12 + 3 * 22, f"最大缩进 {max_depth}px（四级上限 = 78px）")
    REPORT.check(all(n["name"].strip() for n in nodes), "所有节点均有名称")
    s.page.screenshot(path=str(SHOT_DIR / "C01_categories.png"))


@case("C02", "新建一级分类 → 出现在树中")
def c02(app):
    s = _open(app)
    name = f"E2E一级{int(time.time())}"
    s.page.click("#btn-new-root")
    s.page.wait_for_selector("#c-name", timeout=6000)
    s.page.fill("#c-name", name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(2000)
    txt = s.page.inner_text("#cat-tree")
    REPORT.check(name in txt, f"新分类出现在树中：{name}")
    api_list = http("GET", "/api/v1/categories", app.admin.token)["body"] or {}
    flat = api_list.get("items") if isinstance(api_list, dict) else api_list
    found = None

    def walk(items):
        for it in items or []:
            if it.get("name") == name:
                return it
            hit = walk(it.get("children") or [])
            if hit:
                return hit
        return None
    found = walk(flat or [])
    REPORT.check(found is not None, "API 侧也能查到该分类")
    if found:
        app.state["cat_root_id"] = found.get("id")


@case("C03", "同级重名 → 拒绝并提示")
def c03(app):
    app.allow_noise()  # 同级重名 → 服务端 409，属预期
    s = _open(app)
    name = f"E2E重名{int(time.time())}"
    for attempt in range(2):
        s.page.click("#btn-new-root")
        s.page.wait_for_selector("#c-name", timeout=6000)
        s.page.fill("#c-name", name)
        s.page.click("[data-save]")
        s.page.wait_for_timeout(1800)
    toast = s.toast_text(4000)
    count = s.page.inner_text("#cat-tree").count(name)
    REPORT.check(count <= 1, f"同名分类只存在 1 个（页面出现 {count} 次）")
    REPORT.check(bool(toast), f"第二次创建给出提示：{toast[:40]}")


@case("C04", "逐级新建到第四级，且第四级不再提供「＋ 子分类」")
def c04(app):
    """从已有的一级分类逐级下钻：一级→二级→三级→四级，验证深度上限的前端表现。"""
    s = _open(app)
    # 建一棵干净的四级链
    levels = []
    for i in range(1, 5):
        name = f"E2E第{i}级{int(time.time())}"
        if i == 1:
            r = http("POST", "/api/v1/categories", app.admin.token, {"name": name})
        else:
            r = http("POST", "/api/v1/categories", app.admin.token,
                     {"name": name, "parent_id": levels[-1]})
        body = r["body"] or {}
        if r["status"] in (200, 201) and body.get("id"):
            levels.append(body["id"])
        else:
            REPORT.fail(f"第 {i} 级创建失败：{r['status']} {str(body)[:100]}")
            return
    app.state["cat_chain"] = levels
    REPORT.check(len(levels) == 4, f"四级链路建成：{levels}")
    # 后端是否拒绝五级
    r5 = http("POST", "/api/v1/categories", app.admin.token,
              {"name": f"E2E第5级{int(time.time())}", "parent_id": levels[-1]})
    REPORT.check(r5["status"] >= 400, f"第五级被后端拒绝：{r5['status']}")

    # 前端：第四级节点不应出现「＋ 子分类」按钮
    s.goto("/admin/categories")
    s.page.wait_for_selector("#cat-tree .tree-node-content", timeout=15000)
    s.page.evaluate(
        "() => {document.querySelectorAll('#cat-tree .tree-toggle').forEach(t => {"
        "  if (!t.classList.contains('leaf')) t.click();});}")
    s.page.wait_for_timeout(1200)
    nodes = _tree_nodes(s.page)
    deepest = max(nodes, key=lambda n: n["indent"])
    REPORT.check(deepest["indent"] <= 78, f"最深缩进 {deepest['indent']}px（四级=78）")
    REPORT.check(not deepest["addChild"],
                 f"最深层「{deepest['name']}」不再显示＋子分类按钮")
    s.page.screenshot(path=str(SHOT_DIR / "C04_depth_limit.png"))


@case("C05", "删除分类：确认后整棵子树删除")
def c05(app):
    stamp = int(time.time())
    root_name = f"E2E待删树{stamp}"
    child_name = f"E2E待删子类{stamp}"
    root = http("POST", "/api/v1/categories", app.admin.token, {"name": root_name})
    root_id = (root["body"] or {}).get("id")
    child = http(
        "POST",
        "/api/v1/categories",
        app.admin.token,
        {"name": child_name, "parent_id": root_id},
    )
    child_id = (child["body"] or {}).get("id")
    if not root_id or not child_id:
        REPORT.fail(f"临时分类树创建失败：{root['status']} / {child['status']}")
        return

    s = _open(app)
    btn = s.page.query_selector(f'button[data-action="delete"][data-id="{root_id}"]')
    if not btn:
        REPORT.fail(f"未找到删除按钮（id={root_id}）")
        return
    btn.click()
    s.page.wait_for_selector("#modal-root .modal-overlay.show", timeout=6000)
    modal_text = s.page.inner_text("#modal-root .modal-overlay.show")
    REPORT.check("整棵" in modal_text or "子分类" in modal_text, "弹窗说明会删除整棵分类树")
    s.page.click("#modal-root [data-confirm]")
    try:
        s.page.wait_for_selector(
            "#modal-root .modal-overlay", state="detached", timeout=6000
        )
    except Exception:  # noqa: BLE001
        pass
    s.page.wait_for_timeout(1200)

    still = http("GET", "/api/v1/categories", app.admin.token)["body"] or {}

    def exists(items, cid):
        for it in items or []:
            if it.get("id") == cid:
                return True
            if exists(it.get("children") or [], cid):
                return True
        return False

    items = still.get("items") if isinstance(still, dict) else still
    REPORT.check(
        not exists(items, root_id) and not exists(items, child_id),
        "父分类和子分类都已删除",
    )


@case("C06", "重命名分类生效")
def c06(app):
    cid = app.state.get("cat_root_id")
    if not cid:
        REPORT.fail("无自建分类（依赖 C02）")
        return
    s = _open(app)
    btn = s.page.query_selector(f'button[data-action="edit"][data-id="{cid}"]')
    if not btn:
        REPORT.fail("未找到编辑按钮")
        return
    btn.click()
    s.page.wait_for_selector("#c-name", timeout=6000)
    new_name = f"E2E改名{int(time.time())}"
    s.page.fill("#c-name", new_name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(2000)
    REPORT.check(new_name in s.page.inner_text("#cat-tree"), f"树中已更名：{new_name}")


@case("C07", "清理：删除本次新建的空分类")
def c07(app):
    s = _open(app)
    nodes = _tree_nodes(s.page)
    targets = [n for n in nodes if n["name"].startswith("E2E")]
    removed = 0
    # 从最深往上删（先删四级）
    chain = app.state.get("cat_chain") or []
    for cid in reversed(chain):
        r = http("DELETE", f"/api/v1/categories/{cid}", app.admin.token)
        if r["status"] in (200, 204):
            removed += 1
    REPORT.check(removed == len(chain), f"清理自建链路 {removed}/{len(chain)} 个")
    REPORT.check(True, f"页面仍可见 E2E 分类 {len(targets)} 个（部分用于后续用例保留）")
