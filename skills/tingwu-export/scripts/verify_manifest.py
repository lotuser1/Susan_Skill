#!/usr/bin/env python3
"""
校验 manifest.csv / export_state.json / 实际文件 三者一致性。

为什么需要它：早期版本用 transId 的**前 8 位**做文件名后缀，但 transId 结构是
「前 8 位账户级标识 + 后 8 位任务唯一 ID」，前 8 位同账户内大量重复 —— 只要两条任务
原始名相同，后写的就会覆盖先写的，静默丢稿。本脚本专门把这种「覆盖丢失」揪出来。

  python3 scripts/verify_manifest.py                 # 只诊断（只读，安全）
  python3 scripts/verify_manifest.py --fix           # 摘掉丢失条目（从 done 与 manifest），使其可重下
  python3 scripts/verify_manifest.py --account acc2  # 只查某账户
"""
import argparse
import csv
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_transcripts import INBOX, MANIFEST, STATE_FILE  # noqa: E402


def read_manifest():
    rows = []
    if not MANIFEST.exists():
        return rows
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        for r in csv.reader(f):
            if r:
                rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="摘掉被覆盖丢失的条目（从 done 与 manifest 移除），使其可被重下")
    ap.add_argument("--account", default="", help="只处理指定账户（如 acc2）")
    a = ap.parse_args()

    rows = read_manifest()
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    accounts = state.get("accounts", {})

    by_path = defaultdict(list)
    for idx, r in enumerate(rows):
        acc = r[8] if len(r) > 8 else "?"
        if a.account and acc != a.account:
            continue
        by_path[(acc, r[0])].append((idx, r))

    lost = []
    for (acc, path), lst in sorted(by_path.items()):
        if len(lst) < 2:
            continue
        # 保留最后写入的一条（= 磁盘上文件的真实内容），其余视为被覆盖丢失
        for idx, r in lst[:-1]:
            lost.append((acc, path, r[7] if len(r) > 7 else "", idx))

    print(f"manifest 总行数：{len(rows)}（本次范围 {sum(len(v) for v in by_path.values())}）")
    print(f"唯一路径数：{len(by_path)}")
    print(f"被覆盖丢失的条目：{len(lost)}")
    for acc, path, tid, _ in lost:
        print(f"  ⚠️ [{acc}] tid={tid} 的稿子被覆盖 ← {Path(path).name}")

    print()
    for acc, v in accounts.items():
        if a.account and acc != a.account:
            continue
        d = v.get("done", {})
        outdir = INBOX / acc
        files = {p.name for p in outdir.glob("*.md")} if outdir.exists() else set()
        named = {info.get("file", "") for info in d.values()}
        miss = sorted(named - files)
        print(f"{acc}: done={len(d)} · 实盘文件={len(files)} · done 记录但磁盘缺失={len(miss)}")
        for m in miss[:5]:
            print(f"    缺失：{m}")

    if not a.fix:
        print("\n（诊断模式，未改动任何文件。加 --fix 可摘掉丢失条目以便重下）")
        return 0

    if not lost:
        print("\n无需修复。")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = MANIFEST.with_name(f"{MANIFEST.name}.bak-{ts}")
    shutil.copy2(MANIFEST, bak)
    print(f"\n已备份 manifest → {bak.name}")

    drop_idx = {idx for _, _, _, idx in lost}
    with MANIFEST.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for idx, r in enumerate(rows):
            if idx not in drop_idx:
                w.writerow(r)

    removed = 0
    for acc, _path, tid, _idx in lost:
        if not tid:
            continue
        v = accounts.get(acc)
        if v and tid in v.get("done", {}):
            del v["done"][tid]
            removed += 1
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已从 manifest 删除 {len(drop_idx)} 行、从 done 摘掉 {removed} 个 tid。")
    print("下一步：重跑 bash run.sh --account <acc> --apply 补齐"
          "（新命名用 transId 后 8 位，不会覆盖已下载文件）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
