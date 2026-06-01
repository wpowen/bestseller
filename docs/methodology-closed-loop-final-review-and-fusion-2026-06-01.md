# 方法论闭环 —— 最终开发结果复核 + 修复计划 + 框架融合分析

> **日期**：2026-06-01
> **复核人**：Claude (Opus 4.8)
> **复核对象**：`docs/methodology-closed-loop-final-report-2026-06-01.md`（T1–T9 全功能开发最终报告）
> **复核方式**：全仓 caller grep + 关键路径逐行读码 + import 编译 + 跑新增测试（36 passed）
> **结论先行**：**本轮质量显著高于前两轮**——7 个实施任务里有 **5 个（T1/T2/T4/T5/T6）是真接线、真消费**，已用 caller grep + 逻辑核对确认。**但有 1 个任务（T3）是"测试绿、生产空转"的假闭环，是本轮最需要修的点**；T7 报告自己诚实标注为"未接 pipeline"。

---

## 1. 总体开发质量评估

### 1.1 逐任务复核结论（核对到代码，不只看报告）

| 任务 | 报告声称 | 复核结论 | 证据 |
|:--|:--|:--|:--|
| **T1** payoff gate 字段 | ✅ 真修 | ✅ **属实** | `methodology_declared_payoffs` 在 [narrative.py:97](src/bestseller/domain/narrative.py) 定义、[context.py:624](src/bestseller/services/context.py) 单独填充、[reviews.py:6840](src/bestseller/services/reviews.py) 真读 |
| **T2** progress 通道 | ✅ 真接 | ✅ **属实** | [pipelines.py:5788/6386](src/bestseller/services/pipelines.py) 传 `progress=progress`；[worker/tasks.py:770](src/bestseller/worker/tasks.py) 传 `make_sync_callback(reporter)` |
| **T4** payoff evidence 消费 | ✅ 真消费 | ✅ **属实** | `merge_payoff_ledger_audit_into_chapter_review` 收 `chapter_contract`、fold 进 audit dict（[payoff_ledger_runtime.py:115-131](src/bestseller/services/payoff_ledger_runtime.py)）+ rewrite 指令；[reviews.py:6822](src/bestseller/services/reviews.py) 真传 chapter_contract |
| **T5** splice 走 envelope | ✅ 真接 | ✅ **属实** | [reviews.py:4693-4700](src/bestseller/services/reviews.py) 真调 `as_checker_report` → 进 `_compute_chapter_methodology_reports` → `merge_methodology_reports_into_chapter_review` → [methodology_runtime.py:166 `_has_blocking_issue`](src/bestseller/services/methodology_runtime.py) 真读 `can_override`。critical 码 `can_override=False` → 触发 rewrite。**真治理闭环** |
| **T6** scene bible 增量 | ✅ 真写库 | ✅ **属实** | [pipelines.py:5164-5210](src/bestseller/services/pipelines.py) flag 门控 + 幂等 + `apply_scene_bible_delta` 真 upsert；默认 OFF |
| **T7** OverrideStore 落 DB | ✅（待 wiring）| ⚠️ **部分**：helpers + 测试有，**全仓无 pipeline caller**（报告 §7.4 已诚实披露）| `save/load_override_store` grep 仅命中 docstring |
| **T3** chapter-first 预算 | ✅ tier-aware 必保区 | ❌ **生产空转（最重要问题）** | 见下 §2.1 |
| T8/T9 | ⏸ 待决策 | ✅ 合理挂起 | policy 决策项 |

**测试复核**：报告引用的新测试文件我实跑了 6 个文件 = **36 passed**（与报告口径一致）；import 全模块编译通过。

### 1.2 一句话质量评价

> 本轮把"上一轮被批评的孤儿符号"绝大多数真正接进了运行路径（尤其 T5 splice 进 `can_override` 治理、T4 evidence 进 audit/重写、T2 progress 三段贯通），**红线执行到位**。问题集中在 **T3 一个任务的"假绿"** 和 **T7 的"未接线"**，外加几个 minor。

---

## 2. 发现的问题（按严重度）

### 🔴 P0-A：T3 chapter-first 的"必保区保护"在生产中从不触发（假闭环）

