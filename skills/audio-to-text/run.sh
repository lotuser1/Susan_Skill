#!/usr/bin/env bash
# 用法: ./run.sh --dir /path/to/audio
# 会自动读取同目录下的 config.env（或 .env）里的密钥
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/config.env" ]; then
  set -a; source "$SCRIPT_DIR/config.env"; set +a
elif [ -f "$SCRIPT_DIR/.env" ]; then
  set -a; source "$SCRIPT_DIR/.env"; set +a
fi

python3 "$SCRIPT_DIR/scripts/transcribe.py" "$@"
