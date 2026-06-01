# 反常识钩子引擎 — 验收排查结论与修改计划

> 版本：2026-06-01 · 排查对象：已完成开发的 Anti-Commonsense Hook Engine
> 排查方式：源码审计 + 调用链核验 + 单测运行 + 真实生成抽样
>
> **【2026-06-01 复查更新】所有 P0/P1 已修复并通过运行级验证。最终结论 + 融合与验证设计见文末"附录 A / 附录 B"。**

---

## 0. 总结论

**功能可用，且是"真融入"而非"旁挂"。** 但存在 **1 个必修缺陷（P0）+ 1 个逻辑不自洽（P1）+ 3 个完整性缺口（P1/P2）**。
当前状态可以跑通命题→大纲，但旗舰输出（一句话钩子）有可见的中文语法破损，且 gate 与生成器之间存在"自己生成的钩子通不过自己门禁"的不自洽。建议修完 P0/P1 再上线给作者用。

### 已验证 OK 的部分
- 7 个文件全部落地；`config/hook_mechanisms.yaml` 含 8 机制；`settings.HookEngineSettings` + `config/default.yaml hook_engine` 配置齐全。
- 领域模型 `HookSpec/HookScore/HookMechanism` frozen + 校验完整。
- H_norm 计算含 `max(0.3, L/10)` 钳制 + NaN/inf 防护，无除零风险。
- **真实接线**（grep 确认）：
  - `conception.py`：生成候选 → 选 top1 → 注入 prompt block → 产出 `ConceptionResult.hook_spec`。
  - `server.py`：quickstart 生成候选 + 接收用户选定 `hook_spec`。
  - 握手正确：`conception_result.hook_spec` → `project_metadata["hook_spec"]`，planner gate 复用同一份（不会重复生成出不一致的钩子）。
  - `planner.py`：`_run_hook_strength_gate` 挂在 foundation_plan 与 novel_plan 两处；`apply_hook_to_book_spec / world_spec / volume_plan` + `hook_outline_extra_constraints` + 4+ 处 prompt 注入 `render_hook_spec_prompt_block`。
- 6 个单测全部通过（`test_hook_strength_gate / test_anti_commonsense_hook / test_hook_propagation`）。

---

## 1. 问题清单（按严重度）

### P0-1 · 一句话钩子中文语法破损【必修】
`anti_commonsense_hook.build_hook_spec_from_mechanism()` 用模板：
```python
one_liner = f"{role}想{desire}，却必须{mechanism.reversal_template}；赢来{reward}，也付出{cost}。"
```
但多数 `reversal_template` 本身以"必须/越/最不适合"开头，拼接后产出病句：

| 机制 | 实测产出片段 | 问题 |
|---|---|---|
| rule_horror（"必须遵守反直觉规则…"） | "却必须**必须**遵守反直觉规则" | 必须重复 |
| forced_loss / emotion_value / fourth_disaster | 同样"却必须必须…" | 必须重复 |
| death_grows（"越接近死亡…"） | "却必须**越**接近死亡或失败" | 必须+越 病句 |
| hide_anti_trope / misunderstanding（"越…"） | "却必须越想苟住…" | 必须+越 病句 |
| profession_reversal（"最不适合…"） | "却必须**最不适合**战斗或主线的职业技能，反而是破局核心" | 语法断裂 |

这是整个特性最核心的对外产物（"一句话强钩子"），不能带病上线。

**修复**：
- 方案 A（最小改动）：把模板里的"却必须"改成中性连接词，并按 `reversal_template` 首词智能选择连接。例如 reversal 以"必须/越/最/反而"开头时用"，偏偏 {reversal}"或直接"——{reversal}"；否则用"却只能 {reversal}"。
- 方案 B（更好，对应原计划 Beta）：保留确定性骨架，但 `one_liner` / `core_rule` 改由 LLM 基于结构化字段扩写（role=`planner`，经 `complete_text`，传 project_id/workflow_run_id），确定性模板仅作 LLM 失败时的降级回退（回退也要先修好方案 A）。
- 同步检查 `core_rule` 模板里 `「{reversal_template}」` —— 因有书名号包裹尚可读，但建议一并顺一遍。

---

