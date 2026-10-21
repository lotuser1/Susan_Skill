#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
体检：检查 Skill 装对了没有、每个能力能不能被主入口路由到。

用法：
    python3 scripts/doctor.py                # 检查本 Skill 所在的安装位置
    python3 scripts/doctor.py --all          # 检查这台机器上所有 Agent 的 skills 目录
    python3 scripts/doctor.py --path DIR     # 检查指定目录
    python3 scripts/doctor.py --json         # 结构化输出

查七项：
    1. susan 主入口在不在
    2. 有没有多套一层（skills/skills/…）—— 最常见的安装失败
    3. 每个 skill 目录有没有 SKILL.md
    4. frontmatter 能不能解析（首行必须是 ---）
    5. 有没有 ability，且和 susan 能力清单一字不差
    6. 有没有 version
    7. license.json / versions.json 是否和 susan 同层
   --all 时额外比对多处安装的版本是否一致

不做什么：
    不修、不删、不复制。只报告问题和修法。
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELF_ROOT = HERE.parent.parent  # skills/

# 复用 list-skills.py 的 frontmatter 解析，避免两套规则
_spec = importlib.util.spec_from_file_location("list_skills", HERE / "list-skills.py")
_ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ls)

AGENT_DIRS = {
    "workbuddy": Path.home() / ".workbuddy" / "skills",
    "claude": Path.home() / ".claude" / "skills",
    "codex": Path.home() / ".codex" / "skills",
    "codebuddy": Path.home() / ".codebuddy" / "skills",
    "agents": Path.home() / ".agents" / "skills",
}

SKIP = {"__pycache__", ".git", "node_modules", ".DS_Store"}


def issue(level, who, why, fix):
    return {"level": level, "who": who, "why": why, "fix": fix}


def susan_abilities(root: Path):
    """从 susan/SKILL.md 的能力清单表格里提取中文能力名。"""
    p = root / "susan" / "SKILL.md"
    if not p.exists():
        return set()
    names = set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if len(cols) < 3:
            continue
        cell = cols[1].replace("**", "").strip()
        if not cell or cell in ("能力名", "用户可能会这么说") or set(cell) <= set("-: "):
            continue
        names.add(cell)
    return names


