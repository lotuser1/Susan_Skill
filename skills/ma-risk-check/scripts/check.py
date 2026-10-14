#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ma-risk-check · 医美文案合规检查

用法：
    python3 scripts/check.py --text "这段文案能不能发"
    python3 scripts/check.py --file post.txt --subject clinic --platform xhs
    cat post.txt | python3 scripts/check.py
    python3 scripts/check.py --selftest

参数：
    --text / --file / (stdin)   待检查文案（三选一）
    --subject    clinic(持证医美机构) | beauty(生活美容馆) | skincare(普通美妆)  默认 clinic
    --platform   douyin | xhs | sph | all                                      默认 all
    --ai-generated               声明内容由 AI/数字人生成，会检查标识要求
    --format     text | md | json                                              默认 text
    --selftest                   跑内置样例自检
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEXICON = HERE.parent / "references" / "违禁词库.json"

# 单独命中只给「提示」不给「违规判定」的词：需要结合上下文才能定性
SOFT_WORDS = {"安全", "无痛", "无创", "丑", "面相"}

# 命中后只作「可用性提示」、不计入风险的类别：
# medical_term_licensed = 持证机构本就可以说的诊疗术语，只要不搭配功效承诺即合规
INFO_CATEGORIES = {"medical_term_licensed"}

# 语义规则的安全句式提示（对应 references/上下文语义规则.md 第四节）
RULE_TIPS = {
    "R1": "改为不确定的医学表述：可能出现暂时性红肿，一般数天内缓解",
    "R2": "改为群体经验表述：按多数人的恢复节奏，一般会有比较明显的改善",
    "R3": "改为个体差异表述：恢复期因人而异，大多数人反馈一周左右",
    "R4": "改为案例表述：在我们过去的服务案例里，大多数人的反馈是……",
    "R5": "改为节奏表述：按多数人的节奏，一般在数周左右逐渐看到变化",
    "R6": "改为处理方式表述：针对 XX 情况，常见的处理方式有……",
    "R7": "改为基础适配表述：这个部位可以按你的基础做一些调整",
    "R9": "去掉引流信息，或改为不带任何联系方式的纯科普",
    "R10": "改为方案逻辑表述：从方案设计逻辑上讲，这个部位一般考虑……",
    "R12": "删除分期/贷款劝诱话术，或改为中性说明付款方式",
}

