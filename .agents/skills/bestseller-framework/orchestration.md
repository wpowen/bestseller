# Orchestration — 自主执行层（Agent Loop）

> 本文定义 Mode B 下的**自主执行流程**：用户只给一句"帮我写 30 章 xx 小说"，orchestrator 就把剩下的所有事做完（规划 → 写作 → 评分 → 重写 → 知识抽取 → 提交 → 导出），直到告知用户"完成"。
> 本层覆盖 planner / writer / critic / editor / summarizer 五个角色的**调度与状态管理**，它自己不生成内容，而是驱动其他角色按顺序生成内容。

---

## 0. 为什么需要 orchestrator

没有 orchestrator 时，每次 LLM 交互只做一步——写完一章后用户还得手动说"继续"。30 章小说要手动说 100 次"继续"。

有 orchestrator 后：
- 入参只有 **genre / title / target_chapters**（缺一就问用户）
- 它把整个项目切成**可恢复、可跟踪**的状态节点
- 每次对话恢复时自动从 `progress.yaml` 读取续跑点
- 一口气能跑多少步跑多少步（受平台工具调用上限约束）
- 到达"未决用户决策"或"全书完成"时才停下

---

## 1. 状态机（State Machine）

一次完整项目的生命周期按下列状态**顺序**推进。每个状态是原子的：要么完成要么未开始，绝不"半完成"。

