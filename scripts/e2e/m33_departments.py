"""M33 部门管理：CRUD、重名拒绝、删除后成员/目标置空。"""
from __future__ import annotations

import time

from harness import REPORT, SHOT_DIR, case, http


def _open(app):
    s = app.admin
    s.goto("/admin/departments")
    s.page.wait_for_selector("#dept-tbody tr", timeout=15000)
    s.page.wait_for_timeout(600)
    return s


@case("E01", "部门列表：渲染 + 搜索")
def e01(app):
    s = _open(app)
    rows = s.page.query_selector_all("#dept-tbody tr")
    REPORT.check(len(rows) > 0, f"部门列表 {len(rows)} 行")
    if rows:
        name = rows[0].inner_text().split("\n")[0].strip()
        s.page.fill("#f-keyword", name[:4])
        s.page.click("#btn-search")
        s.page.wait_for_timeout(1200)
        REPORT.check(len(s.page.query_selector_all("#dept-tbody tr")) >= 1, "搜索有结果")
        s.page.click("#btn-reset")
        s.page.wait_for_timeout(800)
    s.page.screenshot(path=str(SHOT_DIR / "E01_departments.png"))


@case("E02", "新建部门 → 重命名 → 重名拒绝")
def e02(app):
    app.allow_noise()  # 部门重名 → 服务端 409，属预期
    s = _open(app)
    name = f"E2E部门{int(time.time())}"
    s.page.click("#btn-new-dept")
    s.page.wait_for_selector("#d-name", timeout=6000)
    s.page.fill("#d-name", name)
    # 2026-11-03：部门编码列已按产品决策删除，这里不再填 code
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    REPORT.check(name in s.page.inner_text("#dept-tbody"), f"部门已创建：{name}")

    # 重名
    s.page.click("#btn-new-dept")
    s.page.wait_for_selector("#d-name", timeout=6000)
    s.page.fill("#d-name", name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    toast = s.toast_text(4000)
    REPORT.check(
        s.page.inner_text("#dept-tbody").count(name) == 1,
        f"重名未被接受（提示：{toast[:40]}）",
    )
    cancel = s.page.query_selector("[data-cancel]")
    if cancel:
        cancel.click()
        s.page.wait_for_timeout(400)

    # 重命名
    s.page.fill("#f-keyword", name)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    row = s.page.query_selector("#dept-tbody tr")
    row.query_selector('button[data-action="edit"]').click()
    s.page.wait_for_selector("#d-name", timeout=6000)
    new_name = f"E2E部门改名{int(time.time())}"
    s.page.fill("#d-name", new_name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1000)
    REPORT.check(new_name in s.page.inner_text("#dept-tbody"), f"改名生效：{new_name}")
    app.state["dept_name"] = new_name


@case("E03", "删除部门：成员变未分配，用户/部门关联被解除")
def e03(app):
    stamp = int(time.time())
    dept_name = f"E2E删除部门{stamp}"
    username = f"e2e_dept_user_{stamp}"
    dept_id = None
    user_id = None
    try:
        created_dept = http(
            "POST", "/api/v1/departments", app.admin.token, {"name": dept_name}
        )
        dept_id = (created_dept["body"] or {}).get("id")
        REPORT.check(
            created_dept["status"] == 201 and bool(dept_id),
            f"创建临时部门：{created_dept['status']} id={dept_id}",
        )
        if not dept_id:
            return

        created_user = http(
            "POST",
            "/api/v1/users",
            app.admin.token,
            {
                "username": username,
                "password": "Demo@123456",
                "display_name": "临时部门成员",
                "role": "user",
                "department_ids": [dept_id],
                "must_change_password": False,
            },
        )
        user_id = (created_user["body"] or {}).get("id")
        REPORT.check(
            created_user["status"] == 201
            and (created_user["body"] or {}).get("department_ids") == [dept_id],
            f"临时用户加入部门：{created_user['status']} id={user_id}",
        )
        if not user_id:
            return

        deleted = http(
            "DELETE", f"/api/v1/departments/{dept_id}", app.admin.token
        )
        deleted_body = deleted["body"] or {}
        REPORT.check(
            deleted["status"] == 200,
            f"带成员部门删除成功：{deleted['status']}",
        )
        REPORT.check(
            deleted_body.get("affected_member_count", 0) >= 1,
            f"响应记录受影响成员：{deleted_body.get('affected_member_count')}",
        )

        user_detail = http(
            "GET", f"/api/v1/users/{user_id}", app.admin.token
        )["body"] or {}
        REPORT.check(
            user_detail.get("department_ids") == [],
            f"成员变为未分配部门：{user_detail.get('department_ids')}",
        )
    finally:
        if user_id:
            cleanup = http(
                "DELETE", f"/api/v1/users/{user_id}/purge", app.admin.token
            )
            REPORT.check(
                cleanup["status"] in (200, 204),
                f"临时成员已清理：{cleanup['status']}",
            )


@case("E04", "清理：确认弹窗后删除本次新建的空部门")
def e04(app):
    name = app.state.get("dept_name")
    if not name:
        REPORT.fail("无自建部门（依赖 E02）")
        return
    api = http("GET", "/api/v1/departments", app.admin.token)["body"] or {}
    items = api.get("items") if isinstance(api, dict) else api
    target = next((d for d in items or [] if d.get("name") == name), None)
    if not target:
        REPORT.check(True, "部门已不在列表中")
        return
    s = _open(app)
    s.page.fill("#f-keyword", name)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    row = s.page.query_selector("#dept-tbody tr")
    row.query_selector('button[data-action="delete"]').click()
    s.page.wait_for_selector("#modal-root .modal-overlay.show", timeout=6000)
    REPORT.check(True, "删除部门前出现确认弹窗")
    s.page.click("#modal-root [data-confirm]")
    try:
        s.page.wait_for_selector(
            "#modal-root .modal-overlay", state="detached", timeout=6000
        )
    except Exception:  # noqa: BLE001
        pass
    s.page.wait_for_timeout(1600)
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1000)
    REPORT.check(name not in s.page.inner_text("#dept-tbody"), f"自建部门已删除：{name}")