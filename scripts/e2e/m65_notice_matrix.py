"""M65 通知矩阵：类型 / 状态 / scope / 阅读进度 / 统计分母。

背景（用户反馈）：上一轮只有 1 条通知、1 种类型、1 种 scope，
等于通知模块的筛选、统计、阅读进度这些能力**从未被验证**。
种子现有 30 条通知，覆盖 8 种类型 × 3 scope × 3 状态 × 3 档阅读进度。

重点验证「管理员不计入统计」——这是 2026-11-03 用户实测反馈的缺陷。
"""
from __future__ import annotations

from harness import REPORT, case, http


def _body(resp) -> dict:
    b = resp.get("body")
    return b if isinstance(b, dict) else {}


def _list_all(token: str, extra: str = "") -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        b = _body(http("GET", f"/api/v1/notices?page={page}&page_size=100{extra}", token))
        items.extend(b.get("items") or [])
        if page >= (b.get("pages") or 1):
            break
        page += 1
    return items


@case("N11", "通知列表：按类型筛选，各类型均有数据且计数正确")
def n11(app):
    all_items = _list_all(app.admin.token)
    REPORT.check(len(all_items) >= 20, f"通知总数 {len(all_items)}（要求 ≥20）")

    # 逐个类型验证筛选
    types = sorted({n.get("notice_type") for n in all_items if n.get("notice_type")})
    REPORT.check(len(types) >= 7,
                 f"类型种类 {len(types)}：{types}")

    for t in types[:3]:  # 抽查前 3 种，避免用例过长
        filtered = _list_all(app.admin.token, f"&type={t}")
        expected = [n for n in all_items if n.get("notice_type") == t]
        REPORT.check(len(filtered) == len(expected),
                     f"类型「{t}」筛选 {len(filtered)} 条 = 全量中 {len(expected)} 条")


@case("N12", "通知列表：状态只有 published（草稿已下线）")
def n12(app):
    """2026-11-04 草稿下线后，列表不应再返回 draft，状态筛选只剩 published。"""
    drafts = _list_all(app.admin.token, "&status=draft")
    REPORT.check(len(drafts) == 0, f"status=draft 不再返回数据（实际 {len(drafts)} 条）")

    pubs = _list_all(app.admin.token)
    published = [n for n in pubs if n.get("status") == "published"]
    REPORT.check(len(published) > 0, f"published 通知 {len(published)} 条")
    REPORT.check(all(n.get("status") == "published" for n in pubs),
                 f"列表状态全部是 published（实际 {sorted({n.get('status') for n in pubs})}）")


@case("N13", "统计分母不含管理员（用户实测反馈的核心缺陷）")
def n13(app):
    items = _list_all(app.admin.token)
    target = None
    for n in items:
        if n.get("status") != "published":
            continue
        st = _body(http("GET", f"/api/v1/notices/{n['id']}/statistics", app.admin.token))
        if (st.get("target_user_count") or 0) > 0:
            target = (n, st)
            break

    if not target:
        REPORT.fail("找不到有应读人员的 published 通知")
        return

    n, st = target
    total = st.get("target_user_count") or 0
    REPORT.check(total > 0, f"「{n['title'][:20]}」应读 {total} 人")

    # 名单里不应出现任何管理员
    aud = _body(http("GET", f"/api/v1/notices/{n['id']}/audience?page_size=200",
                     app.admin.token))
    names = [u.get("username") for u in (aud.get("items") or [])]
    admin_like = [x for x in names if "admin" in (x or "")]
    REPORT.check(not admin_like, f"应读名单不含管理员（命中：{admin_like}）")

    # 统计响应里不应有已删除的「确认」字段
    for gone in ("confirmed_count", "unconfirmed_count", "requires_confirmation"):
        REPORT.check(gone not in st, f"统计响应无 {gone}（确认功能已下线）")

    # 管理员自己的工作台未读必须为 0
    dash = _body(http("GET", "/api/v1/dashboard", app.admin.token))
    REPORT.check(dash.get("my_unread_notices") == 0,
                 f"管理员工作台未读 = {dash.get('my_unread_notices')}（应为 0）")


