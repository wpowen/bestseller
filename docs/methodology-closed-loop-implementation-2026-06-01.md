# 方法论闭环审计与首轮实施报告

> **日期**：2026-06-01
> **范围**：BestSeller 框架"方法论传动轴"诊断 + 阶段 A/C 实施
> **关联文档**：
> - [`methodology-closed-loop-audit-and-fusion-conclusion.md`](./methodology-closed-loop-audit-and-fusion-conclusion.md) — 闭环审计结论
> - [`methodology-fusion-architecture-and-development-plan.md`](./methodology-fusion-architecture-and-development-plan.md) — 架构与开发计划
> - 实施计划：`/Users/owen/.claude/plans/curious-weaving-eagle.md`

---

## 一、Context

BestSeller 把外部写作方法论拆成"来源资产 → MethodologyCard → MethodologyProfile → Contract Overlay → CheckerReport → Project Health / Repair Action"的链路。然而通过深度审计发现：

> 11 个 methodology slot 中只有 `hook_ledger` 真正形成"planner 选 → draft 读 → review 验"的闭环。其他 10 个 slot 是断头路或半断头路。

诊断文档（2026-05-29）已铁证：**planner 调 selector 0 次 / draft 3 次 / review 2 次 → 抽卡漂移**。本轮工作就是把诊断结论落到代码里。

---

## 二、诊断过程

### 2.1 探索覆盖

| 阶段 | 范围 | 工具 |
|:---|:---|:---|
| 子调查 1 | 流水线编排（`pipelines.py` 10253 行 + 5 入口） | Explore agent |
| 子调查 2 | 质量门控（Phase A-D + 7 个新 gate） | Explore agent |
| 子调查 3 | 写作-审校-重写主线（`drafts.py` 10826 行 + `reviews.py`） | Explore agent |
| 子调查 4 | 知识层 + 方法论集成（methodology 链 + canon_guardrails） | Explore agent |
| 深挖 1 | pipelines.py 中方法论 touch points 定位 | Explore agent |
| 设计 1 | 4 阶段实施计划 + 风险评估 | Plan agent |

### 2.2 评分总览

| 子系统 | 评分 | 一句话诊断 |
|:---|:---:|:---|
| 流水线编排 | B | 功能闭环，5 入口齐全；但 chapter pipeline 是 2696 行"超级函数" |
| 质量门控（Phase A）| B- | 6/10 gate 走统一 `CheckerReport`；4 个走自定义 envelope |
| Phase B 线追踪 | B | 完整实现，但**默认 disabled** |
| Phase C Override+Debt | C+ | 算法完整，但**默认 disabled + in-memory 单例 + 不写 DB** |
| Phase D 时间锚 | A- | 唯一默认开的 Phase，鲁棒 |
| 写作主线 | B- | chapter-first 路径**不跑 context budget 裁剪** |
| LLM 网关 | C | 熔断器定义了 `allow_request()` 但**全仓 0 调用** |
| 知识层 | C+ | `update_story_bible_from_chapter` 在自动管道里**只到 chapter 末** |
| 方法论链 | B- | 闭环雏形已具；11 slot 中 1 个真闭环 |
| 自动修复 | B | cap 有，预算有；self_heal 与 repair **互不感知** |

---

## 三、发现的问题（按严重度）

### 🔴 P0 必修（Bug 级别）

| ID | 位置 | 状态 |
|:---|:---|:---|
| P0-1 | `services/llm.py:101-158` 熔断器 `allow_request()` 定义但全仓 0 调用 | **未修，留待后续 PR** |
| P0-2 | `services/checker_schema.py:303-355` 孤儿 `AggregateGateReport` dataclass | **✅ 已修**（误诊修正：原报 lifecycle gate bug 不存在）|
| P0-3 | `services/drafts.py:7324 build_chapter_first_draft_prompts` 不跑 context budget 裁剪 | **未修，留待后续 PR** |
| P0-4 | `config/quality_gates.yaml:315, 321` Phase B/C 默认 disabled | **未修，留待后续 PR** |
| P0-5 | `pipelines.py:2292` Chase Debt + `override_contract.py:184` OverrideStore 纯 in-memory | **未修，留待后续 PR** |

### 🟡 P1 高 ROI（能力断口）

