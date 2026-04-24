# BestSeller

**面向长篇小说生产的分布式人机共创框架**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791)](https://www.postgresql.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)](https://fastapi.tiangolo.com/)
[![LiteLLM](https://img.shields.io/badge/LLM-Anthropic%20%7C%20OpenAI%20%7C%20Gemini%20%7C%20MiniMax-purple)](https://github.com/BerriAI/litellm)
[![Migrations](https://img.shields.io/badge/Alembic-29%20versions-brightgreen)](migrations/versions)
[![Services](https://img.shields.io/badge/Services-112%20modules-success)](src/bestseller/services)

BestSeller 把长篇创作拆成可持续迭代的工业级生产流水线——不是"一次性大 prompt 写书"，而是一套 **有规划 · 有知识 · 有合约 · 有审校 · 有债务 · 有记忆** 的自动化叙事系统。

```
Premise → Plan → Draft → Review → Rewrite → Override/Debt → Knowledge Propagation → Export → Publish
```

不同于只做提示词工程的框架，BestSeller 以 PostgreSQL 为唯一事实源，把每一个场景、伏笔、时间锚点、角色状态都写成可校验的结构化数据，并在 `run_chapter_pipeline` 的每一次回合里做 **硬约束 / 软约束 / 债务** 的三层门控。

---

## 目录

- [设计哲学](#设计哲学)
- [能力全景](#能力全景)
- [架构总览](#架构总览)
- [核心子系统](#核心子系统)
  - [1. 多级流水线](#1-多级流水线pipeline-architecture)
  - [2. 叙事知识系统](#2-叙事知识系统narrative-knowledge-system)
  - [3. 上下文装配引擎](#3-上下文装配引擎context-assembly-engine)
  - [4. 质量门控体系](#4-质量门控体系quality-gate-system)
  - [5. 统一检查器 Schema](#5-统一检查器-schemaphase-a)
  - [6. 类型化数值阈值](#6-类型化数值阈值phase-a)
  - [7. 叙事线主导度追踪](#7-叙事线主导度追踪phase-b)
  - [8. Override Contract + Debt Ledger](#8-override-contract--debt-ledgerphase-c)
  - [9. 时间锚点与倒计时守卫](#9-时间锚点与倒计时守卫phase-d)
  - [10. Hype Engine 爽点引擎](#10-hype-engine-爽点引擎)
  - [11. LLM 网关层](#11-llm-网关层llm-gateway)
  - [12. 分阶段世界扩张](#12-分阶段世界扩张staged-world-expansion)
  - [13. 检索增强生成](#13-检索增强生成rag-for-narrative)
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
- [多格式导出](#多格式导出)
- [发布调度](#发布调度)
- [评测体系](#评测体系)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [文档索引](#文档索引)

---

## 设计哲学

长篇小说（10 万 ~ 百万字）的核心难点不是"让 LLM 写出一段好文字"，而是 **如何在数十万字的跨度里维持叙事一致性、角色弧光连贯性和世界观自洽性**。

BestSeller 的设计围绕五个核心信念：

### 1. 流水线 > 一次性推理

单次 prompt 无法可靠生成长篇。系统把创作拆成 `规划 → 草稿 → 审校 → 重写 → 知识沉淀` 的多级流水线，每个环节有独立的 LLM 角色、独立的质量阈值、独立的重试策略。一个场景写砸了，只需要重跑这个场景。

### 2. 知识先于生成

写作不是凭空创作，而是"在已建立的知识库上做受约束的续写"。每个场景写完后，系统自动提取 Canon Facts（角色状态、关系变化、剧情事实）、Timeline Events（时间线事件）、Character State Snapshots（角色状态快照）。下一个场景的 writer 看到的是"当前世界的事实状态"，不是"前情提要"。

### 3. 约束即质量

每个章节有 Chapter Contract（主线推进目标、伏笔兑现任务、情绪弧要求），每个场景有 Scene Contract（具体要完成的叙事动作）。审校阶段逐条对照合约检查偏差，而不是泛泛地说"写得不好"。

### 4. 硬约束与软约束分层

并非所有规则都应该"硬拦截"。系统区分 **Hard Constraints**（世界规则、数值反演、倒计时单调性——绝不允许违反）和 **Soft Constraints**（叙事线间隔、章末钩子强度、情绪密度——允许作者在签字负责的前提下违反）。软违规通过 Override Contract 签字，自动开一笔 Chase Debt（追债），在指定章节前必须偿还，每章复利累积。

### 5. 可观测即可迭代

每一次章节审校都产生一份结构化的 CheckerReport（统一 JSON schema），聚合成 NovelScorecard。无论检查器是 bible_gate、continuity 还是 hype_engine，输出形状一致、可对比、可在时间维度上聚合。

---

## 能力全景

| 领域 | 能力 | 关键服务 / 文件 | 状态 |
|:---|:---|:---|:---:|
| **规划** | Book/World/Cast/Volume Spec | `conception.py` · `story_bible.py` · `planner.py` | ✅ |
| **草稿** | 场景/章节流式生成 | `drafts.py` · `pipelines.py` | ✅ |
| **上下文** | Token-budget 装配 · 路径/树/混合检索 | `context.py` · `retrieval.py` · `rag.py` | ✅ |
| **审校** | 多维度评分 · 重写级联 | `reviews.py` · `rewrite.py` · `scorecard.py` | ✅ |
| **一致性** | Canon · Timeline · 角色连续性 · 矛盾检测 | `knowledge.py` · `continuity.py` · `contradiction.py` | ✅ |
| **叙事结构** | PlotArc · Beat · Clue · Payoff · Contract | `narrative.py` · `narrative_lines.py` · `setup_payoff_tracker.py` | ✅ |
| **Hype 引擎** | 爽点/反转/悬念密度带 | `hype_engine.py` | ✅ |
| **多样性预算** | 爽点类型轮换 · 反 slop | `diversity_budget.py` · `anti_slop.py` | ✅ |
| **节奏** | 停滞/高潮节奏分析 | `pacing_engine.py` | ✅ |
| **检查器 Schema** | 所有审校器统一输出 `CheckerReport` | `checker_schema.py` | ✅ Phase A |
| **类型化阈值** | 8 类题材 × 13 个数值维度 | `genre_profile_thresholds.py` · `config/genre_profile_thresholds/` | ✅ Phase A |
| **叙事线主导度** | 4 层线（明/暗/隐/核轴）章节级间隔守卫 | `narrative_line_tracker.py` | ✅ Phase B |
| **Override Contract** | 软违规签字机制（7 种 rationale） | `override_contract.py` | ✅ Phase C |
| **Chase Debt** | 追债账本 · 复利累积 · 逾期扫描 | `chase_debt_ledger.py` | ✅ Phase C |
| **时间锚点** | 每章 time_anchor · 卷级时间线导出 | `continuity.py` · `cli timeline export` | ✅ Phase D |
| **倒计时算术** | D-n 单调递减 · 跳跃检测 | `continuity.py` | ✅ Phase D |
| **写作合约** | Chapter/Scene Contract · Methodology | `methodology.py` · `writing_profile.py` | ✅ |
| **世界扩张** | Volume Frontier · Deferred Reveal | `world_expansion.py` | ✅ |
| **LLM 网关** | LiteLLM · 熔断 · 重试 · 审计 | `llm.py` | ✅ |
| **导出** | Markdown · DOCX · EPUB · PDF | `exports.py` | ✅ |
| **发布** | 番茄 / 起点 / 七猫 / Amazon KDP | `publishing/` | ✅ |
| **互动小说** | IF Story Bible · Arc · Branch · Walkthrough | `if_*.py` | ✅ |

---

## 架构总览

```
┌───────────────────────────────────────────────────────────────────────┐
│                         接入层 Access Layer                            │
│  ┌────────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────────────┐   │
│  │ Web Studio │ │ REST API │ │ MCP Server│ │     CLI (Typer)      │   │
│  │   :8787    │ │  :8000   │ │   :3000   │ │   bestseller …       │   │
│  └──────┬─────┘ └────┬─────┘ └─────┬─────┘ └──────────┬───────────┘   │
├─────────┼────────────┼─────────────┼──────────────────┼───────────────┤
│                   服务层 Service Layer (112 modules)                   │
│  ┌──────┴────────────┴─────────────┴──────────────────┴────────────┐  │
│  │                  Pipeline Orchestrator                           │  │
│  │  autowrite · project · chapter · scene · progressive            │  │
│  └───┬──────────┬──────────┬────────────┬─────────────┬───────────┘  │
│      │          │          │            │             │              │
│  ┌───┴────┐ ┌───┴────┐ ┌───┴────┐ ┌─────┴─────┐ ┌────┴────────┐     │
│  │Concept │ │Planner │ │Drafts  │ │Reviews    │ │Knowledge    │     │
│  │&Bible  │ │&Outline│ │&Assemb │ │&Rewrite   │ │Propagation  │     │
│  └────────┘ └────────┘ └────────┘ └───────────┘ └─────────────┘     │
│      │          │          │            │             │              │
│  ┌───┴──────────┴──────────┴────────────┴─────────────┴───────────┐  │
│  │             Constraint & Quality Layer                          │  │
│  │ ┌────────────┐ ┌───────────┐ ┌────────────┐ ┌──────────────┐  │  │
│  │ │Hard Invar. │ │Soft Gate  │ │Override    │ │Chase Debt    │  │  │
│  │ │continuity  │ │write_gate │ │Contract    │ │Ledger +复利  │  │  │
│  │ │bible_gate  │ │regen_loop │ │(7 rationale│ │ overdue scan │  │  │
│  │ └────────────┘ └───────────┘ └────────────┘ └──────────────┘  │  │
│  │ ┌────────────┐ ┌───────────┐ ┌────────────┐ ┌──────────────┐  │  │
│  │ │Checker     │ │Genre      │ │Narrative   │ │Time Anchor + │  │  │
│  │ │Schema      │ │Thresholds │ │Line Track  │ │Countdown     │  │  │
│  │ │(统一报告)  │ │(8 题材)   │ │(4 层/间隔) │ │(D-n 单调)    │  │  │
│  │ └────────────┘ └───────────┘ └────────────┘ └──────────────┘  │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│      │          │          │            │             │              │
│  ┌───┴──────────┴──────────┴────────────┴─────────────┴───────────┐  │
│  │         Knowledge & Narrative Layer (Long-term Memory)          │  │
│  │  Canon Facts · Timeline · Character Snapshots · Narrative Graph │  │
│  │  Retrieval · Context Assembly · Staged World Expansion          │  │
│  └─────────────────────────────────────┬───────────────────────────┘  │
├─────────────────────────────────────────┼──────────────────────────────┤
│                     基础设施层 Infrastructure                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────────────┐    │
│  │PostgreSQL│ │  Redis   │ │ LiteLLM  │ │Embedding              │    │
│  │+pgvector │ │+ARQ queue│ │ Gateway  │ │BAAI/bge-m3 (1024d)    │    │
│  │+pg_trgm  │ │+Pub/Sub  │ │(Anthropic│ │sentence-transformers  │    │
│  │29 migrat.│ │          │ │/MiniMax/…│ │                       │    │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────────────┘    │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 核心子系统

### 1. 多级流水线（Pipeline Architecture）

五个入口函数，逐级分解：

| 入口 | 作用 | 典型场景 |
|:---|:---|:---|
| `run_autowrite_pipeline` | 一键整书生成 | 新项目从 premise 到完整书 |
| `run_progressive_autowrite_pipeline` | 渐进式增量生成 | 连载模式，每次追加 N 章 |
| `run_project_pipeline` | 项目级修复 | 检测并重写 `needs_rewrite` 章节 |
| `run_chapter_pipeline` | 单章生成 | 精修某一章 |
| `run_scene_pipeline` | 单场景生成 | 补写或替换某一个场景 |

```
Project Pipeline
 ├── Chapter Pipeline (×N)
 │    ├── Scene Pipeline (×M)
 │    │    ├── Context Assembly       ← 装配写作上下文
 │    │    ├── Draft Generation       ← LLM (writer) 生成草稿
 │    │    ├── Knowledge Propagation  ← 提取事实、时间线、角色状态
 │    │    ├── Review & Scoring       ← LLM (critic) 审校打分
 │    │    ├── Validator Pipeline     ← CheckerReport[] (Phase A)
 │    │    ├── Gate Resolution        ← hard_block / soft_audit / override
 │    │    └── Rewrite (if needed)    ← LLM (editor) 按审校意见重写
 │    ├── Phase B: Line Dominance     ← 计算 dominant_line + 持久化
 │    ├── Phase C: Debt Accrual       ← 逾期扫描 + 复利计提
 │    ├── Phase D: Time Snapshot      ← time_anchor + countdown 校验
 │    ├── Chapter Assembly            ← 合并场景为完整章节
 │    └── Chapter Export              ← Markdown checkpoint
 ├── Consistency Check                ← 项目级一致性评审
 ├── Auto Repair (if needed)          ← 自动重跑未通过章节
 └── Full Export                      ← Markdown / DOCX / EPUB / PDF
```

**设计精妙之处：**

- **Checkpoint Commit**：每个场景写完后立即持久化，避免 PostgreSQL 长事务快照膨胀；失败只需重跑失败的场景。
- **Progress Callback**：每一步通过 `ProgressCallback` 发射事件，Web UI/SSE 实时看到 `"正在写第 3 章第 2 场景"`。
- **幂等恢复**：`project repair` 自动分析 `needs_rewrite` / `failed` 状态。
- **Phase B/C/D 后置 hook**：每章生成完成后，系统自动调用 `_apply_post_chapter_phase_b/c` 和 `_collect_phase_d_reports`，把 Phase A-D 的能力织入流水线而非事后补丁。

### 2. 叙事知识系统（Narrative Knowledge System）

系统维护一个不断增长的叙事知识图谱：

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
    │     Chapter Contract / Scene Contract   │
    └────────────────────────────────────────┘
                         │
            Knowledge Layer (动态积累)
          ┌──────────────┼──────────────┐
    ┌─────┴─────┐  ┌─────┴─────┐  ┌────┴─────────┐
    │Canon Facts│  │ Timeline  │  │  Character   │
    │(结构化)   │  │  Events   │  │State Snapshot│
    └───────────┘  └───────────┘  └──────────────┘
```

**Canon Facts** 不是简单的"前情提要"，而是结构化事实数据库：
- 角色当前位置、情绪状态、已知信息、持有物品
- 角色间关系的最新状态
- 已兑现的伏笔和仍未揭示的暗线
- 世界规则的执行状态

**每个场景写完**，系统自动执行 `propagate_scene_discoveries`：
1. 从草稿文本提取新的 Canon Facts
2. 更新 Timeline Events
3. 刷新 Character State Snapshots
4. 重建 Retrieval Chunks（用于后续检索）

### 3. 上下文装配引擎（Context Assembly Engine）

```
SceneWriterContextPacket
├── story_bible_excerpt        ← 故事圣经核心摘要
├── world_rules (filtered)     ← 当前卷允许可见的世界规则
├── chapter_contract           ← 本章叙事合约
├── scene_contract             ← 本场景叙事合约
├── active_arcs                ← 当前活跃的情节线
├── active_beats               ← 当前需要推进的节拍
├── pending_clues              ← 已埋未兑现的伏笔
├── scheduled_payoffs          ← 本场景应兑现的伏笔
├── emotion_tracks             ← 情绪线走向
├── antagonist_plans           ← 反派当前阶段的行动
├── participant_facts          ← 本场景出场角色的 Canon Facts
├── recent_scene_summaries     ← 最近 N 个场景的摘要
├── character_state_snapshot   ← 上一章结束时的角色状态
├── timeline_window            ← 近期时间线事件
├── time_anchor (Phase D)      ← 本章起始时间锚 + 时长
├── line_gap_report (Phase B)  ← 近期线主导度分布 + 近危线提示
└── retrieval_results          ← 混合检索命中
```

**装配策略的精妙之处：**

- **三层检索**：`path retrieval → tree search → hybrid retrieval`。按叙事树路径精确定位，再按树结构语义搜索，最后向量+词法+结构三路混合。
- **Token 预算制**：总上下文预算固定，各部分按优先级竞争，低优先级被裁剪而不是丢弃。
- **可见性过滤**：World Expansion 系统的 `VolumeFrontier` / `DeferredReveal` 确保 writer 只能看到当前阶段允许展开的内容。
- **线轮换提示**：当某条叙事线接近 `strand_max_gap` 阈值时，系统在 writing brief 里注入一行"本章建议以 {line} 为底色/主导（距上次已 {gap} 章）"。

### 4. 质量门控体系（Quality Gate System）

每个层级都有独立的质量门控，不是一个笼统的"好/坏"判断：

**场景级评分维度：**
- `hook_strength` — 开场吸引力
- `conflict_clarity` — 冲突清晰度
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
- **Override / Debt 指标**：`overrides_count`、`active_debts_count`、`overdue_debts_count`

**三层处置策略：**

```
Violation → filter_blocking() →  ┌ hard_block     → rewrite or fail
                                 ├ soft_audit_with_override → auto-sign contract + open debt
                                 └ audit_only     → record, do not block
```

**重写级联**（Rewrite Cascade）：场景被判定需要重写时，系统自动分析受影响的下游 Canon Facts / Scenes / Chapters，级联重跑。

### 5. 统一检查器 Schema（Phase A）

所有审校器——无论是 `bible_gate`、`continuity`、`hype_engine`、`pacing_engine`、`chapter_validator` 还是 `output_validator`——都输出同一个 `CheckerReport` 数据结构：

```python
@dataclass(frozen=True)
class CheckerIssue:
    id: str                  # "HARD_001", "SOFT_HOOK_STRENGTH"
    type: str
    severity: Literal["critical","high","medium","low"]
    location: str            # "章末" / "第3段" / "全章"
    description: str
    suggestion: str
    can_override: bool       # False=硬约束, True=软约束
    allowed_rationales: tuple[RationaleType, ...]

@dataclass(frozen=True)
class CheckerReport:
    agent: str               # "bible-gate" / "continuity" / "hype" / ...
    chapter: int
    overall_score: int       # 0-100
    passed: bool
    issues: tuple[CheckerIssue, ...]
    hard_violations: tuple[CheckerIssue, ...]
    soft_suggestions: tuple[CheckerIssue, ...]
    metrics: Mapping[str, Any]
    summary: str
```

- **NovelScorecard** 聚合 `list[CheckerReport]`，跨 agent 可对比、可时间序列分析。
- 每个检查器提供 `as_checker_report()` 适配器方法——内部实现不变，只加一层规范化出口。
- 上游消费者（审校面板、CLI 报告、REST API）不再为每个 agent 的 ad-hoc 字段定制解析。

### 6. 类型化数值阈值（Phase A）

**8 类题材 × 13 维阈值** 的结构化数据层，取代散落在 prompt pack 里的字符串常量：

```python
@dataclass(frozen=True)
class GenreProfileThresholds:
    id: str
    name: str
    hook_config: HookConfig              # 偏好类型、强度基线、章末必备、过渡容忍
    coolpoint_config: CoolpointConfig    # 偏好模式、密度带、combo 间距、milestone 间距
    micropayoff_config: MicropayoffConfig
    pacing_config: PacingThresholds      # stagnation_threshold, strand_max_gap, transition_max
    override_config: OverrideConfig      # allowed_rationale_types, debt_multiplier, payback_window
```

内置题材：

| 题材 ID | 名称 | 典型设定 |
|:---|:---|:---|
| `action-progression` | 动作升级流 | 高密度爽点 · 短 payback |
| `relationship-driven` | 关系驱动流 | 高情绪线权重 · 长 strand gap |
| `suspense-mystery` | 悬疑推理 | 高 hook baseline · 严格倒计时 |
| `strategy-worldbuilding` | 战略/世界观 | 低密度 · 高 clue 存活期 |
| `esports-competition` | 电竞竞技 | Milestone 周期 · combo 爆发 |
| `female-growth-ncp` | 女频成长向 | 情绪密度带 · 关系拉扯 |
| `base-building` | 囤货/基建 | 数值可视化 · 低倒计时密度 |
| `eastern-aesthetic` | 东方美学 | 意境浓度 · 低爽点密度 |

配置在 `config/genre_profile_thresholds/*.yaml`；prompt pack 仍可用顶层键 shadow 题材阈值（特例化优先）。

### 7. 叙事线主导度追踪（Phase B）

系统维护 **4 层叙事线**（明线 / 暗线 / 隐线 / 核心轴），并在 **章节粒度** 追踪每条线上一次主导是多少章前：

```
明线 (overt)         ████░░████░░░░█████░░   ← 本章主导 ✓
暗线 (undercurrent)  ░░██░░░░██░░░░░░░░██░   ← 距上次 7 章（阈值 10）
隐线 (hidden)        ░░░░░░██░░░░░░░░░░░░░   ← 距上次 12 章 ⚠ 接近阈值
核心轴 (core_axis)   █░░░░░░░░░░░░░░█░░░░░   ← 距上次 14 章（阈值 20）
                     ↑ 当前章节
```

每章流水线末尾：

1. `classify_chapter(text, outline, bible)` 返回 `(dominant, supports, intensity)`
2. 持久化到 `ChapterModel.dominant_line / support_lines / line_intensity`
3. `report_gaps(project_id, current_chapter)` 比对 `PacingThresholds.strand_max_gap` 出具 `LineGapReport`
4. `LineGapCheck` 发出 `CheckerIssue`：
   - 超过阈值 → `critical`
   - 达到阈值 80% → `high`（"near-overdue"）
   - `can_override=True`，允许 rationale：`ARC_TIMING / TRANSITIONAL_SETUP / GENRE_CONVENTION`
5. 若 near-overdue，下一次 `prompt_constructor` 在 brief 里注入轮换提示

`project.metadata_json.line_dominance_history` 保留最近 50 章滚动记录，支持回放和报表。

### 8. Override Contract + Debt Ledger（Phase C）

**问题**：章节通过审校要求 3 条关键爽点，但作者本章要写一场关键转折，爽点密度只有 2 条。硬拦住等于"为规则服务"，完全不管又丢失了长期追踪。

**解决**：签字机制 + 复利追债。

```
 [Violation: LINE_GAP_OVER]
        │
        │ all(v.code ∈ soft_constraint_codes)?
        ▼
 [Auto-sign Override Contract]
  ├ rationale_type: ARC_TIMING
  ├ rationale_text: "本章压转折, 明线延后一章"
  ├ payback_plan:  "第12章主线大兑现"
  ├ due_chapter:   12
  └ status:        active
        │
        ▼
 [Open Chase Debt]
  ├ principal:     base × genre.debt_multiplier (e.g. 1.5×)
  ├ interest_rate: 10%/chapter
  ├ balance:       principal
  ├ due_chapter:   12
  └ status:        active
        │
        │ 每章 _apply_post_chapter_phase_c 执行:
        │   scan_overdue() → 逾期翻转 status=overdue
        │   accrue_interest() → balance *= (1+rate)
        ▼
 [At Chapter 12]
   满足偿还条件 → close_debt(status=paid)
   未满足 → status=overdue, NovelScorecard.overdue_debts_count += 1
```

**7 种 rationale 类型**（`RationaleType` enum）：
- `TRANSITIONAL_SETUP` — 过渡铺陈
- `LOGIC_INTEGRITY` — 逻辑完整性
- `CHARACTER_CREDIBILITY` — 角色可信度
- `WORLD_RULE_CONSTRAINT` — 世界规则约束
- `ARC_TIMING` — 弧线时序
- `GENRE_CONVENTION` — 题材惯例
- `EDITORIAL_INTENT` — 编辑意图

**复利计算**：`balance *= (1 + rate)^n`，默认 10%/章 —— 5 章未付本金长到 1.61×。

**默认软约束集合**（`invariants.soft_constraint_codes`）：

```python
frozenset({
    "LINE_GAP_OVER",         # 叙事线间隔超限（Phase B）
    "LINE_GAP_WARN",         # 叙事线接近阈值
    "TIME_ANCHOR_REGRESSION" # 时间锚回退（Phase D）
})
```

其他默认均为硬约束，必须重写解决。可按项目覆盖。

### 9. 时间锚点与倒计时守卫（Phase D）

**背景**：类似末日生存、悬疑倒计时、异世界穿越类题材，章与章之间的"绝对时间"容易漂移——上一章说"第 3 天清晨"，这一章跳到"傍晚"但实际事件跨度只有 30 分钟；或倒计时从 D-5 直接跳到 D-2，中间 3 天凭空消失。

**方案**：

1. **每章时间快照**（`ChapterStateSnapshot`）新增两个字段：
   - `time_anchor`：自由文本，如 `"末世第4天 清晨"` / `"凡历元年 秋末 黄昏"`
   - `chapter_time_span`：章内时间跨度，如 `"约 3 小时"`

2. **卷级时间线文件**（`大纲/第{N}卷-时间线.md`）：
   ```bash
   bestseller timeline export my-story --volume 2
   # → 大纲/第2卷-时间线.md
   ```
   输出 markdown 表格：每章的时间锚点 / 章内时间跨度 / 与上章时间差 / 倒计时状态。

3. **两类验证器**（都走 CheckerReport）：
   - `CountdownArithmeticCheck`（硬约束）：对每个命名倒计时（如"物资耗尽"），校验 `prev_value - current_value ∈ {0, 1}`，除非标记为 flashback。`can_override=False`。
   - `TimeRegressionCheck`（软约束）：time_anchor 回退且未标记 flashback → severity=`high`，允许 rationale `WORLD_RULE_CONSTRAINT / LOGIC_INTEGRITY`。

### 10. Hype Engine 爽点引擎

长篇连载的生命线是 "hype"—— 爽点、悬念、反转、情绪峰值的周期性投放。

```
┌──────────────────────────────────────────┐
│  HypeType 维度                            │
│  ├ shock        (反转/震撼)              │
│  ├ power_reveal (实力揭晓)               │
│  ├ face_slap    (打脸)                   │
│  ├ reunion      (重逢)                   │
│  ├ revenge      (复仇)                   │
│  ├ discovery    (线索/真相)              │
│  ├ emotion_peak (情绪巅峰)               │
│  └ cliffhanger  (章末钩子)               │
├──────────────────────────────────────────┤
│  Density Band (题材差异化)                │
│  高密度带  ≥ 3/章                        │
│  中密度带  1-2/章                        │
│  低密度带  1/2-3章                       │
├──────────────────────────────────────────┤
│  Diversity Budget (多样性预算)            │
│  最近 5 章 cliffhanger 已用 3 次 → 本章禁用 │
│  强制轮换类型，避免 slop                  │
├──────────────────────────────────────────┤
│  Golden Finger Ladder (金手指阶梯)        │
│  每 N 章投放一次等级跃升 + 能力揭示        │
└──────────────────────────────────────────┘
```

与 `hype_engine.py / diversity_budget.py / anti_slop.py` 协同，在 `prompt_constructor` 里把 "本章应出 shock + discovery，禁用 cliffhanger" 直接写进 writing brief。审校阶段用 `HypeEngine.detect` 反向校验产出。

### 11. LLM 网关层（LLM Gateway）

系统通过 LiteLLM 统一接入多个模型提供商，并按 **角色** 分配不同的模型和参数：

| 角色 | 职责 | 默认 | 生产默认 | 温度 | 说明 |
|:---|:---|:---|:---|---:|:---|
| `planner` | 规划生成 | Claude Opus | MiniMax M2.7 | 0.65 | 高推理 |
| `writer` | 正文写作 | Claude Sonnet | MiniMax M2.7 | 0.85 | 高创意，流式 |
| `critic` | 审校打分 | Claude Haiku | MiniMax M2.7 | 0.25 | 低温度，精确 |
| `summarizer` | 摘要压缩 | Claude Haiku | MiniMax M2.7 | 0.20 | 信息保持 |
| `editor` | 重写润色 | Claude Sonnet | MiniMax M2.7 | 0.40 | 中温度 |

> `start.sh` 会通过 `BESTSELLER__LLM__*` 环境变量覆盖 `config/default.yaml` 的默认模型。

**韧性设计：**
- **熔断器**：连续 5 次失败熔断 60 秒，防止级联故障
- **指数退避重试**：对 RateLimitError / APITimeout / ServiceUnavailable 自动重试
- **HTTP 连接池**：per-event-loop 的 httpx.AsyncClient 池，减少 TLS 握手
- **审计日志**：每次 LLM 调用写入 `llm_runs` 表（模型、角色、token 数、延迟、成本）
- **Mock 模式**：无外部依赖时自动降级为确定性输出

### 12. 分阶段世界扩张（Staged World Expansion）

```
WorldBackbone         ← 贯穿全书的世界主干（不可漂移）
 ├── VolumeFrontier   ← 当前卷允许展开的世界边界
 ├── DeferredReveal   ← 未来才允许揭示的暗线/真相
 └── ExpansionGate    ← 世界扩张闸门（随章节推进同步）
```

Writer 在写第 5 章时，只能看到 Volume 1 Frontier 允许的世界规则、地点和势力，避免在第 3 章就泄露第 20 章才该揭示的秘密。

### 13. 检索增强生成（RAG for Narrative）

PostgreSQL + pgvector 实现专为叙事场景优化的混合检索：

```
查询 → ┌── Vector Search (pgvector, BAAI/bge-m3)    权重 60%
       ├── Lexical Search (pg_trgm, trigram)         权重 20%
       └── Structural Search (叙事树路径匹配)          权重 20%
       ───────────────────────────────────────────────
       → Ranked Results → Token Budget Trimming → Context Packet
```

**结构化检索** 不仅做语义相似度，还理解叙事结构：查询 `/chapters/003/contract` 精确返回第三章的叙事合约，而不是语义相似但不相关的内容。

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
  migrate:    alembic upgrade head                 # 一次性迁移容器
```

**任务处理：**
- **ARQ**（Async Redis Queue）负责长时任务（autowrite 整书可能运行数小时）
- **APScheduler** 负责定时发布（cron 表达式 + 时区）
- Redis Pub/Sub 实现跨服务的实时进度推送
- SSE 把进度流推到浏览器

### 数据库设计

PostgreSQL 是 **唯一数据源**（Single Source of Truth），50+ 张表覆盖：

| 领域 | 核心模型 |
|:---|:---|
| **项目结构** | Project → Volume → Chapter → SceneCard |
| **规划产物** | PlanningArtifactVersion（版本号 + 审批状态） |
| **世界构建** | WorldRule · Location · Character · Relationship · WorldBackbone · VolumeFrontier |
| **叙事图谱** | PlotArc · ArcBeat · Clue · Payoff · Contract · EmotionTrack · AntagonistPlan |
| **内容草稿** | SceneDraftVersion · ChapterDraftVersion |
| **知识层** | CanonFact · TimelineEvent · CharacterStateSnapshot · **ChapterStateSnapshot (time_anchor)** |
| **检索索引** | RetrievalChunk（pgvector HNSW 索引） |
| **工作流** | WorkflowRun → WorkflowStepRun |
| **审计** | LlmRun（每次 LLM 调用的完整记录） |
| **发布** | PublishingPlatform · Schedule · PublishedChapter |
| **互动小说** | IFStoryBible · IFArcPlan · IFBranch · IFWalkthrough |
| **Phase B** | `chapters.dominant_line / support_lines / line_intensity` |
| **Phase C** | `override_contracts` · `chase_debts` |
| **物料库** | MaterialLibrary · ProjectMaterial · CrossProjectFingerprint |
| **角色生命周期** | CharacterLifecycle（出场/退场/状态轨迹） |

**关键设计模式：**
- **版本化产物**：规划和草稿都有 `version_no`，保留完整修改历史
- **乐观锁**：`lock_version` 防止并发冲突
- **JSONB 灵活存储**：配置、上下文、元数据用 JSONB
- **向量索引**：pgvector HNSW 用于语义检索
- **Alembic 迁移**：29 个版本的 schema 迁移，支持在线升级

### 配置体系

四层配置覆盖：

```
config/default.yaml                       ← 基础默认值
config/local.yaml                         ← 本地覆盖（不入库）
config/quality_gates.yaml                 ← 质量门 + Phase B/C/D 开关
config/genre_profile_thresholds/*.yaml    ← 8 类题材的数值阈值
config/prompt_packs/*.yaml                ← 题材 prompt 模板包
.env / .env.local                         ← 环境变量
BESTSELLER__*                             ← 运行时环境变量覆盖
```

所有配置由 Pydantic Settings 类验证，类型安全且有默认值。

**Phase 开关（`quality_gates.yaml`）：**

```yaml
phase_b_line_tracker:
  enabled: false              # 默认关闭；按项目 opt-in
  only_enforce_from_chapter: 10

phase_c_overrides:
  enabled: false
  auto_sign_all_soft: true    # 全部软违规自动签字开债
  interest_rate: 0.10
  payback_window_default: 10

phase_d_time:
  enabled: false
  countdown_arithmetic_enabled: true
  regression_check_enabled: true
```

---

## 接入方式

### Web Studio

```bash
./start.sh && ./studio.sh
# 或
make ui
```

支持：创建项目、输入 premise、配置写作画像、一键 `autowrite`、实时查看阶段/进度、浏览项目结构 / 故事圣经 / 叙事图谱 / 流程跟踪、在线阅读 Markdown 成品、触发 `project repair`。

### REST API

FastAPI，Bearer Token 认证：

```
POST /api/v1/projects                              创建项目
POST /api/v1/projects/{slug}/autowrite             一键整书
GET  /api/v1/projects/{slug}/structure             项目结构
GET  /api/v1/projects/{slug}/content               完整正文
POST /api/v1/projects/{slug}/export/{format}       导出
GET  /api/v1/tasks/{id}                            任务状态（SSE）
POST /api/v1/projects/{slug}/publishing/schedule   发布调度
GET  /api/v1/projects/{slug}/timeline?volume=N     时间线导出
GET  /api/v1/projects/{slug}/scorecard             审校评分（含 Phase C 债务指标）
```

### MCP Server

FastMCP HTTP（端口 3000），将功能暴露为 MCP 工具，可被 Claude 等 AI 代理直接调用：

```bash
bestseller-mcp
```

20+ 工具覆盖：项目管理、流水线触发、内容检索、格式导出、发布调度、任务监控。

### CLI

Typer 驱动，100+ 命令覆盖所有功能。主命令簇：

| 命令簇 | 用途 |
|:---|:---|
| `bestseller project` | 项目创建 / 列表 / autowrite / pipeline / repair / health |
| `bestseller chapter` | 单章操作 |
| `bestseller scene` | 单场景操作 |
| `bestseller planning` | 大纲 / BookSpec 导入导出 |
| `bestseller narrative` | 查看叙事图谱 |
| `bestseller story-bible` | 故事圣经 CRUD |
| `bestseller timeline` | **时间线导出 / 列表（Phase D）** |
| `bestseller canon` | Canon Facts 查询 |
| `bestseller rewrite` | 重写指定章 / 场景 |
| `bestseller retrieval` | 检索调试 |
| `bestseller export` | Markdown / DOCX / EPUB / PDF |
| `bestseller publish-profile` | 发布平台适配 |
| `bestseller prompt-pack` | 题材 prompt 包 |
| `bestseller writing-preset` | 平台/题材/篇幅预设 |
| `bestseller benchmark` | 评测套件 |
| `bestseller workflow` | 工作流跟踪 |
| `bestseller if` | 互动小说 |

典型示例：

```bash
bestseller project autowrite my-story "标题" sci-fi 80000 20 --premise "..."
bestseller project structure my-story
bestseller narrative show my-story
bestseller timeline export my-story --volume 2
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
./scripts/run.sh project autowrite demo "示例小说" sci-fi 22000 4 \
  --premise "一名被放逐的导航员发现帝国正在篡改边境航线记录，并被迫在追杀中揭穿真相。"

ls output/demo/
cat output/demo/project.md
```

### Docker Compose（生产部署）

```bash
cp .env.example .env
# 编辑 .env 填入 LLM API Key
./stop.sh && ./start.sh --build     # 触发 compose build + 迁移
```

或直接使用脚本：

```bash
./scripts/docker-start.sh --build
```

脚本会自动：
- `docker compose build --no-cache`（`--build` 下）
- `docker compose --profile migrate run --rm migrate`（执行 `alembic upgrade head`）
- 启动 `api / worker / scheduler / mcp / web / db / redis`
- 自动检测 SSD 卷 `/Volumes/SSD/Docker/bestseller`
- 解决端口冲突

### 模型配置

```bash
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Google Gemini
BESTSELLER_LLM_PROVIDER=gemini
GEMINI_API_KEY=...

# NVIDIA NIM（hosted API；也可用 NVIDIA_API_BASE 指向自托管 NIM /v1）
BESTSELLER_LLM_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
NVIDIA_LLM_MODEL=nvidia/nemotron-4-340b-instruct

# 火山方舟 Coding Plan（字节；专属 OpenAI-compatible coding 网关）
BESTSELLER_LLM_PROVIDER=volcengine-coding
ARK_API_KEY=...
ARK_CODING_MODEL=ark-code-latest

# OpenAI
OPENAI_API_KEY=sk-...

# MiniMax（生产默认，由 start.sh 设置）
BESTSELLER__LLM__WRITER__MODEL=openai/MiniMax-M2.7
BESTSELLER__LLM__WRITER__API_BASE=https://...

# 任意 OpenAI-compatible endpoint
BESTSELLER__LLM__WRITER__MODEL=openai/your-model
BESTSELLER__LLM__WRITER__API_BASE=https://your-endpoint/v1
```

`start.sh` 也接受 provider 别名：`nvidia-nim`/`nim` 会归一到 `nvidia`，`byte-coding`/`bytedance-coding`/`ark-coding`/`coding-plan` 会归一到 `volcengine-coding`。火山普通模型 API 不是 Coding Plan 网关时，仍可用“任意 OpenAI-compatible endpoint”方式配置 `https://ark.cn-beijing.volces.com/api/v3` 与具体模型/端点 ID。

### 启用 Phase A–D 能力

编辑 `config/quality_gates.yaml`：

```yaml
phase_b_line_tracker:
  enabled: true
phase_c_overrides:
  enabled: true
phase_d_time:
  enabled: true
```

然后 `./stop.sh && ./start.sh --build` 让新配置生效。已有章节通过 `only_enforce_from_chapter` 老章节豁免机制，不会被追溯。

---

## 写作配置系统

### Prompt Pack

题材专用的 prompt 模板包，**不需要改 Python 逻辑即可新增题材**：

| Pack | 题材 |
|:---|:---|
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

- **平台预设**：番茄小说 / 起点中文网 / 七猫小说 / 晋江文学城
- **题材预设**：17 类（末日囤货 / 仙侠 / 都市异能 / 悬疑 / 无限流 …）
- **篇幅预设**：4 章样书 ~ 超长连载阶段单元
- **热点推荐**：按市场热度排序

```bash
./scripts/run.sh writing-preset list
./scripts/run.sh writing-preset hot --limit 8
```

### 写作画像

完整可执行写作配置，**贯穿规划到审校的所有环节**：

```
平台与内容定位  │ platform_target · content_mode · reader_promise · selling_points
              │ hook_keywords · pacing_profile · payoff_rhythm · update_strategy
主角与人物引擎 │ protagonist_archetype · golden_finger · growth_curve
              │ romance_mode · antagonist_mode · ensemble_mode
世界与信息释放 │ worldbuilding_density · info_reveal_strategy · rule_hardness
              │ power_system_style · mystery_density
文风与表达约束 │ pov_type · tense · tone_keywords · prose_style · dialogue_ratio
              │ taboo_topics · taboo_words · reference_works
连载节奏硬约束 │ opening_mandate · first_three_chapter_goal · scene_drive_rule
              │ chapter_ending_rule · free_chapter_strategy
约束分层       │ soft_constraint_codes (Phase C)
题材阈值绑定   │ genre_profile_threshold_id (Phase A)
```

---

## 多格式导出

```bash
bestseller export markdown my-story    # Markdown（默认）
bestseller export docx my-story        # Word
bestseller export epub my-story        # 电子书
bestseller export pdf my-story         # PDF（需 pip install -e .[export]）
```

产物输出到 `output/<project-slug>/`：
- `project.md` / `chapter-001.md` / `chapter-002.md` …
- `project.docx` / `project.epub` / `project.pdf`
- `大纲/第{N}卷-时间线.md`（Phase D，通过 `bestseller timeline export` 生成）

---

## 发布调度

- APScheduler + Cron 表达式 + 时区
- 平台适配器：番茄小说 · 起点中文网 · 七猫小说 · Amazon KDP
- Redis Pub/Sub 热加载：修改调度立即生效
- 发布历史记录和追踪

```bash
POST /api/v1/projects/{slug}/publishing/schedule
{
  "platform": "fanqie",
  "cron": "0 8 * * *",
  "timezone": "Asia/Shanghai"
}
```

---

## 评测体系

```bash
./scripts/run.sh benchmark list
./scripts/run.sh benchmark run sample-books --slug-prefix bench
./scripts/run.sh benchmark run sample-books --case doomsday-hoarding --slug-prefix bench
```

每个 case 检查：
- autowrite 是否完成
- 产物是否生成
- 项目级评分是否达标
- 叙事线类型是否齐全
- 情绪线和反派推进是否存在
- **Override / Debt 指标在预期范围**（Phase C）
- **时间锚点无回退**（Phase D）

报告输出到 `output/benchmarks/`。

---

## 技术栈

| 层 | 技术 |
|:---|:---|
| **语言** | Python 3.11+ |
| **API** | FastAPI + Uvicorn + Starlette-SSE |
| **ORM** | SQLAlchemy 2.0 (async) + asyncpg |
| **数据库** | PostgreSQL 16 + pgvector + pg_trgm + pgcrypto |
| **队列** | ARQ (Async Redis Queue) |
| **调度** | APScheduler 3.x |
| **缓存/消息** | Redis 7 |
| **LLM 网关** | LiteLLM (Anthropic / OpenAI / Gemini / MiniMax / any compatible) |
| **嵌入** | sentence-transformers (BAAI/bge-m3, 1024 dims) |
| **导出** | python-docx, ebooklib, reportlab, markdown |
| **MCP** | FastMCP 2.0 |
| **CLI** | Typer + Rich |
| **配置** | Pydantic Settings + YAML + dotenv |
| **迁移** | Alembic (29 versions) |
| **容器** | Docker multi-stage + docker-compose |
| **测试** | pytest + coverage（2774+ unit · 3 integration E2E） |
| **代码质量** | ruff + mypy + pre-commit |

---

## 项目结构

```
bestseller/
├── src/bestseller/
│   ├── api/                             # REST API 层
│   │   ├── app.py                       #   FastAPI 应用工厂 + 生命周期
│   │   ├── deps.py                      #   依赖注入
│   │   ├── routers/                     #   路由模块
│   │   └── schemas/                     #   Pydantic 请求/响应模型
│   ├── cli/main.py                      # CLI 入口（Typer）
│   ├── domain/                          # 领域模型
│   │   ├── project.py
│   │   ├── pipeline.py
│   │   ├── narrative.py
│   │   ├── context.py                   #   SceneWriterContextPacket
│   │   ├── knowledge.py                 #   Canon Facts · 时间线事件
│   │   └── …
│   ├── services/                        # 业务逻辑（112 模块，核心层）
│   │   ├── pipelines.py                 #   🔥 流水线编排入口（5 个 run_* 函数）
│   │   ├── conception.py                #   BookSpec/WorldSpec/CastSpec
│   │   ├── planner.py                   #   章节大纲生成
│   │   ├── drafts.py                    #   🔥 场景/章节草稿 + _evaluate_chapter_quality_gate
│   │   ├── reviews.py                   #   审校与质量评分
│   │   ├── context.py                   #   上下文装配引擎
│   │   ├── knowledge.py                 #   知识传播与管理
│   │   │
│   │   ├── checker_schema.py            # ✨ Phase A: 统一 CheckerReport
│   │   ├── genre_profile_thresholds.py  # ✨ Phase A: 类型化数值阈值
│   │   ├── genre_review_profiles.py     #   含 load_thresholds()
│   │   ├── narrative_line_tracker.py    # ✨ Phase B: 4 层线主导度追踪
│   │   ├── override_contract.py         # ✨ Phase C: 签字机制 + 7 rationale
│   │   ├── chase_debt_ledger.py         # ✨ Phase C: 追债账本 + 复利
│   │   │
│   │   ├── bible_gate.py                #   故事圣经门控
│   │   ├── continuity.py                #   角色连续性 + 倒计时 + 时间回退
│   │   ├── contradiction.py             #   矛盾检测
│   │   ├── consistency.py               #   项目级一致性
│   │   ├── narrative.py                 #   叙事图谱操作
│   │   ├── narrative_lines.py           #   4 层叙事线建模
│   │   ├── hype_engine.py               #   爽点引擎 + 密度带
│   │   ├── diversity_budget.py          #   类型轮换 · 反 slop 预算
│   │   ├── anti_slop.py                 #   Tier1 slop 剥除
│   │   ├── pacing_engine.py             #   节奏 / 停滞检测
│   │   ├── setup_payoff_tracker.py      #   伏笔/兑现债务
│   │   ├── methodology.py               #   写作合约与方法论
│   │   ├── scorecard.py                 #   NovelScorecard 聚合
│   │   ├── chapter_validator.py         #   章节验证器（LineGapCheck 等）
│   │   ├── output_validator.py          #   产出合规
│   │   ├── audit_loop.py                #   审校循环
│   │   ├── regen_loop.py                #   重写循环 + propose_overrides
│   │   ├── write_gate.py                #   硬/软门控裁决
│   │   ├── write_safety_gate.py         #   安全硬闸
│   │   ├── quality_gates_config.py      #   Phase B/C/D 配置解析
│   │   ├── prompt_constructor.py        #   writing brief 构造
│   │   ├── invariants.py                #   项目级不变量 + soft_constraint_codes
│   │   ├── story_bible.py               #   故事圣经 + timeline 导出
│   │   ├── retrieval.py / rag.py        #   三层检索
│   │   ├── llm.py                       #   LLM 网关 (熔断 + 审计)
│   │   ├── world_expansion.py           #   分阶段世界扩张
│   │   ├── character_identity_resolver.py
│   │   ├── reader_power.py              #   读者能量曲线
│   │   ├── project_health.py            #   项目健康扫描
│   │   ├── query_broker.py              #   检索代理
│   │   ├── truth_version.py             #   版本化真相管理
│   │   ├── exports.py                   #   多格式导出
│   │   ├── publishing/                  #   发布平台适配
│   │   └── …                            #   其余 60+ 专项模块
│   ├── infra/
│   │   ├── db/                          #   SQLAlchemy 模型 + session + schema
│   │   └── redis.py                     #   Redis 客户端
│   ├── worker/                          # ARQ 异步任务
│   ├── scheduler/                       # APScheduler 发布调度
│   ├── mcp/                             # MCP 服务
│   └── web/                             # 内嵌 Web UI
├── config/
│   ├── default.yaml                     # 基础默认
│   ├── quality_gates.yaml               # 🔥 Phase B/C/D 开关
│   ├── genre_profile_thresholds/        # ✨ 8 类题材阈值
│   ├── prompt_packs/                    # 题材 prompt 包
│   ├── novel_categories/                # 题材分类
│   ├── writing_methodology.yaml         # 写作方法论
│   └── facets/                          # 配置切面
├── migrations/versions/                 # Alembic（29 版本）
│   ├── …
│   ├── 0019_hype_engine.py              # Hype Engine 表
│   ├── 0020_character_lifecycle.py
│   ├── 0021_material_library.py
│   ├── 0022_project_materials.py
│   ├── 0023_cross_project_fingerprint.py
│   ├── 0024_line_dominance.py           # ✨ Phase B 列
│   ├── 0024_title_patterns.py
│   ├── 0025_override_debt.py            # ✨ Phase C 两张表
│   └── 0026_time_anchor.py              # ✨ Phase D 列
├── tests/                               # 2774+ unit · 3 integration E2E
├── scripts/
│   ├── docker-start.sh                  # 容器一键启停 + 迁移
│   ├── run.sh                           # venv + CLI 包装
│   └── …
├── docs/                                # 设计文档
├── examples/                            # 示例配置和规划产物
├── docker-compose.yml
├── Dockerfile                           # 多阶段构建
└── pyproject.toml
```

---

## 文档索引

| 文档 | 内容 |
|:---|:---|
| [架构设计](docs/architecture.md) | 系统架构详解 |
| [数据库方案](docs/database-schema.md) | 完整数据库 schema |
| [Prompt 设计](docs/prompt-engineering-strategy.md) | 提示词工程策略 |
| [框架调研](docs/novel-framework-research-and-proposal.md) | 开源框架调研 |
| [写作配置研究](docs/novel-writing-configuration-research.md) | 写作配置维度 |
| [Prompt Pack 设计](docs/prompt-pack-design.md) | 题材 prompt 包设计 |
| [状态与路线](docs/current-status-and-roadmap.md) | 当前状态与后续规划 |
| [叙事架构路线](docs/pageindex-integration-and-narrative-roadmap.md) | PageIndex 集成评估 |

---

## License

MIT
