#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探测通义听悟内部接口（第一次使用必做）。

Step0  查登录身份（判断是否已登录、当前是哪个账户）
Step1  拉列表前 5 条，打印「完整原始对象」确认 transId 字段名
Step2  对候选的取全文接口各试一次，只打印 key 骨架（不打全文）
Step3  把 3 条（短/中/长）原始 JSON 落到 samples/，供 parse_sample.py 离线迭代

用法：
    bash run.sh --probe
    bash run.sh --probe --dump 5      # 落 5 条样本
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_helper import Page, log

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SAMPLES = SKILL / "samples"

JS_ACCOUNT = """
(async () => {
  const r = await fetch('https://tingwu.aliyun.com/api/account/v2/user/info?c=web',
                        {credentials:'include'});
  const j = await r.json().catch(() => ({}));
  return {http: r.status, code: j.code, msg: j.message, data: j.data || {}};
})()
"""

JS_LIST = """
(async (pageNo, pageSize) => {
  const body = {action:'getTransList', version:'1.0', userId:'',
    filter:{status:[],fileTypes:[],beginTime:'',mediaType:'',endTime:'',
            showName:'',read:'',lang:'',shareUserId:'',client:''},
    preview:0, pageNo:pageNo, pageSize:pageSize};
  const r = await fetch('https://tingwu.aliyun.com/api/trans/request?getTransList&c=web',
    {method:'POST', credentials:'include',
     headers:{'Content-Type':'application/json','Referer':'https://tingwu.aliyun.com/home'},
     body:JSON.stringify(body)});
  const j = await r.json().catch(() => ({}));
  const d = j.data || [];
  return {http:r.status, code:j.code, msg:j.message, total:j.total||0,
          rawKeys: d.length ? Object.keys(d[0]) : [],
          firstItem: d[0] || null,
          items: d.map(x => ({transId:x.transId, name:(x.tag&&x.tag.showName)||x.name||'',
                              status:x.status, showTime:x.showTime||'',
                              duration:x.duration||0, wordCount:x.wordCount||0}))};
})(%d, %d)
"""

