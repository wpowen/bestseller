# 方法论融合架构方案与开发计划

> 本文是**架构定稿 + 可落地开发方案**。前置依据：`methodology-closed-loop-audit-and-fusion-conclusion.md`（实证审计）与 `data/methodology_unified/inventory_summary.md`（224 条规则清单）。
>
> 设计目标：让现有 224 条方法论从"71% runtime_dormant"变成"按合理位置、合理方式、形成可验证闭环地用起来"。
>
> 版本：v1　日期：2026-05-29　视角：框架架构师

---

## 第一部分：架构总览

### 1.1 核心架构决策——方法论传动轴（Methodology Lineage Spine）

审计已证明：框架不缺方法论，缺的是一根贯穿全流程的传动轴。本方案的**唯一核心抽象**就是这根轴：

```
                    ┌─────────────────────────────────────────────┐
                    │   MethodologyLineage (一次选定，全程携带)     │
                    └─────────────────────────────────────────────┘
   planner 选定 ───▶ 写入 chapter_contract.methodology_lineage
        │                              │
        │                              ▼
        │                    draft 读取(只读)→ 注入 prose prompt
        │                              │
        │                              ▼
        │                    review 读取(只读)→ 检查 evidence
        │                              │
        │                              ▼
        │                    repair 读取(只读)→ 按失败 slot 定向修复
        │                              │
        ▼                              ▼
   selection engine            health 聚合 → 晋级/淘汰
   (确定性，非抽卡)
```

**铁律：选择只发生一次（planner），下游一律只读、禁止重新 select。** 这一条同时根治审计发现的三大病：lineage 破裂、抽卡漂移、上下文爆炸。

参照范式：`hook_ledger v2` 已经是这条轴的可运行样板（planner 写 → review 审 → repair 修）。其余所有域照此复刻。

### 1.2 三个分层

```
┌──────────────────────────────────────────────────────────────┐
│ L1 知识层  Knowledge Layer                                      │
│   224 条 inventory 规则（不再增加）+ 11 个 capability slot       │
│   每条规则有: craft_function / binding_artifact / verifiability  │
│              / indicator_targets / source_lineage                │
├──────────────────────────────────────────────────────────────┤
│ L2 决策层  Decision Layer (selection engine)                    │
│   输入: stage, scope, chapter_role, genre, weak_indicators,budget│
│   输出: MethodologyLineage (确定性、预算受限、可解释)             │
├──────────────────────────────────────────────────────────────┤
│ L3 执行层  Execution Layer                                       │
│   planner 写 contract → draft 落地 → review 验 evidence          │
│   → repair 定向修 → health 统计晋级                              │
└──────────────────────────────────────────────────────────────┘
```

### 1.3 核心数据模型

```python
# src/bestseller/services/methodology_lineage.py  (新建)

@dataclass(frozen=True)
class AppliedMethodology:
    rule_id: str                 # inventory 规则 id（如 wm.hook_system.types）
    slot: str                    # 11 个 capability slot 之一
    craft_function: str          # = slot（保留冗余便于查询）
    target_artifact_path: str    # 落点：causal_contract.protagonist_choice / scene.pov_distance / hook_ledger
    application_hint: str        # 项目特化的"怎么用"（genre-adapted，进 prompt）
    evidence_fields: tuple[str, ...]   # review 检查正文是否兑现的字段
    verifiability: str           # "strict" | "heuristic" | "advisory"
    gate_mode: str               # "advisory" | "warn" | "block"
    indicator_targets: tuple[str, ...] # 该规则服务的确定性指标
    source_lineage: str          # "writing_methodology" | "book_core" | "distilled" | "platform"
    why_selected: str            # 可解释性：为何本章选它

@dataclass(frozen=True)
class MethodologyLineage:
    chapter_no: int
    genre_profile: str
    chapter_role: str            # opening/setup/escalation/pivot/payoff/climax/denouement
    selected: tuple[AppliedMethodology, ...]
    selection_seed: str          # 决定性复现用
    budget_tokens: int
    budget_cards: int

    def for_slot(self, slot: str) -> tuple[AppliedMethodology, ...]: ...
    def for_stage(self, stage: str) -> tuple[AppliedMethodology, ...]: ...
    def strict_only(self) -> tuple[AppliedMethodology, ...]: ...
```

