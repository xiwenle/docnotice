"""M63 资料可见范围交叉验证（三种 scope × 四个角色）。

背景（2026-11-03 产品决策 + 用户反馈）：资料支持「全体 / 指定部门 / 指定人员」，
但此前的验证只用了管理员视角，**没有用真实普通用户交叉比对**。
本模块用 4 个独立浏览器上下文各登录一个账号，逐项比对同一批资料
在不同角色下「能不能看到 / 能不能打开 / 能不能取文件」。

种子数据约定的可见范围（seed_demo.py）：
- 全体：安全通道示意图、组织架构图、实习生协议模板…
- 部门：会议室布局(行政)、报销操作动图(财务+人事)、研发规范附件(研发)、消防演练签到表(人事)
- 人员：首页截图(user_rnd_1)、年度内审纪要(fin_1+hr_1)、密码令牌规范(user_rnd_2)

预期口径：
- 管理员 → 全部可见
- user_rnd_1（研发）→ 全体 + 研发部门 + 本人专属
- user_fin_1（财务）→ 全体 + 财务/人事部门（多部门并集）+ 本人专属
- user_hr_1（人事）→ 全体 + 人事部门 + 本人专属，**看不到研发专属**

注意：单点登录会踢掉同账号的其它会话，所以每个角色必须用**独立 context**，
且用完即弃（不复用 app.admin / app.user）。
"""
from __future__ import annotations

from harness import REPORT, case, http

ROLES = {
    "admin": {"username": "admin_demo", "password": "Demo@123456"},
    "rnd1": {"username": "user_rnd_1", "password": "Demo@123456"},
    "fin1": {"username": "user_fin_1", "password": "Demo@123456"},
    "hr1": {"username": "user_hr_1", "password": "Demo@123456"},
}

ADMIN_TOKEN = ""


_APP_ADMIN = {"session": None}


def _admin_token() -> str:
    """管理员令牌：**优先复用 app.admin 的会话**。

    2026-11-04 教训：系统是单点登录（每次登录 bump token_version），
    本模块若自己再登录一次 admin_demo，会把外层 app.admin 的令牌作废，
    导致**后续所有模块 401**（一次跑全量时后 19 个用例全挂）。
    所以这里只读不改，另存一个指针。
    """
    sess = _APP_ADMIN.get("session")
    if sess is not None:
        return sess.token
    raise RuntimeError("m63 需先由 A01 注入 app.admin 会话")


def _login_token(creds: dict) -> str:
    r = http("POST", "/api/v1/auth/login", "", {
        "username": creds["username"], "password": creds["password"],
    })
    return (r["body"] or {}).get("access_token") or ""


def _body_dict(resp) -> dict:
    """http() 的 body 在非 JSON 错误页时是 str，统一收敛成 dict。"""
    b = resp.get("body")
    return b if isinstance(b, dict) else {}


def _visible_titles(token: str) -> set[str]:
    """该角色能在资料列表里看到的全部标题（翻完所有页）。"""
    titles: set[str] = set()
    page = 1
    while True:
        body = _body_dict(http(
            "GET", f"/api/v1/documents?page={page}&page_size=100", token))
        for d in body.get("items") or []:
            titles.add(d["title"])
        if page >= (body.get("pages") or 1):
            break
        page += 1
    return titles


def _find_doc(token: str, keyword: str) -> dict | None:
    """按标题关键字精确找资料（翻完所有页，避免被 E2E 残留资料挤出首页）。"""
    page = 1
    while True:
        body = _body_dict(http(
            "GET", f"/api/v1/documents?keyword={keyword}&page={page}&page_size=100", token))
        for d in body.get("items") or []:
            if keyword in d["title"]:
                return d
        if page >= (body.get("pages") or 1):
            return None
        page += 1


