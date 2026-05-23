# Anti-Slop Prose System — 让 LLM 写出"非 AI 味"长篇

> **状态**：Draft v1.0
> **日期**：2026-05-23
> **目标读者**：框架工程师 + 编辑
> **前置依赖**：Dialogue Voice System（对白层）已设计；本文档补足"非对白叙述层"

---

## 0. 问题陈述

### 0.1 现状证据

《今日问道》v1 第 1 章原文（已部分裁剪）：

> 北荒的风总带着矿砂味，吹在脸上像钝刀。林烬一路走来，早习惯了疼，但这一天的疼不同。**林烬在矿镇灰井中被压迫，意外感应到体内烬火道种，命运开始转向。** ……
>
> **这一章里，他面对的不只是对手**，还有旧秩序塞进他体内的恐惧。有人要他认命，有人要他交出骨戒，也有人劝他把锋芒收起来换一线安稳。林烬却在最危险的节点做了反方向选择：把火点得更亮，把真相抛到人前，把退路亲手烧掉。**他知道这很蠢，也很贵，但不这么做，他永远只是被统计在名册里的"空脉废人"。**
>
> ……
>
> **章末余波并未平息。** 宗门、公审、遗迹、暗城等多方势力都在重新估算林烬的威胁等级，原本隐蔽的线索开始连成链条。所有人都在等下一次爆点，而林烬已经决定先手布局，把对方最稳固的一块基石撬开。远处传来低沉震鸣，**正是本卷长线钩子——矿镇地脉异动。**

这段文字命中 6 种 AI 味重症：

| 症状 | 行内例 | 病因（一句话） |
|---|---|---|
| **元叙述（meta-narration）** | "这一章里" / "章末余波" | LLM 把章节当成有边界的"作业单元"写 |
| **大纲复述** | "林烬在矿镇灰井中被压迫，意外感应到体内烬火道种" | contract.main_plot_progress 字段被直接念出 |
| **设计名词外露** | "正是本卷长线钩子—矿镇地脉异动" | 钩子标签当成台词读出来 |
| **总结式收尾** | "宗门、公审、遗迹、暗城等多方势力都在重新估算……" | 缺乏"用具体动作收束"的指令 |
| **解释主角动机** | "他知道这很蠢，也很贵，但不这么做……" | 心理 telling 而非 showing |
| **抽象群像清单** | "有人要他认命，有人要他交出骨戒，也有人……" | 把多敌人压成 N 行排比，没有人物 |

### 0.2 对比真人榜单作家如何收章

| 作品 | 章末原文 | 共同模式 |
|---|---|---|
| 《大奉打更人》ch2 | "她眉宇间淡淡的哀愁已经散去，像是告别了过往，重获新生。" | **一句话状态特写** |
| 《雪中悍刀行》ch2 | "徐凤年不置可否，转头回望一眼广陵江。" | **纯动作收尾** |
| 《魔临》ch1 | （独立行）"亲爹。" | **单词揭示式 punchline** |
| 《深夜书屋》ch2 | "画中，在池子中浮浮沉沉的人，是周泽，是上辈子还是医生的周泽！" | **图像 + 身份揭示** |

**核心差异**：真人作家在章末给的是「**当前画面的最后一帧 / 一句话**」；我们的产出给的是「**这章/这卷的工作进展汇报**」。

### 0.3 现有门禁为什么管不住

- `ai_flavor_gate` 抓"高频默认短语"（"果然"/"看来"），抓不到"这一章里"这种**结构性元叙述**
- `exposition_density_gate` 抓"信息倾倒比例"，抓不到"复述大纲"这种**单段集中症**
- `dialogue_voice_gate` 只管对白，不管叙述
- `hook_echo_gate` 管钩子是否回响，但反而**鼓励**章末点名钩子

所有门禁都是"事后检测"。**当 prompt 里把 main_plot_progress、hook_strategy、hook_keywords 全部明文塞进去时，LLM 不可能不照念**。门禁堵漏的速度永远跟不上 prompt 出洞的速度。

