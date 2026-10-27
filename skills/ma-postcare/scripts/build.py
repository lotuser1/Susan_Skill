#!/usr/bin/env python3
"""术后须知生成：从固定的时间轴库出稿，不让模型自由发挥。

用法：
  python3 build.py --list
  python3 build.py --project 水光针 --format wechat --name 王女士
  python3 build.py --project 水光 --format print --doctor 李医生
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "references" / "术后时间轴.json"

DISCLAIMER = "以上是常见情况，具体以主诊医生的当面交代为准。有不舒服随时联系我们。"


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def find_project(data, key):
    key = key.strip()
    for p in data["projects"]:
        if key == p["id"] or key == p["name"]:
            return p
    for p in data["projects"]:
        names = [p["name"]] + p.get("aliases", [])
        for n in names:
            if key in n or n in key:
                return p
    return None


def ref_label(p):
    return f"参考区间：{p['downtime']}（因人而异）"


def render_wechat(p, name, doctor, today):
    head = f"{name or '您'}，这是{p['name']}做完之后的注意事项"
    lines = [head, ""]
    if today:
        lines.append(f"日期：{today}")
        lines.append("")

    lines.append("一、这几天的正常反应")
    for ph in p["phases"]:
        lines.append(f"· {ph['when']}：{'；'.join(ph['expect'])}")
    lines.append("")

    care = []
    for ph in p["phases"]:
        care += ph.get("care", [])
    if care:
        lines.append("二、要做的事")
        for c in care:
            lines.append(f"· {c}")
        lines.append("")

    if p.get("avoid"):
        lines.append("三、先别做的事")
        for a in p["avoid"]:
            lines.append(f"· {a}")
        lines.append("")

    lines.append("四、出现这些情况，联系我们")
    for c in p["contact"]:
        lines.append(f"· {c}")
    lines.append("")

    lines.append(f"五、复诊\n· {p['recheck']}")
    lines.append("")
    tail = DISCLAIMER
    if doctor:
        tail = f"以上是常见情况，具体以{doctor}的当面交代为准。有不舒服随时联系我们。"
    lines.append(tail)
    lines.append("")
    lines.append(ref_label(p))
    return "\n".join(lines)


def render_print(p, name, doctor, today):
    lines = [f"# {p['name']} · 术后须知", ""]
    meta = []
    if name:
        meta.append(f"客户：{name}")
    if today:
        meta.append(f"日期：{today}")
    if doctor:
        meta.append(f"主诊医生：{doctor}")
    if meta:
        lines.append(" | ".join(meta))
        lines.append("")

    lines.append("| 时间 | 常见反应 |")
    lines.append("|---|---|")
    for ph in p["phases"]:
        lines.append(f"| {ph['when']} | {'；'.join(ph['expect'])} |")
    lines.append("")

    care = []
    for ph in p["phases"]:
        care += ph.get("care", [])
    lines.append("## 护理要点（做完打勾）")
    for c in care:
        lines.append(f"- [ ] {c}")
    lines.append("")

    if p.get("avoid"):
        lines.append("## 注意事项")
        for a in p["avoid"]:
            lines.append(f"- {a}")
        lines.append("")

    lines.append("## 出现下列情况请联系我们")
    for c in p["contact"]:
        lines.append(f"- {c}")
    lines.append("")

    lines.append(f"## 复诊\n{p['recheck']}")
    lines.append("")
    tail = DISCLAIMER
    if doctor:
        tail = f"以上是常见情况，具体以{doctor}的当面交代为准。有不舒服随时联系我们。"
    lines.append(f"> {tail}")
    return "\n".join(lines)


def render_moments(p, name, doctor, today):
    first = p["phases"][0]
    expect = "、".join(first["expect"])
    avoid = "；".join(p.get("avoid", [])[:2])
    care = "；".join(first.get("care", [])[:2])
    who = doctor or "主诊医生"
    parts = [f"{p['name']}做完这阵子，{first['when']}出现{expect}都算常见。"]
    if care:
        parts.append(f"{care}，照着做就行。")
    if avoid:
        parts.append(f"{avoid}，这阵子都先别碰。")
    parts.append(f"不舒服或者越来越重，随时联系{who}。")
    parts.append("（以上是常见情况，具体以医生当面交代为准）")
    return "".join(parts) + "\n\n" + ref_label(p)


RENDERERS = {"wechat": render_wechat, "print": render_print, "moments": render_moments}


def main():
    ap = argparse.ArgumentParser(description="生成术后须知")
    ap.add_argument("--project", help="项目名，如 水光针 / 热玛吉 / 双眼皮")
    ap.add_argument("--format", default="wechat", choices=list(RENDERERS), help="输出场景")
    ap.add_argument("--name", default="", help="客户称呼，如 王女士")
    ap.add_argument("--doctor", default="", help="主诊医生姓名")
    ap.add_argument("--date", default="", help="日期，默认今天")
    ap.add_argument("--list", action="store_true", help="列出支持的项目")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    data = load()

    if args.list:
        for p in data["projects"]:
            alias = "、".join(p.get("aliases", [])) or "—"
            print(f"{p['name']:8s} {p['family']:6s} 恢复参考 {p['downtime']:10s} 别名：{alias}")
        return 0

    if not args.project:
        ap.error("请用 --project 指定项目，或用 --list 看支持哪些")

    p = find_project(data, args.project)
    if not p:
        names = "、".join(x["name"] for x in data["projects"])
        sys.exit(f"没有「{args.project}」的时间轴。支持：{names}\n"
                 "遇到没收录的项目，不要自己编时间轴——问 Susan 要不要补进词库。")

    today = args.date or date.today().isoformat()
    text = RENDERERS[args.format](p, args.name, args.doctor, today)

    if args.json:
        print(json.dumps({
            "project": p["name"],
            "family": p["family"],
            "format": args.format,
            "downtime": p["downtime"],
            "text": text,
        }, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
