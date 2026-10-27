#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare.py · 面诊转写稿预处理

干三件事：
  1. 解析说话人结构，推断谁可能是咨询师、谁是客户（不靠编号，靠内容）
  2. 长稿切成可逐块提取的片段（避免一次读完漏信息）
  3. 抽出所有数字锚点，供 verify.py 做反幻觉校验

只用标准库，无需 pip 安装任何东西。

用法：
    python3 prepare.py 转写稿.md                  # 完整报告
    python3 prepare.py 转写稿.md --chunks-only    # 只要切块
    python3 prepare.py 转写稿.md --json           # 机器可读
    python3 prepare.py 转写稿.md --size 3000      # 每块字数
    python3 prepare.py 转写稿.md --out ./tmp      # 落盘（chunks.md / anchors.json）
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 解析

UTTER_RE = re.compile(
    r"^【\s*(说话人\s*\d+|[^】]{1,16}?)\s*】\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)?\]?\s*(.*)$"
)
# 微信聊天记录常见格式：客户：…… / 医生: ……
NAME_RE = re.compile(r"^([\u4e00-\u9fa5A-Za-z]{1,6})[：:]\s*(.+)$")
DATE_RE = re.compile(r"(20\d{2})[-_./]?(\d{2})[-_./]?(\d{2})")


def parse_frontmatter(text):
    """极简 YAML 前置块解析，只取本脚本需要的几个字段。"""
    meta = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    block = text[3:end]
    for line in block.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            meta[k] = v
    return meta, text[end + 4:]


def parse_utterances(body):
    """返回 [{speaker, time, text}]。

    三种输入都要能处理：
      1. 通义听悟转写稿： 【说话人1】[00:01] 内容
      2. 微信聊天记录：   客户：内容
      3. 纯文本：         每行一条，speaker=None

    注意：只有带说话人标记的稿子才做「续行拼接」（长句会折行）；
    没有标记的稿子必须按行独立，否则整篇会粘成一条。
    """
    lines = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        lines.append(line)

    tagged = []
    has_tag = False
    for line in lines:
        m = UTTER_RE.match(line)
        if m and m.group(3).strip():
            has_tag = True
            tagged.append({"speaker": m.group(1).replace(" ", ""),
                           "time": m.group(2) or "", "text": m.group(3).strip()})
        elif tagged:
            tagged[-1]["text"] += line          # 转写稿的折行
        else:
            tagged.append({"speaker": None, "time": "", "text": line})

    if has_tag:
        return tagged

    # 无标记：尝试「名字：内容」，否则一行一条
    out = []
    for line in lines:
        m = NAME_RE.match(line)
        if m:
            out.append({"speaker": m.group(1), "time": "", "text": m.group(2).strip()})
        elif out:
            out[-1]["text"] += line
        else:
            out.append({"speaker": None, "time": "", "text": line})
    return out


def guess_date(meta, filename):
    for src in (meta.get("原标题", ""), meta.get("original_name", ""),
                meta.get("title", ""), filename):
        if not src:
            continue
        m = DATE_RE.search(str(src))
        if m:
            y, mo, d = m.groups()
            if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
                return f"{y}-{mo}-{d}"
    return None


# ---------------------------------------------------------------- 角色推断

# 服务方强特征（几乎只有服务方会说）
SERVICE_STRONG = [
    "我给你", "给你安排", "帮你设计", "给你看看", "先给你", "我给你调",
    "我们医院", "我们机构", "我们这边", "我们门诊", "我们店", "咱们这边",
    "到院", "我建议", "预约", "复诊", "术后", "术前", "忌口", "注意事项", "面诊",
]
# 服务方弱特征（问诊话术，医生也会问，但客户很少问）
SERVICE_ASK = [
    "你之前", "做过什么", "做过吗", "有没有做", "过敏", "多久了",
    "什么时候做", "多大", "你的情况", "你这个",
]
# 客户强特征
CLIENT_STRONG = [
    "我想", "我怕", "我要", "我朋友", "我老公", "我妈", "我同事",
    "网上说", "抖音上", "小红书上", "我考虑", "我回去", "我寻思",
]
# 客户询价（价格话题基本由客户发起）
CLIENT_PRICE = ["多少钱", "贵不贵", "太贵", "便宜", "优惠", "价格", "能不能少", "搞活动", "打折"]

