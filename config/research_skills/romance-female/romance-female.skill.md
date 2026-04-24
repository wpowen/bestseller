---
key: romance-female
version: "1.0"
name: 女频言情/甜宠/成长调研方法论
description: >
  面向"女频 / 言情 / 成长 / 恋爱 / 甜宠"题材，拆解情感弧线设计、
  女性主角成长路径、关系张力结构的调研指导。
matches_genres:
  - 女频
  - 言情
  - 成长
  - 恋爱
  - 甜宠
  - romance
  - female-lead
matches_sub_genres:
  - 甜宠
  - 虐恋
  - 先婚后爱
  - 豪门
  - 娱乐圈
  - romance-tension-growth
search_dimensions:
  - world_settings
  - character_archetypes
  - character_templates
  - emotion_arcs
  - plot_patterns
  - scene_templates
  - dialogue_styles
  - thematic_motifs
  - anti_cliche_patterns
seed_queries:
  world_settings:
    - "女频言情 世界观 豪门/娱乐圈/异世界 差异"
    - "言情小说 背景设定 非豪门类型 创新"
    - "甜宠 现代设定 细节 差异化"
  character_archetypes:
    - "言情 女主 非白莲花/灰姑娘类型"
    - "言情 男主 非霸道总裁类型 有弱点"
    - "言情 女配 非绿茶/恶毒 复杂女性形象"
  emotion_arcs:
    - "言情 情感弧线 五阶段 差异设计"
    - "甜宠 cp感 建立 非一见钟情"
    - "虐恋 分与合 节奏 心理变化"
  plot_patterns:
    - "言情 非先婚后爱 主线 创新案例"
    - "女频 成长 非爱情驱动 事业线"
    - "言情 第三者介入 处理方式 差异化"
  scene_templates:
    - "言情 甜蜜场景 非表白/亲吻 多样化"
    - "女频 冲突场景 非误会型"
    - "言情 分手场景 有尊严 女主强"
  dialogue_styles:
    - "言情 对话 CP感 台词写法"
    - "女频 心理独白 避免玛丽苏"
    - "甜宠 轻喜剧感 日常对话 节奏"
  thematic_motifs:
    - "女频 核心母题 自我价值 vs 他人评价"
    - "言情 母题 救赎 成全 理解 哪种更有共鸣"
  anti_cliche_patterns:
    - "言情 雷区 常见失误 读者痛点"
    - "女主 被动型 主动型 比较 哪种更受欢迎"
authoritative_sources:
  - https://zh.wikipedia.org/wiki/言情小说
  - https://baike.baidu.com/item/女频小说
taboo_patterns:
  - 霸道总裁第一章强吻女主             # 审美已过时
  - 女主三番四次原谅男主的出轨背叛     # 价值观问题
  - 女配全是绿茶或恶毒没有立体形象     # 角色单一
  - 男主吃醋靠误会而非理解驱动         # 情节低效
  - 女主成功完全依赖男主资源           # 成长不独立
  - 第三者无来由的爱上男主             # 行为逻辑缺失
---

# 女频言情/甜宠/成长调研方法论

调研女频题材时，以 **情感核心 → 人物内在需求 → 关系张力设计 → 成长弧线 → 细节真实感** 推进。

## 1. 情感弧线（emotion_arcs）

**目标**：≥ 8 种"CP从相识到稳定"的情感进程，每种有不同的张力来源。

- 不要只用"误会 → 解释 → 和好"——这是张力最低的模式。
- 探索高张力来源：
  - 价值观碰撞（两人真的有根本分歧，需要真正妥协）
  - 身份障碍（不是家人反对，是结构性的阻力）
  - 成长错位（一方成长了另一方还停在原地）
  - 代价型（在一起需要真正牺牲某种重要的东西）
- `content_json` 须含：`tension_source / turning_points / reader_investment_mechanism`。

## 2. 女性主角（character_archetypes）

- 禁止"灰姑娘等待被拯救"模式——女主必须有独立目标。
- 独立目标类型：
  - 职业目标（医生/设计师/演员的专业成就）
  - 个人情感目标（找到自我价值感，不是找到对的人）
  - 家族/义务（带着责任感行动的女主）
- 女主的情感选择要有代价：选了爱情就放弃了什么，这是共鸣来源。

## 3. 男性角色（character_archetypes）

- 高质量男主不是"霸道"，是"有内在逻辑的强烈自我"。
- 探索：
  - 低调强型（实力不展示，动机不明显）
  - 成长型（故事里他也在变化，不是完美开始）
  - 有弱点型（他的弱点不能被女主轻易修复，需要过程）

## 4. 情节（plot_patterns）

- 三幕结构：
  - Act 1：相遇 + 初次张力点（不是单纯甜蜜）
  - Act 2：关系深化中的最大危机（不是小误会，是真的可能分开）
  - Act 3：解决危机需要主动行动（主角之一主动做艰难决定）
- 事业线必须和情感线有关联，不能只是背景板。

## 5. 对话与细节（dialogue_styles）

- CP感的对话要有"只有他们才懂的语境"——专属感。
- 心理独白要有自我意识，不能只是"我怎么这么心动"。
- 甜蜜场景要有意外性：不是预设的浪漫，是意料之外的真实。
