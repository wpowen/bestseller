# 7 个写作方法论来源的核心逻辑与框架融合说明

> 本文面向阅读和决策，不保存书名、作者或原文摘录。7 个来源在工程中统一用 `source-0001` 到 `source-0007` 表示。所有结论来自已经完成的蒸馏物料：`data/methodology_books/source-000*/methodology_cards.review.yaml`、`data/methodology_books/analysis/material_inventory.json` 和核心 deck `data/methodology_sources/books_core/cards.yaml`。

## 1. 当前提取出了什么

本轮蒸馏的最终形态不是“读书笔记”，而是一套可运行的写作方法论知识库：

- 原始候选方法论：794 条。
- 通过 review 的方法论卡片：771 张。
- 进入第一版核心 deck 的卡片：115 张。
- 运行 profile：`books_core_v1`。
- 核心运行文件：
  - `data/methodology_sources/books_core/cards.yaml`
  - `config/methodology_profiles/books_core_v1.yaml`

这些卡片被统一归入 10 个方法论域：

| 方法论域 | 卡片数 | 解决的核心问题 |
| --- | ---: | --- |
| `character_arc` | 181 | 人物行动是否来自欲望、缺陷、价值轴、压力和代价 |
| `scene_causality` | 155 | 每场是否真的发生状态变化，而不是只说明信息 |
| `premise_outline` | 132 | 从一句话前提到章节/场景列表的递进式设计 |
| `pov_prose` | 92 | 视角距离、具体行动、感官证据、展示而非解释 |
| `revision_loop` | 55 | 按失败类型修订，而不是泛泛润色 |
| `worldview_theme` | 54 | 主题和世界规则如何通过选择与后果体现 |
| `project_health` | 45 | 长篇的主线、节奏、方法适配和健康度追踪 |
| `dialogue_subtext` | 25 | 对白的冲突、信息、关系、潜台词 |
| `opening_retention` | 19 | 开篇压力、问题、角色处境和读者欲望 |
| `setup_payoff` | 13 | 伏笔、钩子、信息差、偿付账本 |

可观测性分层：

| 类型 | 数量 | 使用策略 |
| --- | ---: | --- |
| `strict` | 137 | 可以进入 contract / gate / warn，后续可晋级 block |
| `heuristic` | 437 | 主要进入 LLM judge、review rubric、repair prompt |
| `advisory_only` | 197 | 只做提示和写作指导，不做硬门禁 |

这个分层很关键：771 张卡不应该全量塞进 prompt。实际运行时每个阶段只选 3 到 8 张，避免 prompt 变长但执行力下降。

## 2. 7 个来源各自的核心内容

### `source-0001`：递进规划 + 场景因果 + 人物驱动

产出 93 张卡，核心集中在 `character_arc`、`scene_causality`、`premise_outline`。

核心理念：

- 故事从小前提逐步展开，不能一开始就堆设定。
- 大纲不是静态摘要，而是后续场景 contract 的来源。
- 每个场景都要有目标、阻碍、结果。
- 人物不是情节工具，人物欲望和内在矛盾要驱动行动。

对框架的价值：

- 适合 planner 的 book/volume/chapter outline。
- 适合把“大纲层意图”落到 scene card 字段。
- strict 占比较高，适合作为第一批核心卡来源之一。

### `source-0002`：场景作为最小故事单元

产出 99 张卡，核心集中在 `scene_causality`、`character_arc`、`pov_prose`。

核心理念：

- 场景不是段落集合，而是一个 mini story。
- 每场必须完成目标、阻力、行动、代价、结果。
- 动作之后要有反应，反应之后要推动下一步行动。
- 视角和描写要服务场景压力，而不是脱离剧情写漂亮句子。

对框架的价值：

- 直接绑定 `PROSE_SCENE` 阶段。
- 可进入 `scene.methodology_contract.goal/obstacle/action/result`。
- 是提升正文“推进感”的最高 ROI 来源之一。

