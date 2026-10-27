#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标题自检：字数、风险词、同质化、钩子分布。

只用标准库。用法：
    python3 check_titles.py titles.txt --platform xhs
    python3 check_titles.py --text "标题一
标题二" --platform douyin

titles.txt 每行一条标题，可以在后面用 | 标钩子类型：
    越贵的水光针，可能越不适合你的脸 | 反常识
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
OWN_LEX = SKILL_DIR / "references" / "标题风险词.json"

# 平台：上限 / 推荐区间 / 显示名
PLATFORMS = {
    "xhs": {"name": "小红书", "limit": 20, "best": (12, 18)},
    "douyin": {"name": "抖音", "limit": 30, "best": (15, 24)},
    "moments": {"name": "朋友圈", "limit": 15, "best": (8, 14)},
    "channels": {"name": "视频号", "limit": 25, "best": (12, 20)},
}

# 从「合规文案审查」词库并入的类别：标题里出现基本都得改
MERGE_CATEGORIES = {
    "absolute", "efficacy_assert", "rate_commit", "before_after",
    "anxiety", "private_traffic", "interaction_bait", "compare",
    "authority_fake", "medical_term_unlicensed", "cosmetic_claim", "endorse",
}

# 这些类别并入后是「必须改」，其余并入的只作「建议改」
MUST_MERGE = {
    "efficacy_assert", "absolute", "before_after", "anxiety",
    "rate_commit", "private_traffic", "interaction_bait",
}

# 发布主体不同，适用的规则不同
SUBJECTS = {
    "clinic": {"name": "持证医美机构/机构内咨询师", "drop": {"medical_term_unlicensed", "cosmetic_claim"}},
    "beauty": {"name": "生活美容/皮肤管理（无医疗资质）", "drop": set()},
    "personal": {"name": "个人博主/自由职业（无资质）", "drop": set()},
}

# 误报排除：某个词后面跟这些字时，语义已经变了
EXCLUDE_NEXT = {
    "第一": "次批天个种步件眼时印感阶年月周秒位节课",
    "最好": "不别先还能是去问在要再",
    "最多": "的",
}
# 误报排除：某个词前面是这些字时，语义已经变了
EXCLUDE_PREV = {
    "丑": "小出",
    "完美": "不",
}

HOOKS = ["反常识", "身份代入", "数字清单", "痛点直击", "踩坑警告",
         "对比反差", "好奇缺口", "经验背书", "时机关联", "提问互动"]


