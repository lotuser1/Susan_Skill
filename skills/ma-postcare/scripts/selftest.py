#!/usr/bin/env python3
"""术后须知能力回归测试。

改了时间轴库或脚本必须跑：python3 scripts/selftest.py
"""

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import build  # noqa: E402
import verify  # noqa: E402

DATA = build.load()
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


GOOD = None


def good_doc():
    global GOOD
    if GOOD is None:
        p = build.find_project(DATA, "水光针")
        GOOD = build.render_wechat(p, "王女士", "李医生", "2026-10-27")
    return GOOD


@case("所有项目都能生成微信版且长度够")
def _():
    bad = []
    for p in DATA["projects"]:
        t = build.render_wechat(p, "", "", "2026-10-27")
        if len(t) < 150:
            bad.append(p["name"])
    return not bad, "全部正常" if not bad else f"过短：{bad}"


@case("所有项目生成物都能过校验")
def _():
    bad = []
    for p in DATA["projects"]:
        t = build.render_wechat(p, "客户", "", "2026-10-27")
        res = verify.check(t, "wechat")
        if not res["ok"]:
            bad.append(f"{p['name']}({res['issues'][0]})")
    return not bad, "全部通过" if not bad else f"不合格：{bad}"


@case("三种格式都能出稿")
def _():
    p = build.find_project(DATA, "热玛吉")
    outs = {
        "wechat": build.render_wechat(p, "客户", "", "2026-10-27"),
        "print": build.render_print(p, "客户", "", "2026-10-27"),
        "moments": build.render_moments(p, "", "", "2026-10-27"),
    }
    ok = all(len(v) > 60 for v in outs.values())
    return ok, " / ".join(f"{k} {len(v)} 字" for k, v in outs.items())


@case("朋友圈版也要过校验")
def _():
    p = build.find_project(DATA, "皮秒")
    t = build.render_moments(p, "", "", "2026-10-27")
    res = verify.check(t, "moments")
    return res["ok"], res["issues"][0] if res["issues"] else "通过"


@case("承诺恢复时间要被拦")
def _():
    bad = good_doc().replace("一、这几天的正常反应", "三天就好了，放心。\n\n一、这几天的正常反应")
    res = verify.check(bad)
    return any("承诺恢复时间" in x for x in res["issues"]), "拦住了"


@case("恐吓式提醒要被拦")
def _():
    bad = good_doc() + "\n出现红肿赶紧去医院，否则毁容。"
    res = verify.check(bad)
    return any("恐吓" in x for x in res["issues"]), "拦住了"


@case("下诊断要被拦")
def _():
    bad = good_doc().replace("出现成片红疹、明显瘙痒", "你这是过敏，吃片抗过敏药就好")
    res = verify.check(bad)
    return any("诊断" in x for x in res["issues"]), "拦住了"


@case("具体用药要被拦")
def _():
    bad = good_doc() + "\n每天口服头孢两次。"
    res = verify.check(bad)
    return any("用药" in x for x in res["issues"]), "拦住了"


@case("术后对比要被拦")
def _():
    bad = good_doc() + "\n可以看我们的术前术后对比图。"
    res = verify.check(bad)
    return any("前后对比" in x for x in res["issues"]), "拦住了"


@case("缺免责句要被拦")
def _():
    bad = good_doc().replace("以上是常见情况，具体以李医生的当面交代为准。有不舒服随时联系我们。", "")
    res = verify.check(bad)
    return any("主诊医生交代为准" in x for x in res["issues"]), "拦住了"


@case("缺就医出口要被拦")
def _():
    bad = "当天会有针眼。三天内别桑拿。一周内注意防晒。谢谢。"
    res = verify.check(bad)
    return any("联系" in x for x in res["issues"]), "拦住了"


@case("时间分段不足要被拦")
def _():
    bad = "上面都写好了，照着做。以上是常见情况，具体以医生交代为准，有问题联系。"
    res = verify.check(bad)
    return any("时间分段" in x for x in res["issues"]), "拦住了"


@case("别名能命中正确项目")
def _():
    a = build.find_project(DATA, "水光")
    b = build.find_project(DATA, "超声炮")
    c = build.find_project(DATA, "刷酸")
    ok = a["name"] == "水光针" and b["name"] == "超声炮" and c["name"] == "果酸焕肤"
    return ok, f"{a['name']} / {b['name']} / {c['name']}"


@case("没收录的项目要返回空，不能瞎编")
def _():
    return build.find_project(DATA, "精灵耳") is None, "返回 None，交给上层提示补库"


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
