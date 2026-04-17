# Template · canon-facts.md / timeline.md / character-snapshots

> 三份知识层文件的骨架。

---

## 1. canon-facts.md（仅追加）

```markdown
# Canon Facts · {书名}

> Append-only. Never modify past entries. Superseding entries MUST carry higher `valid_from_chapter` and reference the one they supersede.

---

## Chapter 1

- **{subject: 主角}** `age` = 6 (valid_from_ch=1)
- **{subject: 主角}** `status` = "江家嫡子，灭门幸存" (valid_from_ch=1)
- **{subject: 主角.mother}** `last_words` = "今慕已暝" (valid_from_ch=1)
- **{subject: 蟾翅针}** `attribute` = "阴司阁秘铸暗器" (valid_from_ch=1)

## Chapter 7

- **{subject: 秦墨}** `true_identity` = "玄济宗隐世长老" (valid_from_ch=7, supersedes ch=1 "surface_identity: 瘸腿老乞丐")
- **{subject: 主角}** `power_tier` = "炼气二层 → 炼气三层" (valid_from_ch=7)

---

## Rules for Authors

- 不以"大概是……"语气入库；**只有明确揭示**的事实才记
- 寿元 / 灵力 / 关系数值的每一次变动都记
- 主角对 {某关键人物} 的认识分阶段：Ch A–B **falsely_believes** "..."；Ch C 之后 **knows** "..."
```

---

## 2. timeline.md

```markdown
# Timeline · {书名}

> Append-only chronology. `story_time_label` 是故事内部时间。

## T-11 年（{某纪年}）· Ch 1

| event_type | story_time | consequences |
|-----------|-----------|--------------|
| family_massacre | {地点}·某夜 | [短句 1] / [短句 2] / ... |
| rescue | 次日凌晨 | 主角被救，得残卷 |

## T-10 至 T-1 年 · {描述期}

| event_type | story_time | consequences |
|-----------|-----------|--------------|
| hidden_cultivation | 落雁岭 | 炼气一层 → 二层 |

## T-0（当下，Ch 2 起）

...

---

## Rules

- 所有 "想起 X" 回忆场景需对照已有事件
- 地域 / 距离 / 时间标签一经写入即锁定，后续章节必须遵守
```

---

## 3. character-snapshots/after-ch-{NNN}.md

```markdown
# Character Snapshot · after ch {NNN}

## {角色 A 名}

| 字段 | 值 |
|------|---|
| arc_state | "一句话刻画当前弧位" |
| emotional_state | "主导情绪（不超 3 词）" |
| physical_state | "伤 / 体能 / 寿元（如适用）" |
| power_tier | "当前境界" |
| trust_map | { "{角色 B}": 0.62, "{角色 C}": -0.35 } |
| beliefs | - "信念 1"<br>- "信念 2（可含错信）" |
| knowledge | - "知 1"<br>- "知 2" |
| recent_decision | "上一次关键选择" |
| open_question | "当前最想知道的事" |

## {角色 B 名}
...

---

## 快照周期

- 每 10 章一次（短篇）
- 每 5 章一次（> 300 章长篇）
- 仅含**书中显著活跃**的角色（7 日内有戏份）
- 不猜测未展示的状态
```

---

## 4. rolling-summary-NNN-NNN.md（> 300 章强制）

```markdown
# Rolling Summary · ch {A}–{B}

> 目标：保留情节骨架 + 未偿伏笔，压缩文字性细节
> 每章 200–400 字

## ch {A}

[200–400 字，保留：已偿伏笔 / 新登场 / 新地点 / 主角关键抉择 / 关系变化 / power 变化]

## ch {A+1}

...

---

## 未偿伏笔 Roll-Over

| # | 种植章 | 当前状态 | 预计偿付章 |
```

---

## 5. consistency-audits.md（每 20 章强制）

```markdown
# Consistency Audit · after ch {N}

## 1. Canon Monotonicity
- [ ] 过去 20 章 canon 条目**仅**通过 supersedes 追加 — Pass / Fail
- 问题：[列出]

## 2. Knowledge Integrity
- [ ] 无角色预知未来揭示 — Pass / Fail

## 3. Character Arc Trajectory
- [ ] 各角色 arc 演进轨迹连续 — Pass / Fail
- 漂移：角色 X 的 voice 与首章比较漂移 {PCT}%

## 4. Clue → Payoff Ratio
- 已种伏笔：N 条
- 已偿付：M 条
- 偿付率：{M/N}%（目标 ≥ 60%）
- 即将偿付（下 20 章内）：[...]

## 5. Relationship Evolution
- 与 `story-bible/characters.md § Conflict Map` 一致性：Pass / Fail

## 6. Lore Consistency
- 世界规则是否矛盾：Pass / Fail

## 7. POV Voice Drift
- 主角 voice vs 首章相似度：{PCT}%

## Summary
- Overall: Pass / Requires Repair
- 若 Requires Repair，触发 `reviews/chapter-reviews.md` 中具体章的 rewrite 入场
```
