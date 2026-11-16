"""JWT 认证边界测试：过期/篡改/格式错误/签名无效。

验证安全层在各种异常认证场景下的防御行为。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings
from app.core.security import JWT_AUDIENCE, JWT_ISSUER, create_access_token
from helpers import login_headers


settings = get_settings()
SECRET = settings.SECRET_KEY


def _forge_token(**overrides) -> str:
    """伪造 JWT（绕过 create_access_token 的标准流程）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "user_id": 1,
        "username": "admin",
        "role": "admin",
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
        "jti": "forged",
    }
    payload.update(overrides)
    return jwt.encode(payload, SECRET, algorithm="HS256")


class TestAuthBoundary:
    """JWT 各种异常场景。"""

    def test_expired_token_rejected(self, client, admin_user):
        """Token 刚好过期 → 401。"""
        token = create_access_token(
            user_id=admin_user.id,
            username=admin_user.username,
            role=admin_user.role,
            expires_minutes=-1,  # 已过期
        )
        resp = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, f"过期 Token 应返回 401: {resp.status_code}"

    def test_invalid_signature_rejected(self, client, admin_user):
        """签名错误 → 401。"""
        token = _forge_token()  # 用 settings.SECRET_KEY 签名
        # 用错误密钥重新签名（需要关闭 audience 校验）
        wrong_payload = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_aud": False})
        wrong_token = jwt.encode(wrong_payload, "wrong-secret-key", algorithm="HS256")

        resp = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {wrong_token}"})
        assert resp.status_code == 401

    def test_missing_bearer_prefix(self, client, admin_user):
        """没有 Bearer 前缀 → 401。"""
        token = create_access_token(
            user_id=admin_user.id, username=admin_user.username, role=admin_user.role,
        )
        # 缺少 "Bearer " 前缀
        resp = client.get("/api/v1/documents", headers={"Authorization": token})
        assert resp.status_code == 401

    def test_empty_authorization_header(self, client, admin_user):
        """空 Authorization 头 → 401。"""
        resp = client.get("/api/v1/documents", headers={"Authorization": ""})
        assert resp.status_code == 401

    def test_missing_authorization_header(self, client, admin_user):
        """无 Authorization 头 → 401。"""
        resp = client.get("/api/v1/documents")
        assert resp.status_code == 401

    def test_tampered_user_id_rejected(self, client, admin_user):
        """篡改 user_id → 401。"""
        token = create_access_token(
            user_id=admin_user.id, username=admin_user.username, role=admin_user.role,
        )
        # 解码 → 改 user_id → 重新编码（用同一个密钥）
        payload = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_aud": False})
        payload["user_id"] = 99999  # 不存在的用户
        tampered = jwt.encode(payload, SECRET, algorithm="HS256")

        resp = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {tampered}"})
        # 应该被拒绝（用户不存在或 token 无效）
        assert resp.status_code in (401, 403)

    def test_tampered_role_escalation(self, client, normal_user):
        """普通用户篡改 role 为 admin → 应被拒绝（v3 两角色）。"""
        token = create_access_token(
            user_id=normal_user.id, username=normal_user.username, role=normal_user.role,
        )
        payload = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_aud": False})
        payload["role"] = "admin"  # 尝试提权
        tampered = jwt.encode(payload, SECRET, algorithm="HS256")

        # 访问管理员 API
        resp = client.get("/api/v1/audit-logs",
                          headers={"Authorization": f"Bearer {tampered}"})
        # 后端从数据库复核角色（app/core/deps.py:106-109）：token.role 与 DB 实际角色
        # 不符 → 401 UNAUTHORIZED（先于 require_roles 的 403 判定）。
        # 2026-09-29 修复：原断言 `in (200, 401, 403, 422)` 把 200（= 提权成功）也当作
        # 通过，这条唯一针对「篡改 JWT 提权」的用例**永远不会失败**——即便有人删掉
        # 角色复核，回归依然全绿，安全网形同虚设。收紧为唯一正确结果。
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    def test_malformed_token_rejected(self, client, admin_user):
        """格式错误的 Token → 401。"""
        resp = client.get("/api/v1/documents",
                          headers={"Authorization": "Bearer not.a.valid.jwt.token"})
        assert resp.status_code == 401

    def test_empty_token_rejected(self, client, admin_user):
        """Bearer 后为空 → 401。"""
        resp = client.get("/api/v1/documents",
                          headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_deactivated_user_token_rejected(self, client, db, admin_user):
        """用户被停用后，其 Token 应失效。"""
        token = create_access_token(
            user_id=admin_user.id, username=admin_user.username, role=admin_user.role,
        )

        # 停用用户
        admin_user.is_active = False
        db.commit()

        resp = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403), f"停用用户的 Token 应失效: {resp.status_code}"


