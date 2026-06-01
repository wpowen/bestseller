# 方法论闭环 —— 未完成项修复与开发计划

> **日期**：2026-06-01
> **作者**：Claude (Opus 4.8) — 复核 `methodology-closed-loop-full-implementation-2026-06-01.md` 后产出
> **用途**：交给执行 LLM 实施。本文档为**自包含**规格，实施者无需上一轮会话上下文即可开工。
> **关联**：
> - `docs/methodology-closed-loop-full-implementation-2026-06-01.md`（被复核的"完成报告"）
> - `docs/methodology-closed-loop-implementation-2026-06-01.md`（首轮报告，对"剩余断口"更诚实）

---

## 0. 背景与本计划要解决的核心病

上一轮"完成报告"把一批 **"只定义符号、没接入运行路径"** 的改动标成了 `✅ 已修` / `真闭环`。复核（含全仓 caller 搜索 + 跑测试）确认：真正落地的只有 3 项（删孤儿类 `AggregateGateReport`、payoff `due_payoff_codes` 列/dict 合并、`MethodologyLineage` Pydantic 化）。其余被标为已修的，要么**无任何 caller**（死代码），要么**校验了错误的字段**，要么是**会切尾的 band-aid 且 docstring 与实现不符**。

> **本计划的统一验收原则**：每一项必须能用一条**集成路径 grep / 测试**证明"运行时真的会走到它"，而不是只证明"新函数不崩"。每个任务都给了 `验收 grep` 或 `验收测试`。

---

## 1. 任务总览（按 ROI / 风险排序）

| # | ID | 标题 | 类型 | 风险 | 预估 |
|:--|:--|:--|:--|:--|:--|
| T1 | P2-C | 修正 payoff LLM output gate 校验错字段 | Bug 修正 | 低 | 0.3d |
| T2 | P1-5 | 接通单章 progress 通道（task→chapter→scene 三段） | 接线 | 低 | 0.5d |
| T3 | P0-3 | chapter-first 改用分层预算器（替换盲切尾） | 重构 | 中 | 0.5–1d |
| T4 | P2-A/B | payoff `evidence_paths` 加真实消费方 + 修 prompt/docstring | 接线 | 低 | 0.5d |
| T5 | P1-7 | splice gate 真正走 Phase A `CheckerReport` envelope | 接线 | 中 | 0.5–1d |
| T6 | P1-1 | scene 级 bible 增量（`SceneBibleDelta`）接入 pipeline | 功能 | 中 | 1–1.5d |
| T7 | P0-5 | `OverrideStore` / `ChaseDebtLedger` 落 DB | 功能 | 中-高 | 1–1.5d |
| T8 | P0-1 | LLM 熔断器 `allow_request()` 接入调用入口 | 接线 | 中 | 0.3d（需用户决策）|
| T9 | P0-4 | Phase B/C 默认开关决策 | 配置 | 低 | 决策项 |

> T8/T9 上一轮因 auto-mode policy 被 revert，**需要用户显式拍板**后再做，不要默认启用。

---

## 2. 逐项规格

### T1 — 修正 payoff LLM output gate（P2-C）✅ 优先做，最便宜

**根因**
[reviews.py:6799](src/bestseller/services/reviews.py) 新增的 gate 检查的是
`chapter_context.chapter_contract.due_payoff_codes`——这是 **合并后**的并集（`PayoffModel` 列 + LLM 写的 `methodology_contract.payoffs_due`，见 [context.py:619](src/bestseller/services/context.py) `_merge_due_payoff_codes`）。
原始意图是"校验 **planner LLM 是否真的写了** `payoffs_due`"。但用合并值判断会出现：
- 列里有码、LLM 一字没写 → gate **误通过**（没堵住 LLM 漏写）；
- warning 文案写 "planner LLM did not declare ... payoffs_due"，实际只在两源都空时触发 → **文案与判定不符**；
- gate 只追加 warning，`verdict` 不变，但 evidence 写 `verdict_action: "rewrite_soft"` → **元数据自相矛盾**。

