---
key: apocalypse
version: "1.0"
name: 末日囤货/废土生存调研方法论
description: >
  面向"末日 / 囤货 / 废土 / 丧尸 / 灾难"题材，拆解生存规则、资源体系、
  废土社会结构和末日人性冲突的调研指导。
matches_genres:
  - 末日
  - 囤货
  - 废土
  - 丧尸
  - 灾难
  - 末世
  - apocalypse
  - post-apocalypse
matches_sub_genres:
  - 重生囤货
  - 末世修仙
  - 废土生存
  - apocalypse-supply-chain
search_dimensions:
  - world_settings
  - power_systems
  - factions
  - character_archetypes
  - character_templates
  - plot_patterns
  - scene_templates
  - device_templates
  - locale_templates
  - thematic_motifs
seed_queries:
  world_settings:
    - "末日小说 世界观 类型差异 核战/病毒/自然灾害"
    - "废土社会 重建阶段 政治形态 历史案例"
    - "丧尸末日 规则体系 网文差异化"
  power_systems:
    - "末世异能 体系设计 差异化 非强攻类"
    - "废土 物资体系 货币 以物换物 设计"
    - "末世 等级划分 异能者/普通人 社会结构"
  factions:
    - "末日基地 组织 类型 军事/平民/教派"
    - "废土 流寇 贸易商 势力结构"
    - "幸存者 心理 群体动态 社会学角度"
  character_archetypes:
    - "末日 主角 非全能类型 有明显弱点"
    - "末日 反派 非纯暴力类型 资源控制者"
    - "末日 女性主角 生存型 非依赖型"
  plot_patterns:
    - "末日小说 前三章 非直接末日爆发 差异化开场"
    - "重生囤货 核心驱动力 非只是物资"
    - "末日 友情/信任 崩塌弧线"
  scene_templates:
    - "末日 资源争夺 非暴力手段场景"
    - "废土 交易 谈判 场景要素"
    - "幸存者基地 日常 场景 有张力"
  locale_templates:
    - "中国末日 地点 非美国式废土"
    - "废土 城市废墟 地下空间 特色地点"
  thematic_motifs:
    - "末日 核心母题 人性 希望 代价 对立"
    - "末日 道德困境 为生存可以做什么"
authoritative_sources:
  - https://zh.wikipedia.org/wiki/后启示录
  - https://zh.wikipedia.org/wiki/世界末日
  - https://baike.baidu.com/item/末日小说
taboo_patterns:
  - 主角前世就把末日所有资源都囤好了        # 重生套路过饱和
  - 末日第一天就建立完美基地无人挑战        # 无冲突
  - 异能觉醒后立刻全方位无敌               # 能力成长过快
  - 所有人（含反派）都对主角无能为力       # 缺乏真实威胁
  - 废土里有完美的现代医疗和食品            # 设定失真
---

# 末日囤货/废土生存调研方法论

调研末日题材时，以 **灾难类型 → 社会崩溃方式 → 资源稀缺度 → 组织重建 → 人性考验** 推进。

## 1. 世界观与末日类型（world_settings）

**目标**：≥ 8 种"末日触发机制"，每种对社会结构的冲击方式不同。

- 探索：
  - 生态型（环境崩溃、食物链断裂）
  - 病毒型（变异、丧尸、隔离区）
  - 核战型（放射区、稀缺资源分布）
  - 外来型（外星、异次元入侵）
  - 灵气型（末世修仙：文明技术失效 + 修真觉醒）
  - 系统型（神明/系统降临，规则强制变更）
- `content_json` 须含：`collapse_trigger / collapse_speed / resource_distribution / power_vacuum_type`。

## 2. 资源与权力（power_systems + factions）

- 末日"体系"不止异能等级——资源控制才是真权力。
- 探索：
  - 物资基地经济（水/食物/药品的控制权）
  - 异能者雇佣市场（能力明码标价）
  - 信息垄断型（谁知道外界情况谁有权）
- 每条势力要有 `core_resource / weakness / expansion_strategy`。

## 3. 人物（character_archetypes）

- 避免全知全能的重生者——要有认知盲区和代价。
- 弱点设计：身体残缺/心理创伤/知识盲区/情感牵绊。
- 反派需有合理资源基础，不是"坏人"标签，是"争夺同一块蛋糕的另一方"。

## 4. 情节（plot_patterns）

- 前三章不要直接开末日，可从"倒计时 N 天 / 前一世记忆开始 / 已在末日中"三种切入各一变体。
- 核心驱动除"囤物资"外，要有：保护目标 / 揭开真相 / 建立新秩序 / 复仇 中的一条主线。
- 必须有至少一次主角失去一个重要物资/人的剧情节点。

## 5. 中国末日特色（locale_templates）

- 地点选中国而非美国框架：地下防空洞、粮库仓储区、南方山地村落、东北矿区、城中村。
- 废土初期社会：自发出现的社区自治、家族互保、宗教聚集，不是直接军政府接管。