持久化：序列化进 `chapter_contract.methodology_lineage`（ChapterContractModel.metadata_json 子字段，**不需 schema 迁移**，沿用现有 JSONB）。

---

## 第二部分：11 个 Capability Slot 与流程闭环映射

### 2.1 Slot 定义（来自 inventory craft_function，hook+ending 已合并）

| # | slot | 作用层 | 选定阶段(owner) | 物理落点 artifact | verifiability | 当前闭环 |
|---|---|---|---|---|---|---|
| 1 | premise_engine | 人物设定 | conception | book_outline.premise / reader_promise | heuristic | 🟡 |
| 2 | character_change_tracker | 人物设定 | conception→outline | cast.arc + chapter_contract.character_delta | heuristic | 🔴 断头 |
| 3 | worldview_theme | 人物设定 | conception | world_rule_refs / key_reveals | heuristic | 🟡 |
| 4 | scene_causality_engine | 故事叙事 | outline_chapter | causal_contract.* + scene methodology_contract | strict+heuristic | 🟡 半闭环 |
| 5 | hook_ledger | 故事叙事 | outline_chapter | hook_ledger（账本） | strict | 🟢 已闭环 |
| 6 | payoff_ledger | 故事叙事 | outline_volume→chapter | payoff_ledger（账本，待建） | strict | 🔴 未建 |
| 7 | pacing_compression_engine | 故事叙事 | outline_volume | volume_plan.compression_curve | heuristic | 🟡 |
| 8 | opening_three_function | 故事叙事 | outline_chapter(前3章) | chapter_outline[0..2] + 平台门禁 | strict | 🟢 |
| 9 | pov_distance_controller | 正文创作 | (outline选定)prose落地 | scene.pov_spec + prose gate | strict+heuristic | 🟡 |
| 10 | dialogue_subtext_engine | 正文创作 | (outline选定)prose落地 | scene.dialogue_spec + dialogue gate | heuristic | 🟡 |
| 11 | revision_repair_engine | 修复 | repair | RewriteTask.repair_domain | strict | 🟡 |

### 2.2 流程逐阶段融入表（哪个环节用哪个方法论、怎么用、如何闭环）

| 流程阶段 | 选定/消费 | 用哪些 slot | 用法（how） | 闭环动作（evidence/gate） |
|---|---|---|---|---|
| **Conception / CastSpec** | 选定 | 1,2,3 | 写入 premise、want/need/flaw、世界规则到 BookSpec/CastSpec | 立项 readiness gate 检查字段齐全 |
| **Volume Plan** | 选定 | 6,7 | 卷级 payoff 队列 + 压缩曲线写入 volume_plan | volume_quality_judge 检查 payoff 节奏 |
| **Chapter Outline** | **选定(lineage owner)** | 2,4,5,6,8 | selection engine 产出 MethodologyLineage→写 chapter_contract；causal_contract + hook/payoff delta | chapter_causality_gate + hook_ledger_audit + outline_readiness |
| **Pre-draft Validation** | 消费 | 4,5,9,10 | 校验 scene methodology_contract 字段可写；缺失则 repair 补齐 | validate_scene_contract_pre_draft（strict 字段存在性） |
| **Scene Draft** | **消费(只读)** | 4,9,10 + lineage hint | 注入 lineage 的 application_hint + target 字段（≤6条），**禁止重新 select** | 无（落地阶段，evidence 留给 review） |
| **Scene Review** | **消费(只读)** | 4,9,10 | 按 lineage.evidence_fields 检查正文是否兑现 | evidence 化 review + deterministic prose gate |
| **Scene Rewrite** | 消费 | 11 + 失败 slot | rewrite_task.repair_domain = 失败 slot；定向改写 | rewrite 后重审同一 evidence |
| **Chapter Review** | 消费 | 5,6,8 | hook/payoff ledger audit + opening gate | merge ledger audit → chapter rewrite |
| **Chapter Rewrite** | 消费 | 11 + 失败 slot | repair_domain 标记，统计失败类型 | 重审 |
| **Knowledge Extraction** | 写状态 | 2,5,6 | 把未偿付 hook、人物变化、关系债写入 snapshot | 供后续章 lineage 上下文 |
| **Health Audit** | 聚合 | all | 统计每 slot 的 coverage/evidence/failure | dormant→active 晋级名单 |

