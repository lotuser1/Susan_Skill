# 医美设计 · Skill 集合仓库（Monorepo）

一个**不断新增的多 Skill 仓库**。所有面向「医美设计行业」的 AI 能力都放在 `skills/` 目录下，每个 Skill 自包含（各有自己的 `SKILL.md` + `scripts/` + `references/`），互不依赖。

当前包含的 Skill：

| Skill | 能力 | 目录 |
|---|---|---|
| **audio-to-text** | 本地音频批量转文字稿（上传OSS → 百炼语音识别 → 输出.md，断点续传） | `skills/audio-to-text/` |

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

安装后，直接对支持 Agent Skills 的客户端说：

- 转写音频：`使用 $audio-to-text 完成它所解决的任务，并按可观察标准检查结果。`

每个 Skill 的详细用法见其所在目录的 `SKILL.md`。

## 发布规范

- **凭证安全**：所有 API Key / AccessKey 一律走环境变量，绝不写进代码或仓库。
- **隐私边界**：仓库只发布运行所需文件；本地样本、密钥、运行记录、私密内容绝不入库。
- **只发布自包含 Skill**：每个 Skill 文件夹独立可用，不依赖仓库其他部分。

## 与知识库衔接

转写出的文字稿可交给 `/dbs-knowledge` 搭建按主题归档的知识库，形成「音频 → 文字稿 → 知识库 → 各 Skill 取用」闭环。
