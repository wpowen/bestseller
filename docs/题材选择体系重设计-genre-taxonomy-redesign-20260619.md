# 题材选择体系重设计 — Genre Taxonomy Redesign

> 状态:**设计稿(待评审)** · 日期:2026-06-19 · 范围:新建创作时的「选题材」逻辑
> 决策已定:① 先出本设计文档再开发;② 现有 62 张预设卡**降级为「热门开局模板」**,不删除。

---

## 0. TL;DR

把「选题材」从**一张扁平的 62 张预设卡目录**(一张卡 = 写死的 `(题材, 子类型)`,整选不可拆)
改成**「频道 → 题材 → 子题材 → 流派标签」的可组合树**(题材是闭合骨架,标签是开放血肉)。

- 复用已存在但被闲置的 `config/facets/dimensions.yaml` 里的 `primary_genre` / `trope_tags` 体系。
- 新增 `config/genre_taxonomy.yaml` 把题材组织成两级树,**每个子题材节点显式映射** `novel_category` + `prompt_pack`(替掉现在脆弱的关键词模糊匹配)。
- 62 张老卡映射到树节点后,变成「一键填充模板」。
- 后端 `project.genre` 仍是字符串(由选择组装),**零 DB 迁移**;`sub_genre/tags/facets` 落已有 metadata JSON。

效果:「末日囤货升级流」「任意玄幻子类」这类组合都能精确表达。

---

## 1. 现状诊断(root cause)

### 1.1 选择链路(file:line)

| 环节 | 位置 | 事实 |
|---|---|---|
| 数据源 | `src/bestseller/services/writing_presets.py:888` `_GENRE_PRESETS` | **62 张题材卡**(中英混合) |
| 卡模型 | `src/bestseller/services/writing_presets.py:42` `GenrePreset` | `genre`/`sub_genre` 是**写死字面量**;另带 `heat_domains/reader_rewards/narrative_drives/content_modes/commercial_signals` 等facet标签 |
| 前端渲染 | `src/bestseller/web/novel_quickstart.html:7673` `renderGenres` | 卡面 = `g.genre / g.sub_genre`;维度chips只**过滤**卡片(`:7596 renderGenreFilters`),不组合 |
| 提交 | `src/bestseller/web/novel_quickstart.html:7478` | 仅发一个 `genre_key` |
| 后端硬锁 | `src/bestseller/web/server.py:3336` | `genre_key` 不在 62 张卡里 → `raise ValueError("Unknown genre_key")` |
| 自由入口(未暴露) | `src/bestseller/api/schemas/projects.py:12` `ProjectCreateRequest.genre` | 其实是 `str(max_length=100)` **自由文本** —— 但 UI 不走这扇门 |
| 下游归类 | `src/bestseller/services/novel_categories.py:174` `resolve_novel_category` | 靠 `_GENRE_NAME_KEYWORD_MAP` + `infer_genre_preset` **模糊匹配** genre/sub_genre 字符串 |

### 1.2 两个痛点的根因

- **末日囤货升级流**:`末日科幻`只有 3 张卡(`重生囤货`/`规则生存`/`基地经营`),子类型焊死进卡,「囤货 + 升级流 + 系统」无法叠加。
- **玄幻没有玄幻**:`玄幻`被打碎成两张孤卡(`玄幻升级/御兽养成`、`玄幻穿越/异界崛起`),**没有干净的「玄幻」大类**,东方玄幻/异世大陆/高武/王朝争霸/洪荒封神全缺。
- **反向碎裂**:`都市异能/都市修真/都市成长/都市娱乐/都市现实/都市竞技` 被当成 6 个平级「题材」,本该是「都市」一个大类的 6 个子类。

> 一句话:**题材是「卡片目录」(leaf = 选择单位),不是可组合的树。** 所以分支会缺、主题会碎、组合无法表达。

### 1.3 关键资产:树其实已存在,只是没当选择轴

`config/facets/dimensions.yaml` 已含:
- `primary_genre`:仙侠/玄幻/武侠/都市/末日/悬疑/历史/科幻… ≈30 个一级题材(带 heat、中英标签)。
- `trope_tags`:囤货、末日求生、系统、重生、无敌、enemies-to-lovers… **80+ 个可多选标签**。
- 正交维度:`tone`/`narrative_drive`/`power_system`/`relationship_mode`/`emotional_register`/`gender_channel`/`platform_style`。

创建流程**没把它当选择轴**,只拿来过滤 62 张卡。**积木齐全,被锁在仓库里。**

---

## 2. 平台调研(范式)

> ⚠️ 基于既有知识,非本环境实时抓取(本会话联网工具的辅助模型被配成不存在的 `qwen3.7-plus`,全部报错)。模型修好后可再做实时核验。

主流平台是**同一个四层范式**:

```
频道(Channel) → 大类(Genre) → 二级/子类(Sub-genre) → 标签(Tags/流派, 多选)  ＋ 正交维度(视角/时代/风格/篇幅)
```

