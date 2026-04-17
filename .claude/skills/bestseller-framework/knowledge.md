# Knowledge — Canon / Timeline / Snapshots

> summarizer 角色的操作手册。每章完稿后运行。

## 1. Canon Facts

### 定义

单调增长的"地面真相"。subject/predicate/value_json 三元组。任何后续章节必须与之相容。

### 写入

- **只追加，不修改**
- 如需"更新"，另起一条 `supersedes ch=N`
- `valid_from_chapter_no` 严格 ≥ 触发章

### 文件

`knowledge/canon-facts.md`（append-only）

### 示例条目

```markdown
## Chapter 7

- **{subject: 林风}** `has_cultivation_level` = "炼气四层" (valid_from_ch=7, supersedes ch=1)
- **{subject: 天灵宗·藏经阁}** `location` = "北山后崖" (valid_from_ch=7)
```

### 写作/阅读规则

- writer 生成 scene 前**必读**所有 subject 出现在本场的 canon facts
- critic 在 `logical_consistency` 维度比对本章内容是否违反 canon
- 如果必须违反（罕见——如幻觉/被篡改记忆桥段），在正文明确标注"不可靠叙述"，并**不**更新 canon

## 2. Timeline Events

### 定义

故事内部时间轴上的显著事件。

### 字段

- `event_type`：family_massacre / rescue / breakthrough / assassination / betrayal / reconciliation / ……
- `story_time_label`：如 "玄真·永泰三十年冬"，必要时具体到日
- `consequences`：列表，若干短句

### 文件

`knowledge/timeline.md`

### 作用

- 防止出现时间矛盾（如"三日后"却跨了季节）
- 为 flashback 场景提供对齐点

## 3. Character State Snapshots

### 写入频率

- 默认：每 10 章
- 长篇 > 300 章：每 5 章
- 章数 ≤ 30 的短篇：ch 10, 20, 30 各一次即可

### 字段

| 字段 | 含义 |
|------|------|
| `arc_state` | 角色当前弧位置的一句话刻画 |
| `emotional_state` | 主导情绪 |
| `physical_state` | 伤势 / 体能 / 寿元（如适用）|
| `power_tier` | 当前境界 / 实力等级 |
| `trust_map` | `dict[other_character, float in [-1, 1]]` |
| `beliefs` | 当前持有的信念（含错误信念）|
| `knowledge` | 当前知道的关键事实 |

### 文件

`knowledge/character-snapshots/after-ch-{NNN}.md`

### 作用

下一次写到该角色时，把**上一次 snapshot** 作为 Tier 1 上下文注入 writer，防止性格漂移与认知穿越。

## 4. Chapter State Snapshots

> 与 character snapshots 区别：chapter 级 snapshot 是"所有硬事实"冻结点。

### 字段

- `facts`：列表，`HardFactContext[]`，每条含 subject + key_state + value
- 典型条目："主角当前在玄济宗内门宿舍"、"阴司阁刺客已撤退"、"第 X 伏笔已种"

### 文件

直接保存在本章 md 末尾的 `<!-- chapter-state-snapshot -->` 注释块里，或 `knowledge/chapter-snapshots/after-ch-{NNN}.md`。

### 作用

下一章 writer 的**第一条**上下文。

## 5. Hybrid RAG Retrieval（代码层，Mode A 参考）

配置：

- **权重**：60% 向量 + 20% 词法 + 20% 结构
- **向量**：pgvector HNSW，1024 维（BAAI/bge-m3）
- **词法**：拉丁三元、CJK 二元
- **结构**：源类型加权 `scene_context > scene_draft > chapter_draft > character > canon_fact`
- **top_k = 12**，`min_score = 0.55`，`chunk_size = 800`，`chunk_overlap = 120`，`candidate_limit = 40`

## 6. Context Budget Tiers

| Tier | 是否必带 | 内容 |
|------|---------|------|
| 1 | 必带 | scene/chapter 契约、写作方法论、参与者 canon facts |
| 2 | 额度够就带 | 近 6 场 scene 摘要、emotion_track、反派当前计划 |
| 3 | 最低优先 | 全部 story bible、plot arcs、检索结果 |

总预算：`context_budget_tokens = 8000`。

## 7. Knowledge 工作流（Mode B 在每章末尾执行）

1. **自提** canon facts（本章新出现的：权限 / 关系 / 地点 / 事件 / 数值）
2. **比对** 现有 canon facts：有冲突 → `supersedes` 写法
3. **追加** timeline event（本章主事件 + 关键支点）
4. **判定** 是否到 snapshot 周期（每 10 / 5 章）
5. **更新** meta.yaml 的 `current_chapter`
6. **更新** volume README 的 chapter index 状态

## 8. 错误模式（规避）

- ❌ 把推测写进 canon（"大概是……" 不入库）
- ❌ 省略 `valid_from_chapter`
- ❌ 在 snapshot 中写 "未来将会……"（只存当下状态）
- ❌ 让角色 `knowledge` 含有尚未揭示的事实
- ❌ 同一 subject-predicate 出现多条同时有效（需用 supersedes 收敛）
