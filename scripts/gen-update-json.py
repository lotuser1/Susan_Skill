#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成公开仓库的远端版本清单 UPDATE.json。

用法：
    python3 scripts/gen-update-json.py --note "合规词库新增视频号限流词 32 个"

做什么：
    扫描 skills/*/SKILL.md，把**免费层**能力的版本号写进 UPDATE.json。
    学员端跑 `check-update.py --online` 时拉的就是这个文件。

不做什么：
    不写任何付费能力的信息。tier 不是 free 的一律跳过并提示，
    避免 3980 / 29800 的能力清单出现在公开仓库里。
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
OUT = ROOT / "UPDATE.json"


def parse_frontmatter(skill_md: Path):
    if not skill_md.exists():
        return None
    lines = skill_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines or lines[0].strip() not in ("---", "\\---"):
        return None
    meta = {}
    for line in lines[1:]:
        if line.strip() in ("---", "\\---", "..."):
            break
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1).strip()] = m.group(2).strip().strip("'\"")
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", default="", help="本次更新说明，会显示在学员的检查结果里")
    ap.add_argument("--date", help="覆盖发版日期，默认今天")
    args = ap.parse_args()

    if not SKILLS.is_dir():
        sys.exit(f"[错误] 找不到 {SKILLS}")

    today = args.date or datetime.now().strftime("%Y-%m-%d")
    skills = {}
    entry = None
    for child in sorted(SKILLS.iterdir()):
        if not child.is_dir():
            continue
        meta = parse_frontmatter(child / "SKILL.md")
        if not meta or not meta.get("name"):
            continue
        name = meta["name"]
        tier = (meta.get("tier") or "free").strip().lower()
        if tier != "free":
            print(f"  [跳过] {name}：tier={tier}，付费能力不进公开清单")
            continue
        item = {
            "ability": meta.get("ability") or name,
            "version": meta.get("version") or "0.0.0",
            "tier": "free",
        }
        if not meta.get("version"):
            print(f"  [警告] {name}：缺 version 字段，已记为 0.0.0")
        if name == "susan":
            # 主入口没有 ability 字段（它不参与路由），这里固定显示给人看的名字
            entry = {"ability": "能力总入口", "version": item["version"], "tier": "free"}
        else:
            skills[name] = item

    data = {"updated": today, "notes": args.note, "skills": skills}
    if entry:
        data["susan"] = entry

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  已写入 {OUT.relative_to(ROOT)}")
    print(f"  发版日期：{today}   免费能力：{len(skills)} 个   主入口：v{entry['version'] if entry else '?'}")
    print("  提交并 push 后，学员端 --online 才能拉到。")


if __name__ == "__main__":
    main()
