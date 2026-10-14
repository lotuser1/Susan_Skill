---
name: tingwu-export
description: 把通义听悟账户上已有的录音转写稿批量下载成本地 markdown（含说话人分离与时间戳），支持多账户串行、断点续跑、防风控限速；下载后先落「00 待分类」，再由 LLM 按医美七大板块归类。适用于已用 auto-transcribe 上传录音到通义听悟转写完成、需要把云端文稿取回本地建知识库的场景。
category: 采集转写
tier: free
---

# tingwu-export：通义听悟转写稿导出

## 一句话定义

**把通义听悟账户上已经转写好的文字稿，批量下载成本地 markdown，落进你的知识库目录。**

它解决的是：
- 录音已经在通义听悟转写完成了，但**文稿还留在云端**，本地拿不到
- 听悟网页端只能手动一条条导出，几千条根本点不过来
- 多个账户（比如 5 个）的转写稿要汇总到同一个知识库

它不处理的是：
- **上传录音去转写**——那是 `auto-transcribe` 的活（本 skill 只做「下载已有文稿」）
- 下载音频本身——只取文字稿，不下载音频文件
- 翻译、总结、润色——只负责原样取回转写全文

## 技术原理

```
已登录的 Chrome（CDP 9222）
   ↓ 调听悟内部接口（复用已登录会话，不需要 API Key）
   ├─ getTransList     → 拿到全部任务的 transId / status / wordCount
   └─ getTransResult   → 逐条取回全文（result 是 JSON 字符串）
   ↓ 解析段落 + 说话人 + 时间戳
   ↓ 渲染成 .md（带 YAML front-matter）
   ↓ 落到 <知识库>/00 待分类/<账户>/
```

**为什么必须走浏览器**：通义听悟没有公开的导出 API。本 skill 通过 CDP 驱动你本机**已登录**的 Chrome，复用你的登录态直接调网页端自己在用的内部接口。因此**必须先用 `start_chrome.sh` 启动一个带调试端口的 Chrome 并扫码登录**。

## 使用前提

1. 本机安装了 Chrome
2. 你已有通义听悟账号，且该账号里**已经有转写完成的文稿**
3. 能扫码登录（阿里云APP / 支付宝 / 钉钉）

## 首次使用流程

### 第 0 步：启动并登录 Chrome

```bash
bash start_chrome.sh
```

在弹出的 Chrome 窗口里扫码登录。**保持窗口打开**。

### 第 1 步：探测接口（首次必做，约 10 分钟）

```bash
bash run.sh --probe
```

会自动确认：登录态、`transId` 字段、取全文接口是否可用、`result` 真实结构、status 真实枚举，并把 3 条原始样本落到 `samples/`。

**只有探测通过，才继续往下走**——后面所有解析都基于探测结果。

### 第 2 步：离线校验解析器

```bash
bash run.sh --parse-samples
```

用本地样本校验解析器。**硬标准：解析出的字数 ≈ 接口给的 wordCount（覆盖率 ≥95%）**。
不达标说明长稿有分页，必须先解决分页，否则会下载到残缺文稿。

### 第 3 步：小批量试跑

```bash
bash run.sh --account acc1 --status          # 先看任务总数和状态分布
bash run.sh --account acc1 --limit 5 --apply # 试跑 5 条
bash run.sh --account acc1 --limit 5 --apply # 再跑一次，应全部跳过（验证幂等）
```

检查输出目录 `<知识库>/00 待分类/acc1/` 里的 .md：文件名、front-matter、说话人标注是否正确。

### 第 4 步：全量下载（多账户串行）

```bash
bash run.sh --account acc1 --apply   # 下载账户 1
bash run.sh --logout                 # 清登录态（不关 Chrome！）
#   → 在 Chrome 里扫码登录账户 2
bash run.sh --account acc2 --apply
bash run.sh --logout
#   → ……重复到第 5 个账户
bash run.sh --finish                 # 最后一个账户跑完才关 Chrome
```

