# 书籍方法论策略化升级方案

## 背景判断

上一轮 A/B 证明 `books_core_v1` 注入能提升场景因果、POV 稳定性、对白承载和结尾钩子，但也暴露了一个关键问题：`setup_payoff_score` 下降。

这个结果说明，仅按 stage/scope 选择高分卡还不够。即使 selector 是确定性的，也仍然偏“选卡式”；它能让某些维度变强，但不能保证全局质量稳定。

正确方向应是：

> 把 7 个来源蒸馏出来的方法论整合成稳定策略矩阵，再和当前框架已有方法论、质量指标、repair code 绑定。生成时不是随机抽卡，而是按“环节职责 + 当前质量短板”调用固定方法论组合。

## 策略原则

### 1. 方法论不是素材池，而是框架策略层

771 张卡片不能被当作 prompt 素材池随机使用。它们应被归入稳定能力层：

- `premise_outline`：立项、全书、卷、章纲。
- `scene_causality`：章纲、场景正文、review、repair。
- `character_arc`：人物策略、场景选择、情绪变化。
- `pov_prose`：正文表达、POV gate、prose repair。
- `dialogue_subtext`：对白、关系压力、信息控制。
- `setup_payoff`：hook ledger、payoff ledger、长篇连续性。
- `revision_loop`：rewrite/repair。
- `project_health`：长篇健康度。
- `opening_retention`：前几章留存。
- `worldview_theme`：世界规则和主题压力。

### 2. 每个生成环节有固定职责

| 环节 | 固定方法论域 | 作用 |
| --- | --- | --- |
| conception | `premise_outline`、`character_arc`、`worldview_theme` | 定义主承诺、主角欲望、世界压力 |
| outline_book | `premise_outline`、`character_arc`、`setup_payoff`、`project_health` | 全书主线、人物弧线、长篇承诺队列 |
| outline_volume | `setup_payoff`、`scene_causality`、`project_health` | 卷目标、阶段偿付、升级节奏 |
| outline_chapter | `scene_causality`、`setup_payoff`、`character_arc` | 章级动作链、伏笔/偿付、人物选择 |
| prose_scene | `scene_causality`、`pov_prose`、`dialogue_subtext`、`character_arc` | 正文推进、视角、对白、人物代价 |
| review | `scene_causality`、`pov_prose`、`character_arc`、`revision_loop` | 检查落地证据 |
| repair | `revision_loop`、`scene_causality`、`pov_prose`、`dialogue_subtext` | 根据失败模式定向修复 |
| health | `project_health`、`setup_payoff`、`character_arc` | 长篇健康度和策略回调 |

### 3. 低分指标驱动定向补强

质量指标低时，不应该继续按普通 stage bias 选卡，而要触发补强域：

| 低分指标 | 补强方法论域 |
| --- | --- |
| `scene_causality_score` | `scene_causality` |
| `setup_payoff_score` | `setup_payoff` |
| `hook_ledger_closure` | `setup_payoff` |
| `pov_stability_score` | `pov_prose` |
| `pov_distance_drift_ratio` | `pov_prose` |
| `dialogue_ratio` / `dialogue_subtext_score` | `dialogue_subtext` |
| `ending_hook_score` | `setup_payoff`、`opening_retention` |
| `character_want_need_coverage` | `character_arc` |
| `repair_trigger_rate` | `revision_loop` |

这让系统从“我这次选到什么卡”变成“当前质量短板需要哪个 craft 能力”。

### 4. 与现有方法论重构关系

当前框架已有多套方法：

- platform hard rules
- `writing_methodology.yaml`
- Plova structured writing
- prompt pack
- distilled strategy
- quality levers

书籍方法论不应简单追加，而应承担三种角色：

1. **补强**：例如 `scene_causality`、`pov_prose` 直接增强现有 draft/review。
2. **解释**：例如 `character_arc` 为现有人物债、欲望碰撞提供 craft rationale。
3. **重构信号**：如果某个领域已有规则分散在多处，例如 `setup_payoff`，应收敛成 ledger-first 设计，而不是继续分散在 prompt 文本里。

优先级仍固定：

`platform_required > writing_methodology.yaml > book_core_deck > book_advisory`

## 已实施的策略化改动

### 1. selector 从 stage bias 升级为 strategy plan

文件：

- `src/bestseller/services/methodology_book_selector.py`

新增能力：

- `STAGE_DOMAIN_BIAS` 作为稳定阶段策略矩阵。
- `QUALITY_METRIC_DOMAIN_REPAIR` 作为指标到方法论域的映射。
- `project_context.metric_scores` 和 `project_context.quality_deficits` 可传入低分指标。
- 低分指标对应的 domain 会优先于普通 stage domain。

示例：

