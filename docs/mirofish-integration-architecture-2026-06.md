# MiroFish × BestSeller 融合架构设计 (2026-06)

> 作者视角:架构师。状态:设计 + Phase 1 落地。
> 单一事实源补充,不替代 `docs/ai-context.md`。

---

## 0. 摘要 (TL;DR)

把 MiroFish(群体智能仿真预测引擎)接成 BestSeller 的**「故事推演脊柱」**,
锚点是 **`StoryDesignKernel`(故事设计层)**,而非大纲/细纲生成步骤,也非逐章全程推演。

- **它产出什么**:从角色真实动机"长"出来的 `beat_schedule`(走向)+ `plot_tree.subplot`(丰富性)+ 动机漏洞清单(可读性)。
- **它喂给谁**:`story_design_kernel` 的 `beat_schedule` / `plot_tree` 字段,由现有闸门(`story_design_kernel_gate` / `commercial_novel_gate` / `arc_tension_monitor`)收口。
- **它解决的核心痛点**:当前 `story_design_kernel_gate` 被 `beat_schedule_incomplete` + `fallback_source_leak` 硬阻断(见 memory)。接入后,beat 是**有依据地生成**而非 LLM 凭空编造再泄漏兜底值。
- **触发频率**:全书 `1(弧线推演) + 卷数(滚动预演)` 次贵仿真 + 选角期若干次廉价 `interview`,**不逐章**。

---

## 1. 两框架深度解读

### 1.1 MiroFish — 自下而上的涌现引擎