class TestMustChangePasswordGate:
    """首登/重置改密服务端闸门（2026-10-16 P0 修复回归）。

    背景：must_change_password 此前仅前端强制（base.html:10-11 meta refresh），
    API 客户端持有效 JWT 可完全绕过改密。现 deps.get_current_user 对 /api/
    前缀路径（白名单外）返回 403 PASSWORD_CHANGE_REQUIRED。

    反向验证约定（AGENTS 测试纪律）：stash 掉 deps.py 的闸门后，
    test_business_api_blocked / test_reauth_still_blocked 必须失败。
    """

    def test_business_api_blocked_for_flagged_user(self, client, user_factory):
        """标记用户调用业务 API → 403 PASSWORD_CHANGE_REQUIRED。"""
        user_factory("gateuser", password="gatePass123", must_change_password=True)
        headers = login_headers(client, "gateuser", "gatePass123")
        resp = client.get("/api/v1/documents", headers=headers)
        assert resp.status_code == 403, f"改密闸门应拦截业务 API: {resp.status_code}"
        assert resp.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    def test_whitelist_apis_allowed_and_cleared_after_change(self, client, user_factory):
        """白名单放行（/auth/me + change-password）；改密后业务 API 恢复。"""
        user_factory("gateuser2", password="gatePass123", must_change_password=True)
        headers = login_headers(client, "gateuser2", "gatePass123")

        # /auth/me：前端守卫据此读标记跳转，必须放行
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["must_change_password"] is True

        # change-password：改密本体必须放行
        r = client.post(
            "/api/v1/auth/change-password", headers=headers,
            json={"old_password": "gatePass123", "new_password": "gatePass456"},
        )
        assert r.status_code == 200, r.text

        # 改密自增 token_version，旧令牌作废——用新密码重新登录，闸门应已放行
        headers = login_headers(client, "gateuser2", "gatePass456")
        assert client.get("/api/v1/documents", headers=headers).status_code == 200

    def test_page_routes_not_gated(self, client, user_factory):
        """页面路由不拦：pages._resolve_user 吞 AppError 会渲染为匿名态，
        若拦页面会把已登录用户弹回登录页并打断 /change-password 自身渲染。
        标记用户 GET /documents 仍应 200 HTML（前端 meta refresh 负责跳转）。"""
        user_factory("gateuser3", password="gatePass123", must_change_password=True)
        headers = login_headers(client, "gateuser3", "gatePass123")
        resp = client.get("/documents", headers=headers)
        assert resp.status_code == 200, f"页面路由不应被闸门拦截: {resp.status_code}"
        assert "text/html" in resp.headers.get("content-type", "")

    def test_logout_allowed_then_reauth_still_blocked(self, client, user_factory):
        """logout 在白名单；重新登录后业务 API 依旧被拦（标记未清）。"""
        user_factory("gateuser4", password="gatePass123", must_change_password=True)
        headers = login_headers(client, "gateuser4", "gatePass123")
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200

        headers2 = login_headers(client, "gateuser4", "gatePass123")
        resp = client.get("/api/v1/notices", headers=headers2)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"


class TestInitialAdminNotForced:
    """初始管理员**不再**强制改密（2026-10-29 产品决策）。

    边界（重要）：只移除「首次建库时初始管理员」这一条；
    「管理员重置他人密码后强制改密」**保留**（防管理员长期知晓他人口令）。

    反向验证约定：把 init_db.seed_initial_admin 里的 must_change_password
    改回 True，本类第一条必须失败。
    """

    def test_seed_initial_admin_does_not_require_password_change(
        self, db, monkeypatch
    ):
        """建库写入的初始管理员 must_change_password=False。"""
        from app.db import init_db as init_mod
        from app.models.user import User

        monkeypatch.setattr(init_mod, "get_settings", lambda: type("S", (), {
            "INITIAL_ADMIN_USERNAME": "bootadmin",
            "INITIAL_ADMIN_PASSWORD": "BootStr0ngPass!",
        })())

        created = init_mod.seed_initial_admin(db)
        assert created is not None, "首次建库应创建初始管理员"
        db.commit()

        user = db.query(User).filter(User.username == "bootadmin").one()
        assert user.must_change_password is False, (
            "初始管理员不应被标记为必须改密（2026-10-29 决策）"
        )
        assert user.role == "admin"

    def test_initial_admin_can_use_business_api_immediately(
        self, client, db, monkeypatch
    ):
        """初始管理员登录后直接可调用业务 API，无须先改密。"""
        from app.db import init_db as init_mod
        from app.models.user import User

        monkeypatch.setattr(init_mod, "get_settings", lambda: type("S", (), {
            "INITIAL_ADMIN_USERNAME": "bootadmin2",
            "INITIAL_ADMIN_PASSWORD": "BootStr0ngPass!",
        })())
        init_mod.seed_initial_admin(db)
        db.commit()
        assert db.query(User).filter(User.username == "bootadmin2").count() == 1

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "bootadmin2", "password": "BootStr0ngPass!"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["must_change_password"] is False

        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        r = client.get("/api/v1/documents", headers=headers)
        assert r.status_code == 200, f"初始管理员应可直接用业务 API: {r.status_code} {r.text}"

    def test_admin_reset_password_still_forces_change(
        self, client, admin_headers, user_factory, db
    ):
        """保留项：管理员重置他人密码后，对方仍被强制改密。

        这是防「管理员长期知晓他人口令」的真实风险，与首登场景不同。
        """
        from app.models.user import User

        user_factory("reset_target", password="OrigPass123")
        target = db.query(User).filter(User.username == "reset_target").one()

        r = client.post(
            f"/api/v1/users/{target.id}/reset-password",
            headers=admin_headers,
            json={"new_password": "ResetPass456"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["must_change_password"] is True

        headers = login_headers(client, "reset_target", "ResetPass456")
        resp = client.get("/api/v1/documents", headers=headers)
        assert resp.status_code == 403, "重置密码后仍应被改密闸门拦截"
        assert resp.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