### 2.3 关键设计原则（防止重蹈覆辙）

1. **规划型 slot（2,4,5,6,7,8）的选择和落点必须在 outline 阶段完成**，prose 阶段只做表达落地。A/B/C 已证伪"prose 阶段补 setup_payoff"。
2. **表达型 slot（9,10）虽在 prose 落地，但其选择仍由 outline 阶段的 selection engine 决定并写入 lineage**，prose 不自选。
3. **每条 AppliedMethodology 必须带 target_artifact_path 和 evidence_fields**，否则不允许进 lineage（只能进 advisory prompt）。这保证"可验证"。

---

## 第三部分：Selection Engine（270→N 的确定性筛选）

### 3.1 为什么不是抽卡

审计铁证：当前 planner 不选、draft/review 各自抽。新引擎是**纯函数**：

```python
def select_methodology_lineage(
    *,
    stage: str,                      # 固定为 outline_chapter（唯一 owner）
    scope: str,
    chapter_no: int,
    chapter_role: str,               # 由 volume_plan 节拍推导
    genre_profile: str,
    weak_indicators: Mapping[str, float],  # 来自滚动 critic 历史
    budget_cards: int = 6,
    budget_tokens: int = 900,
) -> MethodologyLineage:
    ...
```

### 3.2 选择算法（确定性 5 步）

```
1. 槽位需求：chapter_role → slot 优先级画像（must/should/nice）
   例: climax 章 = {payoff_ledger:must, scene_causality:must, hook_ledger:must,
                    pov:should, dialogue:nice}
       opening 章 = {opening_three_function:must, hook_ledger:must, character:should}

2. 候选过滤：对每个 must/should slot，从 inventory 取 stage/scope 匹配的规则

3. 排序：verifiability(strict优先) > genre_fit > coverage_value(高复用母题)
   每个 slot 取 top-1~2 代表（避免同 slot 堆叠）

4. 弱项补强：weak_indicators 命中的 slot → gate_mode 升级(advisory→warn→block)
   并强制纳入（即使该 chapter_role 原本是 nice）

5. 预算与多样性裁剪：总数 ≤ budget_cards；同 source_lineage 不超过 N/2；
   按 must>should>nice 截断。seed=hash(chapter_no,genre,role) 保证可复现
```

### 3.3 上下文适配（传什么信息给每个位置）

| 位置 | 携带的上下文 | 不携带 |
|---|---|---|
| planner prompt | 全量 must/should 规则的 application_hint + 章纲字段要求 | 原始书名/长引文 |
| draft prompt | 仅 lineage 中 prose 相关（slot 4,9,10）的 hint + target，≤6 条 | 规划型 slot 的解释、其他章 lineage |
| review prompt | lineage 的 evidence_fields + 期望值 | application_hint（review 不需要"怎么写"） |
| repair prompt | 失败 slot 的 hint + 失败 evidence | 未失败 slot |

**防爆炸核心**：planner 一次性承载全量（低频、高价值），prose/review 只携带蒸馏后的 lineage 子集（高频、必须瘦）。

---

## 第四部分：验证与指标框架（每个环节都要可衡量）

### 4.1 三级验证（对应 verifiability）

| verifiability | 验证方式 | 可否 block | 实现 |
|---|---|---|---|
| **strict** | 非 LLM 确定性检查（字段存在性、计数关系、账本闭合） | 可 block | gate 函数（hook_ledger_audit 范式） |
| **heuristic** | LLM judge 按 evidence_fields 打分 | 仅 warn | review evidence judge |
| **advisory** | 仅注入 prompt，telemetry 记录 | 不 block | 计数统计 |

