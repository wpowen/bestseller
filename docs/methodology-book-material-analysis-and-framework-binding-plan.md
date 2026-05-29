# 书籍方法论物料分析与框架绑定计划

## 目标

本计划基于 `data/methodology_books/source-000*/` 下已经完成的书籍方法论蒸馏物料，设计它们进入 BestSeller 框架的方式。当前阶段不直接修改生成链路行为，先完成物料理解、框架映射、风险识别和后续实施计划。

为了保持蒸馏物料的脱敏边界，本文只使用 `source-0001` 到 `source-0007` 这样的内部 source id，不在仓库文档中保存书名、作者或原文摘录。

## 物料现状

已完成 7 个书籍来源的蒸馏，主物料为：

- `data/methodology_books/source-000*/methodology_candidates.review.jsonl`
- `data/methodology_books/source-000*/methodology_cards.review.yaml`

总体规模：

- 候选方法论：794 条。
- 可 review 卡片：771 张。
- LLM 运行错误：0。
- 源章节处理：source-0001 20/20，source-0002 21/21，source-0003 18/18，source-0004 25/25，source-0005 52/52，source-0006 21/21，source-0007 14/14。

各源产出：

| source | candidates | review cards |
| --- | ---: | ---: |
| source-0001 | 93 | 93 |
| source-0002 | 99 | 99 |
| source-0003 | 81 | 68 |
| source-0004 | 120 | 119 |
| source-0005 | 239 | 230 |
| source-0006 | 98 | 98 |
| source-0007 | 64 | 64 |

主要 category 分布：

| category | count |
| --- | ---: |
| character | 141 |
| outline | 108 |
| scene_design | 83 |
| revision | 59 |
| progression | 58 |
| prose_style | 58 |
| longform_control | 46 |
| pov | 37 |
| worldview | 32 |
| mainline | 30 |
| emotion_beat | 30 |
| theme | 27 |

阶段和范围分布：

- stage：planning 527，drafting 436，review 148，repair 93，revision 68，health 27。
- scope：book 417，scene 393，chapter 238，project_health 94，asset 38，volume 27。
- 原始候选中仍有少量非规范 scope/stage/mode，例如 `project`、`opening`、`validation`、`enforce`、`review` 等。转换成 review card 时已有基础归一化，但后续进入运行框架前还需要统一分类表和 gate taxonomy。

高频方法论母题：

- `show-don't-tell`：261。
- `setup/payoff`：199。
- `goal-obstacle-result`：165。
- `action-reaction`：132。
- `want-vs-need`：129。
- `scene/sequel`：107。
- `POV distance`：46。

这些统计说明，新物料最适合先进入四个框架层：大纲设计、场景设计、人物弧线、修订/审稿。它不适合直接作为硬 gate 全量启用。

## 当前框架里的方法论通道

### 1. Profile/deck 卡片通道

`src/bestseller/services/methodology_cards.py` 定义了统一的 `MethodologyCardDeck` 和 card schema。当前 schema 已支持 category、scope、stage、required_contract_fields、framework_bindings、gate_bindings 和 maturity。

`src/bestseller/services/methodology_profile.py` 当前只支持一个 profile 加一个 `card_deck`。`enabled_cards()` 按 stage/scope 过滤 profile 中显式启用的 card，`render_methodology_profile_block()` 再把最多 `max_prompt_cards` 张卡片渲染进 prompt。配置入口是 `quality_gates.yaml` 的 `methodology_framework.profile_id`。

当前限制：

- 只能配置一个主动 profile。
- profile 必须显式列出 card id，不适合直接管理 771 张书籍卡片。
- 选择逻辑只按 stage/scope/priority，不按项目类型、章节位置、当前问题或 category intent 选择。

### 2. Methodology compiler 文本通道

`src/bestseller/services/methodology_compiler.py` 的 `compile_methodology()` 是 stage-aware 的 prompt 组装器。它面向 `CONCEPTION`、`OUTLINE_BOOK`、`OUTLINE_VOLUME`、`OUTLINE_CHAPTER`、`PROSE_SCENE`、`REVIEW` 等阶段，来源包括 prompt pack、`writing_methodology.yaml`、quality levers。