---

## 1. 设计哲学

### 1.1 三句话原则

| 编号 | 原则 | 反面教材 → 正面 |
|---|---|---|
| P1 | **场景是相机机位，章节只是剪辑边界** | 不要写"这一章里" → 写"夜里" |
| P2 | **设计文档不出现在成稿里** | 不要写"本卷长线钩子" → 写"远处传来低沉震鸣" |
| P3 | **解释通过具体行动产生，不通过作者旁白** | 不要写"他知道这很蠢" → 写他下意识攥紧了什么 |

### 1.2 "AI 味"的本质定义

> **AI 味 = LLM 把"作业要求"当成"作品内容"输出。**

具体来说，框架告诉 LLM：

> "本章要推动主线，钩子是 X，情绪从 A 到 B，要呼应卷线 Y"

而 LLM 不知道**如何把这些指令转化为画面**，于是直接**字面复述**。它说出了你交给它的东西，但没有用故事的方式说。

### 1.3 解决方案的层级

```
┌─────────────────────────────────────────────────┐
│  L4: Gate    —— 事后检测/微修（守门员）          │
│  L3: Render  —— 把 Beat 渲染成画面（执行）       │
│  L2: Beat    —— 把 Contract 拆成镜头（编辑）     │
│  L1: Prompt  —— 喂给 LLM 的东西（输入）          │
└─────────────────────────────────────────────────┘
       根因在 L1-L2，门禁在 L4 是补丁
```

**主要修复点**：L1（去毒 prompt）+ L2（新增 Beat 层）。L3/L4 是兜底。

---

## 2. 根因：当前 Prompt 链路为什么必然产出 AI 味

### 2.1 当前 Prompt 注入的"毒源字段"

在 `prompt_constructor.py` 中实际拼装到 LLM prompt 的：

| 字段 | 注入内容 | LLM 倾向 |
|---|---|---|
| `reader_contract_section` | "卖点 / 承诺 / 钩子策略 / 钩子意象" | 把钩子意象当词复述 |
| `chapter_hook_strategy` | "章级钩子策略：让主角发现 X" | 章末点名 X |
| `hook_keywords` | "核心钩子意象：地脉/骨戒/血字" | 章末罗列这些词 |
| `contract.main_plot_progress` | "林烬被压迫，意外感应道种，命运转向" | 第一段复述 |
| `contract.subplot_progress` | "林烬与顾行舟/苏晚照/宁玄策关系变化" | 列出所有关系名字 |
| `contract.emotion_shift` | "压抑 → 决断 → 反击" | 第三段写"决断" |

LLM 是"乖巧的助理"：你说什么它写什么。给它**作品的描述**，它就写出**对作品的描述**，而不是作品本身。

### 2.2 真人作家的"作业单"长什么样

真人作家给自己写大纲也用同样的字段，但**这些字段进**他**脑子，不进**他**笔**。他笔下出现的是已经渲染过的画面。

LLM 没有这个"中间渲染层"——除非我们显式加上。

### 2.3 三种修复路径的取舍

| 路径 | 思路 | 代价 |
|---|---|---|
| **A. 删毒源** | 不把 contract / hook 塞 prompt | 损失主线连贯性，钩子可能漏掉 |
| **B. 改注入语气** | 用"约束式"而非"描述式"（"不要点名钩子"） | LLM 对否定指令响应弱 |
| **C. 加渲染层** | 把抽象 contract 翻译成具体 beat，再喂 LLM | 多一道编辑成本，但根治 |

**结论**：采用 **C 为主 + B 辅助 + L4 兜底**。

---

## 3. 系统总体设计

### 3.1 五层架构

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 5: Anti-Meta Gate (新)        —— 元叙述/总结/钩子点名 │
│ Layer 4: Show-Don't-Tell Gate (扩) —— 主角动机被解释         │
│ Layer 3: In-Scene Ending Contract  —— 章末必须是动作或画面   │
│ Layer 2: Scene Beat Sheet (核心)   —— Contract → 镜头脚本    │
│ Layer 1: Sanitized Prompt          —— 移除毒源字段           │
└──────────────────────────────────────────────────────────────┘
              ▲
              │ 上层守门，下层根治
