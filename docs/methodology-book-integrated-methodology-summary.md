# 书籍方法论综合抽取与合理性评估

## 结论

7 个书籍来源的蒸馏物料已经可以整合成一套“长篇小说 craft 方法论”，但它不是一套可以直接硬执行的规则库。更合理的定位是：

- 少量 `strict` 方法论进入 contract/gate。
- 大量 `heuristic` 方法论进入 LLM judge 和 review rubric。
- `advisory_only` 方法论只进入 prompt 或 writer guidance，不进入 application contract。

当前自动审计结果：

- 总卡片：771。
- canonical domain：10 个。
- verifiability：strict 137，heuristic 437，advisory_only 197。
- 单次 3-card 注入估算：约 342 tokens。
- 单次 8-card 注入估算：约 911 tokens。
- 1000 章、每章 3-card 注入估算：约 342,000 tokens。

这支持一个保守结论：可以开始集成，但必须选择式集成，不能全量注入。

## 综合后的 10 个方法论域

### 1. premise_outline

核心方法：

- 用一句话、段落、章节/场景列表逐层展开故事。
- 每一层都要保留主角目标、阻碍、风险、结果。
- 大纲不是装饰文本，而是后续场景 contract 的来源。

合理性评估：

- 与当前 planner 的 book/volume/chapter outline 层级高度兼容。
- 可观测性中等到高：目标、阻碍、结果、章节功能可以进入 contract。
- 优先接入 planner，不优先接入 draft。

### 2. scene_causality

核心方法：

- 每场必须有目标、阻碍、行动、代价/结果、下一压力。
- 场景不是信息容器，而是状态改变单元。
- action-reaction 和 scene/sequel 应形成连续推进。

合理性评估：

- ROI 最高，直接命中当前长篇生成最常见的“场景在讲解但不推进”问题。
- 可观测性高，可绑定 `scene_card`、`methodology_contract`、`chapter_predraft_quality_gate`。
- 第一批 core deck 应重点筛选这个 domain。

### 3. character_arc

核心方法：

- 角色行动必须来自欲望、缺陷、价值轴或压力。
- want-vs-need 要在章节/场景中被外化成选择。
- 情绪变化要由行动和后果触发，不能只写抽象心理。

合理性评估：

- 与 `character_strategy`、`character_idiolect_tracker`、scene contract 都有关。
- 可观测性中等：want/need/choice/cost 可以 strict，情绪真实感多为 heuristic。
- 适合第二批接入，不应先于 scene causality。

### 4. setup_payoff

核心方法：

- 钩子、物件、信息差、承诺都需要入账。
- 长篇连载要维护 setup/payoff 队列，避免只埋不还或只还不埋。
- payoff 应改变读者认知或人物处境。

合理性评估：

- 对 1000+ 章项目非常关键，但当前自动分类数偏少，说明需要从 alignment terms 中补强。
- 可观测性高：ledger、hook、payoff、overdue window 都可记录。
- 应接 planner 的 volume plan/hook ledger，而不是只放进 prose prompt。

### 5. pov_prose

核心方法：

- 控制叙述距离，避免 POV 漂移。
- 用具体动作、感官细节、物件变化替代抽象说明。
- prose style 服务于场景功能和角色视角。

合理性评估：

- 对单章可读性提升明显。
- strict 与 heuristic 混合：POV 漂移可检测，show-don't-tell 多数需要 LLM judge。
- 适合接 drafts.py 场景写作 prompt 和 reviews.py prose review。

### 6. dialogue_subtext

核心方法：

- 对白要同时承担冲突、信息、关系和潜台词。
- 标签和解释应服务清晰度，不应替代人物行动。
- 角色说话方式要和身份、欲望、压力一致。

合理性评估：

- 可观测性偏 heuristic。
- 可以与 `character_idiolect_tracker` 联动，但不应作为早期 hard gate。
- 适合 review rubric 和 repair prompt。

### 7. worldview_theme

核心方法：

- 主题不应被直说，而应通过人物选择、世界规则、象征物和后果呈现。
- 世界观规则需要形成可执行压力，而不是背景百科。

合理性评估：

- 与 `story_design_kernel`、world bible、distilled worldview binding 有交集。
- 容易和既有同类作品蒸馏机制混淆，必须区分：书籍方法论提供 craft rule，distilled strategy 提供 genre/design mechanism。
- 初期仅 advisory/heuristic。

### 8. opening_retention

核心方法：

- 开篇优先建立压力、问题、角色处境和读者欲望。
- backstory 和设定应延后到读者已经有问题之后。
- 前几章需要证明主线承诺，而不是只介绍世界。

合理性评估：

- 与现有 Plova/platform opening 强规则高度重叠。
- 优先级必须低于现有 opening hard contract。
- 可作为补充解释和 repair 指导，不作为替代 gate。

### 9. revision_loop

核心方法：

- 修订应按问题类型分层，不应泛泛“润色”。
- 已知失败模式应匹配具体 craft 方法，例如因果失败、POV 漂移、对白解释化、payoff 缺失。

合理性评估：

- repair 阶段 ROI 高，因为失败模式已经明确。
- 应单独接入 `chapter_block_recovery.py` 和 rewrite queue。
- 初期每次只注入 1 到 3 张 card。

### 10. project_health

核心方法：

- 长篇控制需要持续检查节奏、主线、人物债务、hook/payoff 和方法适配。
- 方法论本身也需要被评估：哪些规则真的提升质量，哪些只是增加 prompt。

合理性评估：

- 适合 health/telemetry，不适合 draft prompt。
- 应和 baseline 评测绑定，作为 gate 晋级依据。

## 集成优先级

第一优先级：

- `scene_causality` 接 drafts.py scene contract。
- `premise_outline` 接 planner chapter/volume outline。
- `setup_payoff` 接 planner hook ledger。

第二优先级：

- `pov_prose` 接 drafts.py prose prompt 和 reviews.py prose judge。
- `character_arc` 接 cast strategy 与 scene contract。
- `revision_loop` 接 repair pipeline。

第三优先级：

- `dialogue_subtext` 接 idiolect/review。
- `worldview_theme` 接 story design kernel。
- `opening_retention` 作为现有 opening rules 的补充。
- `project_health` 接 methodology health report。

## 冲突解决规则

运行时优先级固定为：

1. `platform_required`
2. `writing_methodology.yaml`
3. `book_core_deck`
4. `book_advisory`

当书籍 card 与现有强约束冲突时，书籍 card 必须降级、跳过或只作为解释性提示，不得覆盖现有 contract。

## 是否合理

合理，但要分层使用。

771 张卡片中，只有 137 张初步具备 strict 条件；437 张需要 LLM judge；197 张只能 advisory。这意味着“方法论蒸馏”的价值不在于制造更多 hard rule，而在于建立一个可选择、可追踪、可评估的 craft knowledge corpus。

下一步实施应继续保持三个约束：

- 所有接入都要保留 selected card lineage。
- 所有晋级都要依赖 baseline。
- 所有 prompt 注入都要有 token 成本上限。
