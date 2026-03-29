# 互动爽文小说系统优化方案
## 目标：支持1000章+、真硬分支多路线、全量实施

---

## 现状问题总结

| 问题 | 严重度 | 影响 |
|------|--------|------|
| 章节生成是假并行（实为串行），1000章需3-4小时 | CRITICAL | 不可用 |
| Arc间零上下文传递，第500章不知道第50章发生了什么 | CRITICAL | 故事失忆 |
| 多分支只定义在Bible里，从未实际生成不同章节内容 | HIGH | 伪互动 |
| 无Acts层（全书大纲），1000章无叙事结构骨架 | HIGH | 烂尾风险 |
| 无世界状态快照，角色/势力变化无法在后期章节中感知 | HIGH | 角色前后矛盾 |
| 无Arc级别摘要，伏笔/爽点无法跨Arc追踪 | MEDIUM | 质量下降 |

---

## 架构变更总览

```
旧 Pipeline（6阶段）:
  Story Bible → Arc Plan → Chapter Gen → Walkthrough → Assembly → Compile

新 Pipeline（10阶段）:
  Story Bible → Act Plan(NEW) → Arc Plan(v2) → Chapter Gen(v2,真并行)
  → Branch Plan(NEW) → Branch Chapter Gen(NEW)
  → Arc Summary(NEW) → World Snapshot(NEW)
  → Walkthrough → Assembly → Compile
```

---

## 新增文件

| 文件 | 职责 |
|------|------|
| `src/bestseller/services/if_context.py` | ContextAssembler：三级记忆系统 |
| `src/bestseller/services/if_act_planner.py` | Acts级别规划（全书幕结构） |
| `src/bestseller/services/if_branch_engine.py` | 硬分支规划+生成引擎 |
| `migrations/versions/0009_if_branch_and_memory.py` | DB Schema迁移 |

---

## 修改文件

| 文件 | 修改内容 |
|------|---------|
| `src/bestseller/services/if_generation.py` | 真并行、新pipeline阶段、Arc Summary触发 |
| `src/bestseller/services/if_prompts.py` | arc_plan_prompt_v2、world_snapshot_prompt、arc_summary_prompt、branch_arc_plan_prompt |
| `src/bestseller/domain/project.py` | InteractiveFictionConfig增加新字段 |
| `src/bestseller/domain/enums.py` | IFGenerationPhase、ArtifactType扩展 |
| `src/bestseller/infra/db/models.py` | 新增5个ORM模型 |

---

## 详细实施步骤

### Step 1 — domain/enums.py：扩展枚举

在 `IFGenerationPhase` 增加：
```python
ACT_PLAN = "act_plan"
BRANCH_PLAN = "branch_plan"
BRANCH_CHAPTER_GEN = "branch_chapter_gen"
ARC_SUMMARY = "arc_summary"
WORLD_SNAPSHOT = "world_snapshot"
```

在 `ArtifactType` 增加：
```python
IF_ACT_PLAN = "if_act_plan"
IF_ARC_SUMMARY = "if_arc_summary"
IF_WORLD_SNAPSHOT = "if_world_snapshot"
IF_BRANCH_DEFINITION = "if_branch_definition"
```

---

### Step 2 — domain/project.py：InteractiveFictionConfig 扩展

新增字段（带默认值，向后兼容）：
```python
# Acts结构
act_count: int = Field(default=5, ge=2, le=8)

# 分支控制
enable_branches: bool = Field(default=False)
branch_count: int = Field(default=2, ge=0, le=4)
branch_chapter_span: int = Field(default=30, ge=10, le=80)

# 上下文模式
context_mode: Literal["basic", "tiered", "full"] = Field(default="tiered")
snapshot_interval: int = Field(default=50, ge=25, le=100)

# 爽点质量
power_moment_interval: int = Field(default=5, ge=3, le=10)
```

---

### Step 3 — infra/db/models.py：新增5个ORM模型

