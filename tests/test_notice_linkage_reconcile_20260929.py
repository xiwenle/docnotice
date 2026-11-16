"""废止/替换通知的时效联动重算（§31 / §18）—— 2026-09-29 方案 B+ 守卫用例。

背景：原先联动只在 publish / restore 两处各算一次，`update_notice` 改
`notice_type` / `effective_at` / `document_ids` 都不重算，实测出四种不一致；
取消时的回滚门禁又依赖**当前** `notice_type`，类型一改就永久失去回滚能力。
详见 `docs/变更确认单-通知类型变更联动-2026-09-29.md`。

本文件每条用例对应一个实测过的探针或一条安全护栏，断言都写成"可复现的最小场景"：

| 用例 | 对应 |
|---|---|
| T1 改生效日要跟着走            | 探针 1 |
| T2 新增关联资料要被截断        | 探针 2 |
| T3 移除关联资料要回滚          | 探针 3 |
| T4 类型不再是废止/替换要回滚   | 探针 4（原缺陷） |
| T5 取消仍能正常回滚（基线）    | 对照 |
| T6 不覆盖被独立修改过的资料    | 护栏 A（墓碑规则） |
| T7 墓碑要挡住反复认领          | 护栏 A 加强版 |
| T8 R1 冲突 → 整个 update 回滚  | 护栏 B |
| T9 取消后恢复要重新截断        | restore 路径 |
| T10「替换」类型同样走联动      | AUTO_EXPIRE_TYPES 另一成员 |
| T11 草稿编辑不产生任何截断     | 不该误伤的路径 |
"""
from __future__ import annotations

from helpers import create_document, create_notice

D1 = "2026-05-10T10:00:00"   # 首次发布的生效日
D2 = "2026-11-30T10:00:00"   # 改后的生效日
TRUNC_1 = "2026-05-10"
TRUNC_2 = "2026-11-30"


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _mkdoc(client, headers, title, effective_to=None):
    return create_document(
        client, headers, title=title, effective_from="2026-01-01",
        effective_to=effective_to,
    )["id"]