# 语义规则： (规则号, 名称, [模式组A], [模式组B], 等级, 说明)
# 判定逻辑：A 组与 B 组在「同一句」内共现即触发
SEMANTIC_RULES = [
    ("R1", "安全性断言",
     [r"安全", r"无风险", r"零风险", r"无副作用", r"完全无痛", r"无创伤", r"零恢复期", r"绝对安全"],
     [r"很", r"非常", r"绝对", r"完全", r"一定", r"肯定", r"保证", r"百分百", r"100%", r"都", r"没有"],
     "must", "医美广告禁止对安全性作断言或保证（《医疗美容广告发布合规指引》四(四)）"),

    ("R2", "承诺性动词 + 效果名词",
     [r"保证", r"承诺", r"包", r"一定", r"肯定", r"必定", r"绝对", r"确保"],
     [r"效果", r"见效", r"改善", r"恢复", r"变美", r"成功", r"满意", r"好看", r"年轻", r"瘦"],
     "must", "承诺效果属违规，改为群体经验表述"),

    ("R3", "否定式安全承诺",
     [r"无", r"零", r"没有", r"不存在", r"不会", r"绝不"],
     [r"副作用", r"风险", r"痛苦", r"后遗症", r"失败", r"反弹", r"复发", r"痕迹", r"疤痕"],
     "must", "否定式安全承诺违规，改为个体差异表述"),

    ("R4", "数字/比例 + 效果",
     [r"\d+\s*%", r"\d+\s*成", r"百分之\s*\d+", r"\d+\s*例", r"百分百", r"100\s*%"],
     [r"有效", r"成功", r"满意", r"改善", r"复购", r"好评", r"治愈", r"解决"],
     "must", "宣传治愈率/有效率违规，改为案例表述"),

    ("R5", "时间 + 见效（速效承诺）",
     [r"\d+\s*天", r"\d+\s*周", r"\d+\s*个?月", r"一周", r"一个月", r"立刻", r"马上", r"当场", r"当天", r"瞬间", r"一秒", r"即刻"],
     [r"见效", r"变白", r"变美", r"瘦", r"淡", r"消失", r"改善", r"紧致", r"逆袭", r"恢复", r"好"],
     "must", "速效承诺违规，改为节奏表述"),

    ("R6", "极端副词 + 动作",
     [r"根治", r"彻底", r"永远", r"永不", r"完全", r"全部", r"一律", r"绝对", r"一次性"],
     [r".{0,6}"],
     "must", "极端化表述违规，改为处理方式表述"),

    ("R7", "制造容貌焦虑",
     [r"丑", r"缺陷", r"硬伤", r"拉垮", r"显老", r"显穷", r"没气质", r"不上镜", r"被人嫌弃", r"嫁不出去", r"找不到对象"],
     [r"一定要改", r"必须", r"不改就", r"否则", r"完了", r"没救", r"没法看", r"才能", r"不然"],
     "must", "制造容貌焦虑是监管整治重点，改为基础适配表述"),

    ("R9", "软文变相广告（科普外壳 + 引流内核）",
     [r"科普", r"讲个知识点", r"专访", r"探店", r"分享一个知识", r"新闻报道"],
     [r"地址", r"电话", r"二维码", r"点击链接", r"主页", r"门店", r"预约", r"私信", r"到店", r"加微信", r"咨询"],
     "must", "以科普形式变相发布医疗广告违规，去掉引流信息"),

    ("R10", "患者名义作证明",
     [r"顾客", r"客户", r"客人", r"小姐姐", r"粉丝", r"姐妹"],
     [r"说", r"反馈", r"评价", r"见证", r"亲测", r"做完说", r"都说", r"好评"],
     "should", "以患者名义作推荐证明违规（若同时出现人物形象/聊天截图则必违规）"),

    ("R11", "未成年人指向",
     [r"学生", r"高考", r"暑假", r"未成年", r"00后", r"05后"],
     [r"变美", r"做项目", r"优惠", r"专属", r"分期", r"贷款", r"首付", r"计划"],
     "must", "向未成年人发送医美广告违规"),

    ("R12", "医美贷/分期诱导",
     [r"分期", r"零首付", r"免息", r"贷款", r"借钱", r"花呗", r"信用卡", r"月付"],
     [r"变美", r"项目", r"手术", r"做", r"下单", r"先做", r"垫付"],
     "should", "医美贷诱导是监管重点整治方向"),
]

# 单句切分：中文句号/问号/叹号/换行/分号
SENT_SPLIT = re.compile(r"[^\n。！？!?；;]+[\n。！？!?；;]?")


def load_lexicon():
    if not LEXICON.exists():
        sys.exit("词库不存在：%s" % LEXICON)
    return json.loads(LEXICON.read_text(encoding="utf-8"))


def category_applies(cat, subject, platform):
    """判断这一类规则对当前主体/平台是否生效"""
    if cat.get("subject", "all") not in ("all", subject):
        return False
    plats = cat.get("platforms", ["all"])
    if "all" not in plats and platform not in plats:
        return False
    return True


def find_hits(text, word):
    """返回 word 在 text 中所有起始位置；纯 ASCII 词忽略大小写"""
    flags = re.IGNORECASE if word.isascii() else 0
    return [m.start() for m in re.finditer(re.escape(word), text, flags)]


def snippet(text, pos, word, pad=12):
    a = max(0, pos - pad)
    b = min(len(text), pos + len(word) + pad)
    s = text[a:b].replace("\n", " ")
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def suggest_for(cat, word):
    rep = cat.get("replacements", {}) or {}
    if word in rep:
        return rep[word]
    for k, v in rep.items():
        if word in k or k in word:
            return v
    return None


def scan_words(text, lex, subject, platform):
    hits = []
    for cat in lex.get("categories", []):
        if not category_applies(cat, subject, platform):
            continue
        for w in cat.get("words", []):
            for pos in find_hits(text, w):
                soft = w in SOFT_WORDS
                risk = cat.get("risk", "platform")
                if cat["id"] in INFO_CATEGORIES:
                    lvl = "info"
                elif soft or risk == "platform":
                    lvl = "should"
                else:
                    lvl = "must"
                hits.append({
                    "level": lvl,
                    "type": "word",
                    "word": w,
                    "category": cat["name"],
                    "category_id": cat["id"],
                    "basis": cat.get("basis", ""),
                    "note": cat.get("note", ""),
                    "pos": pos,
                    "context": snippet(text, pos, w),
                    "suggest": suggest_for(cat, w),
                })
    # 同一词只保留一次最高等级
    dedup = {}
    for h in hits:
        key = h["word"]
        if key not in dedup or (h["level"] == "must" and dedup[key]["level"] != "must"):
            dedup[key] = h
    return sorted(dedup.values(), key=lambda x: x["pos"])


