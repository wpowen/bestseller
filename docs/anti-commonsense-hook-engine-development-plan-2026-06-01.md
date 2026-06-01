# 反常识钩子引擎（Anti-Commonsense Hook Engine）融入开发方案

> 版本：2026-06-01 · 架构师视角 · Mode A 开发计划
> 输入调研：`/Users/owen/Downloads/deep-research-report (4).md`（反常识规则脑洞生成 Skill）
> 目标：把"一句话强钩子 / 命题强度可计算"的能力，**原生融入** BestSeller 的命题（premise/conception）→ 大纲（foundation/volume plan）→ 细纲（chapter contract）链路，而**不是**另起一个独立微服务。

---

## 0. 结论先行（TL;DR）

1. **不要按调研报告原样落地成一个独立 REST 微服务**（它自带 DB、向量库、审核层、前端台）。这套基础设施在本框架里**已经存在**（LLM 网关、`deduplication.py`、pgvector、gate 家族、合规 gate）。原样落地等于重复造轮子且割裂数据。
2. **正确定位**：调研报告描述的本质是一个**命题层（premise-stage）的"机制化脑洞 + 强度评分"能力**。它应该作为**一个内部 service 模块 + 一个 gate + 一个领域模型**，挂进现有的 `conception → foundation_plan` 链路，复用 `concept_leap.py` 的确定性生成范式。
3. **三件目前框架缺失、必须新增的东西**：
   - **A. 反常识机制库**（8 类原型：作死变强 / 限制消费 / 情绪值 / 苟道反套路 / 迪化误解 / 第四天灾 / 规则怪谈 / 职业反差）—— 现有 `concept_leap` 是"跨域素材池"，`story_design_grammars` 是"叙事语法"，都**不含**"欲望→反转"这类反常识机制模板。
   - **B. 结构化 HookSpec**（base_desire / reversal / reward / constraints / anti_cheat / cost / misunderstanding / arc_engine / one_liner）—— 现在 premise 只是一个**自由字符串**，这些字段无处承载。
   - **C. H_norm 命题强度评分 + gate**（Δ/R/C/P/M/E/L）—— 现有 gate 多在大纲/章/项目层，**命题层没有强度门禁**。这正是用户要的"钩子的思路要求非常强"的可计算闸口。
4. **融入而非旁挂的关键**：HookSpec 的字段必须**向下传播**到现有下游字段（见 §5 传播契约），让一句话钩子真正约束世界规则、章节契约、误解/代价/反作弊机制，而不是只在命题阶段生成一段漂亮文案后就被丢弃。

---

## 1. 现状盘点（融入点定位）

### 1.1 现有命题链路

```
quickstart (web/server.py)
  └─ genre_creativity.get_genre_creative_direction()      ← 已有 logline/opening_hook/conflict_engine/novelty_pressure/distilled_mechanisms/anti_cliche_guardrails
       └─ run_conception_pipeline()  (services/conception.py)   ← 产出 premise:str + title + writing_profile + commercial_brief
            └─ generate_foundation_plan(premise: str)  (services/planner.py:15681)
                 └─ BookSpec → WorldSpec → CastSpec → VolumePlan → (per-volume) ChapterOutline
```

**关键观察**：
- `GenreCreativeDirection`（`services/genre_creativity.py`）**已经有** `logline` / `opening_hook` / `conflict_engine` / `novelty_pressure` / `distilled_mechanisms` / `anti_cliche_guardrails` 字段，但**只在 quickstart UI 消费**（`web/server.py` + `novel_quickstart.html`），**没有进入 premise 的结构化生成**。
- `run_conception_pipeline()` 产出的 `premise` 是**纯字符串**（`ConceptionResult.premise: str`）。下游 `generate_foundation_plan(premise: str)` 也只接收字符串。**没有结构化命题对象**。
- `concept_leap.py` 已是一个**确定性脑洞生成器**：8 个跨域素材池 → 4 路 mashup → novelty/coherence/saturation/combined 评分 → top-k 候选 + premise_hint。**这是本方案生成器的现成范式**，但它解决的是"跨域新颖度"，**不解决"反常识机制强度"**。两者正交、互补。