### 4.2 指标注册表（扩展现有，每 slot 至少 1 主指标）

| slot | 主指标 | 验证层 | 现状 |
|---|---|---|---|
| scene_causality_engine | scene_causality_score | strict+heuristic | ✅ 已有 |
| hook_ledger | hook_ledger_closure_rate | strict | ✅ 已有(v2) |
| payoff_ledger | payoff_ledger_closure_rate | strict | 🔴 待建 |
| pov_distance_controller | pov_stability_score / drift_ratio | strict+heuristic | ✅ 已有 |
| dialogue_subtext_engine | dialogue_subtext_score | heuristic | 🟡 待加 |
| character_change_tracker | character_change_score | heuristic | 🔴 待加 |
| opening_three_function | opening gate findings | strict | ✅ 已有 |
| emotion/pacing | compression_ratio_compliance | heuristic | 🟡 |
| ending(并入hook) | ending_hook_score | strict | ✅ 已有 |

新增指标必须先进 registry 再被引用（`data/methodology_unified/inventory_format.md` 已立此规矩）。

### 4.3 闭环验证 = 每章 scorecard + 滚动趋势 + A/B/C

```
单章: 生成 → 跑 strict gate + heuristic judge → 产出 per-slot scorecard
       → 写入 chapter snapshot（evidence + 分数）
滚动: 每 K 章聚合 → 各 slot rolling mean + variance
       → 弱项指标回灌 selection engine 的 weak_indicators
对比: A/B/C harness（见 4.4）
```

### 4.4 A/B/C 验证设计（吸取上轮单样本失败教训）

| 组 | 配置 | 验证目标 |
|---|---|---|
| A baseline | 无 lineage（现状抽卡） | 控制 |
| B lineage-only | 传动轴接通，但不做弱项补强 | 验证"一次选定+下游沿用"本身的收益 |
| C lineage+reinforce | 传动轴 + 弱项指标驱动 slot 强度调节 | 验证补强闭环 |

- 规模：3 题材 × 5 样本 × 每样本 2 章（跨章才验得出 ledger 闭合）= 90 章
- 主判据：**C 的指标方差 < B < A**（稳定性优先于均值）；setup_payoff 跨章闭合率 C > 0.70
- "按下葫芦浮起瓢"次数：A 多、C 应消失
- 主评估器：确定性指标为主 + 短 prompt critic 为辅（避免上轮 MiMo 截断）

---

## 第五部分：开发计划（可直接派给 Codex）

### 5.1 阶段与文件（按审计 ROI 排序，lineage 优先于融合）

