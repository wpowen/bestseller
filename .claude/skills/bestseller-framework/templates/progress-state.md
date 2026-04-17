# Template · `progress.yaml`

> 位置：`output/ai-generated/{novel-slug}/progress.yaml`
> 用途：orchestrator 的**单一事实源**。每完成一步必须回写。支持断点续跑。
> 语义：详见 [../orchestration.md § 3](../orchestration.md)。

---

## 1. 完整 Schema（字段级注释）

```yaml
# ─────────────────────────────────────────────
# 基本身份
# ─────────────────────────────────────────────
project_slug: fen-xin-jue           # 与 meta.yaml.slug 一致
created_at: 2026-04-16T11:58:00Z
last_updated: 2026-04-16T14:32:00Z
orchestrator_version: "2026.04.16"   # 与 SKILL.md version 对齐

# ─────────────────────────────────────────────
# 当前状态
# ─────────────────────────────────────────────
state: WRITE_CHAPTER                 # 状态机当前节点（详见 orchestration.md § 1）
next_action: write_chapter           # 小写；与 state 对应的执行动作
next_action_args:
  volume: 1
  chapter: 7

# ─────────────────────────────────────────────
# 项目刻度
# ─────────────────────────────────────────────
target_chapters: 30
target_volumes: 1
current_chapter: 7
current_volume: 1

# ─────────────────────────────────────────────
# 规划阶段进度
# ─────────────────────────────────────────────
# 每个阶段的值 ∈ {pending, in_progress, done, skipped, failed}
# skipped: 条件不满足（如 ≤50 章的 act-plan）
stages:
  init: done
  plan_premise: done
  plan_world: done
  plan_characters: done
  plan_volume_plan: done
  plan_act: skipped                  # target_chapters ≤ 50
  plan_world_expansion: skipped      # volumes ≤ 3
  plan_writing_profile: done
  plan_vol_01_readme: done
  # 更多卷按需添加：plan_vol_02_readme: pending 等
  export: pending
  done: pending

# ─────────────────────────────────────────────
# 章节级进度
# ─────────────────────────────────────────────
# key 用三位数字符串 "001"..."NNN"，与文件名 ch-NNN 对齐
chapters:
  "001":
    state: done                      # pending | drafting | reviewing | rewriting | committed | done | failed
    rewrite_attempts: 0              # 当前章累计重写次数；上限 2
    accept_on_stall: false
    word_count: 6184
    final_scores:
      hook_strength: 0.82
      conflict_clarity: 0.78
      emotional_movement: 0.81
      payoff_density: 0.73
      voice_consistency: 0.88
    canon_facts_added: 3
    timeline_events_added: 1
    committed_at: 2026-04-16T12:05:00Z
  "002":
    state: done
    rewrite_attempts: 1
    accept_on_stall: false
    word_count: 6420
    final_scores: {hook_strength: 0.80, conflict_clarity: 0.81, emotional_movement: 0.79, payoff_density: 0.74, voice_consistency: 0.86}
    canon_facts_added: 2
    timeline_events_added: 1
    committed_at: 2026-04-16T12:42:00Z
  "003":
    state: done
    # ...
  "007":
    state: drafting                  # ← 当前正在处理
    started_at: 2026-04-16T14:30:00Z

# 未开始的章节可留空；orchestrator 遇到未登记的章号视为 pending

# ─────────────────────────────────────────────
# 修复队列（MILESTONE_CHECK 审计发现问题时追加）
# ─────────────────────────────────────────────
repair_queue:
  # 示例（当前为空）：
  # - id: R-001
  #   created_at: 2026-04-16T13:20:00Z
  #   source_audit: consistency-audit-ch-020
  #   issue_type: canon_conflict      # canon_conflict | knowledge_anachronism | clue_unpaid | character_voice_drift
  #   affected_chapter: 18
  #   affected_subject: 秦墨
  #   description: "ch-018 描述秦墨右臂旧伤，但 ch-004 canon fact 记录为左臂"
  #   proposed_fix: "ch-018 改为左臂"
  #   attempts: 0
  #   status: pending                 # pending | in_progress | resolved | escalated

# ─────────────────────────────────────────────
# 失败记录（未恢复的）
# ─────────────────────────────────────────────
failures: []
  # 示例：
  # - state: WRITE_CHAPTER
  #   chapter: 12
  #   reason: "字数连续 2 次扩写仍 < 5000"
  #   attempt_count: 3
  #   escalated_at: 2026-04-16T14:10:00Z

# ─────────────────────────────────────────────
# 需用户决策 —— 非 null 时 orchestrator 必停
# ─────────────────────────────────────────────
human_decision_pending: null
  # 示例：
  # human_decision_pending:
  #   asked_at: 2026-04-16T14:15:00Z
  #   context: "ch-012 转折点"
  #   question: "第 12 章是否让沈青阙当场揭穿秦墨？"
  #   options:
  #     - id: A
  #       text: "当场揭穿 —— 冲突前置，ch-13 起进入公开对立线"
  #       tradeoff: "破坏后续 ch-18 的反转惊喜"
  #     - id: B
  #       text: "隐忍旁观 —— 延迟到 ch-20 揭穿"
  #       tradeoff: "中段 5 章缺明线冲突"
  #     - id: C
  #       text: "半揭不揭 —— 只让主角察觉"
  #       tradeoff: "推荐，但写作难度高"
  #   recommended: C

# ─────────────────────────────────────────────
# 里程碑副产物追踪
# ─────────────────────────────────────────────
milestones:
  character_snapshots_written:       # 每 10 章一份
    - {chapter: 10, path: "knowledge/character-snapshots/after-ch-010.md", written_at: "..."}
  rolling_summaries_written:         # 每 25 章一份
    # - {chapter_range: "1-25", path: "knowledge/rolling-summary.md#block-1", written_at: "..."}
  consistency_audits:                # 每 20 章一份
    - chapter: 20
      path: "reviews/consistency-audits.md#audit-1"
      result: PASS                   # PASS | FAIL_HEALED | FAIL_ESCALATED
      clue_payoff_ratio: 0.70
      canon_conflicts: 0
      knowledge_anachronisms: 0
      written_at: "..."

# ─────────────────────────────────────────────
# 资源消耗追踪（用于判断何时需要保存并停下）
# ─────────────────────────────────────────────
resource_usage:
  tool_calls_this_session: 14
  last_step_tokens: 4821
  total_chapters_this_session: 3     # 本会话已完成的章数
  session_started_at: 2026-04-16T14:00:00Z
```

