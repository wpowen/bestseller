# 镜头化场景锚定（Scene Grounding）改造方案 · 2026-06

> 触发：试点书《借运成神》（slug `oracle-pilot-dianshen`）正文「像写作文一样平铺直叙」。
> 目标：让正文从「解释剧情的作文」转向「站在主角立场、即时落地场景、每处描写都服务剧情」的榜单级写法。
> 性质：Layer 1+2 **已实现并通过 A/B 实验验证**（见 §9）；Layer 3 经实验证明为**主杠杆**，待排期。

> **2026-06 实验结论先行（§9 详）**：横向 A/B 证明——写手 prompt 块（Layer 1）在具体 brief 下增益**边际**；
> 真正决定「作文感」的是上游**物料具体度（Layer 3）**：抽象物料的作者旁白密度是具体物料的 **~14 倍**
> （0.66 vs 0.05），镜头感盲评 7.8 vs 8.3，三题材全胜。优先级因此调整为：**Layer 1+2 作为廉价无害的
> 卫生项已落地；Layer 3（物料具体化）是拿到最优结果的关键，应优先排期。**

---

## 0. TL;DR

- **症结不止 prompt 层**。三层叠加导致平淡：
  1. **写手层**：正文普通章拿不到任何「场景即时锚定 / 镜头连续性 / 禁作者旁白 / 人名洪流上限」约束（这些只在 `first_chapter` 分支 gate #8 里）。
  2. **检测层**：`detect_psychological_dumping` / `audit_emotion_labels` 等只在**已触发的定点重写**里跑，**不是每章常驻闸门**。
  3. **物料层（根因）**：本书 bible/material 是题材中立的 `§default-*` 抽象模板（「商业类型状态引擎」「状态变化规则」），**喂给写手的源材料本身就是抽象议论**，写手只能写成「解释机制的作文」。
- **方案**：照搬已验证的 `prose_craft_techniques` soft 模式，新增一条贯穿**全部章节**的 `scene_grounding` 杠杆；可选地把三类病做成确定性检测喂判官；并从源头把抽象默认物料具体化。
- **红线**：soft 优先、分题材、防紫化、**绝不模板直拼盖掉模型输出**（沿用 [[title-generation-template-override-regression]] 的教训），不与现行 1800–2600–3500 字契约冲突。

---

## 1. 证据与诊断

### 1.1 读的是哪一本

- 全书试点：`output/oracle-pilot-dianshen/`（chapter-001..010）。
- 反例对照（同书、手调、写得好）：`output/model-bakeoff/第一章-最终-借运成神.md`。
- 结论：框架**能**写出镜头化场景（"最终"版证明），但**生产管线的正文章节塌回平铺直叙**。问题在管线，不在模型能力。

### 1.2 三个写手层病灶（取 `chapter-005.md` 原文）

| # | 病灶 | 原文证据 | 本质 |
|---|------|---------|------|
| A | **作者旁白 / 议论文式告知** | 「合同编号触发了追踪模型，三个词凑齐，自动打标签……他们都在等他犯错。」「方远知道追的人会死，所以把自己也烧进去——让账结在他身上。」 | 作者在**解释**剧情与主题，读者没"看到"任何东西 |
| B | **场景无锚跳切** | 「手机屏幕亮起来的时候，陆沉正站在协会大堂里。」（上一拍还在电梯里）一章跨 6 个场景无落地转场 | 读者全程悬空，违反「快速显示当前场景及周围变化」 |
| C | **人名/数字洪流** | 沈墨白/沈晚棠/沈墨璃/卫东/方远/周老板/陆芷晴 + 二十三/一百/七十七/八十三单位/12.7% | 信息砸在抽象叙述里，无戏剧化场景承托 |

### 1.3 物料层根因（最致命）

`output/oracle-pilot-dianshen/source-artifacts/story-bible.md` 整本是 `§default-*` 抽象模板：
- 「商业类型状态引擎 — 章节必须围绕目标、阻力、选择、代价和状态变化推进」
- 「状态变化规则 — 每章产生一个可记录的剧情、人物、关系或世界状态变化」

**没有**任何本书具体血肉：气运借贷复利机制、黑纹债务账本、陈三指断指、灵溪「眼」级权限。
→ 这是题材中立默认物料兜底的产物（见 [[genre-scene-bank-material-library]]：玄幻有具体 scene_templates，都市异能落回抽象 default）。
→ **抽象进，抽象出**：再好的写手，对着「状态变化规则」也只能写"关于状态变化的作文"。

