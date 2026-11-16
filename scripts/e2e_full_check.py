"""全功能浏览器 E2E 走查入口。

用法（先起一个独立实例，避免污染演示库）：
    E2E_BASE_URL=http://127.0.0.1:8010 python scripts/e2e_full_check.py [模块前缀...]

不带参数＝全跑；带参数按模块前缀过滤，例如：
    scripts/e2e_full_check.py A0        # 只跑认证模块
    scripts/e2e_full_check.py D0 N0     # 资料 + 通知模块
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "e2e"))   # harness / m*.py
sys.path.insert(0, str(_HERE))            # _paths.py（跨平台路径统一真源）


def _load_modules() -> list[str]:
    """自动导入 scripts/e2e/m*.py（新增模块无需改本文件；前缀排序即执行顺序）。"""
    import importlib

    harness = importlib.import_module("harness")
    pkg = Path(__file__).resolve().parent / "e2e"
    names = sorted(p.stem for p in pkg.glob("m*.py"))
    for n in names:
        importlib.import_module(n)
    return names, harness


def main() -> int:
    names, harness = _load_modules()
    print(f"已加载 {len(names)} 个模块：{', '.join(names)}")
    selected = set(sys.argv[1:]) or None
    return harness.run_selected(selected)


if __name__ == "__main__":
    sys.exit(main())