### 1.2 现有 gate 范式（直接复用）

`evaluate_*_gate(...) -> *GateReport`（Finding + threshold + verdict）已是成熟模式：
`opening_hook_density_gate` / `premium_book_gate` / `methodology_application_gate` / `distilled_strategy_gate` / `reverse_outline_gate` / `worldview_compliance_gate` …
planner 通过 `_run_*_gate(...)` 包装挂入（见 `planner.py:13811+`）。**新 gate 照抄此形状即可。**

### 1.3 现有下游可承载字段（传播目标，已存在，无需新建）

| 调研报告字段 | 已存在的下游承载字段 | 位置 |
|---|---|---|
| constraints（限制） | `WorldRuleInput.story_consequence` / `power_system.hard_limits` | `domain/story_bible.py:417,438` |
| anti_cheat（反作弊） | `WorldRuleInput.exploitation_potential`（反向约束）/ `ChapterContractRead.conflict_buffs` | `domain/story_bible.py:418` / `domain/narrative.py:100` |
| cost_type（代价） | `ChapterContractRead.conflict_stakes` / payoff ledger / DeferredReveal | `domain/narrative.py:99` |
| misunderstanding（误解/迪化） | `SceneContractRead.dramatic_irony_intent` / `reveal_mode` / `ReaderKnowledgeEntryRead.audience` | `domain/narrative.py:130,158,274` |
| one_liner（一句话钩子） | logline / reader_promise / listing.logline | `genre_creativity` / 上架资料 |
| arc_engine（连载迭代） | VolumeFrontier / ActPlan escalation_path | `world_expansion.py` / planning.md §7 |

**结论**：传播目标几乎全部已存在。融入工作 = 新增"命题层结构化 + 评分"，再把字段**映射注入**到这些既有承载点。

---

## 2. 目标与非目标

### 2.1 目标
- 命题阶段产出**结构化、可评分、可去重**的反常识钩子，一句话 `one_liner` 强度可计算（H_norm）。
- H_norm 作为**命题门禁**：低于阈值的命题不进入 foundation_plan（或强制 LLM 重写补强限制/代价/误解）。
- 钩子的机制字段**向下约束**世界规则、章节契约、误解/代价机制——形成"命题→大纲→细纲"一致性。
- 兼容 Mode A（仓库内调用）与 Mode B（直接写小说时 PLAN_PREMISE 阶段自动运行）。

### 2.2 非目标（明确不做）
- ❌ 不新建独立 REST 微服务 / 独立 DB / 独立向量库（复用现有）。
- ❌ 不重建合规/版权审核栈（复用现有 compliance gate + `distillation_privacy_gate`）。
- ❌ 不把 H_norm 当唯一排序器（与 `concept_leap` 的 novelty/saturation 联合排序，避免单指标过拟合）。
- ❌ 不抓取平台正文做训练（沿用现有蒸馏库的授权边界）。

---

## 3. 架构设计

### 3.1 新增模块总览