```python
select_book_methodology_cards(
    BookMethodologySelectionContext(
        stage="prose_scene",
        scope="scene",
        project_context={"metric_scores": {"setup_payoff_score": 0.33}},
    )
)
```

这个调用会把 `setup_payoff` 提到策略域第一位，优先选伏笔/偿付相关卡，而不是继续只选场景/POV。

### 2. pilot 脚本支持传入质量指标

文件：

- `scripts/methodology_books/run_short_story_pilot.py`

新增参数：

```bash
--metric-score setup_payoff_score=0.33
```

这用于复现实验：当上一轮 A/B 发现 `setup_payoff_score` 低，就能让下一轮 B 组走“策略补强”而不是普通方法论注入。

## A/B/C 对比验证

本轮已经按 A/B/C 跑完一次短篇试点：

| 组别 | 说明 | 目的 |
| --- | --- | --- |
| A baseline | 不注入书籍方法论 | 基线 |
| B generic methodology | 普通 `books_core_v1` 注入 | 验证一般方法论效果 |
| C strategy methodology | 注入并传入低分指标，如 `setup_payoff_score=0.33` | 验证策略补强是否修复短板 |

输出目录：

- A: `output/methodology-book-pilot-baseline-20260528T162333Z/`
- B: `output/methodology-book-pilot-methodology-20260528T162559Z/`
- C: `output/methodology-book-pilot-strategy-20260529T010045Z/`

核心指标：

| 指标 | A baseline | B generic | C strategy | 结论 |
| --- | ---: | ---: | ---: | --- |
| `combined_quality_score` | 0.754 | 0.871 | 0.751 | B 有明显收益，C 没有保住整体收益 |
| `scene_causality_score` | 0.857 | 1.000 | 0.714 | C 在因果链上退步 |
| `setup_payoff_score` | 0.500 | 0.333 | 0.333 | C 没有修复 B 暴露的 setup/payoff 短板 |
| `pov_stability_score` | 0.765 | 0.935 | 0.940 | POV 方法论稳定有效 |
| `dialogue_ratio` | 0.277 | 0.417 | 0.119 | C 的对白承载显著不足 |
| `ending_hook_score` | 0.750 | 1.000 | 1.000 | 结尾钩子收益稳定 |
| `anti_meta_leak_count` | 0 | 0 | 0 | 三组都没有方法论术语泄漏 |

C 组的 `selected_cards.md` 证明 selector 逻辑已经按低分指标工作：前两张卡均为 `setup_payoff` 域。

- `books_core.source-0002.sec-0029.realism_detail_layering`
- `books_core.source-0006.sec-0014.ticking_time_bomb_setup`

但 C 组质量没有提升，说明问题不在“有没有选到 setup/payoff 方法论”，而在“方法论注入的位置太晚”。

`setup_payoff` 不是单场正文阶段能补救的表达问题，它是规划层和账本层问题。伏笔、倒计时、阶段偿付必须在 `outline_chapter` 之前进入 chapter contract / hook ledger / payoff ledger；如果等到 `prose_scene` 才把卡片塞进 prompt，模型只能临场加信息，容易挤压对白、场景因果和动作链。

## 修正后的方案

### 1. 方法论整合不是抽卡，而是能力归并

771 张书籍卡片保留 lineage，但运行时不以“抽卡”为核心。框架应维护一套稳定的 `methodology_capability_profile`：

| 能力 | 主要来源 | 框架绑定 |
| --- | --- | --- |
| 场景因果 | 目标-阻力-行动-代价-结果、action/reaction、五步场景 | `scene_contract`、`drafts.py`、`reviews.py` |
| setup/payoff | 伏笔、倒计时、细节可信度、长篇偿付队列 | `planner.py`、`hook_ledger`、`payoff_ledger`、consistency audit |
| POV/文体 | 视角距离、世界观过滤、具体感官证据 | scene prose contract、POV gate、prose repair |
| 对白潜台词 | 关系压力、信息差、欲言又止 | dialogue contract、review dialogue gate、repair |
| 人物弧线 | want/need、选择代价、情绪移动 | character strategy、chapter contract、snapshot |
| 修订闭环 | 失败模式、定点重写、最小改善阈值 | `RewriteTask`、repair queue、editor prompt |

运行时 selector 只做两件事：

1. 根据环节选择固定能力包。
2. 根据低分指标提高某个能力包权重。

它不再决定“这次随机用哪本书的哪张卡”，而是决定“当前流程必须启用哪种 craft 能力，以及使用哪些代表性证据卡做 lineage”。

### 2. setup/payoff 必须上移到 planner

本次 C 组失败给出的具体改造动作：

