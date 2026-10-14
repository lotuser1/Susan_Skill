#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通义听悟 getTransList 接口封装。

用于「上传前核对」：拉取听悟里全部转写任务，得到每个录音文件的当前状态，
从而判断哪些已经转写成功、哪些还在处理、哪些需要（重新）上传。

状态枚举（来自听悟接口）：
  0 = 转写成功
  1 = 失败
  2 = 排队中
  4 = 转写中
"""
import time
from collections import defaultdict

from cdp_helper import Page, log

# 状态含义（便于打印）
STATUS_NAME = {0: "成功", 1: "失败", 2: "排队中", 4: "转写中"}


def norm_name(n):
    n = (n or "").strip()
    if n.lower().endswith(".m4a"):
        n = n[:-4]
    return n


def pull_status_map(verbose=True):
    """
    翻页拉取全部转写任务，返回 {裸文件名: [status, ...]}。
    同名文件可能出现多条任务（重复上传遗留），故值是列表。
    """
    pg = Page()
    time.sleep(1)
    expr = r"""
    (async () => {
      const all = [];
      let pageNo = 1, total = 0;
      for (; pageNo <= 80; pageNo++) {
        const body = {
          action: 'getTransList', version: '1.0', userId: '',
          filter: { status: [], fileTypes: [], beginTime: '', mediaType: '', endTime: '', showName: '', read: '', lang: '', shareUserId: '', client: '' },
          preview: 0, pageNo: pageNo, pageSize: 200
        };
        const r = await fetch('https://tingwu.aliyun.com/api/trans/request?getTransList&c=web', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const j = await r.json();
        const d = j.data || [];
        total = j.total || 0;
        for (const it of d) {
          all.push({ name: ((it.tag && it.tag.showName) || it.name || ''), status: it.status });
        }
        if (all.length >= total || d.length === 0) break;
      }
      return { collected: all.length, total, items: all };
    })()
    """
    res = pg.js(expr, tries=12)
    m = defaultdict(list)
    if not isinstance(res, dict) or "items" not in res:
        if verbose:
            log(f"  ⚠️ 拉取听悟列表失败：{str(res)[:120]}")
        return m
    for it in res["items"]:
        nm = norm_name(it.get("name"))
        if nm:
            m[nm].append(it.get("status"))
    if verbose:
        from collections import Counter
        c = Counter()
        for ss in m.values():
            for s in ss:
                c[s] += 1
        log(f"  · 听悟现有任务 {res['collected']} 条 / 唯一文件 {len(m)} 个；状态分布："
            + "，".join(f"{STATUS_NAME.get(k, k)} {v}" for k, v in sorted(c.items())))
    return m


def summarize(status_map):
    """把 {name:[status]} 归纳为每文件的归类，便于打印与判断。"""
    out = {}
    for name, ss in status_map.items():
        st = set(ss)
        if 0 in st:
            out[name] = "success"
        elif 4 in st or 2 in st:
            out[name] = "active"
        else:  # 只有 1（失败）或空
            out[name] = "failed_or_missing"
    return out
