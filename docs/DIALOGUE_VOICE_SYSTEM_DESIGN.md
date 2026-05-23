# Dialogue Voice System — 完整设计与开发方案

> **状态**：Draft v1.0
> **作者**：Framework Team
> **日期**：2026-05-23
> **目标读者**：开发框架的工程师 + 落角色卡的编辑/作者
> **前置依赖**：Story Integrity Gates（Timeline / Scene / Character Role / Chapter Length）已落地

---

## 0. 问题陈述（Why this doc exists）

当前框架能保证**情节正确**（时间线 / 场景连贯 / 角色定位 / 章节体量），但**对话本身没有任何质量约束**。后果是：

> 钱婆婆的声音忽然带了点笑意，"有意思，小子。"

这一行可以原封不动塞进任何一本 LLM 写的玄幻、悬疑、修仙、都市小说，没有任何角色识别度。这是典型的 **AI 味对话**。

### 0.1 当前实际产出的 AI 味症状（来自《青囊不语问阴阳》ch1-3）

| 症状 | 出现位置 | 病灶 |
|---|---|---|
| LLM 高频默认短语 | 「有意思」「看来」「果然」 | 跨角色无差别使用 |
| 万能 stage direction | 「冷冷一笑」「淡淡地说」「眼神复杂」 | 角色之间无区别 |
| 通用替换专属词 | 「记上了」（应说"账上添一笔"） | 角色专属语汇未建立 |
| 称呼脱离人设 | 「小子」（七十年代农村老人不这么叫） | 称呼未做语境化 |
| 对话无潜台词 | 角色直说真实意图 | 缺少 want vs say 的分离 |
| 对话节奏对称 | A 一句 B 一句 ping-pong | 缺少不答 / 沉默 / 偏题 |
| 不同角色用同样词 | 林渊和钱婆婆都说「果然」 | 词汇集合未互斥 |

### 0.2 现有框架为什么管不到

- `CharacterRoleGate` 校验**能力 / 身份**（风水师 / 商人），不校验**怎么说话**
- `cast-and-promises.md` 没有 voice_dna 字段
- 写章 prompt 没有给 LLM 任何「这个角色说话长什么样」的样本
- 没有对话专属门禁

### 0.3 本设计要补的能力

把对话从「LLM 自由发挥」改为**有结构、可校验、可重写**的产物。

---

## 1. 设计哲学（Design Principles）

### 1.1 Voice ≠ Personality

- **Personality（人设）**：内心怎么想
- **Voice（声纹）**：嘴上怎么说

LLM 给了人设之后，**默认用统一的"小说腔"输出**所有人的台词。Voice 系统强制每个角色用 **自己的词汇集合 / 句式 / 节奏 / 物理标签** 说话。

### 1.2 白名单 > 黑名单

LLM 对「不要做 X」响应弱，对「你只能从这 5 个口头禅里选」响应强。Voice DNA 用**白名单驱动**：

- pet_phrases（必出现的口头禅）= 白名单
- forbidden_phrases（禁用词）= 黑名单（辅助）
- vocab_ceiling / floor = 语域硬约束

### 1.3 可机器校验 > 抽象描述

❌ Bad：「钱婆婆说话有民间老人的味道」
✅ Good：

```yaml
register: 七十年代农村老一辈
sentence_length_zh: [5, 12]
syntax_quirks: [主语省略, 反问当陈述, 句末粘"哩/呢"]
pet_phrases: [账, 这笔, 毛头, 亏不亏]
forbidden_phrases: [有意思, 小子, 原来如此, 看来]
```

后者每一条都可以写代码校验。

### 1.4 同人异境 > 同境异人

- **同人异境**（要做到的）：同一角色在 5 个语境下用同一套词，但密度/语速变化
- **同境异人**（必须避免的）：所有角色在同一场景下用同一套词

### 1.5 反 AI 第一定律

> **真人对话从来不是问什么答什么，至少 30% 的对话回合是不答的（沉默 / 动作代回答 / 偏题 / 反问）。**

---

## 2. 四层对话模型（Four-Layer Dialogue Model）

每一句对话必须同时通过四层校验：

```
┌────────────────────────────────────────────────────┐
│ Layer 4: Negative Space     —— 这话「拒绝说什么」    │
│ Layer 3: Subtext            —— 这话「真想要什么」    │
│ Layer 2: Context Modulation —— 这话「在何境」       │
│ Layer 1: Voice DNA          —— 这话「像谁说的」      │
└────────────────────────────────────────────────────┘
                     ▲
                     │ 上层必须建立在下层之上
                     │ 缺任一层 → AI 味
```

### Layer 1 — Voice DNA（声纹）

定义见 §3。

### Layer 2 — Context Modulation（语境调度）

定义见 §4。

### Layer 3 — Subtext（潜台词）

每一句对话同时回答 **3 个问题**：

1. **Surface（表面在说什么）**
2. **Want（真正想要什么）**
3. **Hide（想隐藏什么）**

样例：钱婆婆对林渊催账

| Layer | 内容 |
|---|---|
| Surface | "这笔，记你毛头头上。" |
| Want | 让林渊**主动问** "亏什么" |
| Hide | 她其实知道这是林家旧债，不是林渊的 |

要让 LLM 写出 want/hide 不一致的对话，必须在 prompt 里**显式喂给模型**当前对话的三层意图。

### Layer 4 — Negative Space（沉默 / 拒绝 / 偏题）

每个角色必须配 **≥1 种「不答」的方式**：

```yaml
钱婆婆 negative_space:
  - 被问敏感问题 → 翻账本（动作代回答）
  - 被催 → 用更慢的语速
  - 被夸 → 直接岔开 ("茶要凉了")

林渊 negative_space:
  - 被问父亲 → 切到罗盘上（动作代回答）
  - 被恭维 → 反问 ("你说的是几年前的事？")
  - 被威胁 → 多说一字数都没有（沉默）
```