```
                         ┌─────────────────────────────────────────┐
                         │ domain/anti_commonsense_hook.py          │  ← 领域模型（新）
                         │   HookMechanism / HookSpec / HookScore   │
                         │   HookCandidate / HookStrengthReport     │
                         └─────────────────────────────────────────┘
                                          ▲
   ┌──────────────────────────────────────┼───────────────────────────────────┐
   │                                       │                                   │
┌──┴───────────────────────┐  ┌────────────┴─────────────┐  ┌──────────────────┴────────┐
│ services/                │  │ services/                │  │ services/                  │
│ anti_commonsense_        │  │ anti_commonsense_hook.py │  │ hook_strength_gate.py      │
│ mechanisms.py            │  │  generate_hook_candidates│  │  score_hook()              │
│  8 机制原型库(数据)        │→ │  (确定性, 仿 concept_leap)│→ │  evaluate_hook_strength_   │
│  欲望→反转/奖励/限制/      │  │  + LLM 扩写 one_liner/    │  │    gate() -> Report        │
│  反作弊/代价/误解 矩阵      │  │    arc_engine            │  │  (Δ/R/C/P/M/E/L → H_norm)  │
└──────────────────────────┘  └──────────────────────────┘  └────────────────────────────┘
                                          │                              ▲
                                          ▼                              │
                         ┌─────────────────────────────────────────┐    │ _run_hook_strength_gate()
                         │ services/conception.py (扩展)             │    │ 挂入 generate_foundation_plan
                         │  run_conception_pipeline(hook=...)        │────┘
                         │  → ConceptionResult.hook_spec: HookSpec   │
                         └─────────────────────────────────────────┘
                                          │ HookSpec 注入 project.metadata_json["hook_spec"]
                                          ▼
                         ┌─────────────────────────────────────────┐
                         │ services/hook_propagation.py (新)         │  ← 传播契约
                         │  constraints → world rules                │
                         │  anti_cheat  → exploitation_potential     │
                         │  cost        → conflict_stakes/payoff     │
                         │  misunderstanding → dramatic_irony/reveal │
                         │  arc_engine  → VolumeFrontier/ActPlan      │
                         └─────────────────────────────────────────┘
```

### 3.2 领域模型 `domain/anti_commonsense_hook.py`

全部 `frozen=True` pydantic（遵循不可变约定）：

```python
class HookMechanism(BaseModel, frozen=True):
    """8 类反常识机制原型之一（数据驱动，存 YAML）。"""
    key: str                      # death_grows / forced_loss / emotion_value / hide_anti_trope /
                                  # misunderstanding / fourth_disaster / rule_horror / profession_reversal
    label: str
    base_desire_pool: list[str]   # 可适配的正常欲望
    reversal_template: str        # "必须死/必须亏/必须被骂…"
    reward_pool: list[str]
    constraint_dimensions: list[str]   # 时间/地点/对象/次数/方式/禁止项/说明义务
    anti_cheat_rules: list[str]
    cost_templates: list[str]
    misunderstanding_patterns: list[str]
    arc_escalation_axes: list[str]     # 场景/规则/代价/误解/真相 升级轴
    saturation_score: float            # 0..1，复用 concept_leap 的饱和度抑制
    forbidden_overlaps: list[str]      # 与现有 forbidden_defaults 对齐

class HookSpec(BaseModel, frozen=True):
    """一个结构化命题（向下传播的契约对象）。"""
    mechanism_key: str
    genre: str
    setting_locale: str | None
    protagonist_role: str | None
    base_desire: str
    reversal: str
    rewards: list[str]
    constraints: dict[str, str]        # {time, location, object, count, method, ban, must_explain}
    anti_cheat: list[str]
    costs: list[str]
    misunderstanding: str | None
    arc_engine: list[str]              # 连载迭代轴
    one_liner: str                     # 一句话钩子（25–60 字目标）
    core_rule: str

class HookScore(BaseModel, frozen=True):
    delta: int; reward: int; constraint: int; penalty: int
    misunderstanding: int; expansion: int; learning_cost: int
    h_norm: float                      # 归一化（见 §4）
    verdict: str                       # reject / seed / review / expand

class HookCandidate(BaseModel, frozen=True):
    spec: HookSpec
    score: HookScore
    novelty_score: float               # 来自 concept_leap 风格的饱和度抑制
    duplicate_risk: float              # 来自 deduplication.py
    combined_rank: float               # H_norm × novelty 的联合分

class HookStrengthGateReport(BaseModel, frozen=True):
    findings: list[HookStrengthFinding]
    h_norm: float
    passed: bool
    rewrite_suggestions: list[str]
```