def scan_semantics(text):
    out = []
    seen = set()
    for sentences in [s for s in SENT_SPLIT.findall(text) if s.strip()]:
        for rid, name, pats_a, pats_b, lvl, why in SEMANTIC_RULES:
            if rid in seen:
                continue
            hit_a = next((p for p in pats_a if re.search(p, sentences)), None)
            if not hit_a:
                continue
            hit_b = next((p for p in pats_b if re.search(p, sentences)), None)
            if not hit_b:
                continue
            seen.add(rid)
            out.append({
                "level": lvl,
                "type": "rule",
                "rule": rid,
                "name": name,
                "why": why,
                "context": sentences.strip()[:80],
                "matched_a": hit_a,
                "matched_b": hit_b,
                "suggest": RULE_TIPS.get(rid),
            })
    return out


def ai_label_check(text, declared):
    """R13：AI 生成内容标识（必须标注，非禁用）"""
    if not declared:
        return None
    return {
        "level": "must", "type": "rule", "rule": "R13", "name": "AI 生成内容必须标识",
        "why": "《人工智能生成合成内容标识办法》（2025-09-01 施行）：显式+隐式双标识，"
               "未标识将限流/下架/封号，且不得删除、篡改、隐匿标识",
        "context": "内容由数字人/AI 生成",
        "suggest": "视频起始画面 + 播放周边显著位置加提示；文件元数据写入生成属性、服务提供者、内容编号",
    }


def verdict(items):
    must = [i for i in items if i["level"] == "must"]
    should = [i for i in items if i["level"] == "should"]
    if must:
        return "高风险", f"发现 {len(must)} 处必须修改" + (f"，另有 {len(should)} 处建议修改" if should else "")
    if should:
        return "中风险", f"发现 {len(should)} 处建议修改，无法律红线问题"
    return "低风险", "未发现违规词或语义风险"


def render_text(items, meta):
    level, summary = verdict(items)
    must = [i for i in items if i["level"] == "must"]
    should = [i for i in items if i["level"] == "should"]
    L = []
    L.append("=" * 52)
    L.append("医美文案合规检查报告")
    L.append(f"主体：{meta['subject_label']}    平台：{meta['platform_label']}    字数：{meta['chars']}")
    L.append("=" * 52)
    L.append("")
    L.append(f"【风险等级】{level} — {summary}")
    L.append("")
    info = [i for i in items if i["level"] == "info"]
    for title, group in (("必须改（法律红线/平台硬规）", must),
                         ("建议改（影响推流，非法律问题）", should)):
        if not group:
            continue
        L.append(f"■ {title}  {len(group)} 条")
        for i, h in enumerate(group, 1):
            if h["type"] == "word":
                L.append(f" {i}. [{h['category']}] 「{h['word']}」 位置 {h['pos']}")
                if h["basis"]:
                    L.append(f"    依据：{h['basis']}")
                L.append(f"    上下文：{h['context']}")
                if h["suggest"]:
                    L.append(f"    建议改为：{h['suggest']}")
                elif h["note"]:
                    L.append(f"    提示：{h['note']}")
            else:
                L.append(f" {i}. [{h['rule']} {h['name']}]")
                L.append(f"    判定：{h['why']}")
                L.append(f"    命中：{h['context']}")
                if h["suggest"]:
                    L.append(f"    建议改为：{h['suggest']}")
            L.append("")
    if info:
        L.append("■ 可用但需注意（不计入风险）  %d 条" % len(info))
        for h in info:
            L.append(f"   · 「{h['word']}」——{h['category']}")
            if h["basis"]:
                L.append(f"     {h['basis']}")
        L.append("")
    if not must and not should:
        L.append("未发现必须修改或建议修改的问题。")
        L.append("注意：本工具只覆盖文字层，术前术后对比图、二维码、联系方式等")
        L.append("需在图片/视频层人工检查（见 R8）。")
    L.append("-" * 52)
    L.append("免责：本检查基于公开法规与平台规则整理，非平台预审结论。")
    return "\n".join(L)