---

## 2. 榜单写法 → 可校验规则

把"好看"翻译成可注入 prompt、可被检测器量化的规则：

1. **定场即过滤（establishing-through-want）**：开场/转场的环境信息必须从**主角此刻的目标或危险**里渗出，不做客观罗列。
   - 反例：「变电站在暴雨里。」 正例：「拇指按在二号杆塔的螺栓上，电弧咬进掌心。」
2. **每处描写都施压或推进**：没有"背景描写"，只有"对主角构成压力/线索的环境"。任何一段景物若删掉不影响主角处境 → 它是布景，应砍或改造成钩子。
3. **设定靠演不靠讲**：规则通过动作/对峙/物件外显翻出来，不靠作者旁白总结（衔接现有 `information_choreography` 的 reveal_mode）。
4. **专有名词逐个登场**：一次只引入一个新专名，且绑定一张脸/一个动作/一个物件；前 N 章新专名密度设上限。
5. **转场必落地**：换场先给一个「此刻在哪 + 身体在做什么 + 时间标记」的锚点，再展开。
6. **POV 摄影机连续**：贴 `spotlight_character` 的连续感官流，不做无过渡的全知蒙太奇。

这 6 条就是 `scene_grounding` 杠杆与检测器要承载的内容。

---

## 3. 现状接线（已核实）

正文场景生成路径（生产管线）：`methodology_compiler.compile_methodology(stage=PROSE_SCENE)`。

`_STAGE_SECTIONS[PROSE_SCENE]`（`methodology_compiler.py:109-120`）当前注入：
```
writing_methodology_scene, prompt_pack_scene_writer, book_methodology_current,
prose_style_anchors, prose_craft_techniques, public_emotion_role_tags,
emotion_choreography_current, rhythm_engineering_current,
information_choreography_current, chapter_position_current
```

对话式写作路径（Mode B）：`quality_levers/integrator.build_writer_quality_levers_block`（`integrator.py:113`）。

**缺口（精确）**：
- 上述任何一块都**不**管「场景锚定 / 镜头连续 / 禁作者旁白 / 专名洪流」。
- 覆盖 A/C 的唯一硬规则在 `.claude/skills/bestseller-framework/prompts/writer.md` 的 `first_chapter` 分支 gate #8 —— 普通章 `positions` 为空就拿不到。
- `visual_writing` 镜头公式存在，但**按题材 prompt_pack 注入**（`config/prompt_packs/*.yaml`）；本书跑抽象默认物料 → 没接上。
- 确定性检测器（`detectors.py`：`detect_psychological_dumping@331`、`audit_chapter@628`、`audit_emotion_labels`）**只在 `reviews.py:_quality_retrofit_candidate_findings` 且 `requested_causes` 非空时跑**（`reviews.py:8176 if not requested_causes: return []`）——不是每章常驻。

---

## 4. 设计（三层，逐层递进）

### Layer 1 — 写手 soft 杠杆 `scene_grounding`（最快见效、零阻断）

**完全照搬 `prose_craft_techniques` 的成熟模式**（config + loader + render_block + 注入点）。

新增文件：
- `config/scene_grounding.yaml`
- `src/bestseller/services/quality_levers/scene_grounding.py`
- 在 `quality_levers/__init__.py` 导出 `render_scene_grounding_block` 等。