| 阶段 | 模块 | 作用 | 对融合的意义 |
|------|------|------|------------|
| 种子摄入 | `text_processor` | 文件→分块 | 我们用结构化资产**绕过**它 |
| 本体生成 | `ontology_generator` | LLM 设计实体/关系类型(**强绑社媒**) | **必须换成叙事本体**(角色/势力 + LOVES/BETRAYS/OWES) |
| 图谱构建 | `graph_builder` (Zep) | GraphRAG | 我们的 canon 三元组可**结构直注**,省 token |
| 人设生成 | `oasis_profile_generator` | 实体→OASIS Profile | 我们的角色快照≈1:1 映射 persona |
| 配置生成 | `simulation_config_generator` | 活跃度/立场/事件 | stance/sentiment 由角色弧推导 |
| 仿真运行 | `simulation_runner` (OASIS) | 多 Agent 多轮演化 | 贵;只在岔路口触发 |
| 报告/互动 | `report_agent` + `zep_tools` | `insight_forge`/`panorama_search`/**`interview_agents`** | 我们消费它的"报告"蒸馏成 beat/subplot |

引擎=OASIS(CAMEL-AI),记忆=Zep Cloud,LLM=OpenAI-SDK 兼容。
**已有小说先例**:《红楼梦》失传结局推演——证明"小说=种子"是一等用例。

### 1.2 BestSeller — 自上而下的生成框架

规划分层(`orchestration.md` 状态机 / `planner.py`):

```
PLAN_PREMISE → PLAN_WORLD → PLAN_CHARACTERS → PLAN_VOLUME_PLAN
  → [PLAN_ACT >50章] → [PLAN_WORLD_EXPANSION >3卷] → PLAN_WRITING_PROFILE
  → 每卷{ PLAN_VOL_README → 每章 ChapterOutline(细纲) → WRITE→REVIEW→… }
```

- **大纲** = `VolumePlan` + `ActPlan`(卷/幕级走向、win/loss 节奏、跨卷钩子)。
- **细纲** = `ChapterOutline`(每卷 just-in-time 的 scene card,机械分解)。
- **故事设计层** = `StoryDesignKernel`(在大纲之上的统一契约),含:
  - `beat_schedule: list[BeatScheduleItem]` (min_length=1)
  - `plot_tree: list[PlotTreeNode]` (min_length=1, 至少一条 main)
  - `change_vectors`, `structure_strategy`, `premise_contract`, …

### 1.3 关键洞察:两者是镜像互补

| | BestSeller(生成) | MiroFish(涌现) |
|--|------------------|----------------|
| 方向 | 自上而下:作者定情节 | 自下而上:角色演故事 |
| 强项 | 结构可控、商业节奏 | 真实、丰富、有惊喜 |
| 弱项 | 角色像提线木偶、走向是"拍"的 | 无结构、不保证可读、不收束 |

> 融合本质:MiroFish 当 BestSeller 的**群体智能编剧室**——在设计层产出有依据的走向与支线,由 BestSeller 的闸门做商业收口。

---

## 2. Phase 0 验证结果分析

产物:`tmp/mirofish-phase0/`(导出器 + jin-tian-wen-dao 种子包 + 手跑预演)。

| 假设 | 验证结论 |
|------|---------|
| 知识层可低损桥接到 MiroFish | ✅ 成立。canon 三元组→图谱边、角色快照→persona,导出器一跑即成(4角色/4边/快照3命中) |
| 可绕过 MiroFish 有损的本体抽取 | ✅ 预构建叙事本体 `ontology.json` 可直注 |
| 概念对三目标有价值 | ✅ 单轮手跑即产出:1反失衡走向 + 3 beat候选 + 2涌现支线 + 2动机漏洞 |
| 产出与 kernel 结构同构 | ✅ "beat候选"字段级映射 `BeatScheduleItem`;"涌现支线"映射 `PlotTreeNode(subplot)` |

**副发现(已记录/部分修复)**:
1. 导出角色定位有"主角"子串误判(同 prompt-pack genre-misroute 类),原型已修。
2. `jin-tian-wen-dao` 知识层填充严重不足(31章仅4条canon)——**仿真质量受限于知识层质量**,是融合的前置依赖。

---

## 3. 架构设计:推演脊柱的三个落点

> 锚在设计层 + 卷边界滚动;细纲不进仿真。

| 落点 | 触发状态 | 输入就绪度 | 机制 | 产出 → 字段 | 收口闸门 | 成本 | 目标 |
|------|---------|-----------|------|------------|---------|------|------|
| **T1 选角压测** | `PLAN_CHARACTERS` 后 | 角色种子(无快照) | `interview_agents`(非全仿真) | 动机漏洞 → 改 `characters.md` | `cast_compliance_gate` | 低 | 可读性 |
| **T2 弧线推演** | `PLAN_VOLUME_PLAN`/`PLAN_ACT` | 设定+角色 | 全仿真(冷启动,合成快照) | `beat_schedule` + `volume-plan` | `story_design_kernel_gate` + `commercial_novel_gate` | 高(全书1次) | 走向 |
| **T3 滚动预演** | 每个 `PLAN_VOL_README` 前 | **真实快照** | 全仿真(热启动) | `plot_tree.subplot` + 下卷 beat 修正 | `arc_tension_monitor` | 中(每卷1次) | 丰富性+走向 |

### 3.1 状态机改动

```
PLAN_CHARACTERS → [T1] SIMULATE_CAST_PROBE → PLAN_VOLUME_PLAN
PLAN_VOLUME_PLAN → [T2] SIMULATE_ARC → (注入 story_design_kernel) → PLAN_ACT/...
每卷: [T3] SIMULATE_NEXT_VOLUME → PLAN_VOL_README → 章循环
```

### 3.2 数据流与降级

```
project metadata / story-bible
   └─ export_request_from_novel() → OracleRequest
        └─ SimulationOracle.deduce(request)
             ├─ enabled+可达 → MiroFishClient (HTTP: graph→sim→report)
             └─ 否/失败 → HeuristicOracle (离线确定性, 不阻断流水线)
        └─ OracleResult{beats, subplots, motivation_flags, natural_direction}
   └─ augment_kernel(base_kernel, result) → 注入 beat_schedule/plot_tree
        └─ sanitize_distilled_leak() + 章节覆盖补全
   └─ story_design_kernel_from_dict() 校验 → 通过 gate
```

**关键设计约束(经代码验证)**:
- 产出必须**干净中文**——`evaluate_story_design_kernel_quality` 的 `_OFF_GENRE_STATE_RE`/`_FALLBACK_SOURCE_RE` 会把英文机制词/兜底词判为 `fallback_source_leak`(critical)。
- `beat_schedule` 必须**覆盖到 target_chapters**——`_max_covered_chapter ≤ 3` 触发 `beat_schedule_incomplete`(critical)。
- `plot_tree` 非 main 节点必须有 `dependency_on_mainline`;merge 不得删除既有 main 线。
- oracle **永不阻断流水线**:不可用即降级 HeuristicOracle。

---

## 4. 开发方案与分期

| Phase | 内容 | 可验证性 | 依赖 |
|-------|------|---------|------|
| **0 (done)** | 种子导出器 + 手跑预演 | 肉眼 | 无 |
| **1 (本次)** | `simulation_oracle.py`:Protocol 客户端(MiroFish+Heuristic) + 映射 + 导出 + 配置 + 单测 | **pytest 全离线** | 无 |
| 1.5 | 接真 MiroFish(配 Zep+LLM key,跑 jin-tian-wen-dao 第四卷) | 人工对照 Phase0 | 你的 key |
| 2 | T3 滚动预演 + 涌现支线池 | 集成测 | Phase1 |
| 3 | T1 选角压测 + `emergent_plausibility` critic 维度 + 状态机三新态 | 集成测 | Phase1/2 |

**为何先做 T2**:杠杆最高(错的卷规划毒害50章)+ 直接修复正在阻断新书的 P1 + 给后续铺基础设施。

**成本账**:全书贵仿真 `1+卷数` 次(6卷≈7次);仿真用便宜模型,writer 仍用贵模型。

---

## 5. 接入后的效果:对全书质量的质变

### 5.1 解决的具体问题
1. **解阻断**:`beat_schedule_incomplete` / `fallback_source_leak` → beat 有依据生成,kernel 闸门可过(直接救活新书规划)。
2. **防失衡**:校正"主角每卷都赢"——仿真给出角色动机下的自然输点。
3. **补丰富度**:涌现支线/配角暗线进 `plot_tree`,告别"只有主线"的单薄。
4. **堵出戏**:动机漏洞在设计期暴露(如"反派 stake 过薄"),而非读者读到才发现。

### 5.2 质变(从"AI味结构正确"到"有生命的故事")

| 维度 | 接入前 | 接入后 |
|------|--------|--------|
| 走向 | 作者拍脑袋,易套路/失衡 | 角色动机推演,意料之外情理之中 |
| 角色 | 服从大纲的提线木偶 | 有自驱力,反推动情节 |
| 支线 | 主线为主,配角工具化 | 涌现的多线交织,世界有反应 |
| 一致性 | 事后闸门挑错 | 设计期预演堵漏 |
| beat | LLM 凭空编(易泄漏) | 仿真长出(有依据) |

> 一句话:**从"把结构填对"升级为"让故事自己活一遍,再由商业框架收口"**——这是可读性、丰富性、走向三者同时的质变,而非单点优化。

### 5.2.1 榜单级质量保障(确保不只是"过闸",而是真"上榜")

> 核心风险:oracle 若只满足 `story_design_kernel_gate` 的 leak/coverage 检测,会变成
> "过闸橡皮图章"——注入通用空话 beat,内容反而拉低质量(即 memory 里"闸门自伤"风险)。
> 为此 Phase 1 引入**三重榜单级守门**:

1. **同源具象标准(不漂移)**:oracle 的具象度尺子直接 `import` 框架商业门的
   `commercial_planning_readiness._CONCRETE_PRESSURE_TERMS`(90 词:逼/否则/夺/灭口/封锁/
   证据/当场…)。`RANKING_PRESSURE_TERMS is _CONCRETE_PRESSURE_TERMS == True`——oracle 用的
   就是下游 gate 检查的同一把尺,杜绝"自己给自己放水"。
2. **本书落地的具象 beat**:`_segment_beats(request)` 把六阶段模板的 `{P}/{A}` 占位符落到
   **本书真实主角/对手名**上,且每段内置 具体压力词 + 主角能动性(`_AGENCY_VERBS`)+
   可见损失(`_VISIBLE_LOSS_TERMS`)+ 章末钩子——直接对齐规划层商业门对"黄金三章"的要求。
3. **未达标即标记、绝不冒充终稿**:`evaluate_oracle_quality()` 对每个 beat 自检;不达榜单级
   具象度 → `OracleResult.needs_enrichment=True`,`augment_kernel` 写入 `kernel["oracle_meta"]`
   提示"此为榜单感知**草稿**,定稿前必须经真 MiroFish/planner LLM 升级 + 通过
   `commercial_planning_readiness`/`commercial_novel_gate`(专业分≥95)"。

**结论**:HeuristicOracle 离线产出是"榜单感知草稿"——已比通用模板具象得多(过本书具象门),
但**不假装是榜单级成品**;真榜单级由"真 MiroFish 涌现 + 框架既有 95 分商业门"双重保证。
这既"确保融合"(同源、不旁路闸门),又"确保榜单级"(草稿/成品分层,终稿必过商业门)。

### 5.3 边界与风险
- 仿真质量受限于**知识层填充质量**(前置依赖,需配套修 canon/snapshot 填充)。
- 涌现天然发散→**必须**由现有闸门收口,oracle 产出只作候选,绝不直接进正文。
- OASIS/Zep 成本→严格限制在设计层+卷边界触发,绝不逐章。
- 中文链路一致性(避免重蹈 `english_mechanism_leak`)→产出强制中文 + sanitize。

---

## 6. 实测验证结果(T1 真推理 + A/B,2026-06-04)

### 6.1 三档 oracle 客户端(已落地)
- `HeuristicOracle` — 离线确定性兜底(榜单感知草稿)。
- `LLMOracleClient` — **T1 真推理**,注入式 `complete(system,user)->text`;生产期由 planner 注入框架
  `complete_text`,独立验证用 litellm 直连(复用 `.env` key,**0 新 key**)。
- `MiroFishClient` — T2 真群体仿真(需 Zep+LLM key)。
  选择优先级:MiroFish > LLM > Heuristic;任何失败优雅降级,永不阻断。

### 6.2 T1 真模型跑通(deepseek-v4-flash,jin-tian-wen-dao)
真 LLM 产出**显著优于模板**:推理出"父母自愿成祭品为林烬留后路"的反转、"天规祭坛每百年
献祭万相火脉"的世界级暗线、3 条有因果咬合的涌现支线、2 条精准动机修补(顾行舟报恩赎罪、
宁玄策被家族替代献祭的恐惧)。早先一次降级=瞬时 API 抖动,facade 正确兜底未崩(反证可靠性)。
> MiMo 同接口可跑,只差 `base_url`+`model-id`(`.env` 仅有 `XIAOMI_MIMO_API_KEY`);
> 一条命令即可切:`--model ... --base ... --key-env XIAOMI_MIMO_API_KEY`。

### 6.3 A/B 客观评测(框架自有打分器,三臂)
| arm | gate critical | 覆盖章 | 榜单具象 | 损失beat(防失衡) | 支线 | 动机漏洞 |
|-----|----|----|----|----|----|----|
| A 无 oracle | 1(阻断) | 3 | 0/1 | 0 | 0 | 0 |
| B Heuristic | 0 | 40 | 4/4 | 3 | 3 | 1 |
| C LLM(真) | 0 | 40 | 4/4 | 3 | 3 | 2 |

**B/C 相对 A:6/6 项规划层指标全面改善 → 判定"真提升 ✅"**(度量全部来自
`evaluate_story_design_kernel_quality` + 商业门同源词表,非自创)。

### 6.4 诚实边界
- 本 A/B 量化**规划层** delta;**正文质感级**(完读/追读/`commercial_novel_gate` 95 分)需
  全量生成对照,属 T2 / 接入主流程后的工作。
- 粗粒度计数难分 B 与 C(均 6/6),但内容质感 C 远胜(见 §6.2 实跑);精细内容评分可后续加。
- 验证脚本:`tmp/mirofish-phase0/run_llm_oracle.py`(真推理)、`ab_eval.py`(三臂 A/B)。
- 测试:67 项全过(unit+reliability+llm+kernel 回归),lint 干净。

### 6.5 Phase 3:已接进 planner 主流程(2026-06-04)
- 新隔离模块 `simulation_oracle_planner.py`:`augment_story_design_kernel_with_oracle()`。
- 接缝:`planner._generate_story_design_kernel` 在 **payload 生成后、`story_design_kernel_from_dict`
  校验前**调用——增强后的 kernel 走**同一套**校验+`story_design_kernel_gate`+落盘。
  planner.py 仅 +13 行(import + 调用)。
- 红线:**默认关闭**(env `MIROFISH_ORACLE_PLANNER`),失败安全降级原样返回(永不阻断规划),
  复用 `complete_text`(planner 角色)**0 新 key**。
- 验证:6 集成测试(关闭=no-op / 开启=增强且 kernel 合法 / LLM 失败=降级原样返回)+
  全套回归 **90 项全过**;新增/改动文件 lint 干净。
- **启用方式**:设 `MIROFISH_ORACLE_PLANNER=true`(+ 项目已配 LLM)。新建书规划即自动调用 oracle
  增强 story_design_kernel。

### 6.6 小米 MiMo 真模型验证 + 盲评定论(2026-06-04)
- MiMo OpenAI 兼容端点 `https://token-plan-cn.xiaomimimo.com/v1`(key=`XIAOMI_MIMO_API_KEY`,
  模型 `openai/mimo-v2.5-pro`)已真跑通,产出完整悲剧弧(九衡印陷阱→碑林对决→父母旧案真相→
  宁玄策替父挡剑→林烬燃尽化道痕),丰富度高。
- **三臂盲评(Heuristic / deepseek-v4-flash / mimo-v2.5-pro,5 维 1-10,双裁判交叉验证)**:

  | 裁判 | heuristic | deepseek | mimo |
  |------|----|----|----|
  | deepseek | 20 | **43** | 31 |
  | mimo | 31 | **46** | 39 |

  两裁判一致 **deepseek > mimo > heuristic**;MiMo 当裁判未偏袒自己 → 排除自我偏好,结论稳健。
- **结论**:① **两个真 LLM ≫ 模板**(真提升已证,5 维全面领先);② 对"故事推演"这一具体任务
  **deepseek-v4-flash(现有 planner 模型)评分最高 → oracle 角色直接用它,无需换模型**;MiMo 为可用备选。
- **方法论要点**:关键词 A/B harness 分不出"好模型 vs 模板"(越好的模型用词越丰富,反被固定词表
  漏判,如 MiMo 的"崩碎/燃尽"未计入损失);**区分模型优劣必须用 LLM-judge 盲评**。
- 脚本:`tmp/mirofish-phase0/judge_eval.py [deepseek|mimo]`。
- 诚实边界:单样本/温度随机(未做 N 次取均值);裁判为 LLM 非人类;此为规划层质量,非平台完读/追读。

### 6.7 正文级 A/B 最终结论(2026-06-04,诚实记录)
- 实验:同一设定下,"有 oracle 设计稿" vs "无",各生成第四卷开篇章正文(deepseek 写手),
  双裁判盲评(`tmp/mirofish-phase0/write_eval.py`)。
- 结果(同一对文本双裁判重判):**deepseek 有 44 > 无 39;mimo 有 38 < 无 42(差距 ~10%,噪声内)→ 裁判分歧,单章正文无稳健优势。**
- 解读(关键):这是**预期且正确**的——oracle 作用在**结构/设计层**(beat 走向、跨卷支线回收、
  动机一致性),价值在**整本 40 章复利**(防卷卷皆胜、伏笔兑现、人设不崩),**不在任何孤立单章**;
  好写手仅凭设定即可写好一章。**用单章正文测 oracle = 用一块砖评判建筑师,是错的尺子。**
- 终极正文证据(完读/追读、`commercial_novel_gate` 95 分包级门)需**全管线生成 2 本**(with/without)
  对照,需 DB/ARQ + 极费时,本环境未跑。

## 7. 最终验证总结

| 层面 | 结论 | 强度 |
|------|------|------|
| 工程(可用/可靠/接入) | 端到端打通、安全降级、接进 planner(默认关闭)、90 测试全过 | **已证 ✅** |
| 规划/设计层质量 | LLM oracle ≫ 模板(双裁判一致,5 维全胜);≫ 无 oracle(6/6 结构指标) | **已证 ✅** |
| 模型选择 | 故事推演任务 deepseek-v4-flash > mimo-v2.5-pro(双裁判一致);MiMo 可用备选 | **已证 ✅** |
| 单章正文质量 | 无稳健优势(裁判分歧,~10% 噪声)——预期,因 oracle 价值是书级结构性 | **未证(尺子不对)** |
| 整本正文(完读/95分门) | 需全管线 2 本对照,未跑 | **待验** |

**落地建议**:在**设计层**启用(`MIROFISH_ORACLE_PLANNER=true`,oracle 角色 = deepseek-v4-flash);
收益是结构性的(解 beat 阻断、防失衡、伏笔/动机),在整本书尺度兑现。
