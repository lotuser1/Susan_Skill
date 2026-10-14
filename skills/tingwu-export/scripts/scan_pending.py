#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描 00 待分类/ 下的所有 .md，生成 _index/pending.json 供 LLM 判定归类。

只取「文件名 + 正文开头 600 字」——足够判定板块，又省 token。

用法：
    bash run.sh --classify            # 全量扫描
    bash run.sh --classify --limit 50 # 只扫前 50 条
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

KB_ROOT = Path(os.environ.get("KB_ROOT", "./知识库"))
INBOX = KB_ROOT / "00 待分类"
INDEX = INBOX / "_index"

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def read_head(path, limit=600):
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "", ""
    title = ""
    m = FM_RE.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"')
                break
        body = raw[m.end():]
    else:
        body = raw
    body = re.sub(r"^#+ .*$", "", body, flags=re.M)
    body = re.sub(r"\n{2,}", "\n", body).strip()
    return title, body[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classify", action="store_true", help="（由 run.sh 传入，占位）")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    INDEX.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in INBOX.rglob("*.md") if p.is_file())
    if a.limit:
        files = files[:a.limit]

    out = []
    for i, p in enumerate(files, 1):
        title, head = read_head(p)
        if not title:
            title = p.stem
        rel = p.relative_to(INBOX)
        account = rel.parts[0] if len(rel.parts) > 1 else ""
        out.append({
            "id": i,
            "path": str(p),
            "name": p.name,
            "account": account,
            "size": p.stat().st_size,
            "title": title,
            "head": head,
        })

    fp = INDEX / "pending.json"
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已扫描 {len(out)} 条 → {fp}")
    print(f"   下一步：让 Agent 读取 pending.json，按 references/分类标准.md "
          f"判定后写出 {INDEX}/plan.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
