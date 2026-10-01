---
name: audio-to-text
description: 把本地音频文件批量转成文字稿：上传OSS后调用阿里云百炼语音识别API，逐个转写、断点续传、输出Markdown。用户有大量本地音频(本地/网盘下载后)需要变成文字稿以做知识库时使用。
---

# audio-to-text

把本地音频文件批量转成文字稿（.md），供后续做成知识库。

## 一句话定义

**把「一个目录里成百上千个音频」，批量转成「可阅读、可检索的文字稿」，且不会重复转写已完成的文件。**

它解决的是：
- 本地 / 网盘下载后的音频文件很多，无法一个个手动转
- 要批量、要省成本、要能中途断了下次接着跑
- 转出来的文字稿要稳定、可读，方便喂给知识库（如 dbs-knowledge）

它不处理的是：
- 实时语音识别（会议直播等）——本 Skill 只做已存音频文件的离线转写
- 视频文件的转写（如需要，请先单独提取音轨）
- 音频的翻译、总结、润色——本 Skill 只出原始文字稿

## 技术原理

```
本地音频文件
   ↓ 上传
阿里云 OSS（生成临时公网 URL）
   ↓ 提交异步任务
百炼语音识别 API（qwen3-asr-flash-filetrans）
   ↓ 轮询
识别结果 → 提取文本 → 保存为同名 .md
```

核心脚本：`scripts/transcribe.py`

## 使用前提（必须满足）

1. **已开通**阿里云百炼语音识别服务，并拿到 **API Key**（`DASHSCOPE_API_KEY`）。
2. **已有**阿里云 OSS 存储空间（Bucket），并拿到 AccessKey（`OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_BUCKET` / `OSS_ENDPOINT`）。
3. 音频文件**已在本地**（百度网盘的音频需先下载到本地，API 无法读取网盘）。

以上凭证都通过**环境变量**提供，**绝不写进代码**。这也是发布给其他人时，对方各自配置自己 Key 的通用方式。

## 环境变量配置

在运行前，把下面变量写进系统环境变量（Windows 用 `set`，macOS/Linux 用 `export`）：

```bash
export DASHSCOPE_API_KEY="sk-你的百炼Key"
export OSS_ACCESS_KEY_ID="你的AccessKeyId"
export OSS_ACCESS_KEY_SECRET="你的AccessKeySecret"
export OSS_BUCKET="你的Bucket名"
export OSS_ENDPOINT="oss-cn-hangzhou.aliyuncs.com"   # 按你OSS所在地域改
```

依赖安装：

```bash
pip install requests oss2
```

## 用法

### 批量转写整个目录

```bash
python3 scripts/transcribe.py --dir /path/to/audio
```

- 自动处理目录下所有常见音频（mp3/wav/m4a/aac/flac/ogg/amr/wma/opus）
- 每个音频旁边生成同名 `.md` 文字稿
- **已存在 .md 的自动跳过**，断点续传，重跑不重复花钱

### 转写单个文件

```bash
python3 scripts/transcribe.py --file /path/to/single.mp3
```

### 常用参数

| 参数 | 作用 | 默认 |
|---|---|---|
| `--dir` | 批量处理目录 | 必填（与 --file 二选一） |
| `--file` | 处理单个文件 | 必填（与 --dir 二选一） |
| `--ext` | 指定扩展名，逗号分隔 | 常见音频格式 |
| `--lang` | 语言：cn/en/yue/fspk | cn |
| `--overwrite` | 覆盖已存在的文字稿 | 关闭（跳过） |

## 关键行为与边界

- **只读不删**：不改动、不删除你的原始音频，只在旁边新增 `.md`。
- **断点续传**：已生成 `.md` 的音频自动跳过；转写失败会在运行时标出，可用 `--overwrite` 重试单个。
- **凭证不硬编码**：所有 Key 走环境变量；若检测到缺少凭证，脚本会明确报错并提示配置。
- **安全边界**：不读取密钥文件、不上传非音频文件、不把原始音频内容外发到除阿里云以外的第三方。

## 与 dbs-knowledge 衔接

转写完成后，把生成的文字稿目录交给 `/dbs-knowledge` 搭建知识库：

> 把我这个音频文字稿目录建成知识库，按课程板块归档，支持按板块查找。

这样「音频 → 文字稿 → 知识库」链路就闭环了。

## 语言

- 用户用中文就用中文回复，用英文就用英文回复。