后写门禁会统计整章 negative_space 段落数；若 < 2 → high。

---

## 3. Voice DNA 数据模型（9 轴定义）

每个角色由这 **9 个轴**唯一刻画：

```yaml
voice_dna:
  # ─── 语域 ───
  register: str                  # eg "民间老一辈", "城市男 30 岁 风水师"

  # ─── 句长 ───
  sentence_length_zh: [int, int] # [min, max] 中文字符数, eg [5, 12]

  # ─── 句式 ───
  syntax_quirks: list[str]       # eg ["主语省略", "反问当陈述", "末尾粘字哩/呢"]

  # ─── 必出现的口头禅（白名单, ≥1/章）───
  pet_phrases: list[str]         # eg ["账", "毛头", "亏不亏"]

  # ─── 禁用词（黑名单, 一出现即 critical）───
  forbidden_phrases: list[str]   # eg ["有意思", "小子", "原来如此", "看来"]

  # ─── 词汇上下限 ───
  vocab_ceiling: str             # eg "初中以下", "大学", "古文"
  vocab_floor: str               # eg "不爆粗口", "可带方言土腥"

  # ─── 语速 / 节奏 ───
  speech_speed: str              # "极慢" | "慢" | "中" | "快" | "极快"

  # ─── 物理标签（每段开口至少 1 个）───
  body_tells: list[str]          # eg ["舌头顶腮帮", "袖口抹嘴角", "不抬眼"]

  # ─── 永远不主动提的话题 ───
  taboo_topics: list[str]        # eg ["年轻时", "为什么收账"]

  # ─── 语境调度矩阵 ───
  context_modulation:
    debt_pressure:
      sentence_length_zh: [3, 7]
      pace: 慢
      sample: "账上添一笔。"
    to_protagonist:
      sentence_length_zh: [5, 12]
      pace: 中
      sample: "毛头，这笔亏不亏。"
    offended:
      sentence_length_zh: [1, 4]
      pace: 极慢
      sample: "再说一遍。"
    memory_triggered:
      sentence_length_zh: [8, 20]
      pace: 快
      sample: "我那年也是七岁…算了。"
    dying:
      sentence_length_zh: [1, 6]
      pace: 极慢
      sample: "林…正淳…的儿…"

  # ─── 反 AI Negative space ───
  negative_space:
    - condition: 被问敏感问题
      response: 翻账本（动作代回答）
    - condition: 被催
      response: 用更慢的语速
    - condition: 被夸
      response: 直接岔开 ("茶要凉了")
```

---

## 4. 语境调度矩阵（Context Modulation Matrix）

同一角色 × N 个语境 → N 种声纹偏移。

### 4.1 通用调度维度

| 维度 | 取值 | 调度方式 |
|---|---|---|
| 关系 | 陌生 / 熟人 / 亲人 / 敌人 / 上下级 | 影响词汇正式度、称呼 |
| 情绪 | 平静 / 紧张 / 愤怒 / 悲伤 / 喜悦 / 麻木 | 影响句长、语速、口头禅密度 |
| 处境 | 主动 / 被动 / 受压 / 受伤 / 醉酒 / 疲惫 | 影响句子完整度、physical tells 密度 |
| 隐私 | 私下 / 半公开 / 公开 / 被监视 | 影响隐瞒程度、潜台词层数 |

### 4.2 钱婆婆的 5 状态矩阵（具体例）

| 语境 | 句长 | 语速 | 口头禅密度 | Body tell | 例句 |
|---|---|---|---|---|---|
| **对债务人施压** | 3-7 字 | 慢 | 高 | 不抬眼 | "账上添一笔。" |
| **对林渊（受过她恩情）** | 5-12 字 | 中 | 中 | 抹嘴角 | "毛头，这笔亏不亏，看你怎么记。" |
| **被冒犯** | 1-4 字 | 极慢 | 沉默+反问 | 顶腮帮 | "再说一遍。" |
| **回忆触发** | 8-20 字 | 快 | 走神性单字 | 看窗外 | "我那年也是七岁…算了。" |
| **垂死/疲惫** | 1-6 字, 断 | 极慢 | 名字本身 | 喉咙响 | "林…正淳…的儿…" |

**关键**：5 种状态用的**核心词汇集合相同**，但**排列密度 + 句长 + 语速**完全不同。这才叫"同一个人"。

### 4.3 林渊的 5 状态矩阵

| 语境 | 句长 | 语速 | 例句 |
|---|---|---|---|
| **执业中（看宅 / 看物）** | 7-12 字 | 中 | "这镜子背后写了我父亲的名字。" |
| **被父亲声音逼问** | 1-5 字 | 慢, 句间停顿长 | "先查张建军。" |
| **被恭维 / 被试探** | 3-8 字, 反问 | 中 | "你说的是几年前的事？" |
| **被亲近的人（钱婆婆）问** | 5-15 字 | 中, 带语气词 | "婆婆，这笔不在我账上。" |
| **下达决断** | 1-3 字 | 快 | "走。" / "压一下。" |

---

## 5. 反 AI 对话黑名单（Cross-Character Hard Bans）

这一份是**全角色共享**的硬约束。任何角色任何场景都不允许出现。

### 5.1 LLM 高频默认短语（zero tolerance）

```python
LLM_DEFAULT_PHRASE_BLACKLIST = (
    # 万用感叹
    "有意思", "原来如此", "难怪", "怪不得", "果然", "果然如此",
    # 万用应答
    "你说得对", "我懂了", "我明白了", "我知道了",
    # 万用犹豫
    "也许吧", "我猜", "我觉得", "我认为",
    # 万用过渡
    "话说回来", "总之", "简而言之", "不管怎样", "无论如何",
    # 万用观察
    "看来", "看起来", "似乎",
    # 万用 stage direction
    "他冷冷一笑", "他淡淡地说", "他不动声色", "她若有所思",
    "他冷冷开口", "他缓缓道", "他低声道",
)
```

