# 小说产物规范（Story Artifact Spec）

LifeScript-Solo（及 LifeScript-iOS）从 App Bundle 中加载三类 JSON 文件。这三类文件由 `story-factory` 的编译脚本从 `story_package.json` 生成，也可以手动按本规范编写。

---

## 文件清单

| 文件名 | 必须 | 说明 |
|---|---|---|
| `books.json` | 是 | 书库索引，包含所有可用书目的元数据列表 |
| `book_<book_id>.json` | 是 | 单本书的完整元数据（人物、初始属性等） |
| `chapters_<book_id>.json` | 是 | 该书所有章节的内容（正文 + 选择节点） |
| `walkthrough_<book_id>.json` | 是 | 该书的攻略图（章节弧、指引、分路信息） |

命名规则：`book_id` 为全小写字母 + 下划线，如 `xianxia_001`、`apocalypse_001`。

---

## 1. books.json

顶层为 `Book` 对象的数组，每项描述一本书的基本信息供书库列表使用。

```json
[
  {
    "id": "xianxia_001",
    "title": "弃徒逆天",
    "author": "命书工作室",
    "cover_image_name": "cover_xianxia_001",
    "synopsis": "简介文本",
    "genre": "修仙升级",
    "tags": ["废材逆袭", "上古传承"],
    "interaction_tags": ["高互动", "战力成长", "关系推进", "攻略图"],
    "total_chapters": 1000,
    "free_chapters": 20,
    "characters": [ ... ],
    "initial_stats": { ... }
  }
]
```

### 1.1 genre（类型）

枚举值，必须完全匹配以下之一：

| 值 | 说明 |
|---|---|
| `都市逆袭` | 都市逆袭 |
| `修仙升级` | 修仙升级 |
| `悬疑生存` | 悬疑生存 |
| `职场商战` | 职场商战 |
| `末日爽文` | 末日爽文 |

### 1.2 characters（人物列表）

每项描述一个关键角色：

```json
{
  "id": "char_li_qingyun",
  "name": "李青云",
  "title": "天青宗首席弟子",
  "avatar_image_name": "avatar_li_qingyun",
  "description": "角色背景描述",
  "role": "宿敌"
}
```

`role` 枚举值：

| 值 | 说明 |
|---|---|
| `盟友` | ally |
| `宿敌` | rival |
| `红颜` | love interest |
| `师尊` | mentor |
| `家族` | family |
| `中立` | neutral |
| `反派` | antagonist |

### 1.3 initial_stats（主角初始属性）

7 个属性，值域 `0–100`：

```json
{
  "combat": 5,
  "fame": 5,
  "strategy": 20,
  "wealth": 5,
  "charm": 10,
  "darkness": 0,
  "destiny": 30
}
```

| 字段 | 中文名 | 说明 |
|---|---|---|
| `combat` | 战力 | 战斗/求生能力 |
| `fame` | 名望 | 声誉/影响力 |
| `strategy` | 谋略 | 计谋/判断力 |
| `wealth` | 财富 | 资源/财力 |
| `charm` | 魅力 | 说服/魅力值 |
| `darkness` | 黑化值 | 负面倾向，越高代价越重 |
| `destiny` | 天命值 | 主角光环/运势 |

---

## 2. book_\<book_id\>.json

与 `books.json` 中单项结构完全相同，为该书独立存储的元数据文件。
编译脚本会从 `story_package.json` 的 `book` 字段提取并写入此文件。

---

## 3. chapters_\<book_id\>.json

顶层为 `Chapter` 数组，按 `number` 升序排列。

```json
[
  {
    "id": "xianxia_001_ch0001",
    "book_id": "xianxia_001",
    "number": 1,
    "title": "逐出师门",
    "is_paid": false,
    "next_chapter_hook": "下一章钩子文本，吊起悬念（可选）",
    "nodes": [ ... ]
  }
]
```

### 3.1 节点（nodes）

`nodes` 是 `StoryNode` 的有序数组。每个节点为单键对象，键名决定节点类型。

```json
{"text": { ... }}
{"dialogue": { ... }}
{"choice": { ... }}
{"notification": { ... }}
```

#### 3.1.1 text 节点

叙事正文。

