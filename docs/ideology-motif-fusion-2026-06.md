# 核心理念（母题）层融入框架 — 架构与试点 (2026-06)

> 把《从天地不仁到逆命而行的小说方法论》的 13 母题 / 4 层体系，作为一个独立的
> **核心理念内核（IdeologyKernel）** 融入 BestSeller 框架，让小说从"按题材拼大纲"
> 升级为"由一句世界观命题（思想脊柱）生长出世界观、卷纲、章纲、代价与价值反转"。

## 1. 问题：框架缺"灵魂"

现状（融入前）：规划链路从 **题材 → prompt_pack → 大纲** 驱动。`StoryDesignKernel`
（贯穿全链路的规划契约）里：

- 有 `four_causes_contract`（亚里士多德式 目的/质料/形式/动力），但那是结构论，不是
  文学母题；
- `BookSpec` 只有一句兜底的 `theme_statement`（`planner.py::_fallback_theme_statement`）
  和零散的 `themes` 列表；
- 代码注释（`planner.py:6392`）自陈："theme homogenisation across same-genre books"
  —— 同题材的书主题趋同。

也就是说：**缺一个像「天地不仁，以万物为刍狗」那样，能决定整本书走向、并能据此生成
世界观与体系的核心理念**。这正是《凡人修仙传》《诛仙》《道诡异仙》等头部作品有、而
本框架没有的"思想发动机"。

## 2. 理论编码：13 母题 × 4 层（`config/motif_library.yaml`）

把报告的体系沉淀为单一事实源（genre-neutral，母题是世界观命题不是题材）：

| 层（构思顺序） | 母题 | 写作功能 |
|---|---|---|
| 宇宙秩序层 | 天地不仁、大道无情、众生皆苦、因果 | 世界是否偏爱善者 |
| 主体抉择层 | 命运、逆天、人定胜天、代价 | 人如何回应世界 |
| 认知危机层 | 真相、真实与虚幻、神佛皆伪 | 靠什么推进悬念 |
| 伦理反转层 | 长生是诅咒、善恶颠倒 | 最终颠覆什么价值 |

每个母题携带：定义 / 哲学渊源 / 代表作 / 冲突类型 / 角色弧 / 世界观设定 / 情节模板 /
悬念技巧 / **主题陈述模板** / **核心问题模板** / **信念弧（信→碎→立）** / 常见陷阱 +
规避 / **可视化符号示例** / 商业评级 / 组合亲和度。另含 8 条 battle-tested **组合配方**
和"组合公式 = 一强主母题 + 两副母题(管行动/管悬念) + 一隐藏终局母题"。

## 3. 架构融合（全部复用现有模式）

```
            premise + genre + BookSpec
                      │
        derive_ideology_kernel()  ← LLM(planner)，fallback-safe
                      │  (services/ideology_kernel.py)
                      ▼
        ┌──────────── IdeologyKernel ───────────┐   (domain/ideology.py, frozen pydantic)
        │ cosmic_premise / thesis / core_question│
        │ primary + [action, suspense] + hidden  │
        │ belief_arc(信→碎→立) / cost_system     │
        │ motif_to_world_bindings / per_volume   │
        └───────────────────┬────────────────────┘
                            │  作为字段挂入（extra="ignore"，向后兼容）
                            ▼
            StoryDesignKernel.ideology_kernel  ← 经 render_story_design_kernel_prompt_block
                            │                     传播到 worldview / plot_tree / beat_schedule /
                            │                     卷纲 / 章纲 / emotion kernel 等所有下游 prompt
        ┌───────────────────┼───────────────────────────┐
        ▼                   ▼                           ▼
 coherence_gate       motif_scaling              ideology_judge（advisory）
 （确定性结构门禁）   （把具体符号按卷排布）      （大纲是否真把理念戏剧化）
  → GateVerdict        plant→echo→transform        → IdeologyJudgeResult
   advisory(required=False) →resolve               → revision_priority → 回灌重规划
```

### 关键设计点（与框架既有惯例一致）

1. **挂入点 = `StoryDesignKernel`**（非新建 sibling kernel）。它是贯穿全链路的规划契约，
   `extra="ignore"` 使加字段向后兼容，且经 `render_story_design_kernel_prompt_block`
   自动传播到每个下游 prompt —— 一处挂入，全链路可见。
