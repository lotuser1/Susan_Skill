#!/usr/bin/env python3
"""
生成「归类复核清单」Markdown，供人工确认后再移动文件。

清单包含三部分：
  1. 汇总表：各板块篇数/占比
  2. 分板块抽样：每板块 N 篇，含判定理由 + 正文开头，便于抽查归类是否靠谱
  3. 需人工兜底：低置信(0<conf<0.6) + 无命中(08无法鉴别) 的完整文件名列表

用法：
    python3 scripts/render_review.py --plan state/plan.json --per 6
    python3 scripts/render_review.py --plan state/plan.json --out /path/to/xxx.md
"""
import os
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SKILL = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path(os.environ.get("KB_ROOT", "./知识库")) / "_归类复核清单.md"

ORDER = ["01 流量获客", "02 聊客成交", "03 美学设计", "04 医学材料学",
         "05 医疗售后", "06 团队搭建", "07 商业认知", "08 无法鉴别"]
FM = re.compile(r"^---\n.*?\n---\n", re.S)


def head_of(path, n=110):
    """取正文开头（跳过 front-matter 与 H1/引用行）。"""
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    txt = FM.sub("", txt, count=1)
    lines = [ln for ln in txt.splitlines()
             if ln.strip() and not ln.startswith(("#", ">", "##"))]
    body = " ".join(lines)
    body = re.sub(r"【说话人\d+】\[[^\]]*\]", " ", body)
    return re.sub(r"\s+", " ", body).strip()[:n]


def meta_of(path):
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:1200]
    except Exception:  # noqa: BLE001
        return "", ""
    wc = re.search(r"^word_count:\s*(\d+)", head, re.M)
    sp = re.search(r"^speakers:\s*\[([^\]]*)\]", head, re.M)
    n_sp = len([x for x in sp.group(1).split(",") if x.strip()]) if sp else 0
    return (f"{int(wc.group(1)):,} 字" if wc else ""), (f"{n_sp}人" if n_sp else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=str(SKILL / "state" / "plan.json"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--per", type=int, default=6, help="每板块抽样篇数")
    a = ap.parse_args()

    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    by = defaultdict(list)
    for p in plan:
        by[p["target"]].append(p)

    total = len(plan)
    L = []
    L.append("# 医美录音文稿 · 归类复核清单\n")
    L.append(f"> 共 **{total}** 篇待归类 · 生成于人工确认前 · "
             f"确认后才会移动文件（可 `--undo` 回滚）\n")

    L.append("\n## 一、汇总\n")
    L.append("| 板块 | 篇数 | 占比 |")
    L.append("|---|---:|---:|")
    for k in sorted(by, key=lambda x: -len(by[x])):
        v = len(by[k])
        L.append(f"| {k} | {v} | {v / total * 100:.1f}% |")

    conf = Counter()
    for p in plan:
        c = p["confidence"]
        conf["关键词可信(≥0.6)" if c >= 0.6 else
             ("无命中(08无法鉴别)" if c == 0 else "低置信(待复核)")] += 1
    L.append("\n**判定置信度**\n")
    L.append("| 档位 | 篇数 | 占比 |")
    L.append("|---|---:|---:|")
    for k, v in sorted(conf.items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} | {v / total * 100:.1f}% |")

    L.append("\n## 二、分板块抽样（请重点看这部分）\n")
    L.append("每个板块抽若干篇，附**判定依据**和**正文开头**。"
             "扫一眼开头就能判断归得对不对。\n")
    for cat in ORDER:
        if cat not in by:
            continue
        items = by[cat]
        L.append(f"\n### {cat} · {len(items)} 篇\n")
        for p in items[:a.per]:
            wc, sp = meta_of(Path(p["path"]))
            L.append(f"- **{p['name']}**　`{sp}` `{wc}`　置信度 {p['confidence']}")
            L.append(f"  - 依据：{p['reason']}")
            h = head_of(Path(p["path"]))
            if h:
                L.append(f"  - 开头：{h}…")

    weak = [p for p in plan if 0 < p["confidence"] < 0.6]
    none = [p for p in plan if p["confidence"] == 0]
    if weak or none:
        L.append("\n## 三、需人工兜底\n")
        if weak:
            L.append(f"\n### 低置信 {len(weak)} 篇（关键词命中太少，建议 AI 精读或你过目）\n")
            for p in weak[:60]:
                L.append(f"- {p['name']} → 暂判 {p['target']}（{p['confidence']}）{p['reason'][:60]}")
            if len(weak) > 60:
                L.append(f"\n（仅列前 60 篇，完整清单见 `{Path(a.plan).name}`）")
        if none:
            L.append(f"\n### 完全无关键词命中 {len(none)} 篇（已归入 08 无法鉴别）\n")
            for p in none[:60]:
                L.append(f"- {p['name']}")
            if len(none) > 60:
                L.append(f"\n（仅列前 60 篇，完整清单见 `{Path(a.plan).name}`）")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"✅ 已生成复核清单：{out}")
    print(f"   总计 {total} 篇 · 抽样每板块 {a.per} 篇 · "
          f"低置信 {len(weak)} · 无命中 {len(none)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