### 5.2 万能 stage direction（每章 ≤ 1 次）

```python
GENERIC_STAGE_DIRECTIONS = (
    "冷笑", "苦笑", "微微一笑", "笑了笑", "勾起嘴角",
    "眼神冷峻", "目光如炬", "眼神复杂", "眼神冰冷",
    "握紧拳头", "紧握双手", "攥紧",
    "深吸一口气", "长舒一口气",
)
```

### 5.3 对称式对话节奏（节奏门禁）

**禁止**：连续 ≥4 个对话回合长度方差 < 0.1（即均匀 ping-pong）。

**要求**：至少 **30%** 的对话回合采用以下"非对称应答"之一：

- 动作代回答（"他把铜钱放下，没说话"）
- 沉默
- 反问
- 偏题
- 半句中断

### 5.4 跨角色词汇互斥

- 任何 pet_phrase 不能跨角色重复
- 同一对话场景里两个角色的对话词集 Jaccard 相似度必须 < 0.4

---

## 6. 角色原型库（Character Archetype Library）

每本书都会重复出现相同几类角色。先建一个通用原型库，每个原型给一套 voice_dna 模板，按需克隆+微调。

### 6.1 九个常见原型

| ID | 原型 | 关键特征 | 句式 | 词汇上限 | 案例 |
|---|---|---|---|---|---|
| **P1** | 民间高人（老者/术士） | 词少, 主语省, 用旧词 | 短句, 单字句 | 初中 | 钱婆婆, 顾怀山 |
| **P2** | 主角（专业人 / 行家） | 否定句多, 不解释, 命令短 | 中短句, 句尾压住 | 大学 | 林渊 |
| **P3** | 江湖小商人 / 中间人 | 套近乎+敷衍, 反复 | 长句, 多语气词 | 高中 | 王建业 |
| **P4** | 反派 / 拟人鬼物 | 模仿对方语调 | 镜像句式 | 同对方 | 第八张脸 |
| **P5** | 知识分子 / 警察 | 准确, 限定词多 | 长定语, "可能/初步" | 大学+ | 刑警, 法医 |
| **P6** | 同辈伙伴 / 兄弟姐妹 | 开玩笑, 互相打断 | 半句, 抢话 | 中等 | （青囊待引入） |
| **P7** | 小孩 / 弱势 | 简单语法, 重复 | 句末上扬 | 小学 | 7 岁林渊（回忆） |
| **P8** | 权威长辈 | 陈述句, 不商量 | 中长, 完整主谓宾 | 大学 | 林家辉（回忆） |
| **P9** | 烟火气角色（小贩/物业） | 量词多, 方言, 跑题 | 长句+跑题 | 小学-初中 | 楼下大妈 |

### 6.2 原型模板（以 P1 民间高人为例）

```yaml
# templates/archetype_P1_folk_master.yaml
archetype: P1_folk_master
default_voice_dna:
  register: 民间老一辈
  sentence_length_zh: [3, 12]
  syntax_quirks:
    - 主语省略
    - 反问当陈述
    - 末尾粘字 ("哩 / 呢 / 啵 / 嘞")
  pet_phrases_pool:  # 选 4-6 个
    - 这笔
    - 后生 / 毛头 / 娃儿
    - 亏不亏
    - 老规矩
  forbidden_phrases_inherit:
    - 有意思
    - 小子
    - 原来如此
    - 看来
    - 果然
  vocab_ceiling: 初中以下
  speech_speed: 慢
  body_tells_pool:
    - 舌头顶腮帮
    - 袖口抹嘴角
    - 不抬眼
    - 拿东西反复摩挲
  required_context_modulations:
    - debt_pressure / business_pressure
    - to_protagonist
    - offended
    - memory_triggered
```

新建角色时只需要从原型继承 + 改 4-5 项专属值（pet_phrases、taboo_topics、body_tells）。

---

## 7. 框架集成架构（System Architecture）

### 7.1 模块依赖图

```
                 cast-and-promises.md  (加 voice_dna 块)
                          │
                          ▼
         dialogue_voice_profile.py  (parser/dataclass)
              │                    │
              ▼                    ▼
  render_dialogue_voice_block   dialogue_voice_gate.py
        (prompt 注入)             (后写校验)
              │                    │
              ▼                    ▼
      chapter_orchestrator    retention_safety_gate
              │                    │
              ▼                    ▼
          drafts.py              auto-repair loop
   (user_prompt 顶部位置)         (block code:
                                 DIALOGUE_AI_FLAVOR)
```

### 7.2 数据流（写章前 → 写章 → 写章后）

```
1. 章节起步:
   prepare_chapter_context → 加载所有出场角色 voice_dna
                          → 推断每个角色的 context_modulation 模式
                          → 渲染 dialogue_voice_block

2. 写章 prompt 装配:
   drafts.py user_prompt 前 1000 字注入:
     【对白声纹合同】
     + 各角色 voice_dna 摘要
     + few-shot 样本 (从 voice-samples/ 抽 1 段)
     + 本章语境调度提示
     + 全角色硬禁忌

3. 写完后校验:
   dialogue_voice_gate.check 跑 7 项检测
     ↓
   任一 critical → stamp DIALOGUE_AI_FLAVOR
     ↓
   auto-repair loop 触发重写
```

### 7.3 新增 / 修改文件清单

**新建**：

