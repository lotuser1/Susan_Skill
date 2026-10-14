#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用本地样本（samples/*.json）离线校验解析器，不再打线上接口。

硬校验：解析出的字数 ≈ 列表接口给的 wordCount（差异 <5%）。
差太多说明长稿有分页，需要补翻页参数。

用法：
    bash run.sh --parse-samples
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_transcripts import extract_paragraphs, total_words  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def main():
    files = sorted(list(SAMPLES.glob("full_*.json")) + list(SAMPLES.glob("transresult_*.json")))
    _ = sys.argv  # 允许 --parse-samples 等由 run.sh 传入的占位参数
    if not files:
        print(f"❌ {SAMPLES} 下没有样本。请先跑：bash run.sh --probe")
        return 2

    print(f"共 {len(files)} 条样本\n")
    all_ok = True
    for fp in files:
        j = json.loads(fp.read_text(encoding="utf-8"))
        item = j.get("listItem") or {}
        probe = j.get("probe") or {}
        resp = j.get("resp") or {}

        # 取 result 原始字符串：probe 结构里没有全文，只有骨架；
        # 真正带全文的是 resp.data.result 或 probe 里的 data
        raw = None
        if isinstance(resp.get("data"), dict):
            raw = resp["data"].get("result")
        if raw is None and isinstance(probe.get("data"), dict):
            raw = probe["data"].get("result")
        if raw is None:
            print(f"— {fp.name}：样本里没有 result 字段（探测阶段只存了骨架），跳过")
            continue

        parsed = json.loads(raw) if isinstance(raw, str) else raw
        paras = extract_paragraphs(parsed)
        got = total_words(paras)
        want = item.get("wordCount") or 0
        ratio = (got / want) if want else 0
        flag = "✅" if (want == 0 or ratio >= 0.95) else "⚠️"
        if want and ratio < 0.95:
            all_ok = False
        print(f"{flag} {fp.name}")
        print(f"   段落 {len(paras)} 段 · 解析 {got} 字 · 接口 wordCount {want} 字"
              f" · 覆盖率 {ratio * 100:.1f}%")
        if paras:
            spk = sorted({p["speaker"] for p in paras})
            print(f"   说话人：{spk}")
            head = paras[0]["sentences"][0]["text"] if paras[0]["sentences"] else ""
            print(f"   首句：【说话人{paras[0]['speaker']}】{head[:60]}")

    print()
    if all_ok:
        print("✅ 解析器校验通过，可以小批量试跑：bash run.sh --account acc1 --limit 5 --apply")
        return 0
    print("⚠️ 存在覆盖率不足的样本，可能是长稿分页。请先解决分页再批量下载。")
    return 3


if __name__ == "__main__":
    sys.exit(main())