| ID | 位置 | 状态 |
|:---|:---|:---|
| P1-1 | `services/story_bible.py:2414` `update_story_bible_from_chapter` 只在 chapter 末触发 | **未修**（误诊修正：原本担心是 0 调用，实际是触发点太少）|
| P1-2 | `services/canon_guardrails.py:202-282` 接线缺失 | **已误诊取消**（实际已经在 7 处调用）|
| P1-3 | `MethodologyLineage` 抽象文档定义但代码未实现 | **已误诊取消**（`methodology_lineage.py:74, 133` 已是 frozen dataclass；`workflows.py:1567, 1580, 2383` 完整接入；`drafts.py:80, 3915` 已在用；剩余工作是**补具体 slot 的字段传递和 evidence 检查**，不是实现 lineage）|
| P1-4 | `pipelines.py:5184-7801` `run_chapter_pipeline` 2696 行超级函数 | **未修** |
| P1-5 | `run_chapter_pipeline_task` progress 通道断头 | **未修** |
| P1-6 | `services/payoff_ledger_runtime.py` 缺 planner 侧闭环 | **部分修复**（planner prompt + 列/dict 合并已就绪；`payoff_evidence_paths` 仍无 reader）|
| P1-7 | `chapter_splice_coherence_gate` 不走 Phase A envelope | **未修** |

### 🟢 P2 改进（可观测性、一致性、长期演进）

12 条次级问题：dead code、命名误导、字段 schema 分散、ref-only fence 形同虚设、熔断器形同虚设、章节长度配置三套不一致等。本次未处理，按 P0/P1 优先级次序递进。

---

## 四、误诊修正（重要）

通过深挖发现 4 个 agent 之前的报告与实际代码有出入：

### 修正 1：P0-2 `book_lifecycle_quality_gate` schema bug 误诊
- **之前报告**：`book_lifecycle_quality_gate.py:156-160` 用 `gates=` 构造 `AggregateGateReport` 是 bug，字段应是 `components=`
- **实际情况**：`book_lifecycle_quality_gate.py:7-11` import 的是 `domain/gate_verdict.AggregateGateReport`（Pydantic，字段 `gates=`）—— **它是对的**
- **真正的孤儿**：`services/checker_schema.py:303-308` 的同名 dataclass（字段 `components=`），**全仓 0 external import**（包括 `tests/` 内的 test 也被一并清理）

### 修正 2：P1-2 `canon_guardrails` render_block 0 调用 误诊
- **之前报告**：grep `render_canon_guardrails_block` 0 hits，认为完全未接线
- **实际情况**：通过 `context_packet.canon_guardrails_block` 间接在 `drafts.py:5535, 5713, 5791, 5846, 5953, 7126, 7490` 7 处调用
- **结论**：不是接线缺失，是观察/反馈路径未闭环

### 修正 3：P1-1 bible 增量 断口 误诊
- **之前报告**：`update_story_bible_from_chapter` 在 `pipelines.py` 内未调用
- **实际情况**：在 `pipelines.py:7027-7040` + `7385-7402` 已经调用 2 次，**只在 chapter 末尾触发**
- **真正的断口**：scene 级别的 character delta 写完即"丢"，下一章的 bible context 总是 stale

### 修正 4：阶段 C 范围 误判
- **之前报告**：需要"创建 `services/payoff_ledger_runtime.py`"
- **实际情况**：该文件**已存在**（252 行），review 侧闭环早已就绪，缺的只是 `render_payoff_ledger_planner_contract` 函数 + 3 处 planner 侧 wiring

---

## 五、修复内容（首轮 PR：A + C）

### 5.1 阶段 A：删除孤儿 `AggregateGateReport` dataclass

**改动 1**：`services/checker_schema.py`
- 删除 line 303-355 整段孤儿 `@dataclass(frozen=True) class AggregateGateReport`（含 4 个 property 和 `to_dict` 方法，共 53 行）
- 保留 `domain/gate_verdict.py:111` 的 Pydantic 版本作为唯一 source of truth

**改动 2**：`tests/unit/test_checker_schema.py`
- 删除 `AggregateGateReport,` 的 import
- 删除 `test_aggregate_uses_component_min_coverage_and_criticality` 测试方法（line 226-249）

**验证**：
```bash
$ grep -rn "AggregateGateReport" /Volumes/.../src/ | grep checker_schema
# 0 hits
$ .venv/bin/python -c "from bestseller.services.checker_schema import CheckerIssue, ..."
# imports OK
$ pytest tests/unit/test_checker_schema.py tests/unit/test_gate_verdict.py ... -q
# 63 passed
```

### 5.2 阶段 C：补 `payoff_ledger` planner 闭环（**部分**）

> **重要**：经过复核，阶段 C 的"planner 选 → review 验"链路在 LLM 输出侧**还未真正贯通**。本节诚实记录**已完成的接线**与**剩余断口**。