```
src/bestseller/domain/dialogue_voice.py          # 数据模型
src/bestseller/services/dialogue_voice_profile.py # parser
src/bestseller/services/dialogue_voice_gate.py    # 后写门禁
src/bestseller/services/dialogue_voice_blocks.py  # prompt 渲染
src/bestseller/services/dialogue_archetypes.py    # 原型库
tests/unit/test_dialogue_voice_profile.py
tests/unit/test_dialogue_voice_gate.py
tests/unit/test_dialogue_voice_blocks.py
docs/CRAFT_EXAMPLES/dialogue_samples_per_archetype.md
templates/archetype_P1_folk_master.yaml
templates/archetype_P2_protagonist.yaml
... (9 个原型)
```

**修改**：

```
src/bestseller/domain/context.py
  ↳ 给 SceneWriterContextPacket 和 ChapterWriterContextPacket
    各加一个 dialogue_voice_block: str | None = None

src/bestseller/services/pipelines.py
  ↳ 在 P1 originality engine block injection 段落里
    增加 dialogue_voice_block 渲染逻辑

src/bestseller/services/drafts.py
  ↳ build_scene_draft_prompts 加 dialogue_voice_block 参数
  ↳ user_prompt 顶部插入位置 (timeline → scene → character → length → dialogue)
  ↳ block_attrs trace tuple 加 dialogue_voice_block

src/bestseller/services/retention_safety_gate.py
  ↳ 加 DIALOGUE_AI_FLAVOR_BLOCK_CODE
  ↳ 加入 AUTO_REPAIR_RETENTION_CODES
  ↳ evaluate_retention_safety 调 check_dialogue_voice

src/bestseller/services/character_role_gate.py 的 load_character_profiles
  ↳ 在解析 cast-and-promises.md 时同时解析 voice_dna 块
```

---

## 8. 对话门禁规约（DialogueVoiceGate Spec）

### 8.1 检测项

```python
@dataclass(frozen=True)
class DialogueVoiceFinding:
    severity: str  # "critical" | "high" | "info"
    code: str
    detail: str
    character: str | None
    line_index: int | None


def check_dialogue_voice(
    chapter_text: str,
    *,
    chapter_position: int,
    profiles: tuple[CharacterProfileWithVoice, ...],
) -> DialogueVoiceReport:
    """
    Run 7 dialogue voice checks.
    """
```

| # | 检测名 | 触发条件 | severity | code |
|---|---|---|---|---|
| 1 | forbidden_phrase_hit | 任何角色说出自己 forbidden_phrases 中任一词 | critical | DIALOGUE_FORBIDDEN_PHRASE |
| 2 | llm_default_phrase | 任何角色说出 LLM_DEFAULT_PHRASE_BLACKLIST | critical | DIALOGUE_LLM_DEFAULT |
| 3 | stage_direction_abuse | "冷冷一笑/淡淡地说/微微一笑" 等总数 ≥2 | high | DIALOGUE_STAGE_DIR_ABUSE |
| 4 | identifiability_low | 抽 3 段去标签对话, critic 识别准确率 < 0.8 | critical | DIALOGUE_NOT_IDENTIFIABLE |
| 5 | symmetric_rhythm | 连续 ≥4 回合长度方差 < 0.1 | high | DIALOGUE_PING_PONG |
| 6 | negative_space_missing | 整章 < 2 处「动作代回答 / 沉默 / 偏题」 | high | DIALOGUE_NO_NEGSPACE |
| 7 | body_tells_density_low | 有 ≥3 句的角色, body_tells 命中 < 1 | high | DIALOGUE_NO_BODY_TELL |

### 8.2 Critical 触发 auto-repair

```python
# retention_safety_gate.py
DIALOGUE_AI_FLAVOR_BLOCK_CODE = "DIALOGUE_AI_FLAVOR"

AUTO_REPAIR_RETENTION_CODES = (
    ...,
    DIALOGUE_AI_FLAVOR_BLOCK_CODE,
)
```

任一 critical finding 出现，整体 block code 标记为 DIALOGUE_AI_FLAVOR，进入自修复循环。

### 8.3 Identifiability 测试（关键检测）

这是**核心**检测项。算法：

```python
def _identifiability_test(chapter_text, profiles, sample_size=3):
    """
    1. 提取所有 ≥10 字的对话段, 按角色分组
    2. 随机抽 sample_size 个角色, 每个抽 1 段对话
    3. 去掉对话标签 ("X说" / "X道" / "X的声音")
    4. 用 critic LLM (haiku) 判断: 这句话像哪个角色?
    5. 准确率 < 0.8 → critical
    """
```

样例：

- 输入: `"先查张建军。"`（去标签）
- 候选: 林渊 / 钱婆婆 / 王建业 / 顾怀山
- 期望: critic 判定 林渊 (90%+)

如果 critic 把这句话判给钱婆婆，说明 voice 没区分度。

---

## 9. Prompt 注入设计（Prompt Block Layout）

### 9.1 在 drafts.py user_prompt 中的位置

```
[BLOCK 1] 时间锚白名单 (timeline_canon_block)
[BLOCK 2] 场景连贯门 (scene_coherence_block)
[BLOCK 3] 角色定位锁定 (character_role_block)
[BLOCK 4] 章节体量门 (chapter_length_block)
[BLOCK 5] ← 新增 → 对白声纹合同 (dialogue_voice_block)
[BLOCK 6] 前章钩子 (hook_echo_block)
... (existing blocks)
```

放在第 5 位是因为：必须在 LLM 决定**写哪些对话**之前就锁定声纹。

### 9.2 dialogue_voice_block 完整模板

