"""P1 缺陷回归（2026-09-29 多 agent 全项目审计）。

每条用例对应审计报告里一处**已复现**的缺陷；修复前后行为对比如下，锁死不再回退：

- P1-A 资料只传 effective_to 时被静默丢弃（document_service.update_document）
- P1-B 通知替换文件失败后旧文件凭空消失（notice_service.update_notice 顺序）
- P1-C 资料关联通知未按阅读对象过滤（document_service._linked_notices）
- P1-D 取消「废止/替换」通知时静默不回滚资料时效（notice_service.cancel_notice）
- P2-E 彻底删除通知时子表被删两次（purge_notice 重复删除 → SAWarning）

审计背景见 交接记录「2026-09-29 多 agent 审计与修复」一节。
"""
from __future__ import annotations

import io
import warnings

from sqlalchemy.exc import SAWarning

from files.generators import (
    DOCX_FILENAME,
    PDF_FILENAME,
    make_docx,
    make_pdf,
    write_raw_file,
)
from helpers import create_document


# ---------------------------------------------------------------------------
# 公共薄封装（与 tests/test_notices.py 同口径）
# ---------------------------------------------------------------------------
def _create_notice(client, headers, **kw):
    payload = {
        "title": kw.get("title", "测试通知"),
        "notice_type": kw.get("notice_type", "发布"),
    }
    if kw.get("content") is not None:
        payload["content"] = kw["content"]
    if kw.get("effective_at"):
        payload["effective_at"] = kw["effective_at"]
    if kw.get("document_ids"):
        payload["document_ids"] = kw["document_ids"]
    if kw.get("target") is not None:
        payload["target"] = kw["target"]
    resp = client.post("/api/v1/notices", headers=headers, json=payload)
    # 2026-11-04：创建即发布，需有真实阅读对象；无读者时补一个再重试
    if resp.status_code == 400 and "阅读对象范围为空" in resp.text:
        client.post("/api/v1/users", headers=headers, json={
            "username": "auto_reader", "password": "user1234",
            "display_name": "自动补建读者", "role": "user",
        })
        resp = client.post("/api/v1/notices", headers=headers, json=payload)
    assert resp.status_code == 201, f"创建通知失败 {resp.status_code}: {resp.text}"
    return resp.json()


class _OkResp:
    """2026-11-04：创建即发布，/publish 已移除；保留占位以兼容旧断言。"""

    status_code = 200

    def __init__(self, body=None):
        self._body = body or {}

    def json(self):
        return self._body


def _publish(client, headers, notice_id, **kw):
    detail = client.get(f"/api/v1/notices/{notice_id}", headers=headers)
    if detail.status_code != 200:
        return detail
    return _OkResp(detail.json())




def _admin_headers(login):
    """在**当前时钟**下重新签发管理员令牌。

    冻结时间（freezegun）跨日测试必须用这个：令牌的 iat/exp 都按签发时刻算，
    若沿用冻结外签发的令牌，跨日校验会因 exp 已过而 401。
    """
    from tests.conftest import ADMIN_PASSWORD

    return {"Authorization": f"Bearer {login('admin', ADMIN_PASSWORD)}"}


def _detail(client, headers, doc_id) -> dict:
    resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# P1-A：仅传 effective_to 被静默丢弃
