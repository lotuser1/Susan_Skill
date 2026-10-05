#!/usr/bin/env bash
# audio-to-text 一键运行器
#
# 用法（任选其一，在 Terminal 里执行）：
#   1) 编辑同目录 config.env，设置 INPUT_DIRS（要处理的文件夹，逗号分隔）后：
#        bash run.sh
#   2) 临时只处理某一个文件夹（不改 config.env）：
#        bash run.sh --dir /某个/具体/目录
#   3) 临时处理单个文件：
#        bash run.sh --file /某个/文件.mp3
#
# 运行前会自动读取同目录的 config.env（或 .env）里的密钥与配置。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/config.env" ]; then
  set -a; source "$SCRIPT_DIR/config.env"; set +a
elif [ -f "$SCRIPT_DIR/.env" ]; then
  set -a; source "$SCRIPT_DIR/.env"; set +a
else
  echo "未找到 config.env。请先复制模板并填写：" >&2
  echo "  cp config.env.example config.env" >&2
  echo "然后编辑 config.env，填入你的百炼 API Key 与要处理的文件夹。" >&2
  exit 1
fi

# 把 config.env 里的 INPUT_DIRS（逗号分隔）展开成多个 --dir 参数。
# 若命令行已显式指定 --dir/--file，则不再自动追加 INPUT_DIRS（避免重复处理）。
ARGS=()
if [[ "$*" != *"--dir"* && "$*" != *"--file"* ]]; then
  if [ -n "${INPUT_DIRS:-}" ]; then
    IFS=',' read -ra DIRS <<< "$INPUT_DIRS"
    for d in "${DIRS[@]}"; do
      d="$(printf '%s' "$d" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      [ -n "$d" ] && ARGS+=(--dir "$d")
    done
  fi
fi
[ -n "${OUTPUT_DIR:-}" ]      && ARGS+=(--output-dir "$OUTPUT_DIR")
[ -n "${OUTPUT_FORMAT:-}" ]   && ARGS+=(--format "$OUTPUT_FORMAT")
[ -n "${TRANSCRIBE_LANG:-}" ] && ARGS+=(--lang "$TRANSCRIBE_LANG")

if [ ${#ARGS[@]} -eq 0 ] && [ -z "$*" ]; then
  echo "没有要处理的输入。请二选一：" >&2
  echo "  a) 编辑 config.env，设置 INPUT_DIRS=文件夹1,文件夹2" >&2
  echo "  b) 运行时加参数：bash run.sh --dir /某个/目录" >&2
  exit 1
fi

echo ">>> 开始转写（站点：${DASHSCOPE_BASE_URL:-国内站默认}）"
python3 "$SCRIPT_DIR/scripts/transcribe.py" "${ARGS[@]}" "$@"
