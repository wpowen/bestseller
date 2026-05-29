# 当前小说生成流程与方法论插入点

> 目的：梳理 BestSeller 当前从立项到成稿、审稿、修复、知识沉淀的完整流程，并明确方法论在每个环节如何进入。这个文档用于后续判断：哪些书籍方法论已经被消费，哪些还停留在 prompt，哪些需要升级为 contract、gate、ledger 或 repair。

## 1. 当前流程总览

BestSeller 不是单次调用大模型写小说，而是一个分层流水线：

```text
Project
  → Foundation / Novel Plan
  → Story Bible / World / Cast / Volume Plan
  → Chapter Outline / Chapter Contract
  → Scene Cards / Scene Contracts
  → Scene Draft
  → Scene Review
  → Scene Rewrite
  → Chapter Assembly
  → Chapter Review
  → Chapter Rewrite
  → Knowledge Extraction
  → Project Health / Consistency Audit
  → Export / Publishing
```

对应代码主干：

| 层级 | 入口 |
| --- | --- |
| 项目流水线 | `src/bestseller/services/pipelines.py::run_project_pipeline` |
| 渐进式自动写作 | `src/bestseller/services/pipelines.py::run_progressive_autowrite_pipeline` |
| 章节流水线 | `src/bestseller/services/pipelines.py::run_chapter_pipeline` |
| 场景流水线 | `src/bestseller/services/pipelines.py::run_scene_pipeline` |
| 规划 | `src/bestseller/services/planner.py` |
| 正文生成 | `src/bestseller/services/drafts.py` |
| 审稿与重写任务 | `src/bestseller/services/reviews.py` |
| 知识抽取 | `src/bestseller/services/knowledge.py` |

## 2. 方法论来源层

当前框架里同时存在多种方法论来源，不能混成一团：

| 来源 | 作用 | 优先级 |
| --- | --- | ---: |
| 平台强规则 | 七猫/番茄/起点等签约、留存、开篇、字数和禁忌 | 1 |
| `writing_methodology.yaml` | 当前框架已有的主方法论、网文节奏、情绪弹簧、开篇门禁 | 2 |
| prompt pack | 按题材/类型提供的局部写法和 genre-specific craft | 3 |
| distilled strategy | 同类作品蒸馏出的题材机制、商业套路、设计参考 | 4 |
| 七本书 core deck | 抽象 craft knowledge：场景、人物、POV、修订等 | 5 |
| 七本书 advisory pool | 辅助启发，不进入硬门禁 | 6 |

后续融合应固定优先级：

```text
platform_required
> writing_methodology.yaml
> prompt_pack / distilled_strategy
> book_core_deck
> book_advisory
```

书籍方法论的正确角色是补强和结构化，不应覆盖平台规则或已有强约束。

## 3. 流程一：Project 创建与基础约束

### 当前做什么

项目创建后，pipeline 会确保项目级 invariants、identity manifest、emotion kernel、public emotion kernel、entry system 等基础能力存在。

主要职责：

- 确认项目基础元数据。
- 建立身份、入口、情绪和公共爽点相关底座。
- 准备后续 planner 可消费的项目级上下文。

### 当前方法论插入点

| 插入点 | 方法论类型 | 作用 |
| --- | --- | --- |
| project invariants | 平台/框架硬约束 | 保证项目从第一章开始有一致边界 |
| writing profile | 写作画像 | 约束语言、平台、节奏、章节长度 |
| emotion/public emotion kernel | 情绪弹簧、爽点机制 | 给后续章节规划提供情绪压力模型 |
| entry system | 开篇/入口机制 | 约束主角进入故事的方式 |

### 七本书可补强的位置

- 《雪花写作法》：前提、目标读者、类型定位。
- 《布洛克小说写作手册》：类型阅读、市场定位、长期产能。
- 《怎样写故事》：主角内在冲突作为项目级核心。

### 当前不足

项目级方法论现在仍偏“框架已有规则”。七本书的方法论更多进入了后续 selector 和 prompt，还没有形成完整的 project-level capability profile。

## 4. 流程二：Foundation / Novel Plan

### 当前做什么

