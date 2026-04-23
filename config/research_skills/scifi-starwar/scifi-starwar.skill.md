---
key: scifi-starwar
version: "1.0"
name: 星际科幻 / 星战调研方法论
description: >
  面向"星际 / 太空歌剧 / 机甲 / 黑科技"题材，强调硬核科学基础、
  星际政治、非地球化的社会结构。
matches_genres:
  - 科幻
  - 星际
  - 星海
  - 机甲
  - 太空
  - scifi
  - space-opera
matches_sub_genres:
  - starwar
  - mech
  - space-empire
  - first-contact
  - hard-scifi
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
  - real_world_references
seed_queries:
  world_settings:
    - "星际文明 等级 卡尔达舍夫"
    - "星际社会 经济基础 稀缺资源"
    - "恒星际航行 物理约束"
  power_systems:
    - "机甲 分级 战争小说 原型"
    - "超光速航行 物理假设 对比"
    - "星际军衔 体系 差异"
  factions:
    - "星际帝国 与 联邦 组织差别"
    - "殖民星球 独立 运动 历史对照"
  character_archetypes:
    - "星际 主角 非天才驾驶员"
    - "星战 反派 非独裁大魔头"
  plot_patterns:
    - "星际 战争 非正面决战"
    - "星际 情报战 写法"
  scene_templates:
    - "星舰对战 描写 避免雷同"
    - "机甲驾驶舱 特写 套路"
  device_templates:
    - "星际武器 分类 物理原理"
    - "机甲 非人形 设计"
  real_world_references:
    - "物理学 相对论 时间膨胀 效应"
    - "现实 太空探索 国际空间站 运营细节"
authoritative_sources:
  - https://zh.wikipedia.org/wiki/卡爾達肖夫指數
  - https://zh.wikipedia.org/wiki/太空歌劇
  - https://en.wikipedia.org/wiki/Space_opera
  - https://zh.wikipedia.org/wiki/機動戰士鋼彈系列作品列表
  - https://baike.baidu.com/item/星际争霸
taboo_patterns:
  - 天才驾驶员
  - 银河帝国皇帝的私生子
  - 星际学院的低调转学生
  - 高达同款三等人形机甲
---

# 星际科幻调研方法论

## 1. 世界观

- **物理基础要真实**：调研时必须明确作品世界里超光速通讯 / 超光速航行
  是否存在、代价是什么。不能"默认曲率引擎 + 默认通讯零延迟"。
- 星际文明 **等级判定**可参考卡尔达舍夫指数；也可自定义更细分的维度
  （能源 / 信息 / 生存半径）。
- 社会结构不要简单复制"地球 + 未来"。挖掘：
  - 被外星文明殖民后的人类社会
  - 数个不同殖民星发展出的独立文化支流
  - 人类分化成多种亚种（太空适应、基因改造、义体）

## 2. 体系

- 修炼体系可置换为：科技等级 / 机甲驾驶熟练度 / 脑机接口稳定度 /
  AI 融合度。
- 禁止只用"机甲驾驶员能力 = 神经反应速度"单轴指标。

## 3. 派系

- 星际帝国 vs 星际联邦的对立可扩展为：星际宗教势力、星际企业巨头、
  殖民星独立军、未被承认的 AI 文明、外星族群。
- 每个势力要有 `economy / governance / military_doctrine / cultural_profile`
  四项可填。

## 4. 人物

- 禁用"天才驾驶员 + 银河帝国皇子"。
- 鼓励：后勤参谋、战后心理医生、情报分析师、殖民地农学家、星舰工程师、
  星际法官、科学传教士。

## 5. 场景

- 星舰对战不要"齐射—齐射—中弹"三段式。拆解真实海军战术（侦查、电子
  干扰、补给线切断、心理战）重新拼装。
- 机甲驾驶舱特写避免"全息屏 + 红色警报 + 驾驶员冷汗"。

## 6. 真实世界参考（real_world_references 维度必填）

- 调研星际题材时必须附带真实的物理 / 天文 / 社会学参考条目，保证
  设定可信。每条 entry 的 `source_citations_json` 至少含 1 条真实学术
  或百科链接。