### P1-1 · 生成器选出的钩子可能通不过自己的 gate【逻辑不自洽】
`generate_hook_candidates` 按 `combined_rank` 排序取 top1，而 `combined_rank` 里 novelty 权重 0.28、H_norm 权重 0.62，导致 **高新颖度但 H_norm<30 的候选可能排到第一**。实测：
- 悬疑：3 个候选 H_norm 全部 = 28.8（< 30 阈值）→ 选出的 top1 也 < 30。
- 都市：top1=32（过），但次优 19.2 / 25.6。

后果：`_run_hook_strength_gate` 对选出的 hook 评 `passed=False / verdict="warn_only"`，但仍 stash 并向下传播 —— **命题阶段就违反了命题门禁**，gate 沦为纯观测。

**修复**：
- 生成排序时，优先返回"既排名靠前**又** `score.h_norm >= min_h_norm`"的候选；只有当全部候选都不过阈值时才回退到最高分。
- `min_h_norm` 应从 `settings.hook_engine.min_h_norm` 传入 `generate_hook_candidates`（目前生成器内部硬编码 `if score.h_norm < 30: combined -= 0.12`，与配置脱钩）。
- 增大 `attempts`/重采样直到至少 1 个过阈值（封顶次数，避免死循环）。

---

### P1-2 · 缺 rewrite-on-fail 回炉闭环【完整性，偏离原计划】
原计划：`H_norm 不达标 → 注入 rewrite_suggestions 做一次 LLM 命题补强 → 重评 → 二次仍不达标才 warning 放行；H_norm<15(reject) 不得进入写作`。
现状：`_run_hook_strength_gate` 只 生成→评分→stash，`rewrite_suggestions` 算出来从不回灌；`reject` 也不拦截。

**修复**：
- 在 gate 内加一次补强：不过阈值时，用 `rewrite_suggestions` + 当前 HookSpec 让 LLM/或换机制重采样，重评一次。
- `reject`（H_norm<15）至少在 Mode B `invariants.md` 落一条红线（是否硬阻断由你定，建议至少 ERROR 级告警 + 阻止进入写作）。

---

### P1-3 · 去重腿失效【完整性，原计划 1.0 未落地】
`generate_hook_candidates(duplicate_risk_fn=...)` 形参存在，但 `conception.py` 与 `planner.py` 调用时**都没传**，导致 `duplicate_risk` 恒为 0，`combined_rank` 的去重项（权重 0.10）形同虚设。

**修复**：接入现有 `services/deduplication.py`，传入一个 `duplicate_risk_fn(spec) -> float`（对 `one_liner`/`core_rule` 与库存 + 历史命题做近重复检测）。

---

### P1-4 · free-text evaluate 路径不可靠【功能限制】
`extract_hook_spec_from_text` 是硬编码正则，几乎不解析真实命题语义，对任意输入都填充近似固定的 constraints/cost。实测对"看见未来但只会掉头发"类输入给出 H=2.07，看似"对"（报告也判该命题弱），但**强命题同样会被打到低分**——评分不反映真实输入。

**修复**：若要保留"评估已有命题"用例（报告 evaluate API），需把抽取改为 LLM 辅助（structured extraction → HookSpec），正则仅作降级。若暂不需要该用例，应在文档/API 标注"free-text 评分为粗估"。

---

### P2-1 · one_liner 全程模板化、无 LLM 扩写
同机制不同书的钩子高度雷同（见 P0-1 实测，都是"主角想X，却必须…；赢来…，也付出…"）。对"让用户点进来"的传播性有损。归入 P0-1 方案 B 一并解决。

### P2-2 · 测试偏薄、无 planner 集成测试
仅 6 个单测，`apply_hook_to_*` 为孤立单测，**没有** "generate_foundation_plan 端到端把 hook 注入 book/world/volume spec" 的集成测试，也没有覆盖 `_run_hook_strength_gate` 的分支（hook_spec 已存在 / 需生成 / 全部不过阈值）。建议补 2–3 个集成测试 + 提升新模块行覆盖。

### P2-3 · 次要
- `select_mechanisms_for_genre` 用子串双向匹配，"都市" vs "都市异能" 可命中但也可能误配，建议规整为标签集合匹配。
- `anti_commonsense_mechanisms.DEFAULT_HOOK_MECHANISMS_PATH = Path("config/hook_mechanisms.yaml")` 为相对路径——与 `settings.py:DEFAULT_CONFIG_PATH` 同惯例，运行时从仓库根启动可用；**可接受**，但更稳妥可参照 `compliance_boundary_kernel.py` 用 `Path(__file__).resolve().parents[3]/"config"/...`。