**改法**
1. 在 read schema 保留 **LLM 来源的纯字段**，使 provenance 不丢失。在 `ChapterContractRead`（[domain/narrative.py:89](src/bestseller/domain/narrative.py)）新增：
   ```python
   methodology_declared_payoffs: list[str] = Field(default_factory=list)
   ```
   在 [context.py `_chapter_contract_read`](src/bestseller/services/context.py:616) 用 `methodology_contract.get("payoffs_due")` 单独填充该字段（**不** union 列）。`due_payoff_codes` 仍保留合并逻辑不动。
2. 把 [reviews.py:6799](src/bestseller/services/reviews.py) 的 gate 判定源从 `due_payoff_codes` 改为 `methodology_declared_payoffs`。
3. 修正 evidence 元数据：要么真的把 `verdict` 改成触发软重写，要么把 `verdict_action` 从 `"rewrite_soft"` 改为 `"warning_only"` 与实际行为一致（**二选一，建议后者**，保持"软告警"语义）。

**验收测试**（新增到 `tests/unit/` 合适文件）
- 列有码 + LLM 空 → gate **应触发** `PAYOFFS_DUE_EMPTY`。
- 列空 + LLM 有码 → gate **不触发**。
- 两者都空 → 触发。
- v2 OFF → 永不触发。

---

### T2 — 接通单章 progress 通道（P1-5）

**根因（通道在两个关节断开）**
- `run_scene_pipeline` 已加 `progress` 参数 + 4 个 `_emit_progress` 埋点（已完成）。
- **断点 A**：[pipelines.py:5695 与 6292](src/bestseller/services/pipelines.py) 这两处 `run_scene_pipeline(...)` 调用**没有传 `progress=`**。`run_chapter_pipeline` 本身已有 `progress` 参数（[pipelines.py:5238](src/bestseller/services/pipelines.py)）。
- **断点 B**：[worker/tasks.py:737 `run_chapter_pipeline_task`](src/bestseller/worker/tasks.py) 调 `run_chapter_pipeline(...)` 时**不传 progress**，docstring 还写着 *"no progress callback — pipeline doesn't support it"*。该函数已有 `reporter = RedisProgressReporter(...)`。

**改法**
1. **断点 A**：在 `run_chapter_pipeline` 内的两处 `run_scene_pipeline(...)` 调用补 `progress=progress`。
2. **断点 B**：在 `run_chapter_pipeline_task` 内，用 `reporter` 构造一个 `ProgressCallback`（签名见 [pipelines.py:1087](src/bestseller/services/pipelines.py) `ProgressCallback = Callable[[str, dict[str, Any] | None], None]`，**是同步签名**，而 `reporter.emit` 是 async）。
   - **关键设计点（实施者必须先确认）**：全书 pipeline（`run_book_pipeline` 之类）是如何把 async reporter 适配成同步 `ProgressCallback` 的？**复用同一套适配器**，不要自创第二套。先 grep 现有用法：
     ```bash
     grep -rn "progress=" src/bestseller/worker/tasks.py
     grep -rn "ProgressCallback\|def _emit_progress" src/bestseller/services/pipelines.py
     ```
   - 若全书路径用的是 "把事件丢进队列 / `asyncio.create_task` / 同步 wrapper" 模式，照搬。
3. 删除/更新 `run_chapter_pipeline_task` 那句过时 docstring。

**验收 grep（必须全部命中）**
```bash
# 两处 scene 调用都带 progress
grep -n "run_scene_pipeline(" src/bestseller/services/pipelines.py   # 5695/6292 块内应见 progress=
# task 把 progress 传进 chapter pipeline
grep -n "run_chapter_pipeline(" src/bestseller/worker/tasks.py        # 应见 progress=
```
**验收测试**：mock 一个 `progress` callback，跑一次单 scene/单章 pipeline，断言收到 `scene_pipeline_started` / `scene_draft_generated` / `scene_review_completed` / `scene_knowledge_refreshed` 至少各一次。

