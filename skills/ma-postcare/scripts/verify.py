#!/usr/bin/env python3
"""术后须知校验：查合规话术和必备结构。

用法：
  python3 verify.py 须知.md --format wechat
  python3 verify.py 须知.md --json
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROMISE_TIME = re.compile(
    r"([0-9一二三四五六七八九十两]+)\s*(天|周|个月|小时|日)[^。！？\n]{0,8}?"
    r"(就好|就好了|就能好|即可恢复|就能恢复|保证恢复|肯定好|准好|就没事)"
)
PROMISE_ABS = re.compile(
    r"保证|百分百|百分之百|一定能|绝对不会(留疤|反弹|复发|变黑|肿)|彻底(根除|解决|消除)|永久(有效|保持|不反弹)"
)
SCARE = re.compile(r"否则|后果自负|毁容|烂脸|赶紧去医院|立刻去医院|别怪我们|哭都来不及")
DIAGNOSE = re.compile(r"你这是|诊断为|属于感染|是过敏|你已经感染|可以确定是|确诊")
MEDICINE = re.compile(r"(吃|服用|口服|外涂|涂抹)[^。\n]{0,8}(头孢|阿莫西林|消炎药|抗生素|激素|药膏|布洛芬)")
DISCLAIM = re.compile(
    r"以[^。！？\n]{0,8}(医生|医师|医嘱)[^。！？\n]{0,12}(为准|交代)|遵医嘱"
)
CONTACT = re.compile(r"联系|复诊|回院|来院")
COMPARE = re.compile(r"前后对比|术前术后|对比图|before.?after", re.I)
SOOTHE = re.compile(r"没事的|不用担心|肯定没问题|完全正常")

TIME_MARK = re.compile(
    r"当天|24\s*小时|48\s*小时|[0-9一二三四五六七八九十]+\s*(天|周|个月|小时)"
)


def check(text, fmt="wechat"):
    issues, warns = [], []
    n = len(re.sub(r"\s", "", text))

    # 1. 免责句
    if not DISCLAIM.match(text) and not DISCLAIM.search(text):
        issues.append("缺「以主诊医生交代为准」——没有这句就是在替医生下医嘱")

    # 2. 就医出口
    if not CONTACT.search(text):
        issues.append("通篇没有「联系 / 复诊」——客户出问题时不知道找谁")

    # 3. 时间轴分段
    marks = len(TIME_MARK.findall(text))
    need = 3 if fmt in ("wechat", "print") else 2
    if marks < need:
        issues.append(f"时间分段只有 {marks} 处，至少要 {need} 处——术后须知必须按时间讲")

    # 4. 疗效时间承诺
    for m in PROMISE_TIME.finditer(text):
        issues.append(f"承诺恢复时间：「{m.group(0)}」——改成「一般…，具体看个人」")
        break
    for m in PROMISE_ABS.finditer(text):
        issues.append(f"绝对化表述：「{m.group(0)}」——术后没有百分百")
        break

    # 5. 恐吓
    for m in SCARE.finditer(text):
        issues.append(f"恐吓式提醒：「{m.group(0)}」——改成「出现…请联系医生」")
        break

    # 6. 诊断
    for m in DIAGNOSE.finditer(text):
        issues.append(f"下了诊断：「{m.group(0)}」——把判断权交回医生")
        break

    # 7. 具体用药
    for m in MEDICINE.finditer(text):
        issues.append(f"给了具体用药：「{m.group(0)}」——药名剂量属于医嘱")
        break

    # 8. 术前术后对比
    for m in COMPARE.finditer(text):
        issues.append(f"提到前后对比：「{m.group(0)}」——医疗广告高风险项，一张都不要")
        break

    # 9. 安抚式下结论（不拦，但要提醒）
    for m in SOOTHE.finditer(text):
        warns.append(f"「{m.group(0)}」——别替医生保证没事，改成「大多数人会有类似反应」")
        break

    if n < 80:
        warns.append(f"全文只有 {n} 字，可能是片段；完整须知一般 150–600 字")

    return {
        "ok": not issues,
        "chars": n,
        "time_marks": marks,
        "issues": issues,
        "warns": warns,
    }


def render(res):
    lines = [f"字数：{res['chars']} · 时间分段：{res['time_marks']} 处", ""]
    if res["issues"]:
        lines.append(f"不通过（{len(res['issues'])} 项）：")
        lines += [f"  [失败] {x}" for x in res["issues"]]
    else:
        lines.append("合规与结构检查通过：免责句、就医出口、时间分段都在，没有疗效承诺和诊断话术。")
    if res["warns"]:
        lines.append("")
        lines += [f"  [注意] {x}" for x in res["warns"]]
    lines.append("")
    lines.append("结论：可以发。" if res["ok"] else "结论：不合格，按 [失败] 项改完重跑。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="校验术后须知")
    ap.add_argument("file", help="须知文件")
    ap.add_argument("--format", default="wechat", choices=["wechat", "print", "moments"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        sys.exit(f"文件不存在：{p}")
    res = check(p.read_text(encoding="utf-8"), args.format)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