```text
【对白声纹合同 — 本章每句对话必须遵守】

═══ 本章登场角色 ═══

◆ 林渊 (主角, 30 岁, 风水师)
  · 句长: 7-18 字, 句末压住
  · 必带物理标签: 拇指搭铜钱钱眼 / 罗盘不离手 / 阴阳眼烫
  · 口头禅 (本章至少出现 2 次): 先查 / 走 / 压一下 / 看一眼
  · 严禁出现: 我猜 / 也许吧 / 你说得对 / 总之 / 话说
  · 本章语境模式: 「执业中」
  · 黄金范例 (本章必须模仿其味道):
      「这镜子背后写了我父亲的名字。名字写在哪一边？」

◆ 钱婆婆 (民间记账人, 七十年代农村语感)
  · 句长: 5-12 字, 偶尔单字句
  · 句末粘字: 哩 / 呢 / 啵
  · 主语常省略 ("记你账上" 而非 "我记到你账上")
  · 必带物理标签: 舌头顶腮帮 / 袖口抹嘴角 / 不抬眼
  · 口头禅 (本章至少出现 1 次): 账 / 这笔 / 毛头 / 亏不亏
  · 严禁出现: 有意思 / 小子 / 原来如此 / 看来 / 果然
  · 本章语境模式: 「对林渊催账」
  · 黄金范例:
      「毛头，这笔亏不亏，看你怎么记。」

◆ 王建业 (江湖小商人, 套近乎)
  · 句长: 长, 多语气词 "呃 / 那啥"
  · 必带物理标签: 袖口擦汗 / 烟头掐到没烟丝
  · 口头禅: 您几位 / 老规矩 / 那啥
  · 严禁出现: 果然 / 总之 / 看来
  · 本章语境模式: 「演戏 / 假身份」
  · 黄金范例:
      「林师傅, 这单生意呃, 老规矩, 现金。」

═══ 跨角色硬禁忌 (任何一个出现 = 整章重写) ═══

▶ 词汇黑名单 (任何角色任何场景一律禁止):
  有意思 / 原来如此 / 难怪 / 怪不得 / 果然 / 果然如此
  你说得对 / 我懂了 / 我明白了 / 也许吧 / 我猜
  话说回来 / 总之 / 简而言之 / 不管怎样
  看来 / 看起来 / 似乎

▶ Stage direction 配额 (全章累计):
  "冷冷一笑 / 淡淡地说 / 微微一笑 / 苦笑" 总数 ≤ 1 次
  "握紧拳头 / 攥紧 / 深吸一口气" 总数 ≤ 2 次

▶ 对话节奏:
  禁止连续 4 个回合长度对称 (A 5字 B 5字 A 5字 B 5字)
  至少 30% 的对话回合采用以下之一:
    - 动作代回答 (例: "他把铜钱放下, 没说话。")
    - 沉默 (例: "片刻, 没人开口。")
    - 反问 (例: "你说的是几年前的事？")
    - 偏题 (例: "茶要凉了。")

▶ 词汇互斥:
  任意两个角色的 pet_phrase 集合不能有交集

═══ 对白潜台词要求 ═══

每个有 ≥3 句对话的角色, 至少 1 句对话需同时承担:
  Surface (表面在说什么)
  + Want (真正想要什么)
  + Hide (隐藏什么)

例: 钱婆婆 "这笔, 记你毛头头上。"
    Surface: 给林渊记账
    Want: 让林渊主动问 "亏什么"
    Hide: 她知道这是林家旧债, 不是林渊的

═══ Negative Space 要求 ═══

本章至少 2 处使用 negative space:
  - 动作代回答
  - 沉默 / 停顿
  - 反问 / 偏题
  - 半句中断
```

---

## 10. 落地实施计划（Implementation Roadmap）

按依赖顺序分 4 个 phase：

### Phase 1: 数据层（1-2 天）

| Task | 文件 | 输出 |
|---|---|---|
| P1.1 | `domain/dialogue_voice.py` | `@dataclass VoiceDNA, ContextModulation, NegativeSpaceRule` |
| P1.2 | `services/dialogue_voice_profile.py` | `load_voice_dna_from_cast(path) -> tuple[CharacterVoice, ...]` |
| P1.3 | `services/character_role_gate.py` | 修改 `load_character_profiles` 同时返回 voice_dna |
| P1.4 | `templates/archetype_P*.yaml` × 9 | 9 个原型模板 |
| P1.5 | `tests/unit/test_dialogue_voice_profile.py` | 解析测试 |

### Phase 2: 门禁层（2-3 天）

| Task | 文件 | 输出 |
|---|---|---|
| P2.1 | `services/dialogue_voice_gate.py` | 7 项检测 |
| P2.2 | `services/dialogue_voice_gate.py` | `_identifiability_test` (调 critic LLM) |
| P2.3 | `services/retention_safety_gate.py` | 加 DIALOGUE_AI_FLAVOR_BLOCK_CODE |
| P2.4 | `services/retention_safety_gate.py` | 接入 evaluate_retention_safety |
| P2.5 | `tests/unit/test_dialogue_voice_gate.py` | 7 项测试 + 真实 ch1 文本回归 |

### Phase 3: Prompt 层（1-2 天）

| Task | 文件 | 输出 |
|---|---|---|
| P3.1 | `services/dialogue_voice_blocks.py` | `render_dialogue_voice_block(profiles, ctx)` |
| P3.2 | `domain/context.py` | 给两个 ContextPacket 加 dialogue_voice_block 字段 |
| P3.3 | `services/pipelines.py` | 在 P1 originality 段注入 |
| P3.4 | `services/drafts.py` | 加参数、context budget、user_prompt 插入 |
| P3.5 | `services/drafts.py` | block_attrs trace tuple 加入 |
| P3.6 | `tests/unit/test_dialogue_voice_blocks.py` | 渲染测试 |

### Phase 4: 内容层（每本书一次, 0.5-1 天 / 书）

| Task | 文件 | 输出 |
|---|---|---|
| P4.1 | `output/<slug>/story-bible/cast-and-promises.md` | 给每个角色填 voice_dna 块 |
| P4.2 | `output/<slug>/voice-samples/<角色>.md` | 每个主要角色 3-5 段黄金对白 |
| P4.3 | 集成测试 | 触发 ch1-3 重生成验收 |

### Phase 5: 验收（0.5 天）

