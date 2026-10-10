---
name: audio-to-text
description: 把本地音频文件批量转成文字稿：上传到百炼自有文件服务后调用阿里云百炼语音识别API，逐个转写、断点续传、输出文字稿（默认 .doc，可配为 .md / both）。用户有大量本地音频(本地/网盘下载后)需要变成文字稿以做知识库时使用。无需配置OSS。
---

# audio-to-text

把本地音频文件批量转成文字稿（默认 `.doc`，可配置为 `.md` / `both`），供后续做成知识库。

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
   ↓ 上传到百炼自有文件服务（无需 OSS）
   ↓ 换取临时可下载 URL
百炼语音识别 API（qwen3-asr-flash-filetrans，异步）
   ↓ 轮询
识别结果 → 提取文本 → 保存为同名 .doc（默认；可用 --format 改为 .md / both）
```

核心脚本：`scripts/transcribe.py`

## 使用前提（必须满足）

1. **已开通**阿里云百炼语音识别服务，并拿到 **API Key**（`DASHSCOPE_API_KEY`）。
2. 音频文件**已在本地**（网盘音频需先下载到本地，API 无法读取网盘）。

**本 Skill 不依赖对象存储 OSS**：本地音频会先上传到百炼自有的文件服务获取临时托管 URL，再提交转写，无需你开通或配置任何 OSS 资源。

所有凭证通过**环境变量**提供，**绝不写进代码**。这也是发布给其他人时，对方各自配置自己 Key 的通用方式。

## 环境变量配置

只需配置一个必填项，输出目录与站点可选：

```bash
# 必填：阿里云百炼 API Key（只能用你自己的，不能共用别人的）
export DASHSCOPE_API_KEY="sk-你的百炼Key"

# 必填（批量）：要处理的文件夹，多个用逗号分隔
export INPUT_DIRS="/Users/你/音频文件夹A,/Users/你/音频文件夹B"

# 可选：转写文稿输出目录；不填则自动存到音频所在目录
export OUTPUT_DIR="/path/to/your/output"

# 可选：输出格式 doc(默认) / md / both
export OUTPUT_FORMAT="doc"

# 可选：语言 auto(自动)/cn/en/yue/fspk…，默认 auto
export TRANSCRIBE_LANG="auto"

# 可选：识别模型。qwen3-asr-flash-filetrans(默认,最准) / paraformer-v2(最便宜) / paraformer-8k-v2(电话8k)
# 不填=默认最准模型；想省钱改成下面这行：
export ASR_MODEL="paraformer-v2"

