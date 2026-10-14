#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从通义听悟批量导出「转写稿全文」为本地 markdown（只取文字，不下载音频）。

流程：
  登录态校验 → 分页拉任务列表(getTransList) → 逐条取全文(getTransResult)
  → 解析段落/说话人 → 渲染 .md（带 YAML front-matter）→ 落 00 待分类/<账户>/

幂等：state/export_state.json 按账户记录 done/failed/skipped，三重判定跳过，可断点续跑。
防风控：每条间隔 1.2–2.6s 随机 + 每 40 条长休 20s + 连续 3 条失败熔断 + 会话失效立即停。

用法：
  bash run.sh --account acc1 --status          # 只看任务状态分布
  bash run.sh --account acc1 --limit 5 --apply # 小批量试跑
  bash run.sh --account acc1 --apply           # 全量下载
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_helper import Page, log

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SAMPLES = SKILL / "samples"
STATE_DIR = SKILL / "state"
STATE_FILE = STATE_DIR / "export_state.json"
MANIFEST = STATE_DIR / "manifest.csv"
LOGS = SKILL / "logs"

KB_ROOT = Path(os.environ.get("KB_ROOT", "./知识库"))
INBOX = KB_ROOT / "00 待分类"

SLEEP_MIN, SLEEP_MAX = 1.2, 2.6
PAUSE_EVERY, PAUSE_SEC = 40, 20
MAX_ATTEMPTS = int(os.environ.get("TW_MAX_ATTEMPTS", "3"))
PAGE_SIZE = int(os.environ.get("TW_PAGE_SIZE", "200"))

