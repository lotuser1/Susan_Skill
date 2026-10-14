#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查本地 Skill 是否有新版。

用法：
    python3 scripts/check-update.py                # 离线比对（默认，毫秒级，总能跑）
    python3 scripts/check-update.py --online       # 额外联网查远端版本清单（3 秒超时，失败静默）
    python3 scripts/check-update.py --json         # 结构化输出

做什么：
    1. 读本地每个已装 Skill 的 version（SKILL.md frontmatter）
    2. 读 skills/versions.json（上一次发版时随包写入的版本清单）
    3. 报出四类差异：待更新 / 缺失 / 包外新增 / 版本清单已陈旧
    4. --online 时拉远端 UPDATE.json，比对免费层的最新版本

不做什么：
    不下载、不覆盖、不改任何文件。只报告差异，怎么更新由 susan 告诉用户。

为什么默认离线：
    学员在国内访问 GitHub 不稳定。离线比对靠包里自带的 versions.json，
    永远可用；联网只是加分项，失败也不能影响主流程。
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELF_SKILLS_ROOT = HERE.parent.parent  # skills/

# 复用 list-skills.py 的发现逻辑，避免两套扫描规则漂移
_spec = importlib.util.spec_from_file_location("list_skills", HERE / "list-skills.py")
_ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ls)

SCAN_ROOTS = _ls.SCAN_ROOTS
TIER_LABEL = _ls.TIER_LABEL

# 远端版本清单（免费公开层）。可用环境变量覆盖，方便将来换源。
DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/lotuser1/Susan_Skill/main/UPDATE.json"
UPDATE_URL = os.environ.get("SUSAN_UPDATE_URL", DEFAULT_UPDATE_URL)
NET_TIMEOUT = 3

STALE_DAYS = 30  # 版本清单超过这么多天没更新，提示可能有新版
STALE_DAYS_ONLINE = 45  # 联网可用时放宽，因为联网本身就能查到新版


def vtuple(v):
    """'1.2.3' -> (1, 2, 3)。非数字段按 0 处理，坏值返回 (0,)。"""
    try:
        parts = re.findall(r"\d+", str(v))
        return tuple(int(p) for p in parts) if parts else (0,)
    except (TypeError, ValueError):
        return (0,)


def cmp_ver(a, b):
    """返回 -1/0/1。长度不等时补 0 对齐，避免 1.2 与 1.2.0 判成不同。"""
    ta, tb = vtuple(a), vtuple(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


def load_manifest():
    """读 skills/versions.json。这是付费层的更新基准——包里自带的，不依赖联网。"""
    for root in SCAN_ROOTS:
        p = root / "versions.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")), str(p)
            except (json.JSONDecodeError, OSError):
                continue
    return None, None


def local_versions():
    """本地已装 skill -> {ability, version, tier}。含主入口自身。"""
    found, _ = _ls.discover()
    out = {}
    for entry in found.values():
        out[entry["name"]] = {
            "ability": entry["ability"],
            "version": entry["version"] or "0.0.0",
            "tier": entry["tier"],
        }
    out["susan"] = {
        "ability": "能力总入口",
        "version": _ls.self_version(),
        "tier": "free",
    }
    return out


def diff_offline(local, manifest):
    """比对本地与包清单。返回 (问题列表, 包外新增列表)。"""
    issues, extra = [], []
    if not manifest:
        return issues, extra

    listed = manifest.get("skills", {})
    for name, info in sorted(listed.items()):
        cur = local.get(name)
        if cur is None:
            issues.append(
                {
                    "kind": "缺失",
                    "name": name,
                    "ability": info.get("ability", name),
                    "local": None,
                    "target": info.get("version", "0.0.0"),
                    "tier": TIER_LABEL.get(str(info.get("tier", "free")).lower(), "免费"),
                }
            )
            continue
        if cmp_ver(cur["version"], info.get("version", "0.0.0")) < 0:
            issues.append(
                {
                    "kind": "待更新",
                    "name": name,
                    "ability": cur["ability"],
                    "local": cur["version"],
                    "target": info.get("version", "0.0.0"),
                    "tier": TIER_LABEL.get(str(info.get("tier", "free")).lower(), "免费"),
                }
            )

    for name, info in sorted(local.items()):
        if name not in listed:
            extra.append({"name": name, "ability": info["ability"], "version": info["version"]})
    return issues, extra


def stale_days(manifest):
    """版本清单距今多少天。返回 (天数, 日期串)；算不出返回 (None, None)。"""
    if not manifest:
        return None, None
    d = manifest.get("packed") or manifest.get("date")
    if not d:
        return None, None
    try:
        dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
    except ValueError:
        return None, None
    return (datetime.now() - dt).days, str(d)[:10]


def fetch_remote():
    """拉远端版本清单。任何失败都返回 (None, 原因)，绝不抛异常打断主流程。"""
    try:
        req = urllib.request.Request(
            UPDATE_URL, headers={"User-Agent": "susan-skill/1.0", "Cache-Control": "no-cache"}
        )
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        return data, None
    except Exception as e:  # 断网 / 被墙 / 404 / JSON 坏了，一律静默
        return None, f"{type(e).__name__}"