### 3.3 机制库 `services/anti_commonsense_mechanisms.py`

- 8 类机制以**数据**形式定义（参照 `concept_leap.DEFAULT_CONCEPT_POOLS` 的写法），但**优先放 `config/` 下 YAML**（`config/hook_mechanisms.yaml`），代码只做加载 + 校验 + `@lru_cache`。
- 饱和度（saturation_score）应可由番茄市场数据刷新（与 `concept_leap` 注释中提到的刷新机制统一）。
- 提供 `list_mechanisms()` / `get_mechanism(key)` / `select_mechanisms_for_genre(genre)`（按题材过滤，复用 `genre_profile` 的偏好优先级逻辑）。

### 3.4 生成器 `services/anti_commonsense_hook.py`

- **确定性骨架**（无 LLM）：`generate_hook_candidates(genre, locale, role, base_desire?, mechanism_keys?, count, seed)` —— 机制 × 欲望 × 限制矩阵采样，仿 `generate_concept_leap` 的 `rng.Random(seed)` 可复现实现。先产出**结构化 HookSpec 骨架**（constraints/anti_cheat/cost 槽位填充）。
- **LLM 扩写层**（经 `complete_text`，role=`planner`）：把骨架扩成 `one_liner` / `core_rule` / `arc_engine` 文案。必须传 `project_id` / `workflow_run_id` 审计。
- **评分 + 去重**：对每个候选调 `score_hook()`（§4）与 `deduplication.py`，产出 `HookCandidate.combined_rank` 排序。
- 复用 `concept_leap` 的 `forbidden_overlap` / saturation 抑制，避免与现有榜单/已用机制撞车。

### 3.5 评分器 + gate `services/hook_strength_gate.py`

- `score_hook(spec: HookSpec | str, *, platform_profile) -> HookScore`：
  - 对**结构化 HookSpec**：从字段直接量化（constraints 条数→C，cost 条数/强度→P，misunderstanding 存在性+可持续→M，arc_engine 轴数→E，one_liner 长度/复杂度→L，reversal 与 base_desire 对冲度→Δ，reward 等级→R）。规则化为主，**可选** LLM 辅助打分（role=`critic`, temp 0.25，确定性）。
  - 对**自由字符串命题**（调研报告的 evaluate 用例）：先 LLM 抽取成 HookSpec，再评分 + 给 `rewrite_suggestions`。
- `evaluate_hook_strength_gate(spec, settings) -> HookStrengthGateReport`：照 `premium_book_gate` 的 Finding/threshold 形状。阈值见 §4.2，可配置。

---

## 4. H_norm 评分（工程化）

### 4.1 公式（采用调研报告的归一化版 + 钳制）
```
H_norm = 100 × (Δ/10)(R/10)(C/10)(P/10)(M/10)(E/10) ÷ max(0.3, L/10)
```
- 乘法式天然放大/坍缩，**必须钳制** `L` 下界（已含 `max(0.3, …)`），并对最终值 `clamp(0, 100)`。
- **不单独用 H_norm 排序**：`combined_rank = w1·norm(H_norm) + w2·novelty − w3·duplicate_risk`，权重进 `config`。这避免"高 H_norm 但已烂大街"的命题排到前面（与 `concept_leap` 的 saturation 思路一致）。

### 4.2 阈值（进 `config/default.yaml` → `hook_engine`，env `BESTSELLER__HOOK_ENGINE__*`）

| H_norm | verdict | 行为 |
|---|---|---|
| < 15 | reject | 不入命题池 |
| 15–30 | seed | 入种子池，**gate 软警告**：要求补限制/代价 |
| 30–45 | review | **gate 通过**，进 foundation |
| > 45 | expand | 优先扩写 |

