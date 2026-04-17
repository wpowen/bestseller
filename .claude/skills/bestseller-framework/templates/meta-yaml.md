# Template · meta.yaml

> 每个小说项目根目录必备：`output/ai-generated/{slug}/meta.yaml`

```yaml
# 必填基础
slug: fen-xin-jue                    # pinyin-kebab-case
title: 焚心诀
subtitle: 以寿换道，以血烧心          # optional
genre: 玄幻                          # 玄幻 | 仙侠 | 都市 | 科幻 | 历史 | 悬疑 | 奇幻 | 言情 | 轻小说
sub_genre: 修真·复仇·悬疑            # optional 多标签
language: zh-CN                      # zh-CN | en-US

# 体量目标
target_chapters: 30
target_total_words: 180000
words_per_chapter:
  min: 5000         # HARD LOWER BOUND — invariant
  target: 6400
  max: 9000
scenes_per_chapter:
  target: 4         # 2 post-climax, 3 default, 4 climax, 5 max
volumes: 1
acts: 1

# 进度游标（随写随更）
current_chapter: 1
current_volume: 1
status: in_progress  # planning | in_progress | paused | completed

# 拓扑映射
hierarchy_rationale: |
  30 章 → 单卷单幕（§15.1）。
  配置 2–3 位配角 + 1 股势力（§17）。

# 冲突相分布（按章范围）
conflict_phases:
  - ch_range: "1-8"
    phase: survival
  - ch_range: "9-16"
    phase: political_intrigue
  - ch_range: "17-24"
    phase: betrayal
  - ch_range: "25-30"
    phase: internal_reckoning

# 胜负节奏锚点
volume_win_loss_rhythm:
  vol_01:
    opening_win: true
    midsection_loss_band: "11-22"
    penultimate_major_loss_chapter: 27
    final_win_chapter: 30

# 质量门槛（可沿框架默认）
quality_thresholds:
  scene_min_score: 0.70
  chapter_coherence_min_score: 0.75
  max_scene_revisions: 2
  consistency_check_chapter: 20

# 快照 / 压缩策略
snapshot_policy:
  character_snapshot_every_chapters: 10   # 长篇 > 300 ch 改 5
  rolling_summary_every_chapters: 25      # > 300 ch 强制
  meta_summary_every_chapters: 100        # > 1000 ch 强制

# 风格锁定
prompt_pack: xianxia
primary_pov: close_third
tense: past

# 元信息
writing_start_date: "2026-04-16"
generated_by: bestseller-framework-skill
override_flags:
  win_loss_override: false
  allow_pov_switch_over_200_words: false
```

## 校验规则

- `target_chapters` ≤ 50 → `acts` 必须为 1，`volumes` 必须为 1
- `target_chapters` > 50 → `acts ≥ 3`，必须有 `story-bible/act-plan.md`
- `volumes` > 3 → 必须有 `story-bible/world-expansion.md`
- `words_per_chapter.min` **永远** = 5000，不得调低
- `current_chapter` ≤ `target_chapters`；完成后置 `status: completed`