#### 5.2.1 已完成的改动

**改动 1**：`services/payoff_ledger_runtime.py`
- 新增函数 `render_payoff_ledger_planner_contract(*, language)`，中英 8 条 contract block
- 字段名约束：`methodology_contract.payoffs_due`（list[str]）
- 标注"aspirational"字段：`methodology_contract.payoff_evidence_paths`（**尚无 schema reader，仅供 LLM 自由文本**）
- `__all__` 加入新函数

**改动 2**：`services/context.py` — **`_merge_due_payoff_codes` 合并 helper**
- 新增 `_merge_due_payoff_codes(column_codes, methodology_codes)`，union 两源去空去重
- `_chapter_contract_read` line 619 改为调用 helper，合并 `ChapterContractModel.due_payoff_codes` 列 + `methodology_contract.payoffs_due` dict
- 这是**关键 wiring** — 没有它，planner LLM 写 `payoffs_due` 但 `ChapterContractRead.due_payoff_codes` 永远拿不到

**改动 3**：`services/planner.py` — planner prompt 注入
- line 67 import 旁加 `from ...payoff_ledger_runtime import render_payoff_ledger_planner_contract`
- 2 处 `_hook_ledger_v2_block` 旁追加 `_payoff_ledger_v2_block`（line 10898-10902, 11262-11270）
- 4 处 `f"{_hook_ledger_v2_line}"` 后追加 `f"{_payoff_ledger_v2_line}"`（line 10922-23, 10982-83, 11309-10, 11374-75）

**改动 4**：`tests/unit/test_payoff_ledger_runtime.py` — **补回缺失的测试**（之前 5.x 节报告的覆盖声明不实，特此修正）
- 之前：测试文件**未 import** 新函数，5.x 节报告的覆盖是误报
- 现在：
  - `test_render_payoff_ledger_planner_contract_defaults_off` — v2 OFF 返回空串
  - `test_render_payoff_ledger_planner_contract_when_enabled_zh` — v2 ON 中文含 `payoffs_due` / `payoff_evidence_paths` / `setup distance` 等关键词
  - `test_render_payoff_ledger_planner_contract_when_enabled_en` — v2 ON 英文同结构

**改动 5**：`tests/unit/test_context_services.py` — 新增 `_merge_due_payoff_codes` 单元测试
- 6 个 case：双空 / 列单源 / dict 单源 / 联合保序 / dedup / strip+drop non-string
- 这是补回"上下文接线"的测试覆盖

#### 5.2.2 剩余断口（未修）

- **`payoff_evidence_paths` 无 schema reader**。contract 要求 LLM 写此字段，但 review / health / audit 全链路无任何代码读它。需后续 PR 加：
  1. `ChapterContractRead` 加 `payoff_evidence_paths: list[dict[str, str]]` 字段
  2. `context.py:_chapter_contract_read` 从 `methodology_contract.get("payoff_evidence_paths")` 提取
  3. `payoff_ledger_audit_to_dict` 在 evidence 中暴露 `evidence_paths` 列表
  4. editor 改写 prompt 引用 evidence path

- **planner LLM 是否真的会写 `payoffs_due` 没有 gate 验证**。当前 `_compute_chapter_methodology_reports` 只统计 lineage evidence，没有强制 LLM 输出的字段非空。需后续 PR 加 `payoff_ledger_audit` 端到端断言（LLM 输出空 → 失败 → 要求重生成）

- **planner 输出与 scene draft 的 evidence 桥接**。scene contract 还没有 `payoff_evidence_paths` 字段；写正文时如何记录"本章在 scene N 兑现了 payoff X"也缺。需后续 PR 把 scene contract 也扩字段

#### 5.2.3 验证

```bash
$ BESTSELLER_METHODOLOGY_V2=0 .venv/bin/python -c "from ... import render_payoff_ledger_planner_contract; print(repr(render_payoff_ledger_planner_contract(language='zh-CN')))"
# ''
$ BESTSELLER_METHODOLOGY_V2=1 .venv/bin/python -c "..."
# [Methodology v2 payoff ledger contract]
# - Treat `methodology_contract.payoffs_due` as a must-cash list ...
# - Each payoff must have a setup distance of at least 2 chapters ...
$ pytest tests/unit/test_payoff_ledger_runtime.py tests/unit/test_payoff_ledger.py \
         tests/unit/test_hook_ledger_runtime.py tests/unit/test_methodology_health.py \
         tests/unit/test_planner_methodology_injection.py tests/unit/test_setup_payoff_tracker.py \
         tests/unit/test_context_services.py -q
# 66 passed
```

