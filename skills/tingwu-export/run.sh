#!/usr/bin/env bash
# tingwu-export 一键运行器
#
# 前置：先运行 start_chrome.sh 启动带调试端口的 Chrome 并扫码登录通义听悟。
#
# 用法（在 Terminal 里执行）：
#   探测接口（第一次必做）：  bash run.sh --probe
#   本地样本校验解析器：      bash run.sh --parse-samples
#   看当前登录账户：          bash run.sh --whoami
#   只看任务状态（不下载）：  bash run.sh --account acc1 --status
#   小批量试跑 5 条：         bash run.sh --account acc1 --limit 5 --apply
#   正式全量下载：            bash run.sh --account acc1 --apply
#   切换下一个账户（清登录）：bash run.sh --logout
#   最后一轮结束关浏览器：    bash run.sh --finish
#
# 运行前会自动读取同目录的 config.env（不存在则用内置默认值）。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/config.env" ]; then
  set -a; source "$SCRIPT_DIR/config.env"; set +a
fi

# 解析一个「已安装 websocket-client」的 python，避免裸 python3 缺依赖直接报错。
# 顺序：① 系统 python3 → ② 本地 .venv → ③ 本机已有托管 venv 兜底
PY=""
if python3 -c "import websocket" 2>/dev/null; then
  PY="python3"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ] && "$SCRIPT_DIR/.venv/bin/python" -c "import websocket" 2>/dev/null; then
  PY="$SCRIPT_DIR/.venv/bin/python"
else
  :  # 未找到可用解释器，交由下方自动创建 .venv 兜底
fi
if [ -z "$PY" ]; then
  echo ">>> 检测到缺少 websocket-client，正在创建本地虚拟环境并安装（首次稍慢）..."
  if python3 -m venv "$SCRIPT_DIR/.venv" 2>/dev/null && "$SCRIPT_DIR/.venv/bin/pip" install -q websocket-client 2>/dev/null; then
    PY="$SCRIPT_DIR/.venv/bin/python"
  fi
fi
if [ -z "$PY" ]; then
  echo "无法自动准备 Python 运行环境。请手动执行：python3 -m pip install websocket-client" >&2
  exit 1
fi
echo ">>> 使用解释器：$PY"

ARGS="$*"
case "$ARGS" in
  *--probe*)
    exec "$PY" "$SCRIPT_DIR/scripts/probe_api.py" "$@"
    ;;
  *--dump-samples*)
    exec "$PY" "$SCRIPT_DIR/scripts/dump_samples.py" "$@"
    ;;
  *--parse-samples*)
    exec "$PY" "$SCRIPT_DIR/scripts/parse_sample.py" "$@"
    ;;
  *--whoami*|*--logout*|*--finish*)
    exec "$PY" "$SCRIPT_DIR/scripts/tw_session.py" "$@"
    ;;
  *--classify*)
    exec "$PY" "$SCRIPT_DIR/scripts/scan_pending.py" "$@"
    ;;
  *--arch*)
    exec "$PY" "$SCRIPT_DIR/scripts/apply_plan.py" "$@"
    ;;
  *)
    exec "$PY" "$SCRIPT_DIR/scripts/export_transcripts.py" "$@"
    ;;
esac
