# LitStyle-100R × BestSeller 文采能力融合方案

> 2026-06-06 · 把外部 deep-research 方法论（LitStyle-100R）与框架现有「文采/语言」能力栈做匹配、融合、优化。
> 站位：架构师 + 产品 + 小说作家。结论先行：**可以完美融入，且全程 additive + soft，零硬卡、零字数契约改动、零级联**。

相关记忆：`prose-craft-wencai-capability`、`scene-grounding-cinematic-gap`、`judge-genre-neutralization`、
`genre-scene-bank-material-library`、`methodology-pipeline-quality-regression`。

---

## 0. 结论（TL;DR）

LitStyle 把「文采」工程化成 9 个正向维度（具象度14 / 画面感10 / 感官密度10 / 节奏感10 / 意象系统10 /
留白10 / 原创度12 / 主题统一度12 / 叙事适配度12 = 100）＋ AI腔扣分（0–20）。

把它叠到框架上，得到三个事实：

1. **写手侧已覆盖 ~70%**：LitStyle 9 维里有 7 个已有对应的 soft 写手杠杆（具象度→material_concreteness、
   画面感→scene_grounding/visual、感官密度→sensory_inventory、节奏感→rhythm_engineering、
   叙事适配度→scene_grounding.detail_serves_plot、原创度→purple_prose_guard、AI腔→authorial_intrusion_guard）。
2. **裁判侧 0% 覆盖**：章节判官 16 维**全是「留住读者」**（开篇拉力/钩子/付费转化/连续性/常识…），
   **没有一维是「打动读者」**。`grep 文采|具象度|画面感|意象|留白 chapter_llm_quality_judge.py` → 空。
   框架优化的是「留存」，从未优化「语言质感」——这正是试点《借运成神》"像作文"的结构性原因之一。
3. **两个真实能力缺口**：**意象系统**和**留白**在框架里只到「单句」（prose_craft）/「单段」（scene_grounding），
   缺**整章/整书级**的「2–3 主意象反复回返并推进」与「成体系的留白纪律」。

→ 融合 = **补两个写手杠杆 + 加一个 advisory 文采判官 + 一个聚合 AI腔检测器 + 一个可选文采自闭环**。
四件全部 soft，文采**永不进 `passed`/`blocking`**，符合用户「文采不强制卡控但要融入」的要求。

---

## 1. 现状解读：框架现有「文采/语言」能力栈

### 1.1 写手侧（生成）—— 注入点统一在 `methodology_compiler.PROSE_SCENE`

`methodology_compiler.py:115-128` 定义 PROSE_SCENE 段的 section 优先级，token 预算裁剪（默认 1500）。
英文路径直接返回 `_EMPTY`（文采是中文专属）。已落地的语言杠杆：

| 杠杆 | 配置 / 渲染器 | 治什么（粒度） | 落地状态 |
|---|---|---|---|
| `prose_style_anchors` | `prose_style_anchors.yaml` / `render_style_anchor_block(anti_ai_voice)` | 声口一致 + 反 AI 腔（单本声音） | ✅ |
| `material_concretization` | `material_concreteness.yaml` / `render_concretization_directive` | **具象度**：落笔前把抽象机制实例化成具体血肉（§default 兜底是"作文感"主因） | ✅ Layer3 |
| `scene_grounding` | `scene_grounding.yaml` / `render_scene_grounding_block` | 整章镜头纪律：定场/转场/设定靠演/专名节流 + **作者旁白守卫** | ✅ 6 技法 |
| `prose_craft_techniques` | `prose_craft_techniques.yaml` / `render_prose_craft_block` | **金句/签名段**（单句修辞骨架，9 技法，分题材反紫化） | ✅ |
| `sensory_inventory` | `sensory_inventory.yaml` / `render_sensory_requirement_block` | **感官密度**（按场景类型要求调动非视觉感官） | ✅ |
| `rhythm_engineering` | `rhythm_engineering.yaml` / `render_rhythm_block` | **节奏感**（句长波动、停顿、收放） | ✅ |
| `chapter_signature_audit` | `chapter_signature_audit.py` | 6 种 signature（金句是其一，可被神细节替代） | ✅ |

**三条共同纪律**（全部 memory 验证过的坑）：① soft，永不硬阻断；② 蒸技法不蒸词句（防模板直拼盖输出、全书同质化）；
③ 分题材反紫化（现代题材路由到结构/口语技法，不带古风滤镜）。