QUESTION_RE = re.compile(r"[？?]|吗[。，！!]?$|多少|几个|几次|多久|会不会|是不是")

# 说话人名字本身就是角色词时，直接采信 —— 比任何打分都准
ROLE_BY_NAME = {
    "客户": "客户", "顾客": "客户", "患者": "客户", "求美者": "客户",
    "医生": "服务方（医生）", "院长": "服务方（医生）",
    "咨询师": "服务方（咨询师）", "顾问": "服务方（咨询师）",
    "前台": "助理/前台", "助理": "助理/前台", "护士": "助理/前台",
}


def load_lexicon(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return [], [], {}
    words, by_group = [], {}
    for key, grp in data.get("groups", {}).items():
        by_group[key] = grp.get("label", key)
        for w in grp.get("words", []):
            words.append(w)
            by_group.setdefault("_map", {})[w] = grp.get("label", key)
    return words, [], data


def classify_speakers(utts, pro_words):
    """给每个说话人打分，返回统计 + 角色猜测。

    设计要点（踩过的坑）：
      - 不能把"提问多"当客户特征 —— 医生问诊时提问最多
      - 不能只看专业词密度 —— 短发言密度虚高，会把陪同人员判成医生
      - 客户也会说项目名，所以专业词只给低权重
    """
    stats = {}
    for u in utts:
        spk = u["speaker"] or "（无说话人标记）"
        s = stats.setdefault(spk, {
            "speaker": spk, "turns": 0, "chars": 0,
            "questions": 0, "pro_hits": 0,
            "service_hits": 0, "ask_hits": 0,
            "client_hits": 0, "price_hits": 0,
            "pro_samples": [],
        })
        txt = u["text"]
        s["turns"] += 1
        s["chars"] += len(txt)
        if QUESTION_RE.search(txt):
            s["questions"] += 1
        hits = [w for w in pro_words if w in txt]
        if hits:
            s["pro_hits"] += len(hits)
            for h in hits[:3]:
                if h not in s["pro_samples"]:
                    s["pro_samples"].append(h)
        s["service_hits"] += sum(1 for p in SERVICE_STRONG if p in txt)
        s["ask_hits"] += sum(1 for p in SERVICE_ASK if p in txt)
        s["client_hits"] += sum(1 for p in CLIENT_STRONG if p in txt)
        s["price_hits"] += sum(1 for p in CLIENT_PRICE if p in txt)

    total_chars = sum(s["chars"] for s in stats.values()) or 1
    max_pro = max((s["pro_hits"] for s in stats.values()), default=0)

    for s in stats.values():
        chars = max(s["chars"], 1)
        s["share"] = round(s["chars"] / total_chars * 100, 1)
        s["pro_density"] = round(s["pro_hits"] / chars * 1000, 1)

        svc = s["service_hits"] * 3 + s["ask_hits"] * 1.5 + s["pro_hits"] * 0.4
        cli = s["client_hits"] * 3 + s["price_hits"] * 2.5
        # 主导方加成：说了一半以上、且专业词绝对数最多 —— 通常是服务方
        if s["chars"] >= total_chars * 0.5 and s["pro_hits"] == max_pro and max_pro > 0:
            svc += 3
        s["score_service"] = round(svc, 1)
        s["score_client"] = round(cli, 1)

        # 样本太少的说话人不给角色结论，避免密度虚高误判
        if s["speaker"] in ROLE_BY_NAME:
            s["role_hint"] = f"{ROLE_BY_NAME[s['speaker']]}（据称呼直接判定）"
        elif s["chars"] < 200 or s["turns"] < 3:
            s["role_hint"] = "发言过少，无法判断（可能是陪同或助理）"
        elif svc >= cli * 1.5 and svc > 3:
            s["role_hint"] = "疑似服务方（咨询师/医生）"
        elif cli >= svc * 1.5 and cli > 3:
            s["role_hint"] = "疑似客户"
        else:
            s["role_hint"] = "待确认（两侧特征接近，按内容人工判断）"
    return stats


# ---------------------------------------------------------------- 切块

def make_chunks(utts, size):
    chunks, cur, cur_len, start_idx = [], [], 0, 1
    for i, u in enumerate(utts, 1):
        cur.append((i, u))
        cur_len += len(u["text"])
        if cur_len >= size:
            chunks.append((start_idx, i, cur))
            start_idx = i + 1
            cur, cur_len = [], 0
    if cur:
        chunks.append((start_idx, cur[-1][0], cur))
    return chunks


def render_chunk(idx, total, chunk):
    a, b, items = chunk
    chars = sum(len(u["text"]) for _, u in items)
    lines = [f"### 片段 {idx}/{total}（第 {a}–{b} 句 · 约 {chars} 字）", ""]
    for i, u in items:
        spk = u["speaker"] or "—"
        ts = f"[{u['time']}] " if u["time"] else ""
        lines.append(f"{i}. 【{spk}】{ts}{u['text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 数字锚点

NUM_PATTERNS = [
    (r"\d+(?:\.\d+)?\s*(?:万元|万|千元|千|元|块钱|块|w|W)", "金额"),
    (r"[一二三四五六七八九十几两]+(?:万|千|百)?(?:元|块钱|块)", "金额-中文"),
    (r"\d+\s*(?:支|瓶|针|单位|iu|IU|u|U|cc|CC|ml|ML|毫升|发)", "剂量"),
    (r"\d+\s*岁", "年龄"),
    (r"\d+\s*(?:个疗程|疗程|次|回)", "次数"),
    (r"\d+\s*(?:年|个月|月|周|星期|天|日)(?:前|后|左右)?", "时间"),
]

# 裸数字：真实对话里金额经常不带"元"——"按照5980收费""10800是无效童颜"
BARE_NUM_RE = re.compile(r"(?<![\d.])(\d{3,6})(?![\d.])")
PRICE_CONTEXT = ["收费", "价格", "报价", "多少钱", "块钱", "买单", "套餐", "优惠", "定金", "付"]
# 中文数字里不是金额的常见组合
CN_FALSE_POSITIVE = {"一块", "一块儿", "两块", "千万", "万一", "十分", "一会"}


def extract_anchors(text):
    found = []
    seen = set()

    def add(kind, raw, m):
        norm = re.sub(r"\s+", "", raw)
        key = (kind.split("-")[0], norm)
        if key in seen:
            return
        seen.add(key)
        found.append({
            "kind": kind,
            "raw": raw,
            "normalized": norm,
            "context": text[max(0, m.start() - 18):m.end() + 18].replace("\n", " "),
        })

    for pat, kind in NUM_PATTERNS:
        for m in re.finditer(pat, text):
            raw = m.group(0).strip()
            if raw in CN_FALSE_POSITIVE:
                continue
            # "这一块变得很肿" 这类量词不是金额
            if kind == "金额-中文" and re.match(r"^[一两]块", raw):
                ctx = text[max(0, m.start() - 3):m.end() + 3]
                if any(x in ctx for x in ("这一块", "那一块", "哪一块", "一块儿", "一块变", "一块都")):
                    continue
            add(kind, raw, m)

    # 裸数字：4-6 位基本就是金额；3 位要附近有价格语境才算
    for m in BARE_NUM_RE.finditer(text):
        v = m.group(1)
        if len(v) == 4 and 1900 <= int(v) <= 2100:
            continue  # 年份
        nearby = text[max(0, m.start() - 12):m.end() + 12]
        if len(v) >= 4 or any(k in nearby for k in PRICE_CONTEXT):
            add("金额-裸数字", v, m)
    return found


# ---------------------------------------------------------------- 输出

def render_report(meta, utts, stats, chunks, anchors, date):
    L = ["# 预处理报告", ""]
    L.append(f"- 转写稿：{meta.get('_file', '')}")
    L.append(f"- 日期：{date or '未能识别（档案里写"未提及"）'}")
    if meta.get("duration_sec"):
        try:
            L.append(f"- 时长：{int(float(meta['duration_sec'])) // 60} 分钟")
        except ValueError:
            pass
    L.append(f"- 句数：{len(utts)} · 字数：{sum(len(u['text']) for u in utts)}")
    L.append("")

    L.append("## 说话人推断（靠内容判断，编号不代表身份）")
    L.append("")
    L.append("| 说话人 | 轮次 | 字数 | 话量占比 | 专业词数 | 服务话术 | 问诊 | 客户话术 | 询价 | 推断 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in sorted(stats.values(), key=lambda x: -x["chars"]):
        L.append(
            f"| {s['speaker']} | {s['turns']} | {s['chars']} | {s['share']}% | {s['pro_hits']} | "
            f"{s['service_hits']} | {s['ask_hits']} | {s['client_hits']} | {s['price_hits']} | "
            f"{s['role_hint']} |"
        )
    L.append("")
    L.append("> 角色靠内容判断，**编号不代表身份**（有的录音里说话人1反而是客户）。")
    L.append("> 判断依据是「谁在给方案、谁在问价」，不是谁话多。")

    named = [s for s in stats.values() if s["speaker"].startswith("说话人")]
    if len(named) > 2:
        L.append(f"> 检测到 {len(named)} 个说话人。多人场景下要分清**客户本人**和**陪同人员**——")
        L.append("> 陪同说的话不能记成客户诉求，掏钱的是客户。")
        L.append("")

    L.append("## 数字锚点（档案里出现的数字必须能在这里找到）")
    L.append("")
    if anchors:
        groups = {}
        for a in anchors:
            groups.setdefault(a["kind"], []).append(a)
        for kind, items in groups.items():
            vals = "、".join(sorted({i["normalized"] for i in items}))
            L.append(f"- **{kind}**：{vals}")
    else:
        L.append("- 未识别到数字。档案里若出现任何金额、剂量、年龄，都要警惕是编造的。")
    L.append("")

    L.append("## 切块")
    L.append("")
    for i, c in enumerate(chunks, 1):
        L.append(render_chunk(i, len(chunks), c))
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="面诊转写稿预处理")
    ap.add_argument("file", help="转写稿路径（.md / .txt）")
    ap.add_argument("--size", type=int, default=3500, help="每块目标字数，默认 3500")
    ap.add_argument("--chunks-only", action="store_true", help="只输出切块")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--out", help="输出目录，写入 chunks.md 与 anchors.json")
    ap.add_argument("--lexicon", default=None, help="项目词表路径")
    args = ap.parse_args()

    p = Path(args.file)
    if not p.is_file():
        sys.exit(f"找不到文件：{p}")
    raw = p.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(raw)
    meta["_file"] = str(p)
    utts = parse_utterances(body)
    if not utts:
        sys.exit("没有解析到任何对话内容。检查文件是否为转写稿格式。")
    total_chars = sum(len(u["text"]) for u in utts)
    if total_chars < 60:
        print(f"⚠ 内容过少（{total_chars} 字，{len(utts)} 条），可能不是一段对话记录。")
        print("  确认文件选对了。如果确实只有这几句，档案大部分会是「未提及」。")
        print()

    lex_path = args.lexicon or str(Path(__file__).resolve().parent.parent
                                  / "references" / "项目词表.json")
    pro_words, _, _ = load_lexicon(lex_path)

    date = guess_date(meta, p.name)
    stats = classify_speakers(utts, pro_words)
    chunks = make_chunks(utts, args.size)
    full_text = "\n".join(u["text"] for u in utts)
    anchors = extract_anchors(full_text)

    if args.json:
        print(json.dumps({
            "file": str(p), "date": date,
            "meta": {k: v for k, v in meta.items() if not k.startswith("_")},
            "utterances": len(utts),
            "chars": len(full_text),
            "speakers": list(stats.values()),
            "anchors": anchors,
            "chunks": [
                {"index": i, "from": a, "to": b, "chars": sum(len(u["text"]) for _, u in items),
                 "text": render_chunk(i, len(chunks), (a, b, items))}
                for i, (a, b, items) in enumerate(chunks, 1)
            ],
        }, ensure_ascii=False, indent=2))
        return

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        md = "\n\n".join(render_chunk(i, len(chunks), c)
                         for i, c in enumerate(chunks, 1))
        (out / "chunks.md").write_text(md, encoding="utf-8")
        (out / "anchors.json").write_text(json.dumps({
            "date": date, "anchors": anchors,
            "speakers": list(stats.values()),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出：{out / 'chunks.md'}")
        print(f"已写出：{out / 'anchors.json'}")
        print(f"共 {len(chunks)} 块 · {len(anchors)} 个数字锚点 · {len(stats)} 个说话人")
        return

    if args.chunks_only:
        for i, c in enumerate(chunks, 1):
            print(render_chunk(i, len(chunks), c))
            print()
        return

    print(render_report(meta, utts, stats, chunks, anchors, date))


if __name__ == "__main__":
    main()
