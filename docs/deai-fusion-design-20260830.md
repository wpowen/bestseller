# 去AI味外部 Skill 融合设计（2026-08-30）

> 任务：调研全网「去AI味」skill/仓库，把有效判据融合进本框架（简介+正文全路径），
> 用人类语料参考系自测验证，落地为生产能力。
> 调研原始材料：17 个仓库克隆 + 5 份深读报告（scratchpad/deai-research/analysis/）。

## 一、调研范围与结论摘要

**读过的仓库（17）**：blader/humanizer(38807★)、stop-slop(16566★)、Humanizer-zh(16284★)、
shuorenhua(1291★)、ai-flavor-remover(1138★)、academic-humanizer(1200★)、speak-human-tw(904★)、
De-AI-Prompt-Enhancer-Writer-Booster-SKILL(732★，用户指定)、qu-ai-wei(504★，用户指定)、
lieflat-less-ai-tone(482★)、slop-guard(162★)、zh-humanizer-literary、de-ai-tone、noai-flavor、
remove-ai-flavor-writing-skill、ximen-aimazi、oh-story-claudecode(6212★，只读去AI味模块)。

**总体格局**：
- 谱系上 Wikipedia《Signs of AI writing》→ blader/humanizer → Humanizer-zh → 各中文变体是主干；
  oh-story/ximen-aimazi 是唯一的小说向谱系（同源底本，算 1.5 票）；lieflat 是唯一有 283 万字
  对照语料真值的；slop-guard 是唯一可执行打分器；shuorenhua/speak-human-tw 评测工程最完整。
- **绝大多数仓库是 prompt 改写 skill（LLM 兼任检测与改写），无确定性检测器、无语料校准**。
  本框架的既有底盘（17 条章级轴 + 118 条模式 + deslop 闭环 + 94.7 万章人类语料校准）在
  「确定性检测 + 校准」维度上已超过全部调研对象；差距在**判据覆盖面**与**流程资产**。
- 与本框架互补的增量集中在四处：
  1. **小说桥段级判据**（oh-story/ximen 独有）：章末预告/盖章收尾、罐头反应镜头、穿越过渡段、
     金手指特效腔、设定说明书段、解释链密度、监控动作清单、系统面板公文腔；
  2. **语料实证判据**（lieflat）：段首零主语评论(4.4×)、提示性冒号(3.8×)/空转冒号(9.4×)、
     拟人化喻体(7.3×)、翻案腔(3.4×)、相邻句结构指纹(2.0×)；
  3. **简介/文案层判据**（de-ai-tone/remove-ai-flavor/多家）：冒号帽句、假互动结尾、
     金句壳、通用积极收尾——本框架 copy_flavor 仅 5 族，明显薄于正文层；
  4. **流程资产**：SF/SNF 成对回归（speak-human-tw）、反向检测器（删过头也报警，oh-story）、
     末句位置门控（slop-guard closing_aphorism）、防变体逃逸条款（De-AI 仓）、
     改写端禁改清单（lieflat 负结果）、每规则语料误报率标定纪律（oh-story）。

**必须记住的负结果**（lieflat 283 万字实测证伪的流行判据，禁止照抄观察派清单）：
- 句长 CV 无差异（0.87×）——「AI 句长均匀 50 倍」是切句缺陷假象；
- 正文设问人类是 AI 的 **17 倍**——删设问是反向操作；
- 比喻本身人类 2.4×、句内排比人类更多、动词名词化人类更多、正文「首先其次」无差异；
- 「」金句计数同值反结论（shuorenhua）；连词密度不能设全局线（SNF max > SF max）。

## 二、融合原则（本框架铁律 × 外部共识）

1. **词表只进检测器，prompt 只许类别+正例**（种词铁律；与 4 家共识「清单是举例不是边界」互证；
   De-AI 仓的变体逃逸补丁链是「逐词黑名单必然失效」的实证记录）。
2. **新检测器只挣重生和留痕**：全部新轴 advisory 起步，进 `_ADVISORY_STRUCTURAL` 封顶 +
   `DESLOP_DISCOURSE_CATEGORIES` 触发集，不发杀权。