| 平台 | 频道 | 大类 | 子类 | 标签/流派 |
|---|---|---|---|---|
| **起点** | 男生/女生 | ≈14(玄幻·奇幻·武侠·仙侠·都市·历史·军事·游戏·体育·科幻·悬疑·现实·诸天无限·轻小说) | 每类 6–10(玄幻→东方玄幻/异世大陆/高武世界/王朝争霸/远古洪荒…;科幻→星际文明/未来世界/**末世危机**/时空穿梭…) | 较少,层级最规范 |
| **番茄** | 男生/女生 | 粗(都市/玄幻/仙侠武侠/科幻/历史/悬疑/系统流/诸天) | 较浅 | **海量标签驱动**:系统/签到/囤货/末世/无敌/扮猪吃虎/马甲/战神/神医/赘婿 |
| **晋江** | 原创/衍生 | — | — | **多维正交并列**:类型(言情/纯爱/百合/无CP)× 时代(古代/现代/未来/架空)× 风格(正剧/轻松/暗黑)× 主题标签(穿越/重生/宫斗/种田/电竞/娱乐圈/快穿/星际/末世/无限流) |
| **七猫/纵横** | 男频/女频 | 起点式骨架 | 有 | 番茄式标签 |
| **Royal Road** | — | 少量 Genres(Fantasy/Sci-fi/Action/Romance/Horror) | — | 大量可多选 Tags(LitRPG/Progression/Cultivation/Xianxia/Portal-Isekai/Dungeon/Kingdom Building/Post-Apocalyptic/Strong Lead) |
| **Amazon KDP/KU** | — | 严格 2–3 级 BISAC 树(Fantasy>Epic/Dark/Sword&Sorcery;Romance>Paranormal/Enemies-to-Lovers) | 深 | 关键词 |

**共识规律(直接采纳):**
> 题材(大类/子类)是**闭合、稳定、分层**的骨架;标签(流派)是**开放、可多选、可组合**的血肉。
> 「末日囤货升级流」= `男频 · 末世 · 天灾囤货 · 标签[囤货, 升级流, 系统, 无敌]`。

---

## 3. 设计目标与原则

1. **可组合 > 穷举卡片**:题材闭合分层,标签开放多选;不再用「一张卡」表达全部意图。
2. **复用 > 新建**:`primary_genre`/`trope_tags`/正交维度全部复用;映射沿用 `_GENRE_TO_CATEGORY_MAP` 与现有 prompt_pack 绑定。
3. **加法 / 向后兼容**:保留 `genre_key` 老路(模板);`project.genre` 仍是字符串;零 DB schema 迁移。
4. **显式映射 > 模糊匹配**:子题材节点直接写明 `novel_category`+`prompt_pack`,顺手治掉跨题材误路由。
5. **不碰写手 prompt**:本次只改「选择 → 路由」,不动正文生成层。

---

## 4. 新选择模型(4 轴 + 正交facet)

```
轴1 频道 channel      男频 / 女频 / 通用            ← 必选(把现有「读者取向」升到顶)
轴2 题材 genre        闭合 ~19 个大类               ← 必选(一级)
轴3 子题材 sub_genre  随轴2联动的闭合小类(6–12)    ← 选一个,或「不限」
轴4 流派标签 tags     开放、可多选 0–8(复用 trope_tags) ← 血肉
────────────────────────────────────────────────
正交facet(可折叠·选填,均已存在):tone基调 · pov视角 · power_system力量体系 · relationship_mode感情线 · emotional_register情绪 · length篇幅 · platform_style平台风格
```

**选择 → 组装规则**(供下游):
- `genre`(下游字符串)= 子题材 label,空缺时回落题材 label。
- `sub_genre` = 子题材 label。
- `novel_category` = 子题材节点 `category`(无则取题材 `category_default`)。
- `prompt_pack` = 子题材节点 `pack`(无则取题材 `pack_default`;再无则 `infer`)。
- `tags` 并入 premise/标签上下文,参与 prompt_pack 二次确认与画像。

---

## 5. `config/genre_taxonomy.yaml` 完整草案

> Schema:`channels[]`、`tags`(引用 dimensions.yaml `trope_tags` + 新增)、`genres[]`(每个含 `sub_genres[]`)。
> 每个 `genre`:`key/label/channel/heat/category_default/pack_default/aliases/sub_genres`。
> 每个 `sub_genre`:`key/label/category/pack/default_tags/power_system?/notes?`。
> `category` 取值 ∈ 13 个 novel_category;`pack` 取值 ∈ 现有 prompt_pack key。