| Task | 输出 |
|---|---|
| P5.1 | 跑全套测试: gate / pipeline / drafts |
| P5.2 | 重生成《青囊》ch1-3 |
| P5.3 | 7 道门禁全绿 + 对话门禁全绿 |
| P5.4 | 人工抽检: 用户的 "钱婆婆 有意思 小子" 案例不能再出现 |

**总工作量预估**：约 5-8 个工作日 + 角色卡内容 1 天/书。

---

## 11. 测试与验收 Plan

### 11.1 单元测试矩阵

```
test_dialogue_voice_profile.py
  ├── test_parses_voice_dna_from_cast_md_yaml_block
  ├── test_missing_voice_dna_returns_empty_profile
  ├── test_archetype_inheritance_merges_correctly
  └── test_context_modulation_keys_validated

test_dialogue_voice_gate.py
  ├── test_forbidden_phrase_hit_triggers_critical
  ├── test_llm_default_phrase_triggers_critical
  ├── test_stage_direction_abuse_threshold
  ├── test_identifiability_low_critical (mock critic)
  ├── test_symmetric_rhythm_detection
  ├── test_negative_space_missing_high
  ├── test_body_tells_density_high
  ├── test_qiannang_ch1_real_text_caught   # 回归: 用户报告的钱婆婆案例
  └── test_clean_dialogue_passes_all_checks

test_dialogue_voice_blocks.py
  ├── test_render_block_includes_all_active_chars
  ├── test_render_block_omits_missing_voice_dna
  └── test_render_block_chinese_format
```

### 11.2 回归测试样本（关键）

把用户报告的两个对话 case 做成单测：

```python
def test_regression_qiannang_dialog_caught_as_ai_flavor():
    """
    用户原报 (2026-05-23):
    "记上了。" 钱婆婆的声音忽然带了点笑意，"有意思，小子。"

    Must be flagged with DIALOGUE_AI_FLAVOR (at least 2 criticals):
    - "有意思" in LLM blacklist
    - "小子" in 钱婆婆's forbidden_phrases
    """
    chapter_text = '"记上了。" 钱婆婆的声音忽然带了点笑意，"有意思，小子。"'
    profiles = (QIAN_POPO_PROFILE,)
    report = check_dialogue_voice(
        chapter_text, chapter_position=2, profiles=profiles,
    )
    assert report.has_critical
    codes = [f.code for f in report.findings]
    assert "DIALOGUE_LLM_DEFAULT" in codes
    assert "DIALOGUE_FORBIDDEN_PHRASE" in codes
```

### 11.3 集成测试 — 重生成《青囊》ch1-3

1. 填好 7 个角色的 voice_dna
2. 重置 ch1-3 status
3. 触发 chapter pipeline
4. 检查输出：
   - 钱婆婆不再说 "有意思" / "小子"
   - 王建业不再说 "果然" / "总之"
   - 林渊不再说 "我猜" / "也许吧"
   - 整章 negative space 段 ≥ 2
   - identifiability test 通过

### 11.4 人工抽检

让人读 5 段对话，去掉对话标签，能否识别说话人 ≥ 4/5（80%）。

---

## 12. 应用到《青囊不语问阴阳》— 7 角色 Voice DNA 全表

直接复制此表到 `output/exorcist-detective-1778051012/story-bible/cast-and-promises.md`，每个角色追加 `voice_dna:` 块：

### 12.1 林渊 (P2 主角原型)

```yaml
voice_dna:
  archetype: P2_protagonist
  register: 30 岁城市男, 风水师, 受过教育, 父亲失踪 3 年
  sentence_length_zh: [7, 18]
  syntax_quirks:
    - 否定句优先 ("这不是镜子的问题")
    - 反问当确认 ("是吗?")
    - 用专业词不解释
  pet_phrases:
    - 看一眼
    - 先查
    - 走
    - 压一下
  forbidden_phrases:
    - 我猜
    - 也许吧
    - 你说得对
    - 总之
    - 话说
    - 我懂了
  vocab_ceiling: 大学
  vocab_floor: 不爆粗口
  speech_speed: 偏快, 句末压住
  body_tells:
    - 拇指搭铜钱钱眼
    - 罗盘不离手
    - 右眼底烫一下
    - 怀里青囊秘卷
  taboo_topics:
    - 父亲到底是死是活
    - 自己阴阳眼怎么来的
    - 爷爷林家辉
  context_modulation:
    professional: {len: [7,12], pace: 中, sample: "这镜子背后写了我父亲的名字。"}
    pressed_by_father_voice: {len: [1,5], pace: 慢, sample: "先查张建军。"}
    probed: {len: [3,8], pace: 中, sample: "你说的是几年前的事？"}
    decisive: {len: [1,3], pace: 快, sample: "走。"}
  negative_space:
    - condition: 被问父亲
      response: 切到罗盘上 (动作代回答)
    - condition: 被恭维
      response: 反问 ("你说的是几年前的事？")
    - condition: 被威胁
      response: 沉默 (字数 < 3)
```

### 12.2 钱婆婆 (P1 民间高人原型)