规划阶段会生成：

- `Premise`
- `BookSpec`
- `WorldSpec`
- `CastSpec`
- `VolumePlan`
- 在非渐进模式下，还会继续生成 chapter outlines。

`generate_foundation_plan()` 会停在基础规划，不直接产出全部章纲；`generate_novel_plan()` 会继续往完整计划推进。

### 当前方法论插入点

| 插入点 | 文件/函数 | 作用 |
| --- | --- | --- |
| `attach_planner_methodology()` | `planner.py` | 给 BookSpec / WorldSpec / CastSpec / VolumePlan 注入阶段方法论 |
| `render_methodology_phase_block(phase="planner")` | `methodology_bridge.py` | 从已有 `writing_methodology.yaml` 和 prompt pack 渲染 planner 方法论 |
| `distilled_strategy_card` | `planner.py` metadata stash | 注入同类作品蒸馏策略 |
| `distilled_design_reference_blocks` | `planner.py` | 注入 architecture / chapter_outline 等设计参考 |

### 应用的方法论

| 方法论域 | 当前用途 |
| --- | --- |
| `premise_outline` | logline、dramatic question、series engine、读者承诺 |
| `character_arc` | 主角、主要角色、欲望、变化线 |
| `worldview_theme` | 世界规则、主题、世界压力 |
| `project_health` | 长篇结构、卷数、章节目标 |

### 七本书对应关系

- 《雪花写作法》：一句话前提、段落扩展、人物和情节交替深化。
- 《怎样写故事》：先内部故事，再外部情节；主角世界观驱动故事意义。
- 《情节与人物》：人物弧线与三幕结构互相嵌合。
- 《布洛克小说写作手册》：题材定位和读者期待。

### 当前不足

这一层已经有方法论注入，但“七本书 card lineage”还不总是写回规划 artifact。也就是说，prompt 可能消费了方法论，但规划结果未必记录“本次落地了哪条方法论、落在哪个字段”。

## 5. 流程三：Volume Plan

### 当前做什么

卷规划负责：

- 本卷目标。
- 阶段性冲突升级。
- 人物弧线推进。
- 伏笔和回报节奏。
- 与前后卷的衔接。

渐进式 pipeline 会在每卷写完后收集反馈，再生成下一卷规划。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| planner methodology | 约束卷目标、升级、payoff 和节奏 |
| distilled design reference | 按题材给卷结构和商业模式参考 |
| prior volume feedback | 让下一卷吸收实际生成质量反馈 |
| project health | 检查长期结构和伏笔偿付 |

### 七本书可补强的位置

- 《雪花写作法》：结构节点和灾难点。
- 《情节与人物》：人物阶段变化与三幕升级。
- 《布洛克小说写作手册》：长项目持续推进。
- 《故事写作》：波浪式冲突、悬念渐进揭示。

### 当前不足

卷级 `setup_payoff` 还没有完全 ledger-first。也就是说，卷计划会写“伏笔和回报节奏”，但还需要更强的账本结构来决定哪些承诺本卷必须偿付、哪些可以延期。

## 6. 流程四：Chapter Outline / Chapter Contract

### 当前做什么

章纲是当前方法论最关键的落点之一。每章需要写：

- `title`
- `goal`
- `main_conflict`
- `hook_description`
- `causal_contract`
- 章节级 `methodology_contract`
- 场景列表和场景级 `methodology_contract`

章节级 `methodology_contract` 当前要求：

```text
conflict_stakes
conflict_buffs
hooks_to_resolve
hooks_to_plant
relationship_debts
pacing_mode
emotion_phase
is_climax
loop_position
```

场景级 `methodology_contract` 当前要求：

```text
conflict_stakes
conflict_buffs
hook_type
spotlight_character
information_control_mode
camera_distance
reveal_mode
signature_image
cut_point
action_sequence
relationship_debts
```

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| `causal_contract` | 把场景/章节因果变成字段，而不是泛泛提示 |
| `methodology_contract` | 承载筹码、压力、hook、节奏、关系债 |
| `event_cycle_contract` | 防止每章同质化，分配章节在事件单元中的角色 |
| `render_hook_ledger_planner_contract()` | feature flag 开启时，要求 planner 把 hook delta 当账本 |
| title contract | 防止标题模板化 |

