# 方法论闭环实证审计与融合最终结论

> 本文不是又一份流程图。它是**下到代码逐条验证**后的结论：当前框架里"人物弧线、场景因果"这类方法论，哪些是真闭环、哪些是断头路、哪些只是 prompt 里的一句话。所有判断都附 grep 证据。
>
> 审计日期：2026-05-29　审计对象：`src/bestseller/services/`（planner.py 16.5k 行、drafts.py、reviews.py、workflows.py 等）

---

## 0. 一句话结论

> **框架不缺方法论，缺的是"同一份方法论从规划贯穿到正文再到审稿"的传动轴。** 当前 planner 写了一堆结构化 contract，但 draft 生成正文时基本不读这些结构化字段，而是各自重新"抽卡"注入泛化的方法论文本；review 又独立抽一次。这条传动轴在三个地方断裂，导致 71% 的方法论规则 runtime_dormant，也导致用户感受到的"抽卡式不稳定"。

---

## 1. 闭环判定标准

一个方法论域要算"真闭环"，必须四个环节都接通：

```
planner 写入结构化字段  →  draft 读该字段并落进正文  →  review 检查正文是否兑现该字段  →  repair 按该字段定向修复
```

只要任一环断开，就是"半闭环"或"断头路"。下面逐域给出实证。

---

## 2. 逐域闭环审计结果（核心结论表）

| 方法论域 | planner 写入 | draft 消费 | review 检查 | repair 定向 | 判定 |
|---|:---:|:---:|:---:|:---:|---|
| **hook_ledger v2** | ✅ `render_hook_ledger_planner_contract` (planner.py:10730) | ✅ 经 contract | ✅ `merge_hook_ledger_audit_into_chapter_review` | ✅ rewrite instructions | **🟢 唯一真闭环**（feature flag 开启后） |
| **scene methodology_contract** | ✅ planner 8 处 | ✅ drafts 25 处真注入（`visible_action_or_reaction`/`signature_image`/`cut_point`） | ✅ reviews 11 处 | ❌ 无 `repair_domain` | **🟡 接近闭环，缺定向 repair** |
| **scene_causality (causal_contract)** | ✅ planner.py:9504/9599 | ❌ **drafts.py 0 引用 causal_contract** | ✅ 6 个 gate（chapter_causality_gate 等，已注册进 workflows） | 🟡 部分 | **🟡 半闭环：只在 outline 层闭合，prose 层断开** |
| **character_arc / protagonist_choice** | ✅ planner 写入 | ❌ **drafts.py 0 引用** | 🟡 reviews 仅 2 处 | ❌ | **🔴 断头路：写入了但正文不读、章级 arc gate 未注册** |
| **payoff_ledger** | 🟡 仅 prompt 文字 | — | — | — | **🔴 未建：只有分散的 ClueModel / foreshadowing.py / setup_payoff_tracker.py，无统一账本** |
| **方法论选择 lineage** | ❌ **planner 调 selector = 0** | 🟡 draft 调 = 3（独立抽） | 🟡 review 调 = 2（独立抽） | — | **🔴 破裂：各阶段独立抽卡，无共享/持久化** |

### 2.1 最重要的铁证：lineage 破裂

```
planner 调 methodology selector: 0 次   ← 规划阶段根本不选方法论
draft   调 methodology selector: 3 次   ← 正文阶段自己抽
review  调 methodology selector: 2 次   ← 审稿阶段又自己抽
```

**这就是"抽卡式不稳定"的根因。** planner 不选 → draft 和 review 各抽各的 → 同一章里 draft 落地的方法论和 review 检查的方法论可能根本不是同一组。方法论选择结果没有被持久化、没有跨阶段传递。

### 2.2 第二铁证：planner 的结构化产物在 prose 层"蒸发"

- planner 在章纲写了 `causal_contract`（chapter_function/pressure/protagonist_desire/protagonist_choice/resistance/cost/gain/state_change/next_reader_desire 共 10 个字段）
- 这些字段被 6 个 **outline 层 gate** 检查（chapter_causality_gate、outline_llm_judge、planning_readiness_gate、story_principle_gate、outline_specificity_gate、chapter_outline_readiness_gate）
- **但 drafts.py 完全不读 `causal_contract`**。正文生成只读 scene 级 `methodology_contract` 的蒸馏字段。

结论：**章级因果只在"章纲对不对"这件事上闭环了，但"正文有没有按这个因果写"没有直接传动**。中间靠 scene methodology_contract 这个更细的桥半接上，但 chapter→scene 的字段映射是否无损，没有保证。