3. **阈值必须过 .distillation_private 分位数标定**；准入规程照抄 lieflat：
   倍率 ≥2.0 收录、1.25-2.0 条件收录（人类侧稳定）、<1.25 不收；
   **采信频率前先抽 20 条命中人工看**；结构元素分母 = 同类元素总数。
4. **成对回归**：每条新轴带 SF（该报）+ SNF（不该报）测试样本。
5. **改写端护栏**：deslop 增加「禁改清单」（设问/比喻/句内排比/引号内对话/节奏性重复）与
   「假人味黑名单」（假坦白起手、硬造金句、表演不确定、罐头反应同族替换）——均为类别级。
6. **收尾类检测统一「位置门控+合取」**（只扫末段窗口，降误杀）。

## 三、候选判据清单（进校准台，数据说了算）

### A 组：正文 discourse 轴（数据驱动，进 patterns_zh.json）

| # | 轴 | 来源与证据 | 形态 |
|---|---|---|---|
| A1 | trailer_ending 章末预告腔（谁也没想到/才刚刚开始/拉开序幕/即将降临） | oh-story（qimao 1.3%/heiyan 6.6% AI侧 vs 章中段 0.005%） | 末段窗口正则 |
| A2 | trailer_summary 章末盖章腔（这一夜注定/这一切都结束了/新的人生才刚刚开始） | oh-story（0.005%-0.55% FP 标定） | 末段窗口正则 |
| A3 | stock_reaction 罐头反应镜头（指尖/指节/喉结/眼眶+轻轻/微微/攥紧/泛白；语气平静得像在念） | oh-story（qimao 5584 章误报 0.39-0.43%）+ speak-human-tw #20A | 密度正则 |
| A4 | voice_contrast 音量反差腔（声音不大/不高…却/但/偏） | oh-story（万疆 20 章 0 命中） | 正则计数 |
| A5 | negation_parade 否定排比（没有X，没有Y，）+ 跨段工整并列（不是A。也不是B。只是C。） | oh-story（语素级护栏）+ stop-slop negative listing | 正则计数 |
| A6 | reverse_not_is 反序对比（是真嗓子，不是修音修出来的） | oh-story（「X是」合成词前字排除集） | 正则计数 |
| A7 | comment_opening 段首零主语评论（段首「听起来/说白了/更重要的是」无回指） | lieflat（4.4×，全库区分力最强） | 段首正则 |
| A8 | reasoning_chain 解释链密度（他知道/这意味着/关键在于 四桶合取） | oh-story（≥18/千字四桶判据） | 密度正则 |
| A9 | micro_action_tic「了一下」尾巴密度 | oh-story（≥6/千字） | 密度正则 |
| A10 | action_list 监控动作清单（同段≥5 个动作动词） | oh-story + ximen「二修伪自然」 | 段级统计 |
| A11 | personified_negation 拟人化否定金句壳（血管不认这些词/市场不在乎/算法不讲情面） | De-AI 9.9 + stop-slop false agency | 正则计数 |
| A12 | transmigration_trope 穿越过渡段/金手指特效腔（缓缓睁开眼+陌生天花板/嗡+轰然+半透明面板+金色小字/原身原主密度） | ximen 文风F C2/C3/C11（全网唯一桥段级） | 正则计数 |
| A13 | worldbook_dump 设定说明书段（「在这个…的世界里」开段） | ximen C8 | 段首正则 |
| A14 | adjacent_sig 相邻句结构指纹（逗号数+冒号+括号+长度档 连续同构） | lieflat（2.0×，signature 四元组） | 代码轴 |
| A15 | quote_emphasis 叙述层短词引号强调 | oh-story（万疆 2/20 章命中→advisory）+ de-ai-tone 引号三病 | 代码轴（对白豁免需特判） |

### B 组：简介/文案层（进 copy_flavor.py）