```
┌──────────────────────────────────────────────────────────────┐
│                                                                │
│  INIT                                                          │
│   │                                                            │
│   ▼                                                            │
│  PLAN_PREMISE → PLAN_WORLD → PLAN_CHARACTERS                   │
│   │                                                            │
│   ▼                                                            │
│  PLAN_VOLUME_PLAN                                              │
│   │                                                            │
│   ├─→ PLAN_ACT            (target_chapters > 50)               │
│   │                                                            │
│   ├─→ PLAN_WORLD_EXPANSION (volumes > 3)                       │
│   │                                                            │
│   ▼                                                            │
│  PLAN_WRITING_PROFILE                                          │
│   │                                                            │
│   ▼                                                            │
│  ┌──────── loop: for each volume v ──────────────────────┐    │
│  │                                                        │    │
│  │  PLAN_VOL_README(v)                                   │    │
│  │   │                                                   │    │
│  │   ▼                                                   │    │
│  │  ┌──── loop: for each chapter c in v ───────────┐    │    │
│  │  │                                               │    │    │
│  │  │  WRITE_CHAPTER(c)                            │    │    │
│  │  │   │                                          │    │    │
│  │  │   ▼                                          │    │    │
│  │  │  REVIEW_CHAPTER(c)                           │    │    │
│  │  │   │                                          │    │    │
│  │  │   ▼                                          │    │    │
│  │  │  REWRITE_CHAPTER(c)  ×≤ 2                    │    │    │
│  │  │   │                                          │    │    │
│  │  │   ▼                                          │    │    │
│  │  │  EXTRACT_KNOWLEDGE(c)                        │    │    │
│  │  │   │                                          │    │    │
│  │  │   ▼                                          │    │    │
│  │  │  COMMIT_CHAPTER(c)                           │    │    │
│  │  │   │                                          │    │    │
│  │  │   ▼                                          │    │    │
│  │  │  MILESTONE_CHECK(c)   ┐                      │    │    │
│  │  │   │                   │ 每 10 ch → snapshot  │    │    │
│  │  │   │                   │ 每 25 ch → rolling   │    │    │
│  │  │   │                   │ 每 20 ch → audit     │    │    │
│  │  │   │                   ┘                      │    │    │
│  │  │   ▼                                          │    │    │
│  │  │  ADVANCE_CHAPTER                             │    │    │
│  │  │                                              │    │    │
│  │  └──────────────────────────────────────────────┘    │    │
│  │                                                        │    │
│  │  ADVANCE_VOLUME                                        │    │
│  │                                                        │    │
│  └───────────────────────────────────────────────────────┘    │
│   │                                                            │
│   ▼                                                            │
│  EXPORT (full-novel.md / .epub)                                │
│   │                                                            │
│   ▼                                                            │
│  DONE                                                          │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. 状态合同（State Contracts）

每个状态节点是一个"函数"：有明确的**输入**、**调用的角色**、**产出物**、**验证条件**、**下一状态**。

### 2.1 `INIT`

| 字段 | 内容 |
|------|------|
| 输入 | 用户的首条指令（可能只有 genre + target_chapters） |
| 验证 | `genre` / `title` / `target_chapters` 三者全有；缺任一 → **停，问用户** |
| 动作 | 创建 `output/ai-generated/{novel-slug}/` + `meta.yaml`（初始字段）+ 空 `progress.yaml` |
| 产出 | 目录骨架 + meta.yaml + progress.yaml |
| 下一态 | `PLAN_PREMISE` |

### 2.2 `PLAN_PREMISE`

| 字段 | 内容 |
|------|------|
| 角色 | planner |
| 读入 | `meta.yaml` + 用户首条指令 |
| 调用 prompt | [prompts/planner.md](prompts/planner.md) § premise |
| 产出 | `story-bible/premise.md`（BookSpec：logline / pitch / stakes / themes / protagonist / external_goal） |
| 验证 | 文件存在 + 所有必填字段非空 |
| 下一态 | `PLAN_WORLD` |

### 2.3 `PLAN_WORLD`

| 字段 | 内容 |
|------|------|
| 角色 | planner |
| 读入 | `premise.md` |
| 产出 | `story-bible/world.md`（rules[] / power_system.tiers[] / locations[] / factions[]） |
| 验证 | `rules[]` ≥ 5；`power_system.tiers[]` 与主角成长区间对齐；主要 `locations[]` ≥ 3 |
| 下一态 | `PLAN_CHARACTERS` |

### 2.4 `PLAN_CHARACTERS`

| 字段 | 内容 |
|------|------|
| 角色 | planner |
| 读入 | `premise.md` + `world.md` |
| 产出 | `story-bible/characters.md`（主角 + 关键配角 + 反派 ≥ 1 + 初始 knowledge_state） |
| 验证 | 每个角色含 `goal / fear / voice_profile / arc_trajectory / moral_framework_json`；反派含 `escalation_path` |
| 下一态 | `PLAN_VOLUME_PLAN` |

### 2.5 `PLAN_VOLUME_PLAN`

| 字段 | 内容 |
|------|------|
| 角色 | planner |
| 读入 | `premise.md` + `world.md` + `characters.md` |
| 产出 | `story-bible/volume-plan.md`（所有卷 + win/loss 节奏 + 跨卷钩子） |
| 验证 | 卷 1 = `opening win`；倒数第二卷 = `major loss`；最终卷 = `win`；中部 40–70 % loss-biased |
| 下一态 | `PLAN_ACT`（若 > 50 章）/ `PLAN_WORLD_EXPANSION`（若 > 3 卷）/ `PLAN_WRITING_PROFILE` |

### 2.6 `PLAN_ACT`（条件）

触发：`target_chapters > 50`。

产出 `story-bible/act-plan.md`，每个 Act 含 `chapter_range` + `protagonist_arc_stage`。
验证 Acts 章节区间连续不漏不叠，arc_stage 不倒退。

### 2.7 `PLAN_WORLD_EXPANSION`（条件）

触发：`volumes > 3`。

产出 `story-bible/world-expansion.md`，含 `VolumeFrontier[]` / `DeferredReveal[]`（每条 `payoff_chapter_target - planted_chapter ≥ 跨卷`）/ `ExpansionGate[]`。

### 2.8 `PLAN_WRITING_PROFILE`

产出 `story-bible/writing-profile.md`：POV / tense / voice / dialogue_ratio / taboo_words（含 `主角` / `系统` / `穿越` / `金手指` / `内心毫无波动` / `气得浑身发抖` / `仿佛开了挂`）。

### 2.9 `PLAN_VOL_README(v)`

| 字段 | 内容 |
|------|------|
| 角色 | planner |
| 读入 | `volume-plan.md` + `act-plan.md`（若有）+ 前卷 `exit_state` |
| 产出 | `volumes/vol-{NN}-{slug}/README.md`，含**本卷全部章节**的 outline 表（每章 `conflict_summary` / `scenes[]` / `hook_type` / `pacing_mode` / `emotion_phase` / `is_climax` / 状态列） |
| 验证 | outline 数 = 本卷章数；章号连续；每章至少 3 个 scene 卡 |
| 下一态 | `WRITE_CHAPTER(本卷第一章)` |

### 2.10 `WRITE_CHAPTER(c)` 【核心循环】

| 字段 | 内容 |
|------|------|
| 角色 | writer |
| 读入 | `writing-profile.md` + `characters.md` 的本章参与者 + 本章在 `vol-NN/README.md` 的 outline 块 + 前一章最后 200–400 字 + `canon-facts.md` 最近 50 条 |
| 调用 prompt | [prompts/writer.md](prompts/writer.md) |
| 产出 | `volumes/vol-NN/ch-NNN-{slug}.md`，含 frontmatter（scores 暂留空）+ 全部 scene 正文 |
| 验证 | **字数 ≥ 5 000**；frontmatter 字段齐全；scenes 数与 outline 对齐 |
| 失败处理 | 字数不够 → 立即自我扩写（保留本状态，不推进）；最多扩写 2 次 |
| 下一态 | `REVIEW_CHAPTER(c)` |

### 2.11 `REVIEW_CHAPTER(c)`

| 字段 | 内容 |
|------|------|
| 角色 | critic |
| 读入 | 本章草稿 |
| 产出 | 更新章节 frontmatter 的 `scores` 字段 + 追加到 `reviews/chapter-reviews.md` |
| 验证 | Scene 5 维核心均 ≥ 0.70；Chapter 4 维核心均 ≥ 0.75 |
| 失败处理 | 生成 `RewriteTaskModel` 条目，转 `REWRITE_CHAPTER` |
| 下一态（通过）| `EXTRACT_KNOWLEDGE` |
| 下一态（未过）| `REWRITE_CHAPTER(c)` |

### 2.12 `REWRITE_CHAPTER(c)` （≤ 2 次）

| 字段 | 内容 |
|------|------|
| 角色 | editor |
| 输入 | critic 的 RewriteTask（策略文本必须包在 `=== reference only ===` 围栏内） |
| 产出 | 覆盖 scope 内段落；不改 frontmatter 以外的元信息 |
| 重写计数 | `progress.yaml::rewrite_attempts[c]++` |
| 下一态 | 重新 `REVIEW_CHAPTER(c)`；若累计 2 次未过 → **`ACCEPT_ON_STALL`**（保留最高分版本，`status: approved_with_debt`）→ `EXTRACT_KNOWLEDGE` |

### 2.13 `EXTRACT_KNOWLEDGE(c)`

| 字段 | 内容 |
|------|------|
| 角色 | summarizer |
| 读入 | 本章终稿 + 既有 `canon-facts.md` |
| 产出 | 追加到 `canon-facts.md`（append-only）+ 追加到 `timeline.md` |
| 验证 | 新 canon 条目与旧条目无硬冲突；若冲突 → `supersedes` 字段指回旧条目（**不删旧**） |
| 下一态 | `COMMIT_CHAPTER(c)` |

### 2.14 `COMMIT_CHAPTER(c)`

**原子**更新以下 3 个点（已在 EXTRACT_KNOWLEDGE 追加完 canon/timeline）：

1. `volumes/vol-NN/README.md` 状态列 `drafted → approved`；字数回填
2. `meta.yaml` 的 `current_chapter = c`；若越卷则 `current_volume` 同步
3. `progress.yaml::stages.commit_ch_NNN = done`

失败则**整体回滚**本章的所有写入（含 canon/timeline），状态回到 `WRITE_CHAPTER(c)`。

下一态：`MILESTONE_CHECK`

### 2.15 `MILESTONE_CHECK(c)`

按章号判断要不要增加衍生产物：

- `c % snapshot_policy.character_snapshot_every_chapters == 0` → 写 `knowledge/character-snapshots/after-ch-{NNN}.md`
- `c % snapshot_policy.rolling_summary_every_chapters == 0` → 在 `knowledge/rolling-summary.md` 追加一块
- `c % quality_thresholds.consistency_check_chapter == 0` → 写 `reviews/consistency-audits.md` 新一份
  - 若审计发现 canon 冲突 / 知识漏洞 / 伏笔偿付率 < 60 % → 产生修复任务插入队列（写入 `progress.yaml::repair_queue`）

下一态：`ADVANCE_CHAPTER`

### 2.16 `ADVANCE_CHAPTER`

- `progress.yaml::current_chapter++`
- 若 `current_chapter > 本卷最后章` → `ADVANCE_VOLUME`
- 若 `current_chapter > target_chapters` → `EXPORT`
- 否则 → `WRITE_CHAPTER(current_chapter + 1)`

### 2.17 `ADVANCE_VOLUME`

- `current_volume++`
- 若 `current_volume > volumes` → `EXPORT`
- 否则 → `PLAN_VOL_README(current_volume)`

### 2.18 `EXPORT`

| 字段 | 内容 |
|------|------|
| 读入 | 所有 ch-NNN-*.md |
| 产出 | `exports/full-novel.md`（拼装全文 + 卷/章分级标题 + 目录）；若用户要求 → `full-novel.epub` |
| 验证 | 总字数 ≈ `target_total_words`（± 10 %）；章节数 = target_chapters |
| 下一态 | `DONE` |

### 2.19 `DONE`

输出完成报告：

```
🎉 项目完成：《{title}》
- 总章节数：{N} / {target_chapters}
- 总字数：{words} (目标 ±{delta}%)
- 总卷数：{V}
- Canon Facts：{facts_count}
- 重写统计：{rewrites_count}（accept_on_stall: {stall_count}）
- 一致性审计次数：{audits_count}（通过：{passed}，自愈：{healed}）
- 交付物：output/ai-generated/{slug}/exports/full-novel.md

