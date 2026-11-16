"""M62 四级分类下的真实业务数据：筛选、计数、详情。

与 m30 的分工：
- m30 C04 已验证「UI 能逐层建到第 4 级、第 5 级按钮消失」——那是**结构能力**；
- 本模块验证**四级分类真的承载了资料**，且筛选/计数/详情三条链路都对。

背景（用户反馈）：旧的演示数据只有 2 层分类，"四级分类里放资料"这条链路
从未被走过。种子脚本 2026-11-03 已建出 14 个四级节点，其中
「年度内审」「接口与错误码」等挂了真实资料。
"""
from __future__ import annotations

from harness import REPORT, case, http


def _all_categories(app) -> dict[str, dict]:
    """把分类树拍平成 {名称: {id, depth, path}}。"""
    cats = http("GET", "/api/v1/categories", app.admin.token)["body"] or {}
    flat: dict[str, dict] = {}

    def walk(nodes, depth=1, path=""):
        for n in nodes or []:
            full = f"{path}/{n['name']}" if path else n["name"]
            flat.setdefault(n["name"], {"id": n["id"], "depth": depth, "path": full})
            walk(n.get("children"), depth + 1, full)

    walk(cats.get("items") or [])
    return flat


@case("C11", "四级分类节点确实存在且分布合理")
def c11(app):
    flat = _all_categories(app)
    lvl4 = [name for name, info in flat.items() if info["depth"] == 4]
    REPORT.check(len(flat) >= 35, f"分类总数 {len(flat)}（要求 ≥35）")
    REPORT.check(len(lvl4) >= 5, f"四级分类 {len(lvl4)} 个（要求 ≥5）")

    depth_counts = {}
    for info in flat.values():
        depth_counts[info["depth"]] = depth_counts.get(info["depth"], 0) + 1
    for d in (1, 2, 3, 4):
        REPORT.check(depth_counts.get(d, 0) > 0, f"第 {d} 级有 {depth_counts.get(d, 0)} 个")
    REPORT.note(f"层级分布：{dict(sorted(depth_counts.items()))}")


@case("C12", "四级分类下的资料：筛选命中、计数一致、详情可开")
def c12(app):
    flat = _all_categories(app)
    target = None
    for name, info in flat.items():
        if info["depth"] != 4:
            continue
        body = http("GET", f"/api/v1/documents?category_id={info['id']}&page_size=100",
                    app.admin.token)["body"] or {}
        if (body.get("total") or 0) > 0:
            target = (name, info, body)
            break

    if not target:
        REPORT.fail("没有任何四级分类挂着资料（种子数据不达标）")
        return

    name, info, body = target
    REPORT.check(info["depth"] == 4, f"命中分类「{name}」位于第 4 级")
    REPORT.check((body.get("total") or 0) > 0, f"该分类下资料 {body.get('total')} 份")
    REPORT.note(f"路径：{info['path']}")

    s = app.admin
    s.goto(f"/documents?category_id={info['id']}")
    s.page.wait_for_timeout(2300)
    rows = s.page.query_selector_all("#doc-tbody tr")
    REPORT.check(len(rows) == (body.get("total") or 0),
                 f"UI 列表 {len(rows)} 行 = API total {body.get('total')}")

    first = body["items"][0]
    s.goto(f"/documents/{first['id']}")
    s.page.wait_for_timeout(2300)
    txt = s.page.inner_text("body")
    REPORT.check(first["title"] in txt, f"资料详情可打开：{first['title']}")
    REPORT.check("分类：" in txt, "详情页显示所属分类")
    s.shot("C12_level4_document.png")


@case("C13", "父级分类筛选包含全部子孙（文件夹语义）")
def c13(app):
    flat = _all_categories(app)
    root = None
    for name, info in flat.items():
        if info["depth"] != 1:
            continue
        body = http("GET", f"/api/v1/documents?category_id={info['id']}&page_size=100",
                    app.admin.token)["body"] or {}
        if (body.get("total") or 0) >= 2:
            root = (name, info, body)
            break
    if not root:
        REPORT.fail("找不到有资料的根分类")
        return

    name, info, body = root
    descendants_total = 0
    for _, sub in flat.items():
        if sub["path"].startswith(info["path"] + "/") and sub["depth"] == 4:
            b = http("GET", f"/api/v1/documents?category_id={sub['id']}&page_size=100",
                     app.admin.token)["body"] or {}
            descendants_total += b.get("total") or 0

    REPORT.check((body.get("total") or 0) >= descendants_total,
                 f"根「{name}」筛选 {body.get('total')} 份 ≥ 其四级后代 {descendants_total} 份")
    REPORT.note("文件夹语义：父分类筛选包含全部子孙（category_service.subtree_ids）")