```json
{
  "text": {
    "id": "xianxia_001_ch0001_01",
    "content": "正文内容",
    "emphasis": "dramatic"
  }
}
```

`emphasis`（可选）枚举值：

| 值 | 渲染效果 |
|---|---|
| `normal`（默认）| 标准正文 |
| `dramatic` | 大字号，居中，戏剧性停顿 |
| `whisper` | 小字号，斜体，低语感 |
| `system` | 内心独白 / 系统提示风格 |

#### 3.1.2 dialogue 节点

角色对白。

```json
{
  "dialogue": {
    "id": "xianxia_001_ch0001_05",
    "character_id": "char_li_qingyun",
    "content": "哈——还真是天生废料。",
    "emotion": "讥笑"
  }
}
```

- `character_id` 必须引用当前书 `characters` 中存在的 `id`
- `emotion` 为自由文本（可选），如 `"冷笑"`、`"震惊"`、`"愤怒"`

#### 3.1.3 choice 节点

交互选择点。

```json
{
  "choice": {
    "id": "xianxia_001_ch0001_choice_01",
    "prompt": "提示语，描述当前处境",
    "choice_type": "keyDecision",
    "time_limit": 30.0,
    "choices": [ ... ]
  }
}
```

`choice_type` 枚举值：

| 值 | 说明 |
|---|---|
| `keyDecision` | 关键抉择，影响主线走向 |
| `styleChoice` | 风格选择，体现角色个性 |
| `characterPref` | 角色倾向，影响关系推进 |

`time_limit`（可选）：倒计时秒数（`Double`）。

##### Choice 条目结构

```json
{
  "id": "xianxia_001_ch0001_c01_a",
  "text": "当场质疑，要求重测",
  "description": "选项的补充说明（可选）",
  "satisfaction_type": "直接爽",
  "visible_cost": "直接与掌门对立",
  "visible_reward": "展示不屈气节",
  "risk_hint": "可能加重处罚",
  "process_label": "正面抗争",
  "is_premium": false,
  "stat_effects": [
    { "stat": "名望", "delta": 5 }
  ],
  "relationship_effects": [
    { "character_id": "char_song_xuan", "dimension": "敌意", "delta": 15 }
  ],
  "result_nodes": [ ... ]
}
```

**satisfaction_type** 枚举值：

| 值 | 说明 |
|---|---|
| `直接爽` | 即时爽点，正面压制 |
| `延迟爽` | 隐忍蛰伏，后期更大爆发 |
| `阴谋爽` | 暗中操控，智谋玩弄 |
| `碾压爽` | 绝对实力碾压 |
| `情感爽` | 情感牵绊，浪漫情节 |
| `扮猪吃虎` | 示弱藏拙，后续反转 |

**stat_effects**：`stat` 枚举值（与 initial_stats 字段一一对应的中文名）：

| JSON 值 | 对应字段 |
|---|---|
| `战力` | `combat` |
| `名望` | `fame` |
| `谋略` | `strategy` |
| `财富` | `wealth` |
| `魅力` | `charm` |
| `黑化值` | `darkness` |
| `天命值` | `destiny` |

**relationship_effects**：`dimension` 枚举值：

| JSON 值 | 说明 |
|---|---|
| `信任` | trust |
| `好感` | affection |
| `敌意` | hostility |
| `敬畏` | awe |
| `依赖` | dependence |

**result_nodes**（可选）：选项被选择后立即播放的 `StoryNode` 列表，结构与章节 `nodes` 相同。

#### 3.1.4 notification 节点

系统通知，用于提示属性变化、解锁事件等。

```json
{
  "notification": {
    "id": "xianxia_001_ch0001_n01",
    "message": "通知内容文本",
    "type": "statChange"
  }
}
```

`type` 枚举值：

| 值 | 说明 |
|---|---|
| `statChange` | 属性变化提示 |
| `relationshipChange` | 关系变化提示 |
| `itemGained` | 获得道具/资源 |
| `storyHint` | 剧情提示 |

---

## 4. walkthrough_\<book_id\>.json

攻略图文件，为 App 提供章节指引、弧线分段、分路信息。