需要的话我可以：
1. 导出 epub
2. 做最终一致性全量扫描
3. 针对某卷做风格统一二次润色
```

---

## 3. `progress.yaml` Schema

这是 orchestrator 的**单一事实源**。每步执行完必须回写一次（即使在循环中段被中断，下次也能恢复）。

```yaml
# output/ai-generated/{slug}/progress.yaml
project_slug: fen-xin-jue
state: WRITE_CHAPTER
current_chapter: 7
current_volume: 1
target_chapters: 30
target_volumes: 1

stages:                         # 每个状态一条；值 ∈ {pending, in_progress, done, skipped, failed}
  init: done
  plan_premise: done
  plan_world: done
  plan_characters: done
  plan_volume_plan: done
  plan_act: skipped             # target_chapters <= 50
  plan_world_expansion: skipped # volumes <= 3
  plan_writing_profile: done
  plan_vol_01_readme: done

chapters:                       # 每章一个对象
  "001": {state: done, rewrite_attempts: 0, final_scores: {hook_strength: 0.82, ...}}
  "002": {state: done, rewrite_attempts: 1, final_scores: {...}}
  "007": {state: in_progress}   # ← 当前在这章

next_action: write_chapter
next_action_args: {volume: 1, chapter: 7}

repair_queue: []                # MILESTONE_CHECK 审计失败时追加的修复任务