---

### T3 — chapter-first 改用分层预算器（P0-3）

**根因**
[drafts.py:7770 `_soft_trim_user_prompt`](src/bestseller/services/drafts.py) 是 `user_prompt[:char_budget]` **盲切尾**：
- docstring 声称"先丢最低优先级尾部块、再动正文"——**代码没做任何优先级判断**；
- 切的是**尾部**，而尾部常是它声称要保护的"章节收尾钩子 / 方法论证据"，可能从句子中间截断；
- **绕过**了框架已有的分层预算器 [`_budget_context_sections`（drafts.py:2074）](src/bestseller/services/drafts.py)，scene 路径在 [drafts.py:5669](src/bestseller/services/drafts.py) 正是用它。

原始 P0-3 问题是："chapter-first 路径不跑 context budget 裁剪，30+ 块 Tier 3 全塞"。盲切尾**没有解决**这个根因。

**改法（首选）**
让 `build_chapter_first_draft_prompts`（[drafts.py:7330](src/bestseller/services/drafts.py)）在拼 `user_prompt` 前，把各上下文块组织成 `_budget_context_sections` 期望的**结构化 sections**（带 tier 标记），调用它做 tier-aware 预算，再拼装。参照 scene 路径 [drafts.py:5669](src/bestseller/services/drafts.py) 的 `_ctx = _budget_context_sections(...)` 用法。

**改法（退路，若结构化重构超预算）**
至少做到：
1. 让裁剪**优先级感知**：给低优先级尾部块（callback / obligations / tree paths）打标记，只丢被标记块，**绝不**从正文/收尾钩子/方法论证据中间切；
2. 修正 docstring 使其与实现一致；
3. 保证收尾钩子块与方法论证据块在裁剪后**必定保留**。

**验收测试**
- 构造一个超 budget 的 chapter-first prompt（含明确的"收尾钩子"标记块和"方法论证据"块），裁剪后断言这两块**仍在**，且没有出现半句截断（结尾不以非标点中断）。
- budget 充足时 prompt 原样返回。

---

### T4 — payoff `evidence_paths` 加真实消费方（P2-A/B）

**根因**
`payoff_evidence_paths` 已被 lift 进 read schema（[context.py:622/798](src/bestseller/services/context.py)），但**没有任何下游消费**。自相矛盾的是：
- [payoff_ledger_runtime.py:256](src/bestseller/services/payoff_ledger_runtime.py) docstring 仍写 *"no reader yet ... the audit does not consume it"*；
- planner 合同 prose（[payoff_ledger_runtime.py:273/293](src/bestseller/services/payoff_ledger_runtime.py)）明确告诉 LLM *"尚无读取方——保持可读散文描述，不必严格 schema"*，**与"已存在一个期望 list[dict] 的 reader"直接打架**。

**改法**
1. **加消费方**（至少一处）：
   - 在 `payoff_ledger_audit_to_dict`（[payoff_ledger_runtime.py:129](src/bestseller/services/payoff_ledger_runtime.py)）的 evidence 中暴露 `evidence_paths`；**或**
   - 在 `_payoff_ledger_rewrite_instructions`（[payoff_ledger_runtime.py:215](src/bestseller/services/payoff_ledger_runtime.py)）里把对应 payoff 的 evidence path 注入 editor 重写 prompt（"本章在 scene N 兑现了 payoff X，重写时保留该回扣"）。
   - 需要把 `chapter_contract.payoff_evidence_paths` 传到 audit / 重写函数（目前 `compute_payoff_ledger_audit_for_review` 已收 `chapter_contract`，可直接取）。
2. **统一 prompt 与 reader**：把 planner 合同 prose 改成要求**结构化** `payoff_evidence_paths`（list of `{payoff_code, scene_ref, quote}`），删掉"无读取方/不必 schema"那句。
3. **修 docstring**：删除 `render_payoff_ledger_planner_contract` 里"currently aspirational / no reader"那段，改述实际行为。