当前限制：

- compiler 不读取 `MethodologyCardDeck`。
- 它是文本片段优先，不记录“本次选中了哪些书籍 card、card 应该落在哪些 contract 字段、后续由哪个 gate 检查”。
- token budget 只在 section 级裁剪，不适合对 771 张细粒度 card 做检索式选择。

### 3. Draft/review prompt 注入点

`src/bestseller/services/drafts.py` 已在章节和场景 contract 渲染时调用 `render_configured_methodology_profile_block(stage="drafting", scope="chapter|scene")`，并在场景写作 prompt 中调用 `compile_methodology(stage=PROSE_SCENE)`。

`src/bestseller/services/reviews.py` 已在章节 review prompt 中调用 `render_configured_methodology_profile_block(stage="review", scope="chapter")`。

当前限制：

- 这些注入点可以消费新书籍 profile，但只会消费当前单一 profile。
- 注入文本没有和 acceptance contract 中的应用记录强绑定，LLM 可能“看见规则但不落地”。

### 4. Methodology application contract 和 pre-draft gate

`src/bestseller/services/methodology_application_gate.py` 构造显式 card-to-node 应用契约，并由 `chapter_generation_input_builder.py` 放进 `acceptance_contract.methodology_application_contract`。

当前优势：

- 它已经表达了正确的目标形态：每张方法论 card 要映射到 node_path、required_contract_fields、evidence_fields、gate、measurement。

当前限制：

- required cards 是硬编码的 Plova/platform card id。
- 还没有通用能力从任意 deck/profile 中生成 application contract。
- 目前更适合少量强约束 card，不适合直接承接大量书籍方法论。

### 5. Project-level distilled strategy 通道

`src/bestseller/services/distilled_strategy_compiler.py` 已把同类作品聚合蒸馏成项目级 `DistilledStrategyCard`，再由 `planner.py` 写入 `project.metadata_json["distilled_strategy_card"]` 和 `distilled_strategy_blocks`。

这个通道的经验可复用到书籍方法论：

- 先从大量 aggregate 中选少量机制。
- 必须转换成项目专属 binding。
- 记录 maturity、failure mode 和 plan consumption checks。
- 不直接复制来源材料。

## 绑定设计

### 总体原则

书籍方法论不应该直接覆盖当前框架，而应该作为“抽象 craft knowledge corpus”进入框架。运行时每次只选中少量卡片，并要求它们落到结构化 contract、prompt 和 review evidence 三处。

推荐新增四层：

1. 物料归一化层：把 771 张 review card 清洗成统一 taxonomy。
2. 书籍方法论 corpus 层：加载、索引、聚类、选择 card。
3. 运行时选择层：按 stage/scope/category/chapter_position/project_context 选 3 到 8 张 card。
4. 应用契约层：把选中的 card 映射到 contract fields、evidence fields、review/gate 观测项。

### Canonical taxonomy

第一版建议固定为 10 个运行域：

| domain | 来源 category | 主要框架落点 |
| --- | --- | --- |
| premise_outline | outline, mainline, theme | conception, book/volume/chapter outline |
| character_arc | character, relationship, emotion_beat | cast, character strategy, scene contract |
| scene_causality | scene_design, progression, action_scene | scene card, event cycle, draft prompt |
| setup_payoff | foreshadowing, longform_control, timeline | hook ledger, payoff ledger, continuity |
| pov_prose | pov, prose_style, surface_subtext | prose scene prompt, prose gates |
| dialogue_subtext | dialogue, surface_subtext, relationship | scene prompt, review rubric |
| worldview_theme | worldview, theme | story design kernel, world bible |
| revision_loop | revision, repair | review, rewrite queue |
| opening_retention | opening, mainline | opening gates, front-ten contract |
| project_health | longform_control, timeline | health audit, chaos index |

### 运行时选择策略

新增 `methodology_book_selector`，输入：

