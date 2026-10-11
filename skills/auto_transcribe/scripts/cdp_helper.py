#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CDP 驱动：直连已开启调试端口的可见 Chrome（默认 9222），用于操作通义听悟网页版。

之所以不用 agent-browser 之类自建无头会话的工具：它们会抢占 about:blank 标签页、
且没有你的登录态。这里直接对已登录的真实 Chrome 发 CDP 命令，登录态天然保留。

所有页面 DOM 操作都放在 tw_helpers.js 里（window.__tw），Python 只负责发 CDP 命令，
这样彻底避开 Python 字符串里嵌 JS 的转义地狱。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import websocket

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("TINGWU_CHROME_PORT", "9222"))

# 确保 CDP 直连 127.0.0.1，绕过本机可能存在的 HTTP 代理（如 WorkBuddy 本地代理），
# 否则 curl / websocket 连 127.0.0.1:PORT 会被代理劫持，导致连不上 Chrome。
# 本 Python 进程只连本机 CDP，从不直连外网（外网请求由已登录的 Chrome 发起），
# 因此直接清空代理环境变量最彻底，也避免 websocket-client 不读 no_proxy 的问题。
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
    """找到真正的网页标签页（排除 chrome:// 内部页），优先听悟页面"""
    tabs = json.loads(http("/json/list") or "[]")
    pages = [t for t in tabs
             if t.get("type") == "page"
             and not t.get("url", "").startswith(("chrome://", "chrome-untrusted", "devtools://"))]
    if not pages:
        raise RuntimeError("Chrome 里没有可操作标签页")
    for p in pages:
        if "tingwu.aliyun.com" in p.get("url", ""):
            return p
    return pages[0]


