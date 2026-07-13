# BestSeller 全链路问题分析与修改建议

> 分析范围：题材选择 → 书籍创建 → Story Bible 生成 → 规划（book_spec / world_spec / cast_spec / volume_plan / chapter_outline）→ 章节正文生成 → 审校重写 → 组装导出 → 平台发布
>
> 分析日期：2026-07-09；复核修订：2026-07-10（对照代码逐条核验，修正 P2-1 降级机制描述、P3-1 行号与摘要丢字段结论、P4-1 重复次数按路径细化并降级为中、P4-5 注入链与"无现行重复"结论、P5-1/P5-2 行号、P6-1/P6-2 单章 Markdown 例外等）
>
> 原则：仅分析，不改动代码

---

## 目录

- [一、全链路架构总览](#一全链路架构总览)
- [二、阶段一：题材选择与书籍创建](#二阶段一题材选择与书籍创建)
- [三、阶段二：Conception 多 Agent 立项](#三阶段二conception-多-agent-立项)
- [四、阶段三：Planner 规划流水线](#四阶段三planner-规划流水线)
- [五、阶段四：章节正文生成](#五阶段四章节正文生成)
- [六、阶段五：审校与重写](#六阶段五审校与重写)
- [七、阶段六：组装与导出](#七阶段六组装与导出)
- [八、阶段七：平台发布](#八阶段七平台发布)
- [九、跨阶段系统性问题](#九跨阶段系统性问题)
- [十、问题优先级总览](#十问题优先级总览)

---

## 一、全链路架构总览

```
用户输入 (题材/章数/字数)
    │
    ├─ Web UI路径 ─────────────────────────────────────────┐
    │   题材选择 → Story Architect → Conception(10+轮LLM)  │
    │   → 创建项目 → Autowrite → Planner → Story Bible     │
    │                                                       │
    ├─ CLI路径 ──────────────────────────────────────────┐  │
    │   genre字符串 → 创建项目 → Autowrite → Planner     │  │
    │   (无题材解析/无Conception)                         │  │
    │                                                     │  │
    └─ API路径 ────────────────────────────────────────┐  │  │
        resolve_selection → 创建项目                   │  │  │
        (有解析/无Conception/无Planner/无Bible)         │  │  │
        └──────────────────┬───────────────────────────┘  │
                           │                               │
                    ┌──────▼──────┐                        │
                    │  Planner    │◄───────────────────────┘
                    │  规划流水线  │
                    └──────┬──────┘
                           │
            ┌──────────────▼──────────────┐
            │ book_spec → world_spec →     │
            │ cast_spec → volume_plan →    │
            │ chapter_outline              │
            └──────────────┬──────────────┘
                           │
                    ┌──────▼──────┐
                    │ Story Bible │
                    │ 物化         │
                    └──────┬──────┘
                           │
              ┌────────────▼────────────┐
              │ 章节正文生成             │
              │ (场景级 / 整章级双路径)  │
              │ + 上下文装配             │
              │ + 爽点引擎               │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │ 审校 → 重写 → 质量门禁   │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │ 组装 → 导出 (MD/DOCX/    │
              │ EPUB/PDF)               │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │ 发布 (番茄/起点/七猫/    │
              │ Amazon KDP)             │
              └─────────────────────────┘
```

---

## 二、阶段一：题材选择与书籍创建

### 问题 P1-1：[严重] 三条创建路径能力严重不对等

**文件**：`src/bestseller/cli/main.py`、`src/bestseller/api/routers/projects.py`、`src/bestseller/web/server.py`

**问题**：

项目存在三条书籍创建路径，能力差异巨大：

| 能力 | Web UI | CLI autowrite | API |
|------|--------|---------------|-----|
| `resolve_selection` 题材解析 | ✅ | ❌ | ✅ |
| Story Architect 切面生成 | ✅ | ❌ | ❌ |
| Conception 多 Agent 讨论（10+ 轮 LLM） | ✅ | ❌ | ❌ |
| Planner book_spec 生成 | ✅ | ✅ | ❌ |
| Story Bible 物化 | ✅ | ✅ | ❌ |

CLI 路径用单次 LLM 调用生成 book_spec，而 Web UI 的 Conception 有 5+ 轮多 Agent 讨论 + 概念淘汰赛 + 反俗套创意探索。同样题材，CLI 产出的规划质量远低于 Web UI，且更容易陷入同质化。

**建议**：

1. 将 Conception Pipeline 提取为独立服务函数，CLI/API 均可调用
2. CLI `project autowrite` 增加 `--conception` flag，默认启用
3. API `POST /projects` 增加 `conception` 可选参数，支持异步 conception 后创建
4. 短期：至少让 CLI 路径调用 `resolve_selection` 完成题材解析

---

### 问题 P1-2：[严重] CLI 路径完全绕过题材解析

**文件**：`src/bestseller/cli/main.py` 行 1601-1677（`project autowrite`）、行 1212-1331（`project create`）

**问题**：

两个 CLI 命令直接用用户传入的 `genre` 字符串构造 `ProjectCreate(genre=genre, ...)`，**不调用 `genre_taxonomy.resolve_selection`**。导致：

- CLI 创建的项目不会有 `genre_canonical` / `genre_category` / `genre_pack` metadata
- 下游 `prompt_pack` 选择走 `infer_default_prompt_pack_key`（另一套独立的 keyword 匹配），与 `genre_taxonomy.canonicalize` 可能不一致
- 对比 API 路径调用了 `resolve_selection`，CLI 与 API 行为不一致

**建议**：

在 CLI `project create` 和 `project autowrite` 中增加 `resolve_selection` 调用，将 resolved 结果写入 `metadata.genre_canonical` / `metadata.genre_category` / `metadata.genre_pack`。

---

### 问题 P1-3：[中] API 路径 genre 字段与 resolved 结果不一致

**文件**：`src/bestseller/api/routers/projects.py` 行 48-68

**问题**：

行 48 调用了 `resolve_selection`，把 `genre_canonical`/`genre_category`/`genre_pack` 存入 metadata（行 55-57），但行 62 `genre=body.genre` 仍用用户传入的原始字符串，而非 `resolved.genre_str`。

如果用户传 `"修仙"`，`resolved.genre_str` 可能是 `"仙侠"`，但 `project.genre` 存的是 `"修仙"`。下游消费者需要各自再做 canonicalize。

**建议**：

行 62 改为 `genre=resolved.genre_str`（或 `resolved.genre_canonical`），确保 `project.genre` 与 metadata 一致。

---

### 问题 P1-4：[中] genre_taxonomy.yaml 与 _GENRE_PRESETS 双轨未完全收敛

**文件**：`src/bestseller/services/writing_presets.py` 行 952 `_GENRE_PRESETS`、`src/bestseller/services/conception.py` 行 602-624

**问题**：

`genre_taxonomy.yaml` 是新的"单一权威源"，但 `_GENRE_PRESETS` 仍作为 62 卡硬编码列表存在。Conception 的 `_build_genre_context` 先查 `_GENRE_PRESETS`，找不到才从 taxonomy 合成。两套题材定义并存，收敛未完成。

**建议**：

将 `_GENRE_PRESETS` 改为从 `genre_taxonomy.yaml` 动态生成（或标记为 deprecated fallback），完成单一权威源收敛。

---

## 三、阶段二：Conception 多 Agent 立项

### 问题 P2-1：[中] Conception Pipeline 轮次过多，成本与延迟高

**文件**：`src/bestseller/services/conception.py` 行 3219 `run_conception_pipeline`

**问题**：

一次 conception 可能调用 10+ 次 LLM：

| 轮次 | Agent | 说明 |
|------|-------|------|
| -1 | concept_tournament | 概念淘汰赛：反俗套 + 杂交 N 候选 + 审计 + 判官对撞 |
| 0 | commercial_commissioner | 商业定位 |
| 1 | market_strategist | 市场策略 |
| 1 | character_architect | 角色提案 + cast_reality_audit |
| 1 | world_builder | 世界观提案 |
| 2 | chief_editor | 交叉审查 |
| 2.5 | creative_explorer | 反俗套创意探索 |
| 3 | project_director | 合并定稿 |
| 3.5 | mechanism_echo_gate | 机制回声门 + 反债务重试 |
| 3.6 | world_model_deriver | 世界模型推导 |
| 后续 | story_spine_polish / blurb_synopsis_polish / title_polish | 打磨 |

串行 LLM 调用导致时间开销大、Token 成本高（Round 1 三次调用为顺序 await：行 3429 / 3450 / 3483，全文件无 `asyncio.gather`）。降级机制分两层：核心讨论轮（Round 0-3）没有 try/except，失败时靠 `_llm_call_json` 的 `fallback=` 参数返回兜底载荷（行 554-591）；概念淘汰赛、机制回声门、世界模型推导等辅助门用 try/except fail-open，但会记 `logger.warning(exc_info=True)`。两层降级都不会反映到 ConceptionResult 上，下游无法感知质量降级。

**建议**：

1. Round 1 的三个独立 Agent（market/character/world）可并行执行（已有 asyncio 基础设施）。注意 character_architect 之后串联着 cast_reality_audit（行 3463，依赖角色提案），并行结构应为 market ∥ (character → audit) ∥ world
2. 增加 `conception_tier` 参数（`fast` / `standard` / `full`），`fast` 模式跳过 concept_tournament 和 creative_explorer
3. 在 ConceptionResult 中增加 `degraded_rounds: list[str]` 字段，记录哪些轮次被跳过/失败，供下游感知质量降级

---

### 问题 P2-2：[中] logline 字段曾被静默丢弃——Schema 与产出不同步的系统性风险

**文件**：`src/bestseller/domain/project.py` 行 71-74

**问题**：

Conception 把冠军简介同源提炼的 logline 写进 `market.logline`，但 `MarketPositioningConfig` schema 原先没有该字段 → Pydantic 静默丢弃，提炼调用整个被浪费。已补字段修复，但注释说明这是 tracked 书 `market.logline` 为空的根因。

这表明存在 **schema 与上游产出不同步的系统性风险**：Conception 产出的字段如果 schema 没有显式定义，Pydantic 会静默丢弃。

**建议**：

1. 在 `ConceptionResult` 和 `MarketPositioningConfig` 之间增加字段一致性测试（`test_conception_result_fields_all_consumed`）
2. 考虑在 `MarketPositioningConfig` 中设置 `model_config = ConfigDict(extra='forbid')`，防止未来类似问题。注意该 model 当前无任何显式 `model_config`（Pydantic v2 默认 `extra="ignore"` 正是静默丢弃的根因）；`forbid` 会让 LLM 脏输出直接抛错，若管线上游未做清洗，一致性测试是更安全的第一步

---

### 问题 P2-3：[设计观察] 反同质化护栏的对抗性设计暗示 LLM 同质化问题严重

**文件**：`src/bestseller/services/conception.py`

**问题**：

大量反同质化护栏的存在说明框架在反复对抗 LLM 的同质化倾向：

- `_GOLDEN_FINGER_DESIGN_PRINCIPLE`（行 225-280）：13 种金手指形态池
- `_default_motif_guardrail`（行 283-302）：禁家族失踪/死亡/退婚/通用复仇
- `_anti_debt_metaphor_guardrail`（行 345-374）：禁金融记账形态
- `_sanitize_forbidden_default_motifs`（行 377-394）：正则后处理强制替换
- `_attach_mechanism_dedup`：跨书机制去重

这些护栏是必要的，但也使提示词极其复杂（conception.py 超 4400 行，`_finalize_user_prompt` 单个函数近 160 行），维护成本高。

**建议**：

这不是一个"要修的 bug"，而是一个架构观察：反同质化护栏的复杂度已接近维护极限。长期方向可考虑将护栏规则外部化为可配置的规则引擎（YAML/DSL），而非内联在 Python 代码中。

---

## 四、阶段三：Planner 规划流水线

### 问题 P3-1：[中] 阶段间数据传递仅依赖 summarize 摘要，已证实丢失关键约束

**文件**：`src/bestseller/services/planner.py` 行 88-92（导入 summarize_*，函数定义在 `planning_context.py`）、行 14342-14473（book_spec → world_spec，`_world_spec_prompts`）、行 14584-14691（→ cast_spec）

**问题**：

所有阶段间数据传递通过 `summarize_book_spec` / `summarize_world_spec` / `summarize_cast_spec` 摘要实现。经核实，`summarize_book_spec`（`planning_context.py:60-149`）只保留 title/logline/genre/audience/tone/themes、protagonist 的 name/archetype/golden_finger/core_wound/external_goal、stakes、series_engine、key_characters、antagonist_forces——**确实丢弃了** `narrative_lines.core_axis.phrasing_tokens`、`protagonist.psych_profile`、`power_system.tiers`，下游阶段无法从摘要引用这些约束。

目前 volume_plan 通过直接读取 `book_spec` dict 绕过了摘要（行 15084 直接取 `narrative_lines`，这正是摘要丢字段的旁证），但 cast_spec 和 outline 阶段没有这个绕过机制，只能依赖摘要的保真度。

**建议**：

1. 为 `summarize_*` 函数补齐已证实丢弃的关键约束字段（`narrative_lines.core_axis.phrasing_tokens`、`protagonist.psych_profile`、`power_system.tiers`）
2. 增加 `summarize_*` 字段保留测试：输入含特定字段的 spec，断言摘要输出包含这些字段
3. 考虑为关键阶段（volume_plan / outline）提供直接访问上游完整 JSON 的可选通道

---

### 问题 P3-2：[中] 中英文提示词字段不对称

**文件**：`src/bestseller/services/planner.py`

**问题**：

多处中英文提示词不对等：

| 位置 | 中文版 | 英文版 |
|------|--------|--------|
| book_spec 输出（行 14228-14236 vs 14202-14206） | 要求 `unique_hook` + `benchmark_works` | **缺失** |
| outline 钩子分类法（行 15272 / 15790） | `render_outline_hook_taxonomy_block` 注入 | **不注入**（调用在 `if not is_en:` 内） |
| outline 黄金开篇规则（行 15274 / 15793） | `render_golden_opening_rules_block` 注入 | **不注入**（调用在 `if not is_en:` 内） |
| volume_outline 事实自检（行 16129-16135 vs 16030-16034） | 有场景卡事实一致性自检指令 | **缺失** |

英文项目缺少反同质化锚点（unique_hook）、阶段感知方法论指导、事实一致性自检，可能导致英文项目规划质量 silently 退化。

**建议**：

1. book_spec 英文版增加 `unique_hook` 和 `benchmark_works` 输出要求
2. `render_outline_hook_taxonomy_block` 和 `render_golden_opening_rules_block` 增加英文版本
3. volume_outline 英文版补充事实一致性自检指令

---

### 问题 P3-3：[中] 反同质化约束异常静默降级

**文件**：`src/bestseller/services/planner.py` 多处 `try/except Exception`

**问题**：

以下反同质化块都是 `try/except` 静默降级：

- `narrative_lines_constraints_block`（行 14250-14288）
- `world_constraints_block`（行 14456-14469）
- `foundation_constraints_block`（行 14749-14764）
- `antagonist_lifecycle_constraints_block`（行 14772-14809）
- `relationship_constraints_block`（行 14817-14861）
- `foreshadowing_constraints_block`（行 15060-15076）
- `narrative_lines core_axis threading`（行 15083-15117）

如果这些模块的 import 或渲染失败（YAML 配置缺失、函数异常），提示词会在**完全无反同质化约束**的情况下生成，且只记 debug 日志。生产环境中如果约束静默丢失，规划质量会退化到"道种破虚"失败模式，但很难从日志发现。

**建议**：

1. 至少将日志级别从 `logger.debug` 提升到 `logger.warning`
2. 在 prompt 中留占位标记（如 `[反同质化约束因异常未注入]`），让下游 repair gate 能检测到约束缺失
3. 考虑增加 `constraints_health` 指标，在规划完成后报告注入成功率

---

### 问题 P3-4：[低] compact_outline_mode 下题材感知被完全禁用

**文件**：`src/bestseller/services/planner.py` 行 16169-16172

**问题**：

紧凑模式（≤12 章）下，`genre_instruction` 和 `category_context` 都被跳过。短篇项目完全失去题材感知能力——升级流短篇不会注入力量体系分层要求，言情短篇不会注入关系驱动要求。

**建议**：

即使紧凑模式也应注入精简版题材指令（1-2 行核心要求），而非完全跳过。可用 `_compact_prompt_block_text(max_chars=500)` 压缩。

---

### 问题 P3-5：[低] cast_spec 紧凑输出合同与人格底层要求存在张力

**文件**：`src/bestseller/services/planner.py` 行 14724-14741 vs 行 14606-14619

**问题**：

紧凑输出合同要求"每个角色的自由文本字段控制在一句具体中文内"，但人格底层要求主角完整填写 5 大块（psych_profile / life_history / social_network / beliefs / family_imprint）。模型可能在"紧凑"压力下输出稀疏的人格底层。

**建议**：

在紧凑合同中明确"人格底层 5 块不受一句限制"，给出字段级最小填充标准。

---

### 问题 P3-6：[低] _outline_prompts 与 _volume_outline_prompts 重复维护

**文件**：`src/bestseller/services/planner.py` 行 15122-15487 vs 行 15610-16183

**问题**：

两个函数共享约 60% 的逻辑（黄金三章、多样性约束、世界观合规字段、标题合同等），但各自独立维护。约束文本不完全一致（如标题禁止列表不同）。未来更新不同步会导致全书一次性章纲和逐卷章纲的标准漂移。

**建议**：

提取共享约束块为独立函数（如 `_render_golden_three_rules(language, chapter_number)`、`_render_title_contract(language, existing_titles)`），两个 outline 函数共用。

---

## 五、阶段四：章节正文生成

### 问题 P4-1：[中] AI 套话黑名单多处独立维护，场景级路径内重复注入

**文件**：
- `src/bestseller/services/drafts.py` 行 3394-3425（中文 `_NOVEL_OUTPUT_PROHIBITION`，黑名单在行 3405、3411-3418 内部就出现两处；行 3427-3462 为英文版 `_NOVEL_OUTPUT_PROHIBITION_EN`）
- `src/bestseller/services/drafts.py` 行 6202-6209（场景级 system EXAMPLES）
- `src/bestseller/services/drafts.py` 行 8654-8658（整章 system EXAMPLES）

**问题**：

同一批 AI 套话（"血液仿佛凝固了"、"心中五味杂陈"、"这一切才刚刚开始"等）的重复情况按路径不同：

- **场景级路径**（`build_scene_draft_prompts`）：注入 `_NOVEL_OUTPUT_PROHIBITION`（内部黑名单出现两处）+ EXAMPLES 段（行 6202），同一 prompt 中约出现 **3 次**，估计浪费数百至 ~1000 token
- **整章路径**（`build_chapter_first_draft_prompts`）：不注入 `_NOVEL_OUTPUT_PROHIBITION`，黑名单只在 EXAMPLES 段（行 8654-8658）出现 **1 次**，无重复

注意：`build_anti_slop_footer`（prompt_constructor.py:900-925）只含通用去 AI 味铁律（论文腔、工整句式等），**不含**这批具体套话，不构成重复源。

即便整章路径无重复，黑名单在 drafts.py 内至少 3 处独立维护（3405、3411-3418、6202-6209、8654-8658），新增套话需改多处，极易漂移；场景级路径的 3 次重复也不会增强效果，反而稀释其他指令的注意力。

**建议**：

1. 将 AI 套话黑名单提取为单一数据源（如 `AI_SLOP_BLACKLIST` 常量）
2. 场景级路径在 prompt 装配时只注入一次，放在最高优先级位置
3. 其他位置引用该常量做后置检测，不在 prompt 中重复列出

---

### 问题 P4-2：[严重] output_rules 块职责严重过载

**文件**：`src/bestseller/services/drafts.py` 行 8849-8891

**问题**：

名为"输出要求"的块实际混杂了近 20 类不同约束（下表归并为 19 类，按细粒度拆可达 25 条以上）：

| # | 约束类型 | 示例 |
|---|---------|------|
| 1 | 字数硬范围 | `正文必须连贯，篇幅硬范围是 {min}-{max}` |
| 2 | 段落数建议 | `全文建议22-32段，最多36段` |
| 3 | 单场字数控制 | `单场通常控制在 {min}-{max} 字内` |
| 4 | 场景卡压缩禁止 | `不得把场景卡压缩成一句概述` |
| 5 | 离场状态规则 | `到第8段还没完成离场状态，必须用1段收束` |
| 6 | 场景卡硬边界 | `入场状态、离场状态和 forbidden_actions 是硬边界` |
| 7 | 死亡/不可逆事件禁止 | `未写在场景卡里的死亡、关键不可逆事件一律禁止` |
| 8 | 电话/短信用途限制 | `电话/短信只能作为同一视角内的现实沟通工具` |
| 9 | 超 42 段删减策略 | `优先删解释、删重复氛围、删二次推理` |
| 10 | 模板化重复禁止 | `不得出现模板化重复句式` |
| 11 | 非专业角色认知限制 | `非专业角色只能描述亲眼看见的异常` |
| 12 | 叙述者贴标签禁止 | `叙述者也不要替普通角色贴规则标签` |
| 13 | 场景分隔符禁止 | `正文不得使用 ---、***、空行切场` |
| 14 | 转场动作要求 | `每次更换地点或时间，必须先写一句可见转场动作` |
| 15 | 章末 120 字规则 | `章末最后120字必须满足"钩子+落地帧"` |
| 16 | 章末单钩子规则 | `章末只能保留一个主钩子` |
| 17 | 功能性人物称谓规则 | `不得临时发明未在场景卡中出现的人名` |
| 18 | 角色安全块规则 | `角色安全块要求某角色本章不能确认死亡` |
| 19 | 信息量删减优先级 | `优先删解释和术语，保留动作、冲突` |

LLM 很难在一个块中记住近 20 类不同约束。其中许多（如角色安全、非专业角色认知）在其他块中已有覆盖。

**建议**：

按职责拆分为独立块：
- `【字数与结构】`：字数范围、段落数、单场字数（#1-3）
- `【场景执行规则】`：场景卡边界、离场状态、转场（#4-6, 13-14）
- `【内容安全规则】`：死亡禁止、角色安全、功能性人物（#7, 17-18）
- `【角色认知规则】`：非专业角色认知、叙述者贴标签（#11-12）
- `【章末规则】`：章末钩子、落地帧（#15-16）
- `【删减策略】`：超限删减优先级、信息量删减（#9, 19）
- `【其他】`：电话/短信、模板化重复（#8, 10）

---

### 问题 P4-3：[严重] 字数控制指令过度密集且来源多头

**文件**：
- `src/bestseller/services/prompt_constructor.py` 行 367-370（invariants_section）
- `src/bestseller/services/drafts.py` 行 8642（system CONSTRAINTS）
- `src/bestseller/services/drafts.py` 行 8959（章节目标段）
- `src/bestseller/services/drafts.py` 行 8849-8891（output_rules）
- `src/bestseller/services/drafts.py` 行 6177（场景级 system）

**问题**：

字数约束在 **至少 4 个地方**出现，措辞略有差异：

| 来源 | 措辞 |
|------|------|
| invariants_section | `【章长度】{min}–{max} 字（目标 {target}）` |
| system CONSTRAINTS | `字数：严格贴近目标字数，不许为了解释设定扩写` |
| 章节目标段 | `目标字数：约{target}字，必须完整成章；发布硬范围 {min}-{max} 字` |
| output_rules | `正文必须连贯，篇幅硬范围是 {min}-{max} 个汉字...字数是硬交付，不是建议` |
| 场景级 system | `CJK 汉字数须在目标的 90%-120% 之间` |

"严格贴近目标" vs "硬范围" vs "目标约" vs "90%-120%"——措辞不一可能导致 LLM 困惑。

**建议**：

统一为单一字数约束块，定义一次硬范围和目标值，其他位置引用而非重复声明。

---

### 问题 P4-4：[中] 黄金三章规则在多处重复且措辞不一

**文件**：
- `src/bestseller/services/prompt_constructor.py` 行 402-412（`build_opening_hook_directive`，用前 100/200/500 字阈值）
- `src/bestseller/services/drafts.py` 行 8745-8768（`opening_retention_rules`，用前 800 + 前 300 字阈值）
- `src/bestseller/services/drafts.py` 行 8648-8652（整章 system 开篇约束，用前 100/300 字阈值）
- `src/bestseller/services/drafts.py` 行 6196-6200（场景级 OUTPUT FORMAT，用前 200/500 字阈值）
- `src/bestseller/services/chapter_llm_quality_judge.py` 行 230-250（冷读者五锚点检查项）

**问题**：

多处对"前 N 字必须做什么"的规则措辞与阈值不一：

- prompt_constructor 说"前 100 字必须聚焦主角 + 1 个可视化异常物"
- drafts.py 整章 system 说"前 100 字必须给读者可感知的压力或异常"
- drafts.py 场景级说"前 200 字必须出现至少 1 个可视化异常物"
- drafts.py 整章黄金三章说"前 800 字内读者必须从动作/对白中自然得知..."

"前 100 字" vs "前 200 字" vs "前 800 字"的递进关系没有被显式统一。

**建议**：

提取为单一函数 `_render_golden_three_rules(chapter_number, language, path_mode)`，所有位置引用同一函数。

---

### 问题 P4-5：[中] 双轨装配系统边界不清，同类段落两套维护

**文件**：`src/bestseller/services/prompt_constructor.py` vs `src/bestseller/services/drafts.py`

**问题**：

存在三套提示词装配系统：

| 路径 | 入口 | 位置 |
|------|------|------|
| 场景级（legacy） | `build_scene_draft_prompts` | drafts.py:5892 |
| 整章一次成稿 | `build_chapter_first_draft_prompts` | drafts.py:8558 |
| 集中式 L3 构造器 | `build_chapter_prompt` | prompt_constructor.py:933 |

`build_chapter_prompt` 产出的 `PromptPlan` 并不直接用于生成；实际注入链是同源抽取函数 `build_chapter_l3_blocks`（prompt_constructor.py:1311）→ `ChapterL3Blocks.as_prompt_block()` → `pipelines.py:5060` 存入 `shared_context.l3_prompt_block` → **仅场景级路径**在 drafts.py:6642-6643 注入；整章路径完全不消费 l3 块。

经核验，当前**未发现实际的段落重复**（anti_slop_footer 只经 l3 块出现一次，drafts.py 未另行调用 `build_anti_slop_footer`）。真实问题是结构性的：
1. 同类约束（字数、开篇规则、去 AI 味）在两套系统里各自维护措辞（见 P4-3、P4-4），未来任何一侧改动都可能引入重复或冲突，且没有防回归守卫
2. 整章路径（当前主路径）享受不到 L3 构造器的跨章稳定段（invariants、voice_dna、seam_contract 由 drafts.py 自渲染）

**建议**：

1. 明确 `PromptPlan`/L3 块与 `drafts.py` 的职责边界：L3 负责跨章节稳定段（invariants、voice_dna、seam_contract），`drafts.py` 负责章节级动态段（场景卡、契约、上下文）
2. 增加"关键块唯一性"防回归测试：装配完成后断言去 AI 味、字数、黄金三章等标记块在最终 prompt 中只出现一次
3. 长期：统一为单一装配入口，并评估整章路径是否接入 l3 稳定段

---

### 问题 P4-6：[中] 读者契约短语净化的生硬替换可能产生病句

**文件**：`src/bestseller/services/prompt_constructor.py` 行 647-668

**问题**：

通过简单字符串替换把"钩子"→"未完成动作"、"卖点"→"可见吸引力"、"承诺"→"可见期待"。如果原始 selling_points 文本中自然地使用了"钩子"一词（如"主角的金手指是一个钩子"），替换后会变成"主角的金手指是一个未完成动作"，语义扭曲。

**建议**：

改为基于上下文的替换（如只在"钩子"作为指令性词汇出现时替换），或改为在 prompt 中加一条"以下术语仅作内部参考，正文不得出现'钩子/卖点/承诺'等策划用语"的指令。

---

### 问题 P4-7：[中] knowledge_boundary_contract 的 explainer 回退逻辑脆弱

**文件**：`src/bestseller/services/chapter_generation_input_builder.py` 行 517-568

**问题**：

`_knowledge_boundary_contract` 先从 story_bible 的 participants 中找 role 为 protagonist/mentor/exorcist/expert 的角色作为 specialist_names；找不到则用 `_fallback_scene_explainers` 取第一个重复出现的场景参与者。如果 story_bible 的角色 role 字段未填充，可能把普通路人误判为"专业解释者"。

**建议**：

1. fallback 逻辑增加角色名 vs story_bible characters 的交叉验证
2. 如果无法确定 specialist，不注入 specialist_rule_terms，改为"所有角色都按非专业角色处理"

---

### 问题 P4-8：[低] _derive_specialist_rule_terms 的启发式过宽

**文件**：`src/bestseller/services/chapter_generation_input_builder.py` 行 463-514

**问题**：

该函数从 story_bible 的 worldview/power_system/glossary 等字段递归收集 2-8 字的字符串作为"专业规则术语"。但 `2 <= len(t) <= 8` 的范围极宽，会把普通名词（如"世界"、"力量"、"等级"）也收入 specialist_rule_terms，导致 prompt 误禁普通角色使用这些常见词。

**建议**：

1. 增加停用词表过滤通用名词
2. 或改为只收集在 glossary 中显式定义的术语

---

### 问题 P4-9：[低] 热词禁用窗口可能过短

**文件**：`src/bestseller/services/prompt_constructor.py` 行 78

**问题**：

`DEFAULT_HOT_VOCAB_WINDOW = 5`（最近 5 章）。对于长篇连载，某些意象/动词的重复周期可能超过 5 章（如每 8-10 章重复一次的"铜钱旋转"），5 章窗口无法捕获。同时 top 20 + min_count 3 的阈值在短章节中可能无法触发任何禁用词。

**建议**：

考虑增加一个长窗口（如 15 章）做补充检测，或根据章节字数动态调整 min_count 阈值。

---

### 问题 P4-10：[低] seam_prompt_composer 渲染过于简薄

**文件**：`src/bestseller/services/seam_prompt_composer.py` 行 9-30

**问题**：

`chapter_seam.py` 的 `extract_open_threads` 能提取 5 类线索（位置/参与者/威胁/身体状态/未答问题）并做精细分类，但 `render_seam_prompt_block` 只输出扁平列表。丰富的接缝分析结果没有被充分利用到 prompt 中。

**建议**：

在 `render_seam_prompt_block` 中增加分类渲染，如：
```
【前章接缝契约】
- 位置：上一章结束在XX场景，本章必须从这里开始或在合理时间内到达
- 参与者：A和B仍在现场，不可凭空消失
- 即时威胁：XX威胁尚未解除
- 身体状态：主角受了XX伤
- 未答问题：XX问题尚未回答
```

---

## 六、阶段五：审校与重写

### 问题 P5-1：[中] 评判 system prompt 中残留题材偏向

**文件**：`src/bestseller/services/chapter_llm_quality_judge.py` 行 201-203

**问题**：

`_render_reference_block` 中有硬编码注释"以下是同类型（悬疑/驱魔）榜单级章节的代表性开篇片段"，尽管代码已改为题材中立（`genre_context`），但这句说明文字仍残留"悬疑/驱魔"字样。同理 `failing_examples` / `passing_examples`（行 242-249）中的例子明显来自某本特定书，不够通用。

**建议**：

1. 将说明文字改为用已有机制动态渲染：该文件已接入 `resolve_judge_genre_context` / `JudgeGenreContext`（行 23-26），只是这句说明文字漏改——改为 `以下是同类型（{genre_context.display_genre}）榜单级章节的代表性开篇片段`
2. `failing_examples` / `passing_examples` 改为从 config 文件按题材加载，或使用更通用的示例

---

### 问题 P5-2：[中] 重写"定点修复"与字数闸门的潜在冲突

**文件**：`src/bestseller/services/reviews.py` 行 2774-2777（定点修复指令，位于 system prompt 的 THINKING 段）vs 行 2864-2918（字数闸门 `_wc_directive`，注入 user prompt；二者同出自 `build_chapter_rewrite_prompts`，每次调用必同时存在）

**问题**：

system prompt 强调"只修复 rewrite_task 中列出的问题，不要过度编辑"，但当字数闸门检测到当前稿超/不足时，`_wc_directive` 要求"必须压缩到硬范围内"或"必须完整重写并补足缺失的冲突"。如果原稿问题不在字数，但字数恰好越界，重写 LLM 被要求同时做定点修复和全局字数调整，两者可能冲突。

**建议**：

在重写 prompt 中增加优先级声明：
```
如果同时存在定点修复任务和字数调整需求：
1. 先执行定点修复
2. 在定点修复的基础上，通过增删描写调整字数
3. 不得因字数调整而引入新的情节或改变原有剧情走向
```

---

### 问题 P5-3：[低] instruction_priority_block 的优先级与 hype 约束可能冲突

**文件**：`src/bestseller/services/prompt_assembly.py` 行 223-231

**问题**：

优先级第 5 条"反应放大：仅爽文/升级流 pack 强调；治愈/文学/慢热题材不强制围观打脸"与 `build_hype_constraints`（prompt_constructor.py:671-789）无条件注入爽点约束可能冲突。当题材不是爽文时，hype_constraints 仍会被注入（只要 `HypeScheme` 非空），但优先级块说"不强制"。LLM 收到矛盾信号。

**建议**：

在非爽文题材下，要么不注入 `hype_constraints_block`，要么将其改为 `hype_suggestions_block`（建议性而非强制性）。

---

## 七、阶段六：组装与导出

### 问题 P6-1：[严重] 项目级导出静默跳过无当前草稿的章节

**文件**：`src/bestseller/services/exports.py` 行 859-860

**问题**：

`_load_project_export_payload` 在加载章节时，若某章无 `is_current=True` 的草稿，直接 `continue` 跳过，**不报错、不在导出结果中标记**。虽然 `preflight_export_check`（行 1245）会检查缺失草稿并发出警告，但该警告仅记入日志（行 1482/1552/1622 的 `logger.warning`），不反映在 `ExportResponse` 返回值中；且只有项目级 DOCX/EPUB/PDF 导出会调用预检，Markdown 与单章导出连预检都不跑。

这意味着导出可能产出一个"缺失若干章节"的文件，而调用方完全无感知。

**建议**：

1. 在 `ExportResponse` 中增加 `skipped_chapters: list[int]` 字段
2. 如果有跳过的章节，在响应中增加 `warnings: list[str]`
3. 考虑增加 `strict` 模式：有跳过章节时直接拒绝导出

---

### 问题 P6-2：[严重] 单章二进制导出未经净化、无章节标题

**文件**：`src/bestseller/services/exports.py` 行 1455/1524/1595

**问题**：

| 操作 | 项目级导出 | 单章 Markdown 导出 | 单章 DOCX/EPUB/PDF 导出 |
|------|----------|------------------|------------------------|
| `sanitize_novel_markdown_content` | ✅ | ✅（行 1380-1382） | ❌ |
| `_ensure_chapter_heading` | ✅ | ✅ | ❌ |
| 传入 `build_*_bytes` 的内容 | 净化后的 `content_md` | 净化后 | **原始 `draft.content_md`** |

单章 DOCX/EPUB/PDF 导出直接使用 `draft.content_md`，未经净化，可能包含 HTML 注释、修订说明、脚手架回声等元数据泄漏。同时未调用 `_ensure_chapter_heading`，若草稿本身不含标题行，导出文件将无章节标题。（单章 Markdown 导出已正确做这两步，仅二进制三格式漏掉。）

**建议**：

在 `export_chapter_docx` / `export_chapter_epub` / `export_chapter_pdf` 中统一调用 `sanitize_novel_markdown_content` 和 `_ensure_chapter_heading`。

---

### 问题 P6-3：[中] DOCX 手工 XML 功能严重受限

**文件**：`src/bestseller/services/exports.py` 行 574-667

**问题**：

未使用 python-docx 库，手工拼接 OOXML XML 字符串：

- `_parse_markdown_line` 仅识别 `# `（h1）、`## `（h2）、`> `（quote）、`- `（li）四种前缀
- **不支持**：`### h3` 及更深层级、`**粗体**`、`*斜体*`、`` `代码` ``、表格、图片、链接
- 粗体/斜体标记会以字面量 `**text**` 出现在 DOCX 中
- 章节间无分页符

**建议**：

1. 短期：至少在 `_parse_markdown_line` 中增加 `**bold**` 和 `*italic*` 的识别
2. 中期：考虑迁移到 `python-docx` 库，获得完整的 OOXML 支持
3. 增加章节间分页符（`<w:br w:type="page"/>`）

---

### 问题 P6-4：[中] EPUB 单文件结构不符合最佳实践

**文件**：`src/bestseller/services/exports.py` 行 670-725

**问题**：

整本书的所有内容放在单个 `OEBPS/content.xhtml`，spine 只有一个 itemref。对于长篇小说（几十万字），这会导致：

- 阅读器无法按章节导航
- 大文件可能影响阅读器性能
- 不符合 EPUB 最佳实践（每个章节应为独立 spine item）

同时，默认 identifier 为 `"bestseller-export"`，同项目多次导出共享相同 identifier，违反 EPUB 唯一性要求。

**建议**：

1. 按章节拆分为独立 XHTML 文件，每章一个 spine item
2. 目录（nav.xhtml）包含每章链接
3. 默认 identifier 改为 `{project.slug}-{timestamp}` 或 UUID

---

### 问题 P6-5：[中] PDF 不渲染 Markdown 标记

**文件**：`src/bestseller/services/exports.py` 行 728-799

**问题**：

- 第 795 行 `Paragraph(escape(text), style)` 对所有文本做 HTML 转义，`**粗体**` 等 Markdown 标记原样输出
- 仅注册 `STSong-Light` 一个 CID 字体，英文项目也使用此中文字体
- 章节间无分页

**建议**：

1. 使用 `markdown` 库先将 Markdown 转为 HTML，再用 reportlab 的 Paragraph 解析 HTML 标签
2. 英文项目注册英文字体（如 Helvetica/Times）
3. 章节间增加 `PageBreak()`

---

### 问题 P6-6：[中] 质量门禁中大量异常被静默吞没

**文件**：`src/bestseller/services/exports.py`

**问题**：

`collect_publication_blockers` 中多处使用 `except Exception: pass` 或 `except Exception: logger.debug(...)`：

| 行号 | 检查项 | 异常处理 |
|------|--------|---------|
| 1110 | 长度稳定性 | `except Exception: pass` |
| 1151 | 常识门禁 | `except Exception: logger.debug(...)` |
| 1180 | 重复检测 | `except Exception: logger.debug(...)` |
| 1223 | 跨章质量检测 | `except Exception: logger.debug(...)` |

配置错误或门禁模块异常会**静默绕过**对应的质量检查，而非阻断导出。

**建议**：

1. 将 `except Exception: pass` 改为 `except Exception: logger.warning(...)`
2. 在导出结果中增加 `gate_health: dict` 字段，记录哪些门禁因异常被跳过
3. 考虑对关键门禁（长度、重复）使用 `except Exception: blockers.append(...)` 将异常本身视为 blocker

---

### 问题 P6-7：[低] accept-best-on-stall 回退可能引入低质量内容

**文件**：`src/bestseller/services/drafts.py` 行 11046-11068

**问题**：

当场景重写循环达到修订上限但未提升任何草稿为 current 时，组装会回退取最新版本草稿并强制提升。虽然记录了 warning 日志，但这意味着章节可能包含一个"重写失败但仍被接受"的场景草稿。

**建议**：

1. 在 ChapterDraftVersion 的 metadata 中标记 `assembled_with_stall_fallback: true`
2. 导出门禁检查该标记，有标记的章节增加额外审查

---

## 八、阶段七：平台发布

### 问题 P7-1：[严重] Cookie 认证易失效，无自动重认证

**文件**：
- `src/bestseller/services/publishing/adapters/qimao.py` 行 28-37
- `src/bestseller/services/publishing/adapters/qidian.py` 行 28-38
- `src/bestseller/services/publishing/adapters/fanqie.py` 行 33-43

**问题**：

三个中文平台适配器均依赖浏览器 Cookie 认证。Cookie 有效期通常为数小时至数天，过期后所有发布静默失败。调度任务按 cron 定时执行，若 Cookie 在两次手动更新之间过期，发布将持续失败且仅记录到 `PublishingHistoryModel`，无告警机制。

**建议**：

1. 增加 Cookie 有效性预检（发布前发送一个轻量 API 请求验证 Cookie）
2. Cookie 失效时发送告警（Webhook / 邮件 / 站内通知）
3. 在 Web Studio 增加 Cookie 状态看板

---

### 问题 P7-2：[中] 发布失败无重试，无熔断

**文件**：`src/bestseller/scheduler/jobs.py` 行 154-161

**问题**：

发布失败时仅递增 `retry_count` 计数器并 `break` 终止批次。无实际重试逻辑、无指数退避、无最大重试熔断。`schedule.current_chapter` 仅在成功时推进，所以下次调度会重试同一章，但若失败原因持续（如 Cookie 过期），将无限重试同一章。

**建议**：

1. 增加指数退避重试（最多 3 次，间隔 5min / 15min / 60min）
2. 增加熔断机制：连续失败 N 次后暂停 schedule，发送告警
3. 区分可重试错误（网络超时）和不可重试错误（Cookie 过期 / 内容被拒）

---

### 问题 P7-3：[中] 发布后不轮询平台审核状态

**文件**：`src/bestseller/scheduler/jobs.py` 行 143-153

**问题**：

`publish_next_chapter` 调用 `adapter.publish_chapter` 后记录结果，但**从不调用 `adapter.check_publish_status`**（定义在 `base.py:49`，所有适配器均已实现）。章节可能被平台 API 接受但随后在审核中被拒，系统无法感知。

**建议**：

1. 发布后 N 分钟调用 `check_publish_status` 轮询审核结果
2. 审核被拒时记录拒绝原因，通知用户
3. 将审核状态纳入 `PublishingHistoryModel` 的状态机

---

### 问题 P7-4：[低] 番茄适配器为 best-effort，依赖逆向内部 API

**文件**：`src/bestseller/services/publishing/adapters/fanqie.py` 行 8-10

**问题**：

番茄无公开 API，适配器使用逆向的内部 Web API，平台改版会导致发布静默失败。

**建议**：

1. 在发布前增加 API 可用性检测
2. 在文档中明确标注此适配器为 best-effort
3. 考虑增加 Selenium/Playwright fallback 方案

---

## 九、跨阶段系统性问题

### 问题 X-1：[严重] 提示词总量过大，注意力稀释风险

**问题**：

整章路径的 user prompt 由约 **25 个分节**拼接而成，加上 system prompt 的 7 段骨架，总提示词可达 **数万字符**。虽然有个 `char_budget` 软裁剪机制，但默认预算很大（`context_budget_tokens * 3`），多数章节不会触发裁剪。

在如此长的 prompt 中，LLM 的注意力会被稀释——研究表明 LLM 对 prompt 中间部分的指令遵循率显著低于开头和结尾。当前 output_rules（近 20 类约束）位于 prompt 尾部偏前位置，而 must_keep_tail_blocks 在最尾部，中间的大量 JSON 上下文块（故事圣经 3200 字、活动主线 3000 字、时间线 2800 字等）可能挤占 LLM 对关键约束的注意力。

**建议**：

1. 将最关键的约束（字数、章末钩子、角色安全）移到 prompt 最尾部（紧邻 must_keep_tail_blocks）
2. 考虑将大型 JSON 上下文块改为自然语言摘要（减少 token、提高可读性）
3. 进行 A/B 测试：减少 30% prompt 长度后，章节质量是否下降

---

### 问题 X-2：[中] 异常处理普遍采用静默降级，质量问题不可观测

**问题**：

全链路中大量 `try/except Exception` 静默降级：

- Conception：fail-open 设计，任何一轮失败都静默跳过
- Planner：反同质化约束模块异常静默降级
- 导出：质量门禁异常 `except Exception: pass`
- 发布：失败仅记日志

这导致质量问题（约束丢失、门禁跳过、轮次降级）在生产环境中不可观测，很难从日志发现。

**建议**：

1. 引入统一的 `DegradationTracker`，在静默降级时记录 `degradation_event`
2. 在关键产物（ConceptionResult、BookSpec、ChapterDraft、ExportArtifact）的 metadata 中增加 `degradation_report` 字段
3. 在 Web Studio 增加"降级健康度"看板

---

### 问题 X-3：[中] 配置驱动但配置校验不足

**问题**：

系统大量依赖 YAML 配置（genre_taxonomy.yaml、prompt_packs/*.yaml、novel_categories/*.yaml、default.yaml），但配置校验不足：

- `genre_taxonomy.yaml` 与 `_GENRE_PRESETS` 双轨未完全收敛
- `prompt_packs` 的 `obligatory_scenes` 从 required 改为 suggested 后，旧配置可能仍有 required
- `novel_categories` 的 `challenge_evolution_pathway` 模板如果缺失，planner 静默降级
- `default.yaml` 的 `words_per_chapter` 等参数如果被误改，影响全链路

**建议**：

1. 增加 config 启动校验（Pydantic 模型 + 交叉引用检查）
2. 增加 config 一致性测试（如 genre_taxonomy 中每个 sub_genre 的 category/pack 都能找到对应文件）
3. 在 Web Studio 增加 config 健康检查页面

---

### 问题 X-4：[低] conception.py 和 planner.py 文件过大，维护困难

**问题**：

- `conception.py`：4400+ 行
- `planner.py`：23000+ 行
- `drafts.py`：12000+ 行
- `reviews.py`：10000+ 行

这些文件已经远超单文件可维护的合理规模。提示词内联在代码中，修改提示词需要在巨大的 Python 文件中搜索和编辑。

**建议**：

1. 将提示词常量提取到独立的 `prompts/` 模块（如 `prompts/conception/`、`prompts/planner/`、`prompts/drafts/`）
2. 考虑将部分提示词外部化为 YAML/JSON 模板，便于非开发者修改
3. 每个规划阶段拆分为独立文件（`planner/book_spec.py`、`planner/world_spec.py` 等）

---

## 十、问题优先级总览

### P0 — 影响产出质量/数据完整性，建议优先处理

| 编号 | 问题 | 阶段 |
|------|------|------|
| P1-1 | 三条创建路径能力严重不对等 | 题材选择 |
| P1-2 | CLI 路径完全绕过题材解析 | 题材选择 |
| P4-2 | output_rules 块职责严重过载 | 正文生成 |
| P6-1 | 导出静默跳过无草稿章节 | 导出 |
| P6-2 | 单章导出未经净化 | 导出 |
| X-1 | 提示词总量过大，注意力稀释 | 跨阶段 |
| P7-1 | Cookie 认证易失效 | 发布 |

### P1 — 影响产出质量/可维护性，建议近期处理

| 编号 | 问题 | 阶段 |
|------|------|------|
| P1-3 | API genre 字段与 resolved 不一致 | 题材选择 |
| P2-1 | Conception 轮次过多 | Conception |
| P2-2 | Schema 与产出不同步风险 | Conception |
| P3-1 | 阶段间摘要可能丢失关键约束 | Planner |
| P3-2 | 中英文提示词不对称 | Planner |
| P3-3 | 反同质化约束静默降级 | Planner |
| P4-1 | AI 套话黑名单多处维护、场景路径重复注入 | 正文生成 |
| P4-3 | 字数控制指令过度密集 | 正文生成 |
| P4-4 | 黄金三章规则重复且措辞不一 | 正文生成 |
| P4-5 | 双轨装配系统指令重叠 | 正文生成 |
| P4-6 | 读者契约短语净化生硬替换 | 正文生成 |
| P4-7 | knowledge_boundary explainer 回退脆弱 | 正文生成 |
| P5-1 | 评判 system prompt 残留题材偏向 | 审校 |
| P5-2 | 重写定点修复与字数闸门冲突 | 审校 |
| P6-3 | DOCX 功能严重受限 | 导出 |
| P6-4 | EPUB 单文件无章节导航 | 导出 |
| P6-5 | PDF 不渲染 Markdown | 导出 |
| P6-6 | 质量门禁异常静默吞没 | 导出 |
| P7-2 | 发布无重试无熔断 | 发布 |
| P7-3 | 不轮询审核状态 | 发布 |
| X-2 | 异常处理普遍静默降级 | 跨阶段 |
| X-3 | 配置驱动但校验不足 | 跨阶段 |

### P2 — 可维护性/体验问题，建议中期处理

| 编号 | 问题 | 阶段 |
|------|------|------|
| P1-4 | genre_taxonomy 双轨未收敛 | 题材选择 |
| P2-3 | 反同质化护栏复杂度接近极限 | Conception |
| P3-4 | compact_outline_mode 题材感知禁用 | Planner |
| P3-5 | cast_spec 紧凑与人格底层张力 | Planner |
| P3-6 | 两个 outline 函数重复维护 | Planner |
| P4-8 | specialist_rule_terms 启发式过宽 | 正文生成 |
| P4-9 | 热词禁用窗口过短 | 正文生成 |
| P4-10 | seam 渲染过于简薄 | 正文生成 |
| P5-3 | 优先级与 hype 约束冲突 | 审校 |
| P6-7 | accept-best-on-stall 引入低质量 | 导出 |
| P7-4 | 番茄适配器 best-effort | 发布 |
| X-4 | 核心文件过大 | 跨阶段 |

---

*文档结束*