```yaml
version: 1
channels:
  - key: male      # 男频
    label: 男频
  - key: female    # 女频
    label: 女频
  - key: general   # 通用 / 现实
    label: 通用

# ── 男频 ───────────────────────────────────────────────
genres:
  - key: xuanhuan
    label: 玄幻
    channel: [male]
    heat: 85
    category_default: action-progression
    pack_default: xianxia-upgrade-core
    aliases: [玄幻升级, 异界, xuanhuan]
    sub_genres:
      - { key: eastern-xuanhuan, label: 东方玄幻, category: action-progression, pack: xianxia-upgrade-core, power_system: cultivation-tiers, default_tags: [废柴逆袭, 升级流, 宗门] }
      - { key: otherworld-continent, label: 异世大陆, category: otherworld-cross-system, pack: xianxia-upgrade-core, default_tags: [穿越, 升级流, 金手指] }
      - { key: high-martial, label: 高武世界, category: action-progression, pack: urban-power-reversal, power_system: martial-arts, default_tags: [灵气复苏, 无敌] }
      - { key: dynasty-hegemony, label: 王朝争霸, category: strategy-worldbuilding, pack: history-strategy, default_tags: [争霸, 权谋] }
      - { key: primordial-myth, label: 洪荒封神, category: action-progression, pack: xianxia-upgrade-core, default_tags: [洪荒, 神话] }
      - { key: beast-taming, label: 御兽流, category: action-progression, pack: xianxia-upgrade-core, default_tags: [御兽, 养成, 升级流] }
      - { key: xuanhuan-romance, label: 玄幻言情, channel_override: [female], category: relationship-driven, pack: romance-tension-growth, default_tags: [女强, 升级流] }

  - key: xianxia
    label: 仙侠
    channel: [male]
    heat: 88
    category_default: action-progression
    pack_default: xianxia-upgrade-core
    aliases: [修仙, 修真, xianxia]
    sub_genres:
      - { key: cultivation-civilization, label: 修真文明, category: action-progression, pack: xianxia-upgrade-core, power_system: cultivation-tiers, default_tags: [宗门, 升级流, 炼器] }
      - { key: classic-xianxia, label: 古典仙侠, category: action-progression, pack: xianxia-upgrade-core, default_tags: [师徒, 悟道] }
      - { key: fantasy-cultivation, label: 幻想修仙, category: action-progression, pack: xianxia-upgrade-core, default_tags: [金手指, 升级流] }
      - { key: urban-cultivation, label: 都市修真, category: action-progression, pack: urban-cultivation-2.0, power_system: eastern-mysticism, default_tags: [修仙2.0, 东方玄学, 系统] }
      - { key: spiritual-revival, label: 灵气复苏, category: action-progression, pack: xianxia-upgrade-core, default_tags: [灵气复苏, 无敌] }
      - { key: primordial-xianxia, label: 洪荒仙侠, category: action-progression, pack: xianxia-upgrade-core, default_tags: [洪荒, 老祖] }

  - key: wuxia
    label: 武侠
    channel: [male]
    heat: 60
    category_default: wuxia-jianghu
    pack_default: history-strategy
    aliases: [江湖, 侠义, wuxia]
    sub_genres:
      - { key: traditional-wuxia, label: 传统武侠, category: wuxia-jianghu, pack: history-strategy, power_system: martial-arts, default_tags: [门派, 侠义] }
      - { key: new-wuxia, label: 新派武侠, category: wuxia-jianghu, pack: history-strategy, default_tags: [快意恩仇] }
      - { key: martial-skill, label: 国术武技, category: wuxia-jianghu, pack: urban-power-reversal, default_tags: [武技, 热血] }
      - { key: wuxia-fantasy, label: 武侠幻想, category: wuxia-jianghu, pack: xianxia-upgrade-core, default_tags: [武侠, 升级流] }

  - key: urban
    label: 都市
    channel: [male]
    heat: 82
    category_default: urban-contemporary
    pack_default: urban-power-reversal
    aliases: [都市异能, 都市生活, urban]
    sub_genres:
      - { key: urban-life, label: 都市生活, category: urban-contemporary, pack: urban-power-reversal, default_tags: [打脸, 马甲] }
      - { key: urban-psionic, label: 都市异能, category: action-progression, pack: urban-power-reversal, power_system: psionic, default_tags: [异能, 升级流, 无敌] }
      - { key: business-workplace, label: 商战职场, category: urban-contemporary, pack: urban-power-reversal, default_tags: [商战, 职场, 重生] }
      - { key: entertainment-star, label: 娱乐明星, category: urban-contemporary, pack: entertainment-sweet, default_tags: [娱乐圈, 马甲, 直播] }
      - { key: divine-doctor-soldier, label: 神医兵王, category: action-progression, pack: urban-power-reversal, default_tags: [扮猪吃虎, 打脸, 无敌] }
      - { key: urban-slacker, label: 市井奶爸·摆烂日常, category: urban-contemporary, pack: shezhu-bailan-comedy, default_tags: [奶爸, 摆烂, 沙雕, 种田] }
      - { key: campus-youth, label: 校园青春, channel_override: [general], category: relationship-driven, pack: romance-tension-growth, default_tags: [校园, 青春] }

  - key: history
    label: 历史
    channel: [male]
    heat: 65
    category_default: strategy-worldbuilding
    pack_default: history-strategy
    aliases: [历史穿越, 权谋, history]
    sub_genres:
      - { key: alt-history, label: 架空历史, category: strategy-worldbuilding, pack: history-strategy, default_tags: [争霸, 权谋] }
      - { key: history-transmigration, label: 历史穿越, category: strategy-worldbuilding, pack: history-strategy, default_tags: [穿越, 考据, 基建] }
      - { key: court-strategy, label: 谋士权臣, category: strategy-worldbuilding, pack: history-strategy, default_tags: [权谋, 朝堂] }
      - { key: history-business, label: 历史经商, category: base-building, pack: history-strategy, default_tags: [经营, 基建, 种田] }

  - key: military
    label: 军事
    channel: [male]
    heat: 58
    category_default: strategy-worldbuilding
    pack_default: history-strategy
    aliases: [军旅, 战争, military]
    sub_genres:
      - { key: war-fantasy, label: 战争幻想, category: strategy-worldbuilding, pack: history-strategy, default_tags: [战争, 争霸] }
      - { key: army-life, label: 军旅生涯, category: urban-contemporary, pack: urban-power-reversal, default_tags: [热血, 成长] }
      - { key: spy-warfare, label: 谍战特工, category: suspense-mystery, pack: suspense-mystery, default_tags: [谍战, 智斗] }
      - { key: special-forces, label: 特种兵王, category: action-progression, pack: urban-power-reversal, default_tags: [兵王, 扮猪吃虎] }

  - key: scifi-cn
    label: 科幻
    channel: [male]
    heat: 70
    category_default: science-fiction-progression
    pack_default: scifi-starwar
    aliases: [星际, 机甲, 黑科技, sci-fi]
    sub_genres:
      - { key: interstellar, label: 星际文明, category: science-fiction-progression, pack: scifi-starwar, default_tags: [星际, 升级流] }
      - { key: future-world, label: 未来世界, category: science-fiction-progression, pack: scifi-starwar, default_tags: [未来, 科技] }
      - { key: time-travel-sci, label: 时空穿梭, category: science-fiction-progression, pack: scifi-starwar, default_tags: [时空, 穿越] }
      - { key: blacktech, label: 超级科技·黑科技, category: science-fiction-progression, pack: scifi-starwar, power_system: tech-augmentation, default_tags: [黑科技, 系统, 创业] }
      - { key: mecha-war, label: 机甲战争, category: science-fiction-progression, pack: scifi-starwar, default_tags: [机甲, 战争, 升级流] }
      - { key: cyberpunk, label: 赛博朋克, category: science-fiction-progression, pack: scifi-starwar, default_tags: [赛博, 近未来] }
      - { key: cosmic-horror, label: 克苏鲁/SCP, category: suspense-mystery, pack: suspense-mystery, power_system: rule-based, default_tags: [规则怪谈, 诡异复苏] }

  - key: apocalypse
    label: 末世
    channel: [male]
    heat: 78
    category_default: action-progression
    pack_default: apocalypse-supply-chain
    aliases: [末日, 灾变, 末日科幻, apocalypse]
    sub_genres:
      - { key: apocalypse-survival, label: 末日求生, category: action-progression, pack: apocalypse-supply-chain, default_tags: [末日求生, 升级流] }
      - { key: disaster-hoarding, label: 天灾囤货, category: action-progression, pack: apocalypse-supply-chain, default_tags: [囤货, 空间, 升级流, 无敌] }
      - { key: zombie-crisis, label: 丧尸危机, category: action-progression, pack: apocalypse-supply-chain, default_tags: [丧尸, 末日求生] }
      - { key: apocalypse-basebuilding, label: 基地经营, category: base-building, pack: apocalypse-supply-chain, default_tags: [基建, 种田, 经营] }
      - { key: rule-apocalypse, label: 规则末世, category: suspense-mystery, pack: suspense-mystery, power_system: rule-based, default_tags: [规则怪谈, 副本] }
      - { key: system-apocalypse, label: 系统末世, category: action-progression, pack: system-apocalypse-healer, power_system: system-grid, default_tags: [系统, 救世系统, 升级流] }
      - { key: evolution-mutation, label: 进化变异, category: action-progression, pack: apocalypse-supply-chain, power_system: bloodline, default_tags: [进化, 血脉觉醒] }

  - key: game
    label: 游戏竞技
    channel: [male]
    heat: 72
    category_default: esports-competition
    pack_default: game-esport
    aliases: [电竞, 网游, game]
    sub_genres:
      - { key: esports, label: 电子竞技, category: esports-competition, pack: game-esport, default_tags: [电竞, 直播] }
      - { key: vrmmo, label: 虚拟网游, category: esports-competition, pack: game-esport, power_system: litrpg-stats, default_tags: [副本, 系统, 升级流] }
      - { key: game-otherworld, label: 游戏异界, category: otherworld-cross-system, pack: game-esport, default_tags: [穿越, 副本] }
      - { key: streamer, label: 主播直播, category: urban-contemporary, pack: game-esport, default_tags: [直播, 马甲] }
      - { key: sports, label: 体育竞技, category: esports-competition, pack: game-esport, default_tags: [竞技, 热血, 成长] }

  - key: suspense
    label: 悬疑推理
    channel: [male]
    heat: 75
    category_default: suspense-mystery
    pack_default: suspense-mystery
    aliases: [推理, 探案, suspense]
    sub_genres:
      - { key: detective, label: 侦探推理, category: suspense-mystery, pack: suspense-mystery, default_tags: [推理, 智斗] }
      - { key: crime-procedural, label: 刑侦犯罪, category: suspense-mystery, pack: suspense-mystery, default_tags: [刑侦, 反转] }
      - { key: rule-horror, label: 规则怪谈, category: suspense-mystery, pack: suspense-mystery, power_system: rule-based, default_tags: [规则怪谈, 副本] }
      - { key: folk-mystery, label: 民俗诡事, category: suspense-mystery, pack: suspense-mystery, default_tags: [民俗, 灵异] }
      - { key: tomb-raiding, label: 盗墓探险, category: suspense-mystery, pack: suspense-mystery, default_tags: [探险, 秘境] }

  - key: occult
    label: 灵异
    channel: [male]
    heat: 68
    category_default: suspense-mystery
    pack_default: suspense-mystery
    aliases: [惊悚, 灵异神怪, 诡异]
    sub_genres:
      - { key: ghost-supernatural, label: 灵异神怪, category: suspense-mystery, pack: suspense-mystery, default_tags: [灵异, 诡异复苏] }
      - { key: horror-adventure, label: 惊悚冒险, category: suspense-mystery, pack: suspense-mystery, default_tags: [惊悚, 探险] }
      - { key: horror-revival, label: 诡异复苏, category: action-progression, pack: suspense-mystery, default_tags: [诡异复苏, 升级流] }
      - { key: exorcist-trade, label: 民俗驱魔, category: suspense-mystery, pack: suspense-mystery, default_tags: [驱魔, 民俗] }
      - { key: occult-tycoon, label: 神豪诡异, category: action-progression, pack: suspense-mystery, default_tags: [神豪, 诡异复苏] }

  - key: infinite-flow
    label: 无限流·诸天
    channel: [male]
    heat: 76
    category_default: otherworld-cross-system
    pack_default: suspense-mystery
    aliases: [无限流, 诸天, 主神, 快穿]
    sub_genres:
      - { key: infinite-instance, label: 无限闯关, category: suspense-mystery, pack: suspense-mystery, power_system: rule-based, default_tags: [副本, 规则怪谈, 升级流] }
      - { key: omniverse, label: 诸天万界, category: otherworld-cross-system, pack: xianxia-upgrade-core, default_tags: [诸天, 穿越, 升级流] }
      - { key: god-realm, label: 主神空间, category: otherworld-cross-system, pack: suspense-mystery, default_tags: [系统, 副本] }
      - { key: anime-crossover, label: 综漫穿越, category: otherworld-cross-system, pack: xianxia-upgrade-core, default_tags: [穿越, 同人] }

  - key: light-novel
    label: 轻小说·二次元
    channel: [general]
    heat: 55
    category_default: relationship-driven
    pack_default: shezhu-bailan-comedy
    aliases: [二次元, 轻小说, 同人]
    sub_genres:
      - { key: doujin, label: 同人衍生, category: relationship-driven, pack: shezhu-bailan-comedy, default_tags: [同人, 沙雕] }
      - { key: campus-daily, label: 校园日常, category: relationship-driven, pack: romance-tension-growth, default_tags: [校园, 日常] }
      - { key: comedy-absurd, label: 搞笑沙雕, category: urban-contemporary, pack: shezhu-bailan-comedy, default_tags: [沙雕, 摆烂, 反套路] }

# ── 女频 ───────────────────────────────────────────────
  - key: gu-yan
    label: 古代言情
    channel: [female]
    heat: 80
    category_default: relationship-driven
    pack_default: romance-tension-growth
    aliases: [古言, 宫斗, 古代言情]
    sub_genres:
      - { key: palace-intrigue, label: 宫斗宅斗, category: relationship-driven, pack: female-palace, default_tags: [宫斗, 复仇, 重生] }
      - { key: ancient-romance, label: 古言情缘, category: relationship-driven, pack: romance-tension-growth, default_tags: [甜宠, 双洁] }
      - { key: gu-rebirth-revenge, label: 古言重生复仇, category: relationship-driven, pack: villainess-reincarnation, default_tags: [重生, 复仇, 打脸] }
      - { key: gu-farming, label: 古言种田经商, category: base-building, pack: romance-tension-growth, default_tags: [种田, 经营, 团宠] }
      - { key: female-honored, label: 女尊, category: relationship-driven, pack: romance-tension-growth, default_tags: [女强, 女尊] }

  - key: xian-yan
    label: 现代言情
    channel: [female]
    heat: 86
    category_default: relationship-driven
    pack_default: romance-tension-growth
    aliases: [现言, 总裁, 甜宠]
    sub_genres:
      - { key: sweet-ceo, label: 都市甜宠·豪门总裁, category: relationship-driven, pack: entertainment-sweet, default_tags: [甜宠, 双洁, 团宠] }
      - { key: marriage-emotion, label: 婚恋情感, category: relationship-driven, pack: romance-tension-growth, default_tags: [先婚后爱, 虐恋] }
      - { key: entertainment-romance, label: 娱乐圈情感, category: urban-contemporary, pack: entertainment-sweet, default_tags: [娱乐圈, 马甲, 甜宠] }
      - { key: era-romance, label: 年代文, category: relationship-driven, pack: romance-tension-growth, default_tags: [年代文, 重生, 种田] }
      - { key: workplace-romance, label: 职场情感, category: urban-contemporary, pack: romance-tension-growth, default_tags: [职场, 双强] }

  - key: fantasy-romance-cn
    label: 幻想言情
    channel: [female]
    heat: 82
    category_default: relationship-driven
    pack_default: romance-tension-growth
    aliases: [玄幻言情, 仙侠言情, 西幻]
    sub_genres:
      - { key: xuanhuan-romance-f, label: 玄幻言情, category: relationship-driven, pack: romance-tension-growth, default_tags: [女强, 升级流] }
      - { key: xianxia-romance, label: 仙侠情缘, category: relationship-driven, pack: romance-tension-growth, default_tags: [修仙, 虐恋] }
      - { key: interstellar-romance, label: 星际幻言, category: relationship-driven, pack: romance-tension-growth, default_tags: [星际, 甜宠] }
      - { key: eastern-romance, label: 东方志怪言情, category: eastern-aesthetic, pack: eastern-aesthetic, default_tags: [国风, 志怪] }

  - key: female-growth
    label: 女性成长·大女主
    channel: [female]
    heat: 78
    category_default: female-growth-ncp
    pack_default: romance-tension-growth
    aliases: [大女主, 女强, 无CP]
    sub_genres:
      - { key: female-no-cp, label: 无CP大女主, category: female-growth-ncp, pack: romance-tension-growth, default_tags: [女强, 无CP, 升级流] }
      - { key: female-court, label: 女强权谋, category: strategy-worldbuilding, pack: history-strategy, default_tags: [权谋, 女帝] }
      - { key: female-revenge, label: 复仇逆袭, category: female-growth-ncp, pack: villainess-reincarnation, default_tags: [复仇, 重生, 打脸] }
      - { key: female-career, label: 职场大女主, category: urban-contemporary, pack: romance-tension-growth, default_tags: [职场, 女强] }

  - key: pure-love
    label: 纯爱·百合
    channel: [female]
    heat: 70
    category_default: relationship-driven
    pack_default: romance-tension-growth
    aliases: [双男主, 耽美, BL, GL, 百合]
    sub_genres:
      - { key: modern-bl, label: 现代纯爱, category: relationship-driven, pack: romance-tension-growth, default_tags: [双男主, 甜宠] }
      - { key: ancient-bl, label: 古代纯爱, category: relationship-driven, pack: romance-tension-growth, default_tags: [双男主, 古风] }
      - { key: suspense-bl, label: 悬疑纯爱, category: suspense-mystery, pack: suspense-mystery, default_tags: [双男主, 悬疑] }
      - { key: gl, label: 百合GL, category: relationship-driven, pack: romance-tension-growth, default_tags: [百合, 双女主] }

  - key: female-derivative
    label: 女频衍生·穿书快穿
    channel: [female]
    heat: 80
    category_default: otherworld-cross-system
    pack_default: villainess-reincarnation
    aliases: [穿书, 快穿, 反派千金]
    sub_genres:
      - { key: book-transmigration, label: 穿书改命, category: relationship-driven, pack: villainess-reincarnation, default_tags: [穿书, 反派, 改命] }
      - { key: quick-transmigration, label: 快穿, category: otherworld-cross-system, pack: villainess-reincarnation, default_tags: [快穿, 任务] }
      - { key: female-infinite, label: 女主无限流, category: suspense-mystery, pack: suspense-mystery, default_tags: [无限流, 副本, 女强] }

# ── 通用 / 现实 ─────────────────────────────────────────
  - key: realistic
    label: 现实
    channel: [general]
    heat: 60
    category_default: urban-contemporary
    pack_default: urban-power-reversal
    aliases: [现实题材, 行业, 职场]
    sub_genres:
      - { key: slice-realistic, label: 现实百态, category: urban-contemporary, pack: urban-power-reversal, default_tags: [现实, 成长] }
      - { key: industry-workplace, label: 行业职场, category: urban-contemporary, pack: urban-power-reversal, default_tags: [职场, 商战] }
      - { key: family-saga, label: 家庭伦理, category: relationship-driven, pack: romance-tension-growth, default_tags: [家庭, 年代文] }
      - { key: startup-business, label: 创业商战, category: base-building, pack: urban-power-reversal, default_tags: [创业, 重生, 商战] }

# ── 标签池(流派,可多选 0–8)─────────────────────────────
# 复用 config/facets/dimensions.yaml 的 trope_tags 全集(80+),并补以下高频缺口:
tags_additional:
  - { key: 升级流, label_en: Progression }
  - { key: 苟道稳健, label_en: Steady/Cautious }
  - { key: 扮猪吃虎, label_en: Hidden Strength }
  - { key: 退婚流, label_en: Broken Engagement }
  - { key: 签到, label_en: Daily Check-in }
  - { key: 神豪, label_en: Sudden Wealth }
  - { key: 鉴宝, label_en: Antiques/Appraisal }
  - { key: 谍战, label_en: Espionage }
  - { key: 先婚后爱, label_en: Marriage-First }
  - { key: 虐恋, label_en: Angst Romance }
  - { key: 双强, label_en: Power Couple }
```

