#!/usr/bin/env bash
# ma-postcare 入口：自动找一个可用的 Python 后执行
#
# 用法：
#   bash run.sh --list
#   bash run.sh --project 水光针 --format wechat --name 王女士
#   bash run.sh check 须知.md --format wechat   # 校验
#   bash run.sh selftest
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

case "${1:-}" in
  selftest) shift; exec "$PY" "$SCRIPT_DIR/scripts/selftest.py" "$@" ;;
  check)    shift; exec "$PY" "$SCRIPT_DIR/scripts/verify.py" "$@" ;;
  *)        exec "$PY" "$SCRIPT_DIR/scripts/build.py" "$@" ;;
esac