```

每层做什么，下面分小节展开。

---

## 4. Layer 1 — Sanitized Prompt（去毒 prompt）

### 4.1 规则

| 字段 | 当前注入位置 | 新做法 |
|---|---|---|
| `chapter_hook_strategy` | 直接拼接到 prompt | 转入 Beat 层"最后一拍约束"，不在 prompt 出现"钩子"二字 |
| `hook_keywords` | "核心钩子意象：A、B、C" | 改成"本章必须出现的具体物件/感官：A、B、C"——动词换名词 |
| `contract.main_plot_progress` | 整句注入 | **不再注入**；改为 Beat 层第一拍的"开局物理状态" |
| `contract.subplot_progress` | 整句注入 | **不再注入**；改为 Beat 层中段的"必含人物互动一笔" |
| `contract.emotion_shift` | "压抑→决断→反击" 整字串注入 | 不在 prompt 出现；改成每个 Beat 的"内心反应"字段 |
| `reader_contract_section` | 卖点 / 承诺 / 钩子策略 | 改成"读者期待的画面，不是营销词"——见 §4.2 |

### 4.2 改造 `reader_contract_section`

**旧**（直接喂 LLM）：

```
【读者契约】
卖点：草根少年逆袭
承诺：每 5 章一次爽点
章级钩子策略：让主角在劣势中先手翻盘
核心钩子意象：地脉、骨戒、血字
```

**新**（喂的是"读者会看见什么"）：

```
【读者期望画面】
- 看到主角处于明显劣势（具体多劣势？穷/伤/被围）
- 看到主角先做了一个看似自杀的选择
- 看到这个选择产生具体后果（不是"翻盘"两字）
- 本章必须有一个能被读者"截图发朋友圈"的画面
```

差别：把**机制语言**翻译成**读者感官**。LLM 跟着写出"画面"，而不是"机制"。

### 4.3 移除"创作多样性约束"块里所有"钩子/卖点/承诺"提及

在 `prompt_constructor.py:469-518` `build_reader_contract_section` 周边代码做最小侵入：

- 增加 `sanitize_for_prose` 模式开关（默认开）
- 开启时：所有提到"钩子 / hook / 卖点 / selling / 承诺 / promise"的行**不**进 prompt
- 这些信息保留在 metadata，供 Beat 层和 Gate 层用

---

## 5. Layer 2 — Scene Beat Sheet（核心创新）

### 5.1 概念

**Beat = 章节里的一个镜头**。一章一般 4-8 个 beat。

每个 Beat 是一个**JSON 对象**，包含：

```yaml
beat_id: B-1
beat_type: opening|action|dialogue|interior|reveal|cliff
camera:                          # 这一拍在哪儿
  location: "矿镇灰井底部"
  time: "黄昏，井下已无光"
  weather: "井口飘进沙粒"
characters_present: [林烬, 苏晚照]
external_event:                  # 看得到的事情
  - 林烬把手按在胸口
  - 一道烬火从指缝渗出，照亮井壁三尺
interior_reaction:               # 内心反应（不写进文字，仅用于把握情绪）
  - 林烬：恐惧 → 决断（不要明说，用动作或独白片段）
sensory_anchor:                  # 必须命中的感官
  smell: "矿砂味"
  touch: "井壁的湿冷"
  sound: "上面传来钢钎敲击声"
dialogue_lines:                  # 这一拍允许的对白条数（含潜台词）
  count: 1
  speaker: 苏晚照
  intent: "试探主角是否还能用"
  forbidden_explicit: ["关心", "命运", "翻盘"]   # 不能直说的词
beat_payoff:                     # 这一拍要兑现什么
  - 让读者看见烬火第一次外显
  - 苏晚照对主角的判定从"废物"变"可用"
banned_devices:                  # 这一拍不允许的修辞
  - 心理大段解释
  - 抽象群像列举
  - 提及"本章""本卷""钩子"
