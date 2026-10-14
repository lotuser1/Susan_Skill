#!/usr/bin/env python3
"""
回填 dir_id（听悟文件夹归属）到已下载文稿的 front-matter。

为什么需要：dir_id 是「用户在听悟里人工归的文件夹」，是归类时的辅助信号。
早期版本还没带这个字段，已下载的稿子缺它 —— 本脚本只调**列表接口**拿映射再写回本地，
**不需要重新下载全文**（省时省钱）。

用法（必须先扫码登录对应账户，因为要调列表接口）：
    python3 scripts/backfill_dir.py --account acc3
    python3 scripts/backfill_dir.py --account acc3 --dry     # 只看不写
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_helper import Page, log  # noqa: E402
from export_transcripts import INBOX, JS_LIST  # noqa: E402

PAGE_SIZE = 100


def pull_dir_map(pg, account):
    """拉该账户全部任务，返回 {transId: dirId}。"""
    m, total, page = {}, None, 1
    while True:
        r = pg.js(JS_LIST % (page, PAGE_SIZE), tries=3) or {}
        if r.get("code") not in (0, "0", None) and r.get("code") != 200:
            log(f"⚠️ 列表返回异常：{json.dumps(r, ensure_ascii=False)[:200]}")
        if total is None:
            total = r.get("total")
            log(f"账户 {account} 共 {total} 条任务，开始建立 transId→dirId 映射")
        items = r.get("items") or []
        if not items:
            break
        for i in items:
            if i.get("transId"):
                m[i["transId"]] = i.get("dirId", 0)
        if len(m) >= (total or 0) or len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(1.5)
    log(f"映射建立完成：{len(m)} 条")
    return m


def set_fm_field(text, key, value):
    """在 YAML front-matter 里插入/替换一个字段，其余内容原样保留。"""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    fm, rest = text[4:end + 1], text[end + 5:]
    if re.search(rf"^{re.escape(key)}:", fm, re.M):
        fm = re.sub(rf"^{re.escape(key)}:.*$", f"{key}: {value}", fm, flags=re.M)
    else:
        fm = fm.rstrip("\n") + f"\n{key}: {value}\n"
    return "---\n" + fm + "---\n" + rest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="如 acc3")
    ap.add_argument("--dry", action="store_true", help="只看不写")
    a = ap.parse_args()

    outdir = INBOX / a.account
    if not outdir.exists():
        log(f"❌ 目录不存在：{outdir}")
        return 1
    files = sorted(outdir.glob("*.md"))
    log(f"该账户已下载 {len(files)} 个文件")

    pg = Page()
    dm = pull_dir_map(pg, a.account)

    done = skip = miss = 0
    for fp in files:
        try:
            txt = fp.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log(f"  ⚠️ 读取失败 {fp.name}：{str(e)[:80]}")
            continue
        mt = re.search(r"^trans_id:\s*\"?([^\"\n]+)\"?\s*$", txt, re.M)
        if not mt:
            miss += 1
            continue
        tid = mt.group(1).strip()
        if "dir_id:" in txt:
            skip += 1
            continue
        did = dm.get(tid)
        if did is None:
            miss += 1
            continue
        new = set_fm_field(txt, "dir_id", did)
        if new is None:
            miss += 1
            continue
        if not a.dry:
            fp.write_text(new, encoding="utf-8")
        done += 1

    log(f"{'（演练，未写入）' if a.dry else ''}回填 {done} 个 · 已有跳过 {skip} 个 · 未匹配 {miss} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
