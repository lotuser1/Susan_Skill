#!/usr/bin/env python3
"""改写校验：确认「去 AI 味」之后事实没被改坏。

查五件事：数字是否新增、引号原话是否编造、长度是否失控、AI 分是否真的降了、有没有凭空多出诊疗项目。
"""

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import scan_ai  # noqa: E402

NUM_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
LIST_MARK_RE = re.compile(r"(?:^|\n)\s*[0-9]{1,2}[.、)）]")
QUOTE_RE = re.compile(r"「([^」]{2,80})」|“([^”]{2,80})”|\"([^\"]{2,80})\"")
SIBLING_LEXICONS = [
    BASE.parent / "ma-consult-note" / "references" / "项目词表.json",
]


def norm(s):
    return re.sub(r"\s+", "", s)


def numbers_of(text):
    text = LIST_MARK_RE.sub("\n", text)
    return set(NUM_RE.findall(text))


def quotes_of(text):
    out = []
    for m in QUOTE_RE.finditer(text):
        val = next(g for g in m.groups() if g)
        out.append(val.strip())
    return out


def load_treatment_words():
    for p in SIBLING_LEXICONS:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        words = set()
        for key, grp in data.get("groups", {}).items():
            if key == "concern":
                continue
            for w in grp.get("words", []):
                if len(w) >= 2:
                    words.add(w)
        if words:
            return words, str(p)
    return set(), None


def check(source, rewrite):
    issues, warns = [], []
    src_n, new_n = norm(source), norm(rewrite)

    src_res = scan_ai.analyze(source) or {"score": 0}
    new_res = scan_ai.analyze(rewrite) or {"score": 0}
    s_before, s_after = src_res["score"], new_res["score"]

    # 1. 数字
    src_nums = numbers_of(source)
    for num in sorted(numbers_of(rewrite) - src_nums):
        if len(num.replace(".", "")) <= 2 and float(num) <= 10:
            warns.append(f"改写里出现了原文没有的数字「{num}」，若是序号请确认；若是事实请删掉")
        else:
            issues.append(f"改写新增了原文没有的数字「{num}」——去 AI 味不能编数字")

    # 2. 引号原话
    for q in quotes_of(rewrite):
        if norm(q) not in src_n:
            issues.append(f"引号里的原话「{q}」在原文找不到——不能编客户的对话")

    # 3. 长度
    ratio = len(new_n) / max(len(src_n), 1)
    if ratio < 0.5:
        issues.append(f"改后只剩原文的 {ratio:.0%}，信息删太多（标准 60%–130%）")
    elif ratio > 1.5:
        issues.append(f"改后是原文的 {ratio:.0%}，明显在灌水（标准 60%–130%）")
    elif ratio < 0.6 or ratio > 1.3:
        warns.append(f"改后长度是原文的 {ratio:.0%}，略超出 60%–130% 的建议区间")

    # 4. AI 分
    drop = s_before - s_after
    if s_after > 45:
        issues.append(
            f"改完 AI 分还有 {s_after}，仍然偏高（要降到 45 以下）——"
            "多半只换了同义词，句子骨架还是 AI 的"
        )
    elif s_after > 35 and drop < 25:
        issues.append(
            f"AI 分只从 {s_before} 降到 {s_after}（降了 {drop} 分），"
            "说明只是换了同义词，句子骨架还是 AI 的"
        )
    elif drop < 10 and s_after > 20:
        warns.append(f"AI 分从 {s_before} 降到 {s_after}，降幅偏小")

    # 5. 新增诊疗项目名
    words, src_path = load_treatment_words()
    if words:
        new_items = {w for w in words if w in rewrite and w not in source}
        for w in sorted(new_items):
            issues.append(f"改写里多出诊疗项目「{w}」，原文没提——可能变成无依据的推荐")
    else:
        warns.append("没找到项目词表（未装「面诊记录结构化」），跳过项目名核对")

    return {
        "before": s_before,
        "after": s_after,
        "drop": drop,
        "length_ratio": round(ratio, 2),
        "issues": issues,
        "warns": warns,
        "ok": not issues,
        "evidence_path": src_path,
    }


def render(res, before_label, after_label):
    lines = [
        f"AI 分：{res['before']} → {res['after']}（降 {res['drop']} 分）",
        f"长度：改后是原文的 {res['length_ratio']:.0%}",
        "",
    ]
    if res["issues"]:
        lines.append(f"不通过（{len(res['issues'])} 项）：")
        lines += [f"  [失败] {x}" for x in res["issues"]]
    else:
        lines.append("事实核对通过：数字、原话、项目名都没有改动。")
    if res["warns"]:
        lines.append("")
        lines += [f"  [注意] {x}" for x in res["warns"]]
    lines.append("")
    if res["ok"] and res["after"] <= 45:
        lines.append("结论：可以用。发布前建议再跑一次「合规文案审查」过全文。")
    elif res["ok"]:
        lines.append("结论：事实没问题，但 AI 味还偏重，建议再改一轮。")
    else:
        lines.append("结论：不合格，按上面 [失败] 项重改，改完重跑本脚本。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="校验去 AI 味改写的质量")
    ap.add_argument("rewrite", help="改写后的文件")
    ap.add_argument("--source", required=True, help="原文文件")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    for p in (args.rewrite, args.source):
        if not Path(p).exists():
            sys.exit(f"文件不存在：{p}")

    src = Path(args.source).read_text(encoding="utf-8")
    new = Path(args.rewrite).read_text(encoding="utf-8")
    res = check(src, new)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render(res, "原文", "改写"))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