| 改造点 | 文件 | 做法 |
| --- | --- | --- |
| chapter contract 增加 methodology ledger | `src/bestseller/services/planner.py` | 章纲阶段输出 `methodology_applications`、`hook_ledger_delta`、`payoff_ledger_delta` |
| drafts 只消费 planner 已选能力 | `src/bestseller/services/drafts.py` | 不重新选一套卡；沿用 chapter outline 的 selected methodology lineage |
| review 按 ledger 检查 | `src/bestseller/services/reviews.py` | 检查本章是否种下/偿付/推进 ledger，不只看正文关键词 |
| repair 对低分项定向修复 | `src/bestseller/services/chapter_block_recovery.py` | `setup_payoff_score` 低时，生成 ledger repair task，而不是泛化改写 |

### 2.1 Step 4b 已推进：hook ledger 进入 planner/review/repair 闭环

Claude 完成的 Step 4a 已证明项目里不该新建并行账本，而应在现有 `ClueModel` / `methodology_contract` / rewrite queue 之上做薄接入。本轮继续完成了 Step 4b 的最小闭环：

| 接入点 | 当前状态 | 说明 |
| --- | --- | --- |
| `src/bestseller/services/hook_ledger_runtime.py` | 已新增 | 运行时 adapter：从 `ClueModel` + 当前 `ChapterContractRead.hooks_to_plant/hooks_to_resolve` 生成审计，并把审计结果转成 review evidence / rewrite instructions |
| `src/bestseller/services/planner.py` | 已接入 | `BESTSELLER_METHODOLOGY_V2=1` 时，章纲 prompt 增加稳定 hook ledger 合同，要求 planner 把 `hooks_to_resolve` / `hooks_to_plant` 当作 ledger delta |
| `src/bestseller/services/reviews.py` | 已接入 | `review_chapter_draft()` 在 v2 flag 打开时自动运行 hook ledger audit，并把 findings 合并进 `ChapterReviewResult` |
| repair 路径 | 已打通最小闭环 | 不新造 repair 系统；当 hook ledger 缺口需要改写时，复用既有 `chapter_review` → `RewriteTaskModel` 机制，把定向修复说明写入 rewrite task |
| feature flag | 默认关闭 | 仍由 `BESTSELLER_METHODOLOGY_V2=1` 启用，避免影响正在飞的旧流程 |

这一步没有改 `ClueModel` schema，也没有创建新的持久化账本。当前实现用现有数据库 clues 做长期账本，用当前章节 contract 里的 `hooks_to_plant/hooks_to_resolve` 做本章 delta，因此可以先验证策略闭环，再决定是否把 delta 更深地物化到 `ClueModel`。

验证：

- `tests/unit/test_hook_ledger.py` + `tests/unit/test_hook_ledger_runtime.py`：54 passed。
- 加上 `tests/unit/test_methodology_book_selector.py` 的相关组合：57 passed。
- `hook_ledger_runtime.py` 与对应测试 ruff 通过。
- `planner.py` / `reviews.py` / `hook_ledger_runtime.py` 语法编译通过。
- `bestseller.services.reviews`、`bestseller.services.planner`、`bestseller.services.hook_ledger_runtime` import 通过，无循环依赖。

优先级应固定为：

`platform_required > writing_methodology.yaml > methodology_capability_profile > book_lineage_cards > advisory`

### 3. 指标闭环要成为策略入口

低分项不能只影响下一次 prompt 文案，而要影响下一轮 planning/review/repair：

| 低分项 | 下一步动作 |
| --- | --- |
| `setup_payoff_score < 0.70` | 下一章 planner 必须补 `hook_ledger_delta` 或 `payoff_ledger_delta`；当前章 repair 必须明确一个可见偿付 |
| `dialogue_ratio < 0.25` | draft/rewrite 增加冲突对白段，禁止闲聊式对白 |
| `scene_causality_score < 0.70` | repair 重建目标-阻力-行动-代价-结果链 |
| `pov_stability_score < 0.70` | prose repair 限定单一视角距离和感官证据 |
| `ending_hook_score < 0.75` | chapter ending repair 增加新变量/未答问题/倒计时 |

## 当前结论

用户提出的问题是正确的：方法论系统不应该是抽卡式注入，而应该是稳定策略层。本轮 A/B/C 进一步证明：

1. 普通书籍方法论注入对 POV、结尾 hook、场景局部质量有收益。
2. 指标驱动 selector 能正确把低分项映射到方法论域。
3. 但仅在 prose 阶段补卡无法修复 setup/payoff 这类规划型问题。
4. 下一步必须把书籍方法论重构成框架能力层，并把 `setup_payoff` 前移到 planner/hook ledger/payoff ledger，再由 review/repair 闭环验证。

因此，后续实施重点不是继续扩大卡片数量，而是重构绑定位置：planner 负责承诺和账本，draft 负责自然落地，review 负责证据检查，repair 负责定向补洞。
