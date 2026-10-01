#!/usr/bin/env python3
"""
audio-to-text 核心脚本
==========================
把本地音频文件批量转成文字稿。

流程：本地音频 → 上传阿里云 OSS（生成公网 URL）→ 调用百炼语音识别 API（qwen3-asr-flash-filetrans）→ 轮询结果 → 保存同名 .md 文字稿。

关键特性：
- 批量：遍历目录下所有支持的音频文件
- 断点续传：已存在同名 .md 的音频自动跳过，下次继续跑不重复花钱
- 环境变量鉴权：API Key 和 OSS 凭证都从环境变量读取，不写死

所需环境变量：
  DASHSCOPE_API_KEY        阿里云百炼 API Key（必填）
  OSS_ACCESS_KEY_ID        阿里云 AccessKey ID（必填）
  OSS_ACCESS_KEY_SECRET    阿里云 AccessKey Secret（必填）
  OSS_BUCKET               目标 OSS Bucket 名称（必填）
  OSS_ENDPOINT             OSS Endpoint，如 oss-cn-hangzhou.aliyuncs.com（必填）

用法：
  python3 transcribe.py --dir /path/to/audio
  python3 transcribe.py --file /path/to/single.mp3
  python3 transcribe.py --dir /path/to/audio --ext ".mp3,.m4a,.wav"
  python3 transcribe.py --dir /path/to/audio --lang en
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

# 支持的音频扩展名
SUPPORTED_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma", ".opus",
}

# 识别模型
MODEL = "qwen3-asr-flash-filetrans"

# 百炼 API（北京地域）
API_SUBMIT = "https://dashscope.aliyuncs.com/api/v1/tasks"
API_QUERY = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"


# ---------- OSS 上传 ----------
def upload_to_oss(local_path: Path, key: str) -> str:
    """上传文件到 OSS，返回一个临时可公网访问的 URL。"""
    access_id = os.environ.get("OSS_ACCESS_KEY_ID")
    access_secret = os.environ.get("OSS_ACCESS_KEY_SECRET")
    bucket_name = os.environ.get("OSS_BUCKET")
    endpoint = os.environ.get("OSS_ENDPOINT")

    if not all([access_id, access_secret, bucket_name, endpoint]):
        raise RuntimeError(
            "缺少 OSS 凭证。请设置环境变量：OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_BUCKET, OSS_ENDPOINT"
        )

    import oss2

    auth = oss2.Auth(access_id, access_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    bucket.put_object_from_file(key, str(local_path))

    # 生成 1 小时的临时签名 URL，供转写服务下载
    url = bucket.sign_url("GET", key, 60 * 60)
    return url


# ---------- 转写任务 ----------
def submit_transcription(file_url: str, lang: str = "cn") -> str:
    """提交异步转写任务，返回 task_id。"""
    import requests

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少 DASHSCOPE_API_KEY。请先设置环境变量（阿里云百炼控制台获取）。"
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": MODEL,
        "input": {"file_url": file_url},
        "parameters": {
            "language_hints": [lang] if lang else [],
        },
    }

    resp = requests.post(API_SUBMIT, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"提交转写失败 HTTP {resp.status_code}: {resp.text}")

    data = resp.json()
    if data.get("code") not in (None, 200, 0):
        raise RuntimeError(f"提交转写报错: {data}")

    task_id = data["output"]["task_id"]
    return task_id


def query_transcription(task_id: str, api_key: str, timeout_s: int = 1800) -> dict:
    """轮询转写任务直至完成，返回完整任务结果。"""
    import requests

    headers = {"Authorization": f"Bearer {api_key}"}
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
        if status == "FAILED":
            raise RuntimeError(f"转写任务失败: {data.get('output', {}).get('message', '未知')}")
        if status == "RUNNING" or status == "PENDING":
            time.sleep(5)
            continue

        print(f"  未知状态 {status}，继续等待...", file=sys.stderr)
        time.sleep(5)

    raise TimeoutError(f"转写任务 {task_id} 超时（{timeout_s}s）")


# ---------- 结果转文字 ----------
def extract_text(result: dict) -> str:
    """从转写结果 JSON 中提取正文文本。"""
    output = result.get("output", {})
    results = output.get("results", [])
    texts = []
    for item in results:
        url = item.get("url")
        if not url:
            continue
        texts.append(_download_result_text(url))
    return "\n\n".join(text for text in texts if text)


def _download_result_text(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    transcript = data.get("transcript", "")
    # transcript 可能是字符串或带 words 的结构，做兼容
    if isinstance(transcript, str):
        return transcript
    if isinstance(transcript, dict):
        return transcript.get("text", "")
    return ""


# ---------- 辅助 ----------
def make_oss_key(local_path: Path) -> str:
    """生成 OSS 对象键，避免重名，保留可读性。"""
    safe_name = re.sub(r"[^\w\-.]", "_", local_path.stem)
    return f"audio-to-text/{safe_name}-{int(time.time())}{local_path.suffix}"


def list_audio_files(directory: Path, exts: set) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts)


def target_md(audio: Path) -> Path:
    return audio.with_suffix(".md")


def transcribe_one(audio: Path, lang: str, overwrite: bool) -> None:
    md = target_md(audio)
    if md.exists() and not overwrite:
        print(f"[跳过] {audio.name}（文字稿已存在）")
        return

    print(f"[处理] {audio.name} ...")
    key = make_oss_key(audio)
    url = upload_to_oss(audio, key)
    print(f"  已上传 OSS，开始转写...")

    task_id = submit_transcription(url, lang=lang)
    api_key = os.environ["DASHSCOPE_API_KEY"]
    result = query_transcription(task_id, api_key)

    text = extract_text(result)
    if not text:
        raise RuntimeError(f"{audio.name} 转写结果为空")

    md.write_text(text, encoding="utf-8")
    print(f"  ✓ 已生成 {md.name}（{len(text)} 字）")


# ---------- 主流程 ----------
def main() -> int:
    parser = argparse.ArgumentParser(description="批量转写本地音频为文字稿")
    parser.add_argument("--dir", type=Path, help="音频目录（批量）")
    parser.add_argument("--file", type=Path, help="单个音频文件")
    parser.add_argument("--ext", default=",".join(sorted(SUPPORTED_EXT)),
                        help="支持的扩展名，逗号分隔，默认常见音频格式")
    parser.add_argument("--lang", default="cn",
                        help="语言：cn/en/yue/fspk，默认 cn")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已存在的文字稿（默认跳过）")
    args = parser.parse_args()

    if not args.dir and not args.file:
        parser.error("必须提供 --dir 或 --file")

    exts = {e.strip().lower() for e in args.ext.split(",") if e.strip()}

    files: list[Path] = []
    if args.file:
        files = [args.file.resolve()]
    else:
        if not args.dir.is_dir():
            print(f"目录不存在: {args.dir}", file=sys.stderr)
            return 2
        files = list_audio_files(args.dir.resolve(), exts)

    if not files:
        print("没有找到匹配的音频文件。")
        return 0

    print(f"共 {len(files)} 个音频待处理。")

    ok, fail = 0, 0
    for audio in files:
        try:
            transcribe_one(audio, args.lang, args.overwrite)
            ok += 1
        except Exception as exc:
            print(f"  ✗ {audio.name} 失败：{exc}", file=sys.stderr)
            fail += 1

    print(f"\n完成：成功 {ok}，失败 {fail}。失败项可用 --overwrite 重试。")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
