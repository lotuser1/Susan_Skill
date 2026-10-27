#!/usr/bin/env bash
# ma-consult-note 入口：自动找一个可用的 Python 后转到对应脚本
#
#   bash run.sh prepare 原稿.md            # 预处理：角色推断 + 切块 + 数字锚点
#   bash run.sh verify 档案.md --source 原稿.md   # 校验：反幻觉
#   bash run.sh selftest                   # 自测
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

CMD="${1:-}"
case "$CMD" in
  prepare)  shift; exec "$PY" "$SCRIPT_DIR/scripts/prepare.py" "$@" ;;
  verify)   shift; exec "$PY" "$SCRIPT_DIR/scripts/verify.py" "$@" ;;
  selftest) shift; exec "$PY" "$SCRIPT_DIR/scripts/selftest.py" "$@" ;;
  *)
    echo "用法：" >&2
    echo "  bash run.sh prepare 原稿.md" >&2
    echo "  bash run.sh verify 档案.md --source 原稿.md" >&2
    echo "  bash run.sh selftest" >&2
    exit 1
    ;;
esac