- `generate_foundation_plan` 默认要求 `h_norm ≥ 30`（`hook_engine.min_h_norm`，可关）。不达标 → 触发一次 LLM 命题补强（注入 `rewrite_suggestions`）后重评；二次仍不达标 → 记 warning 放行（不阻塞主流程，与现有 gate 的非阻塞惯例一致）。

---

## 5. 传播契约（融入的核心）`services/hook_propagation.py`

> 这是"融入 vs 旁挂"的分水岭。HookSpec 必须把字段映射注入到下游既有承载点。

| HookSpec 字段 | 注入目标 | 机制 |
|---|---|---|
| `constraints{location/time/ban…}` | `WorldRuleInput.story_consequence` + `power_system.hard_limits` | conception 后、WorldSpec 生成时把限制作为**硬世界规则**写入 |
| `anti_cheat[]` | `WorldRuleInput.exploitation_potential`（取反：明确"不可被这样利用"）+ 章节契约 `conflict_buffs` | 防止设定空转 |
| `costs[]` | `ChapterContractRead.conflict_stakes` + payoff ledger 的 cost 条目 + DeferredReveal 升级链 | 每次"成功"必须挂代价 |
| `misunderstanding` | `SceneContractRead.dramatic_irony_intent` + `reveal_mode` + `ReaderKnowledgeEntryRead.audience=reader_only` | 真实意图/外界解读双层 |
| `arc_engine[]` | VolumeFrontier 升级轴 + ActPlan `escalation_path` | 场景/规则/代价/误解/真相逐卷升级 |
| `one_liner` | logline / reader_promise / 上架 listing.logline | 一句话钩子贯穿对外文案 |
| `core_rule` | BookSpec 核心承诺 + 首卷 3 章内必须说清（对齐 planning.md §质量阈值"首卷起盘"） |

实现方式：`run_conception_pipeline` 把选定的 `HookSpec` 写入 `project.metadata_json["hook_spec"]`；`generate_foundation_plan` 在 WorldSpec / VolumePlan / ChapterOutline 生成的 **prompt 注入**阶段读取并渲染成约束块（参照现有 `material_reference_block` / `distilled_strategy_card` 的 stash→注入模式，见 `planner.py:15724` 附近）。

---

## 6. 与 Mode B（直接写小说 skill）的对接

- 在 `.claude/skills/bestseller-framework/` 新增子文件 `hook-engine.md`，并在 `planning.md §8 PLAN_PREMISE` 增加一步："命题阶段先跑反常识钩子生成 + H_norm 评分，选定 HookSpec 后再展开 BookSpec"。
- recipe（30/100/500…）在 premise 锁定前插入 hook 选择；短篇（≤50 章）至少 1 个机制，长篇可叠加 2 类机制（对应报告"机制积木"观点）。
- `invariants.md` 增加红线：命题 H_norm < 15 不得进入写作；HookSpec 的 constraints/cost 必须在首卷兑现。

---

## 7. 分期与里程碑（TDD）

| 阶段 | 周期 | 交付 | 验收 |
|---|---|---|---|
| **MVP（确定性内核）** | 1 周 | `domain/anti_commonsense_hook.py` + `config/hook_mechanisms.yaml`(8 机制) + `anti_commonsense_mechanisms.py` + `hook_strength_gate.score_hook/evaluate_*`（纯规则，无 LLM）+ 单测 | `score_hook` 确定性可复现；8 机制库完整性测试；阈值 verdict 正确；覆盖率 ≥ 80% |
| **Beta（生成 + 融入）** | 1.5 周 | `anti_commonsense_hook.generate_hook_candidates`（含 LLM 扩写）+ `conception` 扩展产出 `HookSpec` + `hook_propagation.py` + `_run_hook_strength_gate` 挂入 `generate_foundation_plan` | 命题→WorldSpec 限制注入打通；一句话钩子贯穿 logline；gate 软门禁生效；evaluate 用例（自由字符串→评分+建议）可用 |
| **1.0（去重/遥测/UI）** | 1 周 | `deduplication` 接入 `duplicate_risk` + `combined_rank` 联合排序 + quickstart UI 暴露"钩子候选+评分+换一批" + 遥测（仿 `distilled_strategy_telemetry`） | 去重命中可观测；编辑可在 quickstart 选钩子；H_norm/采纳率埋点回流 |

