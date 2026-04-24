---
key: xianxia-upgrade
version: "2.0"
name: 仙侠升级流调研方法论
description: >
  面向"仙侠 / 修真 / 玄幻 / 上古神话改写"题材，拆解网文仙侠核心设定
  （宇宙观、境界体系、宗门派系、功法器物、人物原型）的调研指导。
  v2.0 增加 anti_cliche_patterns、dialogue_styles、emotion_arcs、
  real_world_references 维度，大幅扩充 seed_queries 和 taboo_patterns。
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
  - 传统仙侠
  - 宗门升级
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
  - dialogue_styles
  - emotion_arcs
  - thematic_motifs
  - anti_cliche_patterns
  - real_world_references
seed_queries:
  world_settings:
    - "道教宇宙观 三十三天 六道 仙界结构 原典解读"
    - "仙侠小说 地域结构 非三界六道 差异案例"
    - "网文仙侠 世界观 被误用最多的设定 如何改造"
    - "上古神话 山海经 地理 改编 修真世界"
  power_systems:
    - "修真境界 炼气筑基金丹元婴 历史来源 各派差异"
    - "网文修真 升级体系 非传统九境 差异化案例"
    - "灵力 法则 道韵 三者区别 叙事意义"
    - "修炼副作用 天魔心障 走火入魔 叙事利用"
    - "境界天花板 长生代价 哲学设定"
  factions:
    - "仙侠宗门 构成 历史原型 与道教门派对照"
    - "道门与佛门 对立结构 网文演变"
    - "散修 vs 门派 组织差异 叙事功能"
    - "妖族 魔道 划分 原典来源 vs 网文演化"
  character_archetypes:
    - "仙侠 反派 变体 非传统恶毒大师兄 内在逻辑"
    - "仙侠 女性角色 被刻板化的类型 如何突破"
    - "仙侠 配角 非工具人 独立故事线设计"
    - "太古 古神 残影 非标准强者原型"
  emotion_arcs:
    - "修仙 孤独 千年执念 情感弧设计"
    - "仙侠 情感线 非狗血 张力设计"
    - "主角 突破瓶颈 内心障碍 情感驱动"
  plot_patterns:
    - "仙侠 开局套路 为何雷同 解决办法 案例"
    - "仙侠 暗线 埋藏 类型 揭示时机"
    - "仙侠 大战 叙事节奏 不只是斗法"
    - "仙侠 转折 主角 失去一切 再重建 弧线"
  scene_templates:
    - "仙侠 斗法场面 非数值堆砌 类型"
    - "仙侠 突破境界 多感官描写 非光芒大作"
    - "宗门 内部权力 场景 日常运转感"
    - "顿悟 场景 非字面意义 内心叙事"
  device_templates:
    - "仙侠 神兵 命名 避免雷同 命名法"
    - "仙侠 金手指 非传统类型 有代价"
    - "仙侠 典籍 功法 设计 区别化"
  dialogue_styles:
    - "仙侠 老前辈 台词 避免'尔等' 套话"
    - "道家 哲理 在台词里 自然融入 vs 空洞说教"
    - "仙侠 反派 说服性独白 内在一致"
  thematic_motifs:
    - "仙侠 母题 月 剑 药 鼎 对照 选取原则"
    - "道法自然 无为 在仙侠叙事的现代解读"
    - "长生 与 人间 代价结构 母题"
  anti_cliche_patterns:
    - "废灵根觉醒神级体质 代价缺失"
    - "拍卖会第一章捡漏至宝"
    - "宗门驱逐后拜入隐世高人"
    - "同门嫉妒主角被陷害 第三章复仇"
    - "每个反派都是不知道天高地厚"
  real_world_references:
    - "道教原典 道德经 庄子 修炼观念 真实含义"
    - "封神演义 诸神谱系 真实设定 vs 网文改编"
    - "中国古代丹药 外丹内丹 历史演变"
    - "山海经 异兽 地理 可用于世界观设计的具体素材"
authoritative_sources:
  - https://zh.wikipedia.org/wiki/仙俠小說
  - https://zh.wikipedia.org/wiki/道教神仙
  - https://baike.baidu.com/item/修真
  - https://baike.baidu.com/item/仙侠小说
  - https://zh.wikipedia.org/wiki/山海經
  - https://baike.baidu.com/item/封神演义
  - https://ctext.org/dao-de-jing/zh
  - https://www.zdic.net
taboo_patterns:
  - 方域                        # 已被多项目共用，禁止复用
  - 废灵根被驱逐                  # 开篇陷阱：与道种破虚、规则漏洞两本书都重复
  - 青莲剑歌                     # 使用次数 > 8，下本书需变形
  - 九天玄女                     # 过度消费的神话人物
  - 天才少年被误解                # 标签化人物，必须具体化
  - 五行 + 阴阳 + 八卦 的三合一组合
  - 废柴主角三章内觉醒天地无双体质  # 代价缺失
  - 宗门内所有人都针对主角         # 逻辑失真
  - 反派出现立即发言"无名小卒"     # 台词模板
  - 突破境界只靠灵石堆砌无任何感悟  # 成长感缺失