`config/scene_grounding.yaml` 骨架（与 prose_craft 同构：version / techniques / genre_fit / micro_examples / injection_policy / 反面守卫）：
```yaml
version: 2026.06.06
# 与既有杠杆的关系：
#   - visual_writing 解决"单段画面鲜烈"；本文件解决"整章如何像镜头一样落地+连续"。
#   - emotion_choreography 禁情绪标签；本文件禁"作者旁白/议论"这类叙事层告知。
#   两者正交互补。soft only，永不进 hard_gate。

techniques:
  establishing_through_want:
    display_name: 定场即过滤
    principle: 开场/转场的环境信息从主角此刻的目标或危险里渗出，不客观罗列。
    structure: 主角身体正在对抗/争取的那一个点 → 由它带出最小必要环境 → 不写全景
    genre_fit: {good: [都市, 都市异能, 悬疑, 科幻, 职场], careful: [古风, 仙侠], avoid: []}
    purple_risk: 把"过滤"写成大段心理独白；意象空泛（繁华/喧嚣）
    micro_examples:
      - {tag: 都市异能, line: 拇指按在二号杆塔的螺栓上，电弧咬进掌心。}
      - {tag: 职场, line: 工位灯还亮着，屏幕右下角是 23:47，和那封没发出去的邮件。}
  grounded_transition:
    display_name: 转场落地
    principle: 换场先给"此刻在哪+身体在做什么+时间标记"三选二的锚点，再展开。
    structure: 锚点句（地点/动作/时间）→ 主角对该场的目标 → 冲突推进
    ...
  show_dont_explain:
    display_name: 设定靠演
    principle: 规则通过动作/对峙/物件外显翻出来，禁作者旁白总结剧情与主题。
    ...
  one_name_at_a_time:
    display_name: 专名逐个登场
    principle: 一次只引入一个新专名，绑一张脸/一个动作/一个物件。
    ...

injection_policy:
  techniques_per_scene: 2        # 按章号轮换，避免同质化
  rotate_by_chapter: true
  position_hint: 用于本场"定场/转场"的写法选择，不是每句都要
  hard_rule: ""                  # 留空 = soft，绝不阻断

authorial_intrusion_guard:       # 反面教材，渲染成一句"忌"
  banned_moves:
    - {id: plot_summary, bad: "用一段总结剧情因果（X 之所以 Y，是因为…）", fix: 让因果由后续动作/对白翻出}
    - {id: theme_lecture, bad: "作者直接点题/下判断（他被当成了工具）", fix: 把判断留给读者，只给可见事实}
    - {id: name_dump, bad: "一段里塞 ≥3 个新专名/数字", fix: 一次一个，绑具体载体}
```

`render_scene_grounding_block(genre_terms, chapter_number)` → 紧凑 soft 段，措辞参照现有 `render_prose_craft_block`：明确「正文主体走 show-don't-tell，定场/转场用以下骨架，可选不强制」。

注入点（两处，与 prose_craft 并列）：
- `methodology_compiler._STAGE_SECTIONS[PROSE_SCENE]` 追加 `"scene_grounding_current"`；并在 `_sections_for_stage` 的 `PROSE_SCENE` 分支 `_append_block(...)`（紧邻 `prose_craft_techniques`，`methodology_compiler.py:320-333`）。**可选**也加入 `OUTLINE_CHAPTER`，让章纲阶段就规划"每场定场锚点"。
- `integrator.build_writer_quality_levers_block` 追加（覆盖 Mode B 对话式写作）。

**关键**：把 writer.md gate #8 的反信息倒斗规则**从 `first_chapter` 分支提取为全章节适用的 soft 提示**（普通章软提示、首章仍硬 gate）。

### Layer 2 — 确定性检测 + 判官（让镜头化变成可度量、能咬住）

在 `quality_levers/detectors.py` 新增三个纯函数检测器（沿用 deterministic / 无 LLM 风格）：

```python
@dataclass(frozen=True)
class AuthorialIntrusionResult:   # 病灶 A
    passed: bool
    intrusion_sentences: tuple[str, ...]   # 命中"之所以…是因为/这意味着/他被当成了"等议论句式
    density_per_kchars: float

@dataclass(frozen=True)
class SceneAnchorResult:           # 病灶 B
    passed: bool
    unanchored_transitions: int    # 段落级转场缺少 地点/动作/时间 锚点
    paragraphs_checked: int

@dataclass(frozen=True)
class ProperNounFloodResult:       # 病灶 C
    passed: bool
    max_new_names_per_paragraph: int
    number_tokens: int
```

- `detect_authorial_intrusion(text)`：正则匹配议论性连接（之所以/是因为/这意味着/换句话说/说到底/本质上/他/她+被当成）落在**叙述层**（剥离引号对白，复用 `audit_emotion_labels` 的剥离逻辑），密度超阈值 → finding。
- `detect_unanchored_transitions(text)`：按空行分段（已存 `_PARAGRAPH_SPLIT_RE`），检测"场景切换信号"（时间/地点跳变）但段首 N 字内无锚点词（在/站/坐/蹲/推开/走进/灯/窗/点/分 等具体落地词）。
- `detect_proper_noun_flood(text, chapter_number)`：统计单段新增专名（粗略：2-4 字、非常用词、CJK 人名/机构模式）与数字 token；前 N 章阈值更严。
- 接入 `audit_chapter@628`（扩 `QuantitativeChapterAudit`）→ 让判官/dashboard 拿到。
- 判官侧：把三项以 **soft finding / 建议重写** 形式喂 `build_critic_quality_levers_block` 或 reviews 评分；**正文普通章默认 soft（计分不硬阻断）**，避免 [[story-design-kernel-gate-blocker]] 式级联硬卡。仅首章/卷首维持较硬阈值。

