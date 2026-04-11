# BestSeller

**面向长篇小说生产的分布式人机共创框架**

BestSeller 把长篇创作拆成可持续迭代的工业级生产流水线——不是"一次性大 prompt 写书"，而是一套有规划、有知识、有审校、有记忆的自动化叙事系统。

```
Premise → Plan → Draft → Review → Rewrite → Knowledge Propagation → Export → Publish
```

---

## 目录

- [设计哲学](#设计哲学)
- [架构总览](#架构总览)
- [核心子系统](#核心子系统)
  - [1. 多级流水线](#1-多级流水线pipeline-architecture)
  - [2. 叙事知识系统](#2-叙事知识系统narrative-knowledge-system)
  - [3. 上下文装配引擎](#3-上下文装配引擎context-assembly-engine)
  - [4. 质量门控体系](#4-质量门控体系quality-gate-system)
  - [5. LLM 网关层](#5-llm-网关层llm-gateway)
  - [6. 分阶段世界扩张](#6-分阶段世界扩张staged-world-expansion)
  - [7. 检索增强生成](#7-检索增强生成rag-for-narrative)
- [系统架构](#系统架构)
  - [服务拓扑](#服务拓扑)
  - [数据库设计](#数据库设计)
  - [配置体系](#配置体系)
- [接入方式](#接入方式)
  - [Web Studio](#web-studio)
  - [REST API](#rest-api)
  - [MCP Server](#mcp-server)
  - [CLI](#cli)
- [快速开始](#快速开始)
- [写作配置系统](#写作配置系统)
  - [Prompt Pack](#prompt-pack)
  - [写作预设目录](#写作预设目录)
  - [写作画像](#写作画像)
- [多格式导出](#多格式导出)
- [发布调度](#发布调度)
- [评测体系](#评测体系)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [文档索引](#文档索引)

---

## 设计哲学

长篇小说（10 万 ~ 百万字）的核心难点不是"让 LLM 写出一段好文字"，而是**如何在数十万字的跨度里维持叙事一致性、角色弧光连贯性和世界观自洽性**。

BestSeller 的设计围绕三个核心信念：

### 1. 流水线 > 一次性推理

单次 prompt 无法可靠生成长篇。系统把创作拆成 `规划 → 草稿 → 审校 → 重写 → 知识沉淀` 的多级流水线，每个环节有独立的 LLM 角色、独立的质量阈值、独立的重试策略。一个场景写砸了，只需要重跑这个场景，不需要从头来过。

### 2. 知识先于生成

写作不是凭空创作，而是"在已建立的知识库上做受约束的续写"。系统在每个场景写完后，自动提取 `Canon Facts`（角色状态、关系变化、剧情事实）、`Timeline Events`（时间线事件）和 `Character State Snapshots`（角色状态快照），构成一个不断增长的叙事知识库。下一个场景的 writer 看到的不是"前面写了什么"，而是"当前世界的事实状态"。

### 3. 约束即质量

好的长篇不是"想写什么就写什么"，而是"在严格的叙事合约下交付"。每个章节有 `Chapter Contract`（主线推进目标、伏笔兑现任务、情绪弧要求），每个场景有 `Scene Contract`（具体要完成的叙事动作）。审校阶段会逐条对照合约检查偏差，而不是泛泛地说"写得不好"。

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        接入层 Access Layer                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │ Web Studio│  │ REST API │  │ MCP Server│  │   CLI (Typer)│    │
│  │  :8787   │  │  :8000   │  │  :3000   │  │              │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
│       │              │              │               │            │
├───────┴──────────────┴──────────────┴───────────────┴────────────┤
│                      服务层 Service Layer                         │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                    Pipeline Orchestrator                 │     │
│  │  autowrite · project · chapter · scene                  │     │
│  └──────┬────────────┬────────────┬───────────────┬────────┘     │
│         │            │            │               │              │
│  ┌──────┴──┐  ┌──────┴──┐  ┌─────┴────┐  ┌──────┴──────┐       │
│  │Conception│  │ Planner │  │  Drafts  │  │  Reviews    │       │
│  │ & Bible │  │& Outline│  │& Assembly│  │& Rewrite    │       │
│  └─────────┘  └─────────┘  └──────────┘  └─────────────┘       │
│         │            │            │               │              │
│  ┌──────┴────────────┴────────────┴───────────────┴──────┐       │
│  │               Knowledge & Narrative Layer             │       │
│  │  Canon · Timeline · Continuity · Narrative Graph      │       │
│  │  Retrieval · Context Assembly · World Expansion       │       │
│  └───────────────────────┬───────────────────────────────┘       │
│                          │                                       │
├──────────────────────────┴───────────────────────────────────────┤
│                     基础设施层 Infrastructure                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐     │
│  │PostgreSQL│  │  Redis   │  │  LiteLLM │  │  Embedding   │     │
│  │+ pgvector│  │  + ARQ   │  │  Gateway │  │  BAAI/bge-m3 │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘     │
└──────────────────────────────────────────────────────────────────┘
```

---

## 核心子系统

### 1. 多级流水线（Pipeline Architecture）

系统采用三级流水线架构，每一级都有独立的工作流跟踪、进度回调和故障恢复：

```
Project Pipeline
 ├── Chapter Pipeline (×N)
 │    ├── Scene Pipeline (×M)
 │    │    ├── Context Assembly      ← 装配写作上下文
 │    │    ├── Draft Generation      ← LLM (writer) 生成草稿
 │    │    ├── Knowledge Propagation ← 自动提取事实、时间线、角色状态
 │    │    ├── Review & Scoring      ← LLM (critic) 审校打分
 │    │    └── Rewrite (if needed)   ← LLM (editor) 按审校意见重写
 │    ├── Chapter Assembly           ← 合并场景为完整章节
 │    ├── Chapter Review             ← 章节级审校（主线/副线/hook）
 │    └── Chapter Export             ← Markdown checkpoint
 ├── Consistency Check               ← 项目级一致性评审
 ├── Auto Repair (if needed)         ← 自动重跑未通过章节
 └── Full Export                     ← Markdown / DOCX / EPUB / PDF
```

**设计精妙之处：**

- **Checkpoint Commit**：每个场景写完后立即持久化到数据库，而不是在整章完成后才提交。这避免了 PostgreSQL 长事务导致的快照膨胀，也意味着中途失败只需重跑失败的场景。
- **Progress Callback**：流水线的每一步都通过 `ProgressCallback` 发射事件，Web UI 和 SSE 客户端可以实时看到 `"正在写第 3 章第 2 场景"` 这样的进度。
- **幂等恢复**：`project repair` 会分析哪些章节/场景处于 `needs_rewrite` 或 `failed` 状态，只重跑这些，不影响已完成的内容。

### 2. 叙事知识系统（Narrative Knowledge System）

这是 BestSeller 区别于"简单 prompt 链"的核心设计。系统维护一个不断增长的叙事知识图谱：

```
                    Story Bible
                   ┌───────────┐
                   │ BookSpec   │  ← 全书主旨、读者承诺、卖点
                   │ WorldSpec  │  ← 世界观、势力、地理
                   │ CastSpec   │  ← 人物谱、关系网
                   │ VolumePlan │  ← 分卷结构
                   └─────┬─────┘
                         │
              Narrative Graph (显式叙事结构)
          ┌──────────────┼──────────────┐
    ┌─────┴─────┐  ┌─────┴─────┐  ┌────┴────┐
    │  PlotArc  │  │   Clue    │  │ Emotion │
    │  ArcBeat  │  │  Payoff   │  │  Track  │
    └───────────┘  └───────────┘  └─────────┘
          │              │              │
    ┌─────┴──────────────┴──────────────┴────┐
    │     Chapter Contract / Scene Contract   │  ← 每章/场景的叙事合约
    └────────────────────────────────────────┘
                         │
            Knowledge Layer (动态积累)
          ┌──────────────┼──────────────┐
    ┌─────┴─────┐  ┌─────┴─────┐  ┌────┴─────────┐
    │Canon Facts│  │ Timeline  │  │  Character   │
    │(角色事实) │  │  Events   │  │State Snapshot│
    └───────────┘  └───────────┘  └──────────────┘
```

**Canon Facts** 不是简单的"前情提要"，而是结构化的事实数据库：
- 角色当前位置、情绪状态、已知信息、持有物品
- 角色间关系的最新状态
- 已兑现的伏笔和仍未揭示的暗线
- 世界规则的执行状态

**每个场景写完后**，系统自动执行 `propagate_scene_discoveries`：
1. 从草稿文本中提取新的 Canon Facts
2. 更新 Timeline Events
3. 刷新 Character State Snapshots
4. 重建 Retrieval Chunks（用于后续检索）

这意味着第 20 章的 writer 看到的角色状态，是系统从前 19 章中自动累积出来的真实状态，而不是靠 prompt 里放一段"前情提要"。

### 3. 上下文装配引擎（Context Assembly Engine）

长篇写作的上下文窗口永远不够用。系统通过 `SceneWriterContextPacket` 精确控制 writer 能看到什么：

```
SceneWriterContextPacket
├── story_bible_excerpt       ← 故事圣经核心摘要
├── world_rules (filtered)    ← 当前卷允许可见的世界规则
├── chapter_contract          ← 本章叙事合约
├── scene_contract            ← 本场景叙事合约
├── active_arcs               ← 当前活跃的情节线
├── active_beats              ← 当前需要推进的节拍
├── pending_clues             ← 已埋未兑现的伏笔
├── scheduled_payoffs         ← 本场景应兑现的伏笔
├── emotion_tracks            ← 情绪线走向
├── antagonist_plans          ← 反派当前阶段的行动
├── participant_facts         ← 本场景出场角色的 Canon Facts
├── recent_scene_summaries    ← 最近 N 个场景的摘要
├── character_state_snapshot  ← 上一章结束时的角色状态
├── timeline_window           ← 近期时间线事件
└── retrieval_results         ← 混合检索命中
```

**装配策略的精妙之处：**

- **三层检索**：`path retrieval → tree search → hybrid retrieval`。先按叙事树路径精确定位，再按树结构语义搜索，最后用向量+词法+结构三路混合检索补充。
- **Token 预算制**：总上下文预算固定（默认 8000 tokens），各部分按优先级竞争预算，低优先级内容被裁剪而不是被丢弃。
- **可见性过滤**：World Expansion 系统的 `VolumeFrontier` 和 `DeferredReveal` 确保 writer 只能看到当前阶段允许展开的世界信息，避免在第 3 章就泄露第 20 章才该揭示的秘密。

### 4. 质量门控体系（Quality Gate System）

每个层级都有独立的质量门控，不是一个笼统的"好/坏"判断，而是结构化的多维评分：

**场景级评分维度：**
- `hook_strength` — 开场吸引力
- `conflict_clarity` — 冲突是否清晰
- `emotional_movement` — 情感推进力度
- `payoff_density` — 伏笔兑现密度
- `voice_consistency` — 叙述声音一致性

**章节级评分维度：**
- `main_plot_progression` — 主线推进度
- `subplot_progression` — 副线推进度
- `ending_hook_effectiveness` — 章末钩子强度
- `volume_mission_alignment` — 与卷目标的对齐度

**项目级一致性评审：**
- 章节覆盖率、知识层完整度、Canon/Timeline 完整度
- 主线推进、暗线埋设/兑现、情绪线连续性
- 角色弧光台阶、世界规则落地、反派推进压力

**重写级联**（Rewrite Cascade）：当一个场景被判定需要重写时，系统自动分析受影响的下游 Canon Facts、Scenes 和 Chapters，可以自动级联重跑受影响的内容。

### 5. LLM 网关层（LLM Gateway）

系统通过 LiteLLM 统一接入多个模型提供商，并按**角色**分配不同的模型和参数：

| 角色 | 职责 | 默认模型 | 温度 | 说明 |
|------|------|---------|------|------|
| `planner` | 规划生成 | Claude Opus | 0.65 | 高推理，多候选 |
| `writer` | 正文写作 | Claude Sonnet | 0.85 | 高创意，流式输出 |
| `critic` | 审校打分 | Claude Haiku | 0.25 | 低温度，精确评判 |
| `summarizer` | 摘要压缩 | Claude Haiku | 0.20 | 低温度，信息保持 |
| `editor` | 重写润色 | Claude Sonnet | 0.40 | 中温度，保持风格 |

**韧性设计：**
- **熔断器**（Circuit Breaker）：连续 5 次失败后熔断 60 秒，防止级联故障
- **指数退避重试**：对 RateLimitError / APITimeout / ServiceUnavailable 自动重试
- **HTTP 连接池复用**：per-event-loop 的 httpx.AsyncClient 池，减少 TLS 握手开销
- **审计日志**：每次 LLM 调用写入 `llm_runs` 表，记录模型、角色、token 数、延迟、成本
- **Mock 模式**：无外部依赖时自动降级为确定性输出，用于开发和测试

### 6. 分阶段世界扩张（Staged World Expansion）

长篇小说的世界观不应该在第一章就全部展开。系统实现了精细的信息释放控制：

```
WorldBackbone         ← 贯穿全书的世界主干（不可漂移）
 ├── VolumeFrontier   ← 当前卷允许展开的世界边界
 ├── DeferredReveal   ← 未来才允许揭示的暗线/真相
 └── ExpansionGate    ← 世界扩张闸门（随章节推进同步）
```

Writer 在写第 5 章时，只能看到 `Volume 1 Frontier` 允许的世界规则、地点和势力，看不到 Volume 3 才该展开的内容。这确保了：
- 信息释放的节奏感
- 暗线的可控性
- 世界观的逐步展开而非一次性倾倒

### 7. 检索增强生成（RAG for Narrative）

系统使用 PostgreSQL + pgvector 实现了专为叙事场景优化的混合检索：

```
查询 → ┌── Vector Search (pgvector, BAAI/bge-m3)    权重 60%
       ├── Lexical Search (pg_trgm, trigram)         权重 20%
       └── Structural Search (叙事树路径匹配)          权重 20%
       ───────────────────────────────────────────────
       → Ranked Results → Token Budget Trimming → Context Packet
```

**结构化检索**的独特之处在于：它不仅做语义相似度匹配，还理解叙事结构。查询 `/chapters/003/contract` 会精确返回第三章的叙事合约，而不是语义上相似但不相关的内容。

---

## 系统架构

### 服务拓扑

```yaml
services:
  api:        FastAPI REST API           (:8000)   # 1 worker
  worker:     ARQ async job worker       (×2)      # 异步任务执行
  scheduler:  APScheduler cron runner              # 定时发布调度
  mcp:        FastMCP HTTP server        (:3000)   # MCP 协议接入
  web:        Embedded Web UI            (:8787)   # 浏览器交互
  db:         PostgreSQL 16 + pgvector             # 唯一数据源
  redis:      Redis 7                              # 队列 + 进度流 + 缓存
```

**任务处理架构：**
- **ARQ**（Async Redis Queue）负责长时任务（autowrite 整书可能运行数小时）
- **APScheduler** 负责定时发布（cron 表达式 + 时区）
- Redis Pub/Sub 实现跨服务的实时进度推送
- SSE（Server-Sent Events）将进度流推到浏览器

### 数据库设计

PostgreSQL 是**唯一数据源**（Single Source of Truth），50+ 个表覆盖：

| 领域 | 核心模型 |
|------|---------|
| **项目结构** | Project → Volume → Chapter → SceneCard |
| **规划产物** | PlanningArtifactVersion（带版本号和审批状态） |
| **世界构建** | WorldRule, Location, Character, Relationship |
| **叙事图谱** | PlotArc, ArcBeat, Clue, Payoff, Contract |
| **内容草稿** | SceneDraftVersion, ChapterDraftVersion |
| **知识层** | CanonFact, TimelineEvent, CharacterStateSnapshot |
| **检索索引** | RetrievalChunk（pgvector embedding） |
| **工作流** | WorkflowRun → WorkflowStepRun |
| **审计** | LlmRun（每次 LLM 调用的完整记录） |
| **发布** | PublishingPlatform, Schedule, PublishedChapter |
| **互动小说** | IFStoryBible, IFArcPlan, IFBranch, IFWalkthrough |

**关键设计模式：**
- **版本化产物**：规划和草稿都有 `version_no`，保留完整的修改历史
- **乐观锁**：项目级 `lock_version` 防止并发冲突
- **JSONB 灵活存储**：配置、上下文、元数据用 JSONB 存储，兼顾结构和灵活性
- **向量索引**：pgvector HNSW 索引用于语义检索
- **Alembic 迁移**：15 个版本的 schema 迁移，支持在线升级

### 配置体系

四层配置覆盖，从通用到特殊：

```
config/default.yaml          ← 基础默认值
config/local.yaml            ← 本地覆盖（不入库）
.env / .env.local            ← 环境变量
BESTSELLER__*                ← 运行时环境变量
```

所有配置由 Pydantic Settings 类验证，类型安全且有默认值。核心配置维度：

- **LLM**：每个角色的模型、温度、token 上限、超时、重试策略
- **Generation**：目标字数、章节数、每章字数范围、场景数范围
- **Quality**：各维度评分阈值、最大重写轮数、重复检测参数
- **Retrieval**：嵌入模型、维度、混合权重、候选数量
- **Pipeline**：一致性检查间隔、知识压缩间隔、反馈阈值

---

## 接入方式

### Web Studio

内置的浏览器交互界面，直接操作完整的写作流水线：

```bash
./start.sh && ./studio.sh
# 或
make ui
```

支持的操作：
- 创建项目、输入 premise、配置写作画像
- 一键触发 `autowrite` 整书生成
- 实时查看执行阶段和进度
- 浏览项目结构、故事圣经、叙事图谱、流程跟踪
- 在线阅读 Markdown 成品（含字数统计、段落数、预计阅读时长）
- 触发 `project repair` 修复未通过章节

> 页面调用的是真实系统流水线，不是演示逻辑。

### REST API

FastAPI 驱动的 REST API，Bearer Token 认证：

```
POST /api/v1/projects                              创建项目
POST /api/v1/projects/{slug}/autowrite             一键整书生成
GET  /api/v1/projects/{slug}/structure             项目结构
GET  /api/v1/projects/{slug}/content               完整正文
POST /api/v1/projects/{slug}/export/{format}       导出
GET  /api/v1/tasks/{id}                            任务状态（SSE 流）
POST /api/v1/projects/{slug}/publishing/schedule   发布调度
```

### MCP Server

FastMCP HTTP 服务（端口 3000），将完整功能暴露为 MCP 工具，可被 Claude 等 AI 代理直接调用：

```bash
bestseller-mcp  # 启动 MCP 服务
```

20+ 工具覆盖：项目管理、流水线触发、内容检索、格式导出、发布调度、任务监控。

### CLI

Typer 驱动的命令行界面，100+ 命令覆盖所有功能：

```bash
bestseller project autowrite my-story "标题" sci-fi 80000 20 --premise "..."
bestseller project structure my-story
bestseller narrative show my-story
bestseller export epub my-story
```

---

## 快速开始

### 一键启动（本地开发）

```bash
git clone <repo> && cd bestseller
./start.sh                  # 创建 venv、拉起 PostgreSQL、执行迁移
./studio.sh                 # 打开 Web Studio
```

`start.sh` 会自动：
- 创建 `.venv` 并安装依赖
- 检测 LLM API Key，有则关闭 mock 模式
- 拉起本地 PostgreSQL + pgvector 容器
- 执行 `alembic upgrade head`
- 写入 `.runtime/dev.env`

### 第一本小说

```bash
# CLI 方式
./scripts/run.sh project autowrite demo "示例小说" sci-fi 22000 4 \
  --premise "一名被放逐的导航员发现帝国正在篡改边境航线记录，并被迫在追杀中揭穿真相。"

# 查看产物
ls output/demo/
cat output/demo/project.md
```

### Docker Compose（生产部署）

```bash
cp .env.example .env
# 编辑 .env 填入 LLM API Key
docker compose up -d
```

服务：`api(:8000)` · `worker(×2)` · `scheduler` · `mcp(:3000)` · `web(:8787)` · `db` · `redis`

### 模型配置

支持多个 LLM 提供商，通过 `.env` 配置：

```bash
# Anthropic（默认）
ANTHROPIC_API_KEY=sk-ant-...

# Google Gemini
BESTSELLER_LLM_PROVIDER=gemini
GEMINI_API_KEY=...

# OpenAI
OPENAI_API_KEY=sk-...

# 任意 OpenAI-compatible endpoint
BESTSELLER__LLM__WRITER__MODEL=openai/your-model
BESTSELLER__LLM__WRITER__API_BASE=https://your-endpoint/v1
```

---

## 写作配置系统

### Prompt Pack

题材专用的 prompt 模板包，**不需要改 Python 逻辑即可新增题材**。每个 pack 包含：

- 题材说明和反例（anti-patterns）
- 规划 / 写作 / 审校 / 重写专用片段
- `writing_profile_overrides`

内置 pack：

| Pack | 题材 |
|------|------|
| `apocalypse-supply-chain` | 末日囤货升级流 |
| `xianxia-upgrade-core` | 仙侠升级夺机缘 |
| `urban-power-reversal` | 都市异能反转流 |
| `romance-tension-growth` | 感情拉扯成长流 |

```bash
./scripts/run.sh prompt-pack list
./scripts/run.sh project autowrite my-story "标题" 末日科幻 220000 40 \
  --premise "..." --prompt-pack apocalypse-supply-chain
```

### 写作预设目录

一次性确定项目的平台定位、类型、篇幅和读者期望：

- **平台预设**：番茄小说 / 起点中文网 / 七猫小说 / 晋江文学城 等
- **题材预设**：末日囤货 / 仙侠升级 / 都市异能 / 悬疑追凶 / 无限流 等 17 类
- **篇幅预设**：4 章样书 ~ 超长连载阶段单元
- **热点推荐**：按市场热度排序

```bash
./scripts/run.sh writing-preset list
./scripts/run.sh writing-preset hot --limit 8
```

### 写作画像

完整的可执行写作配置，**贯穿规划到审校的所有环节**：

```
平台与内容定位     │ platform_target, content_mode, reader_promise, selling_points,
                  │ hook_keywords, pacing_profile, payoff_rhythm, update_strategy
──────────────────┤
主角与人物引擎     │ protagonist_archetype, golden_finger, growth_curve,
                  │ romance_mode, antagonist_mode, ensemble_mode
──────────────────┤
世界与信息释放     │ worldbuilding_density, info_reveal_strategy, rule_hardness,
                  │ power_system_style, mystery_density
──────────────────┤
文风与表达约束     │ pov_type, tense, tone_keywords, prose_style, dialogue_ratio,
                  │ taboo_topics, taboo_words, reference_works
──────────────────┤
连载节奏硬约束     │ opening_mandate, first_three_chapter_goal, scene_drive_rule,
                  │ chapter_ending_rule, free_chapter_strategy
```

---

## 多格式导出

```bash
bestseller export markdown my-story    # Markdown（默认）
bestseller export docx my-story        # Word 文档
bestseller export epub my-story        # 电子书
bestseller export pdf my-story         # PDF（需 pip install -e .[export]）
```

产物输出到 `output/<project-slug>/`：
- `project.md` / `chapter-001.md` / `chapter-002.md` ...
- `project.docx` / `project.epub` / `project.pdf`

---

## 发布调度

内置多平台定时发布系统：

- APScheduler + Cron 表达式 + 时区支持
- 平台适配器：番茄小说、起点中文网、七猫小说、Amazon KDP
- Redis Pub/Sub 热加载：修改调度后立即生效
- 发布历史记录和追踪

```bash
# REST API 创建发布计划
POST /api/v1/projects/{slug}/publishing/schedule
{
  "platform": "fanqie",
  "cron": "0 8 * * *",
  "timezone": "Asia/Shanghai"
}
```

---

## 评测体系

内置样书评测套件，用于验证生成质量的基线：

```bash
./scripts/run.sh benchmark list                                    # 列出可用 suite
./scripts/run.sh benchmark run sample-books --slug-prefix bench    # 运行全套
./scripts/run.sh benchmark run sample-books --case doomsday-hoarding --slug-prefix bench
```

每个 case 检查：
- autowrite 是否完成
- 产物是否生成
- 项目级评分是否达标
- 叙事线类型是否齐全
- 情绪线和反派推进是否存在

报告输出到 `output/benchmarks/`。

---

## 技术栈

| 层 | 技术 |
|----|------|
| **语言** | Python 3.11+ |
| **API** | FastAPI + Uvicorn + Starlette-SSE |
| **ORM** | SQLAlchemy 2.0 (async) + asyncpg |
| **数据库** | PostgreSQL 16 + pgvector + pg_trgm + pgcrypto |
| **队列** | ARQ (Async Redis Queue) |
| **调度** | APScheduler 3.x |
| **缓存/消息** | Redis 7 |
| **LLM 网关** | LiteLLM (Anthropic / OpenAI / Gemini / any compatible) |
| **嵌入** | sentence-transformers (BAAI/bge-m3, 1024 dims) |
| **导出** | python-docx, ebooklib, reportlab, markdown |
| **MCP** | FastMCP 2.0 |
| **CLI** | Typer + Rich |
| **配置** | Pydantic Settings + YAML + dotenv |
| **迁移** | Alembic |
| **容器** | Docker multi-stage + docker-compose |
| **测试** | pytest + coverage |
| **代码质量** | ruff + mypy + pre-commit |

---

## 项目结构

```
bestseller/
├── src/bestseller/
│   ├── api/                    # REST API 层
│   │   ├── app.py              #   FastAPI 应用工厂 + 生命周期
│   │   ├── deps.py             #   依赖注入（session, settings, redis, api_key）
│   │   ├── routers/            #   6 个路由模块
│   │   └── schemas/            #   Pydantic 请求/响应模型
│   ├── cli/                    # CLI 入口（Typer）
│   ├── domain/                 # 领域模型（20+ 文件）
│   │   ├── project.py          #   项目创建、市场定位、风格配置
│   │   ├── pipeline.py         #   流水线结果模型
│   │   ├── narrative.py        #   情节线、节拍、伏笔、兑现、合约
│   │   ├── context.py          #   写作上下文包
│   │   ├── knowledge.py        #   Canon Facts、时间线事件
│   │   └── ...                 #   评测、检查、反馈、重写等
│   ├── services/               # 业务逻辑（50 模块，核心层）
│   │   ├── pipelines.py        #   流水线编排
│   │   ├── conception.py       #   BookSpec/WorldSpec/CastSpec 生成
│   │   ├── planner.py          #   章节大纲生成
│   │   ├── drafts.py           #   场景/章节草稿生成
│   │   ├── reviews.py          #   审校与质量评分
│   │   ├── context.py          #   上下文装配引擎
│   │   ├── knowledge.py        #   知识传播与管理
│   │   ├── consistency.py      #   一致性检查
│   │   ├── contradiction.py    #   矛盾检测
│   │   ├── continuity.py       #   角色连续性
│   │   ├── narrative.py        #   叙事图谱操作
│   │   ├── retrieval.py        #   混合检索
│   │   ├── llm.py              #   LLM 网关（熔断器 + 审计）
│   │   ├── exports.py          #   多格式导出
│   │   ├── publishing/         #   发布平台适配器
│   │   └── ...                 #   世界扩张、写作预设、反 slop 等
│   ├── infra/                  # 基础设施
│   │   ├── db/                 #   SQLAlchemy 模型 + session + schema
│   │   └── redis.py            #   Redis 客户端
│   ├── worker/                 # ARQ 异步任务
│   ├── scheduler/              # APScheduler 发布调度
│   ├── mcp/                    # MCP 服务
│   └── web/                    # 内嵌 Web UI
├── config/                     # YAML 配置
├── migrations/                 # Alembic 迁移（15 版本）
├── tests/                      # 测试套件
├── scripts/                    # 启动/停止/验证脚本
├── docs/                       # 设计文档
├── examples/                   # 示例配置和规划产物
├── docker-compose.yml          # 多服务编排
├── Dockerfile                  # 多阶段构建
└── pyproject.toml              # 项目元数据和依赖
```

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [架构设计](docs/architecture.md) | 系统架构详解 |
| [数据库方案](docs/database-schema.md) | 完整数据库 schema |
| [Prompt 设计](docs/prompt-engineering-strategy.md) | 提示词工程策略 |
| [框架调研](docs/novel-framework-research-and-proposal.md) | 开源框架调研与总体方案 |
| [写作配置研究](docs/novel-writing-configuration-research.md) | 写作配置维度研究 |
| [Prompt Pack 设计](docs/prompt-pack-design.md) | 题材 prompt 包设计 |
| [状态与路线](docs/current-status-and-roadmap.md) | 当前状态与后续规划 |
| [叙事架构路线](docs/pageindex-integration-and-narrative-roadmap.md) | PageIndex 集成评估 |

---

## License

MIT