> 说明:`channel_override` 用于跨频道子类(如「玄幻言情」挂在男频玄幻下但归女频);加载时按 override 决定它出现在哪个频道。
> `category`/`pack` 取值均已在仓内存在(13 category / 现有 pack key),映射沿用 `_GENRE_TO_CATEGORY_MAP` 的口径。

---

## 6. 数据结构 / Schema

### 6.1 配置 schema(pydantic,新增 `services/genre_taxonomy.py`)

```python
class SubGenre(BaseModel, frozen=True):
    key: str
    label: str
    category: str | None = None     # ∈ novel_category keys
    pack: str | None = None         # ∈ prompt_pack keys
    power_system: str | None = None
    default_tags: list[str] = []
    channel_override: list[str] | None = None
    notes: str = ""

class Genre(BaseModel, frozen=True):
    key: str
    label: str
    channel: list[str]              # male/female/general
    heat: int = 0
    category_default: str
    pack_default: str | None = None
    aliases: list[str] = []
    sub_genres: list[SubGenre] = []

class GenreTaxonomy(BaseModel, frozen=True):
    version: int
    channels: list[Channel]
    genres: list[Genre]
    tags_additional: list[TagDef] = []
```

加载方式仿 `novel_categories.py`:`@lru_cache load_genre_taxonomy()`,
并提供:`list_genres(channel)`、`get_sub_genre(genre_key, sub_key)`、
`resolve_selection(channel, genre_key, sub_key, tags) -> ResolvedSelection`(组装 genre 串 + category + pack)。