#### IFActPlanModel（Acts级规划）
```python
__tablename__ = "if_act_plans"
id: UUID PK
project_id: UUID FK projects
run_id: UUID FK if_generation_runs
act_id: str (VARCHAR 32) — "act_01"..."act_05"
act_index: int
title: str
chapter_start: int
chapter_end: int
act_goal: str
core_theme: str
dominant_emotion: str
climax_chapter: int | None
entry_state: str | None
exit_state: str | None
payoff_promises: list (JSONB)
branch_opportunities: list (JSONB)  — [{trigger_chapter, routes, merge_chapter}]
arc_breakdown: list (JSONB)
created_at: datetime
UNIQUE(project_id, run_id, act_id)
```

#### IFRouteDefinitionModel（分支路线定义）
```python
__tablename__ = "if_route_definitions"
id: UUID PK
project_id: UUID FK projects
run_id: UUID FK if_generation_runs
route_id: str (VARCHAR 64) — "mainline"|"branch_warrior"|"hidden_destiny"
route_type: str (VARCHAR 32) — "mainline"|"branch"|"hidden"
title: str
description: str | None
branch_start_chapter: int | None
merge_chapter: int | None
entry_condition: dict (JSONB)  — {choice_ref, option_id, stat_gate}
merge_contract: dict (JSONB)   — {required_facts[], canonical_hook}
generation_status: str = "planned"  — "planned"|"generating"|"completed"|"failed"
chapter_count: int = 0
output_arc_file: str | None
created_at: datetime
UNIQUE(project_id, run_id, route_id)
```

#### IFWorldStateSnapshotModel（世界状态快照）
```python
__tablename__ = "if_world_state_snapshots"
id: UUID PK
project_id: UUID FK projects
run_id: UUID FK if_generation_runs
route_id: str = "mainline"
snapshot_chapter: int
arc_index: int
character_states: dict (JSONB)   — {char_id: {power_tier, known_enemies, ...}}
faction_states: dict (JSONB)
revealed_truths: list (JSONB)
active_threats: list (JSONB)
planted_unrevealed: list (JSONB)
power_rankings: list (JSONB)
world_summary: str | None        — 200字自然语言，直接注入prompt
created_at: datetime
UNIQUE(project_id, run_id, route_id, snapshot_chapter)
INDEX(project_id, run_id, route_id, snapshot_chapter DESC)
```

#### IFArcSummaryModel（Arc级摘要）
```python
__tablename__ = "if_arc_summaries"
id: UUID PK
project_id: UUID FK projects
run_id: UUID FK if_generation_runs
route_id: str = "mainline"
arc_index: int
chapter_start: int
chapter_end: int
act_id: str | None
protagonist_growth: str | None
relationship_changes: list (JSONB)
unresolved_threads: list (JSONB)
power_level_summary: str | None
next_arc_setup: str | None
open_clues: list (JSONB)
resolved_clues: list (JSONB)
created_at: datetime
UNIQUE(project_id, run_id, route_id, arc_index)
```

#### IFCanonFactModel（IF专用事实库，支持route感知）
```python
__tablename__ = "if_canon_facts"
id: UUID PK
project_id: UUID FK projects
run_id: UUID FK if_generation_runs
route_id: str = "all"  — "all"|"mainline"|"branch_X"
chapter_number: int
fact_type: str  — "chapter_summary"|"character_state"|"event"|"world_rule"
subject_label: str (VARCHAR 255)
fact_body: str
importance: str = "major"  — "critical"|"major"|"minor"
is_payoff_of_clue: str | None
created_at: datetime
INDEX(project_id, run_id, route_id, chapter_number, importance)
```

---

### Step 4 — migrations/versions/0009_if_branch_and_memory.py

创建迁移文件（参考0008的结构）：
- 创建上述5张新表（含所有索引）
- ALTER if_generation_runs 增加字段：
  - `total_routes INT NOT NULL DEFAULT 1`
  - `act_plan_json JSONB`
  - `generation_mode VARCHAR(32) NOT NULL DEFAULT 'simple'`

---

### Step 5 — if_prompts.py：新增Prompt函数