```yaml
voice_dna:
  archetype: P1_folk_master
  register: 七十年代农村老一辈, 民间记账人
  sentence_length_zh: [5, 12]
  syntax_quirks:
    - 主语省略 ("记你账上")
    - 反问当陈述 ("这也算账？")
    - 末尾粘字 ("哩 / 呢 / 啵")
  pet_phrases:
    - 账
    - 这笔
    - 毛头
    - 亏不亏
    - 算
  forbidden_phrases:
    - 有意思
    - 小子
    - 原来如此
    - 看来
    - 果然
    - 后生
  vocab_ceiling: 初中以下
  vocab_floor: 不爆粗口, 可带方言土腥
  speech_speed: 慢
  body_tells:
    - 舌头顶腮帮
    - 袖口抹嘴角
    - 不抬眼
    - 拿铅笔在账本上戳
  taboo_topics:
    - 自己年轻时
    - 为什么收账
    - 丈夫怎么死的
  context_modulation:
    debt_pressure: {len: [3,7], pace: 慢, sample: "账上添一笔。"}
    to_lin_yuan: {len: [5,12], pace: 中, sample: "毛头, 这笔亏不亏。"}
    offended: {len: [1,4], pace: 极慢, sample: "再说一遍。"}
    memory_triggered: {len: [8,20], pace: 快, sample: "我那年也是七岁…算了。"}
    dying: {len: [1,6], pace: 极慢, sample: "林…正淳…的儿…"}
  negative_space:
    - condition: 被问年轻时
      response: 翻账本
    - condition: 被催
      response: 用更慢的语速
    - condition: 被夸
      response: 偏题 ("茶要凉了。")
```

### 12.3 王建业 (P3 江湖小商人原型)

```yaml
voice_dna:
  archetype: P3_jianghu_merchant
  register: 50 岁江湖旧货商人, 半江湖半市井
  sentence_length_zh: [10, 25]
  syntax_quirks:
    - 套近乎开场 ("林师傅 / 您几位")
    - 多语气词 ("呃 / 那啥 / 您看")
    - 反复确认 ("是不是?")
  pet_phrases:
    - 您几位
    - 老规矩
    - 那啥
    - 您看
    - 求您
  forbidden_phrases:
    - 果然
    - 总之
    - 看来
    - 原来如此
    - 难怪
  vocab_ceiling: 高中
  vocab_floor: 可带江湖黑话, 可吞字
  speech_speed: 快, 嘴像漏的
  body_tells:
    - 袖口擦汗
    - 烟头掐到没烟丝
    - 看眼神不正面对人
    - 笑得勉强
  taboo_topics:
    - 镜子真正的来路
    - 自己手腕上的勒痕
  context_modulation:
    pretending: {len: [10,20], pace: 快, sample: "林师傅, 这单生意呃, 老规矩, 现金。"}
    cornered: {len: [3,8], pace: 慢, sample: "您几位别这样, 真不是我。"}
    real_self: {len: [5,12], pace: 中, sample: "他们让我放的, 我哪知道这镜里有人。"}
  negative_space:
    - condition: 被追问真相
      response: 反复套近乎 ("您几位您几位")
    - condition: 被识破
      response: 苦笑 + 沉默
```

### 12.4 顾怀山 (P1 民间高人变体 — 收藏家版)

```yaml
voice_dna:
  archetype: P1_folk_master
  register: 60 岁民俗收藏家, 半江湖半雅
  sentence_length_zh: [4, 10]
  pet_phrases:
    - 收
    - 出
    - 价
    - 这玩意
    - 物件
  forbidden_phrases:
    - 有意思
    - 厉害
    - 不错
    - 看来
  body_tells:
    - 拇指蹭物件边角
    - 不戴老花镜看物件
    - 用茶水沾指尖
  taboo_topics:
    - 物件从哪来
    - 三年前是否见过林正淳
  context_modulation:
    pricing: {len: [3,6], pace: 慢, sample: "出价, 别废话。"}
    to_lin_yuan: {len: [5,10], pace: 中, sample: "你父亲的物件, 我收过。"}
  negative_space:
    - condition: 被问来源
      response: 倒茶
```

### 12.5 第八张脸 (P4 反派模仿原型)

```yaml
voice_dna:
  archetype: P4_mimic_villain
  register: 镜中怨物, 模仿对象的语调
  syntax_quirks:
    - 镜像句式 (说对方刚说过的话, 倒装一次)
    - 复制对手 pet_phrases 反过来用
    - 嘴形与说话不同步 (描写时必须提)
  pet_phrases: []  # 它没有自己的 — 用别人的
  forbidden_phrases:
    - 自己创造的任何新词
  body_tells:
    - 嘴形跟说话不同步
    - 镜面起雾
    - 声音来自不该来的方向
  context_modulation:
    mimicking_father: {use: "林正淳的 voice_dna 反着用"}
    mimicking_qian_popo: {use: "钱婆婆的 voice_dna 反着用"}
  negative_space:
    - condition: 被识破
      response: 笑声 + 镜面碎裂声
```

### 12.6 林正淳的声音 (回忆 / 鬼物模仿)

```yaml
voice_dna:
  archetype: P8_authority_elder
  register: 三年前的父亲 (前刑警, 后入镜)
  sentence_length_zh: [4, 10]
  pet_phrases:
    - 渊
    - 别看
    - 押后
    - 查
  forbidden_phrases:
    - 我儿子
    - 听爸爸的
    - 乖
  body_tells:
    - 只闻声不见人
    - 声音从镜内来
  context_modulation:
    professional_remnant: {len: [3,8], pace: 中, sample: "渊, 别看, 押后查。"}
  negative_space: []  # 鬼物无 negative space, 主动说
```

### 12.7 林家辉 (回忆 / 不出场, 仅旁人转述)

```yaml
voice_dna:
  archetype: P8_authority_elder
  register: 修复匠, 老派文人, 30 年前补镜
  sentence_length_zh: [8, 18]
  pet_phrases:
    - 这镜
    - 当年
    - 我那时候
    - 老规矩
  forbidden_phrases:
    - 手机
    - 微信
    - 现代任何科技词
  body_tells:
    - 拨铜钱声
    - 老花镜挂胸前
  taboo_topics:
    - 林远山
  context_modulation:
    elder_teaching: {len: [10,18], pace: 慢, sample: "这镜啊, 当年我那时候补过一次。"}
  negative_space:
    - condition: 被问林远山
      response: 喝茶
```

---

## 13. 关键风险与缓解（Risks）