---

## 2. 最小初始态（INIT 后立刻写入）

```yaml
project_slug: fen-xin-jue
created_at: 2026-04-16T11:58:00Z
last_updated: 2026-04-16T11:58:00Z
orchestrator_version: "2026.04.16"
state: PLAN_PREMISE
next_action: plan_premise
next_action_args: {}
target_chapters: 30
target_volumes: 1
current_chapter: 0
current_volume: 1
stages:
  init: done
  plan_premise: pending
  plan_world: pending
  plan_characters: pending
  plan_volume_plan: pending
  plan_act: skipped
  plan_world_expansion: skipped
  plan_writing_profile: pending
  plan_vol_01_readme: pending
  export: pending
  done: pending
chapters: {}
repair_queue: []
failures: []
human_decision_pending: null
milestones: {character_snapshots_written: [], rolling_summaries_written: [], consistency_audits: []}
resource_usage: {tool_calls_this_session: 0, total_chapters_this_session: 0, session_started_at: "2026-04-16T11:58:00Z"}
```

---

## 3. 状态字段值域（参考）

### `state`（大写）

```
INIT
PLAN_PREMISE | PLAN_WORLD | PLAN_CHARACTERS | PLAN_VOLUME_PLAN
PLAN_ACT | PLAN_WORLD_EXPANSION | PLAN_WRITING_PROFILE
PLAN_VOL_README
WRITE_CHAPTER | REVIEW_CHAPTER | REWRITE_CHAPTER
EXTRACT_KNOWLEDGE | COMMIT_CHAPTER | MILESTONE_CHECK
ADVANCE_CHAPTER | ADVANCE_VOLUME
DRAIN_REPAIR_QUEUE
EXPORT | DONE
```

### `next_action`（小写；与 state 的执行动作对应）

```
plan_premise | plan_world | ... | write_chapter | review_chapter |
rewrite_chapter | extract_knowledge | commit_chapter |
milestone_check | advance_chapter | advance_volume |
drain_repair_queue | export | report_done
```

### `chapters[N].state`（小写）

```
pending | drafting | drafted | reviewing | reviewed |
rewriting | extracting | committing | committed | done |
failed | stalled
```

---

## 4. 回写规则（CRITICAL）

每执行完一个原子步骤必须：

1. 读现有 `progress.yaml`
2. 修改对应字段
3. 整文件覆盖写（不要只追加片段）
4. `last_updated` 刷新为当前 ISO-8601
5. 写盘后才能进入下一步

**禁止**在内存里累积多步后再一次性写盘——中断后无法恢复。

---

## 5. 损坏恢复

如果读取时发现 `progress.yaml` 字段缺失 / 格式错：

1. **不要**重新初始化
2. 比对 `meta.yaml::current_chapter` + `volumes/vol-NN/README.md` 的状态列 + 磁盘上实际的 `ch-NNN-*.md` 文件
3. 以磁盘实际状态为准，重建 `progress.yaml`
4. 重建后的 `state` 置为 `ADVANCE_CHAPTER`（保守地从上一次已提交的章之后继续）
