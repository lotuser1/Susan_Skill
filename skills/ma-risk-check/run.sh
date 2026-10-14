#!/usr/bin/env bash
# ma-risk-check 入口：自动找一个可用的 Python 后执行检查脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "未找到 Python 3，请先安装：https://www.python.org/downloads/" >&2
  exit 1
fi

exec "$PY" "$SCRIPT_DIR/scripts/check.py" "$@"