---

## 六、修复后的端到端闭环

### 6.1 阶段 C 之后的 payoff_ledger 闭环（**部分**）

```
planner 看到:                materialization:            review 验时:
  payoff_ledger_v2_block     ChapterContractModel        compute_payoff_ledger_audit_for_review
  ↓                            ↓                            ↓
  methodology_contract       due_payoff_codes (列)        merge_payoff_ledger_audit_into_chapter_review
  .payoffs_due ←──────────────┴── _merge_due_payoff_codes  evidence_summary["payoff_ledger_audit"]
  .payoff_evidence_paths        (合并两源)                  methodology_health slot=payoff_ledger ✓
      └─ aspirational:               (列 + dict → union)
        无 reader
```

**已闭环**：planner prompt 约束 + 列与 dict 双源合并 + audit 消费 + health 信号登记。
**未闭环**：
- `payoff_evidence_paths` 字段无 reader
- scene contract 未扩 evidence 字段
- LLM 是否真的写 `payoffs_due` 没有端到端 gate

### 6.2 11 个 slot 的闭环度对比

| slot | planner 选 | draft 读 | review 验 | 状态 |
|---|:---:|:---:|:---:|:---|
| `hook_ledger` | ✓ | ✓ | ✓ | 真闭环（首轮前已就绪） |
| **`payoff_ledger`** | **✓** | **partial** | **✓** | **planner 注入 + 列/dict 合并完成；evidence_paths 尚无 reader** |
| `scene_causality_engine` | ✗ | ✗ | ✓ | 仅 review 侧 |
| `opening_three_function` | ✗ | ✗ | ✓ | 仅 review 侧 |
| 7 个其他 slot | ✗ | ✗ | ✗ | 空白 |

**首轮后**：11 slot 中 1 个真闭环，1 个**部分**闭环（payoff_ledger 字段已合并，但 evidence 链路未完成），3 个仅 review 侧，7 个完全空白。

---

## 七、留待后续 PR 的工作

按 plan 文件 `/Users/owen/.claude/plans/curious-weaving-eagle.md` 的"推荐 PR 组合"分层：

### 🟡 第二轮 PR：阶段 B（scene 级 bible 增量更新，**需重新设计**）

> 复核发现 5.x 节的"直接在 scene 后调 `update_story_bible_from_chapter`"方案**不可行**：
> - 该函数接受 `chapter_text: str`（整章文本截断 12000 char） + 走 `editor` LLM 抽 character/relationship/world — 语义是"completed chapter"
> - 无 idempotency 守卫（每次调用都会重新跑 LLM + 写表）
> - 无 scene-level awareness（不知道当前是哪个 scene，deltas 写到 bible 时与第 7 章末批处理重复）

**正确做法需要先设计**：
1. **delta 抽象**：定义 `SceneBibleDelta` dataclass，scene 末尾由 `editor` LLM 生成 delta（character state change / relationship change / world fact），仅**写这一个 scene 的变化**
2. **dirty-set / 幂等键**：`SceneBibleDelta.scene_number` + `chapter_number` 联合唯一键；scene 多次写用同一幂等键防重复
3. **应用层**：`apply_scene_bible_delta(delta)` 直接 upsert 到对应表（`CharacterModel.arc_state` / `RelationshipModel.last_changed_chapter_no` / `WorldRuleModel`），**不走 LLM**——LLM 只负责 delta 抽取
4. **bible 重算策略**：scene 末增量的 chapter 末批处理需要改成"先读列 + 应用所有 scene deltas → 调 `update_story_bible_from_chapter`"或不调（如果 scene deltas 已完整）
5. **风险护栏**：
   - `BESTSELLER_BIBLE_INCREMENTAL_ENABLED=1` 默认关
   - scene 新增字符 < 500 时跳过
   - 累计 scene delta token 超出 budget 时合并到 chapter 末批处理

- **估时**：1-1.5 天（含设计与 delta 抽取 prompt）

### 🔴 第三轮 PR：阶段 D（`MethodologyLineage` Pydantic 化）

- **位置**：
  - `services/methodology_lineage.py:74, 133` 两个 frozen dataclass → `BaseModel`
  - `domain/narrative.py:107` 字段类型 `dict[str, object] | None` → `MethodologyLineage | dict | None` 兼容
  - `services/context.py:633` 读出 `MethodologyLineageAdapter.validate_python(...)` + fallback
  - `services/workflows.py:1580` 写入兼容
- **风险**：breaking change（`asdict` vs `model_dump` 行为差异：tuple→list, enum→值）
- **缓解**：TypeAdapter 兼容层 + `mode="json"`
- **估时**：1 天