# ---------------------------------------------------------------- JS 片段
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
    items: d.map(x => ({transId:x.transId, name:(x.tag&&x.tag.showName)||x.name||'',
                        status:x.status, showTime:x.showTime||'',
                        duration:x.duration||0, wordCount:x.wordCount||0,
                        dirId:(x.dir&&x.dir.dirId)||0}))};
})(%d, %d)
"""

JS_RESULT = """
(async (tid) => {
  const r = await fetch('https://tingwu.aliyun.com/api/trans/getTransResult?c=web',
    {method:'POST', credentials:'include',
     headers:{'Content-Type':'application/json','Referer':'https://tingwu.aliyun.com/home'},
     body: JSON.stringify({action:'getTransResult', version:'1.0', transId:tid})});
  const j = await r.json().catch(() => ({}));
  return {http:r.status, code:j.code, msg:j.message, data:j.data||null};
})(%s)
"""


# ---------------------------------------------------------------- 工具
def safe_name(s, limit=80):
    """文件名安全化。macOS 中文 NFD/NFC 必须归一化，否则'文件存在但找不到'。"""
    s = unicodedata.normalize("NFC", str(s or ""))
    s = re.sub(r'[/\\:*?"<>|\x00-\x1f\r\n\t]', "_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit] or "未命名"


def mmss(ms):
    ms = int(ms or 0)
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_time(v):
    """听悟列表里的 showTime 是**毫秒时间戳**，统一格式化成可读的 'YYYY-MM-DD HH:MM'。"""
    if v in (None, ""):
        return ""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v)
    if n > 10 ** 11:  # 毫秒 → 秒
        n //= 1000
    if n <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(n))


def account_label(data):
    """从 user/info 的 data 里拼出可识别的账户标签。

    实测听悟**没有** showName / userNick，真实标识是 aliyunUserName + 手机号。
    """
    d = data or {}
    base = (d.get("showName") or d.get("userNick") or d.get("aliyunUserName")
            or d.get("email") or "")
    phone = d.get("phone") or ""
    if base and phone:
        return f"{base} ({phone})"
    return base or phone or ""


def save_state(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"accounts": {}}


# ---------------------------------------------------------------- 解析（防御式，兼容多种结构）
def extract_paragraphs(parsed):
    """→ [{'speaker': str, 'sentences': [{'t': ms, 'text': str}]}]"""
    out = []

    def walk_pg(pg):
        for p in pg or []:
            if not isinstance(p, dict):
                continue
            sc = p.get("sc") or []
            spk = p.get("ui")
            if spk is None and sc and isinstance(sc[0], dict):
                spk = sc[0].get("si")
            sents = [{"t": c.get("bt", 0), "text": c.get("tc", "") or ""}
                     for c in sc if isinstance(c, dict)]
            if sents:
                out.append({"speaker": str(spk if spk is not None else "未知"),
                            "sentences": sents})

    if isinstance(parsed, dict):
        if isinstance(parsed.get("pg"), list):
            walk_pg(parsed["pg"])
        elif isinstance(parsed.get("paragraphs"), list):
            walk_pg(parsed["paragraphs"])
        else:
            keys = sorted([k for k in parsed.keys() if str(k).isdigit()],
                          key=lambda k: int(k))
            hit = False
            for k in keys:
                if isinstance(parsed[k], list):
                    walk_pg(parsed[k])
                    hit = True
            if not hit:
                for key in ("sentenceList", "sentences", "segments"):
                    if isinstance(parsed.get(key), list):
                        out.append({
                            "speaker": "未知",
                            "sentences": [{"t": c.get("bt", c.get("bg", 0)),
                                           "text": c.get("text", c.get("tc", "")) or ""}
                                          for c in parsed[key] if isinstance(c, dict)]})
                        hit = True
                        break
    elif isinstance(parsed, list):
        walk_pg(parsed)
    return out


def total_words(paras):
    return sum(len(s["text"]) for p in paras for s in p["sentences"])


# ---------------------------------------------------------------- 渲染
def render_md(fm, paras):
    head = ["---"]
    for k in ("title", "source", "trans_id", "account", "account_name",
              "original_name", "show_time", "dir_id", "duration_sec", "word_count",
              "speakers", "ingest_at", "status"):
        v = fm.get(k)
        head.append(f"{k}: " + (json.dumps(v, ensure_ascii=False) if v is not None else '""'))
    head.append("---")

    spk_n = len(fm.get("speakers") or [])
    body = [
        f'# {fm["title"]}', "",
        f'> 来源：通义听悟 · {fm["account"]} · 转写 ID `{fm["trans_id"]}`'
        f' · 时长 {mmss((fm.get("duration_sec") or 0) * 1000)}'
        f' · {fm.get("word_count", 0)} 字 · {spk_n} 位发言人', "",
    ]
    if fm.get("original_name"):
        extra = f' · 转写时间 {fm["show_time"]}' if fm.get("show_time") else ""
        body += [f'> 原始文件：{fm["original_name"]}{extra}', ""]
    body += ["## 全文转写", ""]
    for p in paras:
        times = [s["t"] for s in p["sentences"] if s.get("t") is not None]
        stamp = f'[{mmss(min(times))}] ' if times else ""
        body.append(f'【说话人{p["speaker"]}】{stamp}'
                    + "".join(s["text"] for s in p["sentences"]))
        body.append("")
    return "\n".join(head + [""] + body)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="账户标签，如 acc1")
    ap.add_argument("--apply", action="store_true", help="真正下载（不加则只预览）")
    ap.add_argument("--status", action="store_true", help="只看任务状态分布")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    ap.add_argument("--dump-raw", type=int, default=0, help="额外保存前 N 条原始 JSON")
    a = ap.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)

    st = load_state()
    acc = st["accounts"].setdefault(
        a.account, {"label": a.account, "shown_name": "", "done": {},
                    "failed": {}, "skipped": {}})

    pg = Page()
    try:
        pg.goto("https://tingwu.aliyun.com/home", wait=6)
    except Exception as e:
        log(f"  （导航失败，继续：{str(e)[:80]}）")

    info = pg.js(JS_ACCOUNT, tries=3)
    if isinstance(info, dict) and (info.get("code") == "CMN.NotLogin"
                                   or info.get("http") in (401, 403)):
        log("❌ 未登录。请先 bash start_chrome.sh 并在 Chrome 里扫码登录。")
        return 2
    d = (info or {}).get("data") or {}
    acc["shown_name"] = account_label(d)
    log(f"当前账户：{a.account}（听悟账户：{acc['shown_name'] or '未知'}）")

    # 1) 拉全量列表
    items, page_no, total = [], 1, None
    while True:
        r = pg.js(JS_LIST % (page_no, PAGE_SIZE), tries=12)
        if not isinstance(r, dict) or "items" not in r:
            log(f"✗ 第{page_no}页拉取失败：{str(r)[:160]}")
            return 3
        if isinstance(r, dict) and (r.get("code") == "CMN.NotLogin"
                                    or r.get("http") in (401, 403)):
            log("❌ 会话失效，请重新扫码后重跑。")
            return 4
        total = r.get("total", 0)
        items += r["items"]
        log(f"  第{page_no}页：{len(r['items'])} 条（累计 {len(items)}/{total}）")
        if len(items) >= total or not r["items"]:
            break
        page_no += 1
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    log(f"账户 {a.account} 共 {len(items)} 条任务")
    dist = Counter(i["status"] for i in items)
    log(f"  status 分布：{dict(sorted(dist.items()))}（0=成功/1=失败/2=排队/4=转写中）")

    # 只下载「转写成功」（status == 0）的任务；其余（排队/转写中/失败）跳过并记录，
    # 日后转写完成可重跑本命令补齐（幂等，已下载的会跳过）。
    success_items = [i for i in items if i.get("status") == 0]
    pending_items = [i for i in items if i.get("status") != 0]
    for i in pending_items:
        if i.get("transId"):
            acc["skipped"][i["transId"]] = {
                "reason": f"status={i.get('status')}", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    if pending_items:
        log(f"  跳过 {len(pending_items)} 条未完成/失败的任务（日后可重跑补齐）")

    if a.status:
        return 0

    # --limit 作用于「列表前 N 条」而不是「下载 N 条」：这样重跑同一条命令时，
    # 已下载的会被认出并跳过（可验证幂等），而不是又去下新的 N 条。
    window = success_items[:a.limit] if a.limit else success_items
    already = [i for i in window if i.get("transId") in acc["done"]]
    stuck = [i for i in window
             if i.get("transId")
             and i["transId"] not in acc["done"]
             and acc["failed"].get(i["transId"], {}).get("attempts", 0) >= MAX_ATTEMPTS]
    stuck_ids = {s["transId"] for s in stuck}
    todo = [i for i in window
            if i.get("transId")
            and i["transId"] not in acc["done"]
            and i["transId"] not in stuck_ids]
    log(f"本次将下载 {len(todo)} 条 · 已下载过跳过 {len(already)} 条"
        + (f" · 顽固失败跳过 {len(stuck)} 条" if stuck else ""))
    if not a.apply:
        log("（预览模式，未加 --apply，不下载）")
        return 0

    outdir = INBOX / a.account
    outdir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    ok = skip = fail = total_fail = 0
    for n, it in enumerate(todo, 1):
        tid, name = it["transId"], it["name"]
        r = pg.js(JS_RESULT % json.dumps(tid), tries=3)

        if a.dump_raw and n <= a.dump_raw:
            (SAMPLES / f"transresult_{tid}.json").write_text(
                json.dumps({"transId": tid, "listItem": it, "resp": r},
                           ensure_ascii=False, indent=2), encoding="utf-8")

        if not isinstance(r, dict) or r.get("http") in (401, 403) \
                or r.get("code") == "CMN.NotLogin":
            log("❌ 会话失效，请重新扫码后重跑（进度已保存）")
            save_state(st)
            return 4

        data = (r or {}).get("data")
        if not data or not data.get("result"):
            f = acc["failed"].setdefault(tid, {"attempts": 0, "last_err": "", "last_ts": ""})
            f["attempts"] += 1
            f["last_err"] = f'code={r.get("code")} msg={r.get("msg")}'
            f["last_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            fail += 1
            total_fail += 1
            if fail >= 3:
                log("❌ 连续 3 条失败，停止（进度已保存）")
                save_state(st)
                return 5
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
            continue

        try:
            parsed = json.loads(data["result"]) if isinstance(data["result"], str) else data["result"]
        except Exception:
            acc["failed"].setdefault(tid, {"attempts": 0})["attempts"] += 1
            fail += 1
            total_fail += 1
            continue

        paras = extract_paragraphs(parsed)
        if not paras:
            wc = data.get("wordCount") or it.get("wordCount", 0)
            if not wc:
                # 听悟侧就是空的（status=0 但 wordCount=0、result={"sc":[]}）：
                # 多为静音/损坏音频。不算失败、不计入重试，避免每次重跑白试。
                acc["skipped"][tid] = {
                    "reason": "empty_result",
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                skip += 1
                if skip <= 5:
                    log(f"  ⏭ 空稿跳过（听悟无内容）：{name}")
                time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
                continue
            acc["failed"].setdefault(tid, {"attempts": 0})["attempts"] += 1
            fail += 1
            total_fail += 1
            continue

        spk = sorted({p["speaker"] for p in paras})
        fm = {
            "title": name, "source": "通义听悟", "trans_id": tid,
            "account": a.account, "account_name": acc["shown_name"],
            "original_name": name, "show_time": fmt_time(it.get("showTime")),
            "dir_id": it.get("dirId", 0),  # 听悟里的文件夹归属（归类时的强信号）
            "duration_sec": it.get("duration", 0),
            "word_count": data.get("wordCount") or it.get("wordCount", 0),
            "speakers": spk, "ingest_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "success",
        }
        # 文件名唯一性：transId 形如 <8位账户级标识><8位任务唯一ID>，
        # 前 8 位在同一账户内大量重复（同名+同前8位 → 互相覆盖丢稿！），
        # 必须用后 8 位或完整 transId。
        slug = safe_name(name)
        fp = outdir / f'{slug}__{tid[-8:]}.md'
        if fp.exists():  # 极端保险：后 8 位若仍撞车，退回完整 transId
            fp = outdir / f'{slug}__{tid}.md'
        fp.write_text(render_md(fm, paras), encoding="utf-8")
        acc["done"][tid] = {"file": fp.name, "ts": fm["ingest_at"],
                            "words": fm["word_count"]}
        with MANIFEST.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([str(fp), ".md", fp.stat().st_size,
                                    fm["ingest_at"], name, "", "通义听悟",
                                    tid, a.account, "00 待分类"])
        ok += 1
        fail = 0

        if n % 10 == 0:
            el = time.time() - t0
            log(f"  进度 {n}/{len(todo)} ({n / len(todo) * 100:.1f}%) · 成功 {ok}"
                f" · 跳过 {skip} · 失败 {fail} · 预计剩余 {el / n * (len(todo) - n) / 60:.1f} 分钟")
        if n % PAUSE_EVERY == 0:
            time.sleep(PAUSE_SEC)
        else:
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
        if n % 20 == 0:
            save_state(st)

    save_state(st)
    log(f"✅ 完成：成功 {ok} · 跳过 {skip} · 失败 {total_fail} · 耗时 {(time.time() - t0) / 60:.1f} 分钟")
    log(f"   输出目录：{outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