#### act_plan_prompt(bible, cfg) -> str
输入 bible + cfg（含act_count、target_chapters）
输出JSON：acts数组，每个act包含：
- act_id, title, chapter_range(start/end), act_goal
- core_theme, dominant_emotion, climax_chapter
- entry_state, exit_state
- payoff_promises[]（每幕必须兑现的承诺）
- branch_opportunities[]（{trigger_chapter, choice_theme, routes[], merge_chapter}）
- arc_breakdown[]（每幕细分的arc列表）

#### arc_plan_prompt_v2(bible, act_context, arc_summary_prev, arc_start, arc_end, arc_index, total_arcs, cfg) -> str
在现有arc_plan_prompt基础上：
- 增加 `act_context` 参数（当前幕的目标/状态/承诺）
- 增加 `arc_summary_prev` 参数（上一Arc的摘要，200字）
- 注入"本Arc必须回收的伏笔"和"本Arc可植入的新伏笔（≤2个）"
- 章节卡片增加 `is_power_moment: bool` 字段
- 强制约束：每5章必须有1个 is_power_moment=true

#### arc_summary_prompt(bible, arc_chapters, arc_cards, open_clues) -> str
输入：已生成的章节列表 + arc卡片 + 未回收伏笔
输出JSON：
- protagonist_growth, relationship_changes[], unresolved_threads[]
- power_level_summary, next_arc_setup
- open_clues[], resolved_clues[]

#### world_snapshot_prompt(bible, arc_summary, prev_snapshot) -> str
输入：Arc摘要 + 前一快照（如有）
输出JSON：
- character_states{}, faction_states{}
- revealed_truths[], active_threats[], planted_unrevealed[]
- power_rankings[], world_summary（200字）

#### branch_arc_plan_prompt(bible, route_def, fork_state_snapshot, merge_contract, cfg) -> str
专门为硬分支生成Arc卡片序列：
- 注入分叉点世界状态快照（fork_state_snapshot.world_summary）
- 注入汇合约束（merge_contract.required_facts[]）
- 注入"这条路线的独特爽点类型"
- 输出格式与arc_plan_prompt_v2相同（章节卡片数组）

---

### Step 6 — if_context.py（新文件）：ContextAssembler

```python
class ContextAssembler:
    """三级记忆系统，为每章生成注入分层上下文"""

    async def assemble(
        self,
        chapter_number: int,
        route_id: str,
        session: AsyncSession,
        project: ProjectModel,
        run_id: UUID,
        tier: str = "tiered",  # "basic"|"tiered"|"full"
    ) -> str:
        """
        hot  (每章注入): 最近5章摘要 + 当前Arc目标 ≈ 800 tokens
        warm (每Arc注入): 最近3个Arc摘要 + 活跃角色关系状态 ≈ 1200 tokens
        cold (每Act注入): 世界观摘要500字 + critical事实 + 已回收伏笔 ≈ 2000 tokens
        """
```

核心方法：
- `_load_hot_context(chapter_number, route_id, session)` — 查询IFCanonFactModel最近5章摘要
- `_load_warm_context(arc_index, route_id, session)` — 查询IFArcSummaryModel最近3条
- `_load_cold_context(act_id, session)` — 查询世界观快照 + critical facts
- `_get_current_world_snapshot(chapter_number, route_id, session)` — 取最近一个世界状态快照

---

### Step 7 — if_act_planner.py（新文件）：Acts级规划

```python
async def run_act_plan_phase(
    client: LLMClient,
    bible: dict,
    cfg: InteractiveFictionConfig,
    project: ProjectModel,
    run_id: UUID,
    session: AsyncSession,
    on_progress: Callable | None = None,
) -> list[dict]:
    """
    调用act_plan_prompt，生成全书Acts结构。
    结果写入IFActPlanModel（每幕一行）。
    返回acts列表供后续Arc Plan使用。
    """
```

---

### Step 8 — if_branch_engine.py（新文件）：硬分支引擎