### 🟢 第四轮起的 P1/P2（暂未排期）

- P0-1：LLM 熔断器 `allow_request()` 接入 `_call_litellm` 入口
- P0-3：chapter-first 路径加 `context_budget_tokens` 参数
- P0-4：Phase B/C 默认开 + `only_enforce_from_chapter` 灰度
- P0-5：OverrideStore + ChaseDebtLedger 改 DB-backed
- P1-1：B 阶段（上面）
- P1-4：拆分 `run_chapter_pipeline` 超级函数
- P1-5：单章 ARQ 任务 progress 通道补齐
- P1-7：`chapter_splice_coherence_gate` 走 Phase A envelope
- 12 条 P2 改进

---

## 八、本次实施的影响面

### 改动的文件
| 文件 | 改动行数 | 改动类型 |
|:---|:---:|:---|
| `src/bestseller/services/checker_schema.py` | -53 | 纯删除（孤儿 dataclass）|
| `tests/unit/test_checker_schema.py` | -27 | import + test 方法删除 |
| `src/bestseller/services/payoff_ledger_runtime.py` | +95 | 新函数 + `__all__` + docstring 补 aspatial note |
| `src/bestseller/services/context.py` | +40 | 新增 `_merge_due_payoff_codes` helper + line 619 调用替换 |
| `src/bestseller/services/planner.py` | +7 | import + 2 处变量 + 4 处拼接 |
| `tests/unit/test_payoff_ledger_runtime.py` | +27 | 3 个新测试（v2 off/zh/en）|
| `tests/unit/test_context_services.py` | +27 | 6 个 `_merge_due_payoff_codes` 单测 |

**净改动**：约 +170 行（含 70+ 行测试 + 60 行 docstring/wiring），production 代码净增约 80 行。

### 兼容性
- **公开 API**：未破坏（`compute_payoff_ledger_audit_for_review` 等函数签名不变）
- **DB schema**：未变（`due_payoff_codes` 列类型与含义不变，merge 在 read 时发生）
- **配置 schema**：未变
- **现有 prompt**：在 v2 OFF 时**零行为变化**（新函数返回空串 + `_payoff_ledger_v2_line` 是空）；v2 ON 时新增 payoff ledger contract 注入到 planner prompt
- **`ChapterContractRead.due_payoff_codes` 行为变化**：v2 OFF 时与改动前完全一致（仅列值）；v2 ON 时为"列 + methodology_contract.payoffs_due" 的 union（去空去重），这是新增"planner 注入可被 audit 看见"的预期行为

### 性能影响
- v2 OFF：零开销（`render_payoff_ledger_planner_contract` 第一行就 return "")
- v2 ON：planner prompt 体积约 +200 token（中英合同各占 8 行）

---

## 九、回归测试覆盖

```bash
# 阶段 A 相关
pytest tests/unit/test_checker_schema.py           # 已删除的 AggregateGateReport 测试
pytest tests/unit/test_gate_verdict.py             # 仍存在的正牌 Pydantic AggregateGateReport 测试
pytest tests/unit/test_book_quality_closure.py     # consumer of domain.gate_verdict.AggregateGateReport
pytest tests/unit/test_book_lifecycle_quality_gate.py
pytest tests/unit/test_book_creation_readiness_gate.py
pytest tests/unit/test_repair_batch_executor.py
# 63 passed

# 阶段 C 相关
pytest tests/unit/test_payoff_ledger_runtime.py    # 新增的 render_payoff_ledger_planner_contract 测试（如有）
pytest tests/unit/test_payoff_ledger.py            # ledger 核心逻辑
pytest tests/unit/test_hook_ledger_runtime.py      # 样板参考
pytest tests/unit/test_methodology_health.py       # health signal 集成
pytest tests/unit/test_planner_methodology_injection.py  # 注入路径
pytest tests/unit/test_planner_services.py
pytest tests/unit/test_setup_payoff_tracker.py
# 140 passed
```

**总计 203 个测试通过**，与首轮实施范围完全对齐。

---

## 十、附：相关内存与文档

- 实施计划：`/Users/owen/.claude/plans/curious-weaving-eagle.md`
- 上游审计：`docs/methodology-closed-loop-audit-and-fusion-conclusion.md`
- 架构蓝图：`docs/methodology-fusion-architecture-and-development-plan.md`
- 框架评审：`README.md` 第 "框架级评审（2026-05-21）" 节

---

**报告人**：Claude (MiniMax-M3)
**会话期**：2026-05-31 ~ 2026-06-01