### `source-0003`：叙述距离 + 具体化表达 + 修订意识

产出 68 张卡，核心集中在 `pov_prose`、`character_arc`、`revision_loop`。

核心理念：

- POV 距离要稳定，不能在外部讲解和角色感知之间漂移。
- 抽象心理要被转译为动作、身体反应、物件变化、场景证据。
- 文字风格服务人物视角和场景功能。
- 修订时要定位具体失败模式，而不是笼统“润色”。

对框架的价值：

- 适合 draft prompt 的 prose 约束。
- 适合 reviews.py 的 prose gate 和 POV 检查。
- 适合 repair 阶段处理“解释化”“视角漂移”“AI 味”。

### `source-0004`：内部变化 + 角色选择 + 世界观压力

产出 119 张卡，核心集中在 `character_arc`、`scene_causality`、`premise_outline`、`worldview_theme`。

核心理念：

- 好故事的重点不是事件本身，而是事件如何改变人物。
- 人物的选择必须暴露内部变化、恐惧、误判或价值冲突。
- 世界观规则不能只是百科，它必须给人物制造压力。
- 主题不应直接宣讲，而应通过选择、代价和后果呈现。

对框架的价值：

- 适合绑定 `character_desire/choice/cost`。
- 适合提升“情节推进”和“人物弧线”之间的耦合。
- 可补强当前框架中“事件有了，但人物内在变化不足”的问题。

### `source-0005`：综合写作手册式覆盖

产出 230 张卡，是数量最大的来源，覆盖 `scene_causality`、`premise_outline`、`pov_prose`、`character_arc`、`revision_loop`。

核心理念：

- 写作不是单一技巧，而是规划、场景、人物、文字、修订的连续系统。
- 好的章节要同时满足推进、可读性、情绪、信息和钩子。
- 需要持续修订和自检，但修订必须有明确目标。
- 方法要服务作品，不应僵化套用。

对框架的价值：

- 适合作为广覆盖 advisory/heuristic pool。
- 由于卡片多、重复母题多，不能让它独占 core deck。
- 更适合 selector 运行时按阶段挑选，而不是整体注入。

### `source-0006`：人物平衡 + 情节推进

产出 98 张卡，核心集中在 `character_arc`、`premise_outline`、`scene_causality`。

核心理念：

- 情节和人物必须互相产生，不应一边强、一边弱。
- 角色欲望、阻碍和选择决定情节质量。
- 外部行动要带出内部变化，内部变化要反过来改变下一步行动。
- 对白、关系、情绪都应服务人物与情节的平衡。

对框架的价值：

- 适合绑定 character strategy、cast strategy、scene contract。
- 能补强“情节推进但人物像棋子”的问题。
- 适合进入 review rubric，检查人物行动是否有动机和代价。

### `source-0007`：本土类型写作与读者体验

产出 64 张卡，核心集中在 `character_arc`、`scene_causality`、`premise_outline`、`worldview_theme`。

核心理念：

- 类型小说要重视读者体验、节奏、悬念和情绪满足。
- 人物、冲突、世界观要尽快形成可读的吸引力。
- 章节要有阶段性兑现，不能只铺不还。
- 题材表达要落到具体场景和人物选择上。

对框架的价值：

- 适合和现有平台规则、开篇规则、长篇连载节奏结合。
- 可作为 `opening_retention`、`scene_causality`、`worldview_theme` 的补充来源。
- 更偏应用经验，适合作为 advisory/heuristic，不适合一开始硬门禁。

## 3. 综合后的核心设计理念

7 个来源虽然侧重点不同，但可以压缩成一条共同逻辑：

> 小说质量来自“承诺 → 压力 → 选择 → 代价 → 状态改变 → 新承诺”的连续链条。

对应到框架，就是五层设计：

1. **前提层**：一句话故事、读者承诺、题材卖点、主角核心欲望。
2. **结构层**：全书/卷/章的目标、阻碍、升级、伏笔和偿付队列。
3. **场景层**：每场的目标、阻力、行动、代价、结果、下一压力。
4. **表达层**：视角距离、具体动作、感官证据、对白潜台词。
5. **修订层**：发现失败模式，选对应方法论卡，定点修复。