阈值全部进 `config/scene_grounding.yaml` 的 `detector_thresholds:`，可调、可关。

### Layer 3 — 上游物料根治（消除"对着抽象机制写作文"）

根因是 bible/material 全 `§default-*` 抽象。方向（需进一步定位 `material_library` / `planner` 落点）：

1. **诊断当前选择路径**：确认 `oracle-pilot-dianshen` 是否因都市异能题材无具体 scene_bank 而落回 `default-*`（对照 [[genre-scene-bank-material-library]]：玄幻已落地具体 scene_templates）。
2. **为都市异能补具体 scene_templates / plot_patterns / device_templates**（接入点 = `material_library` 的 `scene_templates`，非 prompt_pack）：把"状态账装置"具体化为"债务账本/掌心黑纹/主控屏债务清算"等本题材器物。
3. **物料具体度闸门**：bible 生成后，若关键物料仍是 `§default-*` 占比过高 → 标记"物料抽象，需具体化"，阻止直接进入正文（soft 提示而非硬卡，初期）。
   - 已存在 `material_density.py`，先读它判断是否复用。

> Layer 3 改动面最大、收益最根本，但需要先把 `material_library` 选择/兜底链路读透，单独排期。

---

## 5. 防回归红线（务必遵守）

- **soft 优先**：Layer 1 永不进 hard_gate；Layer 2 正文普通章默认计分 soft。理由见 [[story-design-kernel-gate-blocker]]（strict-prewrite 闸门链对 fallback 级联硬卡是 2026-05 质量崩塌成因之一）。
- **绝不模板直拼**：`micro_examples` 只做"技法骨架示范"，跨题材合成，**严禁原句进正文**（见 [[title-generation-template-override-regression]]）。
- **分题材、防紫化**：`genre_fit` 把都市/科幻路由到结构化技法而非古风意象；`authorial_intrusion_guard` 同时防"辞藻堆砌"反向回潮。
- **不碰字数契约**：与 1800–2600–3500 中文段位无关，不引入"≥5000 字"旧坑（见 [[methodology-pipeline-quality-regression]]）。
- **题材中立**：检测器用通用句式/结构信号，不硬编码探案或某一题材词（延续近期"判官层题材中立化"，见 [[judge-genre-neutralization]]）。

---

## 6. 验证计划

1. **单测**（TDD，先写）：每个新检测器 + render_block 覆盖正/负例；正例直接用《借运成神》"最终"版段落（应 pass），负例用 chapter-005 的 A/B/C 段（应命中）。目标 ≥ 80% 覆盖。
2. **离线渲染对照**：对 `oracle-pilot-dianshen` ch1/ch5，打印接入前后 PROSE_SCENE 编译块 diff，确认 `scene_grounding` 段正确注入、token 预算不挤掉关键块。
3. **实机重生成 A/B**（需大模型，单独确认成本）：用同一 premise 重写 ch1 + ch5，跑现有 judge（`llm_quality_judge`）+ 三检测器，对比"作者旁白密度 / 无锚转场数 / 专名洪流"前后值。复用 `scripts/verify_prose_craft_ab2.py` 的 A/B 骨架。
4. **回归**：确认未触发既有 prewrite 闸门级联（[[story-design-kernel-gate-blocker]]）。

---

## 7. 落地顺序与工作量（估）

| 阶段 | 内容 | 风险 | 估时 |
|------|------|------|------|
| P1 | Layer 1：config + loader + render_block + 两处注入 + 单测 | 低（照搬 prose_craft） | 0.5–1 天 |
| P2 | Layer 1 离线渲染对照 + 实机 A/B 一次 | 中（需模型成本） | 0.5 天 |
| P3 | Layer 2：三检测器 + audit_chapter 扩展 + 判官 soft 接入 + 单测 | 中 | 1–1.5 天 |
| P4 | Layer 3：material_library 都市异能具体化 + 物料具体度软闸门 | 高（链路需先读透） | 单独排期 |

