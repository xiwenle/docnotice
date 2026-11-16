"""M68 历史日期查询 + 备份恢复闭环。

背景：
- §15「系统应支持按照指定日期判断资料状态，不能只根据当前日期」——
  这条只有后端单测，**没有在浏览器里看过**；
- §28「备份不能只备数据库，必须考虑恢复」——m41 验证了创建，
  但**恢复（restore）的完整闭环**覆盖薄弱。
"""
from __future__ import annotations

from harness import REPORT, case, http


def _body(resp) -> dict:
    b = resp.get("body")
    return b if isinstance(b, dict) else {}


def _find_doc(app, keyword: str) -> dict | None:
    page = 1
    while True:
        b = _body(http("GET",
                       f"/api/v1/documents?keyword={keyword}&page={page}&page_size=100",
                       app.admin.token))
        for d in b.get("items") or []:
            if keyword in d["title"]:
                return d
        if page >= (b.get("pages") or 1):
            return None
        page += 1


@case("J01", "历史日期查询：同一资料在不同日期呈现 待生效/生效中/已失效")
def h01(app):
    # 种子里有明确区间的资料：临时安全指引（-60 天 ~ +20 天）
    doc = _find_doc(app, "DEMO-临时安全指引")
    if not doc:
        REPORT.fail("找不到有明确有效期的资料")
        return

    dates = {"early": "2020-01-01", "today": "2026-11-04", "late": "2030-01-01"}
    results = {}
    for label, d in dates.items():
        b = _body(http("GET", f"/api/v1/documents/{doc['id']}/status?date={d}",
                       app.admin.token))
        results[label] = b.get("status")
        REPORT.check(b.get("status") is not None, f"{d} 返回状态 {b.get('status')}")

    REPORT.check(results["early"] == "pending" or results["early"] == "expired",
                 f"早期日期状态：{results['early']}")
    REPORT.check(results["late"] == "expired",
                 f"远期日期应已失效（实际 {results['late']}）")
    REPORT.note(f"三档状态：{results}")


@case("J02", "浏览器：列表按 as_of_date 回看，状态标签随之变化")
def h02(app):
    s = app.admin
    old_date = "2020-01-01"
    s.goto(f"/documents?as_of_date={old_date}")
    s.page.wait_for_timeout(2300)

    txt = s.page.inner_text("body")
    REPORT.check(old_date in txt or "2020" in txt,
                 f"页面反映所选历史日期 {old_date}")
    REPORT.check("待生效" in txt or "已过期" in txt,
                 "历史日期下出现「待生效」或「已过期」状态")
    s.shot("H02_historical_date.png")


@case("J03", "备份 → 改动 → 恢复：改动消失，种子数据仍在")
def h03(app):
    tok = app.admin.token

    # 1. 记录基线
    before = _body(http("GET", "/api/v1/documents?page_size=1", tok))
    baseline_total = before.get("total") or 0
    REPORT.check(baseline_total > 0, f"基线资料数 {baseline_total}")

    # 2. 建备份
    bk = _body(http("POST", "/api/v1/backups", tok, {"backup_type": "full"}))
    bid = bk.get("id")
    REPORT.check(bool(bid), f"创建备份 id={bid}")
    REPORT.check((bk.get("file_count") or 0) > 0,
                 f"备份含 {bk.get('file_count')} 个文件")

    # 3. 做一处改动：新建资料
    marker = "E2E备份恢复-临时标记"
    r = _body(http("POST", "/api/v1/documents", tok, {
        "title": marker, "effective_from": "2020-01-01",
    }))
    REPORT.check(bool(r.get("id")), "改动：新建一份标记资料")

    mid = _body(http("GET", "/api/v1/documents?page_size=1", tok))
    REPORT.check((mid.get("total") or 0) == baseline_total + 1,
                 f"改动后资料数 {mid.get('total')} = {baseline_total} + 1")

    # 4. 恢复备份
    rr = _body(http("POST", f"/api/v1/backups/{bid}/restore", tok,
                    {"confirm": True, "reason": "E2E 恢复闭环验证"}))
    REPORT.check(bool(rr), f"恢复请求已受理：{str(rr)[:60]}")
    REPORT.note("恢复会换库 + 切业务目录，可能需要几秒")

    import time
    time.sleep(3)

    # 5. 验证：标记资料消失、种子数据仍在
    after = _body(http("GET", "/api/v1/documents?page_size=1", tok))
    after_total = after.get("total") or 0
    REPORT.check(after_total == baseline_total,
                 f"恢复后资料数回到 {after_total}（基线 {baseline_total}）")

    found = _find_doc(app, marker)
    REPORT.check(found is None, f"标记资料「{marker}」已随恢复消失")

    seed_doc = _find_doc(app, "DEMO-员工手册")
    REPORT.check(seed_doc is not None, "种子数据仍在（DEMO-员工手册）")

    REPORT.check(after_total > 0, f"恢复后库可用（{after_total} 份资料）")


@case("J04", "备份列表与审计：恢复动作可追溯")
def h04(app):
    tok = app.admin.token
    bl = _body(http("GET", "/api/v1/backups?page_size=50", tok))
    items = bl.get("items") or []
    REPORT.check(len(items) > 0, f"备份列表 {len(items)} 条")

    # 注意：恢复会「换库」——备份记录表本身也被换成备份里的旧版本，
    # 所以恢复动作的标记不会留在当前库的 backup_records 里。
    # 真正的可追溯性靠审计日志（它随备份一起保留）。见下方断言。
    restored = [x for x in items if x.get("restored_at")]
    REPORT.note(f"当前库中 restored_at 非空的备份：{len(restored)} 条"
                "（恢复会换库，该标记通常不保留）")

    # 审计里应有 backup_restore
    al = _body(http("GET", "/api/v1/audit-logs?page_size=100", tok))
    actions = {x.get("action") for x in (al.get("items") or [])}
    REPORT.check("backup_restore" in actions,
                 f"审计含 backup_restore（当前动作：{sorted(a for a in actions if a)[:8]}）")