def load_own():
    with open(OWN_LEX, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for c in data.get("categories", []):
        for w in c.get("words", []):
            out.append({"word": w, "cat": c["name"], "cat_id": c.get("id", ""),
                        "risk": c.get("risk", "legal"),
                        "why": c.get("why", ""), "src": "本能力"})
    return out


def find_sibling_lexicon():
    """找同级 ma-risk-check 的违禁词库。找不到就返回 None（不报错）。"""
    candidates = [
        SKILL_DIR.parent / "ma-risk-check" / "references" / "违禁词库.json",
    ]
    home = Path.home()
    for root in ("workbuddy", "claude", "codebuddy", "agents"):
        candidates.append(home / f".{root}" / "skills" / "ma-risk-check" / "references" / "违禁词库.json")
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_merged(subject="clinic"):
    p = find_sibling_lexicon()
    if not p:
        return [], None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return [], None
    drop = SUBJECTS.get(subject, SUBJECTS["clinic"])["drop"]
    out = []
    for c in data.get("categories", []):
        cid = c.get("id", "")
        if cid not in MERGE_CATEGORIES or cid in drop:
            continue
        for w in c.get("words", []):
            if len(w) < 2:
                continue
            out.append({"word": w, "cat": c["name"], "cat_id": cid,
                        "risk": c.get("risk", "legal"),
                        "why": c.get("why", ""), "src": "合规词库"})
    return out, str(p)


def benign(text, word, start):
    """判断这个词在当前上下文里是不是误报。"""
    nxt = text[start + len(word): start + len(word) + 1]
    if word in EXCLUDE_NEXT and nxt and nxt in EXCLUDE_NEXT[word]:
        return True
    prev = text[max(0, start - 1):start]
    if word in EXCLUDE_PREV and prev and prev in EXCLUDE_PREV[word]:
        return True
    return False


def scan(text, lexicon):
    hits, seen = [], set()
    for item in lexicon:
        w = item["word"]
        if w in seen or w not in text:
            continue
        start = text.find(w)
        # 逐个出现位置判断，只要有一处不是误报就算命中
        pos, real = start, False
        while pos != -1:
            if not benign(text, w, pos):
                real = True
                break
            pos = text.find(w, pos + 1)
        if not real:
            continue
        seen.add(w)
        hits.append(item)
    # 长词优先，避免「根治」和「治」同时报
    hits.sort(key=lambda x: -len(x["word"]))
    kept = []
    for h in hits:
        if any(h["word"] in k["word"] for k in kept):
            continue
        kept.append(h)
    return kept


PUNCT = set("，。！？、；：\u201c\u201d\u2018\u2019（）()【】[]…—·~～!?,.;:'\"| \t\r\n")


def strip_punct(s):
    return "".join(ch for ch in s if ch not in PUNCT)


def check_one(title, hook, platform, lexicon):
    info = PLATFORMS[platform]
    n = len(re.sub(r"\s", "", title))
    issues, warns = [], []

    if n > info["limit"]:
        issues.append(f"超字数：{n} 字，{info['name']}上限 {info['limit']} 字（要删 {n - info['limit']} 字）")
    lo, hi = info["best"]
    if n < lo:
        warns.append(f"偏短：{n} 字，这个平台 {lo}–{hi} 字更容易点开")
    elif n > hi and n <= info["limit"]:
        warns.append(f"偏长：{n} 字，信息流里可能被截断（推荐 {lo}–{hi} 字）")

    for h in scan(title, lexicon):
        if h["src"] == "本能力":
            must = True            # 标题专属词表是按标题语境校准过的，命中就是问题
            tail = h["cat"]
        else:
            must = h["cat_id"] in MUST_MERGE
            tail = f"{h['cat']}·来自合规词库"
        line = f"{'必须改' if must else '建议改'}：「{h['word']}」（{tail}）"
        if must:
            issues.append(line)
        else:
            warns.append(line)

    return {"title": title, "hook": hook, "chars": n, "issues": issues, "warns": warns}


def check_batch(rows):
    """批次层面的问题：同质化、钩子单一、条数不足。"""
    warns = []
    if len(rows) < 6:
        warns.append(f"只出了 {len(rows)} 条，建议一次出 8–10 条再挑，选择空间更大")

    heads = {}
    for r in rows:
        key = strip_punct(r["title"])[:3]
        if len(key) < 2:
            continue
        heads.setdefault(key, []).append(r["title"])
    for k, v in heads.items():
        # 2 条同开头可以接受（同一选题难免），3 条以上就开始显得重复了
        if len(v) >= 3:
            warns.append(f"有 {len(v)} 条开头都是「{k}」——同质化，用户刷到会觉得重复")

    hooks = [r["hook"] for r in rows if r["hook"]]
    if hooks:
        if len(hooks) < len(rows):
            warns.append(f"有 {len(rows) - len(hooks)} 条没标钩子类型，无法判断多样性")
        from collections import Counter
        c = Counter(hooks)
        top, cnt = c.most_common(1)[0]
        if cnt > max(2, len(hooks) // 2):
            warns.append(f"钩子太单一：「{top}」占了 {cnt}/{len(hooks)} 条，换个钩子试试")
        unknown = [h for h in c if h not in HOOKS]
        if unknown:
            warns.append(f"钩子名不在库里的十类中：{'、'.join(unknown)}（可能是笔误，也可能是新钩子）")
    return warns


def parse_input(args):
    if args.text:
        raw = args.text
    else:
        p = Path(args.file)
        if not p.is_file():
            sys.exit(f"找不到文件：{p}")
        raw = p.read_text(encoding="utf-8")
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        # 只剥列表序号（"1." "1、" "-"），不能剥裸数字——"30岁以后…" 的开头是内容
        line = re.sub(r"^(?:[-*+•]\s*|\d{1,2}[.、)）]\s*)", "", line).strip()
        if not line or line.startswith("#"):
            continue
        hook = ""
        if "|" in line:
            line, hook = line.rsplit("|", 1)
            line, hook = line.strip(), hook.strip()
        if line:
            rows.append((line, hook))
    return rows


def main():
    ap = argparse.ArgumentParser(description="标题自检")
    ap.add_argument("file", nargs="?", help="标题文件，每行一条")
    ap.add_argument("--text", help="直接传标题，换行分隔")
    ap.add_argument("--platform", default="xhs", choices=list(PLATFORMS),
                    help="目标平台，默认 xhs")
    ap.add_argument("--subject", default="clinic", choices=list(SUBJECTS),
                    help="发布主体，决定哪些诊疗用语算违规。默认 clinic（持证机构）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if not args.file and not args.text:
        ap.error("要么给文件，要么用 --text")

    rows = parse_input(args)
    if not rows:
        sys.exit("没读到任何标题。检查文件是不是空的，或者每行是不是一条。")

    own = load_own()
    merged, merged_path = load_merged(args.subject)
    lexicon = own + merged

    results = [check_one(t, h, args.platform, lexicon) for t, h in rows]
    batch = check_batch(results)

    n_must = sum(1 for r in results if r["issues"])
    payload = {
        "platform": PLATFORMS[args.platform]["name"],
        "limit": PLATFORMS[args.platform]["limit"],
        "total": len(results),
        "failed": n_must,
        "merged_lexicon": merged_path,
        "items": results,
        "batch_warnings": batch,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if n_must else 0

    info = PLATFORMS[args.platform]
    print(f"平台：{info['name']}（上限 {info['limit']} 字，推荐 {info['best'][0]}–{info['best'][1]} 字）")
    print(f"共 {len(results)} 条" + (f"，已并入合规词库：{merged_path}" if merged_path else "，未找到合规词库（只查标题专属风险）"))
    print()
    for i, r in enumerate(results, 1):
        mark = "✗" if r["issues"] else ("!" if r["warns"] else "✓")
        hook = f"  钩子:{r['hook']}" if r["hook"] else ""
        print(f"[{i}] {mark} {r['title']}  （{r['chars']} 字{hook}）")
        for x in r["issues"]:
            print(f"     ✗ {x}")
        for x in r["warns"]:
            print(f"     ⚠ {x}")
    print()
    if batch:
        print("批次问题：")
        for w in batch:
            print(f"  ⚠ {w}")
        print()
    ok = len(results) - n_must
    n_warn = sum(1 for r in results if r["warns"] and not r["issues"])
    if n_must:
        print(f"结论：{ok} 条可用，{n_must} 条必须改。改完重跑一遍。")
    elif n_warn:
        print(f"结论：{len(results)} 条都没有必须改的问题，其中 {n_warn} 条有建议调整项（看上面 ⚠）。")
        print("下一步：挑 3–5 条最像「客户会问的问题」的去发，不要全发。")
    else:
        print(f"结论：{len(results)} 条全部通过字数和风险词检查，没有建议项。")
        print("下一步：挑 3–5 条最像「客户会问的问题」的去发，不要全发。")
    return 1 if n_must else 0


if __name__ == "__main__":
    sys.exit(main())
