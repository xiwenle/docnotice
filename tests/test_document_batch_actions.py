"""批量动作框架回归：预览、执行、标签、可见范围、恢复与权限。"""
from __future__ import annotations


def _mkdoc(client, headers, title, **kw):
    payload = {"title": title, "effective_from": "2026-01-01"}
    payload.update(kw)
    r = client.post("/api/v1/documents", headers=headers, json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _preview(client, headers, action, selection, params):
    r = client.post(
        f"/api/v1/documents/batch-actions/{action}/preview",
        headers=headers,
        json={"selection": selection, "params": params},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _execute(client, headers, action, selection, params, preview):
    r = client.post(
        f"/api/v1/documents/batch-actions/{action}/execute",
        headers=headers,
        json={
            "selection": selection,
            "params": params,
            "expected_count": preview["affected_count"],
            "preview_token": preview["preview_token"],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_set_category_and_execute(client, admin_headers, category_factory):
    target = category_factory("批量目标")
    d1 = _mkdoc(client, admin_headers, "批量分类甲")
    d2 = _mkdoc(client, admin_headers, "批量分类乙")
    selection = {"mode": "ids", "ids": [d1["id"], d2["id"]]}
    params = {"category_id": target.id}
    p = _preview(client, admin_headers, "set_category", selection, params)
    assert p["matched_count"] == 2
    assert p["affected_count"] == 2
    out = _execute(client, admin_headers, "set_category", selection, params, p)
    assert out["affected_count"] == 2
    for did in (d1["id"], d2["id"]):
        detail = client.get(f"/api/v1/documents/{did}", headers=admin_headers).json()
        assert detail["category_id"] == target.id


def test_preview_token_becomes_stale(client, admin_headers, category_factory):
    target = category_factory("过期预览目标")
    d1 = _mkdoc(client, admin_headers, "预览过期甲")
    d2 = _mkdoc(client, admin_headers, "预览过期乙")
    selection = {"mode": "ids", "ids": [d1["id"], d2["id"]]}
    params = {"category_id": target.id}
    p = _preview(client, admin_headers, "set_category", selection, params)
    assert client.delete(f"/api/v1/documents/{d2['id']}", headers=admin_headers).status_code == 200
    r = client.post(
        "/api/v1/documents/batch-actions/set_category/execute",
        headers=admin_headers,
        json={
            "selection": selection,
            "params": params,
            "expected_count": p["affected_count"],
            "preview_token": p["preview_token"],
        },
    )
    assert r.status_code == 409, r.text


def test_add_and_remove_multiple_tags(client, admin_headers, tag_factory):
    t1 = tag_factory("批量标签甲")
    t2 = tag_factory("批量标签乙")
    doc = _mkdoc(client, admin_headers, "多标签资料")
    selection = {"mode": "ids", "ids": [doc["id"]]}
    add_params = {"tag_ids": [t1.id, t2.id]}
    p = _preview(client, admin_headers, "add_tags", selection, add_params)
    _execute(client, admin_headers, "add_tags", selection, add_params, p)
    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
    assert {t["id"] for t in detail["tags"]} >= {t1.id, t2.id}

    remove_params = {"tag_ids": [t1.id]}
    p2 = _preview(client, admin_headers, "remove_tags", selection, remove_params)
    _execute(client, admin_headers, "remove_tags", selection, remove_params, p2)
    detail2 = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
    ids = {t["id"] for t in detail2["tags"]}
    assert t1.id not in ids
    assert t2.id in ids


def test_batch_audience_three_scope_cover(client, admin_headers, department_factory):
    d = department_factory("批量可见部门")
    doc = _mkdoc(client, admin_headers, "批量可见资料")
    selection = {"mode": "ids", "ids": [doc["id"]]}
    params = {"scope": "department", "department_ids": [d.id]}
    p = _preview(client, admin_headers, "set_audience", selection, params)
    assert p["warnings"], "全体缩小到部门应出现权限变化提示"
    _execute(client, admin_headers, "set_audience", selection, params, p)
    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
    assert detail["audience_scope"] == "department"
    assert detail["audience_department_ids"] == [d.id]


def test_batch_restore(client, admin_headers):
    doc = _mkdoc(client, admin_headers, "批量恢复资料")
    assert client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers).status_code == 200
    selection = {"mode": "ids", "ids": [doc["id"]]}
    p = _preview(client, admin_headers, "restore", selection, {})
    assert p["affected_count"] == 1
    _execute(client, admin_headers, "restore", selection, {}, p)
    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
    assert detail["archived"] is False


def test_filter_selection_and_admin_filters(client, admin_headers, category_factory):
    cat = category_factory("组合筛选分类")
    d1 = _mkdoc(client, admin_headers, "组合筛选目标", category_id=cat.id)
    _mkdoc(client, admin_headers, "组合筛选干扰")
    selection = {
        "mode": "filter",
        "filters": {"category_id": cat.id, "audience_scope": "all"},
    }
    p = _preview(client, admin_headers, "clear_category", selection, {})
    assert p["matched_count"] == 1
    _execute(client, admin_headers, "clear_category", selection, {}, p)
    detail = client.get(f"/api/v1/documents/{d1['id']}", headers=admin_headers).json()
    assert detail["category_id"] is None


def test_non_admin_cannot_preview(client, user_headers):
    r = client.post(
        "/api/v1/documents/batch-actions/set_category/preview",
        headers=user_headers,
        json={"selection": {"mode": "ids", "ids": [1]}, "params": {"category_id": 1}},
    )
    assert r.status_code == 403, r.text


def test_list_admin_filters_and_sort(client, admin_headers, category_factory):
    cat = category_factory("列表筛选分类")
    _mkdoc(client, admin_headers, "列表筛选A", category_id=cat.id,
               audience_scope="all")
    _mkdoc(client, admin_headers, "列表筛选B", category_id=cat.id,
           audience_scope="all")
    r = client.get(
        "/api/v1/documents",
        headers=admin_headers,
        params={"category_id": cat.id, "audience_scope": "all", "sort": "title_asc", "page_size": 100},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert all(x["audience_scope"] == "all" for x in items)
    assert items[0]["title"] <= items[-1]["title"]
    assert "updated_at" in items[0]

def test_user_and_department_ids_lookup(client, admin_headers, user_factory, department_factory):
    u1 = user_factory("ids用户甲", role="user", password="user1234")
    u2 = user_factory("ids用户乙", role="user", password="user1234")
    d1 = department_factory("ids部门甲")
    d2 = department_factory("ids部门乙")
    ur = client.get("/api/v1/users", headers=admin_headers,
                    params={"ids": [u1.id, u2.id], "page_size": 100})
    assert ur.status_code == 200, ur.text
    assert {x["id"] for x in ur.json()["items"]} == {u1.id, u2.id}
    dr = client.get("/api/v1/departments", headers=admin_headers,
                    params={"ids": [d1.id, d2.id], "page_size": 100})
    assert dr.status_code == 200, dr.text
    assert {x["id"] for x in dr.json()["items"]} == {d1.id, d2.id}