2. **全 advisory，不硬阻断**。`coherence_gate` 默认 `required=False`（产出 `warn_only`），
   `ideology_judge` 无 `passed` 字段。遵循 2026-05/06 质量回归教训：主题类杠杆只
   "报告 + 修复"，绝不 hard-abort 整本书（见 [[methodology-pipeline-quality-regression]]）。
3. **题材中立**。判官复用 `judge_genre_context.resolve_judge_genre_context`，按本书自身
   题材判断，不把某一题材的母题硬塞给其它题材（见 [[judge-genre-neutralization]]）。
4. **确定性兜底永不缺位**。`fallback_ideology_kernel` 用组合引擎构造一个 4 层完整、
   schema 合法的内核，即使 LLM 不可用也不会让书"无灵魂"地规划。
5. **判官有确定性地板**。`audit_ideology_outline_grounding`（符号落地率 / 主题关键词回响 /
   信念崩点是否排布 / 禁用解法字面命中 / 代价语言是否在场）作为 LLM 判官的 penalty 先验，
   模型无法低报确定性已证实的症状（镜像 `litstyle_prose.detect_ai_tone`）。

### 闭环（裁判门禁）

- **结构闭环**：`evaluate_ideology_kernel_coherence` 检 9 项（四层覆盖 / 副母题双角色 /
  隐藏母题有揭示槽 / 代价系统 ≥2 / 反口号守卫 / 母题→世界观桥 / 每卷加压 / 无母题复用 /
  主题非占位）。
- **语义闭环**：`judge_outline_ideology` 按 9 维（主题清晰度 / 核心问题戏剧化 / 信念弧完整 /
  代价绑定 / 四层深度 / 宇宙前提一致 / 反口号化 / 隐藏母题铺垫 / 商业兼容）打 0-100，
  产出 `revision_priority` → `build_ideology_repair_directives` → 回灌重规划。

## 3.5 主题与题材解耦 + 大主题语料库（2026-06 修订 · 硬要求）

> 反馈：主题**不能绑死题材**。"所有仙侠都写天地不仁、都市/历史都写大道无情"会导致同题材
> 同质化、缺创意、乏味——**禁止**。同时主题应是一个很大的范围集（1000-2000 量级），由一个
> **主主题**贯穿、若干**子题**穿插。

落地（已实现并经验证）：

1. **删除 `genre_affinity`（题材→母题硬映射）**。`config/motif_library.yaml` 不再有任何
   genre→theme 绑定；`combinations[*].fits_genres` 仅描述用、不参与选择。
2. **新增大主题语料库** `config/theme_corpus.yaml`：**与题材完全解耦**（无 genre 字段）的
   主题命题池，每条只标 `motif`(组织用) + `tone`(按"前提氛围"软匹配)。当前 seed=104 条，
   loader 不限条数，可由 `scripts/expand_theme_corpus.py` 扩到 1000-2000 并去重。
3. **主主题 + 子题模型**：`IdeologyKernel.thesis_statement` = 唯一主主题；新增
   `sub_themes: list[SubTheme]` = 穿插子题（各自 `{proposition, motif_key, layer}`）。
4. **选择由 premise/书目身份的确定性 seed 驱动, 绝不由 genre**：
   `ideology_library.suggest_motif_formula(seed=...)` / `select_themes(seed=...)`，seed =
   `book_diversity_seed(premise, title)`（**不含 genre**）。`genre` 仅作 LLM prompt 的弱上下文,
   且附**"严禁套用题材默认主题"**指令。同一本书 seed 稳定→可复现；不同前提→不同主题。
5. **多样性证明**（确定性, 无需 LLM）：`scripts/verify_ideology_diversity.py` —— 10 个不同的
   **仙侠**前提：

   | 指标 | 结果 |
   |---|---|
   | unique 主主题 | **10/10** |
   | unique 母题脊柱组合 | **10/10** |
   | unique 宇宙母题 | **4/4**（天地不仁/大道无情/众生皆苦/因果, 不再锁死天地不仁）|
   | genre 是否影响选择 | **否**（同前提换题材→同主题）|

   即"仙侠就一定天地不仁"被彻底打破；主题由前提决定、且高度多样。LLM 推导路径在此基础上
   再叠加前提专属创作与反套路指令, 多样性更高。