| 风险 | 缓解 |
|---|---|
| LLM 无视 voice_dna prompt 块 | (1) 放在 user_prompt 顶部 (2) 用白名单不是黑名单 (3) 后写门禁兜底重写 |
| Identifiability test 误判 | (1) 用 critic LLM 多 sample 投票 (2) 准确率阈值留余地 (0.8 而非 1.0) |
| 角色 voice_dna 不够区分 | (1) 强制 Jaccard 互斥 (2) 同一原型最多 1 个角色 |
| 7 个角色填卡工作量大 | (1) 提供 9 个原型模板 (2) 继承式定义只填差异 |
| 旧章节文本不兼容 | (1) Gate 仅校验**新**章节 (2) 旧章节走人工 |

---

## 14. 后续扩展（v2.0+）

- **方言层**：粤语 / 川话 / 东北话的 syntax_quirks 库
- **时代层**：1980s / 1990s / 2000s / 2010s 的词汇时间感
- **关系演化**：随章节推进, 角色之间称呼自动演化（陌生 → 熟人 → 亲密）
- **多角色对话编排器**：超过 3 人同时对话时, 自动分配发言频率 / 抢话频率
- **跨章 voice drift detector**：同一角色在 ch5 和 ch50 是否声纹漂移

---

## 15. 待办清单（落地 TODO，建议作为 P0-F 系列加入主待办）

```
[ ] P0-F-1   扩展 cast-and-promises.md schema 加入 voice_dna 块
[ ] P0-F-2   新建 dialogue_voice_profile.py 解析器
[ ] P0-F-3   扩展 character_role_gate.load_character_profiles 同时输出 voice_dna
[ ] P0-F-4   新建 9 个原型 YAML 模板
[ ] P0-F-5   新建 dialogue_voice_gate.py (7 项检测 + identifiability LLM 调用)
[ ] P0-F-6   接 retention_safety_gate + AUTO_REPAIR_RETENTION_CODES
[ ] P0-F-7   新建 dialogue_voice_blocks.py (prompt 渲染)
[ ] P0-F-8   修 domain/context.py 加 dialogue_voice_block 字段
[ ] P0-F-9   修 pipelines.py 注入 dialogue_voice_block
[ ] P0-F-10  修 drafts.py user_prompt 顶部第 5 位插入
[ ] P0-F-11  单元测试: profile / gate / blocks (≥ 25 case)
[ ] P0-F-12  回归测试: 用户报告的「钱婆婆 有意思 小子」必须被 caught
[ ] P0-F-13  填《青囊》7 个角色 voice_dna 到 cast-and-promises.md
[ ] P0-F-14  写 voice-samples/ 每个主角色 3-5 段黄金对白
[ ] P0-F-15  重生成《青囊》ch1-3 + 7 道门禁 + 对话门禁全绿验收
[ ] P0-F-16  人工抽检: 5 段对话去标签识别准确率 ≥ 80%
```

---

## 16. 备查附录

### 16.1 完整 LLM 默认词黑名单（建议落代码常量）

```python
LLM_DEFAULT_PHRASE_BLACKLIST_FULL = (
    # 万用感叹
    "有意思", "原来如此", "难怪", "怪不得", "果然", "果然如此",
    # 万用应答
    "你说得对", "我懂了", "我明白了", "我知道了",
    # 万用犹豫
    "也许吧", "或许", "我猜", "我觉得", "我认为",
    # 万用过渡
    "话说回来", "总之", "简而言之", "不管怎样", "无论如何",
    # 万用观察
    "看来", "看起来", "似乎",
    # 万用 stage direction
    "他冷冷一笑", "他淡淡地说", "他不动声色", "她若有所思",
    "他冷冷开口", "他缓缓道", "他低声道", "她轻声道",
    # 万用情态
    "脸色一变", "眉头一皱", "嘴角一勾", "眼神一冷",
    # 万用气氛词
    "气氛凝重", "气氛骤然紧张", "空气仿佛凝固",
)
```

### 16.2 通用 stage direction 黑名单

```python
GENERIC_STAGE_DIRECTIONS = (
    "冷笑", "苦笑", "微微一笑", "笑了笑", "勾起嘴角", "嘴角一扬",
    "眼神冷峻", "目光如炬", "眼神复杂", "眼神冰冷", "目光闪烁",
    "握紧拳头", "紧握双手", "攥紧", "拳头紧握",
    "深吸一口气", "长舒一口气", "屏住呼吸",
    "皱眉", "蹙眉", "锁眉",
)
```

### 16.3 关键问答

**Q: 这套系统对英文小说也适用吗？**
A: 部分适用。register / pet_phrases / forbidden 通用；句长换成 word count；body_tells 通用；context_modulation 通用。需要的是再加一份 LLM_DEFAULT_PHRASE_BLACKLIST_EN（"interesting" / "I see" / "I understand" / "well" / "indeed" 等）。

**Q: 配 voice_dna 工作量大吗？**
A: 主角 ≈ 30 分钟（填 9 轴 + 5 个 context_modulation 样本）。次要角色 ≈ 10 分钟（继承原型 + 改 4-5 项）。一本书 7-10 个主要角色 ≈ 2-3 小时。

**Q: identifiability test 会不会很贵？**
A: 用 haiku 做 critic，每次调用 ≈ 0.001 USD。每章测 3 sample = 3 次调用 ≈ 0.003 USD。500 章一本书共 1.5 USD。可忽略。

**Q: 如何处理旁白 / 第三人称叙述？**
A: 本设计只管**直接引语对话**（引号内）。旁白的 voice 走另一个系统（VoiceDNA for author）。

---

**End of Design Document**

下一步：
1. 评审本文档 → 调整 voice_dna schema / archetypes
2. 通过后从 P0-F-1 开始按序落地
3. Phase 1-3 完成后做 Phase 4-5 验收

文件保存路径：`docs/DIALOGUE_VOICE_SYSTEM_DESIGN.md`