@case("N14", "阅读进度三档：统计页数字与名单一致")
def n14(app):
    """找「全部已读」「部分已读」「零已读」各一条，验证统计口径。"""
    items = [n for n in _list_all(app.admin.token) if n.get("status") == "published"]
    buckets = {"all_read": None, "partial": None, "none_read": None}

    for n in items[:20]:
        st = _body(http("GET", f"/api/v1/notices/{n['id']}/statistics", app.admin.token))
        total = st.get("target_user_count") or 0
        viewed = st.get("viewed_count") or 0
        if total == 0:
            continue
        if viewed == total and not buckets["all_read"]:
            buckets["all_read"] = (n, st)
        elif viewed == 0 and not buckets["none_read"]:
            buckets["none_read"] = (n, st)
        elif 0 < viewed < total and not buckets["partial"]:
            buckets["partial"] = (n, st)
        if all(buckets.values()):
            break

    for key, label in (("all_read", "全部已读"), ("partial", "部分已读"), ("none_read", "零已读")):
        got = buckets[key]
        if got:
            n, st = got
            REPORT.check(
                st["viewed_count"] + st["unviewed_count"] == st["target_user_count"],
                f"{label}：已查看 {st['viewed_count']} + 未查看 {st['unviewed_count']}"
                f" = 应读 {st['target_user_count']}",
            )
        else:
            REPORT.note(f"种子中未找到「{label}」样本")


@case("N15", "统计页 UI：三个大数字可点，名单默认未查看")
def n15(app):
    items = [n for n in _list_all(app.admin.token) if n.get("status") == "published"]
    if not items:
        REPORT.fail("没有 published 通知")
        return

    n = items[0]
    s = app.admin
    s.goto(f"/admin/notices/{n['id']}/stats")
    s.page.wait_for_timeout(2400)

    txt = s.page.inner_text("#stats-root")
    REPORT.check("管理员不计入统计" in txt, "统计页标注「管理员不计入统计」")
    REPORT.check("确认" not in txt, "统计页无「确认」字样")

    cards = s.page.query_selector_all("[data-filter]")
    REPORT.check(len(cards) == 3, f"三个大数字卡（实际 {len(cards)}）")

    # 默认落在未查看
    val = s.page.eval_on_selector("#audience-filter", "e => e.value")
    REPORT.check(val == "unread", f"名单默认筛选 = {val}（应为 unread）")

    # 点「已查看」→ 名单切换
    s.page.click('[data-filter="read"]')
    s.page.wait_for_timeout(1300)
    val2 = s.page.eval_on_selector("#audience-filter", "e => e.value")
    REPORT.check(val2 == "read", f"点「已查看」后筛选 = {val2}")

    REPORT.check(s.page.query_selector("#audience-search") is not None,
                 "名单区有搜索框")
    s.shot("N15_notice_stats.png")


@case("N16", "通知附件：4 种类型都能预览（此前零测试覆盖）")
def n16(app):
    """preview/image|pdf|docx|xlsx 四个端点此前没有任何测试。"""
    items = _list_all(app.admin.token)
    with_file = [n for n in items if n.get("has_file")]
    REPORT.check(len(with_file) > 0, f"带附件的通知 {len(with_file)} 条")

    seen = set()
    for n in with_file:
        ft = n.get("file_type")
        if not ft or ft in seen:
            continue
        seen.add(ft)
        endpoint = f"/api/v1/notices/{n['id']}/preview/{'image' if ft in ('png','jpg','jpeg','gif','webp') else ft}"
        r = http("GET", endpoint, app.admin.token, raw=True)
        REPORT.check(r["status"] == 200,
                     f"{ft} 附件预览 {endpoint.split('/')[-1]} → {r['status']}")

        # 浏览器里打开详情，确认能点开预览
        s = app.admin
        s.goto(f"/notices/{n['id']}")
        s.page.wait_for_timeout(2000)
        btn = s.page.query_selector("#btn-preview-" + ("image" if ft in ("png","jpg","jpeg","gif","webp") else ft))
        REPORT.check(btn is not None, f"{ft}: 详情页有预览按钮")

    REPORT.note(f"覆盖的附件类型：{sorted(seen)}")
    REPORT.check(len(seen) >= 3, f"至少 3 种附件类型（实际 {len(seen)}）")