- `stage`：conception、outline_book、outline_volume、outline_chapter、prose_scene、review、repair、health。
- `scope`：book、volume、chapter、scene、project_health。
- `category_intent`：可选，例如 character、scene_causality、pov_prose。
- `chapter_no` 和 `chapter_position`。
- `project_context`：genre、sub_genre、language、reader promise、当前 chapter/scene contract。
- `budget`：默认 3 到 8 张 card 或 600 到 1200 tokens。

选择逻辑：

1. 过滤 stage/scope。
2. 根据 taxonomy 合并同义 category、alignment terms 和 framework_bindings。
3. 优先选择高 confidence、高复用母题、和当前上下文匹配的 card。
4. 同一 source、同一 domain、同一 normalized core_claim 做多样性限制。
5. 输出 `SelectedBookMethodologyCard`，包含 card id、domain、why_selected、required_contract_fields、application_hint、gate_mode。

### 与 planner 绑定

Planner 阶段应该只消费宏观方法论：

- conception：前提、故事问题、读者承诺、人物欲望/缺陷、主题张力。
- outline_book：递进式扩展、主线因果、角色弧线、长篇控制。
- outline_volume：阶段目标、升级模式、setup/payoff 队列。
- outline_chapter：目标-阻碍-结果、scene/sequel、章节钩子、人物选择代价。

具体动作：

- 在 `compile_methodology()` 中新增可选书籍 card section，或者新建 `compile_book_methodology()` 后由 compiler 调用。
- 在 planner 输出 contract 中增加 `book_methodology_applications`，记录本章/本场实际要落地的 card。
- 保持现有 `distilled_strategy_blocks` 不变。书籍方法论负责 craft rule，同类作品蒸馏负责 genre/design mechanism，两者不能混成一个字段。

### 与 draft 绑定

Draft 阶段应该消费场景级和 prose 级方法论：

- scene_design：场景目的、冲突、转折、结果。
- pov/prose_style：叙述距离、展示而非解释、感官细节。
- dialogue/subtext：对白意图、潜台词、信息控制。
- emotion_beat：行动后的情绪变化，而不是抽象情绪说明。

具体动作：

- 在 `drafts.py` 的 contract section 旁边注入“本场选中的书籍方法论”，而不是全局书籍摘要。
- 每条注入必须带 `apply_to`，例如 `scenes[2].methodology_contract.pressure_stack` 或 `scene.prose.pov_distance`。
- 不在 draft prompt 中提供来源书名或长段原文，只给抽象规则和本项目应用方式。

### 与 review/gate 绑定

Review 阶段不能只问“是否符合方法论”，需要检查被选中的 card 是否有证据。

第一版只做 audit/warn：

- review prompt 接收 selected cards 和 expected evidence。
- `methodology_application_gate` 增加通用 evaluator，检查 selected application 是否至少有 node_path、required fields、evidence fields。
- LLM judge 的 `methodology_compliance` 只针对本次 selected cards 打分。
- 输出 violations 写入 rewrite queue，暂不阻断主流程。

后续可提升为 block 的条件：

- card 对应字段稳定存在。
- 非 LLM gate 可以可靠检测。
- 至少一个小样本项目验证不会导致大量误杀。

### 与 health 绑定

Project health 可以增加以下指标：

- `book_methodology_card_coverage`：已启用/已使用/已验证 card 数。
- `domain_coverage`：10 个 domain 的覆盖情况。
- `repeated_methodology_violations`：重复失败的规则。
- `underused_high_value_domains`：例如长期缺失 setup/payoff 或 POV 控制。
- `prompt_budget_pressure`：选卡是否经常被 token budget 截断。

## 主要风险

1. 物料噪声：原始候选里 gate mode、scope、framework binding 存在漂移，必须先清洗再运行。
2. 过度注入：771 张 card 如果不做选择器，会稀释 prompt，反而降低执行力。
3. gate 误杀：写作方法论大多是启发式，初期必须 audit/warn，不应直接 block。
4. 与现有方法论冲突：当前框架已有 Plova/platform 强规则、`writing_methodology.yaml`、quality levers 和 distilled strategy。书籍方法论应补充 craft knowledge，不应覆盖既有商业化强约束。
5. 版权边界：运行时只能使用抽象方法和应用提示，不应回灌书籍原文或长引用。
6. 可观测性不足：如果只注入 prompt，没有记录 selected card 和应用证据，就无法判断是否真的融合成功。