failures: []                    # 未恢复的失败，每项：{state, chapter, reason, attempt_count}

human_decision_pending: null    # 非 null 时 orchestrator 必须停，值形如：
                                # {question: "第 12 章是否让沈青阙当场揭穿秦墨？", options: [...]}

last_updated: 2026-04-16T14:32:00Z
last_step_tokens: 4821          # 上一步大约消耗的 tokens，用于估算剩余预算
```

模板见 [templates/progress-state.md](templates/progress-state.md)。

---

## 4. 循环控制（Loop Controller）

### 4.1 每次"会话启动"的伪代码

```
function orchestrator_run():
    loop:
        state = read_progress_yaml().state
        if state == DONE:
            report_completion()
            return
        if human_decision_pending:
            ask_user(human_decision_pending)
            return
        if tool_budget_exceeded() or context_near_limit():
            save_progress()
            report_partial("已完成 {current}/{target} 章，请说'继续'恢复")
            return

        try:
            execute_state(state)            # 按 § 2 的合同执行
            validate_output(state)          # 失败则抛异常
            advance_state()                 # 写 progress.yaml
            log_progress_line()
        except ValidationError as e:
            record_failure(state, e)
            if retry_count(state) >= max_retries:
                set_human_decision_pending("修复 {state} 失败 3 次，需人工决策")
                return
            # 否则本轮循环再试

        # 循环继续