class Page:
    """极简 CDP 客户端（每个实例对应一个已连接的页面）"""

    def __init__(self, ws_url=None):
        if ws_url is None:
            pg = find_page()
            ws_url = pg["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(ws_url, timeout=240)
        self.i = 0
        # 开启 Page 域以接收 javascriptDialogOpening 事件（离开页面确认框等）
        try:
            self.send("Page.enable")
        except Exception:
            pass
        self._inject()

    def _auto_dismiss_dialog(self, accept=False):
        """自动处理浏览器弹窗。beforeunload(离开页面) 放行导航(accept=True)，否则关闭。"""
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
            # 事件（无 id）：处理浏览器弹窗，避免阻塞导航/脚本
            if "method" in r and "id" not in r:
                if r.get("method") == "Page.javascriptDialogOpening":
                    params = r.get("params", {})
                    dtype = params.get("type")
                    # beforeunload（离开页面确认框）：必须放行导航(去首页上传)，否则 open_panel 卡死；
                    # 其它类型弹窗：直接关闭，避免阻塞。
                    accept = (dtype == "beforeunload")
                    log(f"  [自动处理弹窗] 类型={dtype} → {'放行(继续导航)' if accept else '已关闭'}")
                    self._auto_dismiss_dialog(accept)
                continue
            if r.get("id") == mid:
                if "error" in r:
                    raise RuntimeError(f"{m}: {str(r['error'])[:200]}")
                return r.get("result", {})

    def js(self, expr, tries=1):
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

    def _inject(self):
        """把 tw_helpers.js 注入页面，定义 window.__tw；并抑制 beforeunload 离开确认框。"""
        # 抑制「离开此页面」确认框：
        #   1) 清空 window.onbeforeunload 属性（覆盖属性式处理器）
        #   2) 捕获阶段拦截 beforeunload 事件，阻止页面其它监听器触发弹窗
        # 这样 open_panel 切回首页的导航能正常完成，又不会打扰用户（根本不弹框）。
        # 注意：捕获阶段监听且不调用 preventDefault / 不设 returnValue，浏览器就不会弹确认框。
        suppressor = (
            "(()=>{try{"
            "window.onbeforeunload=null;"
            "window.addEventListener('beforeunload',function(e){"
            "e.stopImmediatePropagation();"
            "},true);"
            "}catch(_){}})()"
        )
        self.js(suppressor)
        code = (HERE / "tw_helpers.js").read_text(encoding="utf-8")
        self.js(f"(() => {{ {code} }})()")

    def ensure_helpers(self):
        if not self.js("typeof window.__tw !== 'undefined'"):
            self._inject()

    def goto(self, url, wait=9):
        self.send("Page.navigate", url=url)
        time.sleep(wait)
        self.ensure_helpers()

    # ---- 具体操作（封装在 window.__tw 里） ----
    def click(self, txt, contains=True, require_all=None, max_width=None):
        """
        点击元素。**必须走 DOM 原生 click**：
        CDP 的 Input.dispatchMouseEvent 合成点击在听悟首页卡片上无效
        （返回 True 但弹层不开），React 的事件只认原生 el.click()。
        """
        self.ensure_helpers()
        opts = {"exact": not contains}
        if require_all:
            opts["requireAll"] = require_all
        if max_width:
            opts["maxWidth"] = max_width
        r = self.js("window.__tw.nativeClick(%s, %s)"
                    % (json.dumps(txt, ensure_ascii=False), json.dumps(opts)))
        return bool(r and r.get("ok"))

    def text(self):
        self.ensure_helpers()
        return self.js("window.__tw.text()") or ""

    def panel_ready(self):
        self.ensure_helpers()
        return bool(self.js("window.__tw.panelReady()"))

    def file_inputs(self):
        doc = self.send("DOM.getDocument", depth=1)
        return self.send("DOM.querySelectorAll", nodeId=doc["root"]["nodeId"],
                         selector="input[type=file]").get("nodeIds", [])

    def attach(self, paths):
        nids = self.file_inputs()
        if not nids:
            return False, "找不到 file input"
        # 过滤掉不存在的路径，避免整个 setFileInputFiles 失败
        exist = [p for p in paths if os.path.exists(p)]
        if len(exist) != len(paths):
            log(f"  ⚠️ {len(paths) - len(exist)} 个路径不存在，已忽略")
        if not exist:
            return False, "无可用文件"
        self.send("DOM.setFileInputFiles", files=exist, nodeId=nids[0])
        return True, f"已投喂 {len(exist)} 个文件"

    def pick_multi(self):
        self.ensure_helpers()
        r = self.js("window.__tw.pickMultiDiscussion()")
        time.sleep(1.2)
        on = bool(self.js("window.__tw.isMultiDiscussionOn()"))
        return r, on

    def chosen_count(self):
        self.ensure_helpers()
        return self.js("window.__tw.fileCountText()")

    def panel_names(self):
        self.ensure_helpers()
        return self.js("window.__tw.panelNames()") or []


def close_debug_browser():
    """优雅关闭整个调试 Chrome（browser 级 CDP 命令），用于上传任务结束后释放常驻进程。

    这样界面就不会一直显示「1 个后台任务运行中」。
    返回 True/False；失败时只记日志、不抛异常，避免阻塞主流程。
    """
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
                pass  # 关闭过程中连接可能已断，忽略
        finally:
            ws.close()
        return True
    except Exception as e:
        log(f"  （关闭调试 Chrome 失败，可手动关闭：{str(e)[:80]}）")
        return False


def open_panel(pg_ref, folder_url="https://tingwu.aliyun.com/"):
    """
    首页 → 上传音视频 → 上传本地音视频文件 → 面板就绪。

    注意：点击会触发 SPA 路由变化，CDP 的 WebSocket 会随之失效
    （报 Inspected target navigated or closed），所以每一步都要重建 Page。
    pg_ref 是 [Page] 单元素列表，便于外部复用同一实例。
    """
    for attempt in range(5):
        try:
            pg = Page()
            pg.goto(folder_url, wait=8)
            pg_ref[0] = pg
        except Exception as e:
            log(f"  (第{attempt+1}次) 连接页面失败: {str(e)[:60]}")
            time.sleep(3)
            continue

        # 首页「上传音视频」卡片：用多个特征词一起定位整张卡片
        if not pg.click("上传音视频",
                        require_all=["上传音视频", "音视频转文字", "区分发言人"],
                        max_width=300):
            log(f"    (第{attempt+1}次) 未点到上传卡片")
            time.sleep(2)
            continue

        time.sleep(4)
        try:
            pg = Page()
            pg.ensure_helpers()
            pg_ref[0] = pg
        except Exception:
            time.sleep(2)
            continue

        if not pg.click("上传本地音视频文件"):
            time.sleep(2)
            continue

        time.sleep(4)
        try:
            pg = Page()
            pg.ensure_helpers()
            pg_ref[0] = pg
        except Exception:
            time.sleep(2)
            continue

        if pg.panel_ready():
            return pg
        time.sleep(2)

    return None