每阶段先写测试（RED）再实现（GREEN）。新 gate / 新 service 必须有单测；planner 集成点加集成测试。新表（若需要持久化 HookSpec 版本）配 Alembic；当前阶段先用 `project.metadata_json["hook_spec"]` 承载，**不立刻建表**。

---

## 8. 风险与取舍

| 风险 | 说明 | 缓解 |
|---|---|---|
| H_norm 单指标过拟合 | 乘法式易把"猎奇但难连载"的命题顶上去 | 联合 novelty + duplicate_risk 排序；gate 仅软门禁，不硬阻塞 |
| 机制库与现有 `concept_leap` / `story_design_grammars` 概念重叠 | 三套"创意源"易混淆职责 | 明确分工：concept_leap=跨域素材新颖度；grammars=叙事结构；**hook=欲望反转机制强度**。三者在 conception prompt 中分层注入，不互相覆盖 |
| 传播不到位 → 退化成"只生成漂亮文案" | 这是最大失败模式 | §5 传播契约是验收硬指标：WorldSpec 必须出现来自 constraints 的硬规则，ChapterContract 必须出现来自 cost 的 stakes |
| LLM 命题抽取不稳定 | evaluate 自由字符串→HookSpec 可能跑偏 | 规则化打分为主、LLM 为辅；抽取失败回退到纯字符串 + 低置信度标记 |
| planner.py 已 16k 行，集成点臃肿 | 不宜再往里堆逻辑 | 新逻辑全部放独立 service，planner 只加薄 `_run_hook_strength_gate` 包装（≤ 30 行），遵循"多小文件"约定 |
| 合规（对公众生成服务） | 报告强调网信合规 | 本能力是**内部工具**，复用既有 compliance/privacy gate；若未来对外开放再补审核层，不在本期 |

---

## 9. 落地文件清单（预估）

**新增**
- `src/bestseller/domain/anti_commonsense_hook.py`
- `src/bestseller/services/anti_commonsense_mechanisms.py`
- `src/bestseller/services/anti_commonsense_hook.py`
- `src/bestseller/services/hook_strength_gate.py`
- `src/bestseller/services/hook_propagation.py`
- `config/hook_mechanisms.yaml`
- `tests/unit/test_hook_strength_gate.py` / `test_anti_commonsense_hook.py` / `test_hook_propagation.py`
- `.claude/skills/bestseller-framework/hook-engine.md`

**修改（薄改动）**
- `src/bestseller/services/conception.py` —— `ConceptionResult` 加 `hook_spec`；pipeline 接收/产出 HookSpec
- `src/bestseller/services/planner.py` —— `_run_hook_strength_gate` + WorldSpec/Volume/Chapter 注入点读 `hook_spec`
- `src/bestseller/settings.py` + `config/default.yaml` —— `hook_engine` 配置段
- `src/bestseller/web/server.py` + `novel_quickstart.html` —— quickstart 暴露钩子候选（1.0 阶段）
- `.claude/skills/bestseller-framework/planning.md` / `invariants.md` —— PLAN_PREMISE 增补

---

## 10. 一句话总纲

把调研报告的"独立脑洞工厂微服务"，**降维成本框架命题层的一个确定性生成器 + 一个强度 gate + 一份结构化 HookSpec 传播契约**——复用 `concept_leap` 的生成范式、gate 家族的门禁范式、既有世界规则/章节契约的承载字段，让"一句话强钩子"从命题一路硬约束到细纲，而不是生成完就被丢弃。