```json
{
  "book_id": "xianxia_001",
  "title": "弃徒逆天·命运图谱",
  "stages": [ ... ],
  "chapter_guides": [ ... ]
}
```

### 4.1 stages（故事弧）

将章节分组为叙事弧，用于攻略图的阶段显示。

```json
{
  "id": "arc_01",
  "title": "觉醒篇",
  "summary": "归来初战",
  "chapter_ids": [
    "xianxia_001_ch0001",
    "xianxia_001_ch0002"
  ]
}
```

- `chapter_ids` 中的每个 ID 必须在 `chapters_<book_id>.json` 中存在
- 每个章节只能属于一个 stage

### 4.2 chapter_guides（章节指引）

每章一条记录，为 App 的首页入口和攻略图提供信息。

```json
{
  "chapter_id": "xianxia_001_ch0001",
  "stage_id": "arc_01",
  "public_summary": "第1章：大典被驱逐，在你的推动下局势演进。",
  "objective": "大典被驱逐，三年流浪，古戒指引",
  "estimated_minutes": 5,
  "interaction_count": 2,
  "visible_routes": [
    {
      "id": "route_1_a",
      "title": "正面强攻",
      "style": "直接爽",
      "unlock_hint": "偏战力",
      "payoff": "快速建立优势",
      "process_focus": "战力压制"
    }
  ],
  "hidden_route_hint": "某些选择会触发隐藏的剧情线索。"
}
```

| 字段 | 类型 | 必须 | 说明 |
|---|---|---|---|
| `chapter_id` | String | 是 | 对应章节 id |
| `stage_id` | String | 是 | 所属 stage id |
| `public_summary` | String | 是 | 显示给读者的章节摘要 |
| `objective` | String | 是 | 本章主线目标，显示于入口页 |
| `estimated_minutes` | Int | 是 | 预计阅读时长（分钟） |
| `interaction_count` | Int | 是 | 本章交互次数，用于"交互密度"统计 |
| `visible_routes` | Array | 是 | 公开分路列表（可为空数组） |
| `hidden_route_hint` | String | 否 | 隐藏路线提示文本 |

**visible_routes 字段说明：**

| 字段 | 说明 |
|---|---|
| `id` | 路线唯一 ID |
| `title` | 路线名称，显示于攻略图 |
| `style` | 爽感类型，与 `satisfaction_type` 枚举一致 |
| `unlock_hint` | 解锁条件提示（偏战力 / 偏谋略等） |
| `payoff` | 走这条路线的回报描述 |
| `process_focus` | 核心过程关键词 |

---

## 5. ID 命名约定

| 类型 | 格式 | 示例 |
|---|---|---|
| 书 ID | `<genre>_<seq>` | `xianxia_001` |
| 章节 ID | `<book_id>_ch<4位数>` | `xianxia_001_ch0001` |
| 节点 ID | `<chapter_id>_<2位数>` | `xianxia_001_ch0001_01` |
| 选择节点 ID | `<chapter_id>_choice_<2位数>` | `xianxia_001_ch0001_choice_01` |
| 选项 ID | `<chapter_id>_c<choice序>_<字母>` | `xianxia_001_ch0001_c01_a` |
| 结果节点 ID | `<chapter_id>_r<choice序>_<字母>` | `xianxia_001_ch0001_r01_a` |
| 角色 ID | `char_<拼音>` | `char_li_qingyun` |
| Stage ID | `arc_<2位数>` | `arc_01` |
| Route ID | `route_<章节序>_<字母>` | `route_1_a` |

---

## 6. 编译流程

源文件 `story-factory/projects/<book_id>/story_package.json` 包含上述所有数据，通过编译脚本拆分输出：

```bash
python3 story-factory/scripts/compile_story_package.py \
  story-factory/projects/<book_id>/story_package.json \
  --resources-dir LifeScript-Solo/Sources/LifeScriptSolo/Resources \
  --output-dir story-factory/projects/<book_id>/build
```

输出文件：

| 文件 | 写入位置 |
|---|---|
| `book_<book_id>.json` | App 资源目录 |
| `chapters_<book_id>.json` | App 资源目录 |
| `walkthrough_<book_id>.json` | App 资源目录 |
| `qa_report.json` | `projects/<book_id>/build/` |
