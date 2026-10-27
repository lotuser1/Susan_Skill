#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py · 回归自测

用一段**虚构**面诊对话，验证 verify.py 能不能按预期抓出三类核心错误：
  1. 数字编造（金额记错）
  2. 既往史记反（原文否认，档案记成做过）
  3. 项目混淆（把 A 写成 B）

全部为合成数据，不含任何真实客户信息，可安全放在公开仓库。

用法：python3 scripts/selftest.py
退出码：0 全部通过  1 有失败
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

SOURCE = """# 合成对话样例

【说话人1】[00:00:00] 你好，今天想了解什么？
【说话人2】[00:00:05] 我想问一下水光针，我脸上最近特别干。
【说话人1】[00:00:12] 你之前打过水光吗？
【说话人2】[00:00:16] 没打过，我不太敢打针。
【说话人1】[00:00:22] 理解。你这个主要是缺水，建议做三次水光。
【说话人2】[00:00:30] 三次多少钱？
【说话人1】[00:00:34] 一次3800，三次打包11000。
【说话人2】[00:00:41] 有点贵，我朋友在别家做的2000一次。
【说话i1】[00:00:48] 我们用的是进口的，效果不一样。
【说话人2】[00:00:55] 那我考虑一下，下周给你答复。
【说话人1】[00:01:00] 好，活动到月底。
"""

DOC_OK = """# 面诊档案 · 2026-10-22 · 【客户】

## A. 会话信息
- 日期：2026-10-22
- 会话类型：初诊

## C. 主诉
1. 【原话】"我想问一下水光针，我脸上最近特别干"

## E. 既往史
- 注射类：【原话】"没打过，我不太敢打针"
- 过敏史：未询问

## F. 面部评估
| 部位 | 问题 | 依据 | 谁说的 |
|---|---|---|---|
| 面部 | 【转述】缺水 | 现场观察 | 咨询师 |

## G. 推荐方案
| 项目 | 部位 | 剂量 | 提出方 | 客户态度 |
|---|---|---|---|---|
| 水光针 | 面部 | 未提及 | 咨询师建议 | 考虑中 |

## H. 报价与优惠
- 报价：【原话】"一次3800，三次打包11000"

## I. 客户异议
**异议 1**
- 【原话】"有点贵，我朋友在别家做的2000一次"
- 类型：价格
- 咨询师回应：【原话】"我们用的是进口的，效果不一样"
- 是否化解：未化解

## J. 决策状态
- 状态：待定
- 依据：【原话】"那我考虑一下，下周给你答复"

## K. 下一步
| 动作 | 谁来做 | 时间 |
|---|---|---|
| 答复是否做 | 客户 | 下周 |

## L. 合规风险提示
- 未发现

## 本次档案缺口
- 未询问：过敏史
- 未提及：年龄段
"""

# 三个注入错误的变体
DOC_WRONG_NUMBER = DOC_OK.replace("一次3800", "一次4800")
DOC_WRONG_NEGATION = DOC_OK.replace(
    '- 注射类：【原话】"没打过，我不太敢打针"',
    '- 注射类：【转述】客户去年打过水光针',
)
DOC_WRONG_TREATMENT = DOC_OK.replace(
    "| 水光针 | 面部 | 未提及 | 咨询师建议 | 考虑中 |",
    "| 热玛吉 | 面部 | 未提及 | 咨询师建议 | 考虑中 |",
)

CASES = [
    ("正确档案应通过", DOC_OK, 0),
    ("金额编造应被抓出", DOC_WRONG_NUMBER, 1),
    ("既往史记反应被抓出", DOC_WRONG_NEGATION, 1),
    ("项目写错应被抓出", DOC_WRONG_TREATMENT, 1),
]


def run_case(tmp, name, doc, expect_must):
    src = tmp / "source.md"
    src.write_text(SOURCE, encoding="utf-8")
    d = tmp / "doc.md"
    d.write_text(doc, encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(HERE / "verify.py"), str(d),
         "--source", str(src), "--json"],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return False, f"脚本输出不是 JSON：{p.stdout[:200]}{p.stderr[:200]}"
    got = data["must"]
    stat = "通过" if got == expect_must else "失败"
    detail = f"期望必须修 {expect_must} 项，实际 {got} 项"
    if got != expect_must:
        kinds = "、".join(i["kind"] for i in data["issues"] if i["level"] == "must")
        detail += f"（实际报出：{kinds or '无'}）"
    return got == expect_must, f"{stat} · {name} · {detail}"


def main():
    ok = 0
    with tempfile.TemporaryDirectory() as td:
        for name, doc, expect in CASES:
            passed, msg = run_case(Path(td), name, doc, expect)
            print(("[PASS] " if passed else "[FAIL] ") + msg)
            ok += passed
    print(f"\n{ok}/{len(CASES)} 通过")
    sys.exit(0 if ok == len(CASES) else 1)


if __name__ == "__main__":
    main()
