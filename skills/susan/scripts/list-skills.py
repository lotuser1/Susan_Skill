#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发现本地已安装的 Susan 能力。

用法：
    python3 scripts/list-skills.py                    # 列出全部已装能力
    python3 scripts/list-skills.py --ability 合规文案审查   # 只查一个能力
    python3 scripts/list-skills.py --json             # 结构化输出

做什么：
    扫描 skills 目录，读取**实际安装版本**的 SKILL.md frontmatter，
    产出「中文能力名 → 调用名 / 说明 / 真源路径」的对照表。
    同时读 license.json 判断当前用户被授权的档位。

不做什么：
    不做路由判断。判断该用哪个能力是 susan 的事，这里只负责"你装了什么"。
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 本文件位于 skills/susan/scripts/，上两级即 skills/ 根目录
SELF_SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent

SCAN_ROOTS = [
    SELF_SKILLS_ROOT,
    Path.home() / ".workbuddy" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".codebuddy" / "skills",
    Path.home() / ".agents" / "skills",
]

TIER_LEVEL = {"free": 0, "core": 1, "pro": 2, "vip": 3}
TIER_LABEL = {
    "free": "免费",
    "core": "299",
    "pro": "3980",
    "vip": "29800",
}

SELF_NAME = "susan"
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
DESC_LIMIT = 100


def parse_frontmatter(skill_md: Path):
    """解析 SKILL.md 的 YAML frontmatter。只取需要的字段，不做完整 YAML 解析。"""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() not in ("---", "\\---"):
        return None
    meta = {}
    for line in lines[1:]:
        s = line.strip()
        if s in ("---", "\\---", "..."):
            break
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", s)
        if m:
            meta[m.group(1).strip()] = m.group(2).strip().strip("'\"")
    return meta


def load_license_level():
    """读 skills 根目录下的 license.json，拿授权档位。找不到按免费用户处理。"""
    for root in SCAN_ROOTS:
        p = root / "license.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            lv = data.get("level")
            if isinstance(lv, int):
                return lv, data
            key = str(data.get("tier", "free")).lower()
            return TIER_LEVEL.get(key, 0), data
    return 0, None


def load_official_names():
    """兜底白名单：漏写 ability 字段时靠它兜住。"""
    p = Path(__file__).resolve().parent.parent / "references" / "official-skills.txt"
    if not p.exists():
        return set()
    return {
        ln.strip()
        for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


def discover():
    """扫描所有 skills 目录，返回 (条目列表, 告警列表)。

    归属判定：只有属于 Susan 体系的 Skill 才会被收录，用户机器上装的
    其他工具箱（比如别家的 skill）不会被误当成 Susan 的能力。
    判定顺序：① frontmatter 有 ability 字段  ② 名字在 official-skills.txt 白名单里
    """
    found = {}
    warnings = []
    official = load_official_names()

    def consider(root: Path):
        if not root or not root.is_dir():
            return
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in SKIP_DIRS or child.name.startswith("."):
                continue
            if child.name == SELF_NAME:
                continue
            meta = parse_frontmatter(child / "SKILL.md")
            if not meta:
                continue
            name = meta.get("name") or child.name
            if name == SELF_NAME:
                continue
            if name in found:
                continue

            ability = meta.get("ability", "").strip()
            if not ability:
                # 没有 ability 字段：只有白名单里的才认，且告警
                if name not in official:
                    continue
                warnings.append(f"{name}：SKILL.md 缺 ability 字段，已按调用名兜底收录")
                ability = name

            found[name] = {
                "name": name,
                "ability": ability,
                "description": (meta.get("description") or "").strip(),
                "category": meta.get("category", "").strip(),
                "tier": (meta.get("tier") or "free").strip().lower(),
                "path": str(child / "SKILL.md"),
            }

    consider(SELF_SKILLS_ROOT)
    for root in SCAN_ROOTS:
        consider(root)

    return found, warnings


def build(level, license_data, only_ability=None):
    found, warnings = discover()
    items = []
    for entry in sorted(found.values(), key=lambda x: (TIER_LEVEL.get(x["tier"], 0), x["ability"])):
        tier = entry["tier"]
        tl = TIER_LEVEL.get(tier, 0)
        usable = tl <= level
        if only_ability and entry["ability"] != only_ability and entry["name"] != only_ability:
            continue
        desc = entry["description"]
        short = desc[:DESC_LIMIT] + ("…" if len(desc) > DESC_LIMIT else "")
        items.append(
            {
                "ability": entry["ability"],
                "name": entry["name"],
                "tier": tier,
                "tier_label": TIER_LABEL.get(tier, tier),
                "level": tl,
                "usable": usable,
                "path": entry["path"],
                "description": short,
            }
        )
    return items, warnings


def render_text(items, level, license_data, warnings):
    if license_data:
        head = f"授权：{license_data.get('licensee', '未知')} · 档位 {TIER_LABEL.get(str(license_data.get('tier', 'free')).lower(), '免费')} · level {level}"
    else:
        head = "授权：未找到 license.json，按免费用户处理 · level 0"

    lines = [head, ""]
    if not items:
        lines.append("（本地没有发现任何 Susan 能力）")
        return "\n".join(lines)

    lines.append(f"已安装 {len(items)} 个能力：")
    lines.append("")
    for it in items:
        flag = "可用" if it["usable"] else "档位不足"
        lines.append(f"- 能力名：{it['ability']}  [{flag} · {it['tier_label']}]")
        lines.append(f"  调用名：{it['name']}")
        lines.append(f"  路径：{it['path']}")
        lines.append(f"  说明：{it['description']}")
        lines.append("")
    lines.append("用法：按「能力名」判断用户要什么，用「调用名」或「路径」去调用它。")
    if warnings:
        lines.append("")
        lines.append("注意：")
        lines += [f"- {w}" for w in warnings]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ability", help="只查某个中文能力名或调用名")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    level, license_data = load_license_level()
    items, warnings = build(level, license_data, args.ability)

    if args.json:
        print(
            json.dumps(
                {"level": level, "license": license_data, "items": items, "warnings": warnings},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if args.ability and not items:
            print(f"本地没有安装「{args.ability}」这个能力。")
            print("它可能需要单独获取，或者根本没装。按 susan 路由表里的档位给出对应引导。")
            return
        print(render_text(items, level, license_data, warnings))


if __name__ == "__main__":
    main()
