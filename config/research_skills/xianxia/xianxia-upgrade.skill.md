---
key: xianxia-upgrade
version: "1.0"
name: 仙侠升级流调研方法论
description: >
  面向"仙侠 / 修真 / 玄幻 / 上古神话改写"题材，拆解网文仙侠核心设定
  （宇宙观、境界体系、宗门派系、功法器物、人物原型）的调研指导。
matches_genres:
  - 仙侠
  - 修真
  - 玄幻
  - 奇幻
  - xianxia
  - cultivation
  - eastern-fantasy
matches_sub_genres:
  - upgrade
  - sect
  - 宗门
  - 金手指
  - 登顶
  - immortal-ascent
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
    - "道教宇宙观 三十三天 六道"
    - "仙侠小说 地域结构 差异案例"
    - "网文仙侠 世界观 被误用最多的设定"
  power_systems:
    - "修真境界 炼气筑基金丹元婴 划分差别"
    - "网文修真 升级体系 非传统九境"
    - "灵力 法则 道韵 三者区别"
  factions:
    - "仙侠宗门 构成 历史原型"
    - "道门与佛门 对立结构"
  character_archetypes:
    - "仙侠 反派 变体 非传统恶毒大师兄"
    - "仙侠 女性角色 被刻板化的类型"
  plot_patterns:
    - "仙侠 开局套路 为何雷同 解决办法"
    - "仙侠 暗线 埋藏 案例"
  scene_templates:
    - "仙侠 斗法场面 类型"
    - "仙侠 突破境界 视觉描写 标准"
  device_templates:
    - "仙侠 神兵 命名 避免雷同"
    - "仙侠 金手指 非传统类型"
  thematic_motifs:
    - "仙侠 母题 月 剑 药 鼎 对照"
authoritative_sources:
  - https://zh.wikipedia.org/wiki/仙俠小說
  - https://zh.wikipedia.org/wiki/道教神仙
  - https://baike.baidu.com/item/修真
  - https://baike.baidu.com/item/仙侠小说
  - https://zh.wikipedia.org/wiki/山海經
  - https://baike.baidu.com/item/封神演义
taboo_patterns:
  - 方域                  # 已被多项目共用，禁止复用
  - 废灵根被驱逐            # 开篇陷阱：与道种破虚、规则漏洞两本书都重复
  - 青莲剑歌               # 使用次数 > 8，下本书需变形
  - 九天玄女               # 过度消费的神话人物
  - 天才少年被误解           # 标签化人物，必须具体化
  - 五行 + 阴阳 + 八卦 的组合
---

# 仙侠升级流调研方法论

调研仙侠题材时，依 **世界观 → 体系 → 派系 → 人物 → 情节** 的顺序挖掘。

## 1. 世界观（world_settings）

**目标**：给库贡献至少 10 种非同构的"仙侠宇宙结构"，每种结构必须有**独立的
地理模型 / 历史脉络 / 运行规则**。

- 不要满足于"三界六道"套娃。至少要挖掘：
  - 单界模型（如《哈利波特》式魔法社会）
  - 平行宇宙模型（如《无限流》嵌套）
  - 时空叠加模型（如《诛仙》的过去 / 现在 / 未来共存）
  - 残局模型（如"仙神已死，地球残存的仙侠遗产"）
- 每个 entry 在 `content_json` 里填 `geography / history / rules /
  factional_tension_points`。

## 2. 体系（power_systems）

**目标**：至少 12 种境界划分方案，其中 **≥ 4 种**不属于"炼气筑基金丹元婴"
传统谱系。

- 探索：契约型（与精怪 / 法器 / 大道契约）、映射型（血脉映射天象）、
  符文型（人即符文载体）、概念型（概念替代数值）。
- `content_json` 必含 `levels[] / upgrade_triggers / bottleneck_logic /
  side_effects_on_society`。

## 3. 派系（factions）

- 破除"宗门 = 院校"的想象。考察：
  - 家族（血脉传承）
  - 教派（信仰结构）
  - 散修联盟（契约 / 货币型组织）
  - 秘密结社（反派常设模板）
- 每个 entry 要有 `origin_myth / governance / economy / tension_with_others`。

## 4. 人物（character_archetypes / character_templates）

- 调研 archetype 时注意**避免"废灵根 + 被驱逐"**的开篇，这是当前库里
  usage_count 最高的模板（超过 8 次）。
- 探索**非传统**反派原型：
  - 不是同门嫉妒而是世界观对立
  - 不是长辈压制而是主角过去的投影
  - 不是权力斗争而是哲学路线之争
- character_templates 必须是**具体到姓名 + 背景 + 弧线**的人，不是标签。

## 5. 情节（plot_patterns / scene_templates）

- 至少一条明线 + 一条暗线组合：明线"登顶"，暗线可以是"真相线 /
  身世线 / 伏笔线 / 情感线"。
- 场景模板要包括：开场 / 反转 / 打脸 / 高潮 / 救场 / 告别 / 祭奠。
- **禁用套路清单**：
  - "废灵根觉醒神级体质"
  - "被宗门驱逐后拜入隐世高人"
  - "同门嫉妒主角被陷害"
  - "拍卖会捡漏至宝"

## 6. 道具 & 母题（device_templates / thematic_motifs）

- 神兵不要再用"青锋剑" / "寒霜剑"这类词，优先挖掘典籍里真实存在的法宝
  名称（如"翻天印"、"斩仙飞刀"），并作现代变形。
- 母题寻找非"月 / 剑"的选项：香、药、鼎、镜、骨、谱、种、血……

## 引用要求

- 每条 `source_citations_json` 至少含一条 wiki/baike 链接 + 一条主题解读。
- 新颖的非传统结构如果来自网文评论而非学术资料，必须在
  `narrative_summary` 里注明"evaluative source"。
