#!/bin/sh
# 安装 git hooks 到 .git/hooks/（hooks 本身不进版本库，故用本脚本分发）。
#
#   bash scripts/git-hooks/install.sh
#
# 卸载：rm .git/hooks/pre-commit
set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ ! -d .git ]; then
  echo "错误：当前目录不是 git 仓库根" >&2
  exit 1
fi

cp scripts/git-hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit 2>/dev/null || true

echo "已安装 .git/hooks/pre-commit"
echo "  每次提交会跑 ruff + mypy（< 1 秒）"
echo "  需要连测试一起跑：FMS_RUN_TESTS=1 git commit ..."
echo "  紧急跳过：git commit --no-verify"