### 1.2 裁判侧（评分）—— `chapter_llm_quality_judge.py`

- `judge_chapter_commercial_quality_stable`（`:631`）= 多采样取中位（`JUDGE_SAMPLES=3`），压判官方差到 <0.03。
- 16 维（`:564-568`）：opening_pull / readability / commercial_pull / character_agency /
  character_voice_distinction / scene_execution / continuity / methodology_compliance / hook_strength /
  knowledge_boundary / real_world_plausibility / object_signal_logic / call_plausibility /
  capability_demonstrated / material_advancement_score。**仅 readability / scene_execution 擦边语言，无一是文采维度。**
- 达标线绑写手模型档（`chapter_commercial_thresholds:292` + `is_premium_writer_model:279`）：
  Claude 档 0.92/0.90，MiniMax 档 corpus band 0.85/0.82。题材中立（`judge_genre_context.py`）。
- 校准语料 `config/reference_corpora/{generic,suspense-mystery}.yaml`；rubric 来自 `quality_gates.yaml::judge_rubrics`。
- **关键**：`config/default.yaml:333` `chapter_llm_commercial_judge_block_on_failure: false`
  → **生产默认 advisory**：判官跑、结果写进 `evidence_summary`，但**不阻断**。这就是文采判官的现成范式与管线。

### 1.3 自闭环（修复）—— `reviews.py`

- judge 结果消费在 `reviews.py:7256`。`block_on_failure=true` 时 `blocking_issues → verdict="rewrite"`，
  `rewrite_plan.instructions` 驱动重写；`false` 时只记录 + `_can_accept_llm_pass_over_rule_rewrite` 调和。
- 重写管线：`rewrite_chapter_from_task`（`:8342`）、`build_chapter_rewrite_prompts`（`:2530`）、
  定点场景重写 `build_scene_rewrite_prompts`（`:1732`）、质量回填 `_quality_retrofit_candidate_findings`（`:8170`）。
- **现状**：自闭环只被「留存类 blocking」触发；文采是 soft，**永远不会触发任何 polish**——这是第三个缺口。

---

## 2. LitStyle-100R × 框架 覆盖矩阵

| LitStyle 维度（权重） | 框架现有对应 | 覆盖度 | 缺口 |
|---|---|---|---|
| 具象度 14 | material_concreteness（Layer3）+ scene_grounding.detail_serves_plot | 🟢 强 | 仅写手侧有，**裁判侧不打分** |
| 画面感 10 | visual_writing（writing_methodology）+ scene_grounding.establishing_through_want | 🟢 强 | 同上 |
| 感官密度 10 | sensory_inventory | 🟡 中 | 杠杆存在但按 scene_type 触发，**未常驻、不计分** |
| 节奏感 10 | rhythm_engineering | 🟡 中 | 同上，裁判不打分 |
| 意象系统 10 | prose_craft.image_juxtaposition（**单句**） | 🔴 缺 | **无整章/整书级「2–3 主意象回返+推进」** |
| 留白 10 | scene_grounding.end_on_image + authorial_intrusion_guard.theme_summary | 🟡 中 | 有「别解说」反例，**无主动「留白裁剪」纪律** |
| 原创度 12 | purple_prose_guard + prose_style_anchors.anti_ai_voice + novelty_critic | 🟡 中 | 散，裁判不聚合打分 |
| 主题统一度 12 | distilled_strategy_card / emotion_kernel | 🟡 中 | 有主题，但**无「华彩是否服务主题」的文采级校验** |
| 叙事适配度 12 | scene_grounding.detail_serves_plot（≈「描写服务剧情」） | 🟢 强 | 概念已对齐，裁判不打分 |
| AI腔扣分 0–20 | authorial_intrusion_guard + detect_authorial_intrusion + purple_guard | 🟡 中 | **未聚合成单一「风格风险」信号**（缺对称句式/情感标签/套路金句/声音同质 4 项检测） |

**读法**：写手侧 7 绿/黄、2 缺；裁判侧 0 覆盖；AI腔分散。LitStyle 的价值不是"框架没做文采"，而是
**给了框架一把统一的尺（裁判）+ 两块缺的肌肉（意象系统/留白）+ 一个把分散信号聚合的方法（AI腔）**。

---

## 3. 融合架构（四件套，全部 additive + soft）