# ---------------------------------------------------------------------------
class TestPartialValidityUpdate:
    def test_effective_to_only_is_applied(self, client, admin_headers):
        """只传 effective_to（DocumentUpdate 明确「可部分」）必须真的落库。

        修复前：validity_changed 只认 effective_from / clear_effective_to，
        该请求整段被跳过 —— 返回 200 但 effective_to 仍是 None（静默丢弃）。
        """
        doc_id = create_document(client, admin_headers, title="仅改失效日")["id"]
        assert _detail(client, admin_headers, doc_id)["effective_to"] is None

        resp = client.put(
            f"/api/v1/documents/{doc_id}", headers=admin_headers,
            json={
                "effective_to": "2026-12-31",
                "confirm_overwrite": True,
                "modify_reason": "补充失效日期",
            },
        )
        assert resp.status_code == 200, resp.text
        assert _detail(client, admin_headers, doc_id)["effective_to"] == "2026-12-31"

    def test_effective_to_only_still_passes_r2_gate(self, client, admin_headers):
        """只传 effective_to 也必须走 R2 门禁，不能绕过（§16）。"""
        doc_id = create_document(client, admin_headers, title="仅改失效日-R2")["id"]
        resp = client.put(
            f"/api/v1/documents/{doc_id}", headers=admin_headers,
            json={"effective_to": "2026-12-31"},  # 与既有区间重叠且未确认覆盖
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "VALIDITY_CONFLICT"
        assert _detail(client, admin_headers, doc_id)["effective_to"] is None


# ---------------------------------------------------------------------------
# P1-B：通知替换文件失败 → 旧文件必须完好
# ---------------------------------------------------------------------------
class TestNoticeFileReplaceFailure:
    def test_invalid_new_file_leaves_old_file_downloadable(
        self, client, admin_headers, normal_user, tmp_path
    ):
        """上传非法新文件被拒后，通知仍应能下载**原来那份**文件。

        修复前：先把旧文件移进回收站、再校验新文件（save_upload 内部），
        校验失败时事务回滚让库记录认为「旧文件仍在原路径」，而磁盘文件已被移走、
        回收站记录也随回滚消失 → 文件凭空消失，下载报「文件不存在」。
        """
        pdf = make_pdf(tmp_path / PDF_FILENAME)
        data = pdf.read_bytes()
        resp = client.post(
            "/api/v1/notices", headers=admin_headers,
            data={"title": "替换失败保护", "notice_type": "发布"},
            files={"file": (PDF_FILENAME, io.BytesIO(data), "application/pdf")},
        )
        assert resp.status_code == 201, resp.text
        notice_id = resp.json()["id"]

        before = client.get(f"/api/v1/notices/{notice_id}/file", headers=admin_headers)
        assert before.status_code == 200, before.text
        assert before.content == data

        # 伪装成 PDF 的可执行内容 → 魔数校验 415
        exe = write_raw_file(tmp_path / "内鬼.exe", b"MZ\x90\x00" + b"\x00" * 64)
        bad = client.put(
            f"/api/v1/notices/{notice_id}", headers=admin_headers,
            data={"title": "替换失败保护"},
            files={"file": ("伪装.pdf", io.BytesIO(exe.read_bytes()), "application/pdf")},
        )
        assert bad.status_code == 415, bad.text
        assert bad.json()["error"]["code"] == "FILE_TYPE_MISMATCH"

        after = client.get(f"/api/v1/notices/{notice_id}/file", headers=admin_headers)
        assert after.status_code == 200, f"旧文件丢失（修复前症状）: {after.text}"
        assert after.content == data

    def test_valid_replace_still_works(self, client, admin_headers, normal_user, tmp_path):
        """正常替换路径不受本次改动影响：换成功且旧文件进回收站。"""
        pdf = make_pdf(tmp_path / PDF_FILENAME)
        first = pdf.read_bytes()
        resp = client.post(
            "/api/v1/notices", headers=admin_headers,
            data={"title": "正常替换", "notice_type": "发布"},
            files={"file": (PDF_FILENAME, io.BytesIO(first), "application/pdf")},
        )
        notice_id = resp.json()["id"]

        docx = make_docx(tmp_path / DOCX_FILENAME)
        second = docx.read_bytes()
        ok = client.put(
            f"/api/v1/notices/{notice_id}", headers=admin_headers,
            data={"title": "正常替换"},
            files={
                "file": (
                    DOCX_FILENAME,
                    io.BytesIO(second),
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document",
                )
            },
        )
        assert ok.status_code == 200, ok.text
        got = client.get(f"/api/v1/notices/{notice_id}/file", headers=admin_headers)
        assert got.status_code == 200
        assert got.content == second


# ---------------------------------------------------------------------------
# P1-C：资料关联通知按阅读对象过滤
# ---------------------------------------------------------------------------
class TestLinkedNoticesAudience:
    def test_user_outside_audience_cannot_read_metadata(
        self, client, admin_headers, user_factory
    ):
        """不在阅读对象内的普通用户，不应从资料详情看到该通知的标题/类型/状态。

        修复前：_linked_notices 只挡 cancelled / draft，与 /notices 列表口径不一致，
        构成跨阅读对象的元数据泄露。
        """
        user_factory("看客", role="user", password="user1234")
        insider = user_factory("读者", role="user", password="user1234")
        doc_id = create_document(client, admin_headers, title="关联通知的资料")["id"]
        notice = _create_notice(
            client, admin_headers, title="只发给读者的通知", content="内部",
            document_ids=[doc_id],
            target={"scope": "user", "user_ids": [insider.id]},
        )
        assert _publish(client, admin_headers, notice["id"]).status_code == 200

        # 阅读对象内：能看到
        from helpers import login_headers

        ins = _detail(client, login_headers(client, "读者", "user1234"), doc_id)
        assert [n["title"] for n in ins["linked_notices"]] == ["只发给读者的通知"]

        # 阅读对象外：看不到（管理员不受限，仍可见）
        out = _detail(client, login_headers(client, "看客", "user1234"), doc_id)
        assert out["linked_notices"] == []
        admin = _detail(client, admin_headers, doc_id)
        assert [n["title"] for n in admin["linked_notices"]] == ["只发给读者的通知"]


# ---------------------------------------------------------------------------
# P1-D：取消废止/替换通知必须回滚资料时效
# ---------------------------------------------------------------------------
class TestCancelRollback:
    def test_cancel_rolls_back_when_effective_at_is_none(
        self, client, admin_user, login, freeze
    ):
        """effective_at 为空的废止通知，隔天取消也要回滚（§31 联动）。

        修复前：取消时用 now() 现算「本通知写入的失效日」，隔天算出的值与当初写入值
        不等 → 判据恒不成立 → 静默不回滚，资料被永久停在废止日上。
        """
        # 令牌必须在冻结时钟内签发，否则跨日校验会因 exp/iat 不一致而 401
        with freeze("2026-05-10 10:00:00"):
            h = _admin_headers(login)
            doc_id = create_document(client, h, title="无生效日的废止对象")["id"]
            notice = _create_notice(
                client, h, title="即日废止", content="废止",
                notice_type="废止", document_ids=[doc_id],
            )
            assert _publish(client, h, notice["id"]).status_code == 200
            assert _detail(client, h, doc_id)["effective_to"] == "2026-05-10"

        # 隔天取消：必须回滚为「暂无失效日期」
        with freeze("2026-05-11 10:00:00"):
            h2 = _admin_headers(login)
            resp = client.post(
                f"/api/v1/notices/{notice['id']}/cancel", headers=h2,
                json={"reason": "误发"},
            )
            assert resp.status_code == 200, resp.text
            assert _detail(client, h2, doc_id)["effective_to"] is None

    def test_cancel_rolls_back_to_previous_effective_to(
        self, client, admin_user, login, freeze
    ):
        """回滚要还原成**原有的**失效日，而不是清空。"""
        with freeze("2026-06-01 10:00:00"):
            h = _admin_headers(login)
            doc_id = create_document(
                client, h, title="有原失效日", effective_to="2026-12-31"
            )["id"]
            notice = _create_notice(
                client, h, title="提前废止", content="废止",
                notice_type="废止", document_ids=[doc_id],
            )
            assert _publish(client, h, notice["id"]).status_code == 200
            assert _detail(client, h, doc_id)["effective_to"] == "2026-06-01"

        with freeze("2026-06-02 10:00:00"):
            h2 = _admin_headers(login)
            assert client.post(
                f"/api/v1/notices/{notice['id']}/cancel", headers=h2,
                json={"reason": "误发"},
            ).status_code == 200
            assert _detail(client, h2, doc_id)["effective_to"] == "2026-12-31"


# ---------------------------------------------------------------------------
# P2-E：彻底删除通知时子表只删一次（无 SAWarning）
# ---------------------------------------------------------------------------
class TestPurgeNoticeChildren:
    def test_purge_notice_does_not_double_delete_children(
        self, client, admin_headers, admin_user, db
    ):
        """purge_notice 不应触发 SAWarning「expected to delete 1 row(s); 0 matched」。

        修复前：先 bulk delete 子行、再 db.delete(notice)，而 targets/reads 是
        cascade="all, delete-orphan"，ORM 会对集合里仍持有的子对象再删一次，
        命中 0 行 → SAWarning（单条彻底删除与 purge-all 都会触发）。
        """
        from app.models.notice import NoticeRead, NoticeTarget
        from app.services import notice_recycle

        notice = _create_notice(
            client, admin_headers, title="彻底删除-子行", content="x",
            target={"scope": "all"},
        )
        notice_id = notice["id"]
        assert _publish(client, admin_headers, notice_id).status_code == 200
        # 软删除进回收站（purge 的前置状态）；已发布通知走 cancel，草稿才走 DELETE
        assert client.post(
            f"/api/v1/notices/{notice_id}/cancel", headers=admin_headers,
            json={"reason": "测试彻底删除"},
        ).status_code == 200

        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            notice_recycle.purge_notice(
                db, current_user=admin_user, notice_id=notice_id, confirm=True
            )

        assert (
            db.query(NoticeTarget).filter(NoticeTarget.notice_id == notice_id).count() == 0
        )
        assert (
            db.query(NoticeRead).filter(NoticeRead.notice_id == notice_id).count() == 0
        )