```python
class BranchEngine:

    async def plan_branches(
        self,
        bible: dict,
        act_plans: list[dict],
        cfg: InteractiveFictionConfig,
        client: LLMClient,
        project: ProjectModel,
        run_id: UUID,
        session: AsyncSession,
    ) -> list[IFRouteDefinitionModel]:
        """
        从act_plans中提取branch_opportunities，
        为每个分支创建IFRouteDefinitionModel记录。
        """

    async def generate_branch_arc_plan(
        self,
        route_def: IFRouteDefinitionModel,
        fork_snapshot: IFWorldStateSnapshotModel,
        bible: dict,
        cfg: InteractiveFictionConfig,
        client: LLMClient,
    ) -> list[dict]:
        """
        调用branch_arc_plan_prompt，生成分支Arc章节卡片。
        """

    async def generate_branch_chapters(
        self,
        route_def: IFRouteDefinitionModel,
        branch_cards: list[dict],
        fork_snapshot: IFWorldStateSnapshotModel,
        bible: dict,
        cfg: InteractiveFictionConfig,
        client: LLMClient,
        project: ProjectModel,
        run_id: UUID,
        session: AsyncSession,
        output_dir: Path,
        on_progress: Callable | None = None,
    ) -> list[dict]:
        """
        真并行生成分支章节（复用主线的asyncio.gather逻辑）。
        上下文注入fork_snapshot.world_summary而非主线上下文。
        每章完成后写入IFCanonFactModel（route_id=route_def.route_id）。
        输出到 output_dir/branches/{route_id}/
        """

    def create_merge_chapter_context(
        self,
        route_def: IFRouteDefinitionModel,
        main_arc_card: dict,
    ) -> str:
        """
        为汇合章节生成叙事说明，主线Arc Plan时注入，
        使汇合章节能自然承接来自不同路线的玩家。
        """
```

---

### Step 9 — if_generation.py：核心Pipeline重构

#### 9a. 修复真并行（最高优先级）

将 `run_chapters_phase` / `run_chapters_phase_integrated` 中的批次内循环：
```python
# 旧（串行）
for card in batch:
    chapter = await _generate_single_chapter(card, ...)
    generated.append(chapter)
```
改为真正的 `asyncio.gather`：
```python
# 新（真并行）
async def _gen_one(card, prev_hook):
    ctx = await context_assembler.assemble(card["number"], route_id, ...)
    return await _generate_single_chapter(card, ctx, ...)

# 批次内并行（同批次共享前批最后一章的hook）
tasks = [_gen_one(card, batch_entry_hook) for card in batch]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

注意：同批次内各章节使用 `arc_card.ending_hook` 占位，批次完成后用实际生成的 next_chapter_hook 更新进度。

#### 9b. 新增 run_act_plan_phase 调用

在 `run_if_pipeline_integrated` 中，Story Bible完成后立即执行：
```python
if "act_plans" not in state:
    act_plans = await run_act_plan_phase(client, bible, cfg, ...)
    state["act_plans"] = act_plans
    _save_progress(output_dir, state)
```

#### 9c. arc_plan_prompt → arc_plan_prompt_v2

在 `run_arc_plan_phase` 中：
- 每个Arc传入对应的act_context（从act_plans中查找）
- 传入前一Arc的arc_summary（从IFArcSummaryModel中查询）

#### 9d. 每个Arc完成后自动生成Arc Summary + World Snapshot

```python
# Arc章节全部生成完毕后
arc_summary = await _generate_arc_summary(client, arc_chapters, arc_cards, ...)
await _store_arc_summary(session, arc_summary, ...)

world_snapshot = await _generate_world_snapshot(client, arc_summary, prev_snapshot, ...)
await _store_world_snapshot(session, world_snapshot, ...)
```

#### 9e. 新增 run_branch_phase 调用

在主线Chapter Gen完成后、Walkthrough之前：
```python
if cfg.enable_branches:
    if "branch_routes" not in state:
        branch_engine = BranchEngine()
        routes = await branch_engine.plan_branches(bible, act_plans, cfg, ...)
        for route in routes:
            fork_snapshot = await _get_snapshot_at(route.branch_start_chapter, session)
            branch_cards = await branch_engine.generate_branch_arc_plan(route, fork_snapshot, ...)
            branch_chapters = await branch_engine.generate_branch_chapters(route, branch_cards, fork_snapshot, ...)
            state["branch_routes"][route.route_id] = branch_chapters
            _save_progress(output_dir, state)
