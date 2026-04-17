# Output — Mode B 目录规范

> Mode B 产出的**唯一**落地根：`output/ai-generated/{novel-slug}/`
> 其他目录一律禁止（特别是仓库源码 `src/` / 配置 `config/` / 文档 `docs/`）。

## 1. 根目录结构

```
output/ai-generated/{novel-slug}/
├── README.md                       顶层总览
├── meta.yaml                       target_chapters / current_chapter / word_count / volumes / acts
├── story-bible/
│   ├── premise.md                  logline / pitch / stakes
│   ├── world.md                    rules / power_system / locations / factions
│   ├── characters.md               cast sheets（goal / fear / voice / arc）
│   ├── plot-arcs.md                main + subplots + clue→payoff 表
│   ├── volume-plan.md              所有卷（标 win/loss 节奏）
│   ├── act-plan.md                 target_chapters > 50 时强制
│   ├── world-expansion.md          volumes > 3 时强制（VolumeFrontier / DeferredReveal / ExpansionGate）
│   └── writing-profile.md          POV / tense / tone / dialogue 比例 / taboo words
├── volumes/
│   ├── vol-01-{volume-slug}/
│   │   ├── README.md               卷内章节索引
│   │   └── ch-NNN-{chapter-slug}.md
│   └── vol-NN-{volume-slug}/
├── knowledge/
│   ├── canon-facts.md              只追加
│   ├── timeline.md                 故事时间线
│   ├── rolling-summary.md          每 25 章压缩（长篇）
│   └── character-snapshots/
│       └── after-ch-NNN.md         每 10 章（长篇每 5 章）
├── reviews/
│   ├── scene-reviews.md
│   ├── chapter-reviews.md
│   └── consistency-audits.md       每 20 章
└── exports/
    ├── full-novel.md
    └── full-novel.epub             如用户请求
```

## 2. 命名规范

- `novel-slug`：中文标题 → 全拼小写连字符；英文标题 → kebab-case
- `vol-{NN}`：两位数（01, 02, …）
- `ch-{NNN}`：三位数（001, 002, …）
- `{volume-slug}` / `{chapter-slug}`：同 novel-slug 规则

## 3. meta.yaml 必填字段

```yaml
slug: fen-xin-jue
title: 焚心诀
genre: 玄幻
target_chapters: 30
target_total_words: 180000
words_per_chapter:
  min: 5000
  target: 6400
  max: 9000
volumes: 1
acts: 1
current_chapter: 1
current_volume: 1
status: in_progress    # planning | in_progress | completed | paused
conflict_phases:
  - ch_range: "1-8"
    phase: survival
volume_win_loss_rhythm:
  vol_01:
    opening_win: true
    midsection_loss_band: "11-22"
    penultimate_major_loss_chapter: 27
    final_win_chapter: 30
quality_thresholds:
  scene_min_score: 0.70
  chapter_coherence_min_score: 0.75
  max_scene_revisions: 2
  consistency_check_chapter: 20
snapshot_policy:
  character_snapshot_every_chapters: 10
  rolling_summary_every_chapters: 25
prompt_pack: xianxia
primary_pov: close_third
tense: past
```

## 4. 章节文件 frontmatter

见 [templates/chapter-frontmatter.md](templates/chapter-frontmatter.md)。`word_count` 必须 **≥ 5000** 且写真实值；`scores` 写真实自评。

## 5. Volume README 必含

- 章节索引表（`#` / 标题 / phase / conflict_phase / 胜负 / 锚点 / 状态）
- 每章详细 outline（conflict_summary + scenes[] + hook_type + estimated_words + pacing_mode + emotion_phase + is_climax）

## 6. 同步约束

每章生成后**原子性地**更新：

1. `volumes/vol-NN/ch-NNN.md`（新文件）
2. `volumes/vol-NN/README.md`（状态列改 drafted / approved）
3. `meta.yaml` 的 `current_chapter` + `current_volume`
4. `knowledge/canon-facts.md`（追加）
5. `knowledge/timeline.md`（追加）
6. 若到 snapshot 周期：`knowledge/character-snapshots/after-ch-NNN.md`

**禁止**半写状态停留——每章要么完整（含评分 + canon 追加），要么整体回滚。

## 7. 禁止事项

- ❌ 写进 `src/` / `tests/` / `config/` / `docs/` / 其他任何仓库源码路径
- ❌ 创建 `output/ai-generated/*.md` 的扁平平层文件（必须在 novel-slug 子目录内）
- ❌ 覆盖已有的 novel-slug 目录（若用户要求"重写"，要求明确并先备份）
- ❌ 用户说"继续"时凭空造目录——若已有同名项目，**读取 meta.yaml 的 current_chapter** 以续写