### 6.2 入参结构(新)

```python
class GenreSelection(BaseModel):
    channel: str                    # male/female/general
    genre: str                      # genre key
    sub_genre: str | None = None    # sub_genre key
    tags: list[str] = []            # trope tag keys, 0–8
    facets: dict[str, str] = {}     # tone/pov/power_system/...(可选)
    template_key: str | None = None # 选了「热门开局模板」时带上(= 旧 genre_key)
```

`ProjectCreateRequest` / quickstart payload 接受 `GenreSelection`;
**保留 `genre_key`**:仅传 `genre_key` 时 = 模板路径,服务端把模板展开成 `GenreSelection` 再走统一逻辑。

---

## 7. API 改造

| 端点 | 改动 |
|---|---|
| `GET /api/genre-taxonomy`(新) | 返回整棵树 + 标签池 + 频道;前端「选题材」向导消费它(替掉只读 preset 目录的部分) |
| `POST /api/tasks/quickstart`(`server.py:3320`) | 入参从 `{genre_key}` 扩展为 `{selection: GenreSelection}`;`genre_key` 仍兼容。`server.py:3336` 的硬锁改为:`selection` 校验走 taxonomy,`genre_key` 校验走 presets |
| `POST /api/projects`(`projects.py:54`) | metadata 增写 `sub_genre/tags/facets/channel`;`genre` 字段由 `resolve_selection` 组装 |
| 预设目录(`server.py:878 _public_writing_preset_catalog_payload`) | 仍提供,作为「热门开局模板」数据;新增字段把每张卡映射到 `(genre_key, sub_genre_key)` 树节点 |

