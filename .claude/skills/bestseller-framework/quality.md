# Quality Gates — 评分与重写

> critic 与 editor 角色的操作手册。每章完成后**必须**走一遍。

## 1. Scene Review — 5 核心维度

| 维度 | 阈值 | 考察点 |
|------|------|-------|
| `hook_strength` | ≥ 0.70 | 开场是否引起阅读动机（未答之问 / 时限 / 陌生者 / 身体失控）|
| `conflict_clarity` | ≥ 0.70 | 本场冲突对象、赌注是否清晰可述 |
| `emotional_movement` | ≥ 0.70 | 场景是否推动了 POV 的情绪曲线 |
| `payoff_density` | ≥ 0.70 | 单位字数内有效推进 / 揭示密度 |
| `voice_consistency` | ≥ 0.70 | 与 `voice_profile` 一致；无 OOC |

完整 31 维（选做）：包括 `show_dont_tell`、`pov_consistency`、`methodology_compliance`、`thematic_resonance`、`worldbuilding_integration`、`sensory_detail`、`dialogue_purpose`、`pacing_fit`、`foreshadow_discipline`、`stakes_visibility`、`cliché_avoidance`、`originality`、`logical_consistency`、`character_motivation`、`scene_exit_hook`、……

## 2. Chapter Review — 4 核心维度

| 维度 | 阈值 | 考察点 |
|------|------|-------|
| `main_plot_progression` | ≥ 0.75 | 主线是否推进（哪怕半步）|
| `subplot_progression` | ≥ 0.75 | 当前活跃副线是否推进 |
| `ending_hook_effectiveness` | ≥ 0.75 | 章末 hook 是否足以拉下一章 |
| `volume_mission_alignment` | ≥ 0.75 | 是否契合 volume_goal / volume_theme |

## 3. Rewrite Logic

```
draft → critic →
  任一维 < threshold
    → 生成 RewriteTask（指出具体维度 + 举例 + 改写策略）
    → editor 带 "=== reference only ===" 围栏执行
    → 重新 critic
  循环：
    max 2 次
    or 改善 < 3%（stall）→ accept 最高分版本（accept_on_stall=True）
```

### RewriteTask 内容

- dimension: 哪一维
- current_score / target_score
- problem_examples: 直接引用 draft 中问题段落（200 字内）
- rewrite_strategy: editor 该做的具体动作
- scope: 限定改写段落范围，不可全章重写

### Editor 约束

- **保留原 voice**（editor temp=0.40，比 writer 低）
- 改写 prompt 用 `=== reference only ===` 围栏包裹策略文本，防止 LLM 把 meta 语言漏进正文
- 最多改写 2 次
- 最小改善阈值 3%（不到即判为 stall）

## 4. Word Count Gate

critic 前先做字数守门：

```
if word_count < 5000:
    force rewrite (editor, expand)
    strategies:
      - 加场景（注意 exit_state → entry_state 衔接）
      - 扩内心（对关键决策前的犹豫）
      - 扩对白（推进冲突，非寒暄）
    DO NOT pad with filler description
```

## 5. Project Consistency Audit（每 20 章）

| 审查项 | 规则 |
|-------|------|
| character arc trajectory | 各角色 arc 演进轨迹是否连续 |
| canon fact monotonicity | 是否仅通过 `supersedes` 追加而非覆盖 |
| clue → payoff ratios | 已种伏笔数 / 已偿付数 是否健康（偿付率 60% +）|
| knowledge state integrity | 无角色预知未来揭示 |
| relationship evolution | 关系线是否符合 conflict_map |
| lore consistency | 世界规则无矛盾 |
| POV voice drift | 主角 voice 与首章比较漂移 < 15% |

审计结果写 `reviews/consistency-audits.md`；触发 `run_project_repair()` 的前置条件：任一审查项失败。

## 6. 长篇的滚动压缩

> 300 章：每 25 章压缩一次
> 目标：保留情节骨架与未偿伏笔；削减文字性细节

```
rolling-summary/
├── summary-ch-001-025.md
├── summary-ch-026-050.md
└── ...
```

压缩规则：
- 每章压至 200–400 字
- 保留：已偿伏笔、新登场角色、新地点、主角重要抉择、关系变化、power 变化
- 舍去：打斗过程、景物描写、非关键对白

## 7. Self-Critic Checklist（没有外部 critic 时自查）

每章收笔后跑一遍：

- [ ] 字数 ≥ 5000
- [ ] POV 未无故切换
- [ ] 每次突破带代价账
- [ ] 开场 / 结尾 hook 类型明确
- [ ] 对白比例 25–45%
- [ ] 世界法则渗透 ≥ 2 条
- [ ] 无禁用词 / 无金手指 / 无系统文本
- [ ] `scores` 五维 ≥ 0.70（如自评低于阈值，标 `status: rework`）
- [ ] canon facts 追加条目
- [ ] volume README 表格状态更新

## 8. Scores 真实性

- **写入 frontmatter 的 scores 必须是真实自评**，不可恒为 0.95 之类的"虚报好看分"
- 低于 0.70 的章应 `status: rework`，并在 [reviews/scene-reviews.md](../../../reviews/scene-reviews.md) 中记录原因