---

## 2. 修改优先级与工作量

| 编号 | 问题 | 优先级 | 预估 | 验收 |
|---|---|---|---|---|
| P0-1 | 一句话钩子语法破损 | **必修** | 0.5–1 天（方案A）/ +1 天（方案B LLM 扩写） | 8 机制各抽样 ≥3 条 one_liner 无"必须必须/必须越"等病句；新增断言测试 |
| P1-1 | 选出的钩子要过自己的 gate | **必修** | 0.5 天 | 各题材 top1 候选 `h_norm >= min_h_norm`（或全不过时显式回退路径有测试覆盖） |
| P1-2 | rewrite-on-fail 闭环 + reject 红线 | 高 | 1 天 | 不过阈值触发一次补强重评的测试；invariants.md 增红线 |
| P1-3 | 去重腿接 deduplication | 高 | 0.5 天 | duplicate_risk 在重复输入下 >0 且影响排序的测试 |
| P1-4 | free-text evaluate 可靠性 | 中 | 1 天（LLM 抽取）或 0.1 天（标注限制） | 看你是否需要该用例 |
| P2-2 | 集成测试补全 | 中 | 0.5 天 | foundation_plan 注入 book/world/volume 的集成测试通过 |
| P2-1/2-3 | 模板化/匹配/路径 | 低 | 合并到上面 | — |

**建议落地顺序**：P0-1 → P1-1 → P1-2 → P1-3 → P2-2 →（按需）P1-4 → P2-3。

---

## 3. 触碰文件清单（修复用）
- `src/bestseller/services/anti_commonsense_hook.py` —— P0-1（one_liner/core_rule 拼接 + 可选 LLM 扩写）、P1-1（选过阈值候选、传 min_h_norm）、P1-3（接 duplicate_risk_fn）
- `src/bestseller/services/hook_strength_gate.py` —— P1-2（补强重评）、P1-4（LLM 抽取）
- `src/bestseller/services/planner.py` —— P1-2（gate 回炉）、P1-3（传 duplicate_risk_fn）
- `src/bestseller/services/conception.py` —— P1-1/P1-3（传 min_h_norm + duplicate_risk_fn）
- `src/bestseller/services/anti_commonsense_mechanisms.py` —— P2-3（匹配/路径）
- `.claude/skills/bestseller-framework/invariants.md` —— P1-2（reject 红线）
- `tests/unit/` + `tests/`（新增集成测试）—— P0-1 病句断言、P1-1、P1-2、P1-3、P2-2

---

## 4. 一句话结论
**核心链路对、融入到位、不孤儿；但"一句话钩子"本身有中文病句（P0），且生成器会选出连自己门禁都过不了的钩子（P1）——这两条必须先修，其余按完整性补齐即可。**

---

# 附录 A · 最终验收结论（复查）

## A.1 修复核对表（逐条运行级验证）