这套逻辑和当前框架不是并列关系，而是嵌入关系：

- 当前框架负责“生成管线、contract、gate、review、repair、health”。
- 书籍方法论负责给这些节点提供“craft rule”和“应用提示”。
- 同类作品蒸馏负责题材机制和商业类型经验。
- 平台规则负责强约束和签约/留存要求。

优先级固定为：

1. `platform_required`
2. `writing_methodology.yaml`
3. `book_core_deck`
4. `book_advisory`

也就是说，书籍方法论补强框架，不覆盖平台强规则。

## 4. 当前已经怎么融入框架

### 4.1 物料层：771 张卡归一化成 core deck

相关文件：

- `src/bestseller/services/methodology_book_taxonomy.py`
- `src/bestseller/services/methodology_book_corpus.py`
- `scripts/methodology_books/analyze_materials.py`
- `data/methodology_sources/books_core/cards.yaml`
- `config/methodology_profiles/books_core_v1.yaml`

作用：

- 给每张卡标注 canonical domain。
- 判断 `strict / heuristic / advisory_only`。
- 清洗 source id、重复 claim、运行时 card id。
- 从 771 张中选出 115 张更适合运行的 core deck。

质量意义：

- 避免全量注入导致 prompt 污染。
- 避免把不可观测的建议误当 hard gate。
- 后续每次生成都能追踪“用了哪张卡”。

### 4.2 Selector 层：按阶段选少量卡

相关文件：

- `src/bestseller/services/methodology_book_selector.py`

核心逻辑：

- 输入 `stage`、`scope`、`chapter_no`、`chapter_position`。
- 按阶段偏好选择 domain，例如：
  - `outline_chapter` 偏 `scene_causality`、`setup_payoff`、`character_arc`
  - `prose_scene` 偏 `scene_causality`、`pov_prose`、`dialogue_subtext`
  - `repair` 偏 `revision_loop`、`scene_causality`、`pov_prose`
- 控制单 source 和单 domain 数量，避免一种书/一种技巧过度主导。
- 输出 `SelectedBookMethodologyCard`，包含：
  - `card_id`
  - `source_card_id`
  - `canonical_domain`
  - `verifiability`
  - `required_contract_fields`
  - `application_hint`

质量意义：

- 让模型每次只执行少量最相关方法，而不是读一大段泛泛理论。
- 每张卡都有落点，例如 `scene.methodology_contract.goal/obstacle/action/result`。
- prompt 中明确标注“内部执行约束”，避免模型把方法论术语写进正文。

### 4.3 Prompt compiler 层：进入 planner / draft / review 的提示词

相关文件：

- `src/bestseller/services/methodology_compiler.py`

已接入阶段：

| 阶段 | 使用方式 | 质量提升目标 |
| --- | --- | --- |
| `CONCEPTION` | 注入前提、人物、主题类卡 | 让故事立项有清晰读者承诺和核心冲突 |
| `OUTLINE_BOOK` | 注入全书结构、人物弧线、长篇控制卡 | 防止长篇只有设定没有主线 |
| `OUTLINE_VOLUME` | 注入阶段目标、升级、伏笔偿付卡 | 防止卷目标松散 |
| `OUTLINE_CHAPTER` | 注入章级因果和人物选择卡 | 让章纲有可写的动作链 |
| `PROSE_SCENE` | 注入场景因果、POV、对白、人物弧线卡 | 提升正文推进感和可读性 |
| `REVIEW` | 注入审稿相关卡 | 让评审看证据，而不是泛泛打分 |

质量意义：

- planner 先把方法论变成结构。
- draft 再把结构变成正文。
- review 检查是否真的落地。

### 4.4 Application contract 层：记录“这章用了哪些方法”

相关文件：

- `src/bestseller/services/methodology_application_gate.py`

