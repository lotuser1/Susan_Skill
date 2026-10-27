#!/usr/bin/env python3
"""AI 味扫描：给一段中文文案打分，指出具体哪几处像 AI 写的。

只用标准库，不联网。
"""

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LEXICON = BASE / "references" / "AI特征库.json"

WEIGHT = {"high": 9, "mid": 5, "low": 2}
LEVEL_LABEL = {"high": "高", "mid": "中", "low": "低"}

# 清单序号：这类数字不算「具体信息」
LIST_NUM_RE = re.compile(r"[0-9]+\s*(?:个|条|种|步|点|类|件|招)")
REAL_NUM_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
TIME_RE = re.compile(r"今天|昨天|前天|上周|上个月|去年|今年|刚才|前天|早上|下午|晚上|周[一二三四五六日天]")
PLACE_RE = re.compile(r"门诊|医院|店里|诊所|工作室|面诊室|手术室|楼下|隔壁")
QUOTE_RE = re.compile(r"「[^」]{2,60}」|“[^”]{2,60}”|\"[^\"]{2,60}\"")
SPEAK_RE = re.compile(r"(说|问|讲)[：:]\s*")


def load_lexicon():
    data = json.loads(LEXICON.read_text(encoding="utf-8"))
    feats = []
    for fam in data["families"]:
        for f in fam["features"]:
            item = dict(f)
            item["family"] = fam["name"]
            item["family_id"] = fam["id"]
            item["compiled"] = [re.compile(s) for s in f.get("signals", [])]
            feats.append(item)
    return feats


