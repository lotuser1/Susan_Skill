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


def load_manifest_names():
    """从 versions.json 读安装包里的能力清单。

    用途：frontmatter 坏了、没有 ability 时，靠它判断"这个目录确实是我们家的"，
    从而报"装坏了"而不是静默跳过。
    """
    for root in SCAN_ROOTS:
        p = root / "versions.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            return set((data.get("skills") or {}).keys())
    return set()


def discover():
    """扫描所有 skills 目录，返回 (条目列表, 告警列表)。

    归属判定：只有属于 Susan 体系的 Skill 才会被收录，用户机器上装的
    其他工具箱（比如别家的 skill）不会被误当成 Susan 的能力。
    判定顺序：① frontmatter 有 ability 字段  ② 名字在 official-skills.txt 白名单里
    """
    found = {}
    warnings = []
    broken = []
    official = load_official_names()
    manifest_names = load_manifest_names()

    def is_ours(name):
        """判断某个目录是不是 Susan 体系的。公告名单 + 版本清单双保险。"""
        return name in official or name in manifest_names

    def consider(root: Path):
        if not root or not root.is_dir():
            return
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in SKIP_DIRS or child.name.startswith("."):
                continue
            if child.name == SELF_NAME:
                continue

            smd = child / "SKILL.md"
            meta = parse_frontmatter(smd) if smd.exists() else None

            # 装坏的情况必须说出来，不能静默当成"没装"。
            # 否则学员买了付费能力，却被回答"这是付费内容"——最难排查的一类事故。
            if meta is None:
                if is_ours(child.name):
                    why = (
                        "目录在，但里面没有 SKILL.md"
                        if not smd.exists()
                        else "SKILL.md 解析失败（首行必须是 ---）"
                    )
                    broken.append({"dir": child.name, "why": why})
                continue

            name = meta.get("name") or child.name
            if name == SELF_NAME:
                continue
            if name in found:
                continue

            ability = meta.get("ability", "").strip()
            if not ability:
                # 没有 ability 字段：白名单/版本清单里的才认，并且必须告警
                if not is_ours(name):
                    continue
                warnings.append(f"{name}：SKILL.md 缺 ability 字段，已按调用名兜底收录")
                broken.append({"dir": child.name, "why": "缺 ability 字段，主入口按中文能力名路由不到它"})
                ability = name

            found[name] = {
                "name": name,
                "ability": ability,
                "description": (meta.get("description") or "").strip(),
                "category": meta.get("category", "").strip(),
                "tier": (meta.get("tier") or "free").strip().lower(),
                "version": (meta.get("version") or "").strip(),
                "path": str(smd),
            }

    consider(SELF_SKILLS_ROOT)
    for root in SCAN_ROOTS:
        consider(root)

    return found, warnings, broken


def build(level, license_data, only_ability=None):
    found, warnings, broken = discover()
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
                "version": entry["version"] or "0.0.0",
                "path": entry["path"],
                "description": short,
            }
        )
    return items, warnings, broken


def self_version() -> str:
    """读主入口自身的版本号（本 Skill 目录的 SKILL.md）。"""
    meta = parse_frontmatter(SELF_SKILLS_ROOT / SELF_NAME / "SKILL.md") or {}
    return (meta.get("version") or "0.0.0").strip() or "0.0.0"


def render_text(items, level, license_data, warnings, broken):
    if license_data:
        head = f"授权：{license_data.get('licensee', '未知')} · 档位 {TIER_LABEL.get(str(license_data.get('tier', 'free')).lower(), '免费')} · level {level}"
    else:
        head = "授权：未找到 license.json，按免费用户处理 · level 0"

    lines = [f"主入口版本：v{self_version()}", head, ""]
    if not items:
        lines.append("（本地没有发现任何 Susan 能力）")
        return "\n".join(lines)

    lines.append(f"已安装 {len(items)} 个能力：")
    lines.append("")
    for it in items:
        flag = "可用" if it["usable"] else "档位不足"
        lines.append(
            f"- 能力名：{it['ability']}  [{flag} · {it['tier_label']}]  v{it['version']}"
        )
        lines.append(f"  调用名：{it['name']}")
        lines.append(f"  路径：{it['path']}")
        lines.append(f"  说明：{it['description']}")
        lines.append("")
    lines.append("用法：按「能力名」判断用户要什么，用「调用名」或「路径」去调用它。")
    if broken:
        lines.append("")
        lines.append("装坏的能力（目录在，但主入口用不了）：")
        for b in broken:
            lines.append(f"- {b['dir']}：{b['why']}")
        lines.append("  → 告诉用户重新解压安装包覆盖这个目录；或跑 scripts/doctor.py 看完整检查")
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
    items, warnings, broken = build(level, license_data, args.ability)

    if args.json:
        print(
            json.dumps(
                {
                    "level": level,
                    "license": license_data,
                    "items": items,
                    "warnings": warnings,
                    "broken": broken,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if args.ability and not items:
            print(f"本地没有安装「{args.ability}」这个能力。")
            if broken:
                print()
                print(f"但注意到有 {len(broken)} 个能力目录装坏了，可能就是你问的这个：")
                for b in broken:
                    print(f"- {b['dir']}：{b['why']}")
                print("→ 先让用户重新解压安装包覆盖这些目录，再确认能不能用。")
                return
            print("它可能需要单独获取，或者根本没装。按 susan 路由表里的档位给出对应引导。")
            return
        print(render_text(items, level, license_data, warnings, broken))


if __name__ == "__main__":
    main()
