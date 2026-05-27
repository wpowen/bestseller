# Planning — Mode B 分层规划

> Mode B 动笔前**必读**。Mode A 修改 `services/planner.py` 时亦可参考（与代码一一对应）。

## 1. Hierarchy from Target Chapters

| Target Chapters | Volumes | Acts | Notes |
|---|---|---|---|
| 1–50 | 1 | 1 | Single arc |
| 51–120 | 3–4 | 3 | Three-act |
| 121–300 | 5–6 | 4 | Four-act |
| 301–800 | 8–16 | 5 | Five-act epic |
| 801–1500 | 16–30 | 5–6 | Epic saga |
| 1500–2000+ | 30–40 | 6 | Multi-generational |

**卷目标约 50 章；`arc_batch_size = 12`；`target_chapters > 50` 触发 ActPlan 强制。**

## 2. Word Budget（硬约束）

| 字段 | Min | Target | Max |
|------|-----|--------|-----|
| **Words / chapter** | **5 000** | 6 400 | 9 000 |
| Words / scene | 1 200 | 1 600 | 2 200 |
| Scenes / chapter | 2 | 3–4 | 5 |

**草稿 < 5000 字 → 强制 rewrite；从场景 / 内心 / 对白方向扩写，不灌水。**

Scene count 规则：climax / reversal 章 = 4；post-climax 章 = 2；默认 = 3。

## 3. Six-Phase Conflict Evolution

| 阶段 | code | 主角面对 |
|------|------|---------|
| 1 | `survival` | 基础威胁、勉强苟活 |
| 2 | `political_intrigue` | 暗中角力、权谋 |
| 3 | `betrayal` | 信任崩塌、盟友反叛 |
| 4 | `faction_war` | 大规模派系冲突 |
| 5 | `existential_threat` | 存亡级别的终局危机 |
| 6 | `internal_reckoning` | 内在转化 |

**30 章**：只走 1（→ 2 → 3 → 6 的精简版）；
**100 章**：1 → 2 → 3 → 4；
**500 章**：全 6 阶段走一轮；
**1000/2000 章**：全 6 阶段走 1–2 轮。

## 4. Volume Win/Loss Rhythm

| 位置 | 胜负 |
|------|------|
| 第 1 卷 | **win** —— hook 小胜 |
| 中部 40–70% | 多为 loss —— 危机带 |
| 倒数第二卷（N-1） | **major loss** —— 付出最深代价 |
| 最终卷（N） | **win** —— 闭环 |
| 其他 | 交替（偶胜奇败） |

## 5. Chapter Phase（卷内位置 `p`）

| 比例 | phase |
|------|-------|
| 0–13% | `hook` |
| 13–33% | `setup` |
| 33–53% | `escalation` |
| 53–73% | `twist` |
| 73–87% | `climax` |
| 87–100% | `resolution_hook` |

## 6. Pacing Mode

- `build` —— 铺垫积压
- `accelerate` —— 节奏骤紧
- `climax` —— 最高强度章
- `breathe` —— 情绪回落

一个卷的 pacing 序列应形如 `build...accelerate...climax...breathe...build...accelerate...climax...breathe`（规律但可变）。

## 7. Emotion Phase

- `compress` —— 情绪积压（钩 / 铺 / 转）
- `release` —— 情绪释放（顶 / 收）

相邻章不得连续 ≥ 3 章同相（除 hook 前三章例外）。

## 8. Mode B Planning Workflow（固定八步）

1. **Premise → BookSpec**
   - protagonist{name, archetype, external_goal, internal_need, flaw, strength, fear}
   - logline, themes[], reader_promise
   - stakes{personal, world}
   - three_act_structure

2. **WorldSpec**
   - world_name, world_premise
   - rules[]{name, description, story_consequence, exploitation_potential}
   - power_system{tiers[], hard_limits, protagonist_starting_tier}
   - locations[], factions[], forbidden_zones

3. **CastSpec**
   - protagonist, antagonist
   - antagonist_forces[]{name, force_type, active_volumes[], escalation_path}
   - supporting_cast[] 各含 voice_profile / knowledge_state / moral_framework / arc_trajectory / conflict_map

4. **VolumePlan**（每卷）
   - volume_number, title, volume_theme, chapter_count_target, word_count_target
   - opening_state, volume_goal, volume_obstacle, volume_climax
   - volume_resolution{goal_achieved, cost_paid, new_threat_introduced}
   - key_reveals[], foreshadowing_planted[], foreshadowing_paid_off[]
   - reader_hook_to_next, conflict_phase, primary_force_name

5. **ActPlan**（章数 > 50 强制）
   - act_number, title, chapter_range, purpose
   - protagonist_arc_stage, world_state_at_start, world_state_at_end, key_scenes[]

6. **ChapterOutline**（每卷 just-in-time）
   - chapter_number, volume_number, chapter_goal, chapter_title, chapter_phase, conflict_phase
   - conflict_summary, scene_count
   - scenes[]{scene_type ∈ action/investigation/relationship/worldbuilding/comic_relief,
              hook_type ∈ information_gap/deadline/mystery/desire/threat,
              spotlight_character, summary, entry_state, exit_state,
              estimated_words, conflict_stakes}
   - estimated_chapter_words ≥ 5000
   - pacing_mode ∈ build/accelerate/climax/breathe
   - emotion_phase ∈ compress/release
   - is_climax

7. **World Expansion**（卷数 > 3 强制）
   - VolumeFrontier{per volume}
   - DeferredReveal[]（ payoff 前至少跨 2 卷种植）
   - ExpansionGate[]
   - 渐进可见：V 卷时世界信息可见度 = min(100%, V/total_volumes)

8. **只有完成以上 1–7，才可开始写第一章正文。**

## 9. Long-Novel Augmentations

- **> 300 章**：character snapshot 每 5 章；rolling summary 每 25 章；consistency audit 每 20 章
- **> 1500 章**：卷间可代际跳跃；旧 canon 依然成立，不得覆盖
- **> 50 章** 强制 ActPlan；**> 3 卷** 强制 DeferredReveal 追踪

## 10. 与代码对齐

- `compute_linear_hierarchy(total_chapters)` → `services/planner.py`
- `generate_foundation_plan()` / `generate_novel_plan()` / `generate_volume_plan()` → 同上
- `config/default.yaml` 中 `words_per_chapter.min=5000`，`act_plan_threshold=50`
