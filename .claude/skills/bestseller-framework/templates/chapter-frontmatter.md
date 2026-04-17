# Template · 章节 frontmatter + 正文骨架

> 每章文件：`output/ai-generated/{slug}/volumes/vol-{NN}-{vslug}/ch-{NNN}-{cslug}.md`

```markdown
---
volume: 1
chapter: 3
title: "初入宗门"
slug: "chu-ru-zong-men"
scenes: 4
word_count: 6400                    # 实际字数，MUST be ≥ 5000
status: approved                    # draft | rework | approved
revision: 1
chapter_phase: setup                # hook | setup | escalation | twist | climax | resolution_hook
conflict_phase: survival            # survival | political_intrigue | betrayal | faction_war | existential_threat | internal_reckoning
pacing_mode: build                  # build | accelerate | climax | breathe
emotion_phase: compress             # compress | release
is_climax: false
pov_character: 江晚
scores:
  hook_strength: 0.82
  conflict_clarity: 0.78
  emotional_movement: 0.80
  payoff_density: 0.75
  voice_consistency: 0.88
chapter_review:
  main_plot_progression: 0.80
  subplot_progression: 0.76
  ending_hook_effectiveness: 0.82
  volume_mission_alignment: 0.78
contract:
  main_plot_progress: "主角通过考核正式成为宗门弟子"
  subplot_progress: "与二师兄初次冲突埋线"
  emotion_shift: "紧张 → 如释重负 → 警惕"
  hook: "二师兄离开时意味深长的一句话"
clues_planted:
  - id: C-009
    description: "二师兄袖口的青鳞暗纹"
clues_paid_off: []
canon_facts_added:
  - "{江晚} joined 玄济宗外门 (ch=3)"
  - "{秦墨} is 玄济宗隐世长老 (ch=3, supersedes ch=1)"
generated_at: "2026-04-16T12:00:00Z"
---

# 第三章 初入宗门

## 场景一 · 山门前的队列

[1600 字正文]

## 场景二 · 气感测试

[1800 字正文]

## 场景三 · 宿舍初见

[1500 字正文]

## 场景四 · 师兄的目光

[1500 字正文]

<!-- chapter-state-snapshot
facts:
  - 江晚 = 玄济宗外门弟子
  - 江晚 = 炼气二层（未突破）
  - 二师兄身份未揭（疑点：青鳞暗纹与阴司阁刺客同源？）
  - 青阙已知道主角落脚处
  - 陆承川尚未登场
-->
```

## 字段说明

| 字段 | 约束 |
|------|------|
| `word_count` | **真实**字数，绝不虚报。未达 5000 → 不得 `status: approved` |
| `scores` | **真实**自评，低于阈值标 `status: rework` |
| `canon_facts_added` | 与 `knowledge/canon-facts.md` 对应章节条目一致 |
| `clues_planted` / `clues_paid_off` | 与 `story-bible/plot-arcs.md` 的偿付表对齐 |
| `pov_character` | 默认主角；若本章特批切换，必须声明 |

## 场景标题约定

- 四场：场景一～场景四
- 三场：场景一、场景二、场景三
- 两场：上半 / 下半（breathe 章可用）
- 标题后接 `·` 简述（如"山门前的队列"）

## 末尾 snapshot 注释块

- 所有 `chapter-state-snapshot` 写入 HTML 注释块
- 作为下一章 writer 的 Tier 1 上下文直接读入
- 不得出现"未来将发生……" — 仅存当下状态
