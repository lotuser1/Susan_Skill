#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify.py · 面诊档案反幻觉校验

面诊档案最危险的问题不是"漏了"，而是"编了"。
这份脚本逐条核对：档案里的每一句原话、每一个数字、每一个项目名，
能不能在原始转写稿里找到依据。

用法：
    python3 verify.py 档案.md --source 转写稿.md
    python3 verify.py 档案.md --anchors tmp/anchors.json --source 转写稿.md
    python3 verify.py 档案.md --source 转写稿.md --json

退出码：0=合格（可能有建议）  1=必须修  2=文件读不到
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare import (  # noqa: E402
    extract_anchors, load_lexicon, parse_frontmatter, parse_utterances,
)

# ---------------------------------------------------------------- 常量

# 档案必备章节（宽松匹配：档案里只要出现这些词就算有）
REQUIRED_SECTIONS = [
    ("主诉", ["主诉", "诉求"]),
    ("既往史", ["既往", "做过的项目"]),
    ("方案", ["方案", "推荐项目"]),
    ("异议", ["异议", "顾虑", "抗拒点"]),
    ("决策状态", ["决策状态", "成交状态", "状态"]),
    ("下一步", ["下一步", "后续动作", "跟进"]),
    ("缺口", ["缺口", "未询问", "待补"]),
]

# 越界词：档案不该出现的判断性表述（这些属于付费的复盘能力）
OUT_OF_SCOPE = [
    (r"诊断为|确诊|属于典型的|病理|病症判断", "输出医学诊断结论"),
    (r"聊得不好|聊得不到位|失误|没聊好|话术有问题", "对咨询过程做评价"),
    (r"应该先.{0,8}再|建议改成|更好的说法是", "给话术改写建议"),
    (r"这个客户(属于|是).{0,6}型", "给客户贴类型标签"),
]

# 否定表述：用于查"原文明确否认、档案记成做过"
NEG_MARKERS = ("没有", "没打", "没做", "没填", "没注射", "未打", "未做", "从未", "从没")
# 疑问结构不是否定 —— "双眼皮有没有打过"是在问，不是说没做过
NEG_EXCLUDE = ("有没有", "是不是", "要不要", "能不能", "会不会", "该不该", "有没有做")
# 档案里出现这些词，说明该行是"承认做过"的语境
AFFIRM_MARKERS = ("做过", "打过", "注射过", "填充过", "曾在", "之前做", "以往", "去年", "前年")

# 语气词与填充词，比对原话前先剔除
FILLERS = [
    "就是", "那个", "然后", "你知道吧", "对对对", "这个这个", "其实",
    "反正", "相当于", "就是说", "怎么说呢", "嗯", "啊", "哦", "呃",
]
PUNCT_RE = re.compile(r"[\s，。！？、；：“”‘’（）()《》【】\[\]…—\-~·,.!?;:'\"|]+")

QUOTE_RE = re.compile(r"【原话】\s*[“\"「『]?([^”\"」』\n]{4,200})[”\"」』]?")
ANNOT_RE = re.compile(r"【原话】|【转述】|【医生提出】|【客户主动要求】|【咨询师建议】|未提及|未询问")


def norm(text):
    t = text
    for f in FILLERS:
        t = t.replace(f, "")
    return PUNCT_RE.sub("", t)


def quote_similarity(quote, source_norm):
    """原话是否能在原文里找到。返回 0~1 的命中率。

    LLM 引用原话时会顺掉语气词、改标点，所以不能精确匹配。
    做法：把引文切成 8 字窗口，看多少个窗口能在原文里找到。
    """
    q = norm(quote)
    if len(q) < 4:
        return 1.0
    if q in source_norm:
        return 1.0
    win = 8 if len(q) >= 12 else 6
    windows = [q[i:i + win] for i in range(0, len(q) - win + 1)]
    if not windows:
        return 1.0 if q in source_norm else 0.0
    hit = sum(1 for w in windows if w in source_norm)
    return hit / len(windows)