```

### 4.2 停止条件（必须停，告知用户）

1. `state == DONE`
2. `human_decision_pending` 非 null
3. 工具调用次数接近平台上限（Cursor Agent ~25 次 / Claude Code 按 usage 判断）
4. 上下文接近窗口上限（留出 20% 缓冲）
5. 同一状态连续失败 ≥ 3 次

### 4.3 恢复协议（用户说"继续"）

1. 不创建新项目
2. 读 `output/ai-generated/{slug}/progress.yaml`
3. 从 `next_action` 开始执行
4. **第一步执行前**不再重新规划——直接读 `story-bible/` 与 `knowledge/` 即可

### 4.4 修复队列（Repair Queue）

当 `MILESTONE_CHECK` 检出 canon 冲突 / 伏笔偿付不足 / 知识漏洞时：

- 问题**不阻塞当前章推进**，而是追加到 `progress.yaml::repair_queue`
- 下一个 `ADVANCE_CHAPTER` 之前执行一次 `DRAIN_REPAIR_QUEUE`
- 单个修复失败 3 次 → 升级为 `human_decision_pending`

---

## 5. 进度输出格式（向用户）

每完成一步，单行打印：

```
▸ [plan]           premise.md          ✓  (logline + stakes defined)
▸ [plan]           world.md            ✓  (rules=7 tiers=9 locations=6)
▸ [plan]           characters.md       ✓  (protagonist + 4 supporting + 2 antagonists)
▸ [plan]           volume-plan.md      ✓  (win-loss: W·L·L·maj-L·W)
▸ [plan]           vol-01 README       ✓  (30 chapter outlines ready)
▸ [ch-001]         drafting...         ⋯  (~6200 words planned)
▸ [ch-001]         drafted             ✓  (6184 words, 4 scenes)
▸ [ch-001]         reviewed            ✓  (hook=0.82 conf=0.78 emo=0.81 pay=0.73 voice=0.88)
▸ [ch-001]         committed           ✓  (canon+3 timeline+1)
▸ Progress: 1/30 chapters (3 %) · vol 1/1 · words 6 184 / 180 000
---
▸ [ch-002]         drafting...
...
```

每 10 章额外打印一行**累计小结**：

```
=== Milestone after ch-010 ===
Chapters:  10/30 (33 %)    Words:  62 410 / 180 000 (35 %)
Canon:     +38 facts        Timeline: +12 events
Rewrites:  2 scenes (1.3 avg)  Stall:  0
Snapshot written: after-ch-010.md
```

每 20 章额外打印 consistency audit 结果：

```
=== Consistency audit @ ch-020 ===
Canon monotonicity:        PASS (0 conflicts)
Character knowledge:       PASS (0 anachronisms)
Clue→Payoff ratio:         PASS (7/10 = 70 %)
Overall:                   PASS
```

---

## 6. 平台适配

| 平台 | 自主度 | 实现方式 |
|------|-------|---------|
| Claude Code | **完全自主** | 会话里 orchestrator 自执行，工具调用到上限自然停并提示"继续" |
| Cursor Agent Mode | **完全自主** | 同 Claude Code；`.cursor/rules/bestseller-orchestrator.mdc` 规则启用 |
| Cursor Chat（非 Agent） | **半自主** | 每次用户说"继续"，LLM 执行 1–3 步后停 |
| ChatGPT Custom GPT | **半自主** | 无真实文件系统；通过 Code Interpreter 维护 progress 或让用户把文件粘贴回来 |
| Gemini Gem / 通用 LLM | **半自主** | 输出预期文件内容 + 下一步，由用户接力粘贴 |

---

## 7. 入参最小集

orchestrator 进入 `INIT` 时只需要用户给：

```
{
  genre: "玄幻" | "都市" | "科幻" | ...,     必须
  title: "焚心诀",                           必须（缺则从 premise 自动提议再与用户确认）
  target_chapters: 30,                       必须
  target_total_words: 180_000,               可选（缺则按 6 000 × target_chapters 估）
  prompt_pack: "xianxia",                    可选（从 genre 推断）
  constraints: {                             可选
    primary_pov: "close_third",
    tense: "past",
    taboo_additions: [...],
    thematic_focus: "复仇 + 救赎",
  }
}
```

缺任意一个**必须**字段时，orchestrator 必须停，问用户，不得自行补全。

---

## 8. 失败恢复策略

| 失败类型 | 策略 |
|---------|------|
| 某步字段不全 | 在 progress.yaml 记录，本步重试；3 次失败后升级为 `human_decision_pending` |
| 某章字数不足 | writer 自我扩写最多 2 次；仍不足 → editor 介入补场景；最终仍不足 → stall |
| 某章连续 2 次 rewrite 仍未过 | `accept_on_stall`，标 `status: approved_with_debt`；不阻塞后续章 |
| consistency audit 失败 | 追加 repair_queue；下一次 ADVANCE_CHAPTER 前处理 |
| 文件系统写失败 | 整体回滚本章所有写入，状态回 WRITE_CHAPTER |
| 上下文窗口将满 | 保存进度，停并提示"继续" |
| 工具调用配额将满 | 同上 |

---

## 9. 与其他文件的关系

- orchestrator 调用的 prompt：见 [prompts/](prompts/)
- orchestrator 维护的文件布局：见 [output.md](output.md)
- 每步的质量门槛：见 [quality.md](quality.md)
- 每步追加的知识：见 [knowledge.md](knowledge.md)
- 写作层面约束：见 [writing.md](writing.md)
- 规划层面约束：见 [planning.md](planning.md)
- 硬红线（orchestrator 也不得违反）：见 [invariants.md](invariants.md)
- 进度文件模板：见 [templates/progress-state.md](templates/progress-state.md)

---

## 10. 一个具体的 30 章自主执行例子

```
User: "帮我写一部 30 章的玄幻小说，主题是复仇，主角叫江晚"