def _eto(client, headers, did):
    resp = client.get(f"/api/v1/documents/{did}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["effective_to"]


_READER = {"username": "linkage_reader", "password": "user1234"}


def _ensure_reader(client, headers) -> None:
    """确保存在一个普通用户作为真实读者。

    2026-11-03 起管理员不进阅读对象，「全体」范围若只剩管理员会导致
    发布被拒（阅读对象为空）。本文件的用例只关心时效联动，不关心
    谁在读，因此统一补一个读者。
    """
    client.post("/api/v1/users", headers=headers, json={
        "username": _READER["username"], "password": _READER["password"],
        "display_name": "联动读者", "role": "user",
    })


def _mkpub(client, headers, title, docs, notice_type="废止", effective_at=D1):
    """创建即发布（2026-11-04：POST /notices 单事务完成，无单独发布步骤）。"""
    _ensure_reader(client, headers)
    notice = create_notice(
        client, headers, title=title, content="联动重算用例", notice_type=notice_type,
        effective_at=effective_at, document_ids=docs, target={"scope": "all"},
    )
    return notice["id"]


def _put_notice(client, headers, nid, **kw):
    """改 published 状态通知（带上 §22 要求的确认与原因）。"""
    payload = dict(kw)
    payload.setdefault("confirm_modify", True)
    payload.setdefault("modified_reason", "守卫用例：联动重算")
    return client.put(f"/api/v1/notices/{nid}", headers=headers, json=payload)


def _cancel(client, headers, nid):
    return client.post(f"/api/v1/notices/{nid}/cancel", headers=headers, json={"reason": "用例"})


# --------------------------------------------------------------------------
# T1–T5：四个探针 + 基线对照
# --------------------------------------------------------------------------
class TestReconcileOnUpdate:
    def test_T1_update_effective_at_realigns_linked_document(self, client, admin_headers):
        """探针 1：改published 状态废止通知的生效日 → 资料的失效日跟着走。

        修复前实测：通知改成 2026-11-30，资料仍停在 2026-05-10。
        """
        did = _mkdoc(client, admin_headers, "T1-资料")
        nid = _mkpub(client, admin_headers, "T1-废止", [did])
        assert _eto(client, admin_headers, did) == TRUNC_1

        resp = _put_notice(client, admin_headers, nid, effective_at=D2)
        assert resp.status_code == 200, resp.text
        assert _eto(client, admin_headers, did) == TRUNC_2

    def test_T2_adding_linked_document_truncates_it(self, client, admin_headers):
        """探针 2：给published 状态废止通知**新增**关联资料 → 新资料被截断。

        修复前实测：新资料 effective_to 保持 None（通知自称废止、资料却有效）。
        """
        d_old = _mkdoc(client, admin_headers, "T2-原资料")
        d_new = _mkdoc(client, admin_headers, "T2-后加资料")
        nid = _mkpub(client, admin_headers, "T2-废止", [d_old])
        assert _eto(client, admin_headers, d_new) is None

        resp = _put_notice(client, admin_headers, nid, document_ids=[d_old, d_new])
        assert resp.status_code == 200, resp.text
        assert _eto(client, admin_headers, d_new) == TRUNC_1
        assert _eto(client, admin_headers, d_old) == TRUNC_1  # 原有那条不受影响

    def test_T3_removing_linked_document_rolls_it_back(self, client, admin_headers):
        """探针 3：从published 状态废止通知**移除**关联资料 → 该资料回滚。

        修复前实测：被移除的资料永久停在废止日（取消时只遍历当前关联集合，永远轮不到它）。
        """
        did = _mkdoc(client, admin_headers, "T3-资料")
        nid = _mkpub(client, admin_headers, "T3-废止", [did])
        assert _eto(client, admin_headers, did) == TRUNC_1

        resp = _put_notice(client, admin_headers, nid, document_ids=[])
        assert resp.status_code == 200, resp.text
        assert _eto(client, admin_headers, did) is None

    def test_T4_type_no_longer_abolish_rolls_back(self, client, admin_headers):
        """探针 4：把published 状态「废止」通知改成「发布」→ 截断立即回滚。

        修复前实测：截断留着，且**取消也不回滚**（取消门禁依赖当前 notice_type）。
        修复后该矛盾状态已不可能存在，所以直接断言"改完即回滚"。
        """
        did = _mkdoc(client, admin_headers, "T4-资料")
        nid = _mkpub(client, admin_headers, "T4-废止", [did])
        assert _eto(client, admin_headers, did) == TRUNC_1

        resp = _put_notice(client, admin_headers, nid, notice_type="发布")
        assert resp.status_code == 200, resp.text
        assert _eto(client, admin_headers, did) is None

        # 再改回废止 → 重新截断（反方向也要成立）
        assert _put_notice(client, admin_headers, nid, notice_type="废止").status_code == 200
        assert _eto(client, admin_headers, did) == TRUNC_1

    def test_T5_cancel_still_rolls_back_baseline(self, client, admin_headers):
        """基线对照：什么都不改，直接取消 → 正常回滚（重构不得破坏原有链路）。"""
        did = _mkdoc(client, admin_headers, "T5-资料")
        nid = _mkpub(client, admin_headers, "T5-废止", [did])
        assert _eto(client, admin_headers, did) == TRUNC_1

        assert _cancel(client, admin_headers, nid).status_code == 200
        assert _eto(client, admin_headers, did) is None


# --------------------------------------------------------------------------
# T6–T8：安全护栏
# --------------------------------------------------------------------------
class TestReconcileGuards:
    def test_T6_does_not_clobber_independently_edited_document(
        self, client, admin_headers
    ):
        """护栏 A：资料被管理员独立改过 → 重算不得覆盖它（墓碑规则）。

        开发中实测过一次反例：释放之后又落进"首次关联"分支被重新认领，
        把管理员独立设的 2027-06-30 冲成了通知的生效日。这条用例就是为了钉住它。
        """
        did = _mkdoc(client, admin_headers, "T6-资料")
        nid = _mkpub(client, admin_headers, "T6-废止", [did])
        assert _eto(client, admin_headers, did) == TRUNC_1

        # 管理员独立把失效日改成别的值（资料编辑页会走的路径）
        resp = client.put(
            f"/api/v1/documents/{did}", headers=admin_headers,
            json={"effective_to": "2027-06-30", "confirm_overwrite": True,
                  "modify_reason": "管理员独立调整"},
        )
        assert resp.status_code == 200, resp.text
        assert _eto(client, admin_headers, did) == "2027-06-30"

        # 改通知生效日 → 不该覆盖
        assert _put_notice(client, admin_headers, nid, effective_at=D2).status_code == 200
        assert _eto(client, admin_headers, did) == "2027-06-30"

        # 取消 → 也不该覆盖
        assert _cancel(client, admin_headers, nid).status_code == 200
        assert _eto(client, admin_headers, did) == "2027-06-30"

    def test_T7_tombstone_survives_repeated_notice_edits(self, client, admin_headers):
        """护栏 A 加强版：连续多次编辑通知，仍不得重新认领被独立改过的资料。"""
        did = _mkdoc(client, admin_headers, "T7-资料")
        nid = _mkpub(client, admin_headers, "T7-废止", [did])
        client.put(
            f"/api/v1/documents/{did}", headers=admin_headers,
            json={"effective_to": "2027-06-30", "confirm_overwrite": True,
                  "modify_reason": "独立调整"},
        )

        for eff in ("2026-10-10T09:00:00", "2026-11-11T09:00:00", "2026-12-12T09:00:00"):
            assert _put_notice(client, admin_headers, nid, effective_at=eff).status_code == 200
            assert _eto(client, admin_headers, did) == "2027-06-30"

        # 连类型改来改去也不该动它
        assert _put_notice(client, admin_headers, nid, notice_type="发布").status_code == 200
        assert _eto(client, admin_headers, did) == "2027-06-30"
        assert _put_notice(client, admin_headers, nid, notice_type="废止").status_code == 200
        assert _eto(client, admin_headers, did) == "2027-06-30"

    def test_T8_r1_conflict_rolls_back_whole_update(self, client, admin_headers):
        """护栏 B：生效日改到资料生效日之前 → R1 冲突，**整个 update 回滚**。

        不允许出现"通知改了一半、资料也改了一半"的状态。
        """
        did = _mkdoc(client, admin_headers, "T8-资料", effective_to="2026-12-31")
        nid = _mkpub(client, admin_headers, "T8-废止", [did])
        assert _eto(client, admin_headers, did) == TRUNC_1

        resp = _put_notice(client, admin_headers, nid, effective_at="2025-12-01T09:00:00")
        assert resp.status_code == 409, resp.text
        assert resp.json().get("error", {}).get("code") == "VALIDITY_CONFLICT"

        detail = client.get(f"/api/v1/notices/{nid}", headers=admin_headers).json()
        assert detail["effective_at"] != "2025-12-01T09:00:00", "通知被改了一半"
        assert _eto(client, admin_headers, did) == TRUNC_1, "资料被改了一半"


# --------------------------------------------------------------------------
# T9–T11：恢复路径、另一联动类型、不该误伤的路径
# --------------------------------------------------------------------------
class TestReconcileOtherPaths:
    def test_T9_restore_reapplies_after_cancel(self, client, admin_headers):
        """取消 → 恢复：应重新截断（与发布语义对称，防"通知已恢复但资料仍有效"）。"""
        did = _mkdoc(client, admin_headers, "T9-资料")
        nid = _mkpub(client, admin_headers, "T9-废止", [did])
        assert _cancel(client, admin_headers, nid).status_code == 200
        assert _eto(client, admin_headers, did) is None

        resp = client.post(f"/api/v1/recycle-bin/notices/{nid}/restore", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert _eto(client, admin_headers, did) == TRUNC_1

    def test_T10_replace_type_also_reconciles(self, client, admin_headers):
        """「替换」与「废止」同属 AUTO_EXPIRE_TYPES，联动必须一致。"""
        did = _mkdoc(client, admin_headers, "T10-资料")
        nid = _mkpub(client, admin_headers, "T10-替换", [did], notice_type="替换")
        assert _eto(client, admin_headers, did) == TRUNC_1

        assert _put_notice(client, admin_headers, nid, effective_at=D2).status_code == 200
        assert _eto(client, admin_headers, did) == TRUNC_2

        assert _cancel(client, admin_headers, nid).status_code == 200
        assert _eto(client, admin_headers, did) is None

    def test_T11_edit_realigns_and_cancel_rolls_back(self, client, admin_headers):
        """改动生效日会重新对齐截断；取消通知则把资料还原。

        2026-11-04：草稿下线后不存在「草稿阶段不该截断」的场景，
        改为验证两个仍成立的方向：改日期要重算、取消要回滚。
        """
        _ensure_reader(client, admin_headers)
        did = _mkdoc(client, admin_headers, "T11-资料")
        notice = create_notice(
            client, admin_headers, title="T11-废止", content="x", notice_type="废止",
            effective_at=D1, document_ids=[did], target={"scope": "all"},
        )
        nid = notice["id"]
        assert _eto(client, admin_headers, did) == D1[:10], "创建即截断"

        for eff in (D2, "2026-08-08T09:00:00"):
            resp = client.put(
                f"/api/v1/notices/{nid}", headers=admin_headers,
                json={"effective_at": eff, "confirm_modify": True,
                      "modified_reason": "用例：调整生效日"},
            )
            assert resp.status_code == 200, resp.text
            assert _eto(client, admin_headers, did) == eff[:10], "改生效日应重新对齐"

        assert client.post(
            f"/api/v1/notices/{nid}/cancel", headers=admin_headers, json={}
        ).status_code == 200
        assert _eto(client, admin_headers, did) is None, "取消后必须回滚截断"