### 应用的方法论

| 方法论域 | 应用方式 |
| --- | --- |
| `scene_causality` | 章内必须有 desire、choice、resistance、cost、gain、state_change |
| `setup_payoff` | `hooks_to_resolve` / `hooks_to_plant` |
| `character_arc` | `protagonist_choice`、relationship debts、emotion phase |
| `opening_retention` | 前几章 hook、冲突、读者欲望 |
| `worldview_theme` | `world_rule_refs`、`world_rule_landing`、`key_reveals` |

### 七本书对应关系

- 《哈佛短篇小说写作指南》：叙事问题、危机类型、五步场景。
- 《雪花写作法》：场景清单、主动/反应场景。
- 《怎样写故事》：因果链、alpha point、主角内在变化。
- 《情节与人物》：人物选择推动情节。
- 《故事写作》：线索可见、波浪式悬念。

### 当前不足

这是下一轮融合最重要的地方：

1. 当前字段已经存在，但部分方法论还只是 prompt 文字，没有统一 `methodology_applications` lineage。
2. `setup_payoff` 正在从 prompt 升级为 hook ledger，但 payoff ledger 还需要继续推进。
3. planner、draft、review 必须共享同一组 selected methodology，不能各自重新选。

## 7. 流程五：Pre-draft Contract Repair / Validation

### 当前做什么

在场景正文生成前，框架会检查 scene contract，必要时自动补齐或修复：

- 参与角色。
- entry/exit 状态。
- scene methodology contract。
- 身份注册和 offstage name 约束。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| `repair_missing_scene_methodology_contract_pre_draft()` | 正文前补齐缺失场景方法论字段 |
| `validate_scene_contract_pre_draft()` | 检查场景 contract 是否可写 |
| `methodology_contract_mode` | 控制 off/warn/strict |

### 应用的方法论

- 场景必须有压力、筹码、焦点角色、揭示方式、标志画面、断点。
- 动作场景必须有目标、失败代价、对手优势、策略变化、转折点、后效。

### 七本书对应关系

- 《哈佛短篇小说写作指南》：场景作为完整故事单位。
- 《小说写作：叙事技巧指南》：标志细节和具体画面。
- 《情节与人物》：人物行动必须和外部事件连接。

### 当前不足

这个阶段适合把 `advisory` 升级为 `strict` 的少数方法论接进来。但必须谨慎，只有能被字段检查的方法论才适合 strict。

## 8. 流程六：Scene Context Assembly

### 当前做什么

生成正文前，`build_scene_writer_context_from_models()` 组装上下文：

- 当前场景和章节 contract。
- 参与角色 canon facts。
- plot arcs、arc beats、clues、payoffs。
- emotion tracks、antagonist plan。
- 检索到的相关上下文。
- writing methodology 和 story principles。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| Tier 1 context | contract、story principle、methodology、角色事实 |
| RAG retrieval | 给正文提供已发生事实和风格上下文 |
| methodology contract read models | 把 chapter/scene metadata 转成可消费字段 |

### 应用的方法论

这一层不应决定“用什么方法论”，它应忠实携带 planner 已经决定的 contract 和 lineage。

### 当前不足

如果方法论只在 draft prompt 临时选择，而不是从 planner contract 传入，这一层就无法保证 planner/draft/review 一致。后续要强化 lineage。

## 9. 流程七：Scene Draft

### 当前做什么

`generate_scene_draft()` 调用 writer 模型写场景正文。它会注入：

- 写作画像。
- contract section。
- story principle。
- prompt pack。
- methodology rules。
- compiled methodology。
- quality levers。
- opening contract。
- material reference。
- context packet。

### 当前方法论插入点

| 插入点 | 文件/函数 | 作用 |
| --- | --- | --- |
| `render_methodology_block(prompt_pack, phase="scene")` | `drafts.py` | prompt pack 场景方法论 |
| `render_methodology_scene_rules()` | `drafts.py` | 开篇/高潮/节奏相关规则 |
| `compile_methodology(PROSE_SCENE)` | `drafts.py` | stage-aware 方法论编译 |
| `render_configured_methodology_profile_block(stage="drafting")` | `drafts.py` | profile/deck 方法论卡 |
| `quality_levers` | `drafts.py` | 质量杠杆和当前项目目标 |

