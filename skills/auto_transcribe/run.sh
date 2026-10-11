#!/usr/bin/env bash
# auto_transcribe 一键运行器
#
# 前置：先运行 start_chrome.sh 启动带调试端口的 Chrome 并扫码登录通义听悟。
#
# 用法（任选其一，在 Terminal 里执行）：
#   预览（不真正上传）：  bash run.sh --dry-run
#   正式上传：            bash run.sh --apply
#   只看状态：            bash run.sh --status
#   临时指定单个目录：    bash run.sh --apply --dir /某个/目录
#
# 运行前会自动读取同目录的 config.env（或 .env）。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/config.env" ]; then
  set -a; source "$SCRIPT_DIR/config.env"; set +a
elif [ -f "$SCRIPT_DIR/.env" ]; then
  set -a; source "$SCRIPT_DIR/.env"; set +a
else
  echo "未找到 config.env。请先复制模板并填写：" >&2
  echo "  cp config.env.example config.env" >&2
  echo "然后编辑 config.env，填入要处理的文件夹 TINGWU_SOURCE_DIRS。" >&2
  exit 1
fi

if [ -z "${TINGWU_SOURCE_DIRS:-}" ]; then
  echo "config.env 里没有 TINGWU_SOURCE_DIRS（要处理的文件夹）。请先填写。" >&2
  exit 1
fi

# 解析一个「已安装 websocket-client」的 python，避免裸 python3 缺依赖直接报错。
# 顺序：① 系统 python3 → ② 本地 .venv → ③ 本机已有托管 venv 兜底
PY=""
if python3 -c "import websocket" 2>/dev/null; then
  PY="python3"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ] && "$SCRIPT_DIR/.venv/bin/python" -c "import websocket" 2>/dev/null; then
  PY="$SCRIPT_DIR/.venv/bin/python"
else
  CAND="/Users/songzi/.workbuddy/binaries/python/envs/default/bin/python3"
  if [ -x "$CAND" ] && "$CAND" -c "import websocket" 2>/dev/null; then
    PY="$CAND"
  fi
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

"$PY" "$SCRIPT_DIR/scripts/auto_transcribe.py" "$@"