建议：**先 P1+P2 验证镜头化杠杆是否显著降低"作文感"**，用数据决定是否继续 P3/P4。

---

## 8. 待确认 / 开放问题

- Layer 3 的 `material_library` 选择链路与 `default-*` 兜底触发条件（需读 `material_library.py` / `material_library_reference.py` / `planner.py`）。
- 检测器阈值初值（已标定，见 §9；后续可随更多数据收紧）。
- 是否同步更新 `docs/ai-context.md`（单一事实源）与 writer.md，使 Mode B 对话写作同享规则。

---

## 9. 已实现 + 实验结果（2026-06-06）

### 9.1 已落地（Layer 1 + 2）

| 文件 | 内容 |
|------|------|
| `config/scene_grounding.yaml` | 6 条镜头化技法骨架 + `authorial_intrusion_guard` + 题材路由 + `detector_thresholds` |
| `src/bestseller/services/quality_levers/scene_grounding.py` | loader + `render_scene_grounding_block` + 3 检测器（A 作者旁白 / B 锚定覆盖 / C 专名洪流）+ `audit_scene_grounding` |
| `quality_levers/__init__.py` | 导出 |
| `methodology_compiler.py` | `PROSE_SCENE` 段注入 `scene_grounding_current`（紧邻 prose_craft），题材路由、按章轮换、英文/REVIEW 不注入 |
| `integrator.py` | Mode B 写手块追加（默认题材） |
| `tests/unit/test_scene_grounding.py` | 22 测试全绿 |

检测器标定（6 个真实样本）：阈值 `authorial_intrusion_per_kchars=3.5` 干净分离最糟的 `chapter-005`
（A=5.92，判 FAIL）与所有镜头化 GOOD 版（A≈0，PASS）。**关键诚实结论：确定性检测只能可靠抓
A（作者旁白）这一端；B 覆盖率区分力弱（vivid 比喻被误判）、C 数字密度受题材混淆——故聚合判定
= A∧B，C 仅作诊断。** "作文感"更弥散的部分需 LLM 盲评。

### 9.2 横向 A/B 实验

- 生成：写手 = `MiniMax-M2.7-highspeed`（生产写手）。
- 判官：**DeepSeek**（独立模型，去自评偏差 + JSON 可靠），**匿名打乱 + 绝对 rubric**（camera 1-10 / essay_feel）。
  ⚠ 最初用「成对+swapped 去偏」判官有**致命位置偏差**（数学上必然 50/50），已弃用；权威结果用
  `scripts/_sg_rejudge.py` 的绝对盲评 + 模型无关的确定性 A。
- 脚本：`scripts/verify_scene_grounding_ab.py`（EXP1 生成）、`verify_material_concreteness_ab.py`（EXP2 生成）、
  `scripts/_sg_rejudge.py`（权威盲评）。

**EXP1 · 写手 prompt 块（具体 brief，块 关 vs 开），N=5×3 题材：**

| 指标 | baseline | treatment | Δ |
|------|---------:|----------:|----:|
| camera 1-10（↑） | 8.00 | 8.07 | +0.07 |
| A 旁白/k（↓） | 0.21 | 0.27 | +0.07 |
| B 锚定（↑） | 0.896 | 0.915 | +0.02 |

→ **块在具体 brief 下增益边际、无害**（更短/更弱的 brief 上更早一轮曾测得 camera +1.15）。

**EXP2 · 物料具体度（都加块，抽象 vs 具体物料），N=5×3 题材：**

| 指标 | abstract | concrete | Δ |
|------|---------:|---------:|----:|
| camera 1-10（↑） | 7.80 | **8.33** | **+0.53** |
| A 旁白/k（↓） | 0.66 | **0.05** | **−0.61（~14×）** |
| B 锚定（↑） | 0.842 | **0.917** | +0.075 |

→ **具体物料三题材全面胜出（悬疑 7.8→8.4 / 职场 8.0→8.6 / 都市异能 7.6→8.0），且确定性 A 与 LLM 判官一致。**

### 9.3 结论与修正后的优先级

1. **主杠杆是物料具体度（Layer 3），不是写手 prompt（Layer 1）。** 试点《借运成神》的"作文感"主因 =
   bible/material 是抽象 `§default-*` 机制语言，写手在"对着抽象机制写读后感"。
