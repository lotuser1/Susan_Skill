#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标题自检的回归测试。用虚构标题验证校验逻辑本身没坏。

    python3 selftest.py

改了词表或 check_titles.py 之后必须跑一遍，全过才发版。
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_titles as ct  # noqa: E402

LEX = ct.load_own() + ct.load_merged("clinic")[0]
# 装了「合规文案审查」才能做的交叉检查，没装就跳过而不是判失败
HAS_MERGED = ct.find_sibling_lexicon() is not None


def run(titles, platform="xhs", lexicon=None):
    lex = lexicon if lexicon is not None else LEX
    results = [ct.check_one(t, "", platform, lex) for t in titles]
    return results


def must_count(results):
    return sum(1 for r in results if r["issues"])


def hit_words(results):
    out = set()
    for r in results:
        for line in r["issues"]:
            if "「" in line:
                out.add(line.split("「")[1].split("」")[0])
    return out


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("合规标题应全部通过")
def _():
    titles = [
        "越贵的水光针，可能越不适合你的脸",
        "第一次打水光的人，最容易忽略这一步",
        "打水光前，先确认这 3 件事",
        "水光打完一周就没效果，是打少了吗",
        "这 2 种情况，先别急着打水光",
    ]
    return must_count(run(titles)) == 0, "应 0 项必须改"


@case("疗效承诺应被抓出")
def _():
    r = run(["一次水光针永久美白"])
    return "永久" in hit_words(r), "应报出「永久」"


@case("容貌焦虑与恐吓应被抓出")
def _():
    r = run(["30岁以后脸就垮了，再不补水就晚了"])
    w = hit_words(r)
    return {"垮了", "就晚了"} <= w, "应报出「垮了」「就晚了」"


@case("导流词应被抓出")
def _():
    r = run(["面诊必问的5个问题，私信我发你"])
    return "私信我" in hit_words(r), "应报出「私信我」"


@case("超字数应被抓出")
def _():
    r = run(["这是一个特别特别长的标题用来测试小红书二十个字的上限到底会不会被拦住"])
    return any("超字数" in x for x in r[0]["issues"]), "应报「超字数」"


@case("同质化开头应被提醒")
def _():
    titles = ["水光针怎么选才对", "水光针打几次有效", "水光针会不会疼", "热玛吉适合谁", "光子嫩肤疼吗", "瘦脸针会垮脸吗"]
    return any("同质化" in w for w in ct.check_batch(run(titles))), "应提醒同质化"


@case("「第一次」不应被「第一」误报")
def _():
    r = run(["第一次做医美的人该知道的事"])
    return must_count(r) == 0, "「第一次」属正常表达，不该报绝对化用语"


@case("「最好别」不应被「最好」误报")
def _():
    r = run(["这几种情况最好先别做"])
    return must_count(r) == 0, "「最好先别」属建议语气，不该报绝对化用语"


@case("绝对化用语应被抓出（本能力自带，不依赖合规词库）")
def _():
    r = run(["全国第一的水光针品牌"])
    return "全国第一" in hit_words(r), "应报出「全国第一」"


@case("个人博主用诊疗项目名应被提醒")
def _():
    if not HAS_MERGED:
        return None, "跳过：未装「合规文案审查」，此项依赖它的诊疗用语类"
    lex_personal = ct.load_own() + ct.load_merged("personal")[0]
    r = run(["水光针到底值不值得做"], lexicon=lex_personal)
    return any("水光针" in x for x in r[0]["warns"] + r[0]["issues"]), "无资质主体提诊疗项目名应提醒"


@case("持证机构用诊疗项目名不应被报")
def _():
    r = run(["水光针到底值不值得做"])
    word_hits = [x for x in r[0]["warns"] + r[0]["issues"] if "「" in x]
    return not word_hits, f"持证机构可正常使用项目名，实际 {word_hits}"


@case("列表序号应被剥掉，裸数字不能剥")
def _():
    rows = ct.parse_input(type("A", (), {"text": "1. 第一条标题\n2、第二条标题\n30岁以后该注意什么", "file": None})())
    words = [t for t, _ in rows]
    return words == ["第一条标题", "第二条标题", "30岁以后该注意什么"], f"实际 {words}"


def main():
    passed, skipped = 0, 0
    for name, fn in CASES:
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"抛异常：{e}"
        if ok is None:
            print(f"[SKIP] {name} · {msg}")
            skipped += 1
            continue
        print(f"[{'PASS' if ok else 'FAIL'}] {name} · {msg}")
        passed += 1 if ok else 0
    total = len(CASES) - skipped
    tail = f"，{skipped} 项跳过" if skipped else ""
    print(f"\n{passed}/{total} 通过{tail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
