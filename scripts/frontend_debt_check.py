#!/usr/bin/env python3
"""前端债务守卫：innerHTML 只减不增。

背景：存量 JS 有 113 处 innerHTML，全部靠 escapeHtml / ui.js 纪律兜底。
2026-10-19 定稿约定：**新代码零新增**，旧文件计数不得增长；新增列表渲染
优先走 ui.js 的转义模板。本脚本用基线 JSON 把约定变成 CI 可执行的守卫。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "app" / "web" / "static" / "js"
BASELINE = Path(__file__).resolve().parent / "frontend_innerhtml_baseline.json"


def main() -> int:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline.pop("_comment", None)
    counts: dict[str, int] = {}
    for path in sorted(JS_DIR.rglob("*.js")):
        rel = path.relative_to(JS_DIR).as_posix()
        n = path.read_text(encoding="utf-8").count("innerHTML")
        if n:
            counts[rel] = n

    regressions: list[str] = []
    for rel, n in counts.items():
        allowed = int(baseline.get(rel, 0))
        if n > allowed:
            regressions.append(f"{rel}: {n} > 基线 {allowed}")
    for rel, allowed in baseline.items():
        if int(allowed) and rel not in counts:
            regressions.append(f"{rel}: 基线 {allowed}，文件已消失（请同步基线）")

    total = sum(counts.values())
    print(f"innerHTML 计数：{total}（基线 {sum(int(v) for v in baseline.values())}）")
    for rel, n in counts.items():
        print(f"  {rel}: {n}")
    if regressions:
        print("\nFAIL：innerHTML 出现增长")
        for item in regressions:
            print(f"  - {item}")
        return 1
    print("\nPASS：无新增 innerHTML")
    return 0


if __name__ == "__main__":
    sys.exit(main())