> ⚠️ **前 4 轮千万不要关 Chrome**。`--logout` 只清 cookie，Chrome 会停在扫码页等你登下一个账户。只有最后一轮用 `--finish` 关闭。

### 第 5 步：归类到七大板块

```bash
bash run.sh --classify     # 扫描 00 待分类 → _index/pending.json
#   → Agent 读 pending.json，按 references/分类标准.md 判定，写 _index/plan.json
bash run.sh --arch --render   # 生成待确认清单 _index/plan_review.md
#   → 你确认清单无误
bash run.sh --arch --apply    # 执行移动（可 --undo 回滚）
```

### 第 6 步：建知识库

对知识库根目录调用 `/dbs-knowledge`，生成 `SOURCE_OF_TRUTH.md` 与各目录 `README.md`。

## 输出格式

```markdown
---
title: "李姐面诊"
source: 通义听悟
trans_id: 4l6xqal2bdkanm2y
account: acc1
account_name: "张某某"
original_name: "李姐面诊.m4a"
show_time: "2026-08-08 14:23"
duration_sec: 3621
word_count: 8421
speakers: ["0", "1"]
ingest_at: "2026-10-12 17:00:03"
status: success
---

# 李姐面诊

> 来源：通义听悟 · acc1 · 转写 ID `4l6xqal2` · 时长 60:21 · 8421 字 · 2 位发言人

## 全文转写

【说话人0】[00:00:00] 你先坐，我看一下你之前打过的地方……
【说话人1】[00:00:12] 好的，我上次是在外面机构打的玻尿酸。
```

文件名：`<标题>__<transId前8位>.md`。`transId` 保证跨账户全局唯一，绝不重名。

## 关键行为与边界

- **幂等断点续跑**：`state/export_state.json` 按账户记录 done/failed/skipped，三重判定跳过。中断后重跑自动跳过已下载的，绝不重复写文件。
- **防风控**：每条间隔 1.2–2.6 秒随机 + 每 40 条长休 20 秒 + 单条最多重试 3 次 + 连续 3 条失败熔断。
- **会话失效立即停**：遇到 `CMN.NotLogin` / 401 / 403 立即保存进度并提示重新扫码，绝不空转。
- **只取文字**：不下载音频，请求量小，行为接近正常使用。
- **凭证不硬编码**：不需要任何 API Key，直接复用已登录的 Chrome 会话。
- **文件命名安全化**：统一 NFC 归一化（macOS 中文文件名坑），非法字符替换为 `_`。
- **归类永不覆盖**：目标目录有同名文件自动追加 `__2`，每次移动写 `move_log` 支持 `--undo` 一键回滚；置信度 <0.6 强制留在 `00 待分类`。

## 降级链（接口失效时逐级降）

| 级别 | 做法 | 触发条件 |
|---|---|---|
| **L0**（默认） | 调内部接口 `getTransResult` 取全文 | — |
| **L1** | CDP Network 抓包：导航到 `detail?transId=<id>`，抓网页自己发出的 `/api/` 响应 | L0 返回结构无法解析 |
| **L2** | 模拟点「导出 Word」，用 `Browser.setDownloadBehavior` 接管下载 | L1 也无果 |
| **L3** | 用户在网页端手动批量导出，用 `import_manual.py` 解包落库 | L2 太慢或被限制 |

## 合规声明

本 skill 仅供导出**你自己拥有权利的内容**（你本人上传、本人账号下的转写稿）。下载的是你自己的数据，不涉及第三方内容。请勿用于导出他人账号或受版权保护的内容。

## 环境变量（config.env）

| 变量 | 说明 | 默认 |
|---|---|---|
| `KB_ROOT` | 知识库根目录（转写稿导出到哪） | `./知识库` |
| `TINGWU_CHROME_PORT` | Chrome 调试端口 | 9222 |
| `TW_PAGE_SIZE` | 列表每页条数 | 200 |
| `TW_MAX_ATTEMPTS` | 单条最多尝试次数 | 3 |

## 语言

用户用中文就用中文回复，用英文就用英文回复。
