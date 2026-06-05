# 提示词×方法论 融合审计与榜单门禁地基修复(2026-06)

> 目标:所有提示词点对齐方法论+提示工程,产出稳定达到框架门禁与榜单级。
> 本文是审计结论 + 已落地修复 + 剩余路径。

## 1. 审计结论(交叉比对 116 个 render 生产者 × writer/planner/critic 消费者)
- **方法论不是"躺在框架里没用"**:writer prompt 实际注入 ~75 个 render 块 + `build_writer_quality_levers_block`
  聚合器(节奏/情绪编排/感官/信息编排/风格锚/章节签名)。真正孤儿只有 3 个通用方法。
- **真正病根 = 组装工程 + 判官不可信**:
  1. 12 个生产者会吐"暂无/未指定/none"占位 → 噪声稀释信号(写作画像 block 已系统性去噪)。
  2. 无分层/预算裁剪:75+ 块无差别拼接,本章内容与高价值方法论被淹没。
  3. 约束 framing 压过方法论 framing。
  4. 个别方法论(book_methodology=修复路径、emotion_stack=短篇路径)未进长篇初稿。
  5. **榜单门禁判官 = MiniMax-2.7(最弱模型)、单样本**:同一稿评分 0.77~0.92 乱跳(方差0.11)。

## 2. 已落地修复(均进真实管线,有验证)
| 修复 | 文件 | 验证 |
|------|------|------|
| thinking.type 归一化(MiniMax `enabled→adaptive`,曾 400 崩溃) | services/llm.py `_normalize_thinking_type_for_model` | 单测:M3 enabled→adaptive |
| 按模型 max_tokens 防截断(正文角色) | services/llm.py `_effective_request_max_tokens` + word_targets `model_min_output_tokens` | M3=16000/DeepSeek=8192 |
| 写作画像系统性去噪(剪占位行+空段) | services/writing_profile.py `_prune_profile_lines` | 重生成后 prompt 0 个"暂无" |
| 写作提示词:方法论前置 + 榜单硬维度定向指令 | tmp/mirofish-phase0 bake-off(待移植 drafts.py) | 质量 0.72→0.83~0.92 |
| **榜单门禁稳定化:多采样中位判官** | chapter_llm_quality_judge.py `judge_chapter_commercial_quality_stable` + 接进 reviews.py 门禁 | **方差 0.11→0.01** |

## 3. 榜单门禁实测(框架自有 16 维判官,chapter-1 线:overall≥0.92 + 8 硬维度≥0.88~0.9)
- 优化提示词后 M3 曾达 0.92(噪声峰值);**稳定中位判官下真实质量 ≈0.87**。
- 单次/best-of-5 峰值 0.91,合取硬维度墙难一次跨过 → **需 editor 定点重写循环**(靠稳定判官给真信号收敛)。

## 4. 剩余路径(地基已稳,现可执行)
1. **editor 定点重写循环 ×稳定判官**:只补判官指出的失败硬维度,保留其余 → 0.87→0.92。
2. **移植优化提示词进 drafts.py/planner.py 真实管线**:方法论前置 + 通用空块过滤 + 分层预算。
3. **补缺口**:book_methodology / emotion_stack 接进长篇初稿。
4. 端到端跑通一本短篇,确认每章稳定过稳定门禁。

## 4b. 移植进真实管线 + 榜单线校准(2026-06-05,已落地)
按用户决策"先移植进 drafts.py 真实管线",已把验证过的优化全部接入生产路径并修复连带框架问题:

| 改动 | 文件 | 说明/验证 |
|------|------|-----------|
| 榜单级硬维度自检块前置进 writer system prompt | services/drafts.py(en/zh 两分支)+ chapter_llm_quality_judge.py `render_ranking_self_check_block` | 写手现在被直接告知门禁评分的 8 个硬维度(及阈值),**单源派生自 `chapter_commercial_thresholds`**,阈值变即同步。集成实测:真实 system prompt 含该块。 |
| 通用占位去噪进 writer user prompt 唯一收口点 | services/prompt_compactor.py `_prune_placeholder_lines`(接入 `compact_user_prompt`) | 跳过含 `"`/`{`/`}` 的 JSON 行防误伤。真实 ch1 prompt 占位 **11→0**,字数 25970→16664。 |
| 门禁浮点伪失败修复(epsilon) | domain/llm_quality_judge.py `_GATE_EPSILON=1e-6`(meets_threshold + 合成 issue)+ chapter_llm_quality_judge.py 稳定判官 | 中位聚合会得 0.8999999999999999,naive `<0.90` 误判失败。修后实质=0.90 的维度可过、真实 0.85 仍失败(单测证)。 |
| strong-tier 写手真正启用 M3(原为空操作) | .env `WRITER__MODEL_OVERRIDE` M2.7-highspeed→**M3** + `THINKING_TYPE=disabled` | 框架本有"黄金三章用 strong tier"机制,但 model_override 配成与 model 相同=空操作。修后 ch1-3 真用 M3。 |
| 应用层 best-of-N(仅 strong tier) | services/drafts.py 候选循环 `variant_plan` + .env `WRITER__N_CANDIDATES=3` | MiniMax 忽略 provider 端 `n`,故按 n_candidates 多次采样、复用 `_score_writer_candidate` 择优;成本集中在 ch1-3。 |
| 榜单线按所选模型校准 0.92→0.88 | config/reference_corpora/suspense-mystery.yaml + chapter_commercial_thresholds 默认 | 见下决定性结论。 |

**决定性结论(真实 per-scene 管线 + 移植后提示词,稳定判官)**:
- M2.7-highspeed(原生产写手)overall **0.65**;同管线换 M3 → **0.80~0.88**(+0.15~0.23)。
- M3 单稿最佳 **0.88**,且 **8 个硬维度全部达标**(epsilon 修复后 call/capability 的 0.90 正常通过),唯一缺口=overall 复合分 0.88 < 0.92。
- 整章重写循环 **会退化不会爬升**(0.88→0.72→0.72),M3 无法稳定冲 0.92。
- → 0.92 是 Claude 级校准线;**MiniMax-M3 的真实榜单线=0.88**。经用户拍板:校准到 0.88 + best-of-3;若日后写手升级 Claude 级,把 floor 调回 0.92(YAML/默认各一处注释已标注)。

## 4c. 大纲/细纲同质化污染 + 题材误配 + 分数区间(2026-06-05,已部署)
用户反馈"大纲没按题材/设定/方法论设计、同质化、引用特定书设定"。根因排查+修复:

**A. 题材误配(同质化主因)**:`genre_review_profiles._PRIORITY_KEYWORD_MAP` 把 `都市→urban-contemporary` 放优先匹配,且优先解析先于通用 map。`都市异能·身份反转`(试点书)同时含"都市/异能",被"都市"短路误判为 **都市职业现实**(异能书当现实书设计/评审)。修:`_resolve_priority_keyword_category` 把"都市/职场"等纯背景词设为 `_SETTING_ONLY_PRIORITY_CATEGORIES`,有强题材驱动词时让位;并补全 `修真/修炼/武道/超能/高武` 等驱动词。验证:都市异能/都市修真→升级流,都市职场→urban,都市言情→relationship,科幻机甲→scifi,娱乐圈→urban 全对。**试点书已正确路由 升级流/Action-Progression**。

**B. 串书污染(硬编码某书专有名词)**:`青囊/困魂镜/林正淳/铜钱/罗盘/认账/镜债/账线` 等《青囊不语问阴阳》(探案)专有名词被硬编码进**通用大纲/写作代码**。已修大纲路径:① `outline_llm_judge` 系统提示词 #2/#4 题材中立化(认知边界/关键道具逻辑改为"按本书设定术语判断,勿套其它题材");② `planner._outline_prompts` 去掉"supernatural/professional object"/"detective whiteboard" 探案预设→"本书自身的关键道具/能力/装置(按题材)";③ `prompt_compactor._terms_for_chapter` 删掉硬编码 青囊 keep-set(原来非青囊书该集合为空→污染),改为返回项目自有的 front-10 列表。**遗留(本轮未做,chapter 级 gate)**:common_sense_gate / exposition_density_gate / chapter_constraint_manifest / commercial_novel_gate / platform_title_workflow / hook_echo_gate / fanqie_long_ranking_gate / reviews.py 仍有 青囊 道具硬编码,应同样改为从 genre_review_profile.signal_keywords 派生。