### 应用的方法论

| 方法论域 | 在正文中的作用 |
| --- | --- |
| `scene_causality` | 保证正文有可见目标、阻力、行动、代价和结果 |
| `pov_prose` | 保持视角距离和具体感官证据 |
| `dialogue_subtext` | 让对白承载压力和信息差 |
| `character_arc` | 让行动体现人物欲望和变化 |
| `opening_retention` | 前几章强化冲突、疑问和小 payoff |

### 七本书对应关系

- 《小说写作：叙事技巧指南》：POV、动作、细节、节奏。
- 《哈佛短篇小说写作指南》：场景 mini story。
- 《情节与人物》：人物选择影响情节。
- 《布洛克小说写作手册》：开篇抓力、类型写法。

### 当前不足

draft 阶段不适合临时解决规划型问题。前面 A/B/C 已证明：`setup_payoff` 如果只在 prose 阶段补卡，容易牺牲场景因果和对白，且不一定修复伏笔闭环。

## 10. 流程八：Scene Review

### 当前做什么

`review_scene_draft()` 对场景打分，核心维度包括：

- hook strength
- conflict clarity
- emotional movement
- payoff density
- voice consistency

扩展维度包括：

- show-don't-tell
- sensory richness
- methodology compliance
- POV consistency
- character voice distinction
- scene/sequel alignment
- duplication score

如果 verdict 为 rewrite，会创建 `RewriteTaskModel`。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| scene review prompt methodology | 让 critic 按方法论判断 |
| deterministic methodology compliance | show-don't-tell + sensory richness |
| `_compute_scene_methodology_reports()` | action scene 等方法论 gate |
| `merge_methodology_reports_into_scene_review()` | 把方法论 gate 结果并入 review |
| `RewriteTaskModel` | 把失败方法论转成修复任务 |

### 应用的方法论

| 方法论域 | review 检查 |
| --- | --- |
| `scene_causality` | 场景是否真的推进 |
| `pov_prose` | 是否视角漂移、解释化、抽象化 |
| `dialogue_subtext` | 对白是否有压力和潜台词 |
| `character_arc` | 行动是否体现动机和变化 |
| `revision_loop` | 失败是否变成具体 rewrite direction |

### 当前不足

review 还需要从“泛 methodology_compliance”升级为“本次 selected methodology 的 evidence 检查”。否则分数高低难以解释。

## 11. 流程九：Scene Rewrite

### 当前做什么

当 scene review 失败时，`rewrite_scene_from_task()` 会用 editor 模型定向重写。rewrite prompt 会带：

- 当前草稿。
- rewrite task。
- scene contract。
- project methodology。
- prompt pack rewrite fragment。
- qimao opening contract。
- material reference。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| `rewrite_task.instructions` | 把失败原因变成可执行改写指令 |
| methodology scene block | 给 editor 提供 craft 约束 |
| review finding categories | 决定是扩写、压缩、修 POV、修对白还是修因果 |

### 应用的方法论

- 《小说写作：叙事技巧指南》的定向修订。
- 《布洛克小说写作手册》的反馈/修订循环。
- 七本书整合出的 `revision_loop`。

### 当前不足

repair 阶段是高 ROI 位置。后续要把低分指标稳定映射到方法论能力包，而不是让 editor 自由发挥。

## 12. 流程十：Chapter Assembly / Chapter Review

### 当前做什么

章节由场景 draft 合并，然后 `review_chapter_draft()` 评估章级质量。核心维度包括：

- main plot progression
- subplot progression
- ending hook effectiveness
- volume mission alignment

同时叠加：

