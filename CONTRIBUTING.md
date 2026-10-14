# 如何新增一个 Skill

本仓库是多 Skill 集合仓库。新增 Skill **不需要改任何现有代码**，往 `skills/` 下加一个文件夹即可。

---

## 一、目录规范

```text
skills/<skill-name>/     # 目录名：小写英文 + 连字符，如 ma-risk-check
├── SKILL.md             # 必填
├── scripts/             # 可选：重复且确定的操作
├── references/          # 可选：按需读取的条件性知识
└── assets/              # 可选：模板/素材
```

**命名用连字符，不要用下划线。**（`auto-transcribe` ✓ / `auto_transcribe` ✗）

---

## 二、SKILL.md 的 frontmatter（必填五个字段）

```yaml
---
name: ma-risk-check          # 必须与目录名完全一致
ability: 合规文案审查         # 中文能力名，主入口按它路由
description: 一句话说明它做什么、什么情况下用。   # 这段决定 Agent 会不会选中它，写「用户会怎么说」
category: 内容合规            # 大类：入口 / 采集转写 / 内容合规 / 内容生产 / 聊客成交 / 美学设计 / 团队搭建
tier: free                   # free=免费 | core=299 | pro=3980 | vip=29800
---
```

**`ability` 是能力名和目录名之间的桥。**

- `ability`（中文能力名）是**公开契约**：写进 `susan` 的能力清单，用户看得懂，定了不要随便改。
- `name`（目录名）是**实现细节**：主入口靠 `scripts/list-skills.py` 从本地实际文件里读出来，
  将来重命名目录不会打断路由。

两者必须**同时维护**，漏了 `ability` 会导致主入口路由不到这个 Skill。

**description 怎么写**：不要写技术名词，写**用户真实会说的需求**。

```
✗ description: 基于规则引擎的医美广告合规检测模块
✓ description: 这段文案能不能发？扫描广告法违禁词和平台限流词，
               用户写完小红书/抖音文案担心违规或被限流时使用
```

---

## 三、SKILL.md 正文至少包含

1. **一句话定义**：解决什么、**不解决什么**（边界比能力更重要）
2. **使用前提**：需要什么凭证 / 依赖 / 材料
3. **关键行为与边界**：只读不删、凭证不硬编码、安全边界
4. **用法**：具体命令或调用方式
5. **完成条件**：怎么判断它做完了

---

## 四、新增步骤

### 1. 建目录

```bash
mkdir -p skills/<skill-name>/{scripts,references}
```

### 2. 写 SKILL.md

按上面第二节模板写。**注意：frontmatter 的分隔符是 `---`，不要写成 `\---`**（会导致 Skill 无法被解析）。

### 3. 本机实测

```bash
cd skills/<skill-name>
bash run.sh --help        # 或按 SKILL.md 里写的用法跑一遍
```

必须验证：**成功路径 + 至少一个失败路径**（比如缺凭证、文件不存在）。

### 4. 注册到主入口（改三处，缺一不可）

1. **能力清单加一行**（`skills/susan/SKILL.md`）：只写中文能力名，不要写目录名

   | 用户可能会这么说 | 能力名 | 档位 |
   |---|---|---|
   | 这段文案能不能发 | 合规文案审查 | 免费 |

2. **确认 frontmatter 的 `ability`** 和上面表格里的能力名**一字不差**

3. **把调用名加进 `skills/susan/references/official-skills.txt`**（漏写 `ability` 时的兜底白名单）

> 主入口的路由表不写目录名。目录名由 `list-skills.py` 从本地实际文件里读出来，
> 这样重命名目录不会打断路由。

### 5. 提交

只暂存明确文件，**不要用 `git add .` / `git add -A`**：

```bash
git add skills/<skill-name> skills/susan/SKILL.md
git commit -m "feat: 新增 <skill-name>"
git push
```

---

## 五、提交前检查清单

- [ ] 目录名 = `SKILL.md` 的 `name`，且用连字符
- [ ] frontmatter 五个字段齐全（含 `ability`），分隔符是 `---`
- [ ] description 写的是用户口语，不是技术名词
- [ ] `ability` 与 `susan` 能力清单里的名字**一字不差**
- [ ] 调用名已加进 `skills/susan/references/official-skills.txt`
- [ ] 已在 `skills/susan/SKILL.md` 能力清单注册
- [ ] `category` 与 `tier` 填写正确
- [ ] 本机实测通过（成功 + 失败路径）
- [ ] `python3 skills/susan/scripts/list-skills.py` 能扫到它
- [ ] **没有**本机绝对路径（应写成相对路径或环境变量）
- [ ] **没有** API Key / 密钥
- [ ] **没有**真实客户内容（转写稿、姓名、联系方式、案例图）

---

## 六、隐私红线（违反即泄露，无法撤回）

公开仓库里的东西**删了也会留在 git 历史里**。以下三类绝对不能提交：

1. **真实客户内容**：录音转写稿、面诊对话、姓名、联系方式、术前术后图
   → 需要示例时用**脱敏结构样例**（见 `skills/tingwu-export/samples/`）
2. **凭证**：API Key、AccessKey、Cookie、Token
   → 一律走 `config.env`（已 gitignore）+ `config.env.example`（提交这个）
3. **个人路径**：`/Users/xxx/...` 这类绝对路径
   → 写成相对路径或 `os.environ.get("XXX", "./默认值")`

提交前自查：

```bash
grep -rn "/Users/" --include="*.py" --include="*.sh" --include="*.md" skills/ | grep -v "/state/" | grep -v "/logs/"
# 应该没有输出
```