@case("U01", "四种角色看到的资料集合符合可见范围约定")
def a01(app):
    global ADMIN_TOKEN
    _APP_ADMIN["session"] = app.admin      # 复用外层会话，绝不重新登录管理员
    admin_tok = app.admin.token
    ADMIN_TOKEN = admin_tok
    admin_titles = _visible_titles(admin_tok)

    # 管理员始终全见：管理员集合必须是其余角色集合的超集
    REPORT.check(len(admin_titles) > 0, f"管理员可见 {len(admin_titles)} 份资料")

    results = {}
    for key in ("rnd1", "fin1", "hr1"):
        tok = _login_token(ROLES[key])
        titles = _visible_titles(tok)
        results[key] = titles
        REPORT.check(len(titles) > 0, f"{key} 可见 {len(titles)} 份")
        REPORT.check(titles.issubset(admin_titles),
                     f"{key} 可见集合 ⊂ 管理员（无越权）")

    # 关键交叉断言：研发专属资料，财务/人事都看不到
    rnd_only = "DEMO-研发规范附件"
    REPORT.check(rnd_only in results["rnd1"], f"研发张三能看到「{rnd_only}」")
    REPORT.check(rnd_only not in results["fin1"], f"财务王五看不到「{rnd_only}」")
    REPORT.check(rnd_only not in results["hr1"], f"人事赵六看不到「{rnd_only}」")

    # 多部门并集：报销操作动图(财务+人事) → 两人都能看
    multi_dept = "DEMO-报销操作动图"
    REPORT.check(multi_dept in results["fin1"], f"财务能看到多部门资料「{multi_dept}」")
    REPORT.check(multi_dept in results["hr1"], f"人事也能看到多部门资料「{multi_dept}」")
    REPORT.check(multi_dept not in results["rnd1"], f"研发看不到多部门资料「{multi_dept}」")

    # 人员专属：首页截图只给 user_rnd_1
    user_only = "DEMO-首页截图"
    REPORT.check(user_only in results["rnd1"], f"张三能看到本人专属「{user_only}」")
    REPORT.check(user_only not in results["fin1"], f"财务看不到他人的专属「{user_only}」")

    # 全体资料：三人都能看
    public = "DEMO-组织架构图"
    for key in ("rnd1", "fin1", "hr1"):
        REPORT.check(public in results[key], f"{key} 能看到全体资料「{public}」")

    REPORT.note(f"可见数量：admin={len(admin_titles)} "
                f"rnd1={len(results['rnd1'])} "
                f"fin1={len(results['fin1'])} "
                f"hr1={len(results['hr1'])}")


@case("U02", "越权直连：详情/文件/预览一律拒绝（404，不泄露存在性）")
def a02(app):
    # 用财务身份尝试访问研发专属资料
    fin_tok = _login_token(ROLES["fin1"])
    doc = _find_doc(_admin_token(), "DEMO-研发规范附件")
    if not doc:
        REPORT.fail("找不到「DEMO-研发规范附件」")
        return

    did = doc["id"]
    for path, label in (
        (f"/api/v1/documents/{did}", "详情"),
        (f"/api/v1/documents/{did}/file", "下载"),
        (f"/api/v1/documents/{did}/preview/pdf", "PDF 预览"),
        (f"/api/v1/documents/{did}/status", "状态查询"),
    ):
        r = http("GET", path, fin_tok)
        REPORT.check(r["status"] == 404,
                     f"越权{label}返回 404（实际 {r['status']}）")

    # 管理员访问同一份 → 200（证明资料确实存在，只是权限不同）
    r = http("GET", f"/api/v1/documents/{did}", _admin_token())
    REPORT.check(r["status"] == 200, "管理员访问同一资料返回 200（对照）")


@case("U03", "搜索不能绕过可见范围")
def a03(app):
    fin_tok = _login_token(ROLES["fin1"])
    body = _body_dict(http("GET", "/api/v1/documents?keyword=研发规范", fin_tok))
    titles = [d["title"] for d in body.get("items") or []]
    REPORT.check("DEMO-研发规范附件" not in titles,
                 f"搜索「研发规范」不返回受限资料（命中：{titles}）")
    REPORT.check((body.get("total") or 0) == 0, f"搜索 total={body.get('total')}")


@case("U04", "浏览器 UI：普通用户的资料列表不含越权项")
def a04(app):
    """API 之外，再用真实浏览器确认列表渲染层同样过滤。"""
    from harness import Session

    bucket: list[str] = []
    sess = Session(app.browser, ROLES["fin1"], "fin_ui", bucket)
    try:
        # 用关键词搜索而非翻列表：E2E 自身会留下大量 E2E-* 资料占满前几页，
        # 直接看第一页无法判断可见性。
        sess.goto("/documents?keyword=研发规范")
        sess.page.wait_for_timeout(2500)
        txt = sess.page.inner_text("#doc-tbody") if sess.page.query_selector("#doc-tbody") else ""
        REPORT.check("DEMO-研发规范附件" not in txt,
                     "UI 搜索「研发规范」不含受限资料")

        sess.goto("/documents?keyword=组织架构图")
        sess.page.wait_for_timeout(2500)
        txt2 = sess.page.inner_text("#doc-tbody") if sess.page.query_selector("#doc-tbody") else ""
        REPORT.check("DEMO-组织架构图" in txt2,
                     "UI 搜索「组织架构图」能命中全体资料（对照）")

        # 越权直连 URL → 页面不应渲染内容
        doc = _find_doc(_admin_token(), "DEMO-研发规范附件")
        if doc:
            sess.goto(f"/documents/{doc['id']}")
            sess.page.wait_for_timeout(2000)
            body = sess.page.inner_text("body")
            REPORT.check("DEMO-研发规范附件" not in body,
                         "越权直连详情页不渲染资料内容")
        sess.shot("A04_fin_documents.png")
    finally:
        sess.ctx.close()
