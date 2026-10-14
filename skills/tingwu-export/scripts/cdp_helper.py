#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CDP 驱动（精简版）：直连已开启调试端口的可见 Chrome（默认 9222）。

与 auto_transcribe/scripts/cdp_helper.py 的区别：
  - 去掉 tw_helpers.js 注入与所有 DOM 点击封装（导出流程只调接口，不点页面）
  - 去掉 _inject / ensure_helpers
  - 新增 clear_cookies()，用于 5 账户串行时清登录态

保留：代理环境变量清理、Page.send / Page.js / Page.goto、close_debug_browser。
"""
import json
import os
import subprocess
import time
from pathlib import Path

import websocket

PORT = int(os.environ.get("TINGWU_CHROME_PORT", "9222"))

# 确保 CDP 直连 127.0.0.1，绕过本机 HTTP 代理（WorkBuddy 本地代理等），
# 否则连 127.0.0.1:PORT 会被代理劫持。本进程只连本机 CDP，外网请求由 Chrome 发起。
for _k in ("http_proxy", "https_proxy", "all_proxy",
           "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_k, None)
_np = os.environ.get("no_proxy", "")
if "127.0.0.1" not in _np:
    os.environ["no_proxy"] = (_np + ",127.0.0.1,localhost").strip(",")
    os.environ["NO_PROXY"] = os.environ["no_proxy"]


def log(*a):
    print(f"[{time.strftime('%m-%d %H:%M:%S')}]", *a, flush=True)


def http(path):
    return subprocess.run(
        ["curl", "-s", "--noproxy", "127.0.0.1,localhost", "--max-time", "8",
         f"http://127.0.0.1:{PORT}{path}"],
        capture_output=True, text=True,
    ).stdout


def find_page():
    """找到真正的网页标签页（排除 chrome:// 内部页），优先听悟页面。"""
    tabs = json.loads(http("/json/list") or "[]")
    pages = [t for t in tabs
             if t.get("type") == "page"
             and not t.get("url", "").startswith(("chrome://", "chrome-untrusted", "devtools://"))]
    if not pages:
        raise RuntimeError(
            "Chrome 里没有可操作标签页。请先运行 bash start_chrome.sh 启动并登录。")
    for p in pages:
        if "tingwu.aliyun.com" in p.get("url", ""):
            return p
    return pages[0]


class Page:
    """极简 CDP 客户端（每个实例对应一个已连接的页面）。"""

    def __init__(self, ws_url=None):
        if ws_url is None:
            pg = find_page()
            ws_url = pg["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(ws_url, timeout=240)
        self.i = 0
        try:
            self.send("Page.enable")
        except Exception:
            pass

    def _auto_dismiss_dialog(self, accept=False):
        try:
            self.i += 1
            self.ws.send(json.dumps({
                "id": self.i,
                "method": "Page.handleJavaScriptDialog",
                "params": {"accept": accept},
            }))
        except Exception:
            pass

    def send(self, m, **p):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p}))
        while True:
            try:
                r = json.loads(self.ws.recv())
            except Exception:
                continue
            if "method" in r and "id" not in r:
                if r.get("method") == "Page.javascriptDialogOpening":
                    dtype = (r.get("params") or {}).get("type")
                    self._auto_dismiss_dialog(dtype == "beforeunload")
                continue
            if r.get("id") == mid:
                if "error" in r:
                    raise RuntimeError(f"{m}: {str(r['error'])[:200]}")
                return r.get("result", {})

    def js(self, expr, tries=1):
        """执行 JS（支持 async IIFE，awaitPromise=True）。"""
        last = None
        for _ in range(max(1, tries)):
            r = self.send("Runtime.evaluate", expression=expr,
                          returnByValue=True, awaitPromise=True)
            if r.get("exceptionDetails"):
                d = r["exceptionDetails"]
                desc = (d.get("exception") or {}).get("description") or d.get("text", "")
                return {"__error__": str(desc)[:300]}
            last = r.get("result", {}).get("value")
            if last is not None:
                return last
            time.sleep(1)
        return last

    def goto(self, url, wait=9):
        self.send("Page.navigate", url=url)
        time.sleep(wait)

    def clear_cookies(self):
        """清空浏览器 cookie（用于切换账户）。

        该 Chrome profile 专用于通义听悟，全清无副作用。
        """
        self.send("Network.clearBrowserCookies")
        return True

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def close_debug_browser():
    """优雅关闭整个调试 Chrome（browser 级 CDP 命令），释放常驻进程。"""
    try:
        ver = json.loads(http("/json/version") or "{}")
        ws_url = ver.get("webSocketDebuggerUrl")
        if not ws_url:
            return False
        log("  正在关闭调试 Chrome（释放常驻进程）…")
        ws = websocket.create_connection(ws_url, timeout=15)
        try:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                ws.recv()
            except Exception:
                pass
        finally:
            ws.close()
        return True
    except Exception as e:
        log(f"  （关闭调试 Chrome 失败，可手动关闭：{str(e)[:80]}）")
        return False