### 2.3 第三铁证：人物弧线 gate 是孤儿

- `ensemble_arc_progress_gate.py`、`arc_tension_monitor.py` 文件存在
- 但在 `workflows.py` / `pipelines.py` 里 **未注册**（grep 无匹配）
- drafts.py 对 character_arc / protagonist_choice **0 引用**

结论：人物弧线有大量基础设施（character_arcs.py、character_evolution.py、ensemble_arc_kernel.py …），但**正文生成不读、章级 gate 没挂上**。人物弧线目前主要活在"立项/CastSpec"阶段，进入逐章正文后基本失联。

### 2.4 好消息：hook_ledger 是已验证的闭环模板

你这轮同步开发已经把 hook_ledger 做成了唯一的真闭环（新增 `hook_ledger_runtime.py` 12k）：
- planner：`render_hook_ledger_planner_contract` 要求章纲产出 hook delta
- review：`compute_hook_ledger_audit_for_review` + `merge_hook_ledger_audit_into_chapter_review`
- repair：`_hook_ledger_rewrite_instructions` 把缺口转成定向改写指令

**这就是其他所有域应该照抄的闭环范式。** 唯一缺口：payoff_ledger 还没建（仍只有分散的 ClueModel）。

---

## 3. 回答你的三个问题

### Q1：融合路径——方法论如何融入 Prompt，又如何融入框架？

当前存在**两条路径**，效果天差地别：

| 路径 | 机制 | 现状 | 是否稳定 |
|---|---|---|---|
| **A. Prompt 文本注入** | `render_*_block()` 把方法论渲染成提示词片段塞进 prompt | 当前主力（draft 阶段一堆 render 函数） | ❌ open-loop，模型"看见但不一定落地"，且各阶段独立抽 |
| **B. Contract 字段注入** | 方法论变成结构化字段（methodology_contract / hook ledger），planner 写、draft 读、gate 检查 | 仅 hook_ledger + scene methodology_contract 走通 | ✅ closed-loop，可检查、可修复、可统计 |

**结论：融合的本质动作不是"往 prompt 里塞更多方法论"，而是把高价值方法论从路径 A 迁移到路径 B。** 路径 A 只适合承载"表达风格类、无法字段化"的软建议；凡是能被字段表达、能被检查的方法论，必须走路径 B 才能稳定起效。

### Q2：插入机制——哪个环节插什么，既起作用又不上下文爆炸？

核心原则：**"planner 选一次并写入 contract，下游只携带不重选"**。这一条同时解决"上下文爆炸"和"抽卡漂移"两个问题。

分层预算建议：

| 环节 | 注入什么 | 预算 | 防爆炸/防漂移机制 |
|---|---|---|---|
| **conception / CastSpec** | 人物设定类方法论（全量可承载，低频高价值） | 宽松 | 一次性，不进逐章 prompt |
| **outline_volume / chapter** | 叙事结构类（causal_contract、hook/payoff ledger delta、节奏），**并在此选定本章方法论 lineage 写入 contract** | 中 | 选择在此固化，写入 chapter contract |
| **scene draft** | 只携带 planner 已选 lineage（3-6 条）+ 表达类（POV/对白/感官） | 紧（≤ ~800 token） | **禁止重新抽卡**，沿用 contract 里的 lineage |
| **review** | 只检查 planner 已选 lineage 的 evidence | 紧 | 同上，按 contract 检查而非泛化打分 |
| **repair** | 只按失败的 `repair_domain` 取对应能力包 | 紧 | 定向，不全量 |

**关键改造**：当前 planner 调 selector = 0，必须改成 planner 选定 → 写入 `chapter_contract.methodology_lineage` → draft/review 读这个字段而不是各自调 selector。

### Q3：应用场景——哪些信息用于人物设定 / 故事叙事 / 正文创作？

七本书 + 现有方法论按"作用层"三分，落点明确：

| 应用层 | 方法论域 | 七本书来源 | 框架落点 | 现状 |
|---|---|---|---|---|
| **人物设定** | character_arc、premise_outline、worldview_theme | 雪花写作法（前提/人物交替）、怎样写故事（内在变化）、情节与人物（弧线嵌合三幕） | CastSpec + character strategy + snapshots | 🔴 写入了但逐章正文失联，需接 idiolect/snapshot |
| **故事叙事** | setup_payoff、scene_causality、pacing、hook_ledger | 哈佛短篇（叙事问题/五步场景）、故事写作（波浪悬念/线索可见）、情节与人物（选择推动情节） | causal_contract + methodology_contract + hook/payoff ledger | 🟡 hook 已闭环，causality 半闭环，payoff 待建 |
| **正文创作** | pov_prose、dialogue_subtext、show-don't-tell、sensory | 小说写作叙事技巧（POV/动作/细节）、哈佛短篇（场景 mini story） | scene methodology_contract + prose gates | 🟡 scene contract 走通，但靠各阶段抽卡不稳 |

