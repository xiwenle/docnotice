"""M21 通知阅读闭环：打开即记 read_at；统计与名单口径一致（§20、§21）。

2026-11-03 产品决策：确认功能整体下线，只保留「已查看 / 未查看」。
原 R02/R04/R05（确认按钮、行内确认、待确认 tab）已随通知确认功能正式下线移除，
替换为「确认接口已不存在」的反向守卫 + 新版统计页语义。
"""
from __future__ import annotations

import time

from harness import NORMAL, REPORT, SHOT_DIR, case, http


def _open(app, nid, role="user"):
    s = app.user if role == "user" else app.admin
    s.goto(f"/notices/{nid}")
    s.page.wait_for_selector("#detail-root", timeout=15000)
    s.page.wait_for_timeout(1500)
    return s


@case("R01", "普通用户打开通知详情 → 自动记录查看时间（read_at）")
def r01(app):
    nid = app.state.get("notice_published_id")
    if not nid:
        REPORT.fail("无 published 状态通知（依赖 N01）")
        return
    s = _open(app, nid)
    detail = http("GET", f"/api/v1/notices/{nid}", app.user.token)["body"] or {}
    REPORT.check(bool(detail.get("my_read_at")),
                 f"普通用户查看状态字段：my_read_at={detail.get('my_read_at')}")
    REPORT.check(detail.get("my_read_status") == "read",
                 f"普通用户阅读状态为已查看：{detail.get('my_read_status')}")
    txt = s.page.inner_text("body")
    REPORT.check("已查看" in txt or "查看" in txt,
                 f"页面呈现查看状态：{[k for k in ('已查看','未查看') if k in txt]}")
    REPORT.check("确认" not in txt, "通知确认正式下线：详情页不再出现「确认」字样")
    s.page.screenshot(path=str(SHOT_DIR / "R01_notice_read.png"))


@case("R02", "确认接口已下线（反向守卫）")
def r02(app):
    nid = app.state.get("notice_published_id")
    if not nid:
        REPORT.fail("无 published 状态通知（依赖 N01）")
        return
    r = http("POST", f"/api/v1/notices/{nid}/confirm", app.user.token, {"confirm": True})
    REPORT.check(r["status"] in (404, 405),
                 f"POST /notices/{{id}}/confirm 应 404/405，实际 {r['status']}")
    s = _open(app, nid)
    REPORT.check(s.page.query_selector("#btn-confirm") is None, "详情页无确认按钮")


@case("R03", "查看与统计联动：看过的人从「未查看」名单消失")
def r03(app):
    # 指定用户受众（仅发给当前普通用户）
    r = http("GET", "/api/v1/users?keyword=" + NORMAL["username"] + "&page=1&page_size=100",
             app.admin.token)["body"] or {}
    target_uid = next(
        (u.get("id") for u in (r.get("items") or [])
         if u.get("username") == NORMAL["username"]), None)
    if not target_uid:
        REPORT.fail("未找到普通用户 id")
        return
    nid_r = http("POST", "/api/v1/notices", app.admin.token, {
        "title": f"E2E查看联动{int(time.time())}",
        "content": "验证查看后从未查看名单消失",
        "target": {"scope": "user", "user_ids": [target_uid]},
    })
    if nid_r["status"] not in (200, 201):
        REPORT.fail(f"创建通知失败：{nid_r['status']} {str(nid_r['body'])[:120]}")
        return
    nid = (nid_r["body"] or {}).get("id")
    # 2026-11-04 起创建即发布，无需（也没有）单独的 publish 调用。

    before = http("GET", f"/api/v1/notices/{nid}/audience?read_status=unread",
                  app.admin.token)["body"] or {}
    REPORT.check((before.get("total") or 0) == 1, f"查看前未查看名单 1 人：{before.get('total')}")

    _open(app, nid)  # 打开即置读
    after = http("GET", f"/api/v1/notices/{nid}/audience?read_status=unread",
                 app.admin.token)["body"] or {}
    REPORT.check((after.get("total") or 0) == 0, f"查看后未查看名单清空：{after.get('total')}")
    app.state["notice_viewonly_id"] = nid


@case("R04", "列表未读 tab：查看后该通知从未读列表移除")
def r04(app):
    nid = app.state.get("notice_viewonly_id")
    if not nid:
        REPORT.fail("无通知（依赖 R03）")
        return
    s = app.user
    s.goto("/notices")
    s.page.wait_for_selector("#notice-list > *", timeout=12000)
    s.page.click("#tab-unread")
    s.page.wait_for_timeout(1500)
    # 纯文本里可能因为时间/分页等数字片段误命中 nid；按 DOM data-id 精确判断。
    remains = s.page.locator(f'.notice-item[data-id="{nid}"]').count()
    REPORT.check(remains == 0,
                 f"未读列表已不含该通知（DOM 残留 {remains} 条）")
    REPORT.check(s.page.query_selector("#tab-confirm") is None,
                 "通知确认正式下线：「待确认」tab 已移除")
    s.page.screenshot(path=str(SHOT_DIR / "R04_unread_tab.png"))


@case("R05", "通知详情侧栏：只有查看时间，无确认时间/阅读要求")
def r05(app):
    nid = app.state.get("notice_published_id")
    if not nid:
        REPORT.fail("无 published 状态通知（依赖 N01）")
        return
    s = _open(app, nid, role="admin")
    txt = s.page.inner_text("body")
    REPORT.check("查看时间" in txt, "侧栏保留「查看时间」")
    REPORT.check("确认时间" not in txt, "侧栏不再显示「确认时间」")
    REPORT.check("阅读要求" not in txt, "侧栏不再显示「阅读要求」")


@case("R06", "阅读统计页：应读/已查看/未查看口径正确，且管理员不计入（§21）")
def r06(app):
    nid = app.state.get("notice_published_id")
    if not nid:
        REPORT.fail("无 published 状态通知（依赖 N01）")
        return
    s = app.admin
    s.goto(f"/admin/notices/{nid}/stats")
    s.page.wait_for_selector("#stats-root", timeout=15000)
    s.page.wait_for_timeout(1800)
    txt = s.page.inner_text("#stats-root")
    for kw in ("未查看", "已查看", "应阅读"):
        REPORT.check(kw in txt, f"统计页包含「{kw}」")
    REPORT.check("确认" not in txt, "统计页不再出现「确认」")
    REPORT.check("管理员不计入统计" in txt, "统计页标注管理员不计入")

    cards = s.page.query_selector_all("[data-filter]")
    REPORT.check(len(cards) == 3, f"三个大数字卡可点击筛选：{len(cards)}")
    REPORT.check(s.page.query_selector("#audience-search") is not None, "名单区有搜索框")

    # 点「未查看」卡 → 名单切到未查看
    s.page.click('[data-filter="unread"]')
    s.page.wait_for_timeout(1400)
    REPORT.check(
        s.page.eval_on_selector("#audience-filter", "e => e.value") == "unread",
        "点击大数字后名单筛选同步为未查看")

    stats = http("GET", f"/api/v1/notices/{nid}/statistics", app.admin.token)["body"] or {}
    total = stats.get("target_user_count") or 0
    read = stats.get("viewed_count") or 0
    unread = stats.get("unviewed_count")
    REPORT.check(unread is not None and read + unread == total,
                 f"已查看 + 未查看 = 应读：{read} + {unread} = {total}")
    for gone in ("requires_confirmation", "confirmed_count", "unconfirmed_count"):
        REPORT.check(gone not in stats, f"统计响应已无 {gone}")
    s.page.screenshot(path=str(SHOT_DIR / "R06_stats.png"))
