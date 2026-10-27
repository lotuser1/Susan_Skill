#!/usr/bin/env bash
# ma-title-forge 入口：自动找一个可用的 Python 后执行标题自检
#
# 用法：
#   bash run.sh titles.txt --platform xhs --subject clinic
#   bash run.sh --text "标题一
# 标题二" --platform douyin
#   bash run.sh selftest          # 跑回归测试
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

if [ "${1:-}" = "selftest" ]; then
  shift
  exec "$PY" "$SCRIPT_DIR/scripts/selftest.py" "$@"
fi

exec "$PY" "$SCRIPT_DIR/scripts/check_titles.py" "$@"
