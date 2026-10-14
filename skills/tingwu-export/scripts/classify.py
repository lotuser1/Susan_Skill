#!/usr/bin/env python3
"""
按关键词词典对转写稿做板块归类（第一遍：关键词打底）。

思路（用户拍板：关键词打底 + AI 判疑难）：
  1. 每篇稿子统计各板块命中的**不同关键词数**（去重，防单词刷分）+ 总命中次数（小权重）
  2. 取总分最高的板块；平局时按客户旅程后置优先（获客→聊客→设计→售后，越靠后越优先）
  3. confidence 由「命中强度 + 领先幅度」算出：
       >= 0.6  → 关键词可信，直接归类
       <  0.6  → 交 AI 精读判疑难（本脚本标为 pending_ai）

用法：
    python3 scripts/classify.py --sample 400          # 抽样试跑，看词典效果
    python3 scripts/classify.py --account acc3        # 只跑某账户
    python3 scripts/classify.py --apply               # 全量跑，写 plan.json
"""
import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_transcripts import INBOX  # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
DICT_FILE = SKILL / "references" / "关键词词典.json"
SCENE_FILE = SKILL / "references" / "场景信号.json"
PLAN_FILE = SKILL / "state" / "plan.json"

FM = re.compile(r"^---\n.*?\n---\n", re.S)
# 客户旅程后置优先：越靠后的环节，平局时越优先
JOURNEY_PRIORITY = ["01 流量获客", "02 聊客成交", "03 美学设计",
                    "04 医学材料学", "05 医疗售后", "06 团队搭建", "07 商业认知"]


def load_dict():
    d = json.loads(DICT_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_scene():
    if not SCENE_FILE.exists():
        return None
    d = json.loads(SCENE_FILE.read_text(encoding="utf-8"))
    return {"clinic": d["clinic"], "lecture": d["lecture"],
            "scope": d.get("scope", [])}


def speakers_of(path):
    """从 front-matter 取发言人数；取不到返回 0。"""
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:1200]
    except Exception:  # noqa: BLE001
        return 0
    m = re.search(r"^speakers:\s*\[([^\]]*)\]", head, re.M)
    if m:
        return len([x for x in m.group(1).split(",") if x.strip()])
    return 0


def scene_of(text, scene):
    """返回 (clinic去重命中数, lecture去重命中数, [命中的clinic词])"""
    if not scene:
        return 0, 0, []
    chits, lhits = [], []
    for w in scene["clinic"]["words"]:
        if text.count(w):
            chits.append(w)
    for w in scene["lecture"]["words"]:
        if text.count(w):
            lhits.append(w)
    return len(chits), len(lhits), chits


def body_of(path, max_chars):
    """取正文（跳过 front-matter），限制长度以提速。"""
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    txt = FM.sub("", txt, count=1)
    return txt[:max_chars]


def score_one(text, dic):
    """返回 {板块: (命中不同词数, 总命中次数, [命中的词])}"""
    out = {}
    for cat, cfg in dic.items():
        words = cfg["words"]
        w = float(cfg.get("weight", 1.0))
        hits, total = [], 0
        for word in words:
            c = text.count(word)
            if c:
                hits.append(word)
                total += c
        # 去重词数为主，总次数取平方根作为次要信号（防长稿刷分）
        sc = (len(hits) + min(total ** 0.5, len(hits) * 2) * 0.25) * w
        out[cat] = (sc, len(hits), total, hits)
    return out


def apply_scene(scores, clinic_n, lecture_n, sp, scene, chits=None,
                before="", scope_all=False):
    """
    面诊现场判定（用户拍板：面诊稿归 02 聊客成交）。

    触发条件（全部满足）：
      1. 发言人数 >= 2                  —— 有客户在场
      2. clinic 去重命中 >= min_hits     —— 确实在对客户说话
      3. clinic > lecture * 1.5          —— 不是讲课/培训
      4. 主题最高分板块在 scope 内        —— 只纠正被 03/04 吸走的面诊稿，
                                            不动 05售后/06团队/01获客/07商业，
                                            避免「招聘入职、内部培训」被误判成面诊
    效果：给 02 加 bonus 分（加权，非硬改判）。
    """
    if not scene or sp < 2:
        return scores, "", False
    cfg = scene["clinic"]
    if clinic_n < cfg.get("min_hits", 3):
        return scores, "", False
    if clinic_n <= lecture_n * 1.5:
        return scores, "", False
    if not scope_all and before and before not in scene.get("scope", []):
        return scores, "", False
    cat = "02 聊客成交"
    if cat not in scores:
        return scores, "", False
    sc, ndist, total, hits = scores[cat]
    bonus = float(cfg.get("bonus", 7.0))
    # 02 主题词零命中时，用 clinic 场景词兜底作证据，避免被判成「无法鉴别」
    if ndist == 0:
        ndist, total, hits = 1, clinic_n, (chits or [])[:8]
    scores[cat] = (sc + bonus, ndist, total, hits)
    return scores, f"面诊现场（{sp}人对话·客户词{clinic_n} vs 讲课词{lecture_n}）→ 加权给 02", ndist <= 1