当前做法：

- 在构造章节方法论应用合同时，自动选择 chapter 级和 scene 级书籍方法论卡。
- 将这些卡写入 `applications`。
- 写入 `book_methodology_lineage`。
- 每条 application 都包含：
  - card id
  - profile id
  - node path
  - required fields
  - evidence fields
  - gate
  - measurement

质量意义：

- 不是“prompt 里出现过方法论”就算融合，而是记录到章节 contract 中。
- 后续 review/repair 可以知道应该检查哪张卡、哪个字段、哪个证据。
- planner/draft/review 可以共享同一组 selected cards，避免规划看了一套、正文看另一套、审稿又扣另一套。

### 4.5 Repair 层：失败后精准选方法

相关文件：

- `src/bestseller/services/quality_repair_playbooks.py`
- 调用点：
  - `src/bestseller/services/drafts.py`
  - `src/bestseller/services/reviews.py`

当前做法：

- 当质量 gate 产生失败 code，原有 playbook 仍然优先。
- 在 playbook 后追加 1 到 3 张 repair 阶段书籍方法论卡。
- 失败开放：如果 selector 出错，不阻断原修复流程。

质量意义：

- repair 是方法论最高 ROI 的位置，因为失败模式已经明确。
- 例如正文“解释化”，就选 POV/prose/revision 相关卡。
- 例如场景不推进，就选 scene_causality/revision 相关卡。

## 5. 小说内容与框架的结合点

下面按生成链路说明这些方法论在哪里被使用，以及如何提升质量。

### 5.1 立项 / conception

使用方法论：

- `premise_outline`
- `character_arc`
- `worldview_theme`

使用位置：

- `compile_methodology(stage=CONCEPTION)`
- 后续 story design kernel / project metadata

提升点：

- 把“我想写一个题材”压缩成“主角是谁、想要什么、为什么被阻挡、读者期待什么”。
- 避免立项阶段只有世界设定和金手指，没有主线承诺。

可验证点：

- premise 是否能一句话复述。
- 主角目标、阻碍、风险、读者承诺是否存在。
- story bible 是否能支撑 chapter contract。

### 5.2 全书 / 卷规划

使用方法论：

- `premise_outline`
- `setup_payoff`
- `project_health`
- `character_arc`

使用位置：

- `compile_methodology(stage=OUTLINE_BOOK)`
- `compile_methodology(stage=OUTLINE_VOLUME)`
- planner 的 volume / chapter planning

提升点：

- 让长篇不只是一串事件，而是阶段目标、升级节奏和未偿承诺的队列。
- 让每卷知道“本卷要解决什么、埋什么、偿付什么、主角改变什么”。

可验证点：

- hook ledger 是否存在。
- payoff ledger 是否能关联到前置 setup。
- 卷目标是否拆到章节目标。
- project health 是否能发现长期只铺不还。

### 5.3 章纲 / chapter outline

使用方法论：

- `scene_causality`
- `character_arc`
- `setup_payoff`
- `opening_retention`

使用位置：

- `compile_methodology(stage=OUTLINE_CHAPTER)`
- `build_methodology_application_contract()`

提升点：

- 章纲不再只是“发生了什么”，而是明确：
  - 本章目标
  - 本章阻力
  - 主角行动
  - 代价
  - 结果
  - 下一章压力
- 对前十章尤其重要，因为开篇必须迅速证明主角、冲突、欲望和读者期待。

可验证点：

- `methodology_application_contract.applications` 是否存在。
- `chapter.methodology_contract` 是否有 required fields。
- 前十章必需 Plova/platform 卡是否仍然优先。
- 书籍卡是否进入 `book_methodology_lineage`。

### 5.4 场景正文 / prose scene

使用方法论：

- `scene_causality`
- `pov_prose`
- `dialogue_subtext`
- `character_arc`

使用位置：

- `compile_methodology(stage=PROSE_SCENE)`
- draft prompt 中的场景 contract 附近

提升点：

