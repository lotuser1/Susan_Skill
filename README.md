# Susan Skill 集合仓库（Monorepo）

一个**不断新增的多 Skill 仓库**。所有面向「医美设计行业」的 AI 能力都放在 `skills/` 目录下，每个 Skill 自包含（各有自己的 `SKILL.md` + `scripts/` + `references/`），互不依赖。

当前包含的 Skill：

| Skill             | 能力                                                | 目录                      |
| ----------------- | ------------------------------------------------- | ----------------------- |
| **audio-to-text** | 本地音频批量转文字稿（上传百炼文件服务，无需OSS → 百炼语音识别 → 输出.doc，断点续传） | `skills/audio-to-text/` |
| **auto_transcribe** | 浏览器自动化把本地录音批量上传到「通义听悟」网页版转写（开启多人讨论/说话人分离）；**上传前先核对每个文件是否已在听悟转写成功，幂等跳过已完成的文件**，避免重复转写与重复计费 | `skills/auto_transcribe/` |

> **两个转写 Skill 怎么选？**
> - `audio-to-text`：走**阿里云百炼 API** 直接调 ASR，无需浏览器、无说话人分离，输出 `.doc` 文字稿。
> - `auto_transcribe`：走**通义听悟网页版**（浏览器自动化），核心卖点是「多人讨论/说话人分离（区分不同发言人）」和「网页批量上传」。它需要你本机先运行 `skills/auto_transcribe/start_chrome.sh` 启动带调试端口的 Chrome 并扫码登录，之后才能通过 CDP 接管窗口。
>
> 两者都具备**幂等续跑**能力：重跑时只处理未完成的文件，不会重复转写已成功的录音。

## 安装（一条命令装全部）

```bash
npx -y skills add lotuser1/Susan_Skill -g --all
```

> 这是你的真实安装命令：GitHub 用户名 `lotuser1`，仓库 `Susan_Skill`。此命令会安装仓库 `skills/` 下**所有** Skill，以后新增的 Skill 也会一并被装上，安装命令不用改。

## 仓库结构

```text
lotuser1/Susan_Skill/                      # 仓库根
├── README.md                        # 本说明（安装 + 使用）
├── CONTRIBUTING.md                  # 如何新增一个 Skill（给维护者/你自己）
├── .gitignore
└── skills/                          # 所有 Skill 都放这里
    ├── audio-to-text/               # 示例：第一个 Skill
    │   ├── SKILL.md
    │   ├── agents/openai.yaml
    │   └── scripts/transcribe.py
    └── <你以后新增的-skill>/         # 新增位置：往这里加文件夹即可
```

## 如何使用某个 Skill

### 方式一：在客户端里直接说（由 Agent 调用）

安装后，直接对支持 Agent Skills 的客户端说：

- 转写音频：`使用 $audio-to-text 完成它所解决的任务，并按可观察标准检查结果。`

### 方式二：自己手动跑（推荐，可批量配置）

1. 进入 `skills/audio-to-text/` 目录
2. `cp config.env.example config.env`，用文本编辑器填入你的百炼 Key 和要处理的文件夹（`INPUT_DIRS`）
3. `python3 -m pip install requests`（只需一次）
4. `bash run.sh` —— 会自动批量转写 `INPUT_DIRS` 里的所有文件夹

### ⚠️ 给别人用的关键提醒

本仓库**不含任何 Key**（`config.env` 已被忽略，不会上传）。别人安装后，**必须用自己的阿里云百炼账号申请 Key** 填进自己的 `config.env`，不能用你的。完整步骤见 `skills/audio-to-text/SKILL.md`。

每个 Skill 的详细用法见其所在目录的 `SKILL.md`。

## 发布规范

- **凭证安全**：所有 API Key / AccessKey 一律走环境变量，绝不写进代码或仓库。
- **隐私边界**：仓库只发布运行所需文件；本地样本、密钥、运行记录、私密内容绝不入库。
- **只发布自包含 Skill**：每个 Skill 文件夹独立可用，不依赖仓库其他部分。

## 与知识库衔接

转写出的文字稿可交给 `/dbs-knowledge` 搭建按主题归档的知识库，形成「音频 → 文字稿 → 知识库 → 各 Skill 取用」闭环。