**铁律（A/B/C 已证伪的教训）**：
- **规划型方法论（setup_payoff、结构、人物弧线）绝不能留到 prose 阶段临场补**——前一轮 C 组证明：prose 阶段补 setup_payoff 卡会牺牲场景因果和对白，且不修复伏笔闭环。它们必须在 outline 阶段进 contract/ledger。
- **只有表达型方法论（POV、对白、感官）才在 prose 阶段注入**——因为它们是句子级的、即时可落地的。

---

## 4. 最终结论与优先级建议

### 4.1 结论

1. **不要再加方法论卡了。** 224 条已经足够，71% 还在 dormant。
2. **当前唯一缺的核心机制是"方法论 lineage 传动轴"**：planner 选定 → 写入 contract → draft/review/repair 沿用。这条轴修好，现有方法论的利用率会从 25% 大幅上升。
3. **hook_ledger 是已验证的闭环范式**，其他域照抄即可。
4. **三个断点按 ROI 排序修复**（见下）。

### 4.2 优先级（建议交给 Codex 执行的顺序）

| 优先级 | 动作 | 对应原 Step | 理由 |
|---|---|---|---|
| **P0** | 建 lineage：planner 选定方法论写入 `chapter_contract.methodology_lineage`，draft/review 改为读该字段、禁止重新调 selector | Step 5 | 直接消灭"抽卡式不稳定"，是所有闭环的前提 |
| **P1** | 补 payoff_ledger：照 hook_ledger 范式，把分散的 ClueModel/foreshadowing/setup_payoff_tracker 收敛成一等公民账本，接 planner→review→repair | Step 4b 延伸 | setup_payoff 是 A/B 暴露的最大短板，且已有零件可整合 |
| **P2** | 接通 character_arc 传动：drafts.py 读 protagonist_choice/character_arc 字段；把 ensemble_arc_progress_gate 注册进 workflows | 新增 | 人物弧线当前是断头路，基础设施齐全只差接线 |
| **P3** | 把 causal_contract 从 outline 层延伸到 prose 层：drafts.py 直接消费 chapter causal_contract，或保证 chapter→scene 字段无损映射 | 新增 | 让"章纲因果"真正约束"正文因果" |
| **P4** | review evidence 化：从泛 `methodology_compliance` 打分改为"检查 lineage 中每条方法论是否有正文 evidence" | Step 6 | 让分数可解释、可定位 |
| **P5** | health 统计 dormant→active 晋级：统计哪些域有 evidence、哪些反复失败 | Step 6 延伸 | 把 71% dormant 逐步盘活 |

### 4.3 与已有 Step 任务的对应

本审计**不改变**既定的 Step 1-8 路线，而是给出了**实证优先级**：
- 原 Step 5（lineage）应提升为 **P0 最高优先级**——它是根因。
- 原 Step 4b（hook 接入）已由你同步开发完成，下一步是**把 payoff_ledger 补成同样的闭环**。
- Step 3（融合 v2.yaml）/ Step 2（capability slots）可以并行，但**没有 lineage 传动轴，融合再漂亮也传不到正文**——所以 lineage 优先于融合。

---

## 5. 附录：本审计的可复现 grep 证据

```bash
# lineage 破裂
grep -c "select_book_methodology\|methodology_book_selector" planner.py   # → 0
grep -c "...同上..." drafts.py                                            # → 3
grep -c "...同上..." reviews.py                                           # → 2

# causal_contract 在 prose 层蒸发
grep -l "causal_contract" drafts.py                                       # → 无匹配
grep -l "causal_contract" chapter_causality_gate.py                       # → 有（仅 outline 层）

# character_arc 断头
grep -c "character_arc\|protagonist_choice" drafts.py                     # → 0
grep -l "ensemble_arc_progress_gate" workflows.py pipelines.py            # → 无匹配（gate 未注册）

# hook_ledger 真闭环
grep "render_hook_ledger_planner_contract" planner.py                     # → planner.py:10730
grep "merge_hook_ledger_audit_into_chapter_review" reviews.py             # → 有

# payoff_ledger 未建
ls payoff_ledger*.py                                                      # → 不存在
```
