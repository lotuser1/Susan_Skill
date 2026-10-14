#!/usr/bin/env bash
# 启动一个带远程调试端口的可见 Chrome（供 auto_transcribe 通过 CDP 操作通义听悟）。
# 启动后请在浏览器里扫码登录，再运行 auto_transcribe。
#
# 环境变量：
#   TINGWU_CHROME_PORT  调试端口（默认 9222）
#   CHROME_BIN          显式指定 Chrome 可执行文件（自动探测失败时用）

set -e
PORT="${TINGWU_CHROME_PORT:-9222}"

# ---- 自动探测 Chrome 可执行文件 ----
CHROME=""
if [ -n "${CHROME_BIN:-}" ]; then
  CHROME="$CHROME_BIN"
elif [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
elif [ -x "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" ]; then
  CHROME="/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
elif [ -x "$HOME/.agent-browser/browsers/chrome-155.0.8059.39/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" ]; then
  CHROME="$HOME/.agent-browser/browsers/chrome-155.0.8059.39/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
elif command -v google-chrome >/dev/null 2>&1; then
  CHROME="$(command -v google-chrome)"
elif command -v chromium >/dev/null 2>&1; then
  CHROME="$(command -v chromium)"
fi

if [ -z "$CHROME" ]; then
  echo "未找到 Chrome。请安装 Chrome，或设置 CHROME_BIN 指向其可执行文件。" >&2
  exit 1
fi
echo ">>> 使用 Chrome：$CHROME"

UDD="$HOME/.auto-tingwu-chrome-profile"
mkdir -p "$UDD"

# 若端口已被占用（上次没关），提示用户
if curl -s --max-time 3 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo ">>> 端口 $PORT 已被占用（可能 Chrome 已在运行）。若是同一个调试窗口可直接使用；否则请先关闭旧 Chrome。"
else
  # 后台启动可见 Chrome，带调试端口 + 独立用户配置（登录态保存在 UDD 里）
  "$CHROME" \
    --remote-debugging-port="$PORT" \
    --remote-allow-origins='*' \
    --user-data-dir="$UDD" \
    --no-sandbox --disable-setuid-sandbox --disable-gpu \
    --no-first-run --no-default-browser-check \
    --window-size=1400,900 \
    "https://tingwu.aliyun.com/folders/0" &
  echo ">>> Chrome 已在后台启动，调试端口 $PORT。"
fi

echo ""
echo ">>> 请在弹出的 Chrome 窗口里登录通义听悟（阿里云APP / 支付宝 / 钉钉扫码）。"
echo ">>> 登录完成后保持窗口打开，再运行："
echo "      bash run.sh --dry-run     # 先预览计划"
echo "      bash run.sh --apply       # 正式上传"
