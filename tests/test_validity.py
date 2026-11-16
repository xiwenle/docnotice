"""时效测试（项目规范 §14/§15/§16/§31、DESIGN §7.1–7.2）：纯函数 + API 双路径。

覆盖边界：生效前一天/当天/后一天、失效前一天/当天/后一天、无失效日期、
未来生效、已失效、已归档、闭区间（2026-06-30 active / 07-01 expired）、
as_of_date 历史重算、R1 冲突 409、R2 确认覆盖。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import AppError
from app.services.validity_service import (
    check_validity_r1,
    compute_status,
    require_confirm_overwrite,
    windows_overlap,
)
from helpers import create_document


# ---------------------------------------------------------------------------
# 纯函数：compute_status 边界（§14 闭区间、§13 无失效日期、§15 任意日期重算）
# ---------------------------------------------------------------------------
class TestComputeStatusPure:
    EFF_FROM = date(2026, 1, 1)
    EFF_TO = date(2026, 6, 30)

    def test_生效前一天_pending(self):
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2025, 12, 31)) == "pending"

    def test_生效当天_active(self):
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 1, 1)) == "active"

    def test_生效后一天_active(self):
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 1, 2)) == "active"

    def test_区间内_active(self):
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 3, 15)) == "active"

    def test_失效前一天_active(self):
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 6, 29)) == "active"

    def test_失效当天_active_闭区间(self):
        """2026-06-30 仍有效（闭区间，§14）。"""
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 6, 30)) == "active"

    def test_失效后一天_expired(self):
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 7, 1)) == "expired"

    def test_无失效日期_暂无失效日期(self):
        """effective_to=None：暂无失效日期，任何未来日期仍 active（§13）。"""
        assert compute_status(self.EFF_FROM, None, date(2030, 12, 31)) == "active"
        assert compute_status(self.EFF_FROM, None, date(2026, 6, 30)) == "active"

    def test_未来生效_pending(self):
        assert compute_status(date(2027, 1, 1), None, date(2026, 12, 31)) == "pending"
        assert compute_status(date(2027, 1, 1), None, date(2027, 1, 1)) == "active"

    def test_已失效(self):
        assert compute_status(date(2020, 1, 1), date(2021, 1, 1), date(2026, 1, 1)) == "expired"

    def test_已归档_优先级最高(self):
        """archived 优先于一切日期状态（§14）。"""
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 3, 1), archived=True) == "archived"
        assert compute_status(date(2027, 1, 1), None, date(2026, 1, 1), archived=True) == "archived"

    def test_历史日期重算(self):
        """同一资料按不同 as_of_date 得到不同状态（§15：不只看今天）。"""
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2025, 6, 1)) == "pending"
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2026, 6, 1)) == "active"
        assert compute_status(self.EFF_FROM, self.EFF_TO, date(2027, 6, 1)) == "expired"


# ---------------------------------------------------------------------------
# 纯函数：R1 / R2 冲突检查（§16）
# ---------------------------------------------------------------------------
class TestValidityConflicts:
    def test_r1_日期倒挂_冲突(self):
        with pytest.raises(AppError) as exc:
            check_validity_r1(date(2026, 7, 1), date(2026, 6, 30))
        assert exc.value.code == "VALIDITY_CONFLICT"

    def test_r1_缺失生效日_冲突(self):
        with pytest.raises(AppError) as exc:
            check_validity_r1(None, None)
        assert exc.value.code == "VALIDITY_CONFLICT"

    def test_r1_合法区间_通过(self):
        check_validity_r1(date(2026, 1, 1), date(2026, 6, 30))
        check_validity_r1(date(2026, 1, 1), None)

    def test_windows_overlap(self):
        assert windows_overlap(date(2026, 2, 1), None, date(2026, 1, 1), date(2026, 6, 30)) is True
        assert windows_overlap(date(2026, 2, 1), date(2026, 3, 1), date(2026, 1, 1), date(2026, 2, 15)) is True
        assert windows_overlap(date(2026, 7, 1), date(2026, 8, 1), date(2026, 1, 1), date(2026, 6, 30)) is False

    def test_r2_重叠未确认_冲突(self):
        with pytest.raises(AppError) as exc:
            require_confirm_overwrite(
                old_from=date(2026, 1, 1), old_to=date(2026, 6, 30),
                new_from=date(2026, 3, 1), new_to=date(2026, 9, 30),
                confirm_overwrite=False,
            )
        assert exc.value.code == "VALIDITY_CONFLICT"

    def test_r2_不重叠_无需确认(self):
        require_confirm_overwrite(
            old_from=date(2026, 1, 1), old_to=date(2026, 6, 30),
            new_from=date(2027, 1, 1), new_to=None,
            confirm_overwrite=False,
        )

    def test_r2_确认后放行(self):
        require_confirm_overwrite(
            old_from=date(2026, 1, 1), old_to=date(2026, 6, 30),
            new_from=date(2026, 3, 1), new_to=date(2026, 9, 30),
            confirm_overwrite=True,
        )


# ---------------------------------------------------------------------------
# API 路径：状态端点 + as_of_date 历史重算（§15）
# ---------------------------------------------------------------------------
class TestDocumentStatusApi:
    def _create(self, client, headers, **kw):
        return create_document(client, headers, **kw)

    def test_status_dates_boundary(self, client, admin_headers):
        """API 状态端点：闭区间 + 前一天/当天/后一天。"""
        doc_id = self._create(client, admin_headers, title="时效边界",
                              effective_from="2026-01-01", effective_to="2026-06-30")["id"]
        cases = {
            "2025-12-31": "pending",
            "2026-01-01": "active",
            "2026-01-02": "active",
            "2026-06-29": "active",
            "2026-06-30": "active",  # 闭区间当天有效
            "2026-07-01": "expired",  # 次日失效
        }
        for as_of, expected in cases.items():
            resp = client.get(f"/api/v1/documents/{doc_id}/status?date={as_of}", headers=admin_headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == expected, f"as_of={as_of} 期望 {expected} 实得 {body}"

    def test_status_without_effective_to_never_expires(self, client, admin_headers):
        doc_id = self._create(client, admin_headers, title="永续资料",
                              effective_from="2026-01-01")["id"]
        for as_of in ("2026-01-01", "2026-06-30", "2030-12-31"):
            resp = client.get(f"/api/v1/documents/{doc_id}/status?date={as_of}", headers=admin_headers)
            assert resp.json()["status"] == "active"

    def test_status_defaults_to_today(self, client, admin_headers):
        doc_id = self._create(client, admin_headers, title="默认今天",
                              effective_from="2020-01-01", effective_to="2021-01-01")["id"]
        resp = client.get(f"/api/v1/documents/{doc_id}/status", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "expired"  # 2021 年已过（本机时钟 2026）

    def test_list_status_filter_and_redo(self, client, admin_headers):
        """列表 status 筛选 + as_of_date 按历史日重算（§15）。"""
        self._create(client, admin_headers, title="历史窗口", effective_from="2026-01-01", effective_to="2026-06-30")
        self._create(client, admin_headers, title="未来窗口", effective_from="2027-01-01")
        # 以 2026-03-01 重算：历史窗口 active、未来窗口 pending
        resp = client.get("/api/v1/documents?as_of_date=2026-03-01", headers=admin_headers)
        by_title = {d["title"]: d["status"] for d in resp.json()["items"]}
        assert by_title["历史窗口"] == "active"
        assert by_title["未来窗口"] == "pending"
        # 以 2027-02-01 重算：历史窗口 expired、未来窗口 active
        resp = client.get("/api/v1/documents?as_of_date=2027-02-01", headers=admin_headers)
        by_title = {d["title"]: d["status"] for d in resp.json()["items"]}
        assert by_title["历史窗口"] == "expired"
        assert by_title["未来窗口"] == "active"
        # status 筛选
        resp = client.get("/api/v1/documents?status=expired&as_of_date=2027-02-01", headers=admin_headers)
        titles = [d["title"] for d in resp.json()["items"]]
        assert "历史窗口" in titles and "未来窗口" not in titles


# ---------------------------------------------------------------------------
# API 路径：R1 创建冲突 / R2 修改覆盖（§16）
# ---------------------------------------------------------------------------
class TestValidityApiConflicts:
    def test_create_r1_conflict_409(self, client, admin_headers):
        resp = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "倒挂资料",
            "effective_from": "2026-07-01",
            "effective_to": "2026-06-30",
        })
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "VALIDITY_CONFLICT"
        assert "effective_from" in resp.json()["error"]["message"]

    def test_update_r2_overlap_without_confirm_409(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="时效覆盖",
                                 effective_from="2026-01-01", effective_to="2026-06-30")["id"]
        resp = client.put(f"/api/v1/documents/{doc_id}", headers=admin_headers, json={
            "effective_from": "2026-03-01",
            "effective_to": "2026-09-30",
        })
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "VALIDITY_CONFLICT"
        # 资料时效未被改变（不静默覆盖，§16）
        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["effective_from"] == "2026-01-01"
        assert detail["effective_to"] == "2026-06-30"

    def test_update_r2_confirm_overwrite_ok(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="确认覆盖",
                                 effective_from="2026-01-01", effective_to="2026-06-30")["id"]
        resp = client.put(f"/api/v1/documents/{doc_id}", headers=admin_headers, json={
            "effective_from": "2026-03-01",
            "effective_to": "2026-09-30",
            "confirm_overwrite": True,
            "modify_reason": "业务周期延长",
        })
        assert resp.status_code == 200, resp.text
        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["effective_from"] == "2026-03-01"
        assert detail["effective_to"] == "2026-09-30"
        # 强制审计 modify_validity（§16 R2）
        logs = client.get("/api/v1/audit-logs?action=modify_validity", headers=admin_headers).json()
        assert logs["total"] >= 1
        assert "业务周期延长" in logs["items"][0]["detail"]

    def test_update_r2_confirm_requires_reason(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="缺原因",
                                 effective_from="2026-01-01", effective_to="2026-06-30")["id"]
        resp = client.put(f"/api/v1/documents/{doc_id}", headers=admin_headers, json={
            "effective_from": "2026-03-01",
            "confirm_overwrite": True,
            "modify_reason": "",
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_update_non_overlapping_without_confirm_ok(self, client, admin_headers):
        """新窗口与旧窗口不重叠 → 无需确认即可改（R2 语义）。"""
        doc_id = create_document(client, admin_headers, title="接续窗口",
                                 effective_from="2026-01-01", effective_to="2026-06-30")["id"]
        resp = client.put(f"/api/v1/documents/{doc_id}", headers=admin_headers, json={
            "effective_from": "2027-01-01",
            "effective_to": "2027-12-31",
        })
        assert resp.status_code == 200, resp.text

    def test_create_ok_with_null_effective_to(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="无失效日",
                                 effective_from="2026-01-01")["id"]
        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["effective_to"] is None
        assert detail["status"] == "active"