**症结**：`_soft_trim_user_prompt`（[drafts.py:7781](src/bestseller/services/drafts.py)）靠在 prompt 里查找 must-keep 标记串
（`【章末收尾钩子】`/`【方法论证据】`/`[chapter closing hook]`/`[methodology evidence]`）来决定切割边界。但全仓搜索证明：

> **这些标记串只存在于 3 个地方：① `_MUST_KEEP_TAIL_MARKERS_*` 常量定义；② 代码注释；③ 测试文件里手工拼的 prompt。
> 真正的 chapter-first prompt 组装器从不产出这些标记。**

后果：生产中 `first_protected_idx` 永远 = -1 → 走 "legacy head-trim"分支 → **仍是上一轮被批评的盲切尾**。
测试之所以全绿，是因为测试 [test_chapter_first_tier_aware_trim.py:12](tests/unit/test_chapter_first_tier_aware_trim.py) 手工注入了 `protected = "【章末收尾钩子】\n..."`——**测试验证的是"函数有保护能力"，不是"生产真的会保护"**。这正是前两轮反复出现的反模式：单测绿 ≠ 集成闭环。

**附带**：报告 §9 文件表写 "drafts.py … `_budget_context_sections` (chapter-first)"，但 chapter-first **并未**调用 `_budget_context_sections`（只有 scene 路径 [drafts.py:5669](src/bestseller/services/drafts.py) 在用）。**该描述不实**。

---

### 🟡 P1-A：T7 OverrideStore 落 DB 未接 pipeline（报告已披露）

`save_override_store` / `load_override_store` + 4 个测试都在，但**无任何 pipeline 边界调用**。报告 §7.4 诚实说明"完整集成需未来 PR（涉及 chapter pipeline 边界与 ReviewStore 状态序列化）"。与 T9（Phase C 默认开）耦合——Phase C 不开时 OverrideStore 路径根本不跑，所以"未接线"暂不影响线上，但**这一项不能算"已完成"**。

---

### 🟡 P1-B：splice gate 现在三处并行运行，治理口径不一致

`evaluate_chapter_splice_coherence` 现在被 3 处消费：
- [reviews.py:4693](src/bestseller/services/reviews.py)（**新增**，走 `CheckerReport` + `can_override` 治理）
- [chapter_quality_bundle.py:189](src/bestseller/services/chapter_quality_bundle.py)（自家 `_finding` 格式）
- [wip_repair_closure.py:81](src/bestseller/services/wip_repair_closure.py)（自家格式）

同一章可能被 splice 评估 3 次、产出 3 套口径不同的 finding（一个走 override 治理、两个不走）。**没有单一 source of truth，也没有去重**。这是融合层面的隐患，不是 crash 级，但会导致"同一问题在不同评审通道结论不一致 / 重复计分"。

---

### 🟢 P2 级（minor，可顺手清）

1. **T1 注释与代码不符**：[reviews.py:6831](src/bestseller/services/reviews.py) 注释说 "severity_max are not promoted"，但代码 `_pl_max(severity_max, ("warning",))` 会把 info → warning。属实际会轻微抬升，注释需更正（或确认是否真要抬升）。
2. **T6 死 import**：[pipelines.py:5165](src/bestseller/services/pipelines.py) import 了 `collect_scene_delta_seen_keys` 但未使用（改用裸 metadata 读）。删之。
3. **T5 异常吞掉**：splice 分支 `except Exception: logger.debug(...)`——debug 级别，生产排障时几乎不可见。建议至少 `logger.warning`。

---

## 3. 修复计划

> 仍沿用上一轮红线：**每项"已修"必须能用一条 grep / 集成测试证明运行时真的走到它**。

### F1 —— 修 T3：让 chapter-first 真正保护必保区（P0，必做）

**两条路，二选一（推荐 A）：**

**路 A（彻底，与框架对齐）**：让 `build_chapter_first_draft_prompts`（[drafts.py:7333](src/bestseller/services/drafts.py)）复用 scene 路径同款分层预算器 `_budget_context_sections`（[drafts.py:2074](src/bestseller/services/drafts.py)）。
- 把 chapter-first 拼 prompt 前的各上下文块组织成 `_budget_context_sections` 期望的**结构化 sections（带 tier 标记）**，让收尾钩子 / 方法论证据归入最高保留 tier；
- 删除 `_soft_trim_user_prompt` 盲切兜底（或仅作最后一道安全网）。
- **验收**：构造 30+ Tier-3 块的超预算 ctx，断言裁剪后 Tier-1（钩子/证据）必在、Tier-3 被丢；并 grep 证明 chapter-first 真调 `_budget_context_sections`。