word_budget: [350, 550]
```

### 5.2 为什么 Beat 能消灭 AI 味

| AI 味症状 | Beat 的对治 |
|---|---|
| 复述大纲 | Beat 已经把大纲拆碎成镜头，没有大纲可复述 |
| 元叙述（"这一章"） | Beat 把章节边界消解为"4 个镜头"，没有"章"的概念 |
| 抽象群像 | `characters_present` 写死 1-3 人，多余人物自动 illegal |
| 总结式收尾 | 最后一个 beat 强制 `beat_type=cliff` 且 `external_event` 非空 |
| 解释动机 | `interior_reaction` 明确"不要明说" |
| 钩子点名 | `banned_devices` 显式禁止"钩子"等词 |

### 5.3 Beat 来源

**两条路径**：

1. **LLM 编排路径**（默认）：在写章前增加一个 `SceneBeatPlanner` 服务，输入 contract + 上一章末尾状态 + 角色 voice DNA，输出 Beat 列表。该服务用一个**专门的 Beat 编排 prompt**（远比写章 prompt 短），且 Beat planner 的输出**经一道结构校验**才能进入下一步。
2. **预制路径**（可选）：在 story-bible 里允许编辑手写关键章节（卷首、卷尾、爆点章）的 Beat 列表，作为强约束。

### 5.4 Beat → Prose 的写章 prompt（关键改写）

**旧 prompt（伪代码）**：

```
请写第 12 章。
本章 contract：
- 主线推进：林烬感应道种
- 情感弧：压抑→决断
- 钩子：矿镇地脉异动
卖点：草根逆袭
请按 5500 字撰写。
```

**新 prompt（伪代码）**：

```
请按以下 5 个镜头写一段连续叙述（不要写章节标题、不要写"第一拍/第二拍"）：

# 镜头 1（350-500 字）
位置：矿镇灰井底部，黄昏
人物：林烬（独自）
看得到的事情：
- 林烬把右手按在胸口
- 烬火从指缝渗出，照亮井壁三尺
- 井口飘下三粒矿砂，落在他手背上
必须出现的感官：矿砂味 / 井壁的湿冷
禁用：解释林烬为什么这么做；提及"道种""命运""转向"
镜头结束于：他抬眼看井口