Orchestrator:
  → INIT
     genre=玄幻, title=?, target_chapters=30, theme=复仇, protagonist=江晚
     title 缺失 → 询问：
       "请确认书名（或我建议：《焚心诀》/ 《北疆落雪》/ 《残簪引》）"
  → User: "用《焚心诀》"
  → 保存 meta.yaml + progress.yaml，进入 PLAN_PREMISE
  → [plan] premise.md         ✓
  → [plan] world.md           ✓
  → [plan] characters.md      ✓
  → [plan] volume-plan.md     ✓ (1 卷 30 章，win-loss 节奏)
  → plan_act 跳过 (≤ 50)
  → plan_world_expansion 跳过 (≤ 3 vol)
  → [plan] writing-profile.md ✓
  → [plan] vol-01 README      ✓ (30 章 outline 全部铺开)
  → [ch-001] drafted → reviewed → committed ✓
  → [ch-002] drafted → reviewed → rewrote ×1 → committed ✓
  → ...
  → (如工具调用到上限) "已完成 7/30 章，请说'继续'恢复"
User: "继续"
  → 读 progress.yaml，从 ch-008 恢复
  → ...
  → [ch-010] committed ✓ → snapshot after-ch-010.md 写入
  → ...
  → [ch-020] committed ✓ → consistency audit PASS
  → ...
  → [ch-030] committed ✓
  → EXPORT → full-novel.md (186 420 字)
  → DONE

🎉 《焚心诀》完成：30/30 章 · 186 420 字 · 1 卷 · canon 127 条 · 重写 8 次（stall 0）
交付：output/ai-generated/fen-xin-jue/exports/full-novel.md
```