def split_doc_sections(text):
    """按 ## 标题切段，返回 {标题: 正文}。"""
    sections, cur, buf = {}, "（正文开头）", []
    for line in text.splitlines():
        if re.match(r"^#{1,3}\s+", line):
            if buf:
                sections[cur] = "\n".join(buf)
            cur = re.sub(r"^#{1,3}\s+", "", line).strip()
            buf = []
        else:
            buf.append(line)
    if buf:
        sections[cur] = "\n".join(buf)
    return sections


# ---------------------------------------------------------------- 检查项

def check_sections(doc):
    issues = []
    for label, keys in REQUIRED_SECTIONS:
        if not any(k in doc for k in keys):
            issues.append({
                "level": "must", "kind": "字段缺失",
                "msg": f"档案缺少「{label}」部分",
                "fix": f"按 references/档案字段表.md 补上「{label}」，没有信息就写「未提及」",
            })
    return issues


def check_quotes(doc, source_norm):
    """所有 【原话】 必须能在原文找到。"""
    issues = []
    seen = set()
    for m in QUOTE_RE.finditer(doc):
        q = m.group(1).strip()
        if len(q) < 4 or q in seen:
            continue
        seen.add(q)
        sim = quote_similarity(q, source_norm)
        if sim < 0.5:
            issues.append({
                "level": "must", "kind": "原话对不上",
                "msg": f"引文在原文找不到依据：\u201c{q[:40]}{'…' if len(q) > 40 else ''}\u201d",
                "fix": "改回原文真实表述，或改成【转述】；原文没有就写「未提及」",
            })
        elif sim < 0.8:
            issues.append({
                "level": "warn", "kind": "原话可能被改写",
                "msg": f"引文与原文只有部分吻合：\u201c{q[:40]}{'…' if len(q) > 40 else ''}\u201d",
                "fix": "核对后改回原文，语气词可以删，但用词不要换",
            })
    return issues


def check_numbers(doc, anchors, source_norm):
    """档案里的金额/剂量/年龄必须能在锚点清单里找到。

    用「拼接后子串匹配」而不是完全相等 —— ASR 会把
    「5980 一个部位」粘成「5559801个部位」，精确匹配会全盘误报。
    """
    issues = []
    joined = "".join(re.sub(r"\D", "", a["normalized"]) for a in anchors)

    def ok(digits):
        return bool(digits) and digits in joined

    seen = set()

    def report(raw, digits, kind, hint):
        if raw in seen:
            return
        seen.add(raw)
        if ok(digits):
            return
        issues.append({
            "level": "must", "kind": f"数字无依据（{kind}）",
            "msg": f"档案写了「{raw}」，原始转写稿里找不到这个数字",
            "fix": hint,
        })

    for m in re.finditer(r"(\d{2,7})\s*(?:元|块钱|块)", doc):
        report(m.group(0).strip(), m.group(1), "金额",
               "改成原文的数字，或删掉改「未提及」——金额写错会误导报价和成交判断")
    for m in re.finditer(r"(\d+)\s*(?:支|瓶|单位|iu|IU|ml|ML|毫升)", doc):
        report(m.group(0).strip(), m.group(1), "剂量",
               "剂量必须来自原文，原文没说就写「未提及」")
    for m in re.finditer(r"(\d{2})\s*岁", doc):
        report(m.group(0).strip(), m.group(1), "年龄",
               "年龄只在客户自己说过时写，不许从声音推断")

    # 裸金额（"报价 5980"这种不带元的写法）
    for m in re.finditer(r"(?<![\d.])(\d{4,6})(?![\d.])", doc):
        v = m.group(1)
        if len(v) == 4 and 1900 <= int(v) <= 2100:
            continue  # 年份
        report(v, v, "金额", "这个数字在原文找不到，确认是不是记错了")
    return issues


