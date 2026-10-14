#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 N 条转写稿的**完整**原始响应落到 samples/，供 parse_sample.py 离线校验解析器。

与 probe_api.py 的区别：probe 只存 key 骨架（省流量、便于看结构），
本脚本存完整 result，用于逐字校验解析覆盖率。

用法：
    bash run.sh --dump-samples --dump 3
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_helper import Page, log
from export_transcripts import JS_ACCOUNT, JS_LIST, JS_RESULT, account_label

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-samples", action="store_true", help="（由 run.sh 传入，占位）")
    ap.add_argument("--dump", type=int, default=3, help="落几条完整样本")
    a = ap.parse_args()

    SAMPLES.mkdir(parents=True, exist_ok=True)
    pg = Page()
    try:
        pg.goto("https://tingwu.aliyun.com/home", wait=6)
    except Exception as e:
        log(f"  （导航失败，继续：{str(e)[:80]}）")

    info = pg.js(JS_ACCOUNT, tries=3)
    if isinstance(info, dict) and info.get("code") == "CMN.NotLogin":
        log("❌ 未登录，请先扫码登录。")
        return 2
    d = (info or {}).get("data") or {}
    log(f"当前账户：{account_label(d) or '未知'}")

    lst = pg.js(JS_LIST % (1, 50), tries=10)
    if not isinstance(lst, dict) or not lst.get("items"):
        log(f"❌ 拉列表失败：{str(lst)[:200]}")
        return 3
    items = lst["items"]
    log(f"共 {lst.get('total')} 条任务，本页 {len(items)} 条")

    # 挑一条最短、一条最长、一条中等，覆盖不同长度
    ordered = sorted(items, key=lambda x: x.get("wordCount") or 0)
    picks = []
    if ordered:
        picks.append(ordered[0])
        if len(ordered) > 2:
            picks.append(ordered[len(ordered) // 2])
        picks.append(ordered[-1])
    picks = picks[:a.dump]

    for it in picks:
        tid = it["transId"]
        r = pg.js(JS_RESULT % json.dumps(tid), tries=3)
        data = (r or {}).get("data") or {}
        if not data.get("result"):
            log(f"  ⚠️ {tid} 没取到全文（code={r.get('code') if isinstance(r, dict) else r}）")
            continue
        fp = SAMPLES / f"full_{tid}.json"
        fp.write_text(json.dumps({"transId": tid, "listItem": it, "resp": r},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  ✅ {fp.name}（wordCount={it.get('wordCount')}，"
            f"result 长度 {len(data['result'])}）")
        time.sleep(1.5)

    log("下一步：bash run.sh --parse-samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
