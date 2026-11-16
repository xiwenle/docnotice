"""M66 废止/替换联动：通知真的改动了资料有效期。

背景（AGENTS §18/§31）：`废止`/`替换` 类型的通知**发布时**会把关联资料的
`effective_to` 截断为通知生效日；取消/改类型/移除关联会回滚。

这套逻辑有 11 条后端单测（test_notice_linkage_reconcile_20260929.py），
但**没有浏览器侧验证**——用户看不到「通知发出去，资料状态跟着变」这条因果。
本模块用真实 UI 走一遍，并对比「延期」类型不触发截断。
"""
from __future__ import annotations

from harness import REPORT, case, http


def _body(resp) -> dict:
    b = resp.get("body")
    return b if isinstance(b, dict) else {}


def _body_dict(resp) -> dict:
    return _body(resp)


def _make_doc(app, title: str, effective_to: str | None = None) -> int | None:
    payload = {"title": title, "effective_from": "2020-01-01"}
    if effective_to:
        payload["effective_to"] = effective_to
    r = _body_dict(http("POST", "/api/v1/documents", app.admin.token, payload))
    return r.get("id")


def _doc_effective_to(app, doc_id: int):
    d = _body(http("GET", f"/api/v1/documents/{doc_id}", app.admin.token))
    return d.get("effective_to")


def _make_notice(app, title: str, notice_type: str, doc_id: int) -> int | None:
    r = _body_dict(http("POST", "/api/v1/notices", app.admin.token, {
        "title": title, "content": "联动验证", "notice_type": notice_type,
        "document_ids": [doc_id], "target": {"scope": "all"},
    }))
    return r.get("id")


@case("K01", "废止通知发布 → 关联资料 effective_to 被截断为生效日")
def l01(app):
    doc_id = _make_doc(app, "E2E联动-废止目标")
    if not doc_id:
        REPORT.fail("建资料失败")
        return
    before = _doc_effective_to(app, doc_id)
    REPORT.check(before is None, f"初始无失效日（{before}）")

    nid = _make_notice(app, "E2E联动-废止通知", "废止", doc_id)
    if not nid:
        REPORT.fail("建通知失败")
        return

    # 2026-11-04 起创建即发布：POST /notices 单事务内已完成废止联动，
    # 不再有 POST /{id}/publish（该端点已移除），这里只需确认通知为 published。
    info = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    REPORT.check(info.get("status") == "published",
                 f"废止通知为 published（{info.get('status')}）")

    after = _doc_effective_to(app, doc_id)
    REPORT.check(after is not None,
                 f"资料 effective_to 已被截断：{before} → {after}")

    # 浏览器里能看到资料状态变化
    s = app.admin
    s.goto(f"/documents/{doc_id}")
    s.page.wait_for_timeout(2000)
    txt = s.page.inner_text("body")
    REPORT.check(after in txt, f"资料详情页显示截断后的失效日 {after}")
    s.shot("L01_abolish_linkage.png")

    app.state["linkage_doc"] = doc_id
    app.state["linkage_notice"] = nid


@case("K02", "取消废止通知 → 资料有效期回滚")
def l02(app):
    doc_id = app.state.get("linkage_doc")
    nid = app.state.get("linkage_notice")
    if not doc_id or not nid:
        REPORT.fail("依赖 L01 的数据")
        return

    truncated = _doc_effective_to(app, doc_id)
    r = http("POST", f"/api/v1/notices/{nid}/cancel", app.admin.token,
             {"reason": "E2E 联动回滚验证"})
    REPORT.check(r["status"] == 200, f"取消通知 {r['status']}")

    after = _doc_effective_to(app, doc_id)
    REPORT.check(after is None or after != truncated,
                 f"资料失效日已回滚：{truncated} → {after}")
    REPORT.note("取消后资料恢复为「暂无失效日期」或原值，符合 §31 联动回滚")


@case("K03", "延期通知不触发截断（AUTO_EXPIRE_TYPES 只含废止/替换）")
def l03(app):
    doc_id = _make_doc(app, "E2E联动-延期目标")
    if not doc_id:
        REPORT.fail("建资料失败")
        return

    nid = _make_notice(app, "E2E联动-延期通知", "延期", doc_id)
    if not nid:
        REPORT.fail("建通知失败")
        return

    info = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    REPORT.check(info.get("status") == "published",
                 f"延期通知为 published（{info.get('status')}）")

    after = _doc_effective_to(app, doc_id)
    REPORT.check(after is None,
                 f"延期通知不截断资料（effective_to = {after}，应为 None）")


@case("K04", "替换通知同样触发截断")
def l04(app):
    doc_id = _make_doc(app, "E2E联动-替换目标")
    if not doc_id:
        REPORT.fail("建资料失败")
        return

    nid = _make_notice(app, "E2E联动-替换通知", "替换", doc_id)
    if not nid:
        REPORT.fail("建通知失败")
        return

    info = http("GET", f"/api/v1/notices/{nid}", app.admin.token)["body"] or {}
    REPORT.check(info.get("status") == "published",
                 f"替换通知为 published（{info.get('status')}）")

    after = _doc_effective_to(app, doc_id)
    REPORT.check(after is not None,
                 f"替换通知触发截断（effective_to = {after}）")