def check_treatments(doc, source_norm, lex):
    """项目名必须能在原文找到依据；易混项目单独查。

    只查"项目/材料/设备/手术"四类。部位（"眼周""面中部"）和诉求词（"显老"）
    不做硬校验 —— 客户只会说"眼睛""下面"，档案规范化成标准术语是正常行为，
    按原文逐字比对会满屏误报。
    """
    issues = []
    groups = lex.get("groups", {})
    SOFT_GROUPS = {"concern", "area"}

    def related(w):
        """原文是否出现过这个词或它的双字片段。"""
        if w in source_norm:
            return True
        return any(w[i:i + 2] in source_norm for i in range(len(w) - 1))

    mentioned_in_doc, mentioned_exact = set(), set()
    for key, grp in groups.items():
        if key in SOFT_GROUPS:
            continue
        for w in grp.get("words", []):
            if len(w) < 2:
                continue
            if w in doc:
                mentioned_in_doc.add(w)
            if w in source_norm:
                mentioned_exact.add(w)

    for w in sorted(mentioned_in_doc):
        if not related(w):
            issues.append({
                "level": "must", "kind": "项目名无依据",
                "msg": f"档案提到「{w}」，但原始转写稿里没有出现相关表述",
                "fix": "确认是不是听错/写错；原文模糊就写「XX[?]」并列入待确认",
            })

    for pair in lex.get("confusables", []):
        a, b = pair["a"], pair["b"]
        if a in mentioned_in_doc and b not in mentioned_in_doc \
                and b in mentioned_exact and a not in mentioned_exact:
            issues.append({
                "level": "must", "kind": "疑似项目混淆",
                "msg": f"档案写「{a}」，但原文说的是「{b}」——{pair['why']}",
                "fix": f"改成「{b}」",
            })
    return issues


def check_negation(doc, source, lex):
    """原文明确否认过的项目，档案里不能记成"做过"。

    这是面诊档案最危险的一类错误：客户说"我没打过生长因子"，
    档案记成"打过生长因子"，后续方案会直接做错。
    """
    issues = []
    treats = []
    for key, grp in lex.get("groups", {}).items():
        if key in ("concern", "area"):
            continue
        treats += [w for w in grp.get("words", []) if len(w) >= 2]

    negated = {}
    sents = [s.strip() for s in re.split(r"[。？！\n]", source) if s.strip()]
    for i, sent in enumerate(sents):
        probe = sent
        for q in NEG_EXCLUDE:
            probe = probe.replace(q, "")
        if not any(n in probe for n in NEG_MARKERS):
            continue
        # 真实对话里客户否定时不重复项目名：「你打过水光吗？」「没打过」
        # 所以前一句是问句时，把两句并起来找项目
        ctx = sent
        if i > 0 and re.search(r"[？?]|吗\s*$|有没有|是不是", sents[i - 1]):
            ctx = sents[i - 1] + sent
        for w in treats:
            if w in ctx:
                negated[w] = ctx.strip()

    for w, sent in negated.items():
        for line in doc.splitlines():
            if w not in line:
                continue
            if any(n in line for n in ("否认", "没有", "未", "没打", "未做")):
                continue  # 档案本身写的是否认，正确
            if any(k in line for k in AFFIRM_MARKERS):
                issues.append({
                    "level": "must", "kind": "与原文矛盾（既往史）",
                    "msg": f"原文否认「{w}」，档案却记成做过：{line.strip()[:60]}",
                    "fix": f"原文是：\u201c{sent[:50]}\u201d。改成否认，或整条删掉",
                })
                break
    return issues


def check_out_of_scope(doc):
    issues = []
    for pat, why in OUT_OF_SCOPE:
        for m in re.finditer(pat, doc):
            issues.append({
                "level": "must", "kind": "超出本能力范围",
                "msg": f"出现{why}的表述：「{m.group(0)}」",
                "fix": "本能力只做记录，不做评价。删掉这类判断——判断好坏是「聊客复盘诊断」的事",
            })
            break
    return issues


def check_annotation(doc):
    """条目是否带来源标注。只做粗查，提示不是硬伤。"""
    lines = [l for l in doc.splitlines()
             if l.strip().startswith(("-", "|", "1.", "2.", "3.", "4.", "5."))
             and len(l.strip()) > 12]
    if not lines:
        return []
    annotated = sum(1 for l in lines if ANNOT_RE.search(l))
    ratio = annotated / len(lines)
    if annotated == 0 and len(lines) > 10:
        return [{
            "level": "must", "kind": "完全没有来源标注",
            "msg": f"{len(lines)} 条条目一条标注都没有，分不清哪句是客户原话、哪句是归纳",
            "fix": "按字段表给每条加【原话】/【转述】/未提及。这是档案能用的前提",
        }]
    if ratio < 0.35:
        return [{
            "level": "warn", "kind": "来源标注不足",
            "msg": f"{len(lines)} 条条目里只有 {annotated} 条带【原话】/【转述】/未提及 标注（{ratio:.0%}）",
            "fix": "按字段表要求，每条都标明来源。不标就分不清哪句是客户原话",
        }]
    return []


