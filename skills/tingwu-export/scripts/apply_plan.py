#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归档执行器：把 classify.py 生成的 plan.json 执行成真实的文件移动。

    --dry      演练：只统计将发生什么，不移动任何文件（**每次执行前必跑**）
    --apply    真正执行移动，并写 move_log_<ts>.csv
    --undo f   按 move_log 回滚

安全规则：
  - 目标目录已有同名文件 → 追加 __2 / __3，**永不覆盖**
  - plan.json 里 target 非法 → 直接报错中止
  - 低置信(conf<0.6) 默认归入终态兜底「08 无法鉴别」，可用 --low-conf keep 改成原地不动
  - 移动前自动校验：plan 里每个 path 都必须真实存在，否则中止
  - 全程有 move_log，可 --undo 完整回滚
"""
import argparse
import csv
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
DEFAULT_PLAN = SKILL / "state" / "plan.json"

KB_ROOT = Path(os.environ.get("KB_ROOT", "./知识库"))
INDEX = KB_ROOT / "00 待分类" / "_index"

LEGAL = {"00 待分类", "01 流量获客", "02 聊客成交", "03 美学设计",
         "04 医学材料学", "05 医疗售后", "06 团队搭建", "07 商业认知",
         "08 无法鉴别"}
LOW_CONF = 0.6


def resolve_target(dest_dir: Path, src: Path):
    """目标已存在同名 → 追加 __2/__3…，永不覆盖。返回 (目标路径, 是否改名)。"""
    t = dest_dir / src.name
    if not t.exists():
        return t, False
    stem, suffix, i = src.stem, src.suffix, 2
    while True:
        t = dest_dir / f"{stem}__{i}{suffix}"
        if not t.exists():
            return t, True
        i += 1


def build_moves(plan, low_conf_mode):
    """返回 (moves, skipped, renamed_cnt, stats)
    moves: [(src:Path, dest:Path, target, conf)]
    """
    moves, skipped, renamed_cnt = [], [], 0
    stats = Counter()
    for row in plan:
        src = Path(row["path"])
        if not src.exists():
            skipped.append((src.name, "源文件不存在"))
            stats["跳过·源文件不存在"] += 1
            continue
        target = row["target"]
        conf = float(row.get("confidence", 0))
        if target not in LEGAL:
            raise SystemExit(f"❌ 非法 target：{target}（id={row.get('id')}）已中止")
        if conf < LOW_CONF:
            if low_conf_mode == "keep":
                skipped.append((src.name, f"低置信 {conf}（原地不动）"))
                stats[f"跳过·低置信留在待分类"] += 1
                continue
            target = "08 无法鉴别"
        if target == "00 待分类":
            skipped.append((src.name, "目标为待分类，不动"))
            stats["跳过·目标为待分类"] += 1
            continue
        dest_dir = KB_ROOT / target
        dest, changed = resolve_target(dest_dir, src)
        if changed:
            renamed_cnt += 1
        moves.append((src, dest, target, conf))
        stats[f"移动 → {target}"] += 1
    return moves, skipped, renamed_cnt, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", action="store_true", help="（由 run.sh 传入，占位）")
    ap.add_argument("--dry", action="store_true", help="演练，不移动")
    ap.add_argument("--apply", action="store_true", help="真正执行移动")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--low-conf", default="junk", choices=["junk", "keep"],
                    help="低置信(<0.6)怎么办：junk=归入08无法鉴别(默认) / keep=原地不动")
    ap.add_argument("--undo", help="move_log_<ts>.csv 路径")
    a = ap.parse_args()

    if a.undo:
        lp = Path(a.undo)
        rows = list(csv.reader(lp.read_text(encoding="utf-8").splitlines()))
        n = 0
        for src, dest in rows[1:]:
            dest_p, src_p = Path(dest), Path(src)
            if dest_p.exists() and not src_p.exists():
                src_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest_p), str(src_p))
                n += 1
        print(f"✅ 已回滚 {n} 个文件")
        return 0

    if not (a.dry or a.apply):
        ap.print_help()
        return 1

    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    print(f"plan: {a.plan}（{len(plan)} 条）· 低置信策略：{a.low_conf}")

    moves, skipped, renamed_cnt, stats = build_moves(plan, a.low_conf)

    print("\n=== 将发生什么 ===")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"  目标重名将自动改名: {renamed_cnt}")
    if skipped:
        print(f"  跳过: {len(skipped)}" +
              (f"（示例：{skipped[0][0]} — {skipped[0][1]}）" if skipped else ""))

    # 完整性预检：不允许同一目标路径出现两次
    dests = [m[1] for m in moves]
    dup = [d for d, c in Counter(dests).items() if c > 1]
    if dup:
        print(f"\n❌ 检测到 {len(dup)} 个重复目标路径，已中止（示例：{dup[0]}）")
        return 2
    print(f"\n✅ 预检通过：{len(moves)} 个文件，目标路径无冲突")

    if a.dry:
        print("\n（演练模式，未移动任何文件。确认无误后加 --apply 执行）")
        return 0

    INDEX.mkdir(parents=True, exist_ok=True)
    moved, t0 = [], time.time()
    for src, dest, target, conf in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moved.append((str(src), str(dest)))
    lp = INDEX / f"move_log_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    with lp.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([("from_path", "to_path")] + moved)
    print(f"\n✅ 已移动 {len(moved)} 个文件 · 耗时 {time.time() - t0:.0f}s")
    print(f"   回滚日志：{lp}")
    print(f"   回滚命令：python3 scripts/apply_plan.py --undo {lp}")

    # 清理空的账户子目录
    inbox = KB_ROOT / "00 待分类"
    for d in sorted(inbox.iterdir()):
        if d.is_dir() and d.name != "_index" and not any(d.iterdir()):
            d.rmdir()
            print(f"   已清理空目录：{d.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
