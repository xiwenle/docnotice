"""浏览器走查脚本的取数方式必须抗得住库里的数据增长。

2026-11-16 实测：``scripts/admin_journey.py`` 通过
``GET /api/v1/notices?page_size=50`` 再在返回里找标题为「DEMO-重要制度发布」
的种子通知。journey 自己会先新建 3 条通知，叠加上一轮的残留，库里超过 50 条
之后种子通知就被挤出第一页，脚本以 ``StopIteration`` 崩掉 —— G4 因此 FAIL，
而且失败点离真正的原因很远。

修法是按标题精确查询（``keyword``），不再翻列表页。这类回归守护「取数方式」，
而不是某个具体数字。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_admin_journey_finds_seed_notice_by_title_not_by_page_position():
    text = read("scripts/admin_journey.py")
    # 不得再出现「翻列表页找种子通知」这种写法。
    assert not re.search(
        r'page_size=50[\'"]\)\["items"\][\s\S]{0,120}?DEMO-重要制度发布',
        text,
    ), "admin_journey still locates the seed notice by paging through the list"
    assert "DEMO-重要制度发布" in text
    assert "keyword" in text, "the seed notice must be looked up by keyword"


def test_admin_journey_does_not_hardcode_a_small_page_size_for_lookups():
    text = read("scripts/admin_journey.py")
    # Any list read that then filters by a specific title must ask for the
    # API's maximum page, otherwise a title that falls past the first page
    # silently disappears from the result.
    for match in re.finditer(r'page_size=(\d+)', text):
        assert int(match.group(1)) >= 100, (
            f"page_size={match.group(1)} is below the API maximum of 100; "
            "a lookup must not depend on the list fitting in one page"
        )


def test_user_journey_lookups_do_not_page_through_a_bounded_list():
    text = read("scripts/user_journey.py")
    for match in re.finditer(r'page_size=(\d+)', text):
        assert int(match.group(1)) >= 100, (
            f"page_size={match.group(1)} is below the API maximum of 100"
        )