---

## 8. 前端 UX(`novel_quickstart.html` 选题材步骤)

```
┌ Step1 选题材 ───────────────────────────────────────────┐
│ [频道] 男频 · 女频 · 通用                                  │  ← 顶轴(现 audienceOrientationRow 升顶)
│ [题材] 玄幻 仙侠 武侠 都市 历史 军事 科幻 末世 游戏竞技      │  ← 闭合 chips(按频道过滤)
│        悬疑 灵异 无限流 …                                  │
│ [子题材] (选末世后联动) 末日求生 天灾囤货 丧尸 基地经营       │  ← 联动 chips,含「不限」
│          规则末世 系统末世 进化变异                          │
│ [流派标签] 🔍 囤货✓ 升级流✓ 系统✓ 无敌✓ 空间 重生 …        │  ← 多选 0–8(搜索框)
│ ▸ 高级(可折叠):基调 视角 力量体系 感情线 篇幅 平台风格      │  ← 复用现有 facet
│                                                          │
│ ── 热门开局模板(按当前频道/题材过滤)──────────────       │  ← 62 张卡降级到这里
│ [天灾囤货·重生] [规则生存] [基地经营] …  (点=一键填充上面四轴) │
│ ▸ 题材脑洞发散 / 钩子 / concept-lab  (保留现有机制)          │
└──────────────────────────────────────────────────────────┘
```