| # | 轴 | 来源 |
|---|---|---|
| B1 | colon_hat 冒号帽句/擂鼓宣告（核心是：/关键在于：/答案很简单：） | de-ai-tone + lieflat 提示性冒号(3.8×) |
| B2 | fake_interaction 假互动结尾（你觉得呢？/是不是很有启发？） | remove-ai-flavor（blocker 级） |
| B3 | idealized_persona 拟人化喻体（像一个贴心的管家/24小时在线的导师） | lieflat（7.3×，带褒义修饰 12.6×） |
| B4 | uplift_closer 通用积极收尾（未来可期/让我们拭目以待/一切才刚刚开始） | 四家共识 + slop-guard 末句门控 |
| B5 | contrast_shell 翻案腔密度（不是A而是B 及跨句变体 ≥2 处） | 五家全票第一指纹 + lieflat 3.4× |

### C 组：deslop / 管线资产（代码改动）

| # | 内容 | 来源 |
|---|---|---|
| C1 | deslop 禁改清单（类别级）：设问、比喻本身、句内排比、引号内对话、节奏性重复句、限定词让步 | lieflat 负结果 + speak-human-tw 误杀放行表 |
| C2 | deslop 假人味黑名单（类别级）：假坦白起手、硬造金句、表演不确定、罐头反应同族替换、油腻倒装口语 | speak-human-tw #17-20 + ximen 模式 10 |
| C3 | 电报体反向守卫：keep-better 增加「过度精炼」病态轴（短段占比+虚词密度地板），deslop 产物删过头不采纳 | oh-story overcompressed/low-connective 反向检测器 |
| C4 | debt_metaphor_leak 死链清理（触发集/引文豁免集/pipelines 硬 block 分支） | 基线盘点 §6 |

## 四、落点架构

```
正文：detector.py + patterns_zh.json（A 组）→ ai_flavor_gate →（advisory 触发）deslop_revise
简介：copy_flavor.py（B 组）→ conception/concept_lab 既有消费点
改写：deslop_revise.py（C1/C2 自查表扩展 + C3 keep-better 病态轴）
引擎小改：_detect_discourse 支持 tail_chars 字段（末段窗口门控，A1/A2 用）
```

## 四·五、校准准入判决（2026-08-30 实测，1135 出版章 vs 60 在架 vs 245 淘汰稿）

**收录 7 轴**（全部 advisory + deslop 触发集，阈值=人类分位数）：

| 轴 | AI/人类倍率 | 人类基线 | 落地阈值 |
|---|---|---|---|
| sentence_signature_run | 人类 **0/1135**（独有指纹） | 0 | 1 处即报（代码轴） |
| reverse_contrast（收紧版：排除「而不是」「不是吗」后 4.2×→**11×**） | 11× | p99=0.24 max=0.45/千字 | ≥3 处且 ≥0.40/千字 |
| stock_reaction | 6.5× | p99=0.59 max=0.93 | ≥4 处且 ≥0.7/千字 |
| voice_contrast | 5.6× | 1.1% 章有 1 次（合法） | ≥2 处（聚集才报） |
| micro_action_tic（收窄版：排除「了下去/了下来」趋向补语等原正则虚宽） | 3.2× | p99=2.98 | ≥8 处且 ≥3.0/千字（人类误报 0.73%，AI 命中 14.3%） |
| trailer_ending | 绊线（AI 本仓 0；oh-story 语料 1.3-6.6%） | 末段 0.9% | 末600字 1 处 |
| trailer_summary | 绊线 | 末段 **0/1135** | 末600字 1 处 |

**否决 9 轴（负结果，防止后人再抬进来）**：
- `comment_opening` 段首零主语评论：**小说侧反转**——人类 9.2% vs AI 0.4%（0.07×）。
  lieflat 的 4.4× 是议论文语料；小说旁白的「说到底/看起来」是人类叙述者声口。
- `reasoning_chain` 解释链密度：同样反转（人类 62.6% vs AI≈0）——
  他知道/任务/风险是小说正常叙述词，oh-story 的判据不适用于我们的语域。
- `negation_parade` 否定排比：2.19× 但样例复核显示「没有腥风，没有腐臭」类复沓
  是人类正当文学手法（6.2% 人类章在用），误杀面不划算。
- `quote_emphasis` 引号强调：实现实际量的是短对白密度（与 staccato 轴重叠），
  需要更好的叙述层判别才有资格重进。
