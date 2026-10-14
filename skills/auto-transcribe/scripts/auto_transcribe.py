#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_transcribe —— 把本地录音批量上传到通义听悟网页版转写（开启多人讨论/说话人分离）。

核心特性（修复"重复转写"的 bug）：
  **上传前先拉取听悟全部任务，逐文件核对状态；只有"尚无成功记录"的文件才上传。**
  已有成功(0) / 正在处理(2,4) 的文件直接跳过 —— 因此无论运行多少次，
  同一个录音都只会有一条成功文稿，绝不会"转写文档数 > 录音数"。

用法
----
  python3 auto_transcribe.py --dry-run     # 只计算并预览计划，不上传
  python3 auto_transcribe.py --apply       # 正式上传需要转写的文件
  python3 auto_transcribe.py --status      # 只报告当前听悟状态

配置来自环境变量（见 config.env）：
  TINGWU_SOURCE_DIRS   要处理的文件夹（逗号分隔）
  TINGWU_CHROME_PORT   调试端口（默认 9222）
  TINGWU_BATCH_SIZE    每批数量（默认 20）
  TINGWU_FOLDER_URL    目标文件夹（默认 folders/0）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cdp_helper import Page, log, open_panel, close_debug_browser  # noqa: E402
from tingwu_api import pull_status_map, summarize, STATUS_NAME  # noqa: E402

AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".amr", ".wma", ".opus", ".mp4", ".mov"}
BATCH_SIZE = int(os.environ.get("TINGWU_BATCH_SIZE", "20"))
POST_WAIT = 45  # 每批提交后等待秒数（避免下一批 open_panel 导航打断上传）
MAX_ATTEMPTS = int(os.environ.get("TINGWU_MAX_ATTEMPTS", "3"))  # 单文件最多尝试次数，超过则判定顽固失败并跳过
FOLDER_URL = os.environ.get("TINGWU_FOLDER_URL", "https://tingwu.aliyun.com/home")
STATE = HERE / "state.json"


# ------------------------------------------------------------------ 命名 / 列举
def file_key(name: str) -> str:
    """把文件名归一为「裸名」（去掉扩展名），用于和听悟 showName 比对。"""
    p = name.strip()
    low = p.lower()
    for ext in (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".amr", ".wma", ".opus", ".mp4", ".mov"):
        if low.endswith(ext):
            return p[: -len(ext)]
    return p


def list_source(dirs):
    """
    列举所有源音频文件，返回 {裸名: {"path":..., "size":...}}。
    同时返回 0 字节文件列表（无法上传，单独记录）。
    """
    source = {}
    zeros = []
    for d in dirs:
        d = d.strip()
        if not d:
            continue
        if not os.path.isdir(d):
            log(f"  ⚠️ 目录不存在，跳过：{d}")
            continue
        for f in sorted(os.listdir(d)):
            ext = os.path.splitext(f)[1].lower()
            if ext not in AUDIO_EXT:
                continue
            full = os.path.join(d, f)
            if not os.path.isfile(full):
                continue
            sz = os.path.getsize(full)
            key = file_key(f)
            if sz == 0:
                if key not in zeros:
                    zeros.append(key)
                continue
            # 同名不同扩展名：后者覆盖（极少见）
            source[key] = {"path": full, "size": sz}
    return source, zeros


# ------------------------------------------------------------------ 幂等核心
def compute_needs(source, status_map):
    """
    根据听悟现有状态，算出"需要上传"的文件。
    规则（这是修复重复转写的关键）：
      · 已有 成功(0)            → 跳过（绝不重复创建成功文稿）
      · 正在 转写中(4)/排队(2)  → 跳过（会自己跑完）
      · 仅 失败(1) 或 不在列表  → 需要上传
    """
    needs, ok_success, ok_active = [], [], []
    for key, info in source.items():
        if info["size"] == 0:
            continue  # 0 字节在调用方已单列，这里不会进来
        ss = set(status_map.get(key, []))
        if 0 in ss:
            ok_success.append(key)
        elif 4 in ss or 2 in ss:
            ok_active.append(key)
        else:
            needs.append(key)
    return needs, ok_success, ok_active