**C. 分数用区间而非单点(用户决策)**:`chapter_commercial_thresholds` 黄金三章改为**达标区间**——overall ≥ `golden_three_floor`(0.85)且每个硬维度 ≥ `golden_three_dimension_floor`(0.82),均 corpus 可覆盖;升级 Claude 级写手后调回 0.92/0.90。写手自检块单源派生,自动同步该区间。

## 4d. 大纲级同质化污染——全量梳理与修复(2026-06-05 收口)
对**规划/大纲/细纲层**做了一次从头的污染全量排查与修复。原则:**无条件作用于所有书的硬编码 = 真污染(必修)**;**条件触发/按 pack 键控的 = 正确的题材数据(保留)**。

**已修(无条件污染,12 处 + 1 新共享模块)**:
| 文件 | 修法 |
|------|------|
| `genre_review_profiles.py` | 题材路由:`都市/职场` 等纯背景词不再压过强题材驱动(`_SETTING_ONLY_PRIORITY_CATEGORIES`);补 `修真/修炼/武道/超能/高武` 驱动词。**都市异能→升级流(原误判都市职业现实)** |
| `outline_llm_judge.py` | 判官系统提示词题材中立化:认知边界 / 关键道具逻辑 / 误伤豁免示例均改"按本书题材判断,勿套其它题材" |
| `planner.py` `_outline_prompts` | 去 "supernatural object"/"detective whiteboard" 探案预设 |
| `commercial_planning_readiness.py` | `evaluate_*` 加 `genre/sub_genre`,具体压力词判定**并入题材档案 conflict 词**(升级流 +34 词);pipelines + cli 透传 |
| `genre_neutral_signals.py`(新) | 题材中立 object-sensory-shortcut 检测(任意物件+发烫,排除身体部位) |
| `planning_readiness_gate.py` | object-heat 换共享检测;删硬编码人名的认知边界泄漏检查(改 LLM 判官) |
| `chapter_outline_readiness_gate.py` | 同上 + forbidden-action **通用动词替换变体生成器**(保留功能、跨题材) |
| `outline_specificity_gate.py` | 实体正则改通用引用实体 + 广义具体名词;占位短语去 林渊/账印 |
| `chapter_generation_input_builder.py` | **specialist_rule_terms 从本书 worldview 派生**(原硬编码注入每本书);lay 规则去 王建业/小雨;object 指令去 铜钱/青囊/罗盘 |
| `chapter_predraft_quality_gate.py` | repair_hint 题材中立;规则术语要求改为"有专业讲解者才提示" |
| `bible_gate.py` | tag_memory 示例去 铜钱/桃木杖 |

**保留(条件触发,非污染)**:`material_density`(pack 键控)、`reveal_schedule_builder`(token 门控)、`character_role_gate`(能力门控)、`commercial_planning_readiness._CONCRETE_PRESSURE_TERMS`(oracle 单源依赖 simulation_oracle:275 + test_ranking_readiness 用作测试内容;题材增广后其中的青囊词对其它书已无害)。

**验证**:相关单测全绿(genre/outline judge/planning gates/outline specificity/chapter outline readiness/predraft/bible/generation input/commercial planning = 94+,逐文件复跑全过);新增/改写 4 条测试反映题材中立契约。镜像重建 + up -d 部署,新容器确认:都市异能→升级流、升级流压力词 +34、object-heat 跨题材生效且排除身体词、specialist 从 worldview 派生。**已知既有失败(非本次引入,clean HEAD 上即存在 14 个)**:`test_pipeline_services`(token-cap/persona/publication/scene-drift)、`test_pipeline_flow_schema`(step 名注册漂移)——属章节正文层,与大纲去污染无关。

## 5. 验证脚本(可复跑)
tmp/mirofish-phase0/ 与 output/ 下:`_ch1_bakeoff.py`(优化提示词多模型生成)、`_ch1_judge.py`(榜单判官)、
`_ch1_iterate.py`(整章重写循环)、`_ch1_bestof.py`(best-of-N)、`_ch1_targeted.py`(定点重写)、
`_judge_variance.py` / `_stable_judge_test.py`(判官方差→中位去噪验证)。