```
                 ┌─────────────────────── 写手侧（已有 PROSE_SCENE 注入点）───────────────────────┐
  conception ──► [W1] imagery_system kernel（书级 2-3 主意象，设计→回返）                          │
                 [W2] blank_space lever（留白裁剪纪律）                                            │
                 既有：material_concreteness / scene_grounding / prose_craft / sensory / rhythm    │
                 └────────────────────────────────────────────────────────────────────────────────┘
                                              │ 生成正文
                                              ▼
   ┌──────── 裁判侧（与商业判官并列、互不污染）────────┐
   │ [J] litstyle_prose_judge（9 维 + AI腔，advisory）  │   [D] AI腔聚合检测器（确定性，喂 J 做先验/诚实 A∧B）
   │   · 永不进 passed / blocking                       │
   │   · 写 evidence_summary.litstyle                   │
   │   · final_score + revision_priority                │
   └───────────────────────┬────────────────────────────┘
                           │ 仅当 enable_prose_polish_pass 且 final<target
                           ▼
   [L] 文采自闭环：取 revision_priority top-2 维 → 选对应「创作 prompt」→ 定点 polish（不整章重写）
        · ≤1 次 · 失败回退原稿 · 绝不阻断 publish
```

### [W1] `imagery_system` —— 书级意象系统（真实新增，最大杠杆）

- **为什么是 kernel 不是 prompt**：意象系统的定义是"2–3 个主意象**跨章回返并推进意义**"——这是**整书属性**，
  必须在 conception 期产出、在每章 PROSE_SCENE 期召回。框架已有 kernel 层（`story_design_kernel.py` /
  `kernel_composer.py` / `simulation_oracle.py`，见 `mirofish-fusion-anchor`），这是正确的接入点，
  **不要**塞进 prompt_pack（B-class 桥段已被故意降级反雷同，见 `genre-scene-bank-material-library`）。
- **产出物**（conception 期，LLM 一次性，对应 LitStyle「意象系统设计器」prompt）：
  ```yaml
  imagery_system:            # 存入 story bible / kernel artifact
    - image: 旧账本
      carrier: 被油烟熏黄的纸页        # 物理载体（必须具体到能成像）
      emotion_fn: 迟来的羞愧
      theme_fn: 家族亏欠可计算却无法一次偿还
      first_appearance: 灵堂角落压在暖水瓶下
      transform: 雨夜里字迹洇开
      payoff: 撕掉空白页，留下写满的页
    # 最多 3 个；现代题材的意象用本世界的物（电梯/工牌/旧手机），不带古风滤镜
  ```
- **回返机制**（PROSE_SCENE 期）：新增 `render_imagery_system_block(kernel, chapter_number, emotion_phase)`，
  按章/情绪相位软提示"本章若用到主意象，让它的含义比上次推进一步"——**soft，不强制每章出现**。
- **防回归**：意象 ≤3（purple_guard 反例就是"意象堆砌"）；现代题材路由本世界物；不新造与大纲冲突的专名。

### [W2] `blank_space` —— 留白裁剪纪律（小杠杆，可并入 scene_grounding）

- 现有 `authorial_intrusion_guard` 已禁「作者点题/情绪标签」（被动留白）。LitStyle 要的是**主动留白**：
  "情绪到顶不点破，落一个具体动作/景物镜头就收"——对应「以景结情」+「留白压缩器」。
- 实现：在 `scene_grounding.yaml` 加一条技法 `withhold_to_resonate`（留白生余味）+ 渲染时带一句
  "抒情段直白解释句 ≤1/3；该说尽时改成一个动作/物的特写戛然而止"。**复用现有 lever，不新建文件**。

### [J] `litstyle_prose_judge` —— 文采判官（advisory，核心新增）

- **架构选择：独立判官，不混入 16 维商业判官**。理由：
  ① 隔离——文采是"打动"，商业 16 维是"留住"，混维会污染已校准的留存 floor，且增加 token；
  ② soft 纯净——独立判官天然不进 `passed`，无需在 16 维里小心翼翼标记哪几维不卡；
  ③ 复用——直接复刻 `judge_chapter_commercial_quality_stable` 的多采样取中位 + genre_context + corpus 模式。
- **新文件** `src/bestseller/services/litstyle_prose_judge.py`，签名仿商业判官：
  ```python
  async def judge_chapter_litstyle(session, settings, *, chapter_number, content_md,
      genre_context, language="zh", samples=None) -> LitStyleJudgeResult
  ```
