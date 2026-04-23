---
key: base-research-discipline
version: "1.0"
name: 基础调研纪律
description: >
  所有题材通用的调研流程与引用规范。是每条调研/ forge 请求都必须遵守的底线。
is_common: true
search_dimensions:
  - world_settings
  - power_systems
  - factions
  - character_archetypes
  - plot_patterns
  - scene_templates
  - device_templates
  - locale_templates
  - emotion_arcs
  - thematic_motifs
  - anti_cliche_patterns
taboo_patterns:
  - 方域            # 同题材雷同陷阱：多本书出现同名反派
  - 废灵根外门弟子    # 雷同化陷阱：开篇同质化
  - 天才少年        # 标签化人物，必须进一步细化
authoritative_sources:
  - https://zh.wikipedia.org
  - https://baike.baidu.com
---

# 基础调研纪律

1. **先说清楚查什么。** 每一个 emit 的 entry 必须标明:
   - `dimension`（world_settings / power_systems / ...）
   - `genre` + `sub_genre`
   - `slug`（唯一引用键，小写连字符，不含空格）

2. **每条目至少 2 条独立来源。** 来源要么来自 web search，
   要么来自 MCP 搜索 / pgvector 已有条目。严禁"凭记忆编造"。

3. **所有 taboo 词汇在 emit 前必须过滤。** 如果输出的
   `narrative_summary` / `content_json` 的实体名命中 taboo，必须重命名并
   记录变形说明。

4. **新颖度第一，覆盖度第二。**
   - 如果库中 usage_count > 8 的条目检索命中率高，说明该维度已雷同化 —
     需要主动扩展更冷门分支。
   - 不要重复输出已存在 slug 的条目；如需变形，使用新的 slug + 注明
     `variation_of`。

5. **structured ≠ verbose.** `content_json` 是结构化字段映射，
   `narrative_summary` 是一句话自我描述（≤ 80 汉字），二者不得互相
   复制。

6. **中文优先，英文保留专有名词。** 网文题材条目默认用中文填写
   `name` / `narrative_summary`；国际题材保留英文专有名词。