- 场景从“信息展示”变成“状态改变”。
- 角色的情绪不靠抽象描述，而靠动作、身体反应、选择代价体现。
- POV 更稳定，减少忽远忽近的解释性旁白。
- 对白承担压力、信息和关系，不只是解释剧情。

可验证点：

- 场景是否有目标/阻力/行动/代价/结果。
- POV 是否稳定。
- 正文是否出现方法论标签泄漏。
- dialogue ratio 和对白功能是否在合理区间。
- 章末是否有具体现场钩子。

### 5.5 Review / gate

使用方法论：

- `scene_causality`
- `pov_prose`
- `character_arc`
- `revision_loop`

使用位置：

- `compile_methodology(stage=REVIEW)`
- `evaluate_methodology_application_contract()`
- LLM judge 的 methodology application 检查

提升点：

- review 不再泛泛问“写得好吗”，而是检查“本章选中的方法论有没有证据”。
- 如果卡片要求 `goal/obstacle/result`，review 就看 contract 和正文里是否有相应证据。

可验证点：

- application entry 是否完整。
- required fields 是否缺失。
- 重复 hook、重复 signature image、模板化 emotion 是否被发现。
- relationship debt 是否仍是占位文本。

### 5.6 Repair / rewrite

使用方法论：

- `revision_loop`
- `scene_causality`
- `pov_prose`
- `dialogue_subtext`

使用位置：

- `render_quality_repair_playbooks()`
- drafts/reviews 中已有 rewrite prompt

提升点：

- 失败后不是让模型“重写得更好”，而是告诉它“这次失败属于哪类 craft 问题，该用哪种方法修”。
- 例如：
  - 章太短：扩写行动、阻力、证据、对话交锋，而不是填充说明。
  - 结尾弱：让最后一句落到现场动作、异常物件或新威胁。
  - 重复段落：保留一次，第二次必须升级为新信息或新阻力。

可验证点：

- repair prompt 是否包含 playbook 和书籍方法论卡。
- repair 后对应 issue code 是否消失。
- rewrite improvement 是否超过阈值。
- 是否出现新的 anti-meta leak。

## 6. 方法论如何提升小说质量

### 6.1 提升“推进感”

主要靠 `scene_causality`。

它要求每场有目标、阻力、行动、代价、结果。这样正文会更少出现“人物想了很多、解释了很多、但状态没变”的段落。

对应质量指标：

- scene causality completeness
- main plot progression
- scene exit state change
- chapter ending hook

### 6.2 提升“人物真实感”

主要靠 `character_arc`。

它要求角色行动来自欲望、恐惧、误判、价值冲突和代价。人物不是为了完成大纲而行动，而是被压力逼出选择。

对应质量指标：

- character want/need coverage
- visible choice cost
- emotional movement
- voice consistency

### 6.3 提升“可读性”

主要靠 `pov_prose` 和 `dialogue_subtext`。

它们要求正文用动作、感官证据、物件变化表达信息；对白要有关系压力和潜台词。

对应质量指标：

- POV drift ratio
- anti-meta leak count
- dialogue ratio
- dialogue subtext score
- show-don't-tell / concrete evidence score

### 6.4 提升“长篇留存”

主要靠 `setup_payoff`、`opening_retention`、`project_health`。

它们要求开篇尽快建立压力和读者问题，长篇持续维护伏笔、承诺、偿付和未解压力。

对应质量指标：

- setup/payoff ledger closure
- overdue hook count
- first chapter gate
- chapter seam continuity
- project health report

### 6.5 提升“修稿效率”

主要靠 `revision_loop`。

它把失败分型：因果失败、结尾弱、解释化、重复、POV 漂移、对白悬浮、伏笔未偿付。每种失败匹配不同修复动作。

对应质量指标：

- repair success rate
- rewrite improvement
- repeated violation rate
- repair token cost

## 7. 当前已经验证了什么

### 7.1 工程链路验证

已验证：