交互要点:
- 选「题材」→ 动态渲染该题材的「子题材」chips + 默认带出 `default_tags`(可改)。
- 「热门开局模板」点击 = 把模板的 `(channel, genre, sub_genre, tags, facets)` 回填到四轴(用户可继续微调)→ 真正实现「模板只是起点」。
- 现有维度过滤(`GENRE_FILTER_FIELDS`)、脑洞/钩子/concept-lab 全部保留,作用域改为「在已选题材内细化」。
- 兼容老书读取:`getGenreDisplayDescription` 等保持。

---

## 9. 下游映射 & 向后兼容

1. **genre 字符串组装**:`resolve_selection` 产出下游用的 `genre`(= 子题材 label,如「天灾囤货」)。现有 `resolve_novel_category` / prompt_pack inference 读的就是这个串,**几乎不动**;只是从「模糊匹配」升级为「节点已带 category/pack,直接用,inference 仅兜底」。
2. **显式映射治误路由**:子题材节点的 `category`/`pack` 来自 `_GENRE_TO_CATEGORY_MAP`(`genre_review_profiles.py`)与 62 卡现有 `prompt_pack_key` 绑定的口径,保证与现网一致。
3. **62 卡 → 树节点映射表**(模板回填用,节选;完整表随实现落 `genre_taxonomy.yaml` 注释或独立 `preset_to_taxonomy.yaml`):