## 3.6 主题必须主流接地（2026-06 修订 · 硬要求）`config/mainstream_themes.yaml`

> 反馈：主题必须符合**主流认知/世界观**——取自各类小说基本都用的、读者耳熟能详的已知主题；
> 我们在这个框架上做**分化/设计/具体化**，而**不是标新立异硬造**。否则主题站不住脚、显得扭曲、
> 不符合实际，读者会觉得"理念不对"而弃读。

落地（已实现并验证）：

1. **新增主流主题库** `config/mainstream_themes.yaml`：**22 个公认主题域(subject) × 88 条接地论断**，
   取材自网络检索的文学界共识普世主题 + 中文网文主流母题/价值观（见下"取材来源"）。一条主题 =
   **主题域(subject) + 论断(insight)**（如 爱与牺牲→"真爱需要牺牲"、权力与腐化→"绝对的权力绝对地腐化"、
   身份→"我是谁由选择而非出身定义"），是被演出来的而非喊出来的。每个 subject 映射到 13 母题之一(深层结构)。
2. **`ThemeEntry.subject` + `grounded`**：来自主流库的主题带 subject 标签(`grounded=True`)；
   `theme_corpus.yaml` 的零散警句不带。loader 把**接地主题排在前**，去重合并(189 条池, 88 接地)。
3. **`select_themes` 主主题只从接地池选**(`grounded_only=True`)——headline 主主题必为公认主题；
   子题可用更宽的池做质感。
4. **prompt 强约束**：`render_motif_library_prompt_block` 先列"主流主题库"菜单 + "主流主题样本"，
   并硬性指令"主主题必须取自公认主题、据前提具体化、**严禁标新立异硬造扭曲理念**"；
   `build_ideology_system_prompt` 同步。
5. **验证**(`scripts/verify_ideology_diversity.py`)：6 个仙侠前提的主主题全部是**公认主题**
   (复仇/生死/希望/善恶/生存…)且**仍随前提分化**；单测 `test_primary_theme_is_grounded_mainstream`
   断言每本书主主题 `grounded=True`。

**主流主题域（22 个，公认选题）**：爱与牺牲、家与归属、友情与忠诚、成长、身份与自我、失去纯真、
命运与选择、自强不息、信仰、希望与绝望、善恶、正义、权力与腐化、反抗、逆袭、守护(亲我主义)、复仇、
生死、真相与欺骗、生存、救赎与宽恕、个人与世界。

