"""M67 边界与并发：非法分页参数、并发发布/删除。

背景（探索发现的覆盖缺口）：
- 分页边界：现有测试只验证正常翻页，没测 `page=0` / `page_size>100`；
- 并发：`test_concurrency.py` 只有 3 例（改标题/替换文件/并发阅读），
  **没有并发发布通知、没有并发删除资料**。

这两类问题不会在人工点击时暴露，但在真实多用户场景下会。
"""
from __future__ import annotations

import threading

from harness import REPORT, case, http


@case("E11", "分页边界：非法参数返回 422")
def e11(app):
    tok = app.admin.token
    cases = [
        ("/api/v1/documents?page=0", "page=0"),
        ("/api/v1/documents?page_size=101", "page_size=101"),
        ("/api/v1/documents?page_size=0", "page_size=0"),
        ("/api/v1/notices?page=0", "通知 page=0"),
        ("/api/v1/notices?page_size=101", "通知 page_size=101"),
    ]
    for path, label in cases:
        r = http("GET", path, tok)
        REPORT.check(r["status"] in (400, 422),
                     f"{label} → {r['status']}（期望 4xx）")

    # 正常边界值应通过
    r = http("GET", "/api/v1/documents?page=1&page_size=100", tok)
    REPORT.check(r["status"] == 200, "page=1&page_size=100 正常（边界内）")


@case("E12", "分页一致性：翻页不重不漏")
def e12(app):
    tok = app.admin.token
    first = http("GET", "/api/v1/documents?page=1&page_size=10", tok)["body"]
    total = first.get("total") or 0
    pages = first.get("pages") or 1
    if pages < 2:
        REPORT.note(f"资料不足 2 页（total={total}），跳过翻页一致性")
        return

    seen: list[int] = []
    for p in range(1, min(pages, 4) + 1):
        body = http("GET", f"/api/v1/documents?page={p}&page_size=10", tok)["body"]
        seen.extend(d["id"] for d in body.get("items") or [])

    REPORT.check(len(seen) == len(set(seen)), f"翻页无重复（{len(seen)} 条 / {len(set(seen))} 唯一）")
    REPORT.check(len(seen) == min(pages, 4) * 10 or len(seen) == total,
                 f"翻页条数合理（{len(seen)}）")


@case("E13", "并发取消同一通知：一次成功，另一次 409")
def e13(app):
    """草稿下线后「并发发布」场景已不存在（创建即发布）。

    改测同一类竞态的现存活路径：两个请求同时取消同一条通知，
    必须恰好一次成功、另一次被 409 拦住，不能双写成两条回收站记录。
    """
    tok = app.admin.token
    r = http("POST", "/api/v1/notices", tok, {
        "title": "E2E并发取消", "content": "并发验证", "notice_type": "发布",
        "target": {"scope": "all"},
    })
    if r["status"] not in (200, 201):
        REPORT.fail(f"建通知失败 {r['status']}")
        return
    nid = r["body"]["id"]

    results: list[int] = []
    lock = threading.Lock()

    def cancel():
        resp = http("POST", f"/api/v1/notices/{nid}/cancel", tok,
                    {"reason": "并发取消验证"})
        with lock:
            results.append(resp["status"])

    threads = [threading.Thread(target=cancel) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = results.count(200)
    conflict = results.count(409)
    REPORT.check(ok == 1, f"恰好 1 次取消成功（实际 {ok}，全部 {results}）")
    REPORT.check(conflict >= 1, f"另一次返回 409 CONFLICT（实际 {conflict}）")


@case("E14", "并发删除同一资料：一次成功，其余 404/409")
def e14(app):
    tok = app.admin.token
    r = http("POST", "/api/v1/documents", tok, {
        "title": "E2E并发删除", "effective_from": "2020-01-01",
    })
    if r["status"] not in (200, 201):
        REPORT.fail(f"建资料失败 {r['status']}")
        return
    did = r["body"]["id"]

    results: list[int] = []
    lock = threading.Lock()

    def archive():
        resp = http("DELETE", f"/api/v1/documents/{did}", tok,
                    {"reason": "并发删除验证"})
        with lock:
            results.append(resp["status"])

    threads = [threading.Thread(target=archive) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = results.count(200)
    REPORT.check(ok >= 1, f"至少 1 次删除成功（全部 {results}）")
    REPORT.check(all(s in (200, 404, 409) for s in results),
                 f"没有 500 等异常状态（全部 {results}）")

    # 最终状态一致：资料已归档
    d = http("GET", f"/api/v1/documents/{did}", tok)["body"]
    if isinstance(d, dict):
        REPORT.check(d.get("archived") is True or d.get("status") == "archived",
                     "并发后最终状态为已归档")


@case("E15", "无效 id 不产生 500（404 而非崩溃）")
def e15(app):
    tok = app.admin.token
    for path, label in (
        ("/api/v1/documents/999999", "资料详情"),
        ("/api/v1/notices/999999", "通知详情"),
        ("/api/v1/documents/999999/file", "资料文件"),
    ):
        r = http("GET", path, tok)
        REPORT.check(r["status"] in (403, 404),
                     f"{label} 无效 id → {r['status']}（不应 500）")