| 旧 preset key | → channel | genre | sub_genre | pack(沿用) |
|---|---|---|---|---|
| apocalypse-supply | male | apocalypse | disaster-hoarding | apocalypse-supply-chain |
| apocalypse-rule | male | apocalypse | rule-apocalypse | suspense-mystery |
| apocalypse-basebuilding | male | apocalypse | apocalypse-basebuilding | apocalypse-supply-chain |
| xianxia-upgrade | male | xianxia | cultivation-civilization | xianxia-upgrade-core |
| urban-xiuxian-2-0 | male | xianxia | urban-cultivation | urban-cultivation-2.0 |
| urban-power-reversal | male | urban | urban-psionic | urban-power-reversal |
| beast-taming-upgrade | male | xuanhuan | beast-taming | xianxia-upgrade-core |
| isekai-rise | male | xuanhuan | otherworld-continent | xianxia-upgrade-core |
| history-hegemony | male | history | alt-history | history-strategy |
| historical-research-travel | male | history | history-transmigration | history-strategy |
| starsea-war / mecha-warfare | male | scifi-cn | interstellar / mecha-war | scifi-starwar |
| game-esports | male | game | esports | game-esport |
| rule-horror | male | suspense | rule-horror | suspense-mystery |
| folk-mystery | male | suspense | folk-mystery | suspense-mystery |
| infinite-flow | male | infinite-flow | infinite-instance | suspense-mystery |
| horror-tycoon / folk-occult-trade | male | occult | occult-tycoon / exorcist-trade | suspense-mystery |
| female-growth-romance / sweet-romance-ceo | female | xian-yan | sweet-ceo | entertainment-sweet / romance-tension-growth |
| palace-revenge / palace-mystery-female | female | gu-yan | palace-intrigue | female-palace |
| female-no-cp | female | female-growth | female-no-cp | romance-tension-growth |
| cn-romantasy-court | female | fantasy-romance-cn | xuanhuan-romance-f | romance-tension-growth |
| entertainment-industry | general | urban | entertainment-star | entertainment-sweet |
| (英文卡 litrpg/romantasy/epic-fantasy/space-opera/...) | — | (英文树或保留扁平) | — | (原 pack) |

4. **DB**:`project.genre` 仍是字符串;`channel/sub_genre/tags/facets/template_key` 落 `metadata`(`projects.py:54` 已有 metadata 通道)。**零 schema 迁移**。
5. **英文题材**:本期先保留英文 preset 卡为扁平模板(英文 Genres + RR/KU 标签模型差异大);中文树跑通后再补「English channel」子树。

---

## 10. 三层验证计划(遵循本仓强制标准 `docs/开发与验证标准-feature-lifecycle-20260618.md`)

- **L1 单测**(`tests/unit/test_genre_taxonomy.py`,含 no-op):
  - taxonomy 能加载;每个 `sub_genre.category` ∈ 13 categories、`pack` ∈ 现有 pack keys(无孤儿)。
  - `tags_additional` ∪ trope_tags 自洽;`default_tags` 全部存在于标签池。
  - 62 张老卡 100% 能落到树节点(覆盖率断言)。
  - `resolve_selection` 对「末世/天灾囤货/[囤货,升级流]」产出 `genre='天灾囤货'`、`category='action-progression'`、`pack='apocalypse-supply-chain'`。
- **L2 回归(git-stash A/B 归因)**:旧 `genre_key` 路径对全部 62 卡产出的 `(genre, sub_genre, category, pack, target_*)` **逐字不变**。
- **L3 真机端到端**(live 栈 + 零 token 打桩 + BaseException 哨兵 + 不 commit + DB 核验 + A/B):
  - 走新四轴路径建「男频·末世·天灾囤货·[囤货,升级流,系统]」→ 跑到落库,核验 DB `genre` 串 + metadata + category + pack;
  - 与旧 `apocalypse-supply` 预设 A/B 对比路由一致;
  - 走纯「玄幻 → 异世大陆」(老体系无法表达的组合)验证能建成。

---

## 11. 分期实施

- **Phase 1(MVP,可独立交付)**:`genre_taxonomy.yaml`(中文树)+ `services/genre_taxonomy.py` + `resolve_selection` + L1 单测。先打通「玄幻全子类」「末世囤货可叠标签」。
- **Phase 2**:`GET /api/genre-taxonomy` + quickstart 接结构化入参(兼容 genre_key)+ L2 回归。
- **Phase 3**:前端「选题材」向导重构 + 62 卡降级为模板货架 + 模板回填 + L3 真机。
- **Phase 4(可选)**:英文 channel 子树;`_GENRE_NAME_KEYWORD_MAP` 收敛为「仅兜底」。

---

## 12. 风险与开放问题

1. **英文体系**:中英 Genres 结构差异大(英文是少量 Genre + 大量 Tag,RR/KU)。本期先保留英文扁平卡,Phase 4 再建英文树。
2. **题材数量上限**:19 个中文大类是建议值;heat 低的(轻小说/军事)可在 UI 折叠进「更多」。
3. **标签爆炸**:标签多选上限设 8(同 dimensions.yaml `trope_tags.max_values`);默认带出 `default_tags` 降低空选率。
4. **pack 缺口**:玄幻/武侠无专属 pack,暂复用最近的(xianxia-upgrade-core / history-strategy);后续可按需补 pack,不阻塞本设计。
5. **脑洞/concept-lab 联动**:现机制以 `genre_key` 为锚;改为以 `(genre, sub_genre, tags)` 为锚后需回归 concept-lab 入参(Phase 3 覆盖)。

---

*本文档为设计稿;评审通过后按 Phase 1→4 进入实现,每阶段走五阶段 + 三层验证。*