def diff_remote(local, remote):
    """本地 vs 远端，只报免费层（远端不暴露付费层内容）。"""
    issues = []
    if not remote:
        return issues
    listed = remote.get("skills", {})
    if "susan" in remote:
        listed = dict(listed)
        listed["susan"] = remote["susan"]
    for name, info in sorted(listed.items()):
        if str(info.get("tier", "free")).lower() != "free":
            continue
        cur = local.get(name)
        if cur is None:
            continue  # 没装的交给路由引导，不是"更新"问题
        if cmp_ver(cur["version"], info.get("version", "0.0.0")) < 0:
            issues.append(
                {
                    "kind": "可更新",
                    "name": name,
                    "ability": cur["ability"],
                    "local": cur["version"],
                    "target": info.get("version", "0.0.0"),
                    "tier": "免费",
                }
            )
    return issues


def merge_issues(issues):
    """同一个能力可能同时被包清单和远端报出来，只留目标版本最高的一条。

    否则用户会看到「合规文案审查 待更新到 v1.1.0」和「可更新到 v1.2.0」两条，
    不知道该听哪个。
    """
    best = {}
    for it in issues:
        key = it["name"]
        cur = best.get(key)
        if cur is None or cmp_ver(it["target"], cur["target"]) > 0:
            best[key] = dict(it)
    order = {"缺失": 0, "待更新": 1, "可更新": 1}
    return sorted(best.values(), key=lambda x: (order.get(x["kind"], 9), x["ability"]))


def render(result):
    lines = []
    lv = result["local"].get("susan", {}).get("version", "0.0.0")
    lines.append(f"版本检查 · {'联网' if result['online'] else '离线'}")
    lines.append(f"主入口：v{lv}")
    if result["manifest"]:
        lines.append(
            f"上次发版：{result['manifest'].get('packed', '未知')}"
            + (f"（{result['stale_days']} 天前）" if result["stale_days"] is not None else "")
        )
    else:
        lines.append("上次发版：未找到 versions.json（免费公开版或手工安装）")
    lines.append("")

    issues = result["issues"]
    if not issues:
        lines.append("状态：全部最新")
    else:
        lines.append(f"状态：{len(issues)} 项需要处理")
        lines.append("")
        for it in issues:
            if it["local"]:
                lines.append(
                    f"- [{it['kind']}] {it['ability']}：本地 v{it['local']} → 新版 v{it['target']}（{it['tier']}）"
                )
            else:
                lines.append(
                    f"- [{it['kind']}] {it['ability']}：本地没有，应有 v{it['target']}（{it['tier']}）"
                )

    if result["remote_error"]:
        lines.append("")
        lines.append(f"远端：{result['remote_url']} 取不到（{result['remote_error']}），已按离线结果处理。")
    elif result["remote"]:
        lines.append("")
        lines.append(f"远端清单：{result['remote'].get('updated', '未知')}")
        if result["remote_note"]:
            lines.append(f"更新说明：{result['remote_note']}")

    if result["stale"]:
        lines.append("")
        lines.append(
            f"提示：距上次发版已 {result['stale_days']} 天，"
            "建议联系 Susan 确认是否有新版（付费能力由 Susan 直接发新包）。"
        )
    if result["extra"]:
        lines.append("")
        lines.append(
            "包外新增（本地有、版本清单里没有，不影响使用）："
            + "、".join(f"{e['ability']} v{e['version']}" for e in result["extra"])
        )
    lines.append("")
    lines.append("更新方式：Susan 发的新包解压后覆盖同名目录即可，不需要卸载旧版。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true", help="联网查远端版本清单")
    ap.add_argument("--quiet", action="store_true", help="没问题时只输出一行，不打扰用户")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--url", help="覆盖远端清单地址")
    args = ap.parse_args()

    global UPDATE_URL
    if args.url:
        UPDATE_URL = args.url

    local = local_versions()
    manifest, manifest_path = load_manifest()
    issues, extra = diff_offline(local, manifest)

    remote, remote_error, remote_note = None, None, None
    if args.online:
        remote, remote_error = fetch_remote()
        issues += diff_remote(local, remote)
        if remote:
            remote_note = remote.get("notes")
    issues = merge_issues(issues)

    days, packed = stale_days(manifest)
    threshold = STALE_DAYS_ONLINE if (args.online and remote) else STALE_DAYS
    stale = days is not None and days > threshold

    result = {
        "online": args.online,
        "local": local,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "issues": issues,
        "extra": extra,
        "stale": stale,
        "stale_days": days,
        "remote": remote,
        "remote_url": UPDATE_URL if args.online else None,
        "remote_error": remote_error,
        "remote_note": remote_note,
        "has_update": bool(issues),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.quiet and not issues and not stale:
        # 例行检查：没问题就一句话带过，不要把版本报告塞进每次对话
        print(f"版本检查：全部最新（{'联网' if args.online else '离线'}）")
        return

    print(render(result))


if __name__ == "__main__":
    main()
