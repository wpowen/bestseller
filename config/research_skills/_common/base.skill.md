---
key: base-research-discipline
version: "2.0"
name: 基础调研纪律（全题材通用）
description: >
  所有题材的调研流程规范、引用标准、novelty 优先级、
  条目质量门槛和反雷同化操作流程。每次 Research Agent /
  Library Curator / Forge 运行时均自动加载。
is_common: true
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
  - emotion_arcs
  - thematic_motifs
  - anti_cliche_patterns
  - real_world_references
  - dialogue_styles
taboo_patterns:
  # ── 全局雷同名 ──
  - 方域               # 跨项目高频反派名，禁止复用
  - 废灵根外门弟子       # 开篇同质化陷阱（规则漏洞+道种破虚均命中）
  - 天才少年被误解       # 标签化人物，必须具体化再使用
  - 青莲剑歌            # usage_count > 8，变形后再用
  - 九天玄女            # 过度引用的神话人物
  # ── 禁用情节模板 ──
  - 废柴主角第一章觉醒最强天赋    # 代价缺失的金手指
  - 所有反派都是纸板坏人          # 无动机的对立
  - 主角被辱后三章之内完美复仇    # 节奏失真
  - 情节全靠误会推动              # 低质量冲突
  # ── 标签化角色 ──
  - 白莲花女主              # 角色功能化，缺乏自主
  - 霸道总裁男主            # 审美疲劳，需要变体
  - 炮灰绿茶女配            # 角色扁平化
authoritative_sources:
  - https://zh.wikipedia.org
  - https://baike.baidu.com
  - https://www.guoxue.com           # 国学网，历史/文化参考
  - https://ctext.org                # 中国哲学书电子化计划
  - https://www.zdic.net             # 汉典，字词来源
seed_queries:
  anti_cliche_patterns:
    - "网文 雷同 开篇 避免方法"
    - "小说 角色 标签化 如何解决"
    - "中文网文 情节 套路 读者吐槽"
  thematic_motifs:
    - "中国文学 母题 归纳 对比"
    - "网文 核心情感 驱动力 研究"
  real_world_references:
    - "中国传统文化 可改编素材 索引"
    - "中文网文 与 真实历史文化 对照"
---

# 基础调研纪律

## 0. 工作前置检查

**每次开始调研 / Forge 前必须确认：**

1. 目标 `dimension` 已明确（world_settings / power_systems / … 中的一个）
2. `genre` 已知（仙侠 / 都市 / 末日 / …）；`sub_genre` 已知或标注 `null`
3. 本项目已有的 `project_materials` 已加载（避免重复造轮子）
4. 当前 `material_library` 中该 `(dimension, genre)` 的 `usage_count > 8` 的条目已标记为"高频已消耗"，本次 emit 优先避开

---

## 1. emit 条目的必要字段

每条 `emit_entry` 必须提供完整结构：

```json
{
  "dimension":          "world_settings",
  "genre":              "仙侠",
  "sub_genre":          null,
  "slug":               "residue-immortal-domains",
  "name":               "残域仙界",
  "narrative_summary":  "仙神已死、法则残破的废墟界域，散修可拾取碎片规则…",
  "content_json": {
    "core_rule":        "...",
    "power_vacuum":     "...",
    "unique_conflict":  "..."
  },
  "source_citations_json": [
    {"url": "https://...", "title": "...", "accessed": "2026-04"},
    {"text": "evaluative source: 评论区分析...", "confidence": 0.7}
  ],
  "confidence":         0.85
}
```

- `slug`：全小写英文/拼音 + 连字符，每个项目独有前缀（已由 Forge 自动添加）
- `narrative_summary`：**≤ 80 汉字**，一句话自我描述，不得和 `content_json` 互相复制
- `confidence`：web search 支撑 → 0.8+；纯 LLM 推演 → 0.5–0.7

---

## 2. 来源规范

| 来源类型 | 最低要求 | confidence 范围 |
|---|---|---|
| Wikipedia / Baidu百科 + 独立验证 | 2 条独立链接 | 0.85–1.0 |
| 学术论文 / 正式出版物 | 1 条即可 | 0.9–1.0 |
| 网文评论 / 创作者分析 | 须注明"evaluative source" | 0.5–0.75 |
| 纯 LLM 推演（无搜索支撑） | 须在 `narrative_summary` 末加 `[LLM推演]` | 0.4–0.6 |

**严禁：** 无来源 emit / 来源只写"互联网通常认为" / 使用已失效的 URL 而不验证。