def should_stop(consec_bad, good_total, accepted, bad):
    """
    判定是否因「时长/额度不足」停止上传。

    听悟在免费额度耗尽时往往不会弹阻断弹窗，而是：文件能进列表、但转写随后失败；
    或整批提交后无任何文件进入成功/处理中状态。
    因此用「连续整批提交后均无成功记录」来判定额度不可用：
      · 本批 accepted 全部 missing/失败(bad == accepted) → 连续失败计数 +1
      · 连续 2 批全失败，或累计成功数为 0（一开始就没额度）→ 停止
    单批个别延迟(missing)不会触发，只有整批失败且连续才停。
    返回 (是否停止, 新的连续失败计数)
    """
    if not accepted:
        return False, consec_bad
    if len(bad) == len(accepted):
        consec_bad += 1
        if consec_bad >= 2 or good_total == 0:
            return True, consec_bad
    else:
        consec_bad = 0
    return False, consec_bad


# ------------------------------------------------------------------ 顽固失败处理
def plan_upload(needs, attempts, max_attempts):
    """根据累计尝试次数，决定哪些文件跳过、哪些继续上传。

    单文件已尝试 >= max_attempts 次仍无成功/处理中记录 → 判定顽固失败，跳过不再重试。
    返回 (skipped, todo)。
    """
    skipped = [n for n in needs if attempts.get(n, 0) >= max_attempts]
    todo = [n for n in needs if n not in set(skipped)]
    return skipped, todo


def update_attempts_and_stubborn(todo, skipped, final_summ, attempts, max_attempts):
    """上传后更新每文件尝试次数，并返回「顽固失败」文件列表。

      · 最终成功/处理中 → 清除尝试计数（已搞定）
      · 本次真正尝试过但仍失败 → 尝试次数 +1
      · 累计尝试 >= max_attempts 且仍无成功记录 → 计入顽固列表（任务结束输出文件名）
    """
    for n in set(todo) | set(skipped):
        if final_summ.get(n) in ("success", "active"):
            attempts.pop(n, None)
        elif n in todo:
            attempts[n] = attempts.get(n, 0) + 1
    stubborn = [
        n for n in (set(todo) | set(skipped))
        if attempts.get(n, 0) >= max_attempts and final_summ.get(n) not in ("success", "active")
    ]
    stubborn.sort()
    return stubborn


# ------------------------------------------------------------------ 上传一批
def submit_batch(pg_ref, source, names):
    """投递一批文件，返回被面板接收的文件裸名列表。"""
    paths = []
    for n in names:
        info = source.get(n)
        if info and os.path.exists(info["path"]) and os.path.getsize(info["path"]) > 0:
            paths.append(info["path"])
    if not paths:
        return []

    pg = open_panel(pg_ref)
    if pg is None:
        log("  ✗ 面板打不开，本批跳过")
        return []
    ok, msg = pg.attach(paths)
    log(f"  {msg}")
    if not ok:
        return []
    time.sleep(10)

    present = set(pg.panel_names())
    accepted = [n for n in names if n in present]
    not_present = [n for n in names if n not in present]
    if not_present:
        log(f"  面板未接收 {len(not_present)} 个，补投一次…")
        retry_paths = [source[n]["path"] for n in not_present
                       if source.get(n) and os.path.exists(source[n]["path"])]
        pg.attach(retry_paths)
        time.sleep(8)
        present = set(pg.panel_names())
        accepted = [n for n in names if n in present]
        if not_present := [n for n in names if n not in present]:
            log(f"  ⚠️ 仍有 {len(not_present)} 个面板拒收（可能损坏）：{not_present[:3]}")

    r, on = pg.pick_multi()
    if not on:
        log("  ⚠️ 多人讨论未选中，本批记录后继续")
        return accepted + not_present
    time.sleep(2)
    if not pg.click("开始转写", contains=True):
        log("  ✗ 未找到「开始转写」按钮")
        return accepted + not_present
    time.sleep(POST_WAIT)
    return accepted