def decide(scores, scene_note=""):
    """选最高分板块；平局按 journey 后置优先。返回 (target, confidence, reason, top_hits)
    scene_note: (说明文本, 是否仅靠场景证据(主题词零命中))
    """
    ranked = sorted(scores.items(),
                    key=lambda kv: (-kv[1][0], -JOURNEY_PRIORITY.index(kv[0])))
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else (None, (0, 0, 0, []))
    cat, (sc, ndist, total, hits) = top

    if ndist == 0:
        return "08 无法鉴别", 0.0, "未命中任何板块关键词", []

    s2 = second[1][0] if second[0] else 0
    lead = (sc - s2) / sc if sc else 0
    # 命中强度：命中 ≥6 个不同词算很强，1~2 个算弱
    strength = min(ndist / 6.0, 1.0)
    conf = round(min(0.95, 0.35 + strength * 0.45 + lead * 0.25), 2)

    top_hits = sorted(hits, key=lambda w: -len(w))[:8]
    prefix = f"[{scene_note[0]}] " if scene_note else ""
    reason = prefix + (f"命中 {ndist} 个词（共 {total} 次）：{'、'.join(top_hits)}"
                       if ndist else "未命中任何板块关键词")
    # 面诊现场是强场景证据，置信度至少 0.7；
    # 但仅靠场景撑起（主题词零命中）的给 0.6，留待复核
    if scene_note:
        conf = 0.6 if scene_note[1] else max(conf, 0.7)
    return cat, conf, reason, top_hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="", help="只跑某账户，如 acc3")
    ap.add_argument("--exclude", default="", help="排除某账户，逗号分隔，如 acc5")
    ap.add_argument("--out", default="", help="plan 输出路径（默认 state/plan.json）")
    ap.add_argument("--sample", type=int, default=0, help="随机抽样 N 篇试跑")
    ap.add_argument("--max-chars", type=int, default=20000, help="每篇最多读多少字")
    ap.add_argument("--apply", action="store_true", help="全量跑并写 plan.json")
    ap.add_argument("--scene-scope", default="", choices=["", "all"],
                    help="all=对所有板块施加面诊加权（默认只纠正 03/04，避免误伤团队/售后稿）")
    a = ap.parse_args()

    dic = load_dict()
    scene = load_scene()
    skip = {x.strip() for x in a.exclude.split(",") if x.strip()}
    print(f"词典 {len(dic)} 个板块 · 场景层：{'已启用' if scene else '未启用'}"
          + (f" · 排除账户 {sorted(skip)}" if skip else ""))
    files = []
    for acc in sorted(INBOX.iterdir()):
        if not acc.is_dir():
            continue
        if a.account and acc.name != a.account:
            continue
        if acc.name in skip:
            continue
        files.extend(sorted(acc.glob("*.md")))

    if a.sample and a.sample < len(files):
        random.seed(42)
        files = random.sample(files, a.sample)

    print(f"待归类 {len(files)} 篇 · 词典 {len(dic)} 个板块 · 每篇最多读 {a.max_chars:,} 字")
    t0 = time.time()
    plan, cat_dist, weak = [], Counter(), []
    stats = Counter()
    for i, fp in enumerate(files, 1):
        text = body_of(fp, a.max_chars)
        scores = score_one(text, dic)
        sp = speakers_of(fp)
        cn, ln, chits = scene_of(text, scene)
        before = max(scores.items(), key=lambda kv: kv[1][0])[0] \
            if any(v[1] for v in scores.values()) else "08 无法鉴别"
        scores, note, scene_only = apply_scene(scores, cn, ln, sp, scene, chits,
                                               before, a.scene_scope == "all")
        target, conf, reason, hits = decide(scores, (note, scene_only) if note else "")
        stats["场景触发"] += 1 if note else 0
        if note and before != target:
            stats[f"  {before} → {target}"] += 1
        plan.append({"id": i, "path": str(fp), "name": fp.name,
                     "target": target, "confidence": conf,
                     "reason": reason, "hits": hits,
                     "speakers": sp, "scene": note})
        cat_dist[target] += 1
        if conf < 0.6:
            weak.append((fp.name, target, conf, reason))
        if i % 300 == 0:
            print(f"  …{i}/{len(files)}（{time.time() - t0:.0f}s）")

    print(f"\n耗时 {time.time() - t0:.0f}s")
    print("\n=== 归类分布 ===")
    for k, v in sorted(cat_dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v} 篇 ({v / len(files) * 100:.1f}%)")
    if stats:
        print(f"\n=== 面诊场景层效果（触发 {stats['场景触发']} 篇）===")
        for k, v in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v} 篇")
    conf_dist = Counter()
    for p in plan:
        c = p["confidence"]
        conf_dist["关键词可信(>=0.6)" if c >= 0.6 else
                  ("无命中(08无法鉴别)" if c == 0 else "待AI判(0<conf<0.6)")] += 1
    print("\n=== 置信度分布 ===")
    for k, v in sorted(conf_dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v} 篇 ({v / len(files) * 100:.1f}%)")
    if weak:
        print(f"\n=== 低置信样稿（共 {len(weak)} 篇，前 8 篇）===")
        for nm, tg, cf, rs in weak[:8]:
            print(f"  {nm[:40]:40s} → {tg} ({cf}) {rs[:56]}")

    if a.apply:
        out = Path(a.out) if a.out else PLAN_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n✅ 已写 {out}（{len(plan)} 条）")
    else:
        print("\n（试跑模式，未写 plan.json。加 --apply 写入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
