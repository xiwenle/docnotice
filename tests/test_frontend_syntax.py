"""前端 JS 语法守卫（2026-11-03 补）。

背景：上一轮删「确认」功能时，误删了一行 `if (...)` 条件却留下 `else if`，
浏览器报 `Unexpected token 'else'`。**后端全量 608 条测试全绿，一条都没抓到**
——因为 Python 测试根本不解析 JS。

本测试用 Node 的 `--check` 对全部静态 JS 做语法检查（只解析不执行，
不依赖浏览器、无副作用）。Node 不存在时跳过并说明原因，不算失败。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "js"

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="未安装 Node.js，跳过 JS 语法检查")
def test_all_static_js_parses():
    """每个静态 JS 文件都必须能通过语法解析。"""
    files = sorted(JS_DIR.rglob("*.js"))
    assert files, f"未找到任何 JS 文件：{JS_DIR}"

    broken = []
    for f in files:
        r = subprocess.run(
            [NODE, "--check", str(f)], capture_output=True, text=True
        )
        if r.returncode != 0:
            detail = (r.stderr or "").strip().splitlines()
            broken.append(f"{f.relative_to(JS_DIR.parent.parent.parent)}: "
                          f"{detail[0] if detail else 'parse error'}")

    assert not broken, "JS 语法错误（浏览器会整文件不执行）：\n" + "\n".join(broken)