- `personified_negation`（人类 1.4% 合法 vs AI 0）、`transmigration_trope`（0.94×，
  语料里穿越文的「原身/原主」是题材常态）、`worldbook_dump`（双侧≈0）、
  `action_list`（1.72×且命中率过低）、`cross_negation` 三行式（双侧 0）。
- 简介层粗形词表全军覆没的教训：「很简单：」「评论区见」「未来可期」在 2218 条
  真实榜单简介里是**人类常态**（自媒体腔在平台语境不是缺陷）——五族全部收窄
  到助手腔/擂鼓帽句/饱和密度后才过准入（误报 0-0.05%）。

## 五、验证计划

1. **校准准入（先于落地）**：`scripts/deai_fusion_calibrate.py` 对全部候选轴跑
   人类语料（.distillation_private 随机 N 章）× AI 语料（舌尖通神 50 章 + 磁盘书章节），
   输出：人类章命中率 / 密度分位数 / AI 章命中率 / 倍率 / 20 条样例命中。
   不过准入线（倍率<1.25 或人类侧不稳）的轴**不落地**，记入负结果。
2. **L1**：每条落地轴配 SF/SNF 成对单测；全量 tests/unit 回归。
3. **L3**：落地后在真章上复跑 detector 前后对比（新轴命中分布 + 无既有轴回归）；
   deslop 真机验证一章（病章治愈 + 干净章 no-op + 字数契约）。

## 五·五、验收结果（2026-08-30 实施完毕后）

**端到端三语料验收**（scripts/deai_fusion_validate.py，接线后真 detect()）：
- 人类 377 出版章：七轴误报率 stock 0% / micro 0.80% / reverse 0% / voice 0.27% /
  trailer_ending 0.53% / trailer_summary 0% / **signature_run 0%**——全部 ≤1.5% ✓
- AI 侧合计点名 106 章次：signature_run 在架 35%、淘汰稿 14.7%；micro 11.7%/14.3%；
  reverse 3.3%/1.2%；stock 0.8%（淘汰稿）✓
- 途中修正一处**量具虚宽**：oh-story 的「了一下」原正则把「了下去/了下来」趋向补语、
  「瞪大了眼睛」「点了一道菜」都算命中（人类侧虚高 1.86%）；收窄后倍率 2.6×→3.2×、
  人类误报 0.73%——「抽 20 条命中人工看」铁律再次抓到问题。

**真机 LLM deslop 闭环**（3 份被淘汰病稿，生产同款 gate→deslop→复检）：
- reverse-ch050：score 52→37，**reverse_contrast 轴被定向重写清除**（检出→触发→
  重写→复检清零全链走通），dash_train 降档、staccato/dialogue_famine 一并清掉；
- micro-ch004：score 28→20；
- stock-ch032：原样拒稿——keep-better 键日志 `(0.48,71.7,0,0,0.82,3.69,9.87)→
  (0.50,…,0.87,…,9.03)`：重写让 staccato 与 stock 密度**变差**，新量具正确拒收
  （改造前这两轴折成 1 span，选稿层根本看不见）。该稿四条病态带全越线，
  属历史已知的 DITTO 型重症章，不是新轴的失败。

## 六、明确不做的（及原因）

- **生成端注入外部规则清单**：六轮 A/B 已证净负（author-x-rule-cartesian）；外部仓的黑名单
  与反例句只进检测/修补端。ai-flavor-remover 的加法派指令（补感官细节/设问互动）与
  Humanizer「灵魂层」不直接采纳——前者的推荐词本身是新 AI 味（「更重要的是」在
  shuorenhua Tier1 删除表里），后者与我们已有 POV 内心声音路线重复。
- **句长 CV / 删设问 / 杀比喻 / 杀名词化 / 正文「首先其次」**：lieflat 实测证伪或反向。
- **浓度放大打分（slop-guard concentration α）**：想法对（同 tic 复读比多样违规更该罚），
  但会改变全部既有分数分布，需独立校准轮，本轮不动计分公式。
- **风格指纹 9 指标漂移门**（ximen）：与本框架「书内文风一致性」是另一条线，本轮不做。
- **内容债短路出口**（zh-humanizer-literary）：思想采纳（空心段回上游），但需要管线级
  改动，记入后续路线图。
