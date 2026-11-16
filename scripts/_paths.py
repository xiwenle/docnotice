"""脚本用路径与解释器的跨平台统一解析（2026-10-26 新增）。

**为什么需要**：此前 scripts/ 下有 20 处硬编码 `.venv/bin/python`（Windows 上
不存在，真实路径是 `.venv/Scripts/python.exe`）、14 处硬编码 `/tmp/...`。
更糟的是二者在 Windows 上**指向不同的目录**：

| 写法 | Windows 实际落点 |
|---|---|
| bash 的 `/tmp` | `%LOCALAPPDATA%\\Temp`（Git Bash 的 MSYS 映射） |
| Python 的 `Path("/tmp")` | `C:\\tmp` |

于是 `bootstrap_env.py`（Python）把 E2E 数据建到 `C:\\tmp\\fms_e2e_data`，
`serve_e2e.sh`（bash）却去 `%TEMP%\\fms_e2e_data` 找 —— 必然报「缺少
/tmp/fms_e2e_data」。这在 Linux 上因 `/tmp` 同源而不会暴露，是跨平台盲区。

**本模块的约定**：

- 所有临时目录统一放在 `tempfile.gettempdir()` 下（Windows = `%TEMP%`，
  Linux = `/tmp`），**Python 与 shell 解析到同一处**。
- 需要 shell 与 Python 共享同一路径时，用 `FMS_TMP_DIR` 环境变量覆盖；
  未设置时两者一致（因为都用 `tempfile.gettempdir()` 的同源规则）。
- 解释器路径按平台给出，调用方直接用 `PY` / `PYTHON` 常量。

用法::

    from _paths import E2E_DATA_DIR, E2E_FILES_DIR, PY, TMP_DIR
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 项目根：scripts/_paths.py -> 上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---- 解释器 ----
# 用 sys.executable 而非拼路径：脚本无论用哪个解释器启动，就用哪个，
# 天然避免「venv 装在别处」和「系统 Python 缺依赖」两类问题。
PY = sys.executable

# 仓库内 venv 的解释器（用于生成给用户看的命令串 / shell 脚本）
if os.name == "nt":
    VENV_PY_REL = Path(".venv/Scripts/python.exe")
    VENV_UVICORN_REL = Path(".venv/Scripts/uvicorn.exe")
else:
    VENV_PY_REL = Path(".venv/bin/python")
    VENV_UVICORN_REL = Path(".venv/bin/uvicorn")

VENV_PY = PROJECT_ROOT / VENV_PY_REL
VENV_UVICORN = PROJECT_ROOT / VENV_UVICORN_REL


def _tmp_root() -> Path:
    """临时根目录。

    优先 `FMS_TMP_DIR` 环境变量（供 shell 与 Python 显式对齐）；
    否则用 tempfile.gettempdir() —— Windows 得到 `%TEMP%`，Linux 得到 `/tmp`。
    """
    override = os.environ.get("FMS_TMP_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir())


TMP_DIR = _tmp_root()

# ---- 脚本专用子目录（全部集中在临时根下）----
E2E_DATA_DIR = TMP_DIR / "fms_e2e_data"
E2E_FILES_DIR = TMP_DIR / "e2e_files"
ADMIN_JOURNEY_DIR = TMP_DIR / "dsh_admin_journey"
EXTREME_SAMPLES_DIR = TMP_DIR / "docnotice-extreme"
FRONTEND_CHECK_DIR = TMP_DIR / "fms_check_data"
THEME_AUDIT_DIR = TMP_DIR / "fms_theme_data"


def py_cmd(rel_script: str) -> str:
    """生成「用本仓库 venv 跑某脚本」的命令串（供提示信息与 shell 使用）。"""
    return f'"{VENV_PY}" {rel_script}'


def ensure_dir(path: Path) -> Path:
    """确保目录存在并返回它。"""
    path.mkdir(parents=True, exist_ok=True)
    return path