| 编号 | 问题 | 修复手段 | 验证证据 | 结论 |
|---|---|---|---|---|
| P0-1 | 一句话钩子中文病句 | `anti_commonsense_hook._render_reversal_phrase()`：reversal 以"必须/越/最/反而/不能/只有"开头→用"偏偏"，否则"却只能" | 8 机制全量抽样 **broken=0**（之前"必须必须/必须越/必须最不适合"全部消除）；回归测试 `test_all_mechanism_one_liners_avoid_broken_must_prefixes` | ✅ 达标 |
| P1-1 | 选出的钩子过不了自己的 gate | `generate_hook_candidates` 透传 `min_h_norm`，排序后 `ordered=[*passing,*failing]` 优先返回过阈值候选 | 都市/玄幻/悬疑/科幻/言情 **top1 全部 ≥30**（悬疑 28.8→36.0）；测试 `test_threshold_selection_prefers_passing_hook_when_available` | ✅ 达标 |
| P1-2 | 缺 rewrite 回炉 + reject 不拦截 | 生成器在"全员不过阈值"时调 `repair_hook_spec_once`；planner gate 在 `not passed` 时补强重评；**`verdict==reject`(H<15) → 抛 `PlannerFallbackError`** | 抛错被 `generate_novel_plan`(try@14363/except@15835) 与 `generate_foundation_plan`(try@15953/except@16518) 捕获 → `WorkflowStatus.FAILED`，**优雅失败不崩溃**；测试 `test_repair_hook_spec_once_improves_failed_structured_hook` | ✅ 达标 |
| P1-3 | 去重腿失效 | `build_hook_duplicate_risk_fn` 用 `deduplication.compute_jaccard_similarity`；conception 与 planner gate **均已传入** `duplicate_risk_fn` + `min_h_norm` | 近重复输入 `duplicate_risk=0.606`（之前恒 0）；测试 `test_duplicate_risk_fn_marks_near_duplicate_and_affects_payload` | ✅ 达标 |
| P1-4 | free-text 评估不可靠 | `extract_hook_spec_from_text` 显式标注"coarse heuristic / rough estimate, not semantic extraction"，建议生产路径传结构化 HookSpec | 文档化限制（未上 LLM 抽取，符合计划的二选一） | ✅ 可接受 |

测试：`test_hook_strength_gate / test_anti_commonsense_hook / test_hook_propagation` 共 **10 passed**。

## A.2 总评
**修复符合预期。** 核心链路正确、优雅失败、回归测试到位。剩余仅 P2 级（集成测试覆盖、free-text LLM 抽取、机制匹配精度）——不阻塞上线，建议纳入下一迭代（见附录 B.3 的 L2/L3 自动化）。

---

# 附录 B · 能力如何与框架融合 + 融合如何起效 + 验证逻辑设计

## B.1 融合点（能力挂在框架的哪些关节）

钩子引擎不是旁路工具，而是嵌进**命题→大纲→细纲**主链路的三个关节：

```
[quickstart / server.py]
   ├─ generate_hook_candidates() ──► 给用户 N 个候选钩子（含 H_norm/verdict/rank）
   └─ 用户选定 hook_spec ─┐
                          ▼
[conception.run_conception_pipeline]
   ├─ 无选定则自动生成 top1 候选（带 min_h_norm + 去重）
   ├─ render_hook_spec_prompt_block() 注入 conception prompt
   └─ 产出 ConceptionResult.hook_spec ──► project_metadata["hook_spec"]   (握手)
                          ▼
[planner.generate_foundation_plan / generate_novel_plan]
   ├─ _run_hook_strength_gate(): 复用/生成 → 评分 → 不过则 repair → reject 则 FAILED
   ├─ apply_hook_to_book_spec()   ── logline / reader_promise / series_engine
   ├─ apply_hook_to_world_spec()  ── rules[] / power_system.hard_limits / exploitation_potential
   ├─ apply_hook_to_volume_plan() ── 每卷 escalation_axis + volume_resolution.cost_paid
   └─ hook_outline_extra_constraints() ── 章节 conflict_stakes / conflict_buffs / dramatic_irony
```

**关键设计：握手一致性**。conception 选定的 hook 写入 `project.metadata_json["hook_spec"]`，planner gate 优先 `coerce_hook_spec(metadata["hook_spec"])` 复用同一份——用户在 quickstart 看到的钩子，与最终向下传播、约束世界观/章节的钩子是**同一个**，不会出现"展示 A、落地 B"。

## B.2 融合如何"起效果"（钩子如何真正约束成品，而非只生成一句文案）

效果 = **HookSpec 字段穿透到最终产物**。一句话钩子之所以"起效"，是因为它的机制字段被强制翻译成下游硬约束：

| HookSpec 字段 | 落到哪个产物字段 | 起的作用 |
|---|---|---|
| `one_liner` | BookSpec.logline / series_engine.reader_promise | 对外卖点、黄金三章承诺 |
| `core_rule` | series_engine.core_serial_engine / first_three_chapter_hook | 连载发动机 |
| `constraints{}` | WorldSpec.rules[].story_consequence + power_system.hard_limits | 把"限制"变成硬世界规则，防止主角开挂 |
| `anti_cheat[]` | WorldSpec.rules[].exploitation_potential（取反）+ 章节 conflict_buffs | 防设定空转/降智破局 |
| `costs[]` | 每卷 volume_resolution.cost_paid + 章节 conflict_stakes | 每次爽点绑定可见代价，维持张力 |
| `misunderstanding` | 章节 dramatic_irony_intent / reveal_mode（黄金三章） | 迪化/误解爽点落到具体场景 |
| `arc_engine[]` | 每卷 anti_commonsense_escalation_axis | 场景/规则/代价/误解逐卷升级，支撑长篇 |

