# 互动爽文小说生成器

## 前置条件

1. 已运行 `scripts/start.sh` 完成依赖安装（`.venv` 目录存在）
2. 配置 Anthropic API Key（选其中一种）：

**方式一：写入 `.env` 文件（推荐，一次配置永久生效）**

在项目根目录创建 `.env` 文件（如已存在则追加）：

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-xxxx' >> .env
```

**方式二：每次运行前设置环境变量**

```bash
export ANTHROPIC_API_KEY=sk-ant-xxxx
```

**方式三：运行时传参**

```bash
./scripts/novel.sh test --api-key sk-ant-xxxx
```

---

## 快速开始

### 交互模式（推荐初次使用）

```bash
./scripts/novel.sh
```

程序会逐步提问：标题、类型、章节数、故事思路，确认后自动开始。

---

## 常用命令

### 生成小说（传参模式）

```bash
./scripts/novel.sh generate \
  --title "废柴逆天" \
  --chapters 100 \
  --genre 修仙升级 \
  --premise "被宗门废除资格的少年，意外觉醒最强传承，从无人问津的废柴开始逆袭之路" \
  --protagonist "林枫，19岁，表面低调，内心够狠，记仇不莽，嘴毒护短"
```

### 从断点继续（中断后恢复）

```bash
./scripts/novel.sh generate --resume
```

或指定输出目录：

```bash
./scripts/novel.sh generate --resume --output ./output
```

### 启用硬分支路线

```bash
./scripts/novel.sh generate \
  --title "我在修仙界装废物" \
  --chapters 200 \
  --genre 修仙升级 \
  --premise "..." \
  --branches \
  --branch-count 2
```

### 10章快速测试（验证 API 和流程）

```bash
./scripts/novel.sh test
```

---

## 全部参数说明

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--title` / `-t` | 小说标题 | 交互输入 |
| `--chapters` / `-c` | 总章节数（10-2000） | 交互输入 |
| `--genre` / `-g` | 类型：`修仙升级` `都市逆袭` `悬疑生存` `职场商战` `末日爽文` | 交互选择 |
| `--premise` / `-p` | 故事思路/前提 | 交互输入 |
| `--protagonist` | 主角描述（可选） | 空 |
| `--tone` | 写作基调 | `爽快、热血、有悬念` |
| `--output` / `-o` | 输出根目录 | `./output` |
| `--resume` / `-r` | 从断点继续上次中断的生成 | 否 |
| `--branches` | 启用硬分支路线 | 否 |
| `--branch-count` | 分支路线数量 | `2` |
| `--free-chapters` | 免费章节数 | `20` |
| `--api-key` | Anthropic API Key（也可用环境变量） | 环境变量 |
| `--planner-model` | 规划模型 ID（覆盖配置文件） | 配置文件默认 |
| `--writer-model` | 写作模型 ID（覆盖配置文件） | 配置文件默认 |

---

## 生成进度

运行时会显示实时进度面板：

```
┌─ 生成进度 ────────────────────────────────────────────┐
│  生成章节内容  (342s)                                  │
│  章节进度 ██████████░░░░░░░░░░  60/100  0:05:42       │
│  ✓ 幕结构规划完成（5 幕）                              │
│  ✓ 弧线 2/4（第51-100章）                             │
│  ✓ 第  58 章                                          │
│  ✓ 第  59 章                                          │
│  ✓ 第  60 章  ⚠1                                      │
└───────────────────────────────────────────────────────┘
```

生成阶段：

1. **故事圣经** — 设定世界观、角色、路线图
2. **幕结构规划** — 全书5幕宏观结构（爽点、转折点）
3. **弧线规划** — 每50章一个弧，含情感节奏、爽点密度
4. **章节生成** — 逐章写作，每弧完成后自动生成总结和世界快照
5. **分支章节**（可选）— 真硬分支，不同路线写不同内容
6. **攻略地图** — 生成章节导览
7. **编译分片** — 输出可直接供 App 使用的分片 JSON

---

## 输出产物

```
output/{slug}/if/
├── if_progress.json              ← 断点续传存档（生成中间产物）
├── story_package.json            ← 完整故事包（单文件）
└── build/
    ├── books.json                ← App 书目索引
    ├── book_{id}.json            ← 书籍元数据
    ├── walkthrough_{id}.json     ← 攻略地图
    ├── chapter_index_{id}.json   ← 章节路由表（含分支路线）
    └── chapters/
        ├── {id}_arc01_ch0001-ch0050.json   ← 第1弧章节
        ├── {id}_arc02_ch0051-ch0100.json   ← 第2弧章节
        └── ...
```

启用分支时额外输出：

```
output/{slug}/if/
└── branches/
    ├── branch_warrior/
    │   └── {id}_branch_warrior_ch0101-ch0130.json
    └── branch_schemer/
        └── {id}_branch_schemer_ch0101-ch0125.json
```

---

## 生成时间估算

| 章节数 | 预计时间 |
|---|---|
| 10章（测试） | 约 3-5 分钟 |
| 50章 | 约 15-25 分钟 |
| 100章 | 约 30-50 分钟 |
| 500章 | 约 2.5-4 小时 |
| 1000章 | 约 5-8 小时 |

实际时间取决于模型响应速度和网络状况。

---

## 中断与恢复

生成过程每完成一章都会自动保存进度到 `if_progress.json`。

中断后直接加 `--resume` 继续：

```bash
./scripts/novel.sh generate --resume --output ./output
```

程序会自动跳过已完成的章节，从中断处继续。

---

## 帮助

```bash
./scripts/novel.sh --help
./scripts/novel.sh generate --help
```