- chapter seam gate。
- stitched draft detection。
- name canon validation。
- opening three function。
- Chekhov emphasis。
- chapter methodology reports。
- hook ledger audit。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| chapter review methodology block | 评估章级情绪压缩、hook lifecycle、冲突筹码、结尾投资 |
| `_compute_chapter_methodology_reports()` | opening、Chekhov 等方法论 gate |
| `compute_hook_ledger_audit_for_review()` | v2 开启时审计 hook ledger |
| `merge_hook_ledger_audit_into_chapter_review()` | 把 ledger 缺口并入 review / rewrite |
| chapter `RewriteTaskModel` | 生成章级修复任务 |

### hook ledger v2 当前规则

feature flag：`BESTSELLER_METHODOLOGY_V2=1`

规则：

- 活跃钩子保持 3-7 个。
- 每章尽量至少消解一个旧钩子。
- 每章至少植入一个新钩子。
- 钩子最长 15 章不能不处理。
- payoff 后下一章必须种下新的压力或问题。
- hook type 归为五类：information_gap、deadline、mystery、desire、threat。

### 七本书对应关系

- 《哈佛短篇小说写作指南》：叙事问题延迟回答。
- 《故事写作》：线索必须可见，悬念逐步释放。
- 《雪花写作法》：结构节点与灾难点。
- 《情节与人物》：高潮选择和人物变化。

### 当前不足

hook ledger 已经进入 planner/review/repair 最小闭环，但 payoff ledger 还没有完全一等公民化。下一步要把 `setup_payoff` 从“关键词/提示词”升级成可持久追踪的承诺账本。

## 13. 流程十一：Chapter Rewrite

### 当前做什么

章级 review 失败时会创建 chapter-level `RewriteTaskModel`。后续 `rewrite_chapter_from_task()` 根据 task 执行修复。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| chapter rewrite prompt | 带入章上下文和方法论 |
| hook ledger repair instructions | 对 hook 缺口给出具体修复方向 |
| recent failed rewrites | 防止重复失败策略 |
| rewrite budget | 防止无限重写 |

### 应用的方法论

- `revision_loop`
- `setup_payoff`
- `scene_causality`
- `pov_prose`
- `dialogue_subtext`

### 当前不足

rewrite task 需要更明确的 capability tag，例如 `repair_domain=setup_payoff`、`repair_domain=pov_prose`。这样后续可以统计哪类方法论长期失败。

## 14. 流程十二：Knowledge Extraction / Continuity

### 当前做什么

每章/每场完成后，框架会抽取和更新：

- Canon facts。
- Timeline events。
- Character state snapshots。
- Chapter state snapshots。
- Retrieval chunks。

这些是后续章节的事实来源。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| canon facts | 防止后文违背已发生事实 |
| timeline events | 支撑因果、时间和 payoff |
| character snapshots | 记录人物状态、关系、知识和变化 |
| retrieval chunks | 给后续 draft 提供上下文 |

### 七本书对应关系

- 《怎样写故事》：人物内部变化必须被记录。
- 《情节与人物》：人物弧线阶段需要可追踪。
- 《故事写作》：线索、社会/历史材料和环境细节要持续一致。

### 当前不足

知识层目前强在事实连续性，但还可以增强“方法论状态”连续性：例如未偿付 hook、人物误信、关系债、主题问题是否进入 snapshot。

## 15. 流程十三：Project Health / Consistency Audit

### 当前做什么

项目每 20 章左右做一致性和健康度检查：

- 人物弧线是否连续。
- canon facts 是否单调。
- clue/payoff 比例是否健康。
- 角色是否知道未来信息。
- 关系演变是否合理。
- 世界规则是否矛盾。
- POV 声音是否漂移。

### 当前方法论插入点

| 插入点 | 作用 |
| --- | --- |
| `methodology_health.py` | 方法论 profile 健康度 |
| consistency audit | 长篇连续性和质量监控 |
| project repair | 触发项目级修复 |
| baseline metrics | 验证方法论是否真实提升质量 |

### 应用的方法论

- 《布洛克小说写作手册》：长期项目健康、持续输出、反馈循环。
- 《雪花写作法》：文档化和早期结构修正。
- 《情节与人物》：人物变化连续性。
- 《故事写作》：线索可见和悬念节奏。

### 当前不足

`data/methodology_unified/inventory_summary.md` 已指出：很多方法论是 `runtime_dormant`。后续 project health 应该统计：