**判断"起没起效"的唯一客观标准**：上述映射在生成产物里出现的"穿透率"。这正是 B.3 验证逻辑的核心。

## B.3 验证逻辑设计（四层金字塔）

> 当前只覆盖了 L1。要证明"融合能力真的起效果"，必须补 L2/L3，并用 L4 做业务度量。

### L1 · 单元层（✅ 已完成，10 测试）
- 评分确定性、H_norm 公式、阈值 verdict 边界、grammar 回归、阈值优选、去重、repair。
- **守护**：把这 3 个测试文件纳入 CI 必跑；新增机制时强制跑 grammar 回归。

### L2 · 集成层（🔲 当前缺口，最该补）
对 `generate_foundation_plan` 写端到端测试（mock LLM 返回固定 book/world/volume 骨架）：
1. 给定强 premise + hook_spec → 断言 `book_spec.logline == hook.one_liner`、`world_spec.rules` 含 `rule_id=hook_*`、`power_system.hard_limits` 含 constraints、`volume_plan[i].volume_resolution.cost_paid` 含 costs。
2. 给定 reject 级 premise（H<15）→ 断言抛 `PlannerFallbackError` 且 `workflow_run.status==FAILED`。
3. 握手测试：metadata 已有 hook_spec → gate 复用而非重新生成（断言 one_liner 不变）。

### L3 · 传播穿透审计（🔲 核心效果验证，建议做成可复用工具）
新增 `services/hook_penetration_audit.py`（仿现有 gate 形状，产出 Report）：
输入一个已生成项目的 artifacts，输出**穿透率指标**：

| 指标 | 计算 | 达标线（建议） |
|---|---|---|
| one_liner 一致率 | logline/reader_promise 是否等于/包含 hook.one_liner | 100% |
| constraint 穿透率 | hook.constraints 中有多少条出现在 world rules/hard_limits | ≥ 80% |
| cost 穿透率 | hook.costs 是否出现在 ≥1 卷 cost_paid 或 ≥30% 章节 conflict_stakes | ≥ 1 卷 + 早期章节 |
| misunderstanding 落地 | 若 hook 有 misunderstanding，黄金三章是否 ≥1 场含 dramatic_irony/reveal_mode | ≥ 1 场/章 |
| escalation 覆盖 | arc_engine 轴是否分布到各卷 | 每卷 ≥1 轴 |

这个审计可以：①作为 Mode B 写作前的"命题落地体检"；②作为离线批量回归（跑 N 本生成项目，看穿透率分布）。**穿透率低 = 钩子没起效，需回查 prompt 注入或 apply_* 逻辑。**

### L4 · 效果/业务层（🔲 度量"钩子强不强、有没有用"）
- **命题质量分布**：批量生成 → H_norm 均值/过阈率/reject 拦截率/去重命中率（接现有 `*_telemetry` 模式）。
- **LLM 评审**：用 critic 角色对"黄金三章是否真实体现反常识机制 + 一句话钩子吸引力"打分（新鲜度/可读性/可连载性/传播性 1–5）。
- **A/B**：`hook_engine.enabled` 开/关两组，比较黄金三章 critic 评分、命题去重率、（上线后）点击/收藏代理指标。每组样本 ≥ 一定量。

### 验证执行顺序建议
1. 先补 **L2 集成测试**（防回归，0.5 天）。
2. 再建 **L3 穿透审计工具 + 阈值**（这是"起效果"的客观证据，1 天）。
3. L4 接 telemetry + 一次 A/B（持续）。

## B.4 一句话
**融合靠"握手一致 + 字段穿透"，起效靠"HookSpec 翻译成下游硬约束"，验证靠"L2 端到端 + L3 穿透率审计 + L4 业务度量"——其中 L3 穿透率是判断钩子有没有真正起作用的唯一客观标尺，也是当前最该补的一块。**
