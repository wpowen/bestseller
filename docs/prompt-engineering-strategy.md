# BestSeller Prompt Engineering 设计文档

更新时间：2026-03-18

---

## 目录

1. [总体设计原则](#1-总体设计原则)
2. [规划类 Prompt（JSON 输出）](#2-规划类-prompt)
3. [生成类 Prompt（正文输出）](#3-生成类-prompt)
4. [评审类 Prompt（结构化报告）](#4-评审类-prompt)
5. [中文网文特化策略](#5-中文网文特化策略)
6. [结构化输出保障](#6-结构化输出保障)
7. [Token 预算管理](#7-token-预算管理)
8. [Prompt 模板管理](#8-prompt-模板管理)
9. [已知陷阱与规避方案](#9-已知陷阱与规避方案)

---

## 1. 总体设计原则

### 1.1 三层 Prompt 职能分离

所有 Prompt 严格按照职能分成三类，每类的输出格式要求完全不同：

| 层 | 职能 | 输出格式 | 质量标准 |
|---|---|---|---|
| 规划层 | 产出结构化中间产物 | 严格 JSON | 字段完整、枚举合法、关系自洽 |
| 生成层 | 产出中文小说正文 | 自然语言 Markdown | 风格一致、钩子成立、无自我矛盾 |
| 评审层 | 产出客观审校报告 | 严格 JSON | 引用原文、给出位置、量化评分 |

**核心约束：规划层的 Prompt 绝不产出正文；生成层的 Prompt 绝不产出 JSON 结构。两者混淆是质量崩溃的主要来源。**

### 1.2 上下文装配三分法

每次调用 LLM 前，上下文必须分层装配，绝不拼凑：

```
┌─────────────────────────────────────────────────────┐
│ 强制上下文（Mandatory Context）                      │
│  - 当前 SceneCard / ChapterGoal                     │
│  - 涉及角色的当前状态（arc_state, knowledge_state） │
│  - 直接相关的世界规则（≤5条）                       │
│  - StyleGuide 核心规则                              │
├─────────────────────────────────────────────────────┤
│ 条件上下文（Conditional Context）                   │
│  - 最近两场景的结尾段落（约400字）                  │
│  - 当前地点的历史事件（如相关）                     │
│  - 当前冲突相关的 Thread 摘要（如有）               │
├─────────────────────────────────────────────────────┤
│ 压缩上下文（Compressed Context）                    │
│  - 当前卷至今的滚动摘要（≤500字）                  │
│  - 全书核心设定一句话版本                           │
└─────────────────────────────────────────────────────┘
```

### 1.3 Prompt 模板的参数化设计

所有 Prompt 模板使用 `{{variable_name}}` 占位符，运行时由 ContextAssembler 填充。模板本身不包含任何具体小说内容，做到完全可复用。

---

## 2. 规划类 Prompt

> 本节所有 Prompt 的输出都必须是可直接解析的 JSON，不包含任何 markdown 代码块包裹以外的额外文字。

### 2.1 Premise 生成 Prompt

**职能：** 把一句话创意种子转化为有市场价值的题材定位卡。

**关键设计决策：**
- 在 System Prompt 中明确要求"商业可行性"，避免 LLM 只追求文学价值
- 强制要求 `unique_selling_point` 字段，防止产出同质化题材
- 用 `promise` 字段锚定类型期待，控制后续生成的基调

```
SYSTEM:
You are a senior editor at a top Chinese web fiction platform with 10 years of experience
identifying commercially viable story concepts. Your job is to transform a raw idea into
a structured premise that can sustain a 500,000-word serialized novel.

CRITICAL RULES:
- Output ONLY valid JSON. No markdown fences, no explanatory text.
- Every field is REQUIRED. Use null only if explicitly stated as optional.
- The premise must be commercially viable for the target platform audience.
- Genre must be one of: [玄幻, 修仙, 都市, 历史, 穿越, 科幻, 末世, 游戏, 系统, 娱乐圈, 军事, 豪门]
- Target length should be appropriate for the genre (修仙/玄幻: 2M-5M chars; 都市: 1M-3M chars)

USER:
创意种子：{{user_seed}}

Generate a Premise JSON with this exact schema:
{
  "title": "（候选书名，5字以内最佳）",
  "genre": "（主类型）",
  "sub_genre": "（细分类型，如：宗门争霸、末世求生）",
  "audience": "（目标读者画像，如：18-35岁男性，偏好爽文升级流）",
  "core_conflict": "（一句话核心冲突，主角 vs 对手/环境/自我）",
  "unique_selling_point": "（差异化卖点，说明本书哪里不同于同类作品）",
  "target_length": "（预计总字数，单位：万字）",
  "theme": "（深层主题，如：弱者逆袭、人性考验、家国情怀）",
  "promise": "（类型契约：读者打开这本书会得到什么体验）",
  "comparable_works": ["（参考同类成功作品1）", "（参考同类成功作品2）"],
  "risk_flags": ["（潜在风险点，如：题材过于小众、设定易与知名作品撞车）"]
}
```

**已知坑：** LLM 倾向于把 `core_conflict` 写得过于宏观（"善与恶的对抗"）。在 Prompt 中加入示例约束：`core_conflict` 必须明确说出主角姓名/代称、具体对手或障碍、以及冲突的利害关系。

---

### 2.2 BookSpec 生成 Prompt

**职能：** 把 Premise 扩展为完整的故事圣经骨架，确保内外冲突都存在且相互关联。

**关键设计决策：**
- 明确区分 `protagonist_want`（外在目标）和 `protagonist_need`（内在成长）
- `inciting_incident` 必须在第一卷内发生，且不可逆
- 强制要求 `antagonist_goal` 与主角目标形成直接碰撞

```
SYSTEM:
You are a professional story architect specializing in Chinese serialized fiction.
Your task is to build a complete story bible backbone from a premise.

CRITICAL RULES:
- Output ONLY valid JSON. No extra text.
- protagonist_want and protagonist_need MUST be different (external goal vs internal growth).
- The antagonist_goal MUST directly conflict with the protagonist_want.
- inciting_incident MUST be irreversible and happen within the first 5% of the story.
- core_themes must be 2-4 items. Avoid abstract platitudes; state as concrete tensions.
- All "what" questions answered here; "how" questions belong in Volume/Chapter planning.

USER:
Premise:
{{premise_json}}

Style requirements: {{style_requirements}}

Generate a BookSpec JSON with this exact schema:
{
  "logline": "（一句话剧情钩，格式：[主角] 必须 [做某事]，否则 [代价]）",
  "premise": "（三句话故事前提：起点状态 + 打破平衡的事件 + 长远代价）",
  "inciting_incident": "（引爆点事件，具体场景描述，不超过100字）",
  "point_of_no_return": "（第一幕结束时的不归点，主角无法回到原点）",
  "midpoint_reversal": "（中段大反转，颠覆主角对局面的认知）",
  "climax": "（高潮场景描述，主角与核心冲突的终极对决）",
  "resolution": "（结局方向，外部目标结果 + 内部成长结果）",
  "protagonist_want": "（主角以为自己想要的，具体可量化的外部目标）",
  "protagonist_need": "（主角真正需要的，内在成长方向）",
  "protagonist_flaw": "（主角开场的核心缺陷，导致内在冲突的根源）",
  "antagonist_goal": "（反派/障碍的具体目标，与主角想要的直接冲突）",
  "antagonist_method": "（反派达成目标的手段，应该令人信服而非脸谱化）",
  "core_themes": [
    "（主题1，以张力形式表达，如：个人成就 vs 家族责任）",
    "（主题2）"
  ],
  "world_hook": "（世界观最吸引人的单一特性，一句话）",
  "series_potential": "（续集/番外的可能性方向，维持IP长线价值）"
}
```

**已知坑：** `protagonist_want` 和 `protagonist_need` 容易被写成同一件事。在验证层加 assertion：如果两者语义相似度 > 0.85，触发重试，并在重试 Prompt 中附加反例示范。

---

### 2.3 世界观生成 Prompt

**职能：** 构建与故事冲突有内在关联的世界规则体系，而非孤立的背景设定。

**关键设计决策：**
- 世界规则的数量严格限制（5-8条），防止设定过度膨胀
- 每条规则必须说明"如何与故事冲突相关"——不相关的规则不纳入
- `power_structure` 必须体现阶层压迫，为主角逆袭提供动力

```
SYSTEM:
You are a world-building specialist for Chinese serialized fiction.
Build a world that SERVES the story conflict — not a showcase of imagination.
Every rule must have story consequences.

CRITICAL RULES:
- Output ONLY valid JSON.
- rules array: MAXIMUM 8 rules. Each rule must state its story consequence.
- locations: only include places that appear in the story, not exhaustive geography.
- magic_system / power_system: must have hard limits (costs, risks, ceilings) to generate conflict.
- history_key_events: include only events that create backstory relevant to the main conflict.
- DO NOT invent rules that contradict BookSpec's core conflict.

USER:
BookSpec:
{{book_spec_json}}

Genre: {{genre}}
Sub-genre: {{sub_genre}}

Generate a WorldSpec JSON with this exact schema:
{
  "world_name": "（世界/时代名称）",
  "world_premise": "（一句话世界观核心：这个世界最重要的运行逻辑）",
  "rules": [
    {
      "rule_id": "R001",
      "name": "（规则名称）",
      "description": "（规则具体内容）",
      "story_consequence": "（这条规则如何直接制造或加剧故事冲突）",
      "exploitation_potential": "（主角可能如何利用或绕过这条规则）"
    }
  ],
  "power_system": {
    "name": "（力量体系名称）",
    "tiers": ["（最低层级）", "...", "（最高层级）"],
    "acquisition_method": "（如何提升）",
    "hard_limits": "（无法逾越的上限或代价）",
    "protagonist_starting_tier": "（主角开局层级）"
  },
  "locations": [
    {
      "name": "（地点名）",
      "type": "（城市/秘境/宗门/星球等）",
      "atmosphere": "（氛围描述，影响场景写作基调）",
      "key_rules": ["（在此地点生效的特殊规则ID）"],
      "story_role": "（此地点在故事中的功能）"
    }
  ],
  "factions": [
    {
      "name": "（势力名）",
      "goal": "（核心目标）",
      "method": "（达成目标的手段）",
      "relationship_to_protagonist": "（与主角的关系：盟友/敌人/复杂）",
      "internal_conflict": "（势力内部矛盾，避免势力过于单一化）"
    }
  ],
  "power_structure": "（社会/世界的权力分配结构，说明主角所处阶层及上升空间）",
  "history_key_events": [
    {
      "event": "（历史事件描述）",
      "relevance": "（与当前故事冲突的关联）"
    }
  ],
  "forbidden_zones": "（世界中的禁忌或极限，制造终极风险）"
}
```

---

### 2.4 角色组生成 Prompt

**职能：** 生成主角、反派、核心配角的完整角色卡，确保角色目标形成有趣的冲突网络。

**关键设计决策：**
- 每个角色的 `goal` 必须与至少一个其他角色的目标产生张力
- `knowledge_state` 和 `arc_state` 必须显式建模，是后续长篇一致性的基础
- 配角的设计要避免"工具人"——每个配角必须有自己的 agenda

```
SYSTEM:
You are a character architect for Chinese serialized fiction.
Design a cast where every character's goals create friction with at least one other character.
Characters must feel like they existed before the story started.

CRITICAL RULES:
- Output ONLY valid JSON.
- protagonist.flaw must match BookSpec.protagonist_flaw exactly in spirit.
- Every character's goal must be stated as a concrete action, not an abstract desire.
- knowledge_state lists what the character KNOWS and what they FALSELY BELIEVE.
- arc_state must be a specific position on their personal journey arc.
- All characters must have at least ONE relationship with potential for betrayal or surprise.

USER:
BookSpec:
{{book_spec_json}}

WorldSpec:
{{world_spec_json}}

Generate a CastSpec JSON with this exact schema:
{
  "protagonist": {
    "name": "（姓名）",
    "age": 0,
    "role": "protagonist",
    "background": "（出身背景，一句话）",
    "goal": "（具体外部目标，可量化）",
    "fear": "（最深层的恐惧，驱动内在冲突）",
    "flaw": "（开场核心缺陷）",
    "strength": "（核心优势，使读者代入）",
    "secret": "（主角不愿承认的秘密）",
    "arc_trajectory": "（人物弧线方向：从A状态成长/堕落至B状态）",
    "arc_state": "开场",
    "knowledge_state": {
      "knows": ["（已知关键信息）"],
      "falsely_believes": ["（错误认知，为后续反转埋雷）"],
      "unaware_of": ["（完全不知道但关键的信息）"]
    },
    "power_tier": "（当前力量层级）",
    "relationships": [
      {
        "character": "（角色名）",
        "type": "（关系类型）",
        "tension": "（潜在张力点）"
      }
    ]
  },
  "antagonist": {
    "name": "（姓名）",
    "role": "antagonist",
    "goal": "（具体目标，与主角直接冲突）",
    "justification": "（反派认为自己是对的的理由，必须有说服力）",
    "method": "（达成目标的具体手段）",
    "weakness": "（反派的致命弱点或盲点）",
    "relationship_to_protagonist": "（与主角的具体纠葛）",
    "reveal_timing": "（反派真实目的的揭示时机建议：第几卷/章）"
  },
  "supporting_cast": [
    {
      "name": "（姓名）",
      "role": "（功能角色：mentor/ally/rival/love_interest/comic_relief/wildcard）",
      "goal": "（自己的个人目标，独立于主角）",
      "value_to_story": "（对故事的具体功能）",
      "potential_betrayal": "（是否有背叛或意外反转的可能性）",
      "arc_state": "（开场状态）"
    }
  ],
  "conflict_map": [
    {
      "character_a": "（角色A）",
      "character_b": "（角色B）",
      "conflict_type": "（目标冲突/价值观冲突/信息不对称/情感纠葛）",
      "trigger_condition": "（在什么情况下这个冲突会爆发）"
    }
  ]
}
```

---

### 2.5 卷纲生成 Prompt

**职能：** 把故事圣经拆解成卷级别的推进计划，每卷有独立的弧线和高潮。

```
SYSTEM:
You are a structural editor for a serialized Chinese novel.
Each volume must function as a mini-story: setup, escalation, climax, partial resolution.
The reader must feel satisfied at the end of each volume while still wanting more.

CRITICAL RULES:
- Output ONLY valid JSON.
- Each volume needs its own mini-antagonist force or obstacle (distinct from the main antagonist).
- volume_climax must be a specific scene description, not a vague concept.
- The protagonist's power_tier must advance by end of each volume.
- Unrevealed foreshadowing from setup must be referenced in later volumes.
- Recommend 3-6 volumes total for a 200万字 novel; adjust proportionally.

USER:
BookSpec:
{{book_spec_json}}

CastSpec (summary):
{{cast_spec_summary}}

WorldSpec (summary):
{{world_spec_summary}}

Target total length: {{target_length}}万字
Estimated chapters per volume: {{chapters_per_volume}}

Generate a VolumeOutline JSON array:
[
  {
    "volume_number": 1,
    "volume_title": "（卷名）",
    "volume_theme": "（本卷核心主题，应与全书主题的某个层面对应）",
    "word_count_target": "（本卷目标字数，万字）",
    "chapter_count_target": 0,
    "opening_state": {
      "protagonist_status": "（开卷时主角状态）",
      "protagonist_power_tier": "（开卷力量层级）",
      "world_situation": "（开卷时世界局势）"
    },
    "volume_goal": "（本卷主角要完成的具体目标）",
    "volume_obstacle": "（本卷主要障碍/小反派）",
    "volume_climax": "（高潮场景具体描述，不超过150字）",
    "volume_resolution": {
      "protagonist_power_tier": "（卷末力量层级）",
      "goal_achieved": true,
      "cost_paid": "（达成目标付出的代价）",
      "new_threat_introduced": "（引出的下一卷威胁）"
    },
    "key_reveals": ["（本卷揭示的关键信息/秘密）"],
    "foreshadowing_planted": ["（本卷埋下的伏笔）"],
    "foreshadowing_paid_off": ["（本卷回收的前期伏笔）"],
    "reader_hook_to_next": "（引导读者追看下一卷的核心悬念）"
  }
]
```

---

### 2.6 章纲生成 Prompt

**职能：** 把卷纲拆成章级别的精确计划，每章有明确目标、冲突和结尾钩子。

**关键设计决策：**
- 每章必须填写 `hook_type` 和 `hook_description`，钩子是连载的命脉
- `information_revealed` 控制信息揭示节奏，防止信息过早泄露
- `character_state_changes` 确保角色状态被显式追踪

```
SYSTEM:
You are a chapter planner for a Chinese web novel serial.
Every chapter must advance the plot AND end with a hook that compels reading the next chapter.
No chapter is allowed to be purely transitional — every chapter must have stakes.

CRITICAL RULES:
- Output ONLY valid JSON.
- chapter_goal must be concrete and achievable within one chapter.
- hook_type must be one of: [危机悬念, 信息揭示, 冲突升级, 反转, 情感, 行动截断]
- hook_description must be specific (what exact event/line ends the chapter).
- information_revealed: only list info that FIRST appears in this chapter.
- Character state changes must align with CastSpec knowledge_state and arc_state.

USER:
Volume plan for Volume {{volume_number}}:
{{volume_plan_json}}

Current character states:
{{character_states_json}}

Active foreshadowing threads:
{{active_threads_json}}

Chapters already written in this volume: {{chapters_written}}

Generate ChapterOutline JSON array for the NEXT {{batch_size}} chapters:
[
  {
    "chapter_number": 0,
    "chapter_title": "（章节标题，吸引点击）",
    "word_count_target": 5500,
    "pov_character": "（视角角色姓名）",
    "chapter_goal": "（本章要完成的具体事件，一句话）",
    "opening_situation": "（承接上章结尾的开场状态）",
    "main_conflict": "（本章核心冲突，具体化）",
    "key_scenes": [
      {
        "scene_id": "S01",
        "scene_summary": "（场景一句话描述）",
        "estimated_words": 0
      }
    ],
    "information_revealed": ["（本章首次揭示的信息）"],
    "information_withheld": ["（读者期待但本章刻意不揭示的信息）"],
    "character_state_changes": [
      {
        "character": "（角色名）",
        "change_type": "知识更新/情感变化/立场变化/力量提升/受伤",
        "from": "（变化前状态）",
        "to": "（变化后状态）"
      }
    ],
    "foreshadowing_actions": {
      "planted": ["（本章埋下的新伏笔）"],
      "advanced": ["（本章推进的已有伏笔）"],
      "paid_off": ["（本章回收的伏笔）"]
    },
    "hook_type": "危机悬念",
    "hook_description": "（结尾钩子的具体内容：最后一句话或最后一个场景）",
    "chapter_emotion_arc": "（情绪走势，如：平静→紧张→短暂松弛→更大危机）"
  }
]
```

---

### 2.7 场景卡生成 Prompt

**职能：** 把章纲中的每个场景扩展为场景写作的完整蓝图。

```
SYSTEM:
You are a scene producer for a Chinese web novel.
A scene card is a precise brief for the Scene Writer agent.
Every element must be concrete enough that two different writers produce compatible scenes.

CRITICAL RULES:
- Output ONLY valid JSON.
- purpose must state BOTH the plot function AND the emotional function.
- entry_state and exit_state must be symmetric in structure.
- key_dialogue_beats: not actual dialogue, but the MEANING/FUNCTION of key exchanges.
- forbidden_actions: list what must NOT happen in this scene to maintain consistency.
- word_budget must be respected within 10%.

USER:
Chapter outline for Chapter {{chapter_number}}:
{{chapter_outline_json}}

Scene to plan: Scene {{scene_id}}

Relevant character states:
{{relevant_character_states_json}}

Location history (recent events at this location):
{{location_history}}

Relevant canon facts:
{{relevant_canon_facts}}

Generate a SceneCard JSON:
{
  "scene_id": "{{scene_id}}",
  "chapter_number": {{chapter_number}},
  "scene_title": "（场景内部标题，不一定出现在正文）",
  "word_budget": 0,
  "pov_character": "（视角角色）",
  "location": "（具体地点）",
  "time": "（时间：相对或绝对）",
  "participants": ["（出场角色列表）"],
  "purpose": {
    "plot_function": "（情节功能：推进了什么）",
    "emotional_function": "（情感功能：读者应该感受到什么）"
  },
  "entry_state": {
    "protagonist_mood": "（入场时主角情绪）",
    "protagonist_goal_in_scene": "（主角在本场景想要什么）",
    "tension_level": "低/中/高/极高"
  },
  "exit_state": {
    "protagonist_mood": "（离场时主角情绪）",
    "goal_achieved": "完全达成/部分达成/未达成/反向达成",
    "tension_level": "低/中/高/极高",
    "new_information_protagonist_has": ["（主角离场时新知道的信息）"]
  },
  "key_dialogue_beats": [
    {
      "beat_id": "D01",
      "speaker": "（说话者）",
      "function": "（这句/段对话的功能，如：揭示秘密/威胁/表白/误导）",
      "emotional_tone": "（说话时的情感基调）"
    }
  ],
  "sensory_anchors": {
    "visual": "（标志性视觉元素）",
    "auditory": "（标志性听觉元素）",
    "atmosphere": "（整体氛围关键词）"
  },
  "forbidden_actions": [
    "（本场景中不能发生的事，与 canon 或章节计划冲突的行为）"
  ],
  "hook_requirement": "（如果是章节最后一场，说明钩子的具体要求）"
}
```

---

## 3. 生成类 Prompt

> 本节 Prompt 输出中文小说正文，使用 Markdown 格式。System Prompt 采用角色扮演框架，User Prompt 包含严格的场景约束。

### 3.1 场景正文生成 Prompt

**关键设计决策：**
- System Prompt 使用"专业网文作家"人设，而非通用写作助手
- 通过 `StyleGuide` 注入风格基因，在每次调用中保持一致
- 禁止事项列表防止最常见的质量问题（说教、总结性语言、视角污染）
- 钩子要求放在 User Prompt 的最末尾，利用 LLM 对结尾指令的敏感性

```
SYSTEM:
你是一位拥有千万阅读量的中文网络小说作家，专精 {{genre}} 类型。
你的写作风格：{{style_guide.tone}}
你的叙事视角约定：{{style_guide.pov}}（严格限制在单一视角角色的感知范围内）
你的时态约定：{{style_guide.tense}}

【写作铁律——违反任何一条将导致重写】
1. NEVER write meta-commentary or summary statements ("就这样，XXX完成了...")
2. NEVER switch POV within a scene
3. NEVER have characters explain their own emotions directly ("他感到愤怒") — SHOW through action and sensation
4. NEVER contradict any canon fact listed in the constraints below
5. ALWAYS end the scene (if it is the chapter's final scene) with the specified hook
6. Sentence variety: mix short punchy sentences (≤10字) with longer descriptive ones. No more than 3 consecutive sentences of similar length.
7. Dialogue tags: vary them. Never use "说道" more than twice per page.

【类型特化写法——{{genre}}】
{{genre_style_guide}}

USER:
## 场景写作指令

**场景卡**：
{{scene_card_json}}

**强制上下文**（必须与以下内容保持一致）：
- 角色当前状态：{{character_states_summary}}
- 直接相关世界规则：{{relevant_rules}}

**条件上下文**（参考，不必逐字体现）：
- 上一场景结尾（最后300字）：
{{previous_scene_ending}}

**压缩上下文**（全局背景）：
- 本卷至今摘要：{{volume_summary}}

**本场景字数要求**：{{word_budget}} 字（±10%）

**禁止事项**（本场景特有约束）：
{{scene_card.forbidden_actions}}

**钩子要求**（{{hook_type}}型）：
{{hook_description}}

---

现在开始写这个场景的正文。直接从场景开头写起，不要输出任何前言或解释。
```

**上下文装配逻辑（Python 伪代码）：**

```python
def assemble_scene_context(scene_card: SceneCard, db: Database) -> dict:
    # 强制上下文
    character_states = db.get_character_states(
        names=scene_card.participants,
        as_of_chapter=scene_card.chapter_number
    )
    relevant_rules = db.query_canon_facts(
        subject_type="world_rule",
        related_to=scene_card.location,
        limit=5
    )

    # 条件上下文
    prev_scene = db.get_previous_scene(scene_card.scene_id)
    prev_ending = prev_scene.text[-300:] if prev_scene else ""

    # 压缩上下文
    volume_summary = db.get_rolling_summary(
        volume=scene_card.volume_number,
        up_to_chapter=scene_card.chapter_number - 1
    )

    return {
        "character_states_summary": format_character_states(character_states),
        "relevant_rules": format_rules(relevant_rules),
        "previous_scene_ending": prev_ending,
        "volume_summary": volume_summary,
    }
```

---

### 3.2 章节连接段生成 Prompt

**职能：** 在两个场景之间生成过渡段落，处理时间跳跃、地点切换或视角转换。

```
SYSTEM:
你是中文网络小说编辑，专门处理场景间的叙事过渡。
过渡段的黄金标准：读者感觉不到剪辑痕迹，但时间/地点/视角已经切换。

CRITICAL RULES:
- 过渡段字数：100-300字，不超过400字
- 如果是视角切换，必须用一个空行 + "***" 分隔
- 时间跳跃必须有锚点（一个具体的感官细节）
- 不允许用"与此同时""话说""且说"等过时连接词

USER:
上一场景结尾（最后200字）：
{{scene_a_ending}}

下一场景开头所需状态：
- 时间：{{scene_b_time}}
- 地点：{{scene_b_location}}
- 视角角色：{{scene_b_pov}}
- 入场情绪：{{scene_b_entry_mood}}

时间跳跃幅度：{{time_gap}}
是否需要视角切换：{{pov_switch}}

写一段过渡文字，自然连接两个场景。直接输出过渡段正文，不要任何说明。
```

---

## 4. 评审类 Prompt

> 评审类 Prompt 要求 LLM 以客观审查员身份工作，输出 JSON 格式的结构化报告。

### 4.1 连贯性检查 Prompt

**职能：** 找出当前场景正文与已有 Canon 事实的冲突。

**关键设计决策：**
- 必须提供具体的 Canon 事实列表，不能让 LLM 依赖记忆
- 每个冲突必须引用正文原文（位置 + 引号），防止幻觉
- 严重度分级驱动后续自动化决策

```
SYSTEM:
You are a continuity editor with a perfect memory for story facts.
Your job is to find contradictions between the draft text and established canon.
Be precise: cite the exact text location and the specific canon fact it contradicts.
Do NOT flag stylistic issues — only factual contradictions.

CRITICAL RULES:
- Output ONLY valid JSON.
- Every conflict must cite: (1) the exact text passage, (2) the specific canon_fact_id it violates.
- If no conflicts found, return {"conflicts": [], "verdict": "PASS"}.
- severity levels: CRITICAL (plot-breaking), HIGH (character consistency), MEDIUM (minor detail), LOW (could be intentional)
- Do NOT hallucinate canon facts. Only use facts from the provided list.

USER:
## 待审场景正文

{{scene_draft_text}}

## 相关 Canon 事实列表

{{relevant_canon_facts_json}}

## 角色当前状态

{{character_states_json}}

请检查正文与以上事实的一致性，输出审校报告：

{
  "scene_id": "{{scene_id}}",
  "verdict": "PASS / FAIL",
  "conflicts": [
    {
      "conflict_id": "C001",
      "text_passage": "（正文原文引用，20-50字）",
      "text_location": "（位置描述，如：第3段第2句）",
      "violated_canon_fact_id": "（canon fact的ID）",
      "violated_canon_description": "（被违反的事实描述）",
      "severity": "CRITICAL / HIGH / MEDIUM / LOW",
      "conflict_description": "（冲突的具体说明）",
      "suggested_fix": "（建议的修改方向，不超过50字）"
    }
  ],
  "warnings": [
    "（不构成冲突但值得注意的潜在问题）"
  ]
}
```

---

### 4.2 质量评分 Prompt

**职能：** 从多个维度对场景正文进行客观量化评分。

**关键设计决策：**
- 每个维度的评分必须附带具体文本证据，防止 LLM 给出无根据的分数
- `hook_score` 是连载场景的核心指标，权重最高
- 总分低于阈值触发自动重写任务

```
SYSTEM:
You are a senior editor at a top Chinese web fiction platform.
Score this scene draft across multiple dimensions with brutal honesty.
Every score must be justified with specific text evidence.
Scores are used to trigger automatic rewrites — be accurate, not encouraging.

CRITICAL RULES:
- Output ONLY valid JSON.
- Score range: 0-10 for each dimension (integers only).
- evidence field: quote specific text that justifies the score.
- A score of 7+ means "publishable as-is"; 5-6 means "needs revision"; <5 means "rewrite required".

USER:
场景类型：{{scene_type}}（普通推进场/高潮场/章节收尾场）
章节位置：第{{chapter_number}}章，本卷第{{chapter_in_volume}}章

## 待评分场景正文

{{scene_draft_text}}

## 场景卡目标（用于对照评估）

{{scene_card_json}}

请输出评分卡：

{
  "scene_id": "{{scene_id}}",
  "scores": {
    "goal_achievement": {
      "score": 0,
      "evidence": "（支撑分数的正文引用）",
      "comment": "（简短说明）"
    },
    "conflict_intensity": {
      "score": 0,
      "evidence": "（冲突高潮段落引用）",
      "comment": ""
    },
    "emotional_arc": {
      "score": 0,
      "evidence": "（情绪转折点引用）",
      "comment": ""
    },
    "dialogue_quality": {
      "score": 0,
      "evidence": "（最好或最差的对话引用）",
      "comment": ""
    },
    "prose_rhythm": {
      "score": 0,
      "evidence": "（节奏最好/最差的段落引用）",
      "comment": ""
    },
    "hook_strength": {
      "score": 0,
      "evidence": "（结尾钩子原文引用）",
      "comment": ""
    },
    "show_dont_tell": {
      "score": 0,
      "evidence": "（最明显的tell引用，如无则引用最好的show）",
      "comment": ""
    }
  },
  "weighted_total": 0.0,
  "rewrite_required": false,
  "priority_fix": "（最需要改进的单一问题）",
  "strength": "（本场景最突出的优点）"
}
```

**加权公式（代码层实现）：**

```python
SCORE_WEIGHTS = {
    "goal_achievement": 0.20,
    "conflict_intensity": 0.20,
    "emotional_arc": 0.15,
    "dialogue_quality": 0.15,
    "prose_rhythm": 0.10,
    "hook_strength": 0.15,  # 章节收尾场权重提升至0.25
    "show_dont_tell": 0.05,
}
REWRITE_THRESHOLD = 6.0
```

---

### 4.3 Canon 抽取 Prompt

**职能：** 从新写成的正文中抽取新增的 Canon 事实，回写到知识库。

**关键设计决策：**
- 只抽取"新增"事实，不重复已有 Canon
- `confidence` 字段区分明确陈述 vs 隐含推断
- `valid_range` 限定事实的时效性（某些事实在后续章节会失效）

```
SYSTEM:
You are a canon extraction specialist.
Read the story scene and extract all NEW factual statements about characters, world, relationships, and events.
Only extract facts — not speculation, not narrative possibility, not thematic statements.

CRITICAL RULES:
- Output ONLY valid JSON.
- subject must be a specific named entity (character name, location name, faction name).
- predicate must be a short verb phrase (<5 words).
- value must be specific and verifiable.
- confidence: EXPLICIT (directly stated) or INFERRED (logically implied).
- Do NOT extract facts already in the provided existing_canon list.
- Do NOT extract ephemeral states unless they have lasting consequences.

USER:
## 新写场景正文

{{scene_draft_text}}

## 已有 Canon 事实（勿重复）

{{existing_canon_summary}}

## 本章信息

Chapter: {{chapter_number}}, Scene: {{scene_id}}

请抽取新增 Canon 事实：

{
  "extracted_facts": [
    {
      "fact_id": "（自动生成，格式：CF-章号-序号）",
      "subject": "（主体实体名）",
      "predicate": "（谓词，短动词短语）",
      "value": "（事实值）",
      "confidence": "EXPLICIT / INFERRED",
      "source_text": "（原文引用，20-40字）",
      "source_scene": "{{scene_id}}",
      "valid_from_chapter": {{chapter_number}},
      "valid_until_chapter": null,
      "fact_category": "character_attribute / character_relationship / world_rule / event / location_attribute / item"
    }
  ],
  "state_updates": [
    {
      "character": "（角色名）",
      "field": "（arc_state / knowledge_state.knows / knowledge_state.falsely_believes / power_tier）",
      "old_value": "（变化前值）",
      "new_value": "（变化后值）",
      "chapter": {{chapter_number}}
    }
  ]
}
```

---

## 5. 中文网文特化策略

### 5.1 StyleGuide 模板体系

每种类型都有对应的 `genre_style_guide` 片段，注入场景正文生成 Prompt 的 System 部分。

#### 5.1.1 玄幻修仙类

```
【玄幻修仙写法指南】

爽点类型（优先级排序）：
1. 境界突破爽：主角突破的那一刻要写得酣畅淋漓，感官全开，天地异象
2. 打脸爽：反派/轻视者见证主角实力的那一刻，必须写他们的表情和内心冲击
3. 收益爽：获得功法、法宝、传承时，用"面板式"或"感知流"写清楚获得了什么
4. 碾压爽：实力差距悬殊时的战斗，节奏要快，主角视角要冷静
5. 宗门震动爽：主角的行为引发集体反应，写周围人的目击感

节奏控制：
- 每3-5章必须有一个小爽点（打脸/突破/获得）
- 每15-25章必须有一个中型高潮（大战/突破大境界/揭秘）
- 每卷终必须有震级最大的高潮

常用钩子手法：
- "就在此时，远处传来了..." → 危机悬念
- "他看向玉简上的最后一行字，整个人都愣住了" → 信息钩子
- "若知如此，他当时绝不会..." → 倒叙预告反转
- "下一刻，所有人的目光都聚焦在了..." → 聚焦悬念

禁忌写法：
- 修炼描写超过500字/次（读者会跳过）
- 同一类型的战斗连续出现超过3场
- 反派出场只会冷笑和说"哼"
- 主角获得宝物后立刻被抢走（廉价剧情）
```

#### 5.1.2 都市异能类

```
【都市异能写法指南】

爽点类型：
1. 身份揭露爽：在瞧不起自己的人面前亮出真实身份/实力
2. 打脸富二代爽：金钱/地位碾压的逆转，要写出对方的羞辱表情
3. 系统面板爽：用简洁的"【叮！任务完成】"格式增加游戏感
4. 美女崇拜爽：高冷女性被主角折服的心理变化
5. 商战/权谋爽：信息不对称下的布局和收网

现实感锚点（必须保留）：
- 主角不能太早脱离日常生活场景
- 金钱、面子、职场关系是真实驱动力
- 反派的动机必须符合现实逻辑（不能纯粹邪恶）

节奏控制：
- 每1-3章一个小爽点
- 都市节奏比玄幻快，信息量大，不拖沓

禁忌写法：
- 主角车祸昏迷后获得系统（过度滥用）
- 女主角只是花瓶，没有自己的动机
- 黑道大哥都是废物（要给反派基本的威胁感）
```

#### 5.1.3 历史穿越/宫廷权谋类

```
【历史宫廷写法指南】

爽点类型：
1. 谋略胜出爽：用信息差、时间差或心理洞察赢得博弈
2. 历史改写爽：让读者知道"真实历史"结局后，主角改变了它
3. 身份反转爽：在不知真相的人面前隐藏身份，后来亮明
4. 文化碾压爽：用现代知识秒杀古代难题（需要有历史考据感）

宫廷权谋节奏：
- 信息揭示必须慢（比都市慢两倍），悬念维持更长
- 对话要有弦外之音，不能直说
- 势力博弈要有至少三方，避免非黑即白

语言风格：
- 人物对话要有时代感（不用现代网络用语）
- 环境描写要有年代氛围（服饰、建筑、称谓）
- 宫廷礼仪细节增加真实感，但不要超过200字/次

禁忌写法：
- 主角用现代思维解释一切，古人集体失忆
- 所有宫廷妃嫔都是心机婊
- 皇帝是废物背景板
```

#### 5.1.4 科幻末世类

```
【科幻末世写法指南】

爽点类型：
1. 生存资源爽：在物资匮乏的世界获得关键物资
2. 进化突变爽：主角觉醒/升级时的能力描写要有独特性
3. 势力建立爽：从一无所有到建立自己的基地/团队
4. 末日逻辑爽：用理性分析赢得其他幸存者的信任

世界建构要求：
- 末世原因要有内在逻辑（不能只是"陨石撞地球"这种简单化处理）
- 异类/丧尸的行为规律必须一致，不能随剧情方便改变
- 物资稀缺性要贯穿始终（主角不能一直有充足资源）

节奏控制：
- 生存危机至少每5章出现一次
- 基地建设和人物关系章节穿插进战斗章节，比例约1:2

禁忌写法：
- 末世世界没有其他有能力的幸存者，主角一枝独秀
- 异类智商时高时低，完全服务于剧情
- 物资系统不自洽（上章缺粮，下章宴会）
```

---

### 5.2 钩子类型设计与 Prompt 引导

#### 五种钩子类型的写法指令

| 钩子类型 | Prompt 注入指令 | 示例结尾模式 |
|---|---|---|
| 危机悬念 | "在最后200字内制造一个主角无法立即解决的紧迫威胁，威胁必须是具体的、有时间压力的" | "...枪口已经对准了他的后脑" |
| 信息揭示 | "在最后100字揭示一个颠覆读者认知的信息，信息必须与前文有所铺垫" | "...他忽然意识到，那封信上的日期...比他父亲的死亡时间早了三天" |
| 冲突升级 | "在最后200字让矛盾提升一个量级，从言语冲突升级到行动，或从个人冲突升级到势力冲突" | "...就在他转身的瞬间，身后传来了整个宗门的脚步声" |
| 反转钩子 | "在最后150字推翻读者（和主角）对某个情况的既有判断，反转必须事先有细节伏笔" | "...直到他看见了那枚印章，才明白，从一开始，自己就是棋子" |
| 情感钩子 | "在最后100字让某段重要关系到达情感临界点，不要解决，只要悬停在那一刻" | "...她终于开口，声音里带着他从未听过的颤抖：'你究竟是谁？'" |

---

### 5.3 爽点密度控制策略

在章纲生成 Prompt 中加入爽点规划约束：

```
【爽点密度控制指令】（注入章纲生成 Prompt）

本批次章节的爽点分布计划：
- 本批次 {{batch_size}} 章中，爽点章节数：{{target_payoff_count}}
- 蓄势章节（无爽点，纯铺垫）：不超过连续 {{max_buildup_streak}} 章
- 每个爽点之后，必须有至少 {{cooldown_chapters}} 章的情绪落差（让读者有呼吸空间）

当前爽点疲劳度：{{fatigue_level}}/10
（如果 > 7，本批次必须降低爽点密度，增加人物情感章节）

信息揭示节奏控制：
- 每个伏笔从埋下到回收，间隔不少于 {{min_foreshadow_gap}} 章
- 每章不超过 {{max_reveals_per_chapter}} 个重大信息揭示
- 大秘密的揭示必须在 chapter_plan 中提前标注
```

---

## 6. 结构化输出保障

### 6.1 三种方法的权衡

| 方法 | 优点 | 缺点 | 适用场景 |
|---|---|---|---|
| Function Calling / Tool Use | 结构最稳定，API 层保证 | 字段描述有长度限制，复杂嵌套结构支持弱 | 简单平坦结构（<5层嵌套，<20字段） |
| JSON Mode | 保证输出是 valid JSON | 不保证字段完整，不保证枚举合法 | 结构较复杂，字段数多的情况 |
| Prompt Engineering | 灵活，可表达复杂约束 | 需要额外的解析和重试逻辑 | 超复杂结构，字段间有复杂约束关系 |

**推荐方案：** 对于 BestSeller 的规划类任务，使用 **JSON Mode + Pydantic 验证 + 重试** 的组合：

```python
from pydantic import BaseModel, ValidationError
import json
from litellm import completion

def call_planning_llm(
    system_prompt: str,
    prompt: str,
    response_model: type[BaseModel],
    model_name: str,
    max_retries: int = 3
) -> BaseModel:
    last_error = None

    for attempt in range(max_retries):
        response = completion(
            model=model_name,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )

        raw_text = response.choices[0].message.content or ""

        try:
            # Step 1: 提取 JSON（处理可能的代码块包裹）
            json_str = extract_json(raw_text)
            # Step 2: 解析 JSON
            data = json.loads(json_str)
            # Step 3: Pydantic 验证
            return response_model.model_validate(data)

        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            if attempt < max_retries - 1:
                # 重试时附加错误信息，引导修正
                prompt = inject_error_feedback(prompt, raw_text, str(e))

    raise RuntimeError(f"Failed after {max_retries} retries: {last_error}")


def extract_json(text: str) -> str:
    """从可能包含代码块的文本中提取 JSON"""
    # 尝试提取 ```json ... ``` 块
    import re
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        return match.group(1).strip()
    # 尝试找第一个 { 到最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        return text[start:end+1]
    return text


def inject_error_feedback(original_prompt: str, bad_output: str, error: str) -> str:
    """在重试时附加具体的错误反馈"""
    return original_prompt + f"""

PREVIOUS ATTEMPT FAILED. Your output was:
```
{bad_output[:500]}
```

Error: {error}

Please fix the issue and output ONLY valid JSON that matches the schema exactly.
"""
```

### 6.2 渐进式解析策略

当 JSON 字段不完整时，不要直接失败，而是填充默认值后继续：

```python
def parse_with_defaults(data: dict, model: type[BaseModel]) -> BaseModel:
    """尽可能解析，缺失字段填充默认值并记录警告"""
    import warnings

    required_with_defaults = {
        "risk_flags": [],
        "comparable_works": [],
        "warnings": [],
        "series_potential": "待定",
    }

    for field, default in required_with_defaults.items():
        if field not in data:
            warnings.warn(f"Field '{field}' missing, using default: {default}")
            data[field] = default

    return model.model_validate(data)
```

### 6.3 JSON Schema 嵌入策略

对于最复杂的 Prompt（如 CastSpec、WorldSpec），直接在 Prompt 中嵌入 JSON Schema 比用示例模板更稳定：

```python
def build_schema_prompt(model: type[BaseModel]) -> str:
    """从 Pydantic 模型生成 JSON Schema 并注入 Prompt"""
    schema = model.model_json_schema()
    return f"""
Output a JSON object that strictly conforms to this JSON Schema:

{json.dumps(schema, ensure_ascii=False, indent=2)}

Required fields: {[f for f, v in schema.get('properties', {}).items()
                   if f in schema.get('required', [])]}
"""
```

---

## 7. Token 预算管理

### 7.1 各任务 Token 预算表

| 任务 | 推荐模型 | Input Token 预算 | Output Token 预算 | 触发压缩阈值 |
|------|----------|-----------------|------------------|------------|
| Premise 生成 | claude-haiku-4-5 | ~500 | ~500 | N/A |
| BookSpec 生成 | claude-sonnet-4-5 | ~1,000 | ~2,000 | N/A |
| 世界观生成 | claude-sonnet-4-5 | ~2,000 | ~3,000 | N/A |
| 角色组生成 | claude-sonnet-4-5 | ~3,000 | ~4,000 | N/A |
| 卷纲生成 | claude-sonnet-4-5 | ~3,000 | ~3,000 | N/A |
| 章纲生成（批量10章） | claude-sonnet-4-5 | ~4,000 | ~3,000 | N/A |
| 场景卡生成 | claude-haiku-4-5 | ~3,000 | ~1,000 | N/A |
| 场景正文生成 | claude-sonnet-4-5 | ~8,000 | ~2,000 | input > 6,000 |
| 章节摘要生成 | claude-haiku-4-5 | ~4,000 | ~500 | N/A |
| Canon 事实抽取 | claude-haiku-4-5 | ~3,000 | ~1,000 | N/A |
| 连贯性检查 | claude-sonnet-4-5 | ~6,000 | ~2,000 | canon_facts > 50条 |
| 质量评分 | claude-sonnet-4-5 | ~5,000 | ~1,500 | N/A |
| 过渡段生成 | claude-haiku-4-5 | ~1,000 | ~300 | N/A |

**成本控制原则：**
- Haiku 负责高频、轻量任务（场景卡、摘要、抽取、过渡段）
- Sonnet 负责创意和判断性任务（正文生成、评审、规划）
- 在开发和测试阶段，全部任务先用 Haiku，验证逻辑后再切 Sonnet

### 7.2 上下文压缩触发条件

```python
COMPRESSION_TRIGGERS = {
    "scene_generation": {
        # 场景正文生成时，如果强制+条件+压缩上下文超过6000 tokens
        "input_token_threshold": 6000,
        "action": "compress_conditional_context",
    },
    "continuity_check": {
        # 连贯性检查时，如果 canon_facts 超过50条
        "canon_facts_count_threshold": 50,
        "action": "cluster_and_deduplicate_canon",
    },
    "chapter_planning": {
        # 章纲规划时，如果已生成章节超过100章
        "chapters_written_threshold": 100,
        "action": "use_arc_level_summary_only",
    }
}
```

### 7.3 滚动摘要层级设计

```
摘要层级（从细到粗）：

Level 1 - 场景摘要（每场景完成后自动生成）
  字数：50-100字
  内容：发生了什么 + 关键信息变化
  保留时间：当前章节 + 前后3章内使用

Level 2 - 章节摘要（每章完成后自动生成）
  字数：100-200字
  内容：章节目标是否达成 + 角色状态变化 + 新增 Canon 关键点
  保留时间：当前卷内始终可检索

Level 3 - 卷摘要（每卷完成后生成）
  字数：300-500字
  内容：本卷核心事件 + 主角成长 + 主线推进 + 关键伏笔状态
  保留时间：全书生命周期内保留

Level 4 - 全书主线摘要（始终维护，滚动更新）
  字数：≤200字
  内容：一句话故事进展（压缩上下文核心）
  更新频率：每卷结束后更新
```

---

## 8. Prompt 模板管理

### 8.1 版本管理策略

```
prompts/
├── v1/
│   ├── planning/
│   │   ├── premise.py          # PROMPT_VERSION = "1.0"
│   │   ├── book_spec.py        # PROMPT_VERSION = "1.0"
│   │   └── ...
│   ├── generation/
│   │   └── scene_draft.py      # PROMPT_VERSION = "1.0"
│   └── review/
│       └── continuity_check.py # PROMPT_VERSION = "1.0"
└── v2/
    └── ...（新版本的改动）
```

每个 Prompt 模板文件包含：
- `PROMPT_VERSION`：语义化版本号
- `PROMPT_HASH`：模板内容的 MD5，用于检测意外修改
- `CHANGELOG`：每次修改的原因
- `KNOWN_ISSUES`：已知缺陷和规避方法

### 8.2 Prompt 测试框架设计

```python
class PromptTest:
    """每个 Prompt 模板必须通过的基础测试"""

    def test_json_validity(self, prompt_fn, test_input):
        """输出必须是 valid JSON"""
        output = call_llm(prompt_fn(test_input))
        json.loads(output)  # 不抛异常即通过

    def test_schema_compliance(self, prompt_fn, test_input, schema_model):
        """输出必须符合 Pydantic schema"""
        output = call_llm(prompt_fn(test_input))
        schema_model.model_validate(json.loads(output))

    def test_required_fields_present(self, prompt_fn, test_input, required_fields):
        """所有必填字段必须存在且非空"""
        output = json.loads(call_llm(prompt_fn(test_input)))
        for field in required_fields:
            assert field in output and output[field] is not None

    def test_no_contradiction_in_output(self, prompt_fn, test_input):
        """用 LLM 自审：输出内容不能自相矛盾"""
        output = call_llm(prompt_fn(test_input))
        contradiction_check = call_llm(
            f"Does the following JSON contain any internal contradictions? "
            f"Answer YES or NO only.\n\n{output}"
        )
        assert contradiction_check.strip().upper() == "NO"
```

### 8.3 A/B 测试方案

对于影响质量最大的场景正文生成 Prompt，建立 A/B 测试机制：

```python
class PromptABTest:
    def __init__(self, variant_a: PromptTemplate, variant_b: PromptTemplate):
        self.variants = {"A": variant_a, "B": variant_b}
        self.results = {"A": [], "B": []}

    def run(self, test_cases: list[SceneCard], n_repeats: int = 3):
        for case in test_cases:
            for variant_name, variant in self.variants.items():
                for _ in range(n_repeats):
                    output = call_llm(variant.render(case))
                    score = evaluate_output(output, case)
                    self.results[variant_name].append(score)

    def report(self) -> dict:
        return {
            "A": {
                "mean_score": statistics.mean(self.results["A"]),
                "hook_strength_mean": ...,
                "json_failure_rate": ...,
            },
            "B": { ... }
        }
```

**评估指标（按优先级）：**
1. JSON 有效率（规划类 Prompt）
2. 钩子强度评分（生成类 Prompt）
3. 连贯性冲突数量（生成类 Prompt）
4. 质量评分均值（生成类 Prompt）
5. Token 消耗效率（输出质量/Token数）

---

## 9. 已知陷阱与规避方案

### 9.1 规划类 Prompt 常见陷阱

| 陷阱 | 症状 | 规避方案 |
|---|---|---|
| want/need 同质化 | `protagonist_want` 和 `protagonist_need` 几乎相同 | 验证层加语义相似度检查，相似度 > 0.8 触发带反例的重试 |
| 规则膨胀 | WorldSpec 产出20+条规则 | Prompt 中硬约束"MAXIMUM 8 rules"，Pydantic 验证 `len(rules) <= 8` |
| 伏笔孤岛 | 章纲中埋下的伏笔没有在后续任何章节回收 | 每次章纲批量生成结束后，运行伏笔追踪器检查未关联的种子 |
| 角色扁平化 | 反派只有目标没有弱点，配角没有自己的 agenda | 强制要求 `antagonist.weakness` 和每个 `supporting_cast` 成员必须有独立 `goal` |
| JSON 注释混入 | LLM 在 JSON 中加入 `// comment` 导致解析失败 | `extract_json` 函数在解析前清除注释；System Prompt 明确禁止 |

### 9.2 生成类 Prompt 常见陷阱

| 陷阱 | 症状 | 规避方案 |
|---|---|---|
| Tell 不 Show | "他感到愤怒，心里非常难过" | System Prompt 铁律 #3；质量评分 `show_dont_tell` < 6 触发定向重写 |
| 摘要式结尾 | "就这样，战斗结束了" | System Prompt 铁律 #1；连贯性检查器加规则：正文末段禁用"就这样/总之/最终" |
| 视角污染 | POV 角色描述了自己看不到的场景 | System Prompt 铁律 #2；评审 Prompt 专门检查"POV 越界" |
| 钩子失效 | 结尾没有悬念，读者可以安然合页 | 钩子强度评分 < 6 自动触发"钩子专项重写"任务，仅重写最后200字 |
| 字数严重超标 | 一场景写了5000字，破坏节奏 | Prompt 中明确 `word_budget`，生成后字数检查，超 ±15% 触发摘要压缩重写 |

### 9.3 评审类 Prompt 常见陷阱

| 陷阱 | 症状 | 规避方案 |
|---|---|---|
| 幻觉式引用 | 连贯性检查引用了正文中不存在的原文 | 验证层：对每个 `text_passage` 做字符串搜索确认在原文中存在 |
| 分数通货膨胀 | 所有维度都给 8-9 分，失去区分度 | 评分 Prompt 加入校准指令：每次调用至少有2个维度评分 ≤ 6 |
| 虚假冲突 | 连贯性检查把合理的叙事推断报为冲突 | 分级处理：CRITICAL/HIGH 必须处理，MEDIUM/LOW 进入人工审核队列 |
| 事实重复抽取 | Canon 抽取产出大量已有的重复事实 | 抽取 Prompt 提供 `existing_canon_summary`；抽取后运行去重比较 |

### 9.4 长篇运行下的系统性风险

| 风险 | 触发条件 | 应对措施 |
|---|---|---|
| 角色名漂移 | 写到第 50+ 章，同一角色出现别名 | 规则检查器：对所有角色名建立别名表，正文中出现未知称谓触发警告 |
| 时间线矛盾 | 事件 A 发生在 B 之前，但后文写成 B 先发生 | `TimelineEvent` 实体维护有序列表，每次写章节前检查章节内事件时序 |
| 设定遗忘 | 第 1 章定义的世界规则在第 80 章被违反 | Canon 事实 `valid_from_chapter` / `valid_until_chapter` 字段 + 连贯性检查必须扫描所有有效规则 |
| 伏笔失忆 | 第 10 章埋下的伏笔到第 100 章仍未回收 | 伏笔追踪器：超过 `max_pending_chapters`（默认50章）未回收的伏笔进入警告队列 |
| 上下文成本爆炸 | 书越写越长，每次调用 Token 越来越多 | 严格执行三层上下文分离，压缩上下文只用滚动摘要，绝不把整本书塞进 Context |

---

*本文档版本：1.0 | 适用于 BestSeller 框架 Phase 1-3 实现阶段*