def split_sentences(text):
    parts = re.split(r"[。！？!?；;\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def split_paragraphs(text):
    return [p.strip() for p in text.split("\n") if p.strip()]


def stdev(vals):
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return var ** 0.5


def evidence_of(text, m):
    """取命中处所在的句子片段，作为证据。"""
    start = max(0, m.start() - 12)
    end = min(len(text), m.end() + 12)
    frag = text[start:end].replace("\n", " ").strip()
    return ("…" if start > 0 else "") + frag + ("…" if end < len(text) else "")


def measure(text):
    sents = split_sentences(text)
    paras = split_paragraphs(text)
    n_chars = max(len(re.sub(r"\s", "", text)), 1)

    lens = [len(s) for s in sents] or [0]
    avg_len = sum(lens) / len(lens)
    sent_cv = stdev(lens) / (avg_len or 1)

    para_lens = [len(p) for p in paras] or [0]
    para_avg = sum(para_lens) / len(para_lens)
    para_cv = stdev(para_lens) / (para_avg or 1) if len(para_lens) >= 5 else None

    comma_per100 = text.count("，") / n_chars * 100
    quotes = len(QUOTE_RE.findall(text))
    quote_per1000 = quotes / n_chars * 1000

    # 具体性：真实数字（排除清单序号）、时间、地点、对话
    numbers = [x for x in REAL_NUM_RE.findall(text)]
    list_marks = len(LIST_NUM_RE.findall(text))
    real_numbers = max(0, len(numbers) - list_marks)
    times = len(TIME_RE.findall(text))
    places = len(PLACE_RE.findall(text))
    speaks = len(SPEAK_RE.findall(text)) + quotes

    return {
        "chars": n_chars,
        "sentences": len(sents),
        "avg_sent_len": round(avg_len, 1),
        "sent_cv": round(sent_cv, 2),
        "paragraphs": len(paras),
        "para_cv": None if para_cv is None else round(para_cv, 2),
        "comma_per100": round(comma_per100, 1),
        "quote_per1000": round(quote_per1000, 1),
        "anchors": {
            "具体数字": real_numbers,
            "时间": times,
            "地点": places,
            "对话/原话": speaks,
        },
    }


def scan_features(text):
    hits = []
    for f in load_lexicon():
        count = 0
        evidences = []
        for rx in f["compiled"]:
            for m in rx.finditer(text):
                count += 1
                if len(evidences) < 2:
                    evidences.append(evidence_of(text, m))
        hits.append({
            "id": f["id"],
            "name": f["name"],
            "family": f["family"],
            "level": f["level"],
            "count": count,
            "evidence": evidences,
            "why": f.get("why", ""),
            "fix": f.get("fix", ""),
        })
    return hits


def apply_layout_metrics(hits, m):
    """排版类特征没有关键词，靠指标判定。"""
    by_id = {h["id"]: h for h in hits}
    if m["paragraphs"] >= 5 and m["para_cv"] is not None and m["para_cv"] < 0.35:
        by_id["ai_even_para"]["count"] = 1
        by_id["ai_even_para"]["evidence"] = [f"段落长度差异很小（变异系数 {m['para_cv']}）"]
    if m["sentences"] >= 6 and m["sent_cv"] < 0.30:
        by_id["ai_even_sent"]["count"] = 1
        by_id["ai_even_sent"]["evidence"] = [f"句长几乎一致（变异系数 {m['sent_cv']}）"]
    if m["quote_per1000"] > 10:
        by_id["ai_quote_dense"]["count"] = 2
        by_id["ai_quote_dense"]["evidence"] = [f"每千字 {m['quote_per1000']} 个引号"]
    return hits


def score_of(hits, m):
    penalty = 0.0
    for h in hits:
        if not h["count"]:
            continue
        penalty += WEIGHT[h["level"]] * min(h["count"], 2)

    if m["avg_sent_len"] > 42:
        penalty += 12
    elif m["avg_sent_len"] > 32:
        penalty += 8
    if m["comma_per100"] > 9.0:
        penalty += 6
    if m["paragraphs"] >= 5 and m["para_cv"] is not None and m["para_cv"] < 0.35:
        penalty += 5
    if m["sentences"] >= 6 and m["sent_cv"] < 0.30:
        penalty += 4

    anchor_total = sum(m["anchors"].values())
    if anchor_total == 0 and m["chars"] > 200:
        penalty += 6

    return int(min(100, round(penalty)))


def grade(score):
    if score <= 20:
        return "人味足", "基本没有 AI 特征，可以直接用。"
    if score <= 45:
        return "轻微 AI 味", "有几处小痕迹，改掉下面列出的就能过。"
    if score <= 70:
        return "明显 AI 味", "结构和句式都像模型写的，需要按下面逐条改。"
    return "重度 AI 味", "通篇是框架句，建议重写而不是微调。"


def advice_list(hits):
    scored = []
    for h in hits:
        if not h["count"]:
            continue
        weight = WEIGHT[h["level"]] * min(h["count"], 2)
        scored.append((weight, h))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, h in scored[:6]:
        if h["level"] == "low":
            continue
        out.append({
            "特征": h["name"],
            "出现": h["count"],
            "为什么像AI": h["why"],
            "怎么改": h["fix"],
        })
    return out


def analyze(text):
    text = text.strip()
    if not text:
        return None
    m = measure(text)
    hits = apply_layout_metrics(scan_features(text), m)
    score = score_of(hits, m)
    label, verdict = grade(score)
    return {
        "score": score,
        "label": label,
        "verdict": verdict,
        "metrics": m,
        "hits": [h for h in hits if h["count"]],
        "advice": advice_list(hits),
    }


def render(res, show_all=False):
    m = res["metrics"]
    lines = [
        f"AI 味评分：{res['score']} / 100（{res['label']}）",
        res["verdict"],
        "",
        f"结构指标：{m['sentences']} 句 / {m['paragraphs']} 段 / {m['chars']} 字",
        f"  平均句长 {m['avg_sent_len']} 字（人写通常 18–26）",
        f"  逗号密度 {m['comma_per100']} 个/百字（超过 9 偏书面）",
        f"  句长差异 {m['sent_cv']}（低于 0.30 像节拍器）",
    ]
    if m["para_cv"] is not None:
        lines.append(f"  段落长度差异 {m['para_cv']}（低于 0.35 说明段落等长）")
    a = m["anchors"]
    lines.append(
        f"人味锚点：具体数字 {a['具体数字']} 处 · 时间 {a['时间']} 处 · "
        f"地点 {a['地点']} 处 · 对话 {a['对话/原话']} 处"
    )

    hits = res["hits"] if show_all else [h for h in res["hits"] if h["level"] != "low"]
    if hits:
        lines.append("")
        lines.append(f"命中 {len(hits)} 类特征：")
        for h in sorted(hits, key=lambda x: (-WEIGHT[x["level"]], -x["count"])):
            ev = f"  证据：{h['evidence'][0]}" if h["evidence"] else ""
            lines.append(f"  [{LEVEL_LABEL[h['level']]}] {h['name']} ×{h['count']}{ev}")

    if res["advice"]:
        lines.append("")
        lines.append("改法（按收益排序）：")
        for i, a in enumerate(res["advice"], 1):
            lines.append(f"  {i}. {a['特征']} → {a['怎么改']}")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="扫描文案的 AI 味")
    ap.add_argument("file", nargs="?", help="要扫描的文件（不给则读 --text 或标准输入）")
    ap.add_argument("--text", help="直接给一段文字")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--all", action="store_true", help="连低置信度特征也列出")
    ap.add_argument("--quiet", action="store_true", help="只输出分数")
    args = ap.parse_args()

    if args.text:
        text = args.text
    elif args.file:
        p = Path(args.file)
        if not p.exists():
            sys.exit(f"文件不存在：{p}")
        text = p.read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    res = analyze(text)
    if not res:
        sys.exit("没有内容可扫描。")

    if args.quiet:
        print(res["score"])
    elif args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render(res, show_all=args.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