---

## 3. novelty 优先级规则

**用新的、不常见的，而不是用已知的、安全的。**

1. 检查 `material_library`：如果一个条目 `usage_count ≥ 6`，它已经是"普通物料"，不要再作为主要 emit 候选
2. 检查 `taboo_patterns`（本 skill 的 + 项目级的）：名字/概念命中则强制变形，记录 `variation_of`
3. 同一 `(dimension, genre)` 内，新 emit 的条目与已有条目的 `narrative_summary` 余弦相似度应 < 0.6（由 Batch 3 novelty_critic 把关，但调研阶段也要主动避免）
4. **每次 Research 的目标：给库增加 50% 以上尚未存在的视角**，而非"再次确认已有内容"

---

## 4. content_json 维度规范

不同 `dimension` 的 `content_json` 必须包含对应的关键字段：

| dimension | 必含字段 |
|---|---|
| `world_settings` | `geography_model`, `power_vacuum`, `civilizational_rules`, `unique_conflict_source` |
| `power_systems` | `levels[]`, `upgrade_triggers`, `bottleneck_logic`, `social_impact`, `side_effects` |
| `factions` | `origin_myth`, `governance`, `core_resource`, `internal_tension`, `external_relations` |
| `character_archetypes` | `core_wound`, `external_goal`, `internal_need`, `fatal_flaw`, `typical_arc` |
| `character_templates` | `name`, `background`, `personality_core`, `arc_stages[]`, `relationships{}` |
| `plot_patterns` | `trigger`, `escalation_logic`, `midpoint_reversal`, `resolution_type`, `subplots[]` |
| `scene_templates` | `scene_type`, `entry_condition`, `tension_source`, `exit_hook`, `variations[]` |
| `device_templates` | `name`, `origin`, `ability`, `cost_or_limitation`, `symbolic_meaning` |
| `locale_templates` | `name`, `atmosphere`, `unique_rules`, `narrative_uses[]`, `sensory_details` |
| `emotion_arcs` | `arc_name`, `stages[]`, `turning_point_trigger`, `reader_investment_mechanism` |
| `thematic_motifs` | `symbol`, `cultural_origin`, `narrative_functions[]`, `variations[]` |
| `dialogue_styles` | `register`, `subtext_pattern`, `rhythm_notes`, `examples[]` |
| `anti_cliche_patterns` | `cliche_description`, `why_it_fails`, `alternative_approach`, `examples[]` |
| `real_world_references` | `source_type`, `event_or_concept`, `adaptation_potential`, `citation` |

---

## 5. structured 写法示例

**正确：**
```json
{
  "narrative_summary": "以铸器为修炼媒介的体系，每件神兵都是修士人格的外化，摧毁神兵即摧毁修士意志",
  "content_json": {
    "levels": ["毛坯期","灵铸期","器魂期","人器合一"],
    "upgrade_triggers": ["铸造突破+战斗磨砺+意志考验三重验证"],
    "social_impact": "神兵作为个人身份标志，社会信用系统绑定器评"
  }
}
```

**错误（避免）：**
```json
{
  "narrative_summary": "这是一个铸器修炼体系，修炼者通过铸器来提升自己的修为，境界分为初级中级高级",
  "content_json": {
    "description": "通过铸造神兵来修炼，每个等级都比上一个更强"
  }
}
```

---

## 6. 冷门分支优先探索

调研时优先探索以下"通常被忽略的方向"：

- **女性视角的世界观**（权力体系对女性的限制与规避方式）
- **底层视角的社会结构**（普通人如何在宏大设定里生存）
- **反派/对立方的合理性**（他们的内部逻辑与资源）
- **历史真实原型**（每个架空设定背后可以查到的现实模型）
- **感官细节**（味道/触感/声音 — 视觉意象以外的沉浸感）
- **失败案例**（主角和配角都可以失败，调研哪种失败最有叙事价值）

---

## 7. 维度优先级（按对反雷同的贡献排序）

1. **`character_archetypes`** — 人物原型是雷同的最主要来源
2. **`world_settings`** — 独特世界观是差异化的最大杠杆
3. **`plot_patterns`** — 情节模板是第二大雷同来源
4. **`thematic_motifs`** — 母题决定书的精神气质
5. **`factions`** — 派系是世界观的具体化
6. **`power_systems`** — 升级体系影响节奏感
7. 其余维度：按具体项目需要排序

每次调研至少覆盖前 4 个维度，不得只盯着 `power_systems`（常见偏误）。