- 哪些方法论域被启用。
- 哪些方法论域有 evidence。
- 哪些方法论域反复失败。
- 哪些 dormant 规则应该升级为 runtime active。

## 16. 当前方法论插入点总表

| 阶段 | 当前插入点 | 应用方法论 | 主要问题 |
| --- | --- | --- | --- |
| Project init | invariants、writing profile、emotion kernel | 平台规则、情绪弹簧、项目底座 | 七本书 project-level profile 还不完整 |
| BookSpec | `attach_planner_methodology`、distilled strategy | 前提、读者承诺、主题、人物核心 | 缺 lineage 写回 |
| WorldSpec | planner methodology、world kernel | 世界规则、主题压力 | 需更好绑定角色选择/代价 |
| CastSpec | planner methodology、character strategy | 欲望、误信、内在冲突、人物弧线 | 需接 character_idiolect / snapshots |
| VolumePlan | planner methodology、prior feedback | 阶段目标、升级、payoff 节奏 | payoff ledger 不够一等公民 |
| ChapterOutline | causal contract、methodology_contract、hook ledger v2 | 场景因果、hook、关系债、节奏 | 需要 selected methodology lineage |
| Pre-draft | scene contract repair/validation | 场景可写性、压力、断点、标志画面 | strict 规则需谨慎晋级 |
| Scene Context | read models、RAG、Tier 1 methodology | 携带 contract 和事实 | 不应重新抽卡，应沿用 planner |
| Scene Draft | prompt pack、compiler、profile block、quality levers | 场景因果、POV、对白、人物行动 | 规划型问题不能靠 prose 临场补 |
| Scene Review | review prompt、methodology reports、deterministic scores | show-don't-tell、POV、场景因果 | methodology_compliance 太泛 |
| Scene Rewrite | RewriteTask、editor prompt | 定向修订 | 需要 repair_domain |
| Chapter Review | methodology reports、hook ledger audit | hook 生命周期、章级推进、结尾钩子 | payoff ledger 待补 |
| Chapter Rewrite | RewriteTask、ledger repair instructions | setup/payoff、结构修复 | 需统计失败类型 |
| Knowledge | canon/timeline/snapshots/retrieval | 连续性、人物变化、线索 | 方法论状态沉淀不足 |
| Health | consistency audit、methodology health | 长篇健康、方法论覆盖 | dormant 规则需要 runtime 化 |

## 17. 当前最明确的问题

1. **方法论过多，但 runtime 消费不足**  
   inventory 显示大量规则仍是 `runtime_dormant`。后续不是继续加卡，而是让高价值规则进入 contract/gate/repair。

2. **prompt 注入多，lineage 和 evidence 不够**  
   现在很多方法论能进入 prompt，但不一定写回“用了哪条、落到哪、由谁检查”。

3. **planner / draft / review 仍可能选到不同方法论**  
   用户指出的“抽卡式不稳定”是对的。后续要让 planner 选择并记录，draft/review 沿用。

4. **setup/payoff 不能只在 prose 阶段补**  
   A/B/C 已证明低分指标驱动 selector 虽然能选到 setup/payoff 卡，但如果插入太晚，不会稳定改善质量。

5. **hook ledger 只完成了最小闭环**  
   当前 v2 可以让 planner 产出 hook delta、review audit 缺口、rewrite task 修复，但还需要扩大样本验证，并继续推进 payoff ledger。

## 18. 后续融合原则

后续分析“哪些不全、哪些有问题、哪些可优化”时，建议按这个顺序做：

1. 先按 10 个方法论域对照现有流程。
2. 判断每个域是否有结构化字段承载。
3. 判断它是否有 review/gate evidence。
4. 判断失败后是否能生成定向 repair。
5. 判断是否有 A/B 指标证明收益。
6. 再决定是补卡、合并、重构、还是升级 gate。

最终目标不是“更多方法论”，而是：

```text
稳定策略层
  → planner 写入 contract
  → draft 按 contract 自然落地
  → review 按 evidence 检查
  → repair 按失败域定向修复
  → health 统计长期效果
```