**路 B（最小改动，保留 marker 方案但补上产出端）**：在 chapter-first prompt 组装处，**真正用这些 marker 串包裹**收尾钩子段与方法论证据段。
- 定位 build_chapter_first 里拼"收尾钩子"和"方法论证据"的代码，在段首插入 `【章末收尾钩子】`/`【方法论证据】`（中），英文路径插 `[chapter closing hook]`/`[methodology evidence]`。
- **验收（关键，区别于现状）**：写一条**集成测试**——调用真实的 `build_chapter_first_draft_prompts`（喂超预算 context），断言返回的 `user_prompt` 里 marker 存在且裁剪后保留。**不允许只测 `_soft_trim_user_prompt` 裸函数**。

**并修文档**：报告 §9 "(chapter-first) `_budget_context_sections`" 的不实描述，按实际方案订正。

---

### F2 —— 接通 T7 OverrideStore 到 pipeline 边界（P1，与 T9 一起决策）

- 在 chapter pipeline 开始处 `load_override_store(session, project)`，在 override 决策落定后 `save_override_store(...)`。
- 由于 Phase C 默认 OFF，wiring 应 **flag 门控**（Phase C enabled 时才 load/save），与 T9 决策绑定。
- **验收**：开 Phase C，模拟两个 worker session：session1 写 override → session2 load 读到。集成测试覆盖 pipeline 边界，而非只测 helper。

---

### F3 —— 统一 splice 的单一治理入口（P1）

- 选定 **唯一** source of truth：建议以 `reviews._compute_chapter_methodology_reports`（走 `can_override` 治理）为主。
- 让 `chapter_quality_bundle` / `wip_repair_closure` 要么复用同一个 `as_checker_report` 产出、要么明确分工（如 bundle 只在"非 review 主线"场景跑），避免同章 3 次评估 + 口径分裂。
- **验收**：grep 证明 splice 评估在 review 主线只发生一次；或文档化各入口的非重叠职责 + 去重键。

---

### F4 —— minor 清理（P2，可并入任意 PR）

- F4-1：更正 [reviews.py:6831](src/bestseller/services/reviews.py) T1 注释（severity_max 实际会抬到 warning），或确认设计意图后保留。
- F4-2：删 [pipelines.py:5165](src/bestseller/services/pipelines.py) 未用 import `collect_scene_delta_seen_keys`。
- F4-3：T5 splice 分支异常日志 `logger.debug` → `logger.warning`。

---

## 4. 与当前框架的融合分析（哪些点需要改 / 需要融）

这一节超出"补洞"，聚焦**新能力如何与既有框架长期共存**。

### 4.1 质量门控融合：splice 进了 Phase A，但 Phase A 的"统一入口"仍未收口

T5 把 splice 接进了 `CheckerReport` + `can_override` 治理，**方向完全正确**——这正是框架早期审计要的"统一 envelope"。但：
- 框架里仍有**多套 finding 体系并存**：`CheckerReport`（Phase A 统一）、`chapter_quality_bundle._finding`、`GateVerdict`（splice 原生）、`ChapterReviewFinding`。splice 现在横跨其中三套。
- **融合建议**：把 splice 作为"第一个迁移样板"，逐步让其余自定义 envelope 的 gate（参见前几轮 P2 列表里的 `chapter_word_count_truth` / `chapter_prose_segmenter` 等 0-callsite gate）也走 `as_checker_report` 同款适配，最终 Phase A 只保留 `CheckerReport` 一种 finding 真值源。这是把"门控统一"从 1 个点扩成体系的关键。

### 4.2 知识层融合：scene bible 增量（T6）与 chapter-end 批处理的"双写"边界