def render_md(items, meta):
    level, summary = verdict(items)
    must = [i for i in items if i["level"] == "must"]
    should = [i for i in items if i["level"] == "should"]
    L = [f"**风险等级：{level}** — {summary}", ""]
    L.append(f"> 主体 {meta['subject_label']} · 平台 {meta['platform_label']} · {meta['chars']} 字")
    L.append("")
    info = [i for i in items if i["level"] == "info"]
    for title, group in (("必须改", must), ("建议改", should)):
        if not group:
            continue
        L.append(f"### {title}（{len(group)}）")
        L.append("")
        L.append("| # | 问题 | 命中 | 建议 |")
        L.append("|---|---|---|---|")
        for i, h in enumerate(group, 1):
            if h["type"] == "word":
                prob = f"{h['category']}：「{h['word']}」"
            else:
                prob = f"{h['rule']} {h['name']}"
            ctx = h["context"].replace("|", "\\|")[:30]
            sug = (h["suggest"] or h.get("note") or "—").replace("|", "\\|")
            L.append(f"| {i} | {prob} | {ctx} | {sug} |")
        L.append("")
    if info:
        L.append("### 可用但需注意（不计入风险）")
        L.append("")
        L.append("、".join(f"「{h['word']}」" for h in info))
        L.append("")
    return "\n".join(L)


SUBJECT_LABEL = {"clinic": "持证医美机构", "beauty": "生活美容馆", "skincare": "普通美妆护肤"}
PLATFORM_LABEL = {"douyin": "抖音", "xhs": "小红书", "sph": "视频号", "all": "全平台"}


def main():
    ap = argparse.ArgumentParser(description="医美文案合规检查")
    ap.add_argument("--text")
    ap.add_argument("--file")
    ap.add_argument("--subject", default="clinic", choices=list(SUBJECT_LABEL))
    ap.add_argument("--platform", default="all", choices=list(PLATFORM_LABEL))
    ap.add_argument("--ai-generated", action="store_true")
    ap.add_argument("--format", default="text", choices=["text", "md", "json"])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.text:
        text = args.text
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        ap.error("请用 --text / --file 或管道输入文案")
        return

    lex = load_lexicon()
    items = scan_words(text, lex, args.subject, args.platform)
    items += scan_semantics(text)
    ai = ai_label_check(text, args.ai_generated)
    if ai:
        items.append(ai)

    meta = {
        "subject": args.subject, "subject_label": SUBJECT_LABEL[args.subject],
        "platform": args.platform, "platform_label": PLATFORM_LABEL[args.platform],
        "chars": len(text),
    }

    if args.format == "json":
        level, summary = verdict(items)
        print(json.dumps({"level": level, "summary": summary, "meta": meta, "items": items},
                         ensure_ascii=False, indent=2))
    elif args.format == "md":
        print(render_md(items, meta))
    else:
        print(render_text(items, meta))


def selftest():
    """内置样例自检：覆盖主体差异、语义规则、误报控制"""
    lex = load_lexicon()
    cases = [
        ("这个项目很安全，没有副作用", "clinic", ["R1", "R3", "安全"]),
        ("保证你做完比现在好看", "clinic", ["R2"]),
        ("7天淡斑，一个月逆袭", "clinic", ["R5"]),
        ("这个缺陷一定要改善，不然显老又显穷", "clinic", ["R7"]),
        ("科普一下水光针，想做的私信我地址", "clinic", ["R9"]),
        ("学生党专属，暑假变美计划", "clinic", ["R11"]),
        ("术前术后对比图来了", "clinic", ["前后对比"]),
        ("我们家水光针打得挺多的", "clinic", []),           # 持证机构可用，不该报
        ("水光针补水，感兴趣的来", "beauty", ["水光针"]),    # 生活美容馆用了才违规
        ("按多数人的恢复节奏，数周左右逐渐看到变化", "clinic", []),
    ]
    ok = fail = 0
    for text, subject, expect in cases:
        items = scan_words(text, lex, subject, "all") + scan_semantics(text)
        real = [i for i in items if i["level"] in ("must", "should")]   # info 不计风险
        blob = json.dumps(real, ensure_ascii=False)
        miss = [e for e in expect if e not in blob]
        if expect and not real:
            fail += 1; print(f"❌ 漏报: {text[:24]}  期望 {expect}")
        elif miss:
            fail += 1; print(f"❌ 未命中: {text[:24]}  缺 {miss}")
        elif not expect and real:
            fail += 1; print(f"❌ 误报: {text[:24]}  不该报却报了 {[i.get('word') or i.get('rule') for i in real]}")
        else:
            ok += 1; print(f"✅ {text[:26]}  →  风险 {len(real)} 项")
    print(f"\n自检结果：通过 {ok} / {len(cases)}，失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