2. **Layer 1+2 保留**：廉价、无害、对弱 prompt 有小增益，且 Layer 2 检测器是 Layer 3 的度量仪表
   （已证明能量化 abstract→concrete 差异）。
3. **下一步（拿到最优结果的关键）= Layer 3**：为都市异能补具体 `material_library` 物料 +
   物料具体度软闸门（bible 生成后若 `§default-*` 占比过高则提示具体化）。先读透
   `material_library*` / `planner` 的物料选择与兜底链路再动手。

---

## 10. Layer 3 已实现 + 试点验证（2026-06-06 续）

### 10.1 已落地（Layer 3）

| 文件 | 内容 |
|------|------|
| `config/material_concreteness.yaml` | `concretization_directive`（写手侧修复指令）+ `abstract_markers`（25 个机制词）+ `default_slug_marker` + 阈值 |
| `src/bestseller/services/quality_levers/material_concreteness.py` | `render_concretization_directive`（注入 PROSE_SCENE）+ `detect_material_abstractness`（作用于 bible/物料）+ `MaterialAbstractnessResult` |
| `methodology_compiler.py` | `PROSE_SCENE` 段注入 `material_concretization_current`（紧邻 scene_grounding 之前），英文/REVIEW 不注入 |
| `tests/unit/test_material_concreteness.py` | 8 测试全绿（合计 scene_grounding 22 + 物料 8 = **30 测试**） |

**检测器在真实试点 bible 上的铁证**：`detect_material_abstractness` 扫 `oracle-pilot-dianshen` 的
story-bible → **60/60 (100%) 物料引用全是 `§default-*`**，机制词密度 24.86/k（阈值 8.0）→ 判 FAIL。
这就是"作文感"的根因实锤。具体对照样本 PASS。

> 设计取舍：检测器作用于【物料/bible】而非正文——写手会把机制术语释义成模糊散文，正文里术语命中≈0，
> 术语只在 bible 里成片出现（已用 EXP2 的 30 篇草稿验证：机制词在正文 ≈0，不可作正文信号；正文侧
> 信号仍是 scene_grounding 的 A 旁白）。

### 10.2 试点验证（OLD 旧管线 vs NEW 全栈，真实问题书物料）

`scripts/verify_pilot_fullstack_ab.py`：用《借运成神》**真实具体 premise + 真实抽象 §default 物料块**作输入
（最贴近真实管线失败场景），单书 N=6，DeepSeek 绝对盲评 + 确定性 A。

| 指标 | OLD（无杠杆） | NEW（scene_grounding+具体化） | Δ |
|------|------------:|------:|----:|
| camera 1-10（↑） | 8.17 | 8.33 | +0.17 |
| **A 作者旁白/k（↓）** | **0.682** | **0.000** | **−0.682（清零）** |
| B 锚定（↑） | 0.925 | 0.936 | +0.012 |

- OLD 旁白分布 `[0,0,0,0.78,0.84,2.47]`（6 篇 3 篇有旁白）→ **NEW `[0,0,0,0,0,0]` 全清零**。
- camera 分布 OLD `[8,8,8,8,8,9]` → NEW `[8,8,8,8,9,9]`。
- 模型无关确定性 A 与 LLM 判官两路一致：**新全栈在真实问题书物料上把"作者旁白"失败模式可靠压到 0。**

### 10.3 最终结论

1. **功能全部完成、端到端验证通过**：Layer 1（镜头化）+ Layer 2（检测器）+ Layer 3（物料具体化指令 +
   抽象度检测）全部实现、接入 PROSE_SCENE、30 单测全绿、在真实问题书物料上证明可消除作者旁白。
2. **证据层级**（强→弱）：① bible 检测 60/60 全抽象（根因实锤）；② EXP2 物料具体度 14× 旁白差（决定性杠杆）；
   ③ 忠实试点 OLD→NEW 旁白清零（全栈有效）；④ fresh-brief 纯 prompt A/B 边际（MiniMax 在干净 brief 上本就好，符合预期）。
3. **仍待集成（已定位、低风险，建议下一步）**：
   - 把 `detect_material_abstractness` 接入 `bible_gate.default_validators()`（新增一个 severity=low 的
     `MaterialConcretenessCheck`，**软提示不硬卡**），让管线在规划期就对 100% 抽象物料行动；
   - 终极根治：让 `material_forge/*` 为都市异能等题材生成具体物料，替代 `§default-*` 兜底（最大、单独排期）。
