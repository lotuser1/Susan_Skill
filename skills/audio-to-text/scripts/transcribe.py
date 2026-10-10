#!/usr/bin/env python3
"""
audio-to-text 核心脚本（无 OSS 版 · 国际站/国内站通用）
=================================================
把本地音频文件批量转成文字稿，供后续做成知识库。

默认输出 `.doc`（与你本地已有的文档格式统一）；也可通过 --format 设为
.md / both。

流程：本地音频 → 上传到百炼文件服务(非 OSS) 拿到 file_id → 换取可下载的
签名 URL → 调用百炼语音识别 API（qwen3-asr-flash-filetrans，异步）→ 轮询
结果 → 保存为同名文字稿到本地目录。

关键特性：
- 批量：遍历目录下所有支持的音频文件
- 断点续传：已存在同名文字稿的音频自动跳过，下次继续跑不重复花钱
- 转写清单 transcribe_manifest.csv：每个文件标为 已转写(DONE)/未转写(PENDING)/失败(FAILED)，
  无论因何停止（额度耗尽/保存失败/手动中断/崩溃）都能清楚看到哪些已转、哪些没转，下次重跑即续
- 不依赖对象存储 OSS：音频直接上传到百炼自有文件服务，用户无需开通/配置 OSS
- 输出格式可配置：--format 或环境变量 OUTPUT_FORMAT，支持 doc(默认)/md/both
- 输出路径可配置：--output-dir 或环境变量 OUTPUT_DIR；不配置则存到音频同目录
- 站点可配置：国内站默认；国际站账号填 DASHSCOPE_BASE_URL 即可
- 续取能力：自动记录 文件名↔task_id，额度耗尽或结果未落盘时，可在阿里云 24 小时
  留存窗口内用 --recover 把已转写的文稿重新拉回（需仍在 24h 内）

所需环境变量：
  DASHSCOPE_API_KEY    阿里云百炼 API Key（必填）
  DASHSCOPE_BASE_URL   服务站点（可选，默认 https://dashscope.aliyuncs.com；
                        国际站填 https://dashscope-intl.aliyuncs.com）
  OUTPUT_DIR           转写文稿输出目录（可选，默认与音频同目录）
  OUTPUT_FORMAT        输出格式 doc/md/both（可选，默认 doc）

依赖安装（只需 requests）：
  python3 -m pip install requests

用法：
  python3 transcribe.py --dir /path/to/audio
  python3 transcribe.py --file /path/to/single.mp3
  python3 transcribe.py --dir /path/to/audio --output-dir /path/to/output
  python3 transcribe.py --dir /path/to/audio --format doc   # 默认即 doc
  python3 transcribe.py --dir /path/to/audio --ext ".mp3,.m4a" --lang en
  python3 transcribe.py --recover          # 续取：把 task_index.csv 里已转写但没存下的文稿拉回来（限提交后 24h 内）
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

# 支持的音频扩展名
SUPPORTED_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma", ".opus",
}

# 识别模型（异步长文件转写，支持最长 12 小时录音）。
# 可在 config.env 用 ASR_MODEL 覆盖：
#   qwen3-asr-flash-filetrans  → 默认，最准确，国内约 0.00022 元/秒
#   paraformer-v2              → 最便宜，国内约 0.00008 元/秒（医美方言语种略弱）
#   paraformer-8k-v2           → 电话录音等 8k 采样场景
MODEL = os.environ.get("ASR_MODEL", "qwen3-asr-flash-filetrans")

# 百炼 API 地址（默认国内站；国际站账号通过环境变量 DASHSCOPE_BASE_URL 切换）
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com").rstrip("/")
API_FILE_UPLOAD = f"{BASE_URL}/api/v1/files"
API_FILE_GET = f"{BASE_URL}/api/v1/files/{{file_id}}"
API_SUBMIT = f"{BASE_URL}/api/v1/services/audio/asr/transcription"
API_QUERY = f"{BASE_URL}/api/v1/tasks/{{task_id}}"

# 扩展名 → MIME（上传时携带，便于服务端识别）
MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".amr": "audio/amr", ".wma": "audio/x-ms-wma", ".opus": "audio/opus",
}


class TranscribedButSaveFailed(RuntimeError):
    """转写任务已在阿里云完成，但取回结果/落盘失败。

    触发于：extract_text 解析失败（Expecting value）、结果为空、write_text 写盘失败。
    语义：钱已花、阿里云已转完，但我们没把文稿拿到/存下。
    这通常意味着检索/保存环节存在系统性问题（限流、网络、磁盘），
    继续提交更多文件只会空耗额度 → 由 main 决定整批停止（防欠费）。
    """


def _auth_headers() -> dict:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少 DASHSCOPE_API_KEY。请先在 config.env（或环境变量）里配置百炼 API Key。"
        )
    return {"Authorization": f"Bearer {api_key}"}


# ---------- 上传到百炼文件服务（替代 OSS） ----------
def upload_to_dashscope(local_path: Path) -> str:
    """上传本地音频到百炼文件服务，返回可下载的签名 URL（无需 OSS）。"""
    headers = _auth_headers()
    mime = MIME.get(local_path.suffix.lower(), "application/octet-stream")

    # 1) 上传拿 file_id（带重试，避免批量高并发下被限流导致整文件失败）
    with open(local_path, "rb") as f:
        body = _post_json(
            API_FILE_UPLOAD,
            headers=headers,
            files={"file": (local_path.name, f, mime)},
            data={"purpose": "inference"},
            timeout=120,
            stage="上传到百炼",
        )
    up = (body.get("output") or body.get("data") or {})
    fid = (up.get("uploaded_files") or [{}])[0].get("file_id")
    if not fid:
        raise RuntimeError(f"上传成功但未返回 file_id：{body}")

    # 2) 换取可下载的签名 URL（ASR 接口只接受真实 URL，不接受 file_id）
    gbody = _get_json(API_FILE_GET.format(file_id=fid), headers=headers, timeout=30,
                     stage="获取文件URL")
    url = (gbody.get("output") or gbody.get("data") or {}).get("url")
    if not url:
        raise RuntimeError(f"未返回可下载 URL：{gbody}")
    return url


# ---------- 转写任务 ----------
def submit_transcription(file_url: str, lang: str = "auto") -> str:
    """提交异步转写任务，返回 task_id。"""
    _auth_headers()  # 仅做密钥存在性检查
    headers = {
        **_auth_headers(),
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    # channel_id=[0] 识别第 1 条音轨；enable_itn=False 保留原始读法
    params = {"channel_id": [0], "enable_itn": False}
    # auto/cn/zh 走自动识别，避免误伤；其余语种显式指定
    if lang and lang not in ("auto", "cn", "zh"):
        params["language"] = lang

    payload = {
        "model": MODEL,
        "input": {"file_url": file_url},
        "parameters": params,
    }
    data = _post_json(API_SUBMIT, headers=headers, json=payload, timeout=30,
                     stage="提交转写")
    if data.get("code") not in (None, 200, 0, "None", ""):
        raise RuntimeError(f"提交转写报错: {data}")
    return data["output"]["task_id"]


def query_transcription(task_id: str, timeout_s: int = 1800) -> dict:
    """轮询转写任务直至完成，返回完整任务结果。"""
    headers = _auth_headers()
    url = API_QUERY.format(task_id=task_id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"  查询失败 HTTP {resp.status_code}，重试中...", file=sys.stderr)
                time.sleep(5)
                continue
            data = resp.json()
        except Exception as exc:
            print(f"  查询异常：{exc}，重试中...", file=sys.stderr)
            time.sleep(5)
            continue
        status = data.get("output", {}).get("task_status", "")
        if status == "SUCCEEDED":
            return data
        if status == "UNKNOWN":
            raise RuntimeError("任务不存在或已超期(UNKNOWN)")
        if status == "FAILED":
            raise RuntimeError(
                f"转写任务失败: {data.get('output', {}).get('message', '未知')}"
            )
        time.sleep(5)
    raise TimeoutError(f"转写任务 {task_id} 超时（{timeout_s}s）")


# ---------- 结果转文字 ----------
def _get_json(url: str, headers=None, timeout: int = 60, tries: int = 5,
             stage: str = "获取数据") -> dict:
    """带重试的 JSON GET。

    - 4xx（欠费/限流/鉴权）立即抛出，交给上层处理（欠费不要再重试浪费时间）
    - 其余网络/解析错误（含返回非 JSON）按指数退避重试
    """
    last_err = None
    for i in range(tries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                last_err = f"{stage} HTTP {resp.status_code}: {resp.text[:200]}"
                if 400 <= resp.status_code < 500:
                    raise RuntimeError(last_err)
                time.sleep(3 * (i + 1))
                continue
            return resp.json()
        except RuntimeError:
            raise
        except Exception as exc:
            last_err = f"{stage} 异常：{exc}"
            time.sleep(3 * (i + 1))
            continue
    raise RuntimeError(f"{stage} 失败（已重试 {tries} 次）：{last_err}")


def _post_json(url: str, headers=None, files=None, data=None, json=None,
              timeout: int = 120, tries: int = 4, stage: str = "提交数据") -> dict:
    """带重试的 JSON POST。4xx 立即抛出（欠费/鉴权），其余网络/解析错误退避重试。"""
    last_err = None
    for i in range(tries):
        try:
            resp = requests.post(
                url, headers=headers, files=files, data=data, json=json, timeout=timeout
            )
            if resp.status_code != 200:
                last_err = f"{stage} HTTP {resp.status_code}: {resp.text[:300]}"
                if 400 <= resp.status_code < 500:
                    raise RuntimeError(last_err)
                time.sleep(3 * (i + 1))
                continue
            return resp.json()
        except RuntimeError:
            raise
        except Exception as exc:
            last_err = f"{stage} 异常：{exc}"
            time.sleep(3 * (i + 1))
            continue
    raise RuntimeError(f"{stage} 失败（已重试 {tries} 次）：{last_err}")


def extract_text(result: dict) -> str:
    """从转写结果中提取正文文本。

    真实结构：result.output.result.transcription_url → 该 URL 指向的 JSON 含
    {"transcripts": [{"channel_id":0, "text": "..."}, ...]}
    """
    transcription_url = (
        result.get("output", {}).get("result", {}).get("transcription_url")
    )
    if not transcription_url:
        raise RuntimeError("结果中未找到 transcription_url")
    # 结果文件可能较大或受网络波动影响，带重试拉取，避免“转写成功却没存下”
    data = _get_json(transcription_url, timeout=60, stage="拉取转写结果")
    parts = []
    for tr in data.get("transcripts", []) or []:
        txt = tr.get("text")
        if txt:
            parts.append(txt.strip())
    return "\n\n".join(parts).strip()


# ---------- 续取：额度耗尽/结果未落盘时，从已记录的 task_id 拉回结果 ----------
def recover_from_index(index_path: str, out_dir: Path, fmt: str) -> int:
    """读取 task_index.csv，把『已提交转写、但本地还没生成文稿』的任务，
    在阿里云 24 小时留存窗口内重新拉回结果并落盘。

    适用场景：
      - 上次批量跑到一半额度耗尽，部分已转写的文稿没存下来；
      - 上次因网络/限流导致“转写成功却没存下”。
    注意：阿里云只保留任务结果 24 小时，超期后 task_id 失效、无法找回。
    """
    p = Path(index_path)
    if not p.exists():
        print(f"未找到 {index_path}，无法续取。请先用普通模式跑过一次（会生成该文件）。")
        return 1
    rows = []
    with open(p, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if not rows:
        print("task_index.csv 为空，没有可续取的任务。")
        return 0
    print(f"读取到 {len(rows)} 条任务记录，开始续取（仅处理本地尚未生成文稿的）...")
    ok = skipped = failed = 0
    for r in rows:
        fname = r.get("filename") or ""
        tid = r.get("task_id") or ""
        if not fname or not tid:
            continue
        targets = []
        if fmt in ("doc", "both"):
            targets.append(out_dir / (Path(fname).stem + ".doc"))
        if fmt in ("md", "both"):
            targets.append(out_dir / (Path(fname).stem + ".md"))
        if targets and all(t.exists() for t in targets):
            skipped += 1
            continue
        print(f"[续取] {fname} (task_id={tid}) ...")
        try:
            result = query_transcription(tid, timeout_s=120)
            text = extract_text(result)
            if not text:
                print("  结果为空，跳过")
                failed += 1
                continue
            for t in targets:
                t.write_text(text, encoding="utf-8")
            ok += 1
            print(f"  ✓ 已补回（{len(text)} 字）")
        except Exception as exc:
            print(f"  ✗ 续取失败（很可能已超 24h 或任务不存在）：{exc}")
            failed += 1
    print(f"\n续取完成：成功补回 {ok} 个，已存在跳过 {skipped} 个，失败 {failed} 个。")
    return 0


# ---------- 批量找回：列出租户下最近 24h 内已成功的任务，逐个下载转写结果 ----------
def recover_bulk(base_url: str, api_key: str, model: str, out_dir: Path,
                window_start: str = "", window_end: str = "",
                status: str = "SUCCEEDED", page_size: int = 100) -> int:
    """批量找回历史任务结果（无需事先记录 task_id）。

    利用百炼「管理异步任务」的批量查询接口 GET /api/v1/tasks（不带 task_id 即返回
    该账号最近 24 小时内提交的所有任务），逐个取出 transcription_url 并下载结果 JSON。

    适用场景：
      - 之前批量转写成功、但因脚本未记录 task_id / 未落盘而“丢失”的文稿；
      - 任何一次“转写已完成但没存下”的意外。

    重要限制（平台侧）：
      - 任务结果仅保留 24 小时，超期后无法查询/下载；
      - 返回的结果文件名是 task_id(uuid)，**不含原始录音名**，找回的文稿无法直接
        对应回原录音（除非你按文稿内容手动辨认）。
    因此本功能主要用于“抢救已付费但没存下的文本”，而非替代规范的续跑/清单机制。
    """
    if not api_key:
        print("缺少 DASHSCOPE_API_KEY，无法找回。", file=sys.stderr)
        return 1
    headers = {"Authorization": f"Bearer {api_key}"}
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"批量找回 → 输出目录：{out_dir}")
    print(f"查询条件：model={model}, status={status}"
          + (f", 时间窗 {window_start}~{window_end}" if window_start or window_end else "（默认最近 24h）"))

    # 1) 分页列出所有符合条件的 task_id
    task_ids: list[str] = []
    page = 1
    while True:
        params = {"status": status, "model_name": model,
                  "page_size": page_size, "page_no": page}
        if window_start:
            params["start_time"] = window_start
        if window_end:
            params["end_time"] = window_end
        try:
            r = requests.get(f"{base_url}/api/v1/tasks", headers=headers,
                             params=params, timeout=30)
        except Exception as exc:
            print(f"  列表请求失败：{exc}")
            break
        if r.status_code != 200:
            print(f"  列表页 {page} HTTP {r.status_code}：{r.text[:200]}")
            break
        data = r.json()
        for row in data.get("data", []) or []:
            if row.get("status") == status and row.get("task_id"):
                task_ids.append(row["task_id"])
        total_page = data.get("total_page", 1) or 1
        if page >= total_page or not data.get("data"):
            break
        page += 1
        time.sleep(0.1)

    if not task_ids:
        print("没有找到可找回的任务（可能已超 24h，或该时间窗内无成功任务）。")
        return 0
    print(f"共找到 {len(task_ids)} 个 {status} 任务，开始下载结果...")

    # 2) 逐个取详情 → 下载 transcription_url → 存 JSON + 提取纯文本
    ok = fail = 0
    for i, tid in enumerate(task_ids, 1):
        try:
            d = requests.get(f"{base_url}/api/v1/tasks/{tid}", headers=headers,
                             timeout=30).json()
            out = (d.get("output") or {})
            urls = []
            res = out.get("result") or {}
            if res.get("transcription_url"):
                urls = [res["transcription_url"]]
            else:
                for rr in (out.get("results") or []):
                    if rr.get("transcription_url"):
                        urls.append(rr["transcription_url"])
            if not urls:
                print(f"  [{i}/{len(task_ids)}] {tid} 无 transcription_url，跳过")
                fail += 1
                continue
            # 下载结果 JSON（OSS 直链；部分网络环境下偶尔返回空响应，做一次重试）
            content = b""
            for attempt in range(2):
                try:
                    content = requests.get(urls[0], timeout=60).content
                    if content:
                        break
                except Exception:
                    pass
                time.sleep(1)
            if not content:
                print(f"  [{i}/{len(task_ids)}] {tid} 下载结果为空，跳过", file=sys.stderr)
                fail += 1
                continue
            # 已存在且非空的同名文件跳过，便于分多次运行累计、不重复下载
            jpath = out_dir / f"{tid}.json"
            if jpath.exists() and jpath.stat().st_size > 0:
                ok += 1
                if i % 50 == 0:
                    print(f"  已处理 {i}/{len(task_ids)}（含跳过已有）")
                continue
            jpath.write_bytes(content)
            # 尝试从 JSON 抽取纯文本，方便直接阅读/搜索
            try:
                j = json.loads(content)
                parts = []
                for tr in (j.get("transcripts") or []):
                    if isinstance(tr, dict) and tr.get("text"):
                        parts.append(tr["text"].strip())
                text = "\n\n".join(p for p in parts if p)
                if text:
                    (out_dir / f"{tid}.txt").write_text(text, encoding="utf-8")
            except Exception:
                pass
            ok += 1
            if i % 50 == 0:
                print(f"  已下载 {i}/{len(task_ids)}")
        except Exception as exc:
            print(f"  [{i}/{len(task_ids)}] {tid} 失败：{exc}", file=sys.stderr)
            fail += 1
        time.sleep(0.1)

    print(f"\n批量找回完成：成功 {ok} 个，失败 {fail} 个。")
    print(f"文件保存在：{out_dir}")
    print("⚠️ 找回的文件以 task_id(uuid) 命名，不含原始录音名，无法直接对应回原录音；"
          "可按文稿内容手动辨认，或用于内容检索。")
    return 0


# ---------- 辅助 ----------
def list_audio_files(directory: Path, exts: set) -> list[Path]:
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts
    )


def transcribe_one(audio: Path, lang: str, overwrite: bool, out_dir: Path, fmt: str,
                  index_cb=None) -> None:
    # 根据输出格式决定生成哪些文件
    targets: list[Path] = []
    if fmt in ("doc", "both"):
        targets.append(out_dir / (audio.stem + ".doc"))
    if fmt in ("md", "both"):
        targets.append(out_dir / (audio.stem + ".md"))

    if targets and all(t.exists() for t in targets) and not overwrite:
        names = "、".join(t.name for t in targets)
        print(f"[跳过] {audio.name}（文字稿已存在：{names}）")
        return

    print(f"[处理] {audio.name} ...")
    url = upload_to_dashscope(audio)
    print("  已上传到百炼，开始转写...")

    task_id = submit_transcription(url, lang=lang)
    # 立即记录 task_id ↔ 文件名 映射，方便额度中途耗尽/结果未落盘时，
    # 在阿里云 24 小时留存窗口内用 --recover 把已转写的文稿重新拉回来
    if index_cb:
        index_cb(audio, task_id)
    result = query_transcription(task_id)

    # 以下任何一步失败，都意味着“阿里云已转完、但结果没拿到/没存下”
    # → 检索/保存环节有系统性问题，应整批停止，避免继续空耗额度（防欠费）
    try:
        text = extract_text(result)
        if not text:
            raise RuntimeError(f"{audio.name} 转写结果为空（任务成功但无文本）")
        for t in targets:
            t.write_text(text, encoding="utf-8")
    except Exception as exc:
        raise TranscribedButSaveFailed(
            f"转写已完成但文稿未保存成功：{exc}"
            f"（请排查网络/限流/磁盘；账号恢复后可用 --recover 在 24h 内免费补回）"
        ) from exc
    names = "、".join(t.name for t in targets)
    print(f"  ✓ 已生成 {names}（{len(text)} 字）→ {out_dir}")


# ---------- 转写清单（manifest）：记录每个文件「已转写 / 未转写」，方便下次续跑 ----------
MANIFEST_COLS = ["audio_path", "status", "output_doc", "task_id", "updated_at", "note"]


def compute_targets(audio: Path, out_dir: Path, fmt: str) -> list[Path]:
    """根据输出格式计算该音频应生成的文字稿路径列表。"""
    targets = []
    if fmt in ("doc", "both"):
        targets.append(out_dir / (audio.stem + ".doc"))
    if fmt in ("md", "both"):
        targets.append(out_dir / (audio.stem + ".md"))
    return targets


def build_manifest(files: list[Path], out_dir: Path, fmt: str, manifest_path: Path) -> dict:
    """构建/合并转写清单。

    - 若本地已存在对应文字稿，直接标记为 DONE（续跑时不重复花钱）；
    - 否则标记为 PENDING；
    - 若已有旧清单，沿用其中的 task_id（便于 --recover 续取）。
    返回的 dict 以「音频绝对路径」为键，值是各列组成的 dict。
    """
    prev = {}
    if manifest_path.exists():
        with open(manifest_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                prev[r.get("audio_path", "")] = r
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    m = {}
    for audio in files:
        od = out_dir if out_dir else audio.parent
        targets = compute_targets(audio, od, fmt)
        key = str(audio)
        done = bool(targets) and all(t.exists() for t in targets)
        old = prev.get(key, {}) or {}
        if done:
            m[key] = {
                "audio_path": key,
                "status": "DONE",
                "output_doc": ";".join(str(t) for t in targets),
                "task_id": old.get("task_id", "") or "",
                "updated_at": old.get("updated_at", "") or now,
                "note": old.get("note", "") or "已存在文稿",
            }
        else:
            # 旧清单即便标过 DONE，只要文稿丢失就重置为 PENDING，下次重新转写
            m[key] = {
                "audio_path": key,
                "status": "PENDING",
                "output_doc": ";".join(str(t) for t in targets),
                "task_id": old.get("task_id", "") or "",
                "updated_at": old.get("updated_at", "") or "",
                "note": old.get("note", "") or "",
            }
    return m


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    """原子写入清单：先写临时文件再 rename，避免写到一半程序崩溃导致清单损坏。"""
    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS)
        w.writeheader()
        for row in manifest.values():
            w.writerow(row)
    os.replace(tmp, manifest_path)


def print_manifest_status(manifest_path: Path) -> int:
    """打印清单统计：已转写 / 未转写 / 失败 各多少，并列出未完成的文件。"""
    if not manifest_path.exists():
        print(f"未找到清单 {manifest_path}，请先运行一次转写（会自动生成该文件）。")
        return 1
    done = pending = failed = 0
    pending_files, failed_files = [], []
    with open(manifest_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            st = r.get("status", "")
            if st == "DONE":
                done += 1
            elif st == "FAILED":
                failed += 1
                failed_files.append(r.get("audio_path", ""))
            else:
                pending += 1
                pending_files.append(r.get("audio_path", ""))
    print(f"转写清单（{manifest_path}）：")
    print(f"  已转写完成：{done} 个")
    print(f"  未转写    ：{pending} 个")
    print(f"  失败      ：{failed} 个")
    if pending_files:
        print(f"\n未转写文件清单（共 {len(pending_files)} 个）：")
        for p in pending_files:
            print(f"  - {p}")
    if failed_files:
        print(f"\n失败文件清单（共 {len(failed_files)} 个，可 --overwrite 重试）：")
        for p in failed_files:
            print(f"  - {p}")
    return 0


# ---------- 主流程 ----------
def main() -> int:
    parser = argparse.ArgumentParser(description="批量转写本地音频为文字稿（无需 OSS）")
    parser.add_argument(
        "--dir",
        action="append",
        default=[],
        type=Path,
        help="要处理的音频目录；可多次使用以批量处理多个文件夹（也可用 config.env 的 INPUT_DIRS）",
    )
    parser.add_argument("--file", type=Path, help="单个音频文件")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="转写文稿输出目录（默认：音频所在目录；也可用环境变量 OUTPUT_DIR）",
    )
    parser.add_argument(
        "--ext",
        default=",".join(sorted(SUPPORTED_EXT)),
        help="支持的扩展名，逗号分隔，默认常见音频格式",
    )
    parser.add_argument(
        "--lang",
        default="auto",
        help="语言：auto(自动)/cn/en/yue/fspk…，默认 auto",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="覆盖已存在的文字稿（默认跳过）"
    )
    parser.add_argument(
        "--format",
        choices=["md", "doc", "both"],
        default=None,
        help="输出格式：doc(默认,与本地已有.doc统一) / md / both；也可用环境变量 OUTPUT_FORMAT",
    )
    parser.add_argument(
        "--recover", action="store_true",
        help="续取模式：读取 task_index.csv，把『已提交转写但没生成文稿』的任务在 24h 内存活窗口内重新拉回结果",
    )
    parser.add_argument(
        "--recover-bulk", action="store_true",
        help="批量找回：列出账号最近 24h 内已成功的任务(无需事先记录task_id)，逐个下载转写结果JSON+文本（按时间窗筛选，结果以uuid命名）",
    )
    parser.add_argument("--start-time", default="", help="批量找回时间窗起点 YYYYMMDDhhmmss（默认最近24h）")
    parser.add_argument("--end-time", default="", help="批量找回时间窗终点 YYYYMMDDhhmmss")
    parser.add_argument(
        "--status", action="store_true",
        help="仅打印转写清单（已转写/未转写/失败统计），不执行转写",
    )
    args = parser.parse_args()

    # 输出格式：命令行 > 环境变量 OUTPUT_FORMAT > 默认 doc
    fmt = (args.format or os.environ.get("OUTPUT_FORMAT", "doc")).lower()
    if fmt not in ("md", "doc", "both"):
        fmt = "doc"

    # 解析输出目录：命令行 > 环境变量 > 音频同目录
    out_dir = None
    if args.output_dir:
        out_dir = args.output_dir.expanduser().resolve()
    elif os.environ.get("OUTPUT_DIR"):
        out_dir = Path(os.environ["OUTPUT_DIR"]).expanduser().resolve()
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"输出目录：{out_dir}")

    # 仅查看清单模式：不执行转写
    if args.status:
        base = out_dir if out_dir else Path.cwd()
        return print_manifest_status(base / "transcribe_manifest.csv")

    # 续取模式：读取 task_index.csv，把已转写但没落盘的任务在 24h 窗口内拉回
    if args.recover:
        base = out_dir if out_dir else Path.cwd()
        index_path = str(base / "task_index.csv")
        return recover_from_index(index_path, out_dir if out_dir else Path.cwd(), fmt)

    # 批量找回模式：列出账号最近 24h 内已成功任务，逐个下载结果（无需事先记录 task_id）
    if args.recover_bulk:
        base = out_dir if out_dir else Path.cwd()
        ts = time.strftime("%Y%m%d_%H%M%S")
        folder = base / f"recovered_bulk_{ts}"
        return recover_bulk(
            BASE_URL, os.environ.get("DASHSCOPE_API_KEY", ""), MODEL, folder,
            window_start=args.start_time, window_end=args.end_time,
        )

    if not args.dir and not args.file:
        parser.error("必须提供至少一个 --dir 或 --file")

    # 进度日志：每处理一个文件就追加一行，方便额度耗尽后知道转到了哪、下次从哪续
    log_path = os.environ.get("PROGRESS_LOG")
    if not log_path:
        base = out_dir if out_dir else Path.cwd()
        log_path = str(base / "transcribe_progress.log")
    log_f = open(log_path, "a", encoding="utf-8")
    print(f"进度日志：{log_path}")

    # task_id 索引：文件名 ↔ task_id，用于额度耗尽后 24h 内存活窗口内续取结果
    index_path = str(Path(log_path).parent / "task_index.csv")
    index_f = open(index_path, "a", newline="", encoding="utf-8")
    index_writer = csv.writer(index_f)
    if os.path.getsize(index_path) == 0:
        index_writer.writerow(["filename", "task_id", "submit_time", "audio_path"])
    print(f"task_id 索引：{index_path}")

    def index_cb(audio: Path, task_id: str) -> None:
        # 提交成功即刻写入 文件名↔task_id，便于额度耗尽后在 24h 窗口内用 --recover 续取
        index_writer.writerow([audio.name, task_id, time.strftime("%Y-%m-%d %H:%M:%S"), str(audio)])
        index_f.flush()
        # 同步记到转写清单，方便 --recover / 排查
        row = manifest.get(str(audio))
        if row is not None:
            row["task_id"] = task_id

    def log_line(status: str, audio: Path, note: str = "") -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log_f.write(f"{ts}\t{status}\t{audio}\t{note}\n")
        log_f.flush()

    exts = {e.strip().lower() for e in args.ext.split(",") if e.strip()}

    files: list[Path] = []
    if args.file:
        files = [args.file.resolve()]
    for d in args.dir:
        d = d.resolve()
        if not d.is_dir():
            print(f"目录不存在: {d}", file=sys.stderr)
            return 2
        files.extend(list_audio_files(d, exts))

    if not files:
        print("没有找到匹配的音频文件。")
        log_f.close()
        index_f.close()
        return 0

    print(f"共 {len(files)} 个音频待处理。")

    # 转写清单：记录每个文件「已转写(DONE)/未转写(PENDING)/失败(FAILED)」。
    # 无论因何种原因停止（额度耗尽、保存失败、手动中断、意外崩溃），都能凭此清单
    # 知道哪些已转写、哪些没转写，下次直接重跑即可无缝续跑（已 DONE 的会自动跳过）。
    manifest_path = (out_dir if out_dir else files[0].parent) / "transcribe_manifest.csv"
    manifest = build_manifest(files, out_dir, fmt, manifest_path)
    save_manifest(manifest_path, manifest)  # 先落一份初始清单（全部 PENDING/DONE）
    print(f"转写清单：{manifest_path}")

    ok, fail = 0, 0
    stopped = False
    # 每处理完一个文件后的间隔（秒），避免短时间大量请求触发百炼限流；可用环境变量 REQUEST_INTERVAL 调整
    REQUEST_INTERVAL = float(os.environ.get("REQUEST_INTERVAL", "2"))
    # 额度/余额/账号未开通/限流相关的关键词：命中即判定为“充钱才能继续”，主动停止整批
    QUOTA_KEYWORDS = (
        "nobalance", "balance", "额度", "余额", "insufficient", "quota",
        "paymentrequired", "402", "accountbalance", "accountnotactivated", "未开通",
        "accountnotexist", "nocode", "forbidden", "arrearage", "overdue",
        "access denied", "throttling", "throttled",
    )

    try:
        for idx, audio in enumerate(files, 1):
            od = out_dir if out_dir else audio.parent
            key = str(audio)
            row = manifest.get(key)
            # 已转写完成且文稿仍在 → 跳过，不重复花钱
            if row is not None and row["status"] == "DONE":
                if all(t.exists() for t in compute_targets(audio, od, fmt)):
                    print(f"[跳过] {audio.name}（已转写完成）")
                    continue
                row["status"] = "PENDING"  # 文稿丢失，重置待处理
            try:
                transcribe_one(audio, args.lang, args.overwrite, od, fmt, index_cb=index_cb)
                ok += 1
                log_line("OK", audio)
                if row is not None:
                    row["status"] = "DONE"
                    row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    row["note"] = ""
                    save_manifest(manifest_path, manifest)
            except Exception as exc:
                msg = str(exc)
                log_line("FAIL", audio, msg[:240])
                if row is not None:
                    row["status"] = "FAILED"
                    row["note"] = msg[:200]
                # 两类“硬停止”条件：
                #  1) 额度/余额/限流（关键词命中）→ 账号层面无法继续
                #  2) 转写已完成但文稿未保存成功（TranscribedButSaveFailed）
                #     → 检索/保存环节系统性故障，继续提交只会空耗额度、甚至造成欠费
                if isinstance(exc, TranscribedButSaveFailed) or any(
                    k in msg.lower() for k in QUOTA_KEYWORDS
                ):
                    if isinstance(exc, TranscribedButSaveFailed):
                        print(f"\n⚠️  检测到「转写已完成但文稿未保存成功」的系统性故障（{msg[:200]}）")
                        print(f"    已成功 {ok} 个，进度已写入清单：{manifest_path}")
                        print(f"    本次停止于文件：{audio.name}")
                        print(f"    👉 阿里云侧转写其实已完成，问题在「取结果/存盘」。请先排查网络/限流/磁盘；")
                        print(f"       账号恢复正常后，24h 内运行 `bash run.sh --recover` 可免费把已转写的文稿补回；")
                        print(f"       重跑 `bash run.sh` 则已生成的 .doc 会自动跳过。")
                    else:
                        print(f"\n⚠️  检测到额度/余额不足或账号未开通/限流（{msg[:200]}）")
                        print(f"    已成功 {ok} 个，进度已写入清单：{manifest_path}")
                        print(f"    本次停止于文件：{audio.name}")
                        print(f"    👉 下次充值后，直接重跑相同命令即可；已生成的 .doc 会自动跳过，")
                        print(f"       脚本会从「{audio.name}」继续转写。")
                    if row is not None:
                        save_manifest(manifest_path, manifest)
                    stopped = True
                    break
                print(f"  ✗ {audio.name} 失败：{exc}", file=sys.stderr)
                fail += 1
                if row is not None:
                    save_manifest(manifest_path, manifest)
            # 限速间隔：跳过已处理/失败的文件，降低触发限流的概率
            if idx < len(files):
                time.sleep(REQUEST_INTERVAL)
    finally:
        # 兜底：无论正常结束、硬停止还是意外退出，都确保清单已落盘
        try:
            save_manifest(manifest_path, manifest)
        except Exception:
            pass
        if log_f:
            log_f.close()
        if index_f:
            index_f.close()

    done_n = sum(1 for r in manifest.values() if r["status"] == "DONE")
    pend_n = sum(1 for r in manifest.values() if r["status"] == "PENDING")
    fail_n = sum(1 for r in manifest.values() if r["status"] == "FAILED")
    print(f"\n清单 {manifest_path}：已转写 {done_n} / 未转写 {pend_n} / 失败 {fail_n}")
    print(f"下次直接重跑相同命令即可续跑；或运行 `bash run.sh --status` 查看进度。")

    if stopped:
        # 返回码 2 表示“硬停止（额度耗尽 / 转写完成但保存失败）”，便于外层脚本判断
        return 2
    print(f"\n完成：成功 {ok}，失败 {fail}。失败项可用 --overwrite 重试。")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