**取材来源（网络检索 2026-06 + 母题报告）**：
- 西方文学共识普世主题：[Reedsy 12 common themes](https://reedsy.com/blog/guide/theme/common-themes-in-literature/)、
  [ProWritingAid 200 themes](https://prowritingaid.com/themes-in-literature)、
  [Scribophile 25 themes](https://www.scribophile.com/academy/common-themes-in-literature)、
  [HireAWriter 25 universal themes](https://www.hireawriter.us/storytelling/25-universal-themes-that-create-unforgettable-literature)。
- 中文网文主流母题/价值观（屌丝逆袭/成长骨架/守护·亲我主义/复仇/寻找/自强不息）：
  [中国作家网·网络文学审美](https://www.chinawriter.com.cn/n1/2022/0519/c404027-32425232.html)、
  [知乎·网络小说写作攻略](https://zhuanlan.zhihu.com/p/478448510)。

## 3.7 报告能力补充融合（2026-06 · 把方法论的剩余可写能力全部吸收）

> 复核报告后, 把此前只抽了"13 母题 + 组合公式 + 信念弧 + 代价系统"之外、尚未融入的
> **具体可写能力**全部补齐, 让选中的母题自带"成熟、符合主流认知的执行脚手架", 而非凭空硬造。

| 补充能力（报告出处） | 落地 |
|---|---|
| **每母题写作脚手架**（母题模板表：开篇钩子/三幕/人物范式/关键场景/可延展副线）| `motif_library.yaml::motif_templates`(13 母题全覆盖) → `Motif` 新增 5 字段 → 推导菜单"写作脚手架"段 |
| **10 个母题配方范例**（刍狗城/生死簿外包员/假佛国…，报告自带成熟样例）| `config/ideology_exemplars.yaml` → `load_ideology_exemplars` → 推导 prompt 按 seed 抽 2 条做 **few-shot**（"照着分化, 不要凭空硬造"）|
| **世界观从代价表往回建**（能量→代价→收税者→例外→例外代价 五问）| `ideology_exemplars.yaml::principles.worldview_from_cost` → 注入推导 prompt |
| **节奏双轨**（每章小勾子/每卷大翻面/每季价值重估）| `principles.pacing_dual_track` → 注入 |
| **可转译 IP 资产预埋**（门派标识/法器视觉/强记忆场景/反派口号/地图节点/支线角色/单元故事）| `principles.ip_preembed` → 注入 |
| **收束铁律**（先写世界为何不帮你, 再写你为何还选择…）| `principles.closing_rule` → 注入 |

效果：`build_ideology_user_prompt` 现含 主流主题库 + 母题配方范例(few-shot) + 写作脚手架 +
创作原则(代价表/节奏/IP/铁律) + 反标新立异指令（~7200 字）。单测
`test_every_motif_has_writing_scaffolding` / `test_exemplars_and_principles_load_and_resolve` /
`test_derivation_menu_includes_scaffolding_exemplars_principles`。

## 4. 试点对比（A/B）：`scripts/verify_ideology_outline_ab.py`

同一前提 + 题材，两臂：

- **BASELINE**：题材-only 大纲（框架现状）。
- **TREATMENT**：先 `derive_ideology_kernel` → 过结构门禁 → 把理念 block 注入大纲生成
  prompt，让世界观/卷纲/黄金三章都由主题生长。

两份大纲都被**盲评**（judge 端 kernel=None，对称）：DeepSeek 独立判官按 9 维理念深度打分；
再做位置互换的 pairwise 盲评（抵消位置偏置）。

### 试点结果（MiniMax-M2.7 规划 / DeepSeek 独立判官，4 题材）

| 题材 | 自动推导的主母题（+副+隐藏） | baseline | treatment | Δ | pairwise（互换两次） |
|---|---|---|---|---|---|
| 仙侠 | 天地不仁 + 代价/真相 + 善恶颠倒 | 82 | 88 | +6 | treatment 2 : 0 |
| 悬疑 | 因果 + 命运/真相 + 善恶颠倒 | 80 | 89 | +9 | treatment 2 : 0 |
| 都市异能 | 大道无情 + 命运/真相 + 长生是诅咒 | 76 | 87 | +11 | treatment 1 : 1 |
| 历史 | 大道无情 + 人定胜天/善恶颠倒 + 众生皆苦 | 64 | 87 | **+23** | treatment 2 : 0 |
| **均值** | （每题材 4/4 层覆盖，门禁全 pass） | **75.5** | **87.8** | **+12.2** | **treatment 7 : 1**（n=8）|

要点：

- 四个题材各自推导出**不同且题材贴切**的母题配方（主母题 天地不仁/因果/大道无情），
  全部 4/4 层覆盖，结构门禁全部 `pass(1.00)` —— 体系可按题材自适应。
- **历史**题材-only baseline 最低（64，建设流大纲最缺思想脊柱），融入后提升最大（+23）。
- 唯一一次 baseline pairwise 胜（都市异能某一位置）说明判官在真实区分而非橡皮图章。

> 结论：理念内核驱动的大纲在"思想深度"维度对题材-only 大纲有稳定、可复现的质的提升
> （均值 +12.2，pairwise 7:1），位置互换 + 独立判官排除了位置偏置与同模型自夸偏置。

## 4.6 真实框架端到端试点（引用验证 + 修复）`scripts/pilot_ideology_framework.py`

> 不是 standalone 重写，而是**真实调用框架**：连真实 Postgres(localhost:5432)，
> 经框架 `complete_text` 真实网关推导内核(含 `llm_runs` 审计)，再调**真实** planner
> prompt builders 验证下游引用。

**Part A — 真实网关**：`derive_ideology_kernel`→`complete_text`(断路器/重试/审计)实跑，
推导出前提专属内核(如「真相不是奖品，是分期账单——你每看清一层，就欠下一笔无法转嫁的命」)。

**Part B — 真实下游引用**：用框架自身 `_fallback_story_design_kernel` 造 StoryDesignKernel +
挂 ideology，调真实 `_volume_plan_prompts`(卷纲) / `_outline_prompts`(大纲) /
`_volume_outline_prompts`(细纲, 含 compact/非 compact 两模式)，断言理念被引用 + 框架必要元素仍在。

**试点暴露并修复的 3 个真实问题**：

| # | 问题(试点发现) | 修复 |
|---|---|---|
| 1 | LLM 偶尔只给 1 个副母题/无隐藏母题，`parse_ideology_kernel` 让残缺输出覆盖了 fallback 的完整脊柱 | `_ensure_structure`：保留 LLM 主题内容，但从 fallback 回填 action+suspense 双副母题 + 隐藏母题(按 motif 去重) |
| 2 | 细纲 compact 模式(卷≤12 章)把 story_design_kernel 截断成 ~228 字 JSON，理念被丢 | `_compact_outline_context_block` 改为先渲染**紧凑理念块**(`render_ideology_compact_block`：主主题/核心问题/信念弧/代价/禁用解法) |
| 3 | 非宇宙层主母题(代价/真相 领衔)被门禁误判"缺宇宙层" | 结构门禁：`cosmic_premise` 实质文本即视为覆盖宇宙层(世界前提本就是宇宙层内容) |

**修复后结果**：下游引用 **4/4 PASS**(卷纲/大纲/细纲-full/细纲-compact) + 世界观同块；
每个下游 prompt 都含 主主题 + 子题 + 信念弧，且框架必要元素(volume/scene/chapter/JSON 结构)俱在。
3 个修复各配单测(`test_parse_backfills_partial_llm_structure` / `test_compact_ideology_block_carries_spine` /
`test_gate_credits_cosmic_premise_for_noncosmic_primary`)。

> 结论(逻辑正确性)：理念内核已**被真实框架的大纲/细纲/世界观各环节引用**，且不破坏框架既有
> 必要写作元素；试点过程本身验证了"引用是否有问题"并修掉了 3 处。

## 5. 复刻到全架构的路径

理念内核作为脊柱已传播到 `StoryDesignKernel` 全链路。后续可低成本接续：

- **卷纲**：`per_volume_thesis_pressure` → 每卷 `volume_theme` 绑定 belief_arc 推进。
- **章纲/正文**：`concrete_symbols` → `motif_scaling` 的 plant→echo→transform→resolve
  排布（接口已存在，仅需把内核符号喂入）。
- **代价系统**：`cost_system` → 既有 chase_debt_ledger / payoff_ledger 绑定。
- **正文判官**：`thematic_resonance` 维度可改为"对本书 IdeologyKernel 的回响"，而非泛化。

## 6. 交付物清单

| 类型 | 文件 |
|---|---|
| 母题脚手架（深层结构） | `config/motif_library.yaml` |
| 主流主题库（公认接地主题, 主主题来源） | `config/mainstream_themes.yaml` |
| 主题语料库（补充警句, 与题材解耦） | `config/theme_corpus.yaml` |
| 母题写作脚手架（开篇钩子/三幕/人物范式/场景） | `config/motif_library.yaml::motif_templates` |
| 母题配方范例 + 创作原则（few-shot） | `config/ideology_exemplars.yaml` |
| 判官配置 | `config/ideology_judge.yaml` |
| 内核领域模型 | `src/bestseller/domain/ideology.py` |
| 判官结果类型 | `src/bestseller/domain/ideology_judge.py` |
| 库加载 + 组合引擎 | `src/bestseller/services/ideology_library.py` |
| 内核推导（LLM + fallback） | `src/bestseller/services/ideology_kernel.py` |
| 结构门禁 + 落地审计 | `src/bestseller/services/ideology_coherence_gate.py` |
| 理念判官（advisory） | `src/bestseller/services/ideology_judge.py` |
| 规划链路融合 | `src/bestseller/services/planner.py`（`_generate_story_design_kernel` / `_story_design_kernel_prompts`）+ `story_design_kernel.py`（字段 + 渲染） |
| 试点 A/B | `scripts/verify_ideology_outline_ab.py` |
| 真实框架端到端试点 | `scripts/pilot_ideology_framework.py` |
| 多样性验证（去题材绑定） | `scripts/verify_ideology_diversity.py` |
| 主题语料库扩充（→1000-2000） | `scripts/expand_theme_corpus.py` |
| 单元测试 | `tests/unit/test_ideology_library.py`、`tests/unit/test_ideology_kernel_judge.py` |