---

# 仙侠升级流调研方法论（v2.0）

调研仙侠题材时，依 **世界观 → 体系 → 派系 → 人物 → 情节 → 母题** 的顺序挖掘，每维度至少 ≥ 10 条非重复条目。

## 1. 世界观（world_settings）

**目标**：给库贡献至少 10 种非同构的"仙侠宇宙结构"，每种结构必须有**独立的地理模型 / 历史脉络 / 运行规则**。

- 不要满足于"三界六道"套娃。至少要挖掘：
  - 单界模型（如《哈利波特》式魔法社会的修真版）
  - 平行宇宙模型（如《无限流》嵌套）
  - 时空叠加模型（过去 / 现在 / 未来共存）
  - 残局模型（"仙神已死，地球残存的仙侠遗产"）
  - 大道残损模型（天道本身有漏洞，才产生修仙机会）
- 每个 entry 在 `content_json` 里填：
  - `geography_model`：地理结构逻辑
  - `power_vacuum`：权力空白如何形成冲突
  - `civilizational_rules`：修仙世界的社会契约
  - `unique_conflict_source`：区别于其他世界观的独有冲突来源

## 2. 境界体系（power_systems）

**目标**：至少 12 种境界划分方案，其中 ≥ 4 种不属于"炼气筑基金丹元婴"传统谱系。**

- 探索非传统体系：
  - 契约型（与精怪/法器/大道契约，断约即衰败）
  - 映射型（血脉映射天象，受天时影响）
  - 符文型（人即符文载体，信息与肉体合一）
  - 概念型（概念替代数值，"无畏"即护盾）
  - 感知型（感知天地越细腻境界越高，战力是副产品）
- 每条 entry 必含：`levels[] / upgrade_triggers / bottleneck_logic / side_effects / social_impact`

## 3. 派系（factions）

**破除"宗门 = 院校"的想象。**

- 探索非宗门类型：
  - 家族（血脉传承，外人难入）
  - 教派（信仰结构，教义即法律）
  - 散修联盟（契约/货币型组织，无忠诚度）
  - 秘密结社（反派常设模板，但要有内在逻辑）
  - 天然联盟（妖族/异类的组织逻辑，非人类视角）
- 每个 entry 须含：`origin_myth / governance / core_resource / internal_tension / external_relations`

## 4. 人物（character_archetypes）

**调研 archetype 时注意避免"废灵根 + 被驱逐"的开篇，这是当前库里 usage_count 最高的模板（> 8 次）。**

- 探索非传统反派原型：
  - 世界观对立型（不是嫉妒，是对"道"的不同理解）
  - 过去投影型（主角的命运的另一条路）
  - 哲学路线之争型（不是权力斗争）
- 主角原型多样化：
  - 非天才探索型（真实摸索，有方向性错误）
  - 代价承担型（每次强大都失去真实的东西）
  - 非战力核心型（靠智谋/知识/工匠技艺推进）

## 5. 情节（plot_patterns / scene_templates）

**明线 + 暗线必须同时存在：**

| 明线类型 | 暗线类型 | 最佳组合 |
|---------|---------|---------|
| 登顶 | 真相/身世 | 经典，但暗线必须真正改变主角价值观 |
| 宗门博弈 | 情感代价 | 需要让情感真正干预决策 |
| 寻道 | 历史阴谋 | 适合大世界观叙事 |
| 复仇 | 哲学转变 | 适合黑化/成长弧 |

- 场景模板要包括：开场 / 反转 / 打脸 / 高潮 / 救场 / 告别 / 祭奠
- **禁用套路清单**（taboo 之外的提示）：
  - 斗法描写依赖"招式名"而非感官细节
  - 突破境界的描写每次都是同一个光芒/振动模板

## 6. 道具 & 母题（device_templates / thematic_motifs）

- 神兵命名：不要再用"青锋剑" / "寒霜剑"，优先挖掘典籍里真实存在的法宝名称（如"翻天印"、"斩仙飞刀"）并作现代变形。
- 母题：寻找非"月/剑"的选项——香、药、鼎、镜、骨、谱、种、血——每种都有道教/上古典籍根据。

## 7. 真实文化根系（real_world_references）

- **道德经**：第1章"道可道非常道"的修炼哲学解读，是仙侠"道"的概念最直接的来源
- **庄子**：蝴蝶梦、逍遥游，提供"超脱"概念的真实哲学背景
- **封神演义**：最完整的中国神仙谱系，有具体神职/法宝/神话事件可供直接改编
- **山海经**：异兽/异国的具体描述，可作为monster/race/locale的真实考据素材