# 镜头 2（400-600 字）
...
```

LLM 此时拿到的是**电影脚本**，不是"工作总结"。它能做的事情只有"渲染画面"。

---

## 6. Layer 3 — In-Scene Ending Contract（章末规则）

### 6.1 章末必须满足三选一

| 类型 | 形态 | 真人示例 |
|---|---|---|
| **A. 动作落幕** | 章末最后一段是一个具体动作 | 雪中："转头回望一眼广陵江" |
| **B. 画面定格** | 章末最后一段是一个具体物理画面 | 大奉："眉宇间淡淡的哀愁已经散去" |
| **C. 揭示反转** | 章末最后一句揭示一个新事实/身份 | 深夜书屋："是上辈子还是医生的周泽！" |

### 6.2 章末**绝对禁止**

- 提及"本章 / 本卷 / 这一章 / 章末 / 这段"
- 多方势力点名（"宗门、公审、遗迹、暗城都在……"）
- 总结这章发生了什么
- 预告下一章要发生什么（钩子靠"未完成动作 / 悬念画面"自然产生，不靠预告）
- 出现"钩子 / 长线 / 主线 / 副线 / 卷线"等设计词

### 6.3 实现位置

- 在 Beat Planner 强制最后一个 Beat：`beat_type=cliff`，`format ∈ {action, image, reveal}`
- 在写章 prompt 末尾追加："最后一拍只写发生了什么，不要写它意味着什么"
- 在 `hook_echo_gate` 改造：检测是否点名钩子词→ 改为**惩罚**而非鼓励

---

## 7. Layer 4 — Show-Don't-Tell Gate（叙述层心理 telling）

### 7.1 检测目标

抓四种"作者直接讲出来"的句式：

| 模式 | 触发词 | 示例 |
|---|---|---|
| **动机解释** | "他知道X，但Y" / "他明白X" / "他清楚X" | "他知道这很蠢，也很贵" |
| **情绪命名** | "X的恐惧 / X的决断 / X的愤怒"（不带具身动作） | "压抑的恐惧涌上心头" |
| **能力总结** | "X的XX能力 / X的XX力量" | "他的判断力告诉他" |
| **关系定性** | "他和X的关系开始变化"（无具体事件） | "林烬与顾行舟关系变化" |

### 7.2 与 `exposition_density_gate` 的关系

- exposition gate 抓"信息倾倒"（世界观/规则/历史）
- show-don't-tell gate 抓"心理倾倒"（人物动机/情绪/关系）
- 两者互补，各管一类 telling

### 7.3 修复策略

- 标记触发段落
- 自动重写候选：
  - 替"他知道X" → 加一个具身动作（攥紧/松手/转开视线/咬唇）
  - 替"X的愤怒" → 替换为具身表现（指节发白/呼吸变快）
- 若 LLM 重写失败 2 次，整章打回 Beat 层重排

---

## 8. Layer 5 — Anti-Meta Gate（元叙述/章节边界感）

### 8.1 黑名单（硬阻断）

任何成稿出现以下短语，**直接打回**：

```
这一章 / 这一卷 / 本章 / 本卷 / 章末 / 卷末
故事到此 / 至此为止 / 接下来 / 下一章
钩子 / 长线 / 主线 / 副线 / 卖点 / 承诺
读者期待 / 读者会 / 我们的主角
余波 / 涟漪暂歇 / 暂告一段落
```

### 8.2 灰名单（高警告，需人复审）

```
所有人都在 / 各方势力 / 多方势力 / 江湖众人
原本的 / 原本隐蔽的 / 至此已经
重新估算 / 重新评估
```

这些是"摄影机拉到天上"的标志——叙述者突然变成全知评论员。

### 8.3 章末三句单独审

- 章末倒数三句必须满足 §6.1 的 A/B/C 之一
- 不满足时单独触发 cliff-rewrite 子流程（只重写章末 3-5 句，不动其他内容）

### 8.4 与 `ai_flavor_gate` 的关系

- ai_flavor_gate：抓"高频默认短语"（词级）
- anti_meta_gate：抓"叙述视角失控"（结构级）
- 配套，不互相替代

---

## 9. Sample-Driven Voice Anchoring（样本驱动锚定）

### 9.1 思路

现有"高评分小说"库已经放在 `/Volumes/书籍/Ebook_UTF8/高评分小说/`，把它**结构化抽取**为：

- **章末 200 字样本库**：每本前 20 章的最后 200 字（已经 strip 掉对设计的引用）
- **场景开篇 200 字样本库**：每章首段（不含章标题）
- **对白片段库**：连续 5 句以上的对白序列

### 9.2 用法

- **不**作为 few-shot 直接抄（会污染版权）
- 抽取**结构特征**：
  - 章末"动作 vs 画面 vs 揭示"分布占比 → 用作 Beat 层 cliff type 的先验
  - 章首是"对白先入 / 感官先入 / 远景先入"的分布 → 作为 opening archetype 的概率分布
  - 一段连续 5 句对白中"没有 stage direction 的句子"占比 → 作为对白生成约束
- 这些**统计特征**喂给 Beat Planner 而不是写章 LLM

### 9.3 落地服务

新增 `sample_corpus_indexer.py`：

```
input: /Volumes/书籍/Ebook_UTF8/高评分小说/*.txt
output: data/style/samples/{book_slug}/
        ├── chapter_endings.jsonl     # {chapter_no, last_200_chars, classified_type}
        ├── chapter_openings.jsonl    # {chapter_no, first_200_chars, opener_type}
        └── dialogue_runs.jsonl       # {speakers, turns, no_stage_ratio}

output: data/style/aggregates.yaml    # 聚合后的概率/直方图
```

聚合输出供 Beat Planner / Gate 引用。

---

## 10. 实施路线图（4 个 PR）

### PR-1：Beat Planner 骨架（最关键）

**Scope**:
- 新增 `src/bestseller/domain/scene_beat.py`（领域模型）
- 新增 `src/bestseller/services/scene_beat_planner.py`（编排服务）
- 新增 `src/bestseller/services/scene_beat_renderer.py`（写章 prompt 改造）
- `chapter_orchestrator.py` 把 contract → Beat → Prose 三步串起来
- 关闭/移除 prompt 里 `chapter_hook_strategy` / `hook_keywords` / `contract.*` 直接注入
- 测试：beat 必须能被 deterministic 生成、prompt 必须不含"钩子"二字

### PR-2：Anti-Meta Gate + In-Scene Ending Gate

**Scope**:
- 新增 `src/bestseller/services/anti_meta_gate.py`
- 复用 `ai_flavor_gate` 的 patcher 基础设施
- 新增章末 cliff-rewrite 子流程（只动最后 3-5 句）
- 与现有 `hook_echo_gate` 关系：hook_echo 改为"钩子意象是否在 Beat 层落地"，不再要求成稿出现钩子词

### PR-3：Show-Don't-Tell Gate

**Scope**:
- 新增 `src/bestseller/services/show_dont_tell_gate.py`
- 心理 telling 检测规则集
- 与 `exposition_density_gate` 并行不冲突
- 重写策略：行内动作替换 + 失败回退到 Beat 层

### PR-4：Sample Corpus Indexer + 概率喂入

**Scope**:
- 新增 `scripts/index_style_corpus.py`
- 新增 `src/bestseller/services/style_priors.py`
- Beat Planner 引用 priors 决定 opening / cliff 类型分布
- 同时提供 inspection API：`/api/projects/{slug}/style-priors`

---

## 11. 与现有系统的对接

### 11.1 已有可复用资产

| 已有 | 复用方式 |
|---|---|
| `dialogue_voice_gate` | 对白层保留不动，本系统只管叙述层 |
| `ai_flavor_gate` | 词级仍由它处理，本系统补结构级 |
| `exposition_density_gate` | 信息倾倒由它处理，本系统补心理 telling |
| `hook_echo_gate` | 语义改为"Beat 层是否兑现"，不再要求成稿点名 |
| `chapter_orchestrator` | 中间插入 BeatPlanner 一步 |
| Voice DNA | 直接成为 Beat 层 `dialogue_lines.forbidden_explicit` 的来源 |

### 11.2 需要破坏性改动的位置

| 文件 | 改动 |
|---|---|
| `prompt_constructor.py:469-518` | `build_reader_contract_section` 增加 sanitize 模式 |
| `prompt_constructor.py:798-836` | 主装配器在 sanitize 模式下不注入 hook/contract 字段 |
| `pipelines.py` | chapter 写章前调用 BeatPlanner |
| `quality_gates.yaml` | 新增 anti_meta / show_dont_tell / in_scene_ending 三档门禁 |

### 11.3 配置默认值

`config/default.yaml` 新增：

```yaml
prose_quality:
  sanitize_prompt: true            # 切断设计字段→prompt
  beat_planner:
    enabled: true
    beats_per_chapter: [4, 7]
    cliff_type_distribution:
      action: 0.4
      image: 0.35
      reveal: 0.25
  gates:
    anti_meta:
      severity: block              # 元叙述零容忍
    show_dont_tell:
      severity: warn               # 初期收信号
    in_scene_ending:
      severity: block              # 章末规则零容忍
```

---

## 12. 验证方案

### 12.1 离线验证（不需要 LLM）

- 拿当前已经生成的 jin-tian-wen-dao 全部章节作为 negative samples
- 拿 `/Volumes/书籍/Ebook_UTF8/高评分小说/` 抽样章节作为 positive samples
- 跑全套新 gates，验证：
  - 我们的章节 anti_meta_gate 命中率应当 >70%（说明 gate 能识别问题）
  - 真人章节 anti_meta_gate 命中率应当 <5%（说明 gate 不会误伤）
  - 我们的章节 show_dont_tell_gate 命中率应当 >40%
  - 真人章节 show_dont_tell_gate 命中率应当 <10%

### 12.2 在线验证（需要 LLM）

- 选 jin-tian-wen-dao 第 1 章作为基准，在新系统下重新生成
- 对比指标：
  - 是否还出现"这一章 / 本卷长线钩子 / 章末余波"
  - 章末 200 字与高分样本相似度（向量距离）
  - 心理 telling 句子占比
  - 人工盲读评分（5 个章节，6 个评委）

### 12.3 验收门槛

PR-1 验收：
- BeatPlanner 输出对 5 个不同 contract 100% 通过结构校验
- 同一 contract 重跑 3 次，Beat 集合 Jaccard 相似度 >0.5（稳定但不死板）

PR-2 验收：
- 对 negative samples，anti_meta_gate 召回 >70%
- 对 positive samples，误伤 <5%

PR-3 验收：
- show_dont_tell_gate 把 jin-tian-wen-dao ch1 的 6 处心理 telling 抓到 ≥5 处

PR-4 验收：
- style_priors 输出与人工标注一致率 >80%

---

## 13. 失败模式与回退

| 失败模式 | 现象 | 回退 |
|---|---|---|
| BeatPlanner 输出不合法 | 缺字段、beat 数过少 | 回退到旧 prompt 模式，记 incident |
| Beat 太死，章节失去整体感 | 章节读起来像零散镜头 | 引入 `beat_continuity_check`（前一拍最后一帧 = 后一拍第一帧的 anchor） |
| 章末 cliff 重写后丢钩子 | 下一章接不上 | hook_echo_gate 改成检查"钩子物件是否在某 beat 出现过"，不要求章末 |
| Sample priors 偏向某种风格 | 写出来全是雪中悍刀行味 | priors 按目标市场 profile 加权（仙侠 / 都市 / 灵异分桶） |

---

## 14. 决策点（需用户确认）

- [ ] BeatPlanner 是否要支持人工预制 beat（编辑可锁定关键章节）？
- [ ] sanitize_prompt 是否默认全本开启？还是按章节配置（卷尾爽章可能需要更强的钩子）？
- [ ] show_dont_tell 初期 severity 是 `warn` 还是 `block`？
- [ ] 样本库索引是否上传至项目内 `data/style/`（约 50-100MB）还是按需读取？

---

## 15. 附录：现状 vs 目标重写对比

**现状（第 1 章首段）**：

> 北荒的风总带着矿砂味，吹在脸上像钝刀。林烬一路走来，早习惯了疼，但这一天的疼不同。林烬在矿镇灰井中被压迫，意外感应到体内烬火道种，命运开始转向。当他把手按在胸口时，烬火道种像一枚被唤醒的暗星，在经脉深处一闪一灭，提醒他：再往前一步，便再也回不到从前。

**问题标注**：
- "林烬在矿镇灰井中被压迫……命运开始转向" = contract 复述
- "提醒他：再往前一步……" = 心理 telling

**目标重写（同一首段，按 Beat 1 渲染）**：

> 北荒的风裹着矿砂，灰井底已经看不见井口。林烬背靠井壁坐下，掌心按在胸口，那里跳着一点不属于自己的东西。
> 一团火从他指缝渗出，烤热了井壁三尺，土皮上的水珠"嘶"地散成白汽。他没动，只是看着自己被照亮的另一只手——指节是白的，攥着那枚比他还旧的骨戒。
> 井口落下三粒砂，砸在他手背。
> "下来。"上面传来一声。
> 林烬没抬头。

**变化**：
- 没有任何抽象动词（"被压迫" / "命运" / "转向"）
- 没有任何作者旁白（"提醒他"）
- 所有内心都通过物理动作（攥骨戒 / 没抬头）
- 钩子（地脉异动）通过感官（井壁的水变白汽）暗示，不点名

**这就是非 AI 味的样子。**

---