def check_root(root: Path):
    """检查一个 skills 目录，返回报告 dict。"""
    report = {
        "root": str(root),
        "exists": root.is_dir(),
        "susan_version": None,
        "license": None,
        "manifest": None,
        "skills": [],
        "issues": [],
    }
    if not root.is_dir():
        report["issues"].append(
            issue("失败", "目录", f"{root} 不存在", "确认改过路径，或先跑 install.py")
        )
        return report

    # 2. 多套一层
    nested = root / "skills"
    if nested.is_dir() and (nested / "susan").is_dir():
        report["issues"].append(
            issue(
                "失败",
                "目录结构",
                "检测到 skills/skills/ 多套了一层，Agent 扫描不到里面的能力",
                "把 skills 文件夹里的内容上移一层，或重跑 install.py（会自动修正）",
            )
        )

    # 1. susan 主入口
    susan_md = root / "susan" / "SKILL.md"
    if not susan_md.exists():
        report["issues"].append(
            issue("失败", "susan 主入口", "没装上", "重新解压安装包，把 susan 文件夹放进来")
        )
    else:
        meta = _ls.parse_frontmatter(susan_md) or {}
        report["susan_version"] = (meta.get("version") or "0.0.0").strip()

    # 7. license / versions 是否同层
    lp = root / "license.json"
    if lp.exists():
        try:
            report["license"] = json.loads(lp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            report["issues"].append(
                issue("失败", "license.json", "文件存在但内容损坏", "重新解压安装包覆盖")
            )
    else:
        report["issues"].append(
            issue(
                "告警",
                "license.json",
                "没和 susan 放在同一层，付费能力会被判成档位不足",
                "从安装包根目录把 license.json 复制到 skills 目录（与 susan 平级）",
            )
        )

    mp = root / "versions.json"
    if mp.exists():
        try:
            report["manifest"] = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            report["manifest"] = None
    else:
        report["issues"].append(
            issue(
                "告警",
                "versions.json",
                "缺版本清单，以后查不出更新",
                "从安装包 skills 目录里复制 versions.json（与 susan 平级）",
            )
        )

    abilities_in_list = susan_abilities(root)

    # 3-6. 逐个 skill 检查
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in SKIP or child.name.startswith("."):
            continue
        if child.name == "susan":
            continue
        # 嵌套结构已经单独报过了，别再把它当成一个"坏技能"重复报一次
        if child.name == "skills" and (child / "susan").is_dir():
            continue
        smd = child / "SKILL.md"
        if not smd.exists():
            report["issues"].append(
                issue(
                    "失败",
                    child.name,
                    "目录在，但里面没有 SKILL.md",
                    "这个技能的文件夹不完整，重新解压安装包覆盖它",
                )
            )
            report["skills"].append({"dir": child.name, "ok": False})
            continue

        meta = _ls.parse_frontmatter(smd)
        if meta is None:
            report["issues"].append(
                issue(
                    "失败",
                    child.name,
                    "SKILL.md 首行不是 ---，整个文件无法解析",
                    "检查文件开头是不是写成了 \\--- 或有多余空行",
                )
            )
            report["skills"].append({"dir": child.name, "ok": False})
            continue

        name = (meta.get("name") or "").strip() or child.name
        ability = (meta.get("ability") or "").strip()
        version = (meta.get("version") or "").strip()
        entry = {"dir": child.name, "name": name, "ability": ability, "version": version, "ok": True}

        if name != child.name:
            report["issues"].append(
                issue(
                    "告警",
                    name,
                    f"name 是 {name}，但目录名是 {child.name}（改了目录名没改 name）",
                    "把两者改成一致，否则调用时会找不到",
                )
            )

        if not ability:
            report["issues"].append(
                issue(
                    "失败",
                    name,
                    "缺 ability 字段，主入口认不出它——用户说了也路由不到",
                    "在 frontmatter 加一行 ability: 中文能力名（与 susan 清单一字不差）",
                )
            )
            entry["ok"] = False
        elif abilities_in_list and ability not in abilities_in_list:
            report["issues"].append(
                issue(
                    "失败",
                    name,
                    f'ability 是「{ability}」，susan 能力清单里没有这个名字',
                    "把 SKILL.md 的 ability 改成清单里的名字，或去清单补一行",
                )
            )
            entry["ok"] = False

        if not version:
            report["issues"].append(
                issue("告警", name, "缺 version 字段，版本检查会当成 v0.0.0", "加一行 version: 1.0.0")
            )

        report["skills"].append(entry)

    # 清单里有、本地没装的（只提示，不算失败）
    local_abilities = {s.get("ability") for s in report["skills"] if s.get("ability")}
    missing = sorted(a for a in abilities_in_list if a not in local_abilities)
    if missing and report["manifest"]:
        listed = {
            v.get("ability")
            for v in (report["manifest"].get("skills") or {}).values()
        }
        should = [a for a in missing if a in listed]
        if should:
            for a in should:
                report["issues"].append(
                    issue(
                        "失败",
                        a,
                        "安装包里有这个能力，但本地目录没找到",
                        "重新解压安装包覆盖，或检查是不是只复制了部分文件夹",
                    )
                )

    report["passed"] = len([i for i in report["issues"] if i["level"] != "失败"])
    report["failed"] = len([i for i in report["issues"] if i["level"] == "失败"])
    return report


def cross_check(reports):
    """--all：比对多处安装，报版本不一致。"""
    installed = [(r["root"], r) for r in reports if r.get("susan_version")]
    if len(installed) < 2:
        return []

    out = []
    versions = {}
    for root, r in installed:
        for s in r["skills"]:
            if s.get("ability") and s.get("version"):
                versions.setdefault(s["ability"], {})[root] = s["version"]
        if r.get("susan_version"):
            versions.setdefault("能力总入口", {})[root] = r["susan_version"]

    for ability, m in versions.items():
        if len(set(m.values())) > 1:
            detail = "；".join(f"{Path(k).parent.name}={v}" for k, v in m.items())
            out.append(
                issue(
                    "告警",
                    ability,
                    f"多处安装的版本不一致：{detail}",
                    "用 install.py --agent <名称> 把其他位置覆盖成同一版本",
                )
            )
    return out


def render(report):
    lines = []
    root = Path(report["root"])
    lines.append(f"安装诊断：{root}")
    which = [k for k, v in AGENT_DIRS.items() if v == root]
    if which:
        lines[0] += f"（{which[0]}）"
    if report.get("susan_version"):
        lines.append(f"主入口：v{report['susan_version']}")
    lic = report.get("license")
    if lic:
        lines.append(
            f"授权：{lic.get('licensee', '未知')} · {lic.get('tier', '?')} · {lic.get('serial', '无编号')}"
        )
    if report.get("manifest"):
        lines.append(f"版本清单：{report['manifest'].get('packed', '未知')}")

    issues = list(report["issues"])
    failed = [i for i in issues if i["level"] == "失败"]
    warns = [i for i in issues if i["level"] == "告警"]
    n_skills = len(report.get("skills") or [])
    lines.append("")

    if not issues:
        lines.append(f"结论：一切正常，{n_skills} 个能力都能被主入口路由到。")
        return "\n".join(lines)

    n_ok = len([s for s in (report.get("skills") or []) if s.get("ok", True)])
    lines.append(
        f"结论：{n_ok} 个能力可用，{len(failed)} 项失败，{len(warns)} 项告警。"
    )
    lines.append("")
    for i in failed:
        lines.append(f"[失败] {i['who']}：{i['why']}")
        lines.append(f"       修法：{i['fix']}")
    for i in warns:
        lines.append(f"[告警] {i['who']}：{i['why']}")
        lines.append(f"       修法：{i['fix']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="检查所有 Agent 的 skills 目录")
    ap.add_argument("--path", help="检查指定目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.path:
        targets = [Path(args.path).expanduser()]
    elif args.all:
        targets = [d for d in AGENT_DIRS.values() if d.is_dir()] or [SELF_ROOT]
    else:
        targets = [SELF_ROOT]

    reports = [check_root(t) for t in targets]
    extra = cross_check(reports) if args.all else []

    if args.json:
        print(
            json.dumps(
                {"reports": reports, "cross": extra},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    for idx, r in enumerate(reports):
        if idx > 0:
            print()
        print(render(r))

    if extra:
        print()
        print("跨目录比对：")
        for i in extra:
            print(f"[{i['level']}] {i['who']}：{i['why']}")
            print(f"       修法：{i['fix']}")


if __name__ == "__main__":
    main()
