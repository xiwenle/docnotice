"""用户与部门管理测试（API.md §2/§3、项目规范 §21/§23/§25、DESIGN §4.2）。

覆盖：用户 CRUD、部门分配、禁用后登录 403、重置密码后旧密码失效、
must_change_password 标记、角色授权保护、部门停用保护。
"""
from __future__ import annotations


from helpers import login_headers


class TestUserCrud:
    def test_create_and_get_user(self, client, admin_headers):
        resp = client.post("/api/v1/users", headers=admin_headers, json={
            "username": "lisi", "password": "initial-pass-1",
            "display_name": "李四", "role": "user", "email": "lisi@company.cn",
        })
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["username"] == "lisi"
        assert body["display_name"] == "李四"
        assert body["role"] == "user"
        assert body["is_active"] is True
        assert "password_hash" not in body  # 不泄露哈希

        detail = client.get(f"/api/v1/users/{body['id']}", headers=admin_headers)
        assert detail.status_code == 200
        assert detail.json()["departments"] == []

    def test_duplicate_username_409(self, client, admin_headers):
        payload = {"username": "dup", "password": "password123",
                   "display_name": "重复", "role": "user"}
        assert client.post("/api/v1/users", headers=admin_headers, json=payload).status_code == 201
        resp = client.post("/api/v1/users", headers=admin_headers, json=payload)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "DUPLICATE"

    def test_weak_password_400(self, client, admin_headers):
        resp = client.post("/api/v1/users", headers=admin_headers, json={
            "username": "weak", "password": "123", "display_name": "弱密码",
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_admin_can_grant_admin(self, client, admin_headers):
        resp = client.post("/api/v1/users", headers=admin_headers, json={
            "username": "新管理员", "password": "password123",
            "display_name": "新管理员", "role": "admin",
        })
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"

    def test_list_filters(self, client, admin_headers, user_factory):
        user_factory("员工A", role="user", password="user1234")
        user_factory("管理员B", role="admin", password="user1234")

        all_users = client.get("/api/v1/users", headers=admin_headers).json()
        assert all_users["total"] >= 3  # admin + 员工A + 管理员B
        by_role = client.get("/api/v1/users?role=admin", headers=admin_headers).json()
        assert all(u["role"] == "admin" for u in by_role["items"])
        by_keyword = client.get("/api/v1/users?keyword=员工A", headers=admin_headers).json()
        assert [u["username"] for u in by_keyword["items"]] == ["员工A"]

    def test_update_user(self, client, admin_headers, user_factory):
        u = user_factory("被修改", role="user", password="user1234")
        resp = client.put(f"/api/v1/users/{u.id}", headers=admin_headers,
                          json={"display_name": "新名字", "email": "new@company.cn"})
        assert resp.status_code == 200, resp.text
        detail = client.get(f"/api/v1/users/{u.id}", headers=admin_headers).json()
        assert detail["display_name"] == "新名字"
        assert detail["email"] == "new@company.cn"

    def test_admin_can_manage_users_fully(self, client, user_factory):
        """权限模型 v3：任意 admin 均可创建用户/变更他人角色（v2 分层已合并）。"""
        user_factory("普通经理", role="admin", password="manager1234")
        mgr = login_headers(client, "普通经理", "manager1234")
        # 创建用户 → 201
        resp = client.post("/api/v1/users", headers=mgr, json={
            "username": "新员工甲", "password": "password123",
            "display_name": "新员工甲", "role": "user",
        })
        assert resp.status_code == 201, resp.text
        # 变更他人角色 → 200
        target = user_factory("普通员工乙", role="user", password="user1234")
        resp = client.put(f"/api/v1/users/{target.id}", headers=mgr,
                          json={"role": "admin"})
        assert resp.status_code == 200, resp.text


class TestUserProtections:
    def test_cannot_disable_self(self, client, admin_headers):
        """管理员不能停用自己（保护规则）。"""
        me = client.get("/api/v1/auth/me", headers=admin_headers).json()
        resp = client.put(f"/api/v1/users/{me['id']}", headers=admin_headers,
                          json={"is_active": False})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONFLICT"

    def test_cannot_disable_last_admin(self, client, admin_headers, user_factory):
        """唯一启用的 admin 不能被停用/降级（防系统失去全部管理者）。"""
        me = client.get("/api/v1/auth/me", headers=admin_headers).json()
        resp = client.put(f"/api/v1/users/{me['id']}", headers=admin_headers,
                          json={"role": "user"})
        assert resp.status_code == 409
        resp = client.delete(f"/api/v1/users/{me['id']}", headers=admin_headers)
        assert resp.status_code == 409

    def test_disable_then_login_forbidden(self, client, admin_headers, user_factory):
        u = user_factory("即将停用", role="user", password="user1234")
        assert client.put(f"/api/v1/users/{u.id}", headers=admin_headers,
                          json={"is_active": False}).status_code == 200
        resp = client.post("/api/v1/auth/login",
                           json={"username": "即将停用", "password": "user1234"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"


class TestResetPassword:
    def test_reset_password_flow(self, client, admin_headers, user_factory):
        u = user_factory("改密用户", role="user", password="user1234")
        resp = client.post(f"/api/v1/users/{u.id}/reset-password", headers=admin_headers,
                           json={"new_password": "newpass-888"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["must_change_password"] is True

        # 旧密码失效
        assert client.post("/api/v1/auth/login",
                           json={"username": "改密用户", "password": "user1234"}).status_code == 401
        # 新密码可登录
        token = login_headers(client, "改密用户", "newpass-888")
        me = client.get("/api/v1/auth/me", headers=token).json()
        assert me["must_change_password"] is True  # 前端据此提示首次登录改密

        # 修改本人密码后标记清除
        resp = client.post("/api/v1/auth/change-password", headers=token, json={
            "old_password": "newpass-888", "new_password": "finalpass-999",
        })
        assert resp.status_code == 200, resp.text
        # 单点登录：改密自增 token_version，改密前 Bearer 令牌已作废；
        # 当前设备靠响应下发的全新 Cookie 继续会话
        me = client.get("/api/v1/auth/me").json()
        assert me["must_change_password"] is False

    def test_change_password_wrong_old_401(self, client, admin_headers):
        resp = client.post("/api/v1/auth/change-password", headers=admin_headers, json={
            "old_password": "wrong-old-1", "new_password": "newpass-888",
        })
        assert resp.status_code == 401

    def test_change_password_weak_400(self, client, admin_headers):
        resp = client.post("/api/v1/auth/change-password", headers=admin_headers, json={
            "old_password": "admin1234", "new_password": "123",
        })
        assert resp.status_code == 400


class TestDepartment:
    def test_department_crud(self, client, admin_headers):
        resp = client.post("/api/v1/departments", headers=admin_headers,
                           json={"name": "技术部", "description": "研发"})
        assert resp.status_code == 201
        dept_id = resp.json()["id"]

        listed = client.get("/api/v1/departments", headers=admin_headers).json()
        assert any(d["id"] == dept_id for d in listed["items"])

        resp = client.put(f"/api/v1/departments/{dept_id}", headers=admin_headers,
                          json={"name": "技术发展部"})
        assert resp.status_code == 200
        detail = client.get("/api/v1/departments?keyword=技术发展部", headers=admin_headers).json()
        assert detail["total"] == 1

    def test_duplicate_department_409(self, client, admin_headers):
        assert client.post("/api/v1/departments", headers=admin_headers,
                           json={"name": "行政部"}).status_code == 201
        resp = client.post("/api/v1/departments", headers=admin_headers,
                           json={"name": "行政部"})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "DUPLICATE"

    def test_assign_user_to_department(self, client, admin_headers, user_factory, department_factory):
        dept = department_factory("财务部")
        u = user_factory("部门员工", role="user", password="user1234")
        resp = client.put(f"/api/v1/users/{u.id}", headers=admin_headers,
                          json={"department_ids": [dept.id]})
        assert resp.status_code == 200
        detail = client.get(f"/api/v1/users/{u.id}", headers=admin_headers).json()
        assert detail["department_ids"] == [dept.id]
        assert [d["name"] for d in detail["departments"]] == ["财务部"]

        # 部门列表显示成员数
        listed = client.get("/api/v1/departments?with_member_count=true",
                            headers=admin_headers).json()
        fin = next(d for d in listed["items"] if d["id"] == dept.id)
        assert fin["member_count"] == 1

        # 按部门筛选用户
        users = client.get(f"/api/v1/users?department_id={dept.id}", headers=admin_headers).json()
        assert [u2["username"] for u2 in users["items"]] == ["部门员工"]

    def test_delete_department_with_members_unassigns_users(self, client, admin_headers, user_factory, department_factory):
        dept = department_factory("有成员部门")
        user = user_factory("成员甲", role="user", password="user1234", department_ids=[dept.id])
        resp = client.request("DELETE", f"/api/v1/departments/{dept.id}",
                              headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["affected_member_count"] == 1
        detail = client.get(f"/api/v1/users/{user.id}", headers=admin_headers).json()
        assert detail["department_ids"] == [], "成员应变为未分配部门"

    def test_disable_empty_department_ok(self, client, admin_headers, department_factory):
        dept = department_factory("空部门")
        resp = client.request("DELETE", f"/api/v1/departments/{dept.id}",
                              headers=admin_headers)
        assert resp.status_code == 200
        # 产品决策（2026-09）：部门删除=物理删除，行已消失
        listed = client.get("/api/v1/departments", headers=admin_headers).json()
        assert all(d["id"] != dept.id for d in listed["items"])

class TestSingleSessionLogin:
    """单点登录（2026-09-03 产品决策）：同账号新登录踢掉旧会话。"""

    def test_relogin_invalidates_old_token(self, client, user_factory):
        user_factory("单点用户", role="user", password="user1234")
        first = client.post("/api/v1/auth/login",
                            json={"username": "单点用户", "password": "user1234"}).json()
        h1 = {"Authorization": f"Bearer {first['access_token']}"}
        assert client.get("/api/v1/auth/me", headers=h1).status_code == 200

        # 设备 B 登录同账号 → 设备 A 令牌立即失效
        second = client.post("/api/v1/auth/login",
                             json={"username": "单点用户", "password": "user1234"}).json()
        h2 = {"Authorization": f"Bearer {second['access_token']}"}
        resp = client.get("/api/v1/auth/me", headers=h1)
        assert resp.status_code == 401, "旧会话必须被新登录顶掉"
        assert "其他设备" in resp.json()["error"]["message"] or "重新登录" in resp.json()["error"]["message"]
        assert client.get("/api/v1/auth/me", headers=h2).status_code == 200

    def test_change_password_keeps_current_session(self, client, user_factory):
        """改密自增 token_version：其他会话失效，但当前设备靠重签 Cookie 保持登录。"""
        user_factory("改密保活用户", role="user", password="user1234")
        # 先模拟第二台设备登录
        client.post("/api/v1/auth/login",
                    json={"username": "改密保活用户", "password": "user1234"})
        # 当前设备登录（cookie 记录在 client jar）
        client.post("/api/v1/auth/login",
                    json={"username": "改密保活用户", "password": "user1234"})
        resp = client.post("/api/v1/auth/change-password", json={
            "old_password": "user1234", "new_password": "newpass-888",
        })
        assert resp.status_code == 200, resp.text
        # 当前设备（新 Cookie）仍有效
        assert client.get("/api/v1/auth/me").status_code == 200
        # 新密码可再登录
        assert client.post("/api/v1/auth/login",
                           json={"username": "改密保活用户",
                                 "password": "newpass-888"}).status_code == 200