T6 引入了 scene 级增量写库（`apply_scene_bible_delta`），而框架原有 chapter 末 `update_story_bible_from_chapter` 批处理仍在。flag OFF 时无冲突，但**一旦 flag ON，两条写路径会对同一 `CharacterModel.arc_state` / `RelationshipModel` 竞争写入**。
- **融合建议**：明确"增量为主、批处理补差"的契约——flag ON 时，chapter 末批处理应先读已应用的 scene deltas（`project.metadata_json["scene_bible_deltas"]`），只补未被增量覆盖的字段，避免覆盖回滚。这一点目前**代码里没有实现**（chapter-end 路径未感知 scene deltas）。属 flag 启用前必须补的融合逻辑。

### 4.3 payoff/hook 双账本融合：T1+T4 让 payoff 真闭环，但与 hook_ledger 仍是平行体系

T1/T4 后 `payoff_ledger` 达到与 `hook_ledger` 同级的真闭环。框架早期审计已指出 `setup_payoff_tracker`（hype 视角）与 `hook_ledger`（clue 视角）双轨、`methodology_contract` 字段有 5 种 dict 形态。
- **融合建议**：现在两个 ledger 都真闭环了，正是抽公共"ledger 基类/协议"的时机——统一 `payoffs_due` / `hooks_to_resolve` 的合并、evidence_paths、output gate 三段为一个泛型 `LedgerRuntime`，避免每加一个 slot 就复制一遍 T1+T4 的接线。这能直接降低剩余 7 个空白 slot 的接入成本。

### 4.4 progress / 可观测性融合：T2 通了单章，但 scene-bible-delta、splice 等新事件未进 SSE 契约

T2 让单章 progress 三段贯通，T6 还 emit 了 `scene_bible_delta_applied`。但这些新事件类型**是否在前端 SSE 契约 / `pipeline_flow_schema` 里登记**需确认——否则前端收到未知事件类型会忽略或报错。
- **融合建议**：把本轮新增的 progress 事件（`scene_pipeline_started`/`scene_draft_generated`/`scene_review_completed`/`scene_knowledge_refreshed`/`scene_bible_delta_applied`）补进 `pipeline_flow_schema.py` 的事件枚举与前端契约。

### 4.5 配置融合：T9（Phase B/C 默认开）+ T7（OverrideStore DB）+ Phase C in-memory 失忆是同一个决策簇

T7 的 DB 化、T9 的默认开、以及早期 P0-5 的 in-memory 单例失忆，本质是**同一件事的三个面**。
- **融合建议**：当用户决定开 Phase C（T9）时，必须**同一个 PR 内**完成 T7 的 pipeline wiring + `ChaseDebtLedger` 持久化 + `only_enforce_from_chapter` 灰度，否则开了 Phase C 但 override 状态跨 worker 丢失，会比不开更糟（override 决策无法跨章生效）。**不要把 T9 单独启用**。

---

## 5. 建议执行顺序

| 阶段 | 内容 | 依赖 |
|:--|:--|:--|
| **PR-1（立即）** | F1（修 T3 假闭环，路 A 优先）+ F4 minor 清理 | 无 |
| **PR-2** | F3（splice 单一治理入口）+ 4.1 门控统一样板 | 无 |
| **PR-3** | 4.4 progress 事件补进 SSE 契约 | 无 |
| **决策后（用户拍板 T9）** | T9 + F2（T7 wiring）+ `ChaseDebtLedger` 持久化 + 4.2 知识层双写契约，**同一 PR** | 用户决策 |
| **后续** | 4.3 抽 `LedgerRuntime` 泛型基类，降低剩余 7 slot 接入成本 | PR-1/2 后 |

---

## 6. 交付红线（给执行 LLM 与校验方）

每项完成后必须提供：
- [ ] **集成级**验收证据：F1 必须测**真实 `build_chapter_first_draft_prompts`** 的输出，不接受只测裸 trim 函数；
- [ ] caller grep 输出，证明运行路径真走到新代码；
- [ ] flag OFF / v2 OFF 的零行为变化回归；
- [ ] 若动 SSE 事件，附前端契约 diff。

> **本轮特别提醒**：T3 的教训是"单测注入了生产不存在的输入"。今后凡涉及"按标记/结构裁剪或路由"的逻辑，验收测试**必须从真实组装入口进**，禁止在测试里手工拼出生产不产出的标记。

---

**复核人**：Claude (Opus 4.8)
**核对方式**：caller grep（T1-T7 全覆盖）+ 关键路径读码 + 全模块 import 编译 + 36 项新测试实跑
