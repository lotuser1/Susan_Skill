#!/usr/bin/env python3
"""去 AI 味能力的回归测试。

改了特征库或脚本必须跑一遍：python3 scripts/selftest.py
"""

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import scan_ai  # noqa: E402
import check_rewrite  # noqa: E402

AI_TEXT = (
    "在当今医美行业快速发展的时代，越来越多的人开始关注面部抗衰。值得注意的是，"
    "抗衰并不是越早越好，而是要根据个人的皮肤状态来决定。首先，要了解自己的皮肤类型；"
    "其次，要建立合理的预期；最后，要选择正规的机构。为什么有些人做完项目效果不明显？"
    "因为没有做好术前评估。这不仅影响效果，而且会增加风险。综上所述，抗衰的核心在于科学认知。"
    "希望这篇文章对你有帮助。"
)

HUMAN_TEXT = (
    "上周三下午来了个姐姐，42 岁，坐下第一句话就是「我这个脸是不是没救了」。"
    "我先让她咬了下牙，摸了摸咬肌，再看下颌缘。她其实不是松，是瘦，脸颊凹下去，显得法令纹深。"
    "我跟她说，你这不用做，先吃胖三斤再来。她笑了半天。面诊 10 个里大概有 3 个是这种情况，"
    "不是没救，是被告知得太夸张。"
)

GOOD_REWRITE = (
    "抗衰这件事，我一般不建议一上来就做项目。先看她皮肤现在是什么状态，再问她到底想改善哪儿。"
    "很多人做完觉得没效果，问题往往出在术前没评估清楚，钱花了，脸还是那个脸。"
    "皮肤状态不一样，能做的事也不一样，这个得当面看。我见过太多人，一进门就说要抗衰，"
    "其实她只是最近没睡好。"
)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def hits_of(text):
    res = scan_ai.analyze(text)
    return {h["id"]: h for h in res["hits"]}, res


@case("典型 AI 文案要高分")
def _():
    _, res = hits_of(AI_TEXT)
    return res["score"] >= 70, f"得分 {res['score']}"


@case("人写的口语稿要低分")
def _():
    _, res = hits_of(HUMAN_TEXT)
    return res["score"] <= 20, f"得分 {res['score']}"


@case("能抓出「首先其次最后」")
def _():
    h, _ = hits_of(AI_TEXT)
    return "ai_enum" in h, "命中 ai_enum" if "ai_enum" in h else "没命中"


@case("能抓出设问自答")
def _():
    h, _ = hits_of(AI_TEXT)
    return "ai_self_q" in h, "命中 ai_self_q" if "ai_self_q" in h else "没命中"


@case("能抓出「不是…而是」")
def _():
    h, _ = hits_of(AI_TEXT)
    return "ai_not_but" in h, "命中 ai_not_but" if "ai_not_but" in h else "没命中"


@case("医美术语不算 AI 味")
def _():
    txt = (
        "苹果肌下垂的人，下颌缘多半也糊。我让她先做咬肌评估，再看要不要动中面部。"
        "水光针这东西，打之前先看皮肤屏障。"
    )
    _, res = hits_of(txt)
    return res["score"] <= 25, f"得分 {res['score']}（术语不该被当成 AI 特征）"


@case("合格改写要通过")
def _():
    res = check_rewrite.check(AI_TEXT, GOOD_REWRITE)
    return res["ok"], "；".join(res["issues"]) or f"AI 分 {res['before']}→{res['after']}"


@case("编造数字要被拦")
def _():
    bad = "上周来了个 38 岁的客人，油皮。抗衰别急，先看皮肤状态再谈，术前没评估清楚，做完就没效果。"
    res = check_rewrite.check(AI_TEXT, bad)
    return any("数字" in x for x in res["issues"]), "拦住了新增数字" if res["issues"] else "没拦住"


@case("编造客户原话要被拦")
def _():
    bad = GOOD_REWRITE.replace(
        "我见过太多人", "有个客人说「我这脸是不是没救了」。我见过太多人"
    )
    res = check_rewrite.check(AI_TEXT, bad)
    return any("原话" in x for x in res["issues"]), "拦住了编造对话" if res["issues"] else "没拦住"


@case("砍太短要被拦")
def _():
    res = check_rewrite.check(AI_TEXT, "抗衰别急，先看皮肤状态再说。")
    return any("信息删太多" in x for x in res["issues"]), "拦住了过度删减"


@case("只换同义词要被拦")
def _():
    swapped = (
        "在现今医美行业高速发展的时期，愈发多的人开始关注面部抗衰。需要注意的是，抗衰并非是越早越好，"
        "而是要根据个人的皮肤状态来决定。首先要了解自己的皮肤类型，其次要建立合理的预期，"
        "最后要选择正规的机构。为什么有些人做完项目效果不明显？因为没有做好术前评估。"
        "这不仅影响效果，而且会增加风险。总而言之，抗衰的核心在于科学认知。"
    )
    res = check_rewrite.check(AI_TEXT, swapped)
    return (not res["ok"]) and any("仍然偏高" in x or "同义词" in x for x in res["issues"]), (
        f"AI 分 {res['before']}→{res['after']}，已判不合格"
    )


@case("段落句子等长要被识别")
def _():
    txt = (
        "第一句话讲的是原因。\n第二句话讲的是现象。\n第三句话讲的是结果。\n"
        "第四句话讲的是影响。\n第五句话讲的是办法。\n第六句话讲的是总结。"
    )
    h, res = hits_of(txt)
    ok = ("ai_even_para" in h) or ("ai_even_sent" in h)
    return ok, f"得分 {res['score']}，排版特征已识别"


@case("空输入不崩")
def _():
    return scan_ai.analyze("") is None, "返回 None，未抛异常"


def main():
    passed = 0
    for name, fn in CASES:
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"抛异常：{e}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name} · {msg}")
        passed += 1 if ok else 0
    print(f"\n{passed}/{len(CASES)} 通过")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