JS_RESULT_CANDS = """
(async (tid) => {
  const cands = [
    ['A_getTransResult_path', 'https://tingwu.aliyun.com/api/trans/getTransResult?c=web',
        {action:'getTransResult', version:'1.0', transId:tid}],
    ['B_request_getTransResult', 'https://tingwu.aliyun.com/api/trans/request?getTransResult&c=web',
        {action:'getTransResult', version:'1.0', transId:tid}],
    ['C_request_getTransDetail', 'https://tingwu.aliyun.com/api/trans/request?getTransDetail&c=web',
        {action:'getTransDetail', version:'1.0', transId:tid}],
  ];
  const out = [];
  for (const [tag, url, body] of cands) {
    try {
      const r = await fetch(url, {method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json','Referer':'https://tingwu.aliyun.com/home'},
        body: JSON.stringify(body)});
      const j = await r.json().catch(() => null);
      if (!j) { out.push({tag, http:r.status, note:'非 JSON 响应'}); continue; }
      const d = j.data || {};
      let parsed = null, perr = null;
      try { parsed = typeof d.result === 'string' ? JSON.parse(d.result) : d.result; }
      catch (e) { perr = String(e).slice(0,120); }
      out.push({tag, http:r.status, code:j.code, msg:j.message,
        dataKeys: Object.keys(d),
        duration: d.duration, wordCount: d.wordCount, status: d.status,
        resultType: typeof d.result,
        resultLen: typeof d.result === 'string' ? d.result.length : null,
        parsedType: parsed === null ? null : (Array.isArray(parsed) ? 'array' : typeof parsed),
        parsedKeys: (parsed && !Array.isArray(parsed)) ? Object.keys(parsed).slice(0,20) : null,
        perr,
        skeleton: parsed ? JSON.stringify(parsed).slice(0,1500) : null});
    } catch (e) { out.push({tag, err: String(e).slice(0,150)}); }
  }
  return out;
})(%s)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="（由 run.sh 传入，占位）")
    ap.add_argument("--dump", type=int, default=3, help="落几条原始样本到 samples/")
    ap.add_argument("--page-size", type=int, default=5)
    a = ap.parse_args()

    SAMPLES.mkdir(parents=True, exist_ok=True)
    pg = Page()

    # ---------- Step0 登录身份 ----------
    log("Step0：检查登录态…")
    try:
        pg.goto("https://tingwu.aliyun.com/home", wait=6)
    except Exception as e:
        log(f"  （导航失败，继续尝试：{str(e)[:80]}）")
    info = pg.js(JS_ACCOUNT, tries=3)
    if isinstance(info, dict) and (info.get("code") == "CMN.NotLogin"
                                   or info.get("http") in (401, 403)):
        log("❌ 未登录。请先 bash start_chrome.sh，在 Chrome 里扫码登录，再跑探测。")
        return 2
    d = (info or {}).get("data") or {}
    name = d.get("showName") or d.get("userNick") or d.get("aliyunUserName") or "未知"
    log(f"✅ 已登录，账户：{name}")

    # ---------- Step1 列表与 transId ----------
    log("Step1：拉取列表前若干条，确认 transId 字段…")
    lst = pg.js(JS_LIST % (1, a.page_size), tries=10)
    if not isinstance(lst, dict) or "items" not in lst:
        log(f"❌ 列表拉取失败：{str(lst)[:300]}")
        return 3
    log(f"  总数 total={lst.get('total')}，本次返回 {len(lst['items'])} 条")
    log(f"  原始字段 keys：{lst.get('rawKeys')}")
    print("\n----- 第一条完整原始对象（肉眼确认 transId）-----")
    print(json.dumps(lst.get("firstItem"), ensure_ascii=False, indent=2)[:2000])
    print("------------------------------------------------\n")
    print("提取结果：")
    for it in lst["items"]:
        print(f"  transId={it['transId']}  status={it['status']}  "
              f"words={it['wordCount']}  name={it['name']}")

    if not lst["items"]:
        log("❌ 列表为空，无法继续探测。")
        return 4

    # ---------- Step2 试候选取全文接口 ----------
    # 优先挑一条 status 为 0（成功）的；没有就用第一条
    target = next((i for i in lst["items"] if i.get("status") == 0), lst["items"][0])
    tid = target["transId"]
    log(f"Step2：用 transId={tid}（status={target['status']}）试候选取全文接口…")
    cands = pg.js(JS_RESULT_CANDS % json.dumps(tid), tries=6)
    if not isinstance(cands, list):
        log(f"❌ 探测取全文接口失败：{str(cands)[:300]}")
        return 5
    hit = None
    for c in cands:
        print(f"\n--- 候选 {c.get('tag')} ---")
        print(json.dumps({k: v for k, v in c.items() if k != "skeleton"},
                         ensure_ascii=False, indent=2))
        if c.get("skeleton"):
            print(f"  skeleton: {c['skeleton'][:600]}")
        if c.get("dataKeys") and "result" in (c.get("dataKeys") or []) and not c.get("perr"):
            if hit is None:
                hit = c["tag"]
    print(f"\n★ 命中的取全文接口：{hit or '无（需要走 L1 抓包降级）'}\n")

    # ---------- Step3 落样本 ----------
    log(f"Step3：落 {a.dump} 条原始样本到 {SAMPLES}…")
    saved = 0
    for it in lst["items"][:a.dump]:
        t = it["transId"]
        r = pg.js(JS_RESULT_CANDS % json.dumps(t), tries=6)
        # 只保存命中的那一个候选的完整 data，便于离线解析
        want = next((c for c in (r or [])
                     if c.get("tag") == (hit or "A_getTransResult_path")), None)
        payload = want or (r[0] if r else {"transId": t})
        fp = SAMPLES / f"transresult_{t}.json"
        fp.write_text(json.dumps({"transId": t,
                                  "listItem": it,
                                  "probe": payload},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  ✅ {fp.name}（words={it['wordCount']}）")
        saved += 1
        time.sleep(1.5)

    log(f"探测结束。共落 {saved} 条样本。")
    log("下一步：bash run.sh --parse-samples  # 用本地样本校验解析器")
    return 0


if __name__ == "__main__":
    sys.exit(main())
