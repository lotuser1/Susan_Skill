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

    text = extract_text(result)
    if not text:
        raise RuntimeError(f"{audio.name} 转写结果为空")

    for t in targets:
        t.write_text(text, encoding="utf-8")
    names = "、".join(t.name for t in targets)
    print(f"  ✓ 已生成 {names}（{len(text)} 字）→ {out_dir}")


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

    # 续取模式：读取 task_index.csv，把已转写但没落盘的任务在 24h 窗口内拉回
    if args.recover:
        base = out_dir if out_dir else Path.cwd()
        index_path = str(base / "task_index.csv")
        return recover_from_index(index_path, out_dir if out_dir else Path.cwd(), fmt)

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
        return 0

    print(f"共 {len(files)} 个音频待处理。")
    ok, fail = 0, 0
    stopped_for_quota = False
    # 每处理完一个文件后的间隔（秒），避免短时间大量请求触发百炼限流；可用环境变量 REQUEST_INTERVAL 调整
    REQUEST_INTERVAL = float(os.environ.get("REQUEST_INTERVAL", "2"))
    # 额度/余额/账号未开通/限流相关的关键词：命中即判定为“充钱才能继续”，主动停止整批
    QUOTA_KEYWORDS = (
        "nobalance", "balance", "额度", "余额", "insufficient", "quota",
        "paymentrequired", "402", "accountbalance", "accountnotactivated", "未开通",
        "accountnotexist", "nocode", "forbidden", "arrearage", "overdue",
        "access denied", "throttling", "throttled",
    )
    for idx, audio in enumerate(files, 1):
        od = out_dir if out_dir else audio.parent
        try:
            transcribe_one(audio, args.lang, args.overwrite, od, fmt, index_cb=index_cb)
            ok += 1
            log_line("OK", audio)
        except Exception as exc:
            msg = str(exc)
            log_line("FAIL", audio, msg[:240])
            # 判断是否为额度/余额耗尽/限流：是则停止整批，避免对剩下上千个文件逐个重试浪费时间
            if any(k in msg.lower() for k in QUOTA_KEYWORDS):
                print(f"\n⚠️  检测到额度/余额不足或账号未开通/限流（{msg[:200]}）")
                print(f"    已成功 {ok} 个，进度已写入日志：{log_path}")
                print(f"    本次停止于文件：{audio.name}")
                print(f"    👉 下次充值后，重新运行相同命令即可；已生成的 .doc 会自动跳过，")
                print(f"       脚本会从「{audio.name}」继续转写。")
                stopped_for_quota = True
                break
            print(f"  ✗ {audio.name} 失败：{exc}", file=sys.stderr)
            fail += 1
        # 限速间隔：跳过已处理/失败的文件，降低触发限流的概率
        if idx < len(files):
            time.sleep(REQUEST_INTERVAL)

    if log_f:
        log_f.close()
    if index_f:
        index_f.close()

    if stopped_for_quota:
        # 返回码 2 表示“额度耗尽主动停止”，便于外层脚本判断
        return 2
    print(f"\n完成：成功 {ok}，失败 {fail}。失败项可用 --overwrite 重试。")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
