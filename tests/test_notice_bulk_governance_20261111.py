"""通知批量治理与阅读统计部门下钻（2026-11-11）。"""
from __future__ import annotations

import json

from helpers import create_document, login_headers


def _detail_json(audit_item) -> dict:
    raw = audit_item.get("detail")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _create_targeted_notice(
    client,
    headers,
    *,
    title: str,
    user_id: int,
    notice_type: str = "发布",
    document_ids: list[int] | None = None,
    effective_at: str | None = None,
) -> dict:
    payload = {
        "title": title,
        "content": title + "正文",
        "notice_type": notice_type,
        "target": {"scope": "user", "user_ids": [user_id]},
    }
    if document_ids:
        payload["document_ids"] = document_ids
    if effective_at:
        payload["effective_at"] = effective_at
    resp = client.post("/api/v1/notices", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_bulk_cancel_restore_skips_and_preserves_linkage(client, admin_headers, user_factory):
    reader = user_factory("批量读者", password="user1234", display_name="批量读者")
    doc = create_document(
        client,
        admin_headers,
        title="批量联动资料",
        effective_from="2026-01-01",
        effective_to="2030-12-31",
    )
    linked = _create_targeted_notice(
        client,
        admin_headers,
        title="批量废止通知",
        user_id=reader.id,
        notice_type="废止",
        document_ids=[doc["id"]],
        effective_at="2026-11-11T00:00:00",
    )
    ordinary = _create_targeted_notice(
        client,
        admin_headers,
        title="批量普通通知",
        user_id=reader.id,
    )

    # 覆盖「已在回收站」跳过分支，并验证单条旧语义未变。
    assert client.post(
        f"/api/v1/notices/{ordinary['id']}/cancel",
        headers=admin_headers,
        json={"reason": "先单条删除"},
    ).status_code == 200

    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
    assert detail["effective_to"] == "2026-11-11"

    cancel = client.post(
        "/api/v1/notices/bulk-cancel",
        headers=admin_headers,
        json={
            "notice_ids": [linked["id"], ordinary["id"], 999999, linked["id"]],
            "reason": "批量整理",
        },
    )
    assert cancel.status_code == 200, cancel.text
    cancel_body = cancel.json()
    assert cancel_body["requested"] == 3, "重复 id 应只处理一次"
    assert [row["id"] for row in cancel_body["succeeded"]] == [linked["id"]]
    assert {row["id"] for row in cancel_body["skipped"]} == {ordinary["id"], 999999}
    assert all(row["reason"] for row in cancel_body["skipped"])

    # 软删除：进入通知回收站，详情 API 不再可见。
    deleted = client.get("/api/v1/recycle-bin/notices", headers=admin_headers).json()["items"]
    assert {row["id"] for row in deleted} == {linked["id"], ordinary["id"]}
    assert client.get(
        f"/api/v1/notices/{linked['id']}", headers=admin_headers
    ).status_code == 404

    # 旧联动保持：取消废止通知后资料原失效日回滚。
    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
    assert detail["effective_to"] == "2030-12-31"

    logs = client.get(
        "/api/v1/audit-logs?action=cancel_notice&page_size=100",
        headers=admin_headers,
    ).json()["items"]
    summaries = [
        _detail_json(row) for row in logs
        if _detail_json(row).get("via") == "notice_bulk_cancel"
    ]
    assert len(summaries) == 1
    assert summaries[0]["requested"] == 3
    assert summaries[0]["succeeded"] == [linked["id"]]
    assert {row["id"] for row in summaries[0]["skipped"]} == {ordinary["id"], 999999}

    restore = client.post(
        "/api/v1/recycle-bin/notices/bulk-restore",
        headers=admin_headers,
        json={"notice_ids": [linked["id"], ordinary["id"], 999999]},
    )
    assert restore.status_code == 200, restore.text
    restore_body = restore.json()
    assert {row["id"] for row in restore_body["succeeded"]} == {linked["id"], ordinary["id"]}
    assert [row["id"] for row in restore_body["skipped"]] == [999999]

    # 新状态联动保持：恢复废止通知后重新截断资料有效期。
    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
    assert detail["effective_to"] == "2026-11-11"
    assert client.get(
        f"/api/v1/notices/{linked['id']}", headers=admin_headers
    ).status_code == 200
    assert client.get(
        f"/api/v1/notices/{ordinary['id']}", headers=admin_headers
    ).status_code == 200
    assert client.get(
        "/api/v1/recycle-bin/notices", headers=admin_headers
    ).json()["items"] == []

    restore_logs = client.get(
        "/api/v1/audit-logs?action=recycle_restore&page_size=100",
        headers=admin_headers,
    ).json()["items"]
    restore_summaries = [
        _detail_json(row) for row in restore_logs
        if _detail_json(row).get("via") == "notice_bulk_restore"
    ]
    assert len(restore_summaries) == 1
    assert set(restore_summaries[0]["succeeded"]) == {linked["id"], ordinary["id"]}
    assert [row["id"] for row in restore_summaries[0]["skipped"]] == [999999]


def test_notice_bulk_routes_admin_only_and_validate_empty(
    client, admin_headers, user_headers
):
    denied_cancel = client.post(
        "/api/v1/notices/bulk-cancel",
        headers=user_headers,
        json={"notice_ids": [1]},
    )
    assert denied_cancel.status_code == 403
    denied_restore = client.post(
        "/api/v1/recycle-bin/notices/bulk-restore",
        headers=user_headers,
        json={"notice_ids": [1]},
    )
    assert denied_restore.status_code == 403

    empty_cancel = client.post(
        "/api/v1/notices/bulk-cancel",
        headers=admin_headers,
        json={"notice_ids": []},
    )
    assert empty_cancel.status_code == 400
    assert empty_cancel.json()["error"]["code"] == "VALIDATION_ERROR"
    empty_restore = client.post(
        "/api/v1/recycle-bin/notices/bulk-restore",
        headers=admin_headers,
        json={"notice_ids": []},
    )
    assert empty_restore.status_code == 400
    assert empty_restore.json()["error"]["code"] == "VALIDATION_ERROR"


def test_audience_department_drilldown_filters_before_pagination(
    client, admin_headers, department_factory, user_factory
):
    dept_a = department_factory("部门甲")
    dept_b = department_factory("部门乙")
    user_a1 = user_factory(
        "dept_a1", password="user1234", display_name="甲一", department_ids=[dept_a.id, dept_b.id]
    )
    user_factory(
        "dept_a2", password="user1234", display_name="甲二", department_ids=[dept_a.id]
    )
    user_factory(
        "dept_b1", password="user1234", display_name="乙一", department_ids=[dept_b.id]
    )

    notice_resp = client.post(
        "/api/v1/notices",
        headers=admin_headers,
        json={
            "title": "部门下钻通知",
            "content": "查看部门名单",
            "notice_type": "发布",
            "target": {"scope": "department", "department_ids": [dept_a.id, dept_b.id]},
        },
    )
    assert notice_resp.status_code == 201, notice_resp.text
    notice_id = notice_resp.json()["id"]

    user_a1_headers = login_headers(client, "dept_a1", "user1234")
    assert client.post(
        f"/api/v1/notices/{notice_id}/read", headers=user_a1_headers
    ).status_code == 200

    stats = client.get(
        f"/api/v1/notices/{notice_id}/statistics", headers=admin_headers
    ).json()
    by_dept = {row["department_id"]: row for row in stats["by_department"]}
    assert by_dept[dept_a.id]["target"] == 2
    assert by_dept[dept_a.id]["viewed"] == 1
    assert by_dept[dept_a.id]["unviewed"] == 1
    assert by_dept[dept_b.id]["target"] == 2, "多部门用户应在每个部门口径中各计一次"
    assert by_dept[dept_b.id]["viewed"] == 1

    all_a = client.get(
        f"/api/v1/notices/{notice_id}/audience",
        headers=admin_headers,
        params={"department_id": dept_a.id, "read_status": "all", "page_size": 1},
    ).json()
    assert all_a["total"] == 2
    assert all_a["department_id"] == dept_a.id
    assert len(all_a["items"]) == 1, "部门过滤必须先于分页，total 不应受当前页限制"

    unread_a = client.get(
        f"/api/v1/notices/{notice_id}/audience",
        headers=admin_headers,
        params={"department_id": dept_a.id, "read_status": "unread"},
    ).json()
    assert unread_a["total"] == 1
    assert [row["username"] for row in unread_a["items"]] == ["dept_a2"]

    all_b = client.get(
        f"/api/v1/notices/{notice_id}/audience",
        headers=admin_headers,
        params={"department_id": dept_b.id, "read_status": "all"},
    ).json()
    assert {row["username"] for row in all_b["items"]} == {"dept_a1", "dept_b1"}
    assert user_a1.id in {row["user_id"] for row in all_b["items"]}


def test_bulk_controls_render_on_admin_pages(client, admin_headers):
    notices_page = client.get("/notices", headers=admin_headers)
    assert notices_page.status_code == 200
    assert 'id="notice-bulk-bar"' in notices_page.text
    assert 'id="btn-notice-bulk-cancel"' in notices_page.text

    recycle_page = client.get("/admin/recycle-bin", headers=admin_headers)
    assert recycle_page.status_code == 200
    assert 'id="notice-rb-bulk-bar"' in recycle_page.text
    assert 'id="btn-notice-rb-bulk-restore"' in recycle_page.text
    assert 'id="notice-rb-check-all"' in recycle_page.text