```

#### 9f. 扩展 if_progress.json 结构（断点续传支持分支）

```json
{
  "bible": {...},
  "act_plans": [...],
  "arc_plans": {"mainline": [...], "branch_warrior": [...]},
  "chapters": {"mainline": [...], "branch_warrior": [...]},
  "arc_summaries": {"mainline": {...}},
  "world_snapshots": {"mainline": {...}},
  "branch_routes": {"branch_warrior": {...}}
}
```

#### 9g. Assembly阶段扩展

`_assemble_story_package` 中：
- 输出的 `chapter_index.json` 增加 `routes` 数组（含所有路线定义和入口条件）
- 分支章节单独输出到 `output_dir/branches/{route_id}/` 目录
- 主线保持现有 `arc` 分片格式

---

### Step 10 — ContextAssembler 集成到 chapter_prompt

修改 `_generate_single_chapter_integrated`：

```python
context_assembler = ContextAssembler()
ctx_text = await context_assembler.assemble(
    chapter_number=card["number"],
    route_id=route_id,
    session=session,
    project=project,
    run_id=run_id,
    tier=cfg.context_mode,  # "basic"|"tiered"|"full"
)
prompt = chapter_prompt(card, prev_hook, bible, context_text=ctx_text)
```

三级上下文注入（按 tier 控制）：
- `basic`: 只注入最近5章摘要（现有行为）
- `tiered`: hot + warm（最近3 Arc摘要）
- `full`: hot + warm + cold（世界快照 + critical事实）

---

## 输出文件结构（1000章+硬分支）

```
output/{project_slug}/if/
├── if_progress.json
├── bible/
│   └── bible_{book_id}.json
├── build/
│   ├── book_{book_id}.json           — 元数据（含routes数组）
│   ├── chapter_index.json            — 全局索引+路由表
│   ├── walkthrough_{book_id}.json
│   ├── chapters/                     — 主线章节（arc分片）
│   │   ├── {book_id}_act01_arc01_ch0001-ch0050.json
│   │   └── ...
│   └── branches/                     — 硬分支章节
│       ├── branch_warrior/
│       │   └── {book_id}_branch_warrior_ch0101-ch0130.json
│       └── branch_schemer/
│           └── {book_id}_branch_schemer_ch0101-ch0125.json
```

---

## 性能估算（1000章）

| 阶段 | 旧耗时 | 新耗时 | 提升原因 |
|------|--------|--------|---------|
| Story Bible | 1min | 1.5min | 增加acts字段 |
| Act Plan（新增） | — | 1min | 1次LLM调用 |
| Arc Plans（×20） | 10min | 12min | 增加上文注入 |
| Chapter Gen（1000章，并行8） | ~180min | ~65min | **真正并行** |
| Arc Summaries（×20） | — | 5min | 每Arc 1次 |
| World Snapshots（×20） | — | 3min | 每Arc 1次 |
| Branch Chapters（~150章） | — | ~15min | 并行生成 |
| Walkthrough | 2min | 3min | — |
| **总计** | **~193min** | **~106min** | 在2小时内 |

---

## 分支规模推荐（1000章）

| 路线类型 | 数量 | 章节跨度 | 触发条件 |
|---------|------|---------|---------|
| 主线 | 1 | 1-1000 | 全部玩家 |
| 重要硬分支 | 2 | 各25-40章 | 第100、300章的关键选择 |
| 隐藏路线 | 1 | 10-20章 | 天命值≥80 或 特定选择序列 |
| **总生成量** | — | **约1150章** | — |

每条分支都有强制的 `merge_chapter`，不允许无限分叉。

---

## 质量保证机制

1. **爽点密度**：arc_plan_prompt_v2 强制每5章1个 `is_power_moment=true`
2. **伏笔追踪**：IFArcSummaryModel 的 open_clues/resolved_clues 跨Arc传递
3. **角色一致性**：world_snapshot 中的 character_states 注入后续Arc规划
4. **Acts承诺兑现**：每幕 payoff_promises 在Arc规划时作为硬约束注入
5. **汇合完整性**：MergeContract 定义分支汇入点的必要条件，防止叙事断层