**验收测试**
- 给 `chapter_contract.payoff_evidence_paths` 塞一条，跑 audit→dict / 重写指令，断言该 evidence 出现在输出里。
- 跑现有 `tests/unit/test_payoff_ledger_runtime.py`，更新断言关键词（合同 prose 改了）。

---

### T5 — splice gate 真正走 Phase A `CheckerReport` envelope（P1-7）

**根因**
`as_checker_report`（[chapter_splice_coherence_gate.py:90](src/bestseller/services/chapter_splice_coherence_gate.py)）定义了、进了 `__all__`，但**全仓 0 调用**。splice gate 实际在两处被消费，都没用它：
- [chapter_quality_bundle.py:189](src/bestseller/services/chapter_quality_bundle.py)：把 findings 转成自家 `_finding(...)` bundle 格式；
- [wip_repair_closure.py:81](src/bestseller/services/wip_repair_closure.py)。

"走 Phase A envelope" 的真正价值是让 splice 结果进入统一的 `CheckerReport` 治理（`can_override` / `allowed_rationales` / override 合同）。

**改法（实施者先确认 Phase A envelope 的消费者在哪）**
1. 先定位"统一 `CheckerReport` 聚合点"——即其它 gate 产出 `CheckerReport` 后被谁汇总、谁读 `issue.can_override` / `allowed_rationales`：
   ```bash
   grep -rn "CheckerReport\|can_override\|allowed_rationales" src/bestseller/services/ | grep -v test
   ```
2. 在该聚合点把 splice gate 经 `as_checker_report(verdict, chapter_number=...)` 接入，使 splice 的 critical/high code 能参与 override 治理（`_SPLICE_CRITICAL_CODES` 不可 override，其余带 rationales）。
3. 若 `chapter_quality_bundle` 才是事实上的统一通道，则方案改为：让 bundle 通过 `as_checker_report` 产出 issue（或反过来弃用 `as_checker_report`、把 `can_override`/rationales 直接并入 bundle 的 `_finding`）。**两条路二选一，目标是 override 治理字段真的被某处读到。**

**验收 grep / 测试**
```bash
grep -rn "as_checker_report" src/bestseller/ | grep -v "__all__\|def as_checker_report"   # 必须出现真实 caller
```
测试：构造一个含 `CHAPTER_SPLICE_LOCATION_DRIFT`（high，可 override）和 `CHAPTER_SPLICE_REPEATED_SENTENCE`（critical，不可 override）的 verdict，经聚合后断言前者 `can_override=True` 且带 rationales、后者 `can_override=False` 被某处治理逻辑读取。

---

### T6 — scene 级 bible 增量接入 pipeline（P1-1）

**根因**
`SceneBibleDelta` / `is_bible_incremental_enabled` / `collect_scene_delta_seen_keys` / `filter_fresh_deltas`（[story_bible.py](src/bestseller/services/story_bible.py) 末尾）**全仓 0 调用**，纯死代码。原始问题：scene 级 character/relationship delta 写完即丢，下一章 bible context 永远 stale（只在 chapter 末 `update_story_bible_from_chapter` 批处理）。

**改法（feature flag `BESTSELLER_BIBLE_INCREMENTAL_ENABLED` 默认关）**
1. 在 `run_scene_pipeline`（[pipelines.py:3404](src/bestseller/services/pipelines.py)）的 `refresh_scene_knowledge` 之后，当 `is_bible_incremental_enabled()` 为真时：
   - 由 editor LLM 产出**仅本 scene** 的 delta（character state / relationship / world fact）→ `SceneBibleDelta`；
   - 用 `collect_scene_delta_seen_keys` + `filter_fresh_deltas` 做**幂等**过滤；
   - 新增 `apply_scene_bible_delta(delta)`：**不走 LLM**，直接 upsert 到 `CharacterModel.arc_state` / `RelationshipModel.last_changed_chapter_no` / `WorldRuleModel`。
