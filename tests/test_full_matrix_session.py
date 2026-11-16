"""会话失效全矩阵回归测试。

锁定口径：登出、停用/启用、重置密码、主动改密、角色变更和同账号再次
登录后，先前签发的 Bearer / Cookie 均不得继续访问业务 API。
"""
from __future__ import annotations

from app.models.user import User

_USER_PASSWORD = "session-pass-123"
_NEW_PASSWORD = "session-pass-456"


def _login(client, username: str, password: str = _USER_PASSWORD) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("access_token")
    return body


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token_version(db, user_id: int) -> int:
    db.expire_all()
    user = db.get(User, user_id)
    assert user is not None
    return user.token_version or 0


def _get_with_cookie(client, token: str, path: str = "/api/v1/auth/me"):
    client.cookies.clear()
    return client.get(path, cookies={"access_token": token})


def test_logout_invalidates_old_bearer_and_cookie(client, db, user_factory):
    user = user_factory("matrix_logout", password=_USER_PASSWORD)
    old_token = _login(client, user.username)["access_token"]
    version_before = _token_version(db, user.id)

    response = client.post("/api/v1/auth/logout", headers=_bearer(old_token))
    assert response.status_code == 200, response.text
    assert _token_version(db, user.id) == version_before + 1
    assert client.get("/api/v1/auth/me", headers=_bearer(old_token)).status_code == 401
    assert _get_with_cookie(client, old_token).status_code == 401


def test_disable_then_enable_keeps_old_token_invalid(client, db, admin_headers, user_factory):
    user = user_factory("matrix_disable", password=_USER_PASSWORD)
    old_token = _login(client, user.username)["access_token"]
    version_after_login = _token_version(db, user.id)

    response = client.request(
        "DELETE", f"/api/v1/users/{user.id}", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    version_after_disable = _token_version(db, user.id)
    assert version_after_disable == version_after_login + 1
    assert client.get("/api/v1/auth/me", headers=_bearer(old_token)).status_code == 403
    assert _get_with_cookie(client, old_token).status_code == 403

    response = client.put(
        f"/api/v1/users/{user.id}",
        headers=admin_headers,
        json={"is_active": True},
    )
    assert response.status_code == 200, response.text
    assert _token_version(db, user.id) == version_after_disable + 1
    assert client.get("/api/v1/auth/me", headers=_bearer(old_token)).status_code == 401
    assert _get_with_cookie(client, old_token).status_code == 401

    new_token = _login(client, user.username)["access_token"]
    assert client.get("/api/v1/auth/me", headers=_bearer(new_token)).status_code == 200


def test_reset_password_invalidates_old_token_and_keeps_change_gate(
    client, db, admin_headers, user_factory
):
    user = user_factory("matrix_reset", password=_USER_PASSWORD)
    old_token = _login(client, user.username)["access_token"]
    version_before = _token_version(db, user.id)

    response = client.post(
        f"/api/v1/users/{user.id}/reset-password",
        headers=admin_headers,
        json={"new_password": _NEW_PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert response.json()["must_change_password"] is True
    assert _token_version(db, user.id) == version_before + 1
    assert client.get("/api/v1/auth/me", headers=_bearer(old_token)).status_code == 401
    assert _get_with_cookie(client, old_token).status_code == 401

    reset_login = _login(client, user.username, _NEW_PASSWORD)
    assert reset_login["must_change_password"] is True
    gated = client.get(
        "/api/v1/documents", headers=_bearer(reset_login["access_token"])
    )
    assert gated.status_code == 403
    assert gated.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_change_password_invalidates_old_tokens_and_issues_working_new_token(
    client, db, user_factory
):
    user = user_factory("matrix_change", password=_USER_PASSWORD)
    old_token = _login(client, user.username)["access_token"]
    version_before = _token_version(db, user.id)

    response = client.post(
        "/api/v1/auth/change-password",
        headers=_bearer(old_token),
        json={"old_password": _USER_PASSWORD, "new_password": _NEW_PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert _token_version(db, user.id) == version_before + 1
    new_token = response.cookies.get("access_token")
    assert new_token

    assert client.get("/api/v1/auth/me", headers=_bearer(old_token)).status_code == 401
    assert _get_with_cookie(client, old_token).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(new_token)).status_code == 200
    assert _get_with_cookie(client, new_token).status_code == 200


def test_update_user_role_change_invalidates_old_token(
    client, db, admin_headers, user_factory
):
    user = user_factory("matrix_role", password=_USER_PASSWORD)
    old_token = _login(client, user.username)["access_token"]
    version_before = _token_version(db, user.id)

    response = client.put(
        f"/api/v1/users/{user.id}",
        headers=admin_headers,
        json={"role": "admin"},
    )
    assert response.status_code == 200, response.text
    assert _token_version(db, user.id) == version_before + 1
    assert client.get("/api/v1/auth/me", headers=_bearer(old_token)).status_code == 401
    assert _get_with_cookie(client, old_token).status_code == 401

    response = client.put(
        f"/api/v1/users/{user.id}",
        headers=admin_headers,
        json={"role": "user"},
    )
    assert response.status_code == 200, response.text
    assert _token_version(db, user.id) == version_before + 2
    # 角色改回 user 后数据库角色已与旧 token 相同，仍必须由 ver 拒绝。
    assert client.get("/api/v1/auth/me", headers=_bearer(old_token)).status_code == 401
    assert _get_with_cookie(client, old_token).status_code == 401


def test_second_login_invalidates_previous_token(client, db, user_factory):
    user = user_factory("matrix_relogin", password=_USER_PASSWORD)
    first_token = _login(client, user.username)["access_token"]
    first_version = _token_version(db, user.id)

    second_token = _login(client, user.username)["access_token"]
    assert _token_version(db, user.id) == first_version + 1
    assert client.get("/api/v1/auth/me", headers=_bearer(first_token)).status_code == 401
    assert _get_with_cookie(client, first_token).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(second_token)).status_code == 200