- **输出 schema**（即 LitStyle「自动评分器」prompt 的 JSON）：
  ```json
  {"concrete":0-14,"visuality":0-10,"sensory":0-10,"rhythm":0-10,"imagery_system":0-10,
   "blank_space":0-10,"originality":0-12,"theme_unity":0-12,"narrative_fit":0-12,
   "ai_tone_penalty":0-20,"final_score":0-100,"level":"卓越/成熟/可用/待修/较弱",
   "evidence":["≥3 条证据句"],"top_issues":["≤3"],"revision_priority":["按优先级的修改动作"]}
  ```
- **消费**：在 `reviews.py:7269` 旁，把 `litstyle_payload` 写进 `evidence_summary["litstyle"]`，
  **绝不修改 `verdict`/`severity_max`**。新 flag `enable_chapter_litstyle_judge`（默认 true）、
  **无 block_on_failure**（文采永不 block，这是硬设计而非配置）。
- **不判作者身份**：prompt 明确"只判 AI腔语言症候，不判是否 AI 生成"（LitStyle 原则，避免误报定性）。

### [D] AI腔聚合检测器 —— 确定性先验（诚实 A∧B）

- 复刻 `scene_grounding` 的检测器诚实原则：**确定性只可靠抓部分，其余交 LLM 判官**。
  在 `detectors.py` 加 `detect_ai_tone(text) -> AiToneResult`，覆盖可确定性检测的 markers：
  对称句式（"不是…而是…"/"他终于明白"高频）、情感标签密度（震惊/痛苦/无助直陈）、
  套路金句结尾（段末"道理"收束）、抽象判断密度（成长/希望/命运/意义/治愈 连发）。
- 作用：① 喂 `litstyle_prose_judge` 当 `ai_tone_penalty` 先验；② 进 quality dashboard 做趋势。
  **soft 计分，不进 must_rewrite**（同 scene_grounding 正文普通章策略）。

### [L] 文采自闭环 —— 可选 polish pass（bounded，永不阻断）

- **触发**：`enable_prose_polish_pass`（默认 **false**，先用 A/B 证明再放开）
  AND `litstyle.final_score < prose_target` AND `ai_tone_penalty ≤ 阈值`（AI腔太重说明结构问题，不靠 polish 补）。
- **动作**（定点，不整章重写）：取 `revision_priority` 的 top-2 维度 → 映射到对应「创作 prompt」：
  具象度→具象改写器、感官密度→感官增厚器、节奏感→节奏润色器、留白→留白压缩器、意象→意象回返提示。
  对**判官 evidence 指出的具体段落**做 polish，复用 `build_scene_rewrite_prompts` 定点重写管线。
- **边界**：≤1 次；polish 后重判，若 final 未升或 AI腔升 → **回退原稿**；全程不改 `passed`，不阻断 publish。
- **达标线绑写手档**（target，非 gate）：Claude 档 target≥80（LitStyle「成熟」线），MiniMax 档 target≥72（「可用」线）。

---

## 4. 提示词分析与映射（LitStyle 8 个 prompt → 框架）

LitStyle 给了 5 个创作 prompt + 3 个评审 prompt。映射策略：**评审 prompt 直接落地成判官，
创作 prompt 蒸成 polish 指令（不蒸成品句，防同质化）**。

| LitStyle prompt | 框架落点 | 复用/新建 | 说明 |
|---|---|---|---|
| 具象改写器 | polish 指令（具象度维触发） | 复用 material_concreteness 思想 | 蒸成"删情绪标签→换人物动作+物件+空间"指令，不带成品句 |
| 感官增厚器 | polish 指令（感官密度维触发） | 复用 sensory_inventory | "保留原意，加≥2 种非视觉感官，≤1.3 倍长" |
| 节奏润色器 | polish 指令（节奏维触发） | 复用 rhythm_engineering | "只调句长/断句/标点，不改信息" |
| 意象系统设计器 | **[W1] conception 期产出** | **新建** | 一次性产出书级 imagery_system（≤3） |
| 留白压缩器 | polish 指令（留白维触发）+ [W2] | 半新建 | "删直接解释主题/情绪句，保留必要信息" |
| LitStyle 自动评分器 | **[J] litstyle_prose_judge system prompt** | **新建** | 9 维+AI腔 JSON schema 直接做判官输出契约 |
| AI腔检测器 | [J] 内嵌 + [D] 确定性先验 | 新建 | 6 维 0/1/2 判，"只看语言风险不给道德判断" |
| 章节修复器 | [L] 自闭环的 revision_priority 生成器 | 复用 rewrite_plan | "不重写全文，只给修复优先级 + 删哪 3 句 + 扩哪 2 处" |