2. chapter 末 `update_story_bible_from_chapter` 与增量的关系：flag 开时改为"读已应用的 scene deltas + 仅补差"，避免与第 N 章末批处理重复写。
3. 护栏：scene 新增字符 < 阈值跳过；累计 delta token 超 budget 时回落到 chapter 末批处理。

**验收测试**
- flag OFF：行为与现状逐字节一致（回归）。
- flag ON：同一 delta 投递 2 次只生效 1 次（幂等键）；scene delta 应用后，下一 scene 的 bible context 能读到更新值。

---

### T7 — `OverrideStore` / `ChaseDebtLedger` 落 DB（P0-5）

**根因**
`override_contract.py` 的 `persist_to_metadata_json` / `load_from_metadata_json` 只是 shim，**无 caller**；`_CHASE_DEBT_LEDGER`（[pipelines.py:2292](src/bestseller/services/pipelines.py)）+ `OverrideStore`（[override_contract.py:184](src/bestseller/services/override_contract.py)）是 in-memory 单例，跨 worker / 跨 run 失忆。

**改法**
1. 优先：直接写 `OverrideContractModel`（确认该 ORM 是否已存在；若无需建 migration）。在 run 开始 `load_*`、关键变更后 `persist_*`。
2. 退路（若不建表）：在 chapter/scene pipeline 边界调用现有 shim，把 store 快照进 `project.metadata_json` 的固定 key，启动时重建。
3. `ChaseDebtLedger` 同理持久化。

**验收测试**：模拟两个独立 session（模拟跨 worker），第一个写 override，第二个 load 后能读到。

> ⚠️ 涉及 DB schema 变更需 migration，属中-高风险，**建议单独 PR**。

---

### T8 — LLM 熔断器接入（P0-1）⚠️ 需用户决策

`CircuitOpenError`（[llm.py](src/bestseller/services/llm.py)）已定义、**无 caller**；`allow_request()` 全仓 0 调用。
**改法**：在 `_call_litellm_with_retry` 入口加 `if not _llm_breaker.allow_request(): raise CircuitOpenError(...)`，失败计入 breaker。
**为何挂起**：上一轮因 auto-mode policy 被 revert。**请用户确认是否启用**后再做（会改变失败行为，需配套测试：连续失败后短路、冷却后恢复）。

### T9 — Phase B/C 默认开（P0-4）⚠️ 纯决策

`config/quality_gates.yaml` 的 `phase_b_line_tracker.enabled` / `phase_c_overrides.enabled` 当前为 `false`。是否改 `true`（或加 `only_enforce_from_chapter` 灰度）是**产品决策**，不是 bug。等用户拍板。

---

## 3. 建议实施顺序与 PR 切分

1. **PR-1（低风险接线，先合）**：T1 + T2 + T4 —— 都是小改、低风险、立刻能验。
2. **PR-2**：T3（chapter-first 预算器）—— 中风险，独立可测。
3. **PR-3**：T5（splice envelope）—— 需先勘探 Phase A 消费点。
4. **PR-4**：T6（bible 增量，flag 默认关）。
5. **PR-5**：T7（DB 持久化，带 migration）。
6. **决策后**：T8 / T9。

---

## 4. 交付与验收清单（交给校验方）

实施者每完成一项，须在 PR 描述贴出：
- [ ] 对应 `验收 grep` 的输出（证明运行路径上有真实 caller，而非孤儿符号）；
- [ ] 新增/更新的集成测试通过截图或日志；
- [ ] v2 OFF / flag OFF 时**零行为变化**的回归确认；
- [ ] 若改了 planner 合同 prose，附 v2 ON 的 prompt 片段 diff。

**通用红线**：本轮所有"已修"都必须能用一条 grep 证明被运行时调用。**只新增函数/字段/类而无 caller 的，一律视为未完成。**

---

**复核与计划人**：Claude (Opus 4.8)
**核对方式**：全仓 caller grep + `pytest`（已确认 T 涉及文件当前 46 项相关单测为绿，但均为孤立单测，未覆盖闭环集成）