## 修改计划

### Phase 0：baseline 评测集和晋级口径

这是实施前置项，必须早于运行时接入。目标是回答“书籍方法论是否真的让生成质量发生可度量变化”，而不是只证明 prompt 里多了规则。

新增：

- `src/bestseller/services/methodology_book_baseline.py`
- `data/methodology_books/analysis/baseline_metric_spec.yaml`
- `data/methodology_books/analysis/baseline_runs/`

实现：

- 固定 3 个既有 project，每个抽取 20 到 30 个 chapter contract，覆盖至少 3 个题材。
- 记录现有 pipeline 的基线指标：场景因果完整度、setup/payoff 闭环、POV 距离漂移、对白潜台词、角色 want-vs-need 承载、rewrite/repair 触发率。
- 每个后续 phase 跑相同样本，输出 before/after 和成本增量。

验收：

- baseline case、metric spec 和 run summary 都可序列化。
- 没有 baseline 的情况下，任何 book methodology gate 都不得从 advisory/warn 晋级到 block。
- 计划内所有质量结论必须引用 baseline run id。

### Phase 1：物料归一化和审计

新增：

- `src/bestseller/services/methodology_book_corpus.py`
- `src/bestseller/services/methodology_book_taxonomy.py`
- `data/methodology_books/analysis/material_inventory.json`
- `data/methodology_books/analysis/domain_clusters.yaml`

实现：

- 加载所有 `methodology_cards.review.yaml`。
- 同时读取 `methodology_candidates.review.jsonl`，保留 confidence、alignment_terms、operating_steps、conflicts_with 等筛选信号。
- 统一 category、stage、scope、gate mode、framework_bindings。
- 给每张 card 标注 `canonical_domain` 与 `verifiability: strict | heuristic | advisory_only`。
- 把 `strict` 限定为可映射到稳定 contract 字段或非 LLM gate 的 card；`heuristic` 需要 LLM judge；`advisory_only` 只能进 prompt，不进 application contract。
- 生成 source/card/domain 统计。
- 标记低 confidence、非规范字段、重复/近重复 card。
- 建立 core deck 筛选候选池：可观测优先、同质 cluster 留代表、source/domain 覆盖均衡。

验收：

- 单元测试覆盖 loader、taxonomy normalization、duplicate clustering。
- 不输出书名、作者、原文。
- `material_inventory.json` 必须包含 domain、verifiability、source balance、prompt 成本预估。

### Phase 2：书籍方法论核心 deck 和 profile

新增：

- `data/methodology_sources/books_core/cards.yaml`
- `config/methodology_profiles/books_core_v1.yaml`

实现：

- 从 771 张 review card 中抽取第一版核心 card，建议 80 到 120 张。
- 每个 canonical domain 保留 6 到 15 张。
- 每个 domain 至少保留 1 张来自非最大来源的 card，避免单一 source 数量优势造成风格偏移。
- 同质化 cluster 只保留最具操作性的代表 card。
- 默认 `gate_mode: advisory` 或 `warn`。
- 只对高度结构化、可观测的 card 设置 required_contract_fields。
- 引入 `project_profile_overlay`：按 genre/sub_genre 对 card 设置 weight bias、application_hint override 或 disabled。

验收：

- `load_methodology_profile()` 和 `load_profile_deck()` 可加载 `books_core_v1`。
- `validate_methodology_profile()` 无 error。
- 渲染 block 不超过配置的 card 数。
- 每张 core card 必须有 `canonical_domain`、`verifiability` 和冲突优先级说明。

### Phase 3：运行时选择器

新增：

- `src/bestseller/services/methodology_book_selector.py`

实现：

