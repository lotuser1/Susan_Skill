#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话管理：查登录身份 / 退出登录 / 关闭调试 Chrome。

5 个账户串行导出时用：
    --whoami   打印当前登录的听悟账户名（未登录则报 CMN.NotLogin）
    --logout   清 cookie 并验证已登出（不关 Chrome，等下一个账户扫码）
    --finish   关闭调试 Chrome（最后一轮才用）
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_helper import Page, close_debug_browser, log
from export_transcripts import account_label

JS_ACCOUNT = """
(async () => {
  const r = await fetch('https://tingwu.aliyun.com/api/account/v2/user/info?c=web',
                        {credentials:'include'});
  const j = await r.json().catch(() => ({}));
  return {http: r.status, code: j.code, msg: j.message, data: j.data || {}};
})()
"""


def account_info(retries=3):
    """返回 (is_login, name, raw)。"""
    raw = None
    for i in range(retries):
        try:
            pg = Page()
            raw = pg.js(JS_ACCOUNT, tries=3)
            pg.close()
            if isinstance(raw, dict) and raw.get("code") is not None:
                break
        except Exception as e:
            log(f"  (第{i+1}次) 连接失败：{str(e)[:80]}")
            time.sleep(2)
    if not isinstance(raw, dict):
        return False, "", raw
    if raw.get("code") == "CMN.NotLogin" or raw.get("http") in (401, 403):
        return False, "", raw
    name = account_label(raw.get("data") or {})
    return bool(name), name, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--whoami", action="store_true")
    ap.add_argument("--logout", action="store_true")
    ap.add_argument("--finish", action="store_true")
    a = ap.parse_args()

    if a.finish:
        close_debug_browser()
        return 0

    if a.whoami:
        ok, name, raw = account_info()
        if ok:
            log(f"✅ 当前登录账户：{name}")
            return 0
        log(f"❌ 未登录（{str(raw)[:160]}）。请先运行 bash start_chrome.sh 并扫码登录。")
        return 2

    if a.logout:
        try:
            pg = Page()
            pg.clear_cookies()
            time.sleep(1)
            pg.goto("https://tingwu.aliyun.com/home", wait=6)
            pg.close()
        except Exception as e:
            log(f"⚠️ 清 cookie 时出错：{str(e)[:120]}")
        time.sleep(1)
        ok, name, raw = account_info()
        if ok:
            log(f"⚠️ 仍显示已登录（{name}）。可能是阿里云 SSO 域外 cookie 残留，"
                f"请在该 Chrome 窗口里手动点「退出登录」，然后再跑一次 --logout。")
            return 3
        log("✅ 已退出登录，Chrome 仍开着。请在窗口里扫码登录下一个账户，"
            "然后运行：bash run.sh --account accN --apply")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
