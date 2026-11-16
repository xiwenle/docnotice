"""测试公共 API helper（t23；供后续测试文件复用）。

约定：全部是**薄封装**，契约对齐 docs/API.md：
- 登录（§1）：POST /api/v1/auth/login
- 资料（§6）：POST /api/v1/documents、POST /api/v1/documents/{id}/file
- 通知（§7）：POST /api/v1/notices（multipart 或 JSON）

注意生命周期：登录接口（阶段1 t20）已可运行；资料/通知接口由后端阶段2/3
（t21/t22）落地后生效 —— helper 按 API.md 契约编写，功能落地即测。
"""
from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# 认证（API.md §1）
# ---------------------------------------------------------------------------
def login_user(client, username: str, password: str) -> dict[str, Any]:
    """调用登录接口，返回完整响应 JSON（含 access_token / user）。

    失败时抛出 AssertionError 并携带响应体，便于定位。
    """
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"登录失败 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("access_token"), f"登录响应缺少 access_token: {body}"
    return body


def bearer_headers(token: str) -> dict[str, str]:
    """由 JWT 生成 Bearer 认证头。"""
    return {"Authorization": f"Bearer {token}"}


def login_headers(client, username: str, password: str) -> dict[str, str]:
    """登录并直接返回认证头。"""
    return bearer_headers(login_user(client, username, password)["access_token"])


# ---------------------------------------------------------------------------
# 资料（API.md §6；端点由 t21 落地）
# ---------------------------------------------------------------------------
def create_document(
    client,
    headers: dict[str, str],
    *,
    title: str = "测试资料",
    summary: Optional[str] = None,
    category_id: Optional[int] = None,
    effective_from: str = "2026-01-01",
    effective_to: Optional[str] = None,
    tag_ids: Optional[list[int]] = None,
) -> dict[str, Any]:
    """新建资料（返回响应 JSON；201 期望成功）。"""
    payload: dict[str, Any] = {
        "title": title,
        "effective_from": effective_from,
    }
    if summary is not None:
        payload["summary"] = summary
    if category_id is not None:
        payload["category_id"] = category_id
    if effective_to is not None:
        payload["effective_to"] = effective_to
    if tag_ids is not None:
        payload["tag_ids"] = tag_ids
    resp = client.post("/api/v1/documents", json=payload, headers=headers)
    assert resp.status_code in (200, 201), f"创建资料失败 {resp.status_code}: {resp.text}"
    return resp.json()


def upload_document_file(
    client,
    headers: dict[str, str],
    doc_id: int,
    *,
    filename: str,
    content: bytes,
    mime: str = "application/octet-stream",
):
    """上传/替换资料文件（multipart，字段名 file）。"""
    import io

    return client.post(
        f"/api/v1/documents/{doc_id}/file",
        files={"file": (filename, io.BytesIO(content), mime)},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# 通知（API.md §7；端点由 t22 落地）
# ---------------------------------------------------------------------------
def create_notice(
    client,
    headers: dict[str, str],
    *,
    title: str = "测试通知",
    content: Optional[str] = None,
    notice_type: Optional[str] = "发布",
    effective_at: Optional[str] = None,
    document_ids: Optional[list[int]] = None,
    target: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """新建通知（2026-11-04 起创建即发布；JSON 形式）。target 示例：{"scope": "all"}。"""
    payload: dict[str, Any] = {"title": title}
    if notice_type is not None:
        payload["notice_type"] = notice_type
    if content is not None:
        payload["content"] = content
    if effective_at is not None:
        payload["effective_at"] = effective_at
    if document_ids is not None:
        payload["document_ids"] = document_ids
    if target is not None:
        payload["target"] = target
    resp = client.post("/api/v1/notices", json=payload, headers=headers)
    # 2026-11-04：创建即发布，「全体」对象必须有真实读者；没有就补一个再重试
    if resp.status_code == 400 and "阅读对象范围为空" in resp.text:
        client.post("/api/v1/users", headers=headers, json={
            "username": "helper_reader", "password": "user1234",
            "display_name": "辅助读者", "role": "user",
        })
        resp = client.post("/api/v1/notices", json=payload, headers=headers)
    assert resp.status_code in (200, 201), f"创建通知失败 {resp.status_code}: {resp.text}"
    return resp.json()