# 可选：服务站点。国内站默认 https://dashscope.aliyuncs.com ；
# 国际站（密钥以 sk-ws- 开头多为国际站）必须填下面这行：
export DASHSCOPE_BASE_URL="https://dashscope-intl.aliyuncs.com"
```

依赖安装（本机只需一次，仅需 requests）：

```bash
python3 -m pip install requests
```

## 用法

### 批量转写整个目录

```bash
python3 scripts/transcribe.py --dir /path/to/audio
```

- 自动处理目录下所有常见音频（mp3/wav/m4a/aac/flac/ogg/amr/wma/opus）
- 每个音频生成同名 `.doc` 文字稿（可用 `--format md` / `--format both` 变更）
- **已存在同名文字稿的自动跳过**，断点续传，重跑不重复花钱

### 指定输出目录（文稿统一存放）

```bash
python3 scripts/transcribe.py --dir /path/to/audio --output-dir /path/to/output
```

输出目录也可写进环境变量 `OUTPUT_DIR`，一次配置长期生效，无需每次在命令行加 `--output-dir`。

### 转写单个文件

```bash
python3 scripts/transcribe.py --file /path/to/single.mp3
```

### 常用参数

| 参数 | 作用 | 默认 |
|---|---|---|
| `--dir` | 批量处理目录 | 必填（与 --file 二选一） |
| `--file` | 处理单个文件 | 必填（与 --dir 二选一） |
| `--output-dir` | 转写文稿输出目录（也可用环境变量 OUTPUT_DIR） | 音频所在目录 |
| `--format` | 输出格式：doc(默认)/md/both（也可用环境变量 OUTPUT_FORMAT） | doc |
| `--ext` | 指定扩展名，逗号分隔 | 常见音频格式 |
| `--lang` | 语言：auto(自动)/cn/en/yue/fspk | auto |
| `--overwrite` | 覆盖已存在的文字稿 | 关闭（跳过） |
| `--recover` | 续取模式：把 `task_index.csv` 里已转写但没落盘的任务在 24h 内拉回 | 关闭 |

## 批量配置（推荐给非技术用户）

不用每次敲命令。把要处理的文件夹和输出位置写进 `config.env`，然后只运行一行：

1. 复制模板：`cp config.env.example config.env`
2. 用文本编辑器打开 `config.env`，填好这几项：
   - `DASHSCOPE_API_KEY`：你的百炼 Key（**改 API Key 就是改这一行**）
   - `INPUT_DIRS`：要处理的文件夹路径，多个用半角逗号 `,` 隔开（**批量配置就改这里**）
   - `OUTPUT_DIR`：想统一存到哪里（不填就存在音频旁边）
   - `OUTPUT_FORMAT`：默认 `doc`
3. 在 Terminal 进入本目录，运行：
   ```bash
   bash run.sh
   ```
   run.sh 会自动读取 config.env，把 `INPUT_DIRS` 里的每个文件夹都跑一遍。

> 想临时只处理某一个文件夹，不改动 config.env，也可以直接：
> `bash run.sh --dir /某个/具体/目录`

## 修改 / 更换 API Key 怎么改

直接编辑 `config.env` 里的 `DASHSCOPE_API_KEY="..."` 这一行，换成新申请的 Key，保存即可。
下次运行 `bash run.sh` 会用新 Key。

**安全提醒**：`config.env` 已被 `.gitignore` 忽略，不会被上传到 GitHub，可以放心放本地 Key；
千万不要把它加进 git（`git add config.env`）或分享出去。需要对外共享时，只发 `config.env.example` 模板。

## 他人（同事/朋友）如何使用这个 Skill

本 Skill 不绑定任何人的 Key，**别人必须用自己的阿里云百炼账号 Key**。步骤：

1. 安装：在支持 Agent Skills 的客户端里执行
   ```bash
   npx -y skills add lotuser1/Susan_Skill -g --all
   ```
2. 找到安装后的 skill 目录（一般在用户目录下的 skills 文件夹里的 `audio-to-text/`），复制模板：
   ```bash
   cp config.env.example config.env
   ```
3. 编辑 `config.env`：填 **自己的** `DASHSCOPE_API_KEY`、`INPUT_DIRS`（要处理的文件夹）、`OUTPUT_DIR`。
4. 安装依赖（只需一次）：
   ```bash
   python3 -m pip install requests
   ```
5. 运行：在本目录执行 `bash run.sh`。

## 找回未落盘的文稿（阿里云仅保留 24 小时）

阿里云百炼转写完成后，结果 JSON 只通过一个 **`transcription_url` 临时下载链接** 提供，**有效期 24 小时**，超时后任务连同结果一并清除；且阿里云**没有"列出我所有任务"的入口**——必须靠本 Skill 自己留下的 `task_id` 才能回去找。

为此，本 Skill 会在输出目录自动生成 `task_index.csv`（字段：文件名、task_id、提交时间、音频路径），记录每个文件的转写任务 ID。

- **正常情况**：转写成功会立刻落盘，无需关心。
- **额度中途耗尽 / 网络抖动导致"转写成功却没存下"**：只要还在 **提交后 24 小时内**，用续取模式把已转写但没落盘的文稿拉回来（不重复花钱）：

  ```bash
  # 在 config.env 已配置 OUTPUT_DIR 的前提下
  python3 scripts/transcribe.py --recover
  # 或经 run.sh：
  bash run.sh --recover
  ```

  `--recover` 读取 `task_index.csv`，对"本地还没生成文稿"的任务重新 `GET /api/v1/tasks/{task_id}` 换链接并下载；已存在的自动跳过；超 24h 或任务不存在的会报失败（此时只能重新转写）。

> 重要：一旦超过 24 小时，`task_id` 失效、结果不可恢复，只能重新提交转写（重新计费）。因此额度不足时建议尽快充值，并立刻跑一次 `--recover`。

## 关键行为与边界

- **只读不删**：不改动、不删除你的原始音频，只在旁边新增 `.doc`（或按 `--format` 指定的格式）。
- **断点续传**：已生成文字稿的音频自动跳过；转写失败会在运行时标出，可用 `--overwrite` 重试单个。
- **凭证不硬编码**：所有 Key 走环境变量；若检测到缺少凭证，脚本会明确报错并提示配置。
- **安全边界**：不读取密钥文件、不上传非音频文件、不把原始音频内容外发到除阿里云以外的第三方。

## 与 dbs-knowledge 衔接

转写完成后，把生成的文字稿目录交给 `/dbs-knowledge` 搭建知识库：

> 把我这个音频文字稿目录建成知识库，按课程板块归档，支持按板块查找。

这样「音频 → 文字稿 → 知识库」链路就闭环了。

## 语言

- 用户用中文就用中文回复，用英文就用英文回复。