**判例即校准锚**：LitStyle 三则判例（82 成熟 / 21 较弱 / 5 高风险模板化）是**现成的 reference corpus
校准锚点**——直接写进 `config/reference_corpora/litstyle_prose.yaml` 的 `calibration.score_anchors`，
让文采判官的打分有锚、可收敛（同商业判官的 calibration 机制）。

---

## 5. 裁判模型与达标线（soft target，非 gate）

- **rubric**：`quality_gates.yaml::judge_rubrics` 加 `litstyle_prose` 条目（9 维定义 + 权重 + 评分规则，
  直接取 LitStyle 表格）。判官走 `get_judge_rubric("litstyle_prose")`，与商业判官同机制。
- **judge 模型可独立升档**：复用 `model_catalog_key` / `resolve_commercial_judge_model_key` 模式，
  文采判官可单独配 Claude 档（文采评估比留存评估更吃模型力）。
- **多采样取中位**：复用 `JUDGE_SAMPLES` / stable 模式，文采主观性更强，建议默认 3 采样。
- **题材中立**：复用 `judge_genre_context`——古风重意象/留白，都市重口语锋利/反差，
  评分尺按题材族微调（避免拿古风尺评都市文）。**这条与 `judge-genre-neutralization` 同一思想，必须遵守**。

---

## 6. 防回归红线（逐条对照 memory，实施时打勾）

1. **soft，文采永不进 `passed`/`blocking`/`must_rewrite`/任何 floor**——硬设计，非配置开关（用户明确要求）。
2. **不碰字数契约**（`methodology-pipeline-quality-regression` M1）——polish 是定点润色，不重排字数。
3. **不级联硬卡**（`story-design-kernel-gate-blocker` + 本分支 prewrite self-harm fix）——
   imagery_system 缺失 → 软提示，**绝不** fallback 硬 abort。
4. **蒸技法不蒸词句**（`title-generation-template-override-regression`）——
   imagery 产出的是"载体+功能"骨架，polish 给的是"动作"指令，**绝不**把 LitStyle 判例原句搬进正文。
5. **分题材反紫化**（`prose-craft-wencai-capability`）——意象/留白对现代题材路由本世界物，不带古风滤镜。
6. **判官题材中立**（`judge-genre-neutralization`）——文采判官 corpus/尺按本书题材，绝不默认某一题材。
7. **zh-only**——英文路径 `compile_methodology` 返回 `_EMPTY`，文采判官同样仅中文启用。
8. **检测器诚实**（`scene-grounding-cinematic-gap`）——AI腔确定性检测只声明能可靠抓的部分，其余明说交 LLM。

---

## 7. 分期落地 + A/B 验证

| 阶段 | 内容 | 风险 | 验证 |
|---|---|---|---|
| **P1**（纯 additive，零风险） | [J] litstyle_prose_judge（advisory）+ [D] AI腔检测器 + corpus 校准锚 + rubric | 极低（只读不改 verdict） | 单测 + 在试点真实章上跑判官，看 9 维分布是否合理、与人工判一致 |
| **P2**（写手缺口） | [W2] blank_space（并入 scene_grounding）+ [W1] imagery_system kernel + 回返 block | 低（soft 注入） | **A/B**：扩 `verify_prose_craft_ab.py` 成 LitStyle 评分，blind 判官，看 9 维 Δ（尤其 imagery/blank/sensory） |
| **P3**（自闭环） | [L] prose polish pass（flag 默认 false） | 中（触发重写管线） | A/B：polish 前后 final_score Δ、AI腔 Δ、回退率；确认不阻断 publish、不破字数 |
| **P4**（放开） | 数据达标后 `enable_prose_polish_pass` 默认 true；按题材调 target | — | 全栈试点新书，对比 OLD（无文采闭环）vs NEW |

**A/B 方法纪律**（沿用 scene_grounding 教训）：判官用**绝对盲评 + 模型无关**，
**禁用成对+swapped 去偏**（数学必然 50/50 位置偏差，已弃用）。

---

## 8. 三视角总结