- 771 张卡可以加载、归一化、分类。
- core deck 可以生成并由 profile 加载。
- selector 可以按阶段和 scope 选卡。
- prompt compiler 能把 `book_methodology_current` 注入各阶段。
- application contract 能记录 `book_methodology_lineage`。
- repair playbook 能追加 repair 方法论卡。
- 相关单元测试通过：26 个方法论相关测试全部通过。

这证明“功能点接通了”。

### 7.2 模型试点验证

已用 `xiaomi-mimo` profile 跑过短篇试点。

试点目录：

- `output/methodology-book-pilot-20260528T153929Z/`

关键结果：

| 指标 | 数值 |
| --- | ---: |
| 生成模型 | `openai/mimo-v2.5-pro` |
| fallback | 否 |
| 综合质量分 | 0.851 |
| 确定性质量分 | 0.792 |
| LLM 评审分 | 0.900 |
| 中文正文长度 | 3003 |
| anti_meta_leak_count | 0 |
| dialogue_ratio | 0.271 |
| scene_causality_score | 0.714 |
| setup_payoff_score | 0.333 |
| ending_hook_score | 1.000 |

这证明“方法论注入可以被模型消费，并生成可评分正文”。

第一轮试点暴露过一个问题：模型把“目标/阻力/行动/代价/结果”当成正文标签输出。随后 prompt 增加了内部约束和 anti-meta 检测，第二轮 `anti_meta_leak_count=0`。这说明方法论不能裸注入，必须告诉模型“只能内部执行，不能写进正文”。

### 7.3 目前还没有完全证明什么

当前还不能严谨宣称“整体质量已因方法论提升了 X%”。

原因：

- 当前只完成了有效性试点，不是完整 A/B baseline。
- 没有在同一题材、同一 prompt、同一模型下跑“未注入 vs 注入”的对照组。
- `setup_payoff_score=0.333` 暴露出伏笔账本仍需更硬绑定。

所以当前结论应表述为：

> 工程融合已经完成，方法论注入能被模型执行，并在短篇试点中产生可见改善信号；但质量提升的因果证明，需要下一步基线评测集和 A/B 对照。

## 8. 后续如何确认它真的提升了质量

建议用三层验证。

### 8.1 单章 / 短篇 A/B

同一故事设定、同一模型、同一温度，跑两组：

- A：不注入书籍方法论。
- B：注入 `books_core_v1` selected cards。

比较：

- scene causality completeness
- anti-meta leak
- POV stability
- dialogue subtext
- ending hook
- setup/payoff closure
- LLM judge overall

只有 B 稳定优于 A，才算证明短链路有效。

### 8.2 章节批量 baseline

固定 3 个既有 project，每个抽 20 到 30 个 chapter contract。

比较：

- 原 pipeline 质量分。
- 接入书籍方法论后的质量分。
- rewrite 触发率。
- repair 成功率。
- token 成本增量。

这能回答“对长篇生成是否真的有效”。

### 8.3 Gate 晋级规则

书籍方法论卡从 advisory 到 warn/block 必须满足：

1. 字段存在且稳定。
2. 非 LLM 或低误杀检测可用。
3. A/B 或 baseline 证明有提升。
4. token 成本可接受。
5. 不覆盖 platform_required 和现有强约束。

否则保持 advisory 或 heuristic。

## 9. 当前最重要的结论

1. 这 7 个来源的核心不是“技巧清单”，而是一套从前提、结构、场景、人物、表达、修订到项目健康的闭环方法。
2. 和当前 BestSeller 框架最强的结合点是 `scene_causality`、`character_arc`、`pov_prose`、`revision_loop`。
3. 当前最大短板是 `setup_payoff` 的字段化和账本化还不够，需要下一步重点接 hook/payoff ledger。
4. 已经完成的验证证明链路可运行、模型可消费、输出可评分；严格质量提升证明还需要 A/B baseline。
5. 书籍方法论必须选择式注入、记录 lineage、绑定 evidence，不能全量注入，也不能替代平台强约束。
