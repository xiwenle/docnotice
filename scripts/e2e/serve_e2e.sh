#!/usr/bin/env bash
# 启动 E2E 被测实例（独立数据副本 + 放宽登录限速），端口 8010。
#
#   scripts/e2e/serve_e2e.sh          # 前台运行（Ctrl-C 停止）
#   scripts/e2e/serve_e2e.sh &        # 后台运行
#
# 前置：先跑 scripts/e2e/bootstrap_env.py 造好隔离数据副本与测试文件。
#
# 路径口径（2026-10-26 修正）：临时目录**不再硬编码 /tmp**。
# 此前 bash 的 /tmp（Windows = %TEMP%）与 Python 的 Path("/tmp")（Windows = C:\tmp）
# 指向不同目录，导致本脚本在 Windows 上必然报「缺少 /tmp/fms_e2e_data」。
# 现在两边都从 scripts/_paths.py 取值（默认 tempfile.gettempdir()，
# 可用 FMS_TMP_DIR 环境变量显式覆盖），保证同源。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# ---- 定位 venv 解释器（Windows / POSIX 分支）----
if [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  echo "找不到 .venv 解释器，请先创建虚拟环境（见 README「快速开始」）" >&2
  exit 1
fi

# ---- 向 Python 问同一个路径（唯一真源，避免 bash 与 Python 各算各的）----
PATHS="$("$PY" -c "import sys; sys.path.insert(0, 'scripts'); from _paths import E2E_DATA_DIR; print(E2E_DATA_DIR)")"
DATA_DIR="$PATHS"

if [ ! -d "$DATA_DIR" ]; then
  echo "缺少隔离数据副本：$DATA_DIR" >&2
  echo "请先运行：$PY scripts/e2e/bootstrap_env.py" >&2
  exit 1
fi

# E2E 用例会连续登录/改密多次，必须放宽限速（默认 10 次/60s 会被自己挡掉）
exec env \
  DATA_DIR="$DATA_DIR" \
  PORT=8010 \
  APP_ENV=development \
  LOGIN_RATE_MAX_ATTEMPTS=5000 \
  "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --log-level warning