| Phase | 名称 | 新建/修改文件 | 验收 | 依赖 |
|---|---|---|---|---|
| **P0** | 传动轴骨架 | 新建 `methodology_lineage.py`（数据模型+序列化）；planner 写入 `chapter_contract.methodology_lineage`；feature flag `BESTSELLER_METHODOLOGY_V2` 复用 | 单测：lineage 可序列化/反序列化；flag=off 时 chapter_contract 字节不变 | hook_ledger 范式 |
| **P0** | Selection Engine | 新建 `methodology_selection_engine.py`（§3.2 五步）；读 `inventory.jsonl`；chapter_role 推导器 | 单测：同输入同输出；不同 role 选不同 slot；预算生效；弱项补强生效 | inventory ✅ |
| **P1** | draft/review 改读 lineage | 改 `drafts.py`（删除自调 selector，改读 lineage 注入）；改 `reviews.py`（删自调，改按 evidence_fields 检查） | golden prompt：draft/review 出现同一组方法论；selector 调用数 planner≥1,draft=0,review=0 | P0 |
| **P1** | payoff_ledger | 新建 `payoff_ledger.py` + `payoff_ledger_runtime.py`（照 hook_ledger 范式，整合 ClueModel/foreshadowing/setup_payoff_tracker）；接 planner→review→repair | 单测 closure_rate；接入后 setup_payoff_score 跨章可测 | P0 |
| **P2** | character_arc 接线 | `drafts.py` 读 character_delta/protagonist_choice；注册 `ensemble_arc_progress_gate` 进 workflows | draft prompt 含人物变化字段；arc gate 在 pipeline 运行 | P1 |
| **P2** | causal_contract 延伸 prose | `drafts.py` 直接消费 chapter causal_contract，或保证 chapter→scene 字段无损映射 | review 能定位"正文未兑现的因果字段" | P1 |
| **P3** | review evidence 化 | `reviews.py` 从泛 methodology_compliance 改为 per-AppliedMethodology evidence 检查 | 分数可归因到具体 rule_id | P1 |
| **P3** | 融合 v2.yaml（原Step3） | `config/staging/writing_methodology_v2.yaml` + advisory_pool（§审计的 Replace/Augment/Keep 表） | validate 无 error；不影响在飞 | P0(可并行) |
| **P4** | health 晋级统计 | `methodology_health.py` 增 coverage/evidence/failure 统计 + dormant→active 名单 | health report 显示 11 slot 状态 | P1,P3 |
| **P5** | A/B/C harness | 扩 `scripts/methodology_books/run_short_story_pilot.py` 支持 3 组×90 章 + 方差对比 | 产出方差对比报告 | P0-P4 |

### 5.2 与既有 Task 列表的对应（重排优先级）

| 既有 Task | 新优先级 | 说明 |
|---|---|---|
| #5 Step5 lineage | **P0（提升）** | 审计证明是根因，必须最先做 |
| #9 Step4b hook 接入 | ✅ 已由同步开发完成 | 范式样板 |
| 新增 payoff_ledger | **P1** | A/B 最大短板 |
| 新增 character_arc 接线 | **P2** | 断头路，只差接线 |
| #4 Step2 capability slots | 并入 P0 selection engine | slot 定义已在本文 §2.1 |
| #3 Step3 融合 v2.yaml | **P3（降低）** | 没传动轴，融合传不到正文 |
| #6 Step6 指标驱动 | P3-P4 | evidence 化 + 晋级 |
| #8 Step8 AB | **P5** | 全部接通后验证 |

### 5.3 在飞任务隔离（不变）

- 全部新代码走 `BESTSELLER_METHODOLOGY_V2=1`，默认 off
- 道种破虚 217-230 走 legacy
- 每个 Phase 必须有"flag=off 时行为字节不变"的回归测试

---

## 第六部分：风险与铁律

1. **lineage 优先于融合**：没有传动轴，v2.yaml 再漂亮也传不到正文。顺序不可颠倒。
2. **selection 必须确定性**：同输入同输出，否则 A/B 无法归因。
3. **strict 才能 block**：heuristic/advisory 只能 warn/记录，避免误杀（上轮教训）。
4. **prose 阶段禁止重新 select**：这是抽卡漂移的根，必须在 P1 物理删除 draft/review 的 selector 调用。
5. **不再加方法论卡**：224 条够用，工作全在"接线"和"验证"，不在"扩容"。
6. **每个 Phase 自带验证**：用得对不对（evidence 命中）、好不好（指标均值）、行不行（方差）、能不能用（flag=off 回归）——四问每 Phase 都要答。

---

## 附：一页纸执行摘要

```
问题: 224 条方法论，71% 没用起来，各阶段抽卡不一致
根因: 缺"planner选定→下游沿用"的传动轴（lineage）
方案: MethodologyLineage 贯穿全流程 + 确定性 selection engine
      + 11 slot 物理落点 + 三级验证(strict/heuristic/advisory)
顺序: P0传动轴+引擎 → P1 draft/review改读+payoff_ledger
      → P2 character/causal接线 → P3 evidence化+融合v2
      → P4 health晋级 → P5 A/B/C验证
样板: hook_ledger v2(已闭环)，全部照抄
铁律: lineage优先于融合; 确定性; strict才block; prose禁重选; 不加卡
```