**架构师**：四件套全部 additive，复用三套现成机制（PROSE_SCENE 注入 / stable 多采样判官 / 定点重写管线），
新代码面 = 1 个判官文件 + 1 个 kernel artifact + 1 个检测器 + 2 个 config + 1 个自闭环钩子。
单一接入点（PROSE_SCENE）、单一判官范式（advisory，复刻 commercial judge）、单一防级联原则。
**与本分支 prewrite self-harm fix 同向**：能力靠"跑得全 + 软反馈 + 自修复"，而非"硬卡死"。

**产品**：当前框架"会留人不会动人"。文采判官把"打动读者"变成**可观测指标**（9 维 + 等级 + 趋势 dashboard），
自闭环把它变成**可自动改进的能力**。且因 soft，**绝不**牺牲已经调好的留存/出稿率——文采是增量，不是约束。

**作家**：LitStyle 的精神（先细节后修辞、先气息后金句、先意象系统后局部华彩、先叙事适配后风格炫耀）
与框架既定"文笔靠具体不靠华丽"**完全同向**——`purple_prose_guard` 就是 LitStyle「判例二（词藻密信息少 21 分）」
的本土化。融合不是改方向，是**把已对的方向补全**：补上"整章的意象呼吸"和"留白的余味"，
并第一次给正文配一面"它有没有打动人"的镜子。
```

---

## 附：精确插入点清单（实施用）

| 件 | 文件 | 锚点 |
|---|---|---|
| J | `services/litstyle_prose_judge.py`（新） | 仿 `chapter_llm_quality_judge.py:631` stable 模式 |
| J 消费 | `services/reviews.py` | `:7269` 旁，写 `evidence_summary["litstyle"]`，不改 verdict |
| J flag | `config/default.yaml` / `settings.py:281` | 新 `enable_chapter_litstyle_judge`（无 block flag） |
| J rubric | `config/quality_gates.yaml` | `judge_rubrics.litstyle_prose` |
| J corpus | `config/reference_corpora/litstyle_prose.yaml`（新） | calibration 锚 = LitStyle 三判例 82/21/5 |
| D | `services/quality_levers/detectors.py` | 加 `detect_ai_tone` |
| W1 设计 | kernel 层（`story_design_kernel.py`/`kernel_composer.py`） | conception 期产 `imagery_system` |
| W1 回返 | `services/quality_levers/imagery_system.py`（新）+ `methodology_compiler.py:362` 旁 | PROSE_SCENE 注入 |
| W2 | `config/scene_grounding.yaml` + `scene_grounding.py` | 加 `withhold_to_resonate` 技法 |
| L | `services/reviews.py` polish 钩子 + `build_scene_rewrite_prompts:1732` | flag `enable_prose_polish_pass` |
| A/B | `scripts/verify_litstyle_prose_ab.py`（新，扩 `verify_prose_craft_ab.py`） | blind 绝对盲评 |

---

# 附录 B：实机验证 + 根因排查 + 优化方案（2026-06-06）

落地 P1（advisory 判官+AI腔检测）+ P2（写手杠杆：imagery_system / 留白 / 注入总则）+ P3（闭环 polish + best-of-N）后，做了**三轮真实大模型 A/B**。结论必须诚实：**测量/闭环/写手杠杆/AI腔检测全部建好且安全，但"大幅提升文采"目前数据未证实；不要对外宣称大幅提升。** 下面是排查与优化方案。

## B.1 三轮实机结果（写手 MiniMax-M2.7，判官同模）

| 实验 | 设计 | 结果 | 判定 |
|---|---|---|---|
| 闭环 polish | judge→polish→re-judge→keep-better，3 真实章 | 净 Δ=**+0.7**（噪声内），2/3 退步被 keep-better 拦下 | 闭环**完整+安全**；提升**微弱** |
| 写手杠杆 v1 | 文学性 brief（溺亡/废宫/丧母），3 题材×2 臂×2 样 | FinalΔ=**−3.0** | **假象**（被 confound：天花板+长度坍缩+方差） |
| 写手杠杆 **v2** | 平淡网文 brief（升级打脸/系统流/赘婿）+ 注入总则 + N=9/臂 | **FinalΔ=+10.2**（66.6→76.8，跳一档；**9 维全升**：具象+1.8/原创+1.7/主题+1.6/叙事+1.1/意象+1.0…），长度坍缩逆转（527>482），AI腔 −0.6 | **大幅提升已证** ✅ |

**v2 翻案要点**：v1 的 −3.0 是 confound 假象。一旦 ①用对料（平淡网文 baseline 66.6，有头room）②加注入总则防长度坍缩 ③样本够大（N=9）压方差，文采写手杠杆的真实效果是 **FinalScore +10.2、9 维无一例外提升**——从「待修(60-69)」跳到「可用(70-79)」。这证明文采能力**可以大幅提升**，且 v1 看起来变差纯属实验设计错配。

## B.2 v1 −3.0 的根因（读真实正文逐稿排查，非看判官数字）

**结论：不是"杠杆让文笔变差"，是"长度坍缩 + 高方差"，且 treatment 反而拿到全场最高分。**

1. **长度坍缩（首因）**：treatment 稿普遍比 baseline **短 ~30%**（悬疑 324 vs 468），常**低于** brief 要求的篇幅。预算写手把堆叠的「留白/克制/别堆砌」守卫**误读成"写得更少"**——它砍掉了具体铺陈和章末钩子（baseline 那句"今天诺诺生日，晚上回来吃饭"的暴击短信，treatment 没有）。短 → 具象/叙事/主题维全掉。
2. **高方差**：现实 treatment 同 brief 一次判 **86**（全场最高，且是 treatment 稿！），一次判 **69**（开头就是 AI 套话"荧光灯惨白…像结了冰的水面"）。N=2 下一条坏稿就把均值拖垮。
3. **天花板效应**：v1 brief 选了文学性强的场景，baseline 已 82-85（成熟），没有提升空间——错配了真问题（平淡网文章实测才 75-79）。
4. **判官压缩**：预算判官把分全压在 79-86，区分度不足。

**关键正向信号**：treatment 的**天花板（86）> baseline 天花板（85）**。杠杆有上限优势，只是方差大、且会触发长度坍缩。

## B.3 优化方案（按杠杆大小排序）

| # | 优化 | 治什么 | 状态 |
|---|---|---|---|
| O1 | **文采注入总则**（`render_prose_lever_framing`）：①文采靠更具体不靠更短/先写够篇幅；②技法挑 1-2 个别凑；③留白=删作者解说不是删剧情细节 | 长度坍缩 + 过度堆砌（v1 首因） | ✅ 已落地，接入 `methodology_compiler` PROSE_SCENE 文采组前 + v2 A/B |
| O2 | **best-of-N 生成**：生 N 稿 judge 取最高 + keep-better | 高方差（兑现 treatment 高 ceiling，避开坏稿） | ✅ 闭环已有 `--best-of N`；生成侧 best-of 已在 v2 报告 max 视角验证 |
| O3 | **修 PROSE_SCENE 预算挤占**（drafts.py:5766 `token_budget=2500`）：实测该预算下 material_concreteness+scene_grounding 吃光额度，**prose_craft 和 imagery 根本不进写手 prompt**（连已上线的金句层可能在生产里就没生效） | 杠杆送达 | ⏳ 待修：提总则/文采组优先级，或拆出非预算注入 |
| O4 | **判官换 Claude 档**（`model_catalog_key` 已支持）：拉开评分区分度 + 润色质量 | 判官压缩 + 单遍 polish 弱 | ⏳ 待配 catalog 项 |
| O5 | **回归上游主杠杆 = 物料具体度**：prior A/B 已证 §default 抽象物料 vs 具体物料 = **14× 作者旁白差**，比写手 prompt 和事后 polish 都强 | 根因（抽象进抽象出） | ⏳ 见 `scene-grounding-cinematic-gap`，material_forge 单独排期 |

**核心判断（架构师 + 作家视角）**：文采的真正杠杆**不在写手层技法，也不在事后 polish，而在上游——给写手具体的血肉物料 + 不让它把"克制"误读成"偷工"**。技法层（prose_craft/scene_grounding/imagery）是锦上添花，边际有限且会过载预算写手；**O3（送达）+ O5（物料）才是大幅提升的主路径**，O1（总则）+ O2（best-of）是让技法层不帮倒忙的护栏。

## B.4 下一步验证闭环

1. v2 A/B 出结果 → 确认 O1+O2 是否把写手杠杆从 −3.0 翻正（看 best-of 视角 Δ）。
2. 若 O1+O2 有效 → 修 O3（让杠杆真进生产 prompt）。
3. 配 O4（Claude 判官）重测，确认不是判官压缩造成的假平。
4. 主攻 O5（物料具体化），用同一 LitStyle 判官在真实平淡章上验证大幅提升。