def check_unknown_marks(doc):
    """用了 [?] 就要在待确认里列出来。"""
    marks = re.findall(r"([^\s，。、]{2,12})\[\?\]", doc)
    if not marks:
        return []
    tail = doc[-600:]
    missing = [m for m in set(marks) if m not in tail.split("待确认")[-1] and "待确认" not in tail]
    if missing:
        return [{
            "level": "warn", "kind": "待确认未汇总",
            "msg": f"标了不确定的项目（{'、'.join(sorted(set(marks)))})，但末尾缺口清单里没列",
            "fix": "在「本次档案缺口」里加一行待确认项，提醒下次核实",
        }]
    return []


# ---------------------------------------------------------------- 主流程

def load_source(path, lex_path):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    _, body = parse_frontmatter(raw)
    utts = parse_utterances(body)
    full = "\n".join(u["text"] for u in utts) if utts else body
    anchors = extract_anchors(full)
    pro_words, _, lex = load_lexicon(lex_path)
    return full, anchors, lex


def main():
    ap = argparse.ArgumentParser(description="面诊档案反幻觉校验")
    ap.add_argument("doc", help="档案文件（.md）")
    ap.add_argument("--source", required=True, help="原始转写稿路径")
    ap.add_argument("--anchors", help="预处理产出的 anchors.json（可选，给了就不用重算）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--lexicon", default=None, help="项目词表路径")
    args = ap.parse_args()

    doc_path = Path(args.doc)
    if not doc_path.is_file():
        sys.exit(f"找不到档案：{doc_path}")
    if not Path(args.source).is_file():
        sys.exit(f"找不到原稿：{args.source}")

    doc = doc_path.read_text(encoding="utf-8", errors="replace")
    lex_path = args.lexicon or str(Path(__file__).resolve().parent.parent
                                  / "references" / "项目词表.json")
    source, anchors, lex = load_source(args.source, lex_path)
    if args.anchors and Path(args.anchors).is_file():
        anchors = json.loads(Path(args.anchors).read_text(encoding="utf-8")).get("anchors", anchors)
    source_norm = norm(source)

    issues = []
    issues += check_sections(doc)
    issues += check_numbers(doc, anchors, source_norm)
    issues += check_treatments(doc, source_norm, lex)
    issues += check_negation(doc, source, lex)
    issues += check_quotes(doc, source_norm)
    issues += check_out_of_scope(doc)
    issues += check_annotation(doc)
    issues += check_unknown_marks(doc)

    must = [i for i in issues if i["level"] == "must"]
    warn = [i for i in issues if i["level"] == "warn"]
    verdict = "不合格" if must else ("合格（有建议）" if warn else "合格")

    if args.json:
        print(json.dumps({
            "verdict": verdict, "must": len(must), "warn": len(warn),
            "issues": issues,
            "stats": {"source_chars": len(source), "doc_chars": len(doc),
                      "anchors": len(anchors)},
        }, ensure_ascii=False, indent=2))
    else:
        print(f"校验结果：{verdict}")
        print(f"原稿 {len(source)} 字 · 档案 {len(doc)} 字 · 数字锚点 {len(anchors)} 个")
        print(f"必须修 {len(must)} 项 · 建议 {len(warn)} 项")
        print()
        if must:
            print("【必须修】")
            for i in must:
                print(f"  · {i['kind']}：{i['msg']}")
                print(f"    改法：{i['fix']}")
            print()
        if warn:
            print("【建议】")
            for i in warn:
                print(f"  · {i['kind']}：{i['msg']}")
                print(f"    改法：{i['fix']}")
            print()
        if not issues:
            print("所有原话、数字、项目名都能在原文找到依据。")
    sys.exit(1 if must else 0)


if __name__ == "__main__":
    main()
