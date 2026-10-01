# 如何新增一个 Skill（Monorepo）

本仓库是**多 Skill 集合仓库**。新增一个 Skill 不需要改任何现有代码，只要按下面的步骤往 `skills/` 下加一个文件夹。

## 目录结构：每个 Skill 自包含

```text
skills/
└── your-skill-name/               # 小写英文、数字、连字符
    ├── SKILL.md                   # 必填：能力说明 + 使用条件 + 关键流程
    ├── agents/openai.yaml        # 可选：UI 元数据
    ├── scripts/                  # 可选：重复且确定的操作脚本
    ├── references/               # 可选：按需读取的条件性知识
    └── assets/                   # 可选：进入交付的模板/素材
```

## 新增步骤

### 1. 用脚手架生成（推荐）

```bash
python3 /home/user/.agents/skills/dbs-skill-maker/scripts/init_skill_project.py \
  your-skill-name \
  --output /home/user/Doubao/chats/38445015441618946/audio-transcribe-github/skills \
  --description "一句话说明：这个skill做什么、在什么情况下用" \
  --task "它反复解决的问题" \
  --workflow "关键工作动作1" \
  --workflow "关键工作动作2" \
  --done "可观察的完成证据1"
```

### 2. 补全 SKILL.md

把脚手架生成的初稿补成完整文档，至少包含：

- **一句话定义**：这个 Skill 解决什么、不解决什么
- **使用前提**：需要什么凭证/依赖/材料
- **关键行为与边界**：只读不删、凭证不硬编码、安全边界
- **用法**：具体命令或调用方式
- **完成条件**：怎么判断它做完了

### 3. 校验

```bash
python3 /home/user/.agents/skills/dbs-skill-maker/scripts/validate_skill_project.py \
  /home/user/Doubao/chats/38445015441618946/audio-transcribe-github/skills/your-skill-name
```

### 4. 确认无误后提交

只暂存明确文件，不要用 `git add .` / `git add -A`：

```bash
git add skills/your-skill-name
git commit -m "feat: 新增 your-skill-name 技能"
```

## 关键规则

1. **自包含**：每个 Skill 不依赖仓库其他部分，拿到该文件夹就能独立使用。
2. **凭证不写死**：所有 Key 走环境变量，绝不入库。
3. **隐私边界**：本地样本、密钥、运行记录、私密内容绝不提交。
4. **每个 Skill 都要能通过结构校验**才提交。
5. **一条命令装全部**：新增 Skill 后，别人的安装命令 `npx -y skills add lotuser1/songzi -g --all` 无需改动，自动包含新 Skill。

## 检查清单（提交前）

- [ ] `skills/<name>/SKILL.md` 存在且 description 能区分近邻任务
- [ ] 目录名与 SKILL.md 的 `name` 一致
- [ ] scripts 已运行验证（成功路径 + 至少一个失败路径）
- [ ] 没有未完成占位符 / 本机绝对路径 / 密钥
- [ ] 通过了 `validate_skill_project.py`（0 提醒）