- 支持 stage/scope/domain/context 选择。
- 支持 token/card 数预算。
- 输出 selected cards 和 selection reason。
- 给 planner/draft/review 提供统一 render block。
- 选择器不得分别让 planner/draft/review 各自随意选卡。首次选择发生在 planner 写 chapter outline 时，写入 `chapter_outline.methodology_applications`；draft 和 review 默认沿用同一组 lineage，review 只允许追加标注为 `cross_check` 的检查 card。
- 合并优先级固定为：`platform_required > writing_methodology.yaml > book_core_deck > book_advisory`。
- 输出成本估算：draft/review prompt token 增量、单章 selection 开销、1000 章项目总成本预估。

验收：

- 同一输入稳定选择。
- 不同 stage/scope 选择不同 card。
- budget 生效。
- source/domain 多样性生效。
- 同一 chapter 的 planner/draft/review lineage 一致。
- 与 Plova/platform 强约束冲突时，书籍 card 被降级或跳过。

### Phase 3.5：repair 高 ROI 接入

repair 阶段失败模式明确，最适合使用高密度方法论指导，应单独实施，而不是只作为 stage 枚举存在。

修改：

- `src/bestseller/services/chapter_block_recovery.py`
- rewrite queue / autonomous repair 相关服务。

实现：

- 根据具体失败 code 选择 revision、repair、scene_causality、pov_prose card。
- repair prompt 只注入与当前失败模式匹配的 1 到 3 张 card。
- repair 输出必须记录 `repair_methodology_card_ids` 和修复证据。

验收：

- 对相同失败 code 选择稳定。
- token 增量可控。
- repair 成功率和二次失败率进入 baseline 对比。

### Phase 4：接入 compiler 和 prompt

修改：

- `src/bestseller/services/methodology_compiler.py`
- `src/bestseller/services/drafts.py`
- `src/bestseller/services/reviews.py`
- 必要时扩展 `src/bestseller/services/planner_prompt_helpers.py`

实现：

- compiler 增加可选 book methodology section。
- draft/review prompt 注入 selected cards，而不是全 deck。
- `CompiledMethodology.used_sources` 增加 `methodology_books/books_core_v1` 或 selected card source ids。
- drafts.py 优先接 scene contract 注入；planner.py 优先接 outline_volume 和 hook ledger；prose/review、character idiolect、repair 依次接入。

验收：

- Golden prompt 测试证明 planner/draft/review 都出现精简书籍方法论块。
- 英文路径保持现状或显式空输出，不引入回归。

### Phase 5：应用契约和审稿证据绑定

修改：

- `src/bestseller/services/methodology_application_gate.py`
- `src/bestseller/services/chapter_generation_input_builder.py`
- chapter/outline LLM judge 相关 prompt。

实现：

- 从 selected cards 生成通用 `methodology_application_contract.applications`。
- 对每个 application 记录 `node_path`、`required_contract_fields`、`evidence_fields`、`mode`。
- review 输出把失败 card id、失败字段、修复提示写入 rewrite queue。
- `advisory_only` card 不进入 application contract。
- `heuristic` card 只进入 LLM judge，不进入非 LLM hard gate。
- `strict` card 先 audit/warn，只有 baseline 证明收益后才允许晋级 block。

验收：

- 没有 selected cards 时保持当前行为。
- selected cards 缺失应用证据时产生 warn/audit finding。
- 现有 Plova/platform hardcoded required cards 保持优先级。

### Phase 6：health、telemetry 和 gate 晋级

修改：

- `src/bestseller/services/methodology_health.py`
- 相关 audit/telemetry 输出。

实现：

- 输出 card coverage、domain coverage、重复违反、prompt budget 压力。
- 建立从 audit 到 warn/block 的晋级名单。

验收：

- health report 能显示书籍方法论使用情况。
- 至少一个小项目端到端跑通，不因书籍方法论导致 hard block。

## 推荐实施顺序

1. 先做 Phase 1 和 Phase 2，只生成可审查物料，不影响运行链路。
2. 再做 Phase 3，让选择器可独立测试。
3. 然后接入 compiler/prompt，但默认 audit/advisory。
4. 最后把少量高确定性规则绑定到 application contract 和 health。

这个顺序可以避免两个主要错误：一是把未清洗物料直接塞进提示词，二是在没有证据链的情况下把写作建议升级成阻断 gate。