# ------------------------------------------------------------------ 状态 / 续跑
def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"uploaded": [], "passes": []}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只计算计划，不上传（默认）")
    ap.add_argument("--apply", action="store_true", help="真正上传需要转写的文件")
    ap.add_argument("--status", action="store_true", help="只报告听悟当前状态")
    ap.add_argument("--dir", help="临时指定单个目录（覆盖 config.env 的 TINGWU_SOURCE_DIRS）")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    a = ap.parse_args()

    # 确定源目录
    if a.dir:
        dirs = [a.dir]
    else:
        env = os.environ.get("TINGWU_SOURCE_DIRS", "")
        dirs = [x for x in env.split(",") if x.strip()]
    if not dirs:
        log("❌ 没有指定要处理的文件夹：请设置 TINGWU_SOURCE_DIRS 或传 --dir")
        return 2

    # 连接 Chrome
    try:
        first = Page()
        t = first.text()
        if "立即登录" in t:
            log("❌ 未登录：请先运行 start_chrome.sh 启动 Chrome 并扫码登录通义听悟")
            return 2
    except Exception as e:
        log(f"❌ 无法连接 Chrome（端口 {os.environ.get('TINGWU_CHROME_PORT','9222')}）：{str(e)[:120]}")
        log("   请先运行 bash start_chrome.sh 启动带调试端口的 Chrome 并登录。")
        return 3

    # 列举源文件
    source, zeros = list_source(dirs)
    log(f"源目录共扫描到 {len(source)} 个有效音频（另有 {len(zeros)} 个 0 字节已单列）")

    # 拉取听悟状态
    log("正在拉取通义听悟现有转写记录…")
    status_map = pull_status_map(verbose=True)
    summary = summarize(status_map)

    # 幂等去重：只上传尚无成功的
    needs, ok_success, ok_active = compute_needs(source, status_map)
    log("")
    log(f"=== 计划（幂等核对结果）===")
    log(f"  源文件总数        ：{len(source)}")
    log(f"  已成功(将跳过)    ：{len(ok_success)}")
    log(f"  处理中(将跳过)    ：{len(ok_active)}")
    log(f"  需要新上传        ：{len(needs)}")
    log(f"  0 字节(无法上传)  ：{len(zeros)}")
    # 关键不变量：四类之和应等于"全部源文件数(有效+0字节)"，绝不超出（保证不会重复造成功文稿）
    total_files = len(source) + len(zeros)
    total_class = len(ok_success) + len(ok_active) + len(needs) + len(zeros)
    if total_class != total_files:
        log(f"  ⚠️ 计数校验异常：分类合计 {total_class} ≠ 源文件 {total_files}")

    if zeros:
        log(f"  0 字节文件（需回源头重导出）：{zeros[:5]}{' …' if len(zeros) > 5 else ''}")

    if a.status:
        return 0

    if not a.apply:
        log("")
        log("（dry-run）未真正上传。确认无误后运行：bash run.sh --apply")
        return 0

    # ---- 正式上传 ----
    # 注意：直接以「听悟真实状态」compute_needs 给出的 needs 为准上传，
    # 不再用 state.json 的乐观「已上传」清单去过滤——否则会把"面板投喂成功
    # 但听悟侧失败/未接收"的文件误判为已完成而漏传（曾导致 32 个文件被跳过）。
    st = load_state()
    attempts = st.setdefault("attempts", {})
    # 顽固失败跳过：单文件已尝试 >= MAX_ATTEMPTS 次仍无成功/处理中记录，则跳过不再重试
    skipped, todo = plan_upload(needs, attempts, MAX_ATTEMPTS)
    if skipped:
        log(f"  ⏭️ 跳过 {len(skipped)} 个「已尝试 {MAX_ATTEMPTS} 次仍失败」的顽固文件（任务结束时会输出文件名）")
    if not todo:
        log("✓ 没有需要新上传的文件（全部已在听悟成功/处理中）")
        close_debug_browser()
        log("调试 Chrome 已关闭。下次上传请先运行 bash start_chrome.sh 启动并登录。")
        return 0

    batches = [todo[i:i + a.batch_size] for i in range(0, len(todo), a.batch_size)]
    log(f"开始上传 {len(todo)} 个文件，分 {len(batches)} 批（每批 {a.batch_size}）…")
    log("⚠️ 若听悟时长/额度不足，整批提交后均无成功记录时会自动停止，并输出未转写清单。")
    pg_ref = [first]
    good_total = 0
    consec_bad = 0
    stopped = False
    for bi, batch in enumerate(batches):
        log(f"  --- 批次 {bi + 1}/{len(batches)}：{len(batch)} 个 ---")
        accepted = submit_batch(pg_ref, source, batch)
        for n in accepted:
            if n not in st["uploaded"]:
                st["uploaded"].append(n)
        save_state(st)
        # 提交后核对真实状态（检测额度不足：文件是否真的进入成功/处理中）
        time.sleep(15)
        new_map = pull_status_map(verbose=False)
        summ = summarize(new_map)
        good = [n for n in accepted if summ.get(n) in ("success", "active")]
        bad = [n for n in accepted if summ.get(n) not in ("success", "active")]
        good_total += len(good)
        stop, consec_bad = should_stop(consec_bad, good_total, accepted, bad)
        if stop:
            log(f"  ✗ 判定时长/额度不足（连续 {consec_bad} 批提交后均无成功记录），停止上传。")
            stopped = True
            break
        if bad:
            log(f"  · 本批 {len(accepted)} 个中 {len(good)} 已入列，{len(bad)} 个暂未成功（将随最终复核处理）")
        time.sleep(5)

    # ---- 复核 + 输出未转写清单 ----
    log("正在复核听悟最终状态…")
    final_map = pull_status_map(verbose=True)
    final_summ = summarize(final_map)
    good_set = [n for n in todo if final_summ.get(n) in ("success", "active")]
    pending = [n for n in todo if final_summ.get(n) not in ("success", "active")]

    # 更新尝试次数并判定顽固文件（三次顽固失败 → 跳过并在结尾输出文件名）
    stubborn = update_attempts_and_stubborn(todo, skipped, final_summ, attempts, MAX_ATTEMPTS)

    log("")
    log(f"=== 结果 ===")
    log(f"  本次计划 {len(todo)} 个；已成功/处理中 {len(good_set)}；未转写(待补) {len(pending)}")
    if stopped:
        log("  （因时长/额度不足提前停止）")
    if pending:
        out = HERE / "pending_not_transcribed.txt"
        lines = []
        for n in pending:
            info = source.get(n)
            lines.append(info.get("path") if info else n)
        out.write_text("\n".join(lines), encoding="utf-8")
        log(f"  ⚠️ 未转写文件清单已写入：{out}")
        log(f"     示例（前 10）：{[os.path.basename(p) for p in lines[:10]]}")
        log(f"     充值后再次运行 --apply 即可续传（自动跳过已成功的）")
    if stubborn:
        out2 = HERE / "skipped_stubborn.txt"
        lines2 = []
        for n in stubborn:
            info = source.get(n)
            lines2.append(info.get("path") if info else n)
        out2.write_text("\n".join(lines2), encoding="utf-8")
        log(f"  🚫 顽固失败（已尝试 {MAX_ATTEMPTS} 次仍无成功记录，本轮跳过）：共 {len(stubborn)} 个")
        log(f"     文件名已写入：{out2}")
        for p in lines2:
            log(f"       - {os.path.basename(p)}")
    st.setdefault("passes", []).append({
        "time": time.strftime("%m-%d %H:%M"),
        "planned": len(todo), "good": len(good_set), "pending": len(pending),
        "stopped": stopped,
    })
    save_state(st)
    log("结束。可再次运行 bash run.sh --status 查看最终状态。")
    # 上传任务已全部处理完（含提前停止的场景），关闭调试 Chrome，
    # 避免界面一直显示「1 个后台任务运行中」的常驻进程。
    close_debug_browser()
    log("调试 Chrome 已关闭。下次上传请先运行 bash start_chrome.sh 启动并登录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
