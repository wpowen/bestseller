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

## 1.1 Opening Signing Gate — 高敏感位置 hard gates（必跑）

> 数据源：[config/chapter_position_profiles.yaml](../../../config/chapter_position_profiles.yaml) +
> [config/platform_profiles.yaml](../../../config/platform_profiles.yaml)

**触发条件**：章节 `positions` 字段非空（如 `[first_chapter]`、`[first_chapter, first_unit_case_chapter]`）。

**机制**：通用 5 维 / 4 维 rubric 之外**叠加**一组 hard gates，任一项失败即 must_rewrite=true，**不论其他维度分数多高**。

### First Chapter Gate（最严格，签约样章首章）

critic 输出 JSON 子结构：

```json
{
  "signing_gate": {
    "platform": "qimao",
    "positions": ["first_chapter"],
    "hard_gates": {
      "protagonist_spotlight_by_100w": {"pass": true, "evidence": "首段前 30 字主角出场+主语动作"},
      "visible_conflict_by_200w": {"pass": true, "evidence": "门外封条+倒计时"},
      "protagonist_emotional_pulse_by_500w": {"pass": false, "evidence": "前 500 字仅 0 个 pulse 词", "reason": "主角全程冷面"},
      "core_conflict_visible_by_600w": {"pass": true, "evidence": "..."},
      "emotional_hook_by_2000w": {"pass": true, "evidence": "..."},
      "small_payoff_before_chapter_end": {"pass": false, "evidence": "无可感正反馈节点", "reason": "章末主角被反将"},
      "chapter_end_hook": {"pass": true, "evidence": "尸体用反派声音说话"},
      "anti_pattern_psychological_dumping": {"pass": false, "count": 2, "locations": ["L27-29", "L33-37"]},
      "anti_pattern_cold_protagonist": {"pass": false, "pulse_word_count_per_300w": 0.4},
      "anti_pattern_terminology_overload": {"pass": false, "first_appearance_count": 8, "limit": 5},
      "anti_pattern_no_payoff_in_ch1": {"pass": false}
    },
    "weighted_score": 0.42,  // hard gates 通过率 + weighted_checks 加权
    "must_rewrite": true,
    "repair_playbook_refs": ["weak_attraction", "weak_satisfaction", "ordinary_entry"]
  }
}
```

### Hard Gates 检测规则（量化）

| Gate | 检测方法 |
|------|---------|
| `protagonist_spotlight_by_100w` | 正则：前 100 字内是否出现 `protagonist.name` + 主语动作动词 |
| `visible_conflict_by_200w` | 关键词匹配：前 200 字是否含冲突词集（锁/封/拦/抢/烧/夺/逼/迫/胁/截...）+ 对立角色动作 |
| `protagonist_emotional_pulse_by_500w` | pulse_words 词表匹配（见 platform_profiles.pulse_words），前 500 字 ≥ 1 次 |
| `core_conflict_visible_by_600w` | 主观打分：用 1 句话复述本章核心矛盾，长度 ≤ 30 字、内容具体 → pass |
| `emotional_hook_by_2000w` | 主观打分：是否有读者可述的具体疑问 / 紧张 / 期待 |
| `small_payoff_before_chapter_end` | 章节后 40% 范围内是否有正反馈节点（打脸 / 救成功 / 拿证据 / 反将 / 揭穿 / 关系建立） |
| `chapter_end_hook` | 章末 150 字内是否有新变量 / 颠覆 / 未答之问 |
| `anti_pattern_psychological_dumping` | 单段 > 150 字 + 含 ≥ 2 条背景设定 / 阴谋分析 / 旧案回忆 → 计 1 次；阈值 = 0 |
| `anti_pattern_cold_protagonist` | pulse_words 词频 < 1/300 字 → fail |
| `anti_pattern_terminology_overload` | 私设词首次出现计数（不含通用现实词），阈值 ≤ 5 |
| `anti_pattern_no_payoff_in_ch1` | small_payoff_before_chapter_end 的反向检查 |

### 失败后的整改路径

任一 hard gate 失败 → 生成 RewriteTask，**必须**附 `repair_playbook_refs` 字段，
editor 按 [config/rejection_repair_playbook.yaml](../../../config/rejection_repair_playbook.yaml) 的对应 cause 的 `repair_actions` 改写（按 priority 顺序），不允许 LLM 现编策略。

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

## 4. Word Count Gate（平台感知）

critic 前先做字数守门，**按 platform_profile 取阈值**：

```
threshold = platform_profiles[meta.target_platform].pacing_preference.chapter_word_count
# 七猫: 2500-4000
# 起点: 3000-4500
# 番茄: 2000-3000
# 未指定 platform: 5000-9999（框架默认）

if word_count < threshold.min:
    force rewrite (editor, expand)
    strategies:
      - 加场景（注意 exit_state → entry_state 衔接）
      - 扩内心（对关键决策前的犹豫）
      - 扩对白（推进冲突，非寒暄）
    DO NOT pad with filler description

if word_count > threshold.max:
    force rewrite (editor, trim)
    strategies:
      - 删冗余感官描写
      - 合并重复对白
      - 压缩过渡场景
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

- [ ] 字数 ∈ `platform_profile.pacing_preference.chapter_word_count` 区间（七猫 2500-4000 / 起点 3000-4500 / 番茄 2000-3000 / 无平台默认 ≥ 5000）
- [ ] POV 未无故切换
- [ ] 每次突破带代价账
- [ ] 开场 / 结尾 hook 类型明确
- [ ] 对白比例 25–45%
- [ ] 世界法则渗透 ≥ 2 条
- [ ] 无禁用词 / 无金手指 / 无系统文本
- [ ] `scores` 五维 ≥ 0.70（如自评低于阈值，标 `status: rework`）
- [ ] canon facts 追加条目
- [ ] volume README 表格状态更新
- [ ] **本章 positions 是否非空？非空则跑 § 1.1 Opening Signing Gate 全检（hard gates 任一失败 = 强制 rework）**

## 8. Multi-Persona Critique（每章必跑）

> 数据源：[config/critic_personas.yaml](../../../../config/critic_personas.yaml)

### 为什么需要

单 critic LLM 跑通用 5+4 维 rubric 时容易"自己说服自己"——他用同一套眼光评分，扣分点和真实读者反馈错位。
解决方法：让 critic 依次代入 **4 种关注点完全不同** 的读者人格，独立打分；**最低分 persona 触发 rewrite**。

| persona | 关注点 | 不在乎 |
|---------|-------|-------|
| `platform_editor`（签约编辑）| 留人 / 节奏 / 卖点识别 / 章末勾子 | 文笔美感 |
| `new_reader`（新读者）| 主角是谁 / 跟得上吗 / 信息密度 | 伏笔 / 上章连续 |
| `loyal_reader`（老读者）| 主线推进 / 伏笔偿付 / 角色连续 | 友好新读者 |
| `peer_author`（同行作者）| 句法 / 动词 / 对白个性化 / AI 腔 | 商业卖点 |

### 运行机制

```
critic.persona_run(chapter):
  for persona in [platform_editor, new_reader, loyal_reader, peer_author]:
    load persona definition from config/critic_personas.yaml
    score this chapter using ONLY this persona's lens
    emit per_persona_json (scoring_breakdown + top_3_strengths + top_3_issues + verdict)

  aggregate:
    min_score = min(p.overall_score for p in personas)
    must_rewrite =
        any p.must_rewrite for p in personas
        OR ≥ 2 personas with score < 0.75
        OR min_score < 0.65
    consensus_issues = ≥ 2 persona votes 重叠的 issue
    merged_rewrite_task = top_priority consensus_issues + repair_playbook_refs

  落档至 reviews/multi-persona/ch{N}.md
```

### 决策表

| min_score | ≥ 0.75 persona 数 | 结果 |
|-----------|------------------|------|
| ≥ 0.75 | 4 | approved |
| ≥ 0.65 | 3 | minor_rewrite（按最低分 persona 的 top_3_issues 整改） |
| ≥ 0.65 | ≤ 2 | rewrite（合并 consensus_issues） |
| < 0.65 | * | force_rewrite（任一 persona 都不容妥协） |

### 与现有 5+4 维 critic 的关系

- **5+4 维 critic 不取消**，但作为 "通用底线"；min_score 优先
- **Opening Signing Gate 优先级最高**（hard_gates 失败 → must_rewrite）
- **Multi-Persona 在通用 critic 和 signing gate 之间** 提供"读者代入"维度的覆盖

## 8.1 三大质变检查（peer_author 强化子检查）

> 数据源：
> - [config/character_engine.yaml](../../../../config/character_engine.yaml)
> - [config/prose_style_anchors.yaml](../../../../config/prose_style_anchors.yaml)
> - [config/sensory_inventory.yaml](../../../../config/sensory_inventory.yaml)

multi-persona 的 peer_author 在通用维度之外，必须额外跑以下三组硬检：

### A. character_engine（解决主角对白通用 / 反派脸谱）

```
对每个主要参与者：
  1. 抽出主角对白 → dialogue_individuation_test：替换检验
  2. 检查 unique_response_chain 触发：本场刺激类型是否对应到三步反应链
  3. 计 signature_density：signature_assets 出现 ≥ 2 次
  4. 检查 antagonist_dimensionality：反派是否表现具体 voice_dna 细节
```

### B. prose_style_anchor（反 AI 腔）

```
按 meta.style_anchors 加载锚点：
  1. 扫描 banned_patterns：parallel_action / not_only / smooth_transition / emotion_label / weak_verbs / cliched_metaphor
     任一命中 → 扣 0.05
  2. 检查 style_anchor_adherence：句法 / 词库 / 比喻类型符合锚点
```

### C. sensory_inventory（场景物理感）

```
对每个 scene 按 scene_type 取 required_sensory：
  1. 计 sensory_coverage：命中数 / 应命中数
  2. 扫描 abstraction_violation：阴森 / 神秘 / 诡异 / 难闻 / 寂静（叙述中）
  3. 多人物场景检查 spatial_clarity
```

### 触发关系

```
任一硬子检查失败 → peer_author.must_rewrite=true
→ aggregation 计入 multi-persona 决策
→ 最终 must_rewrite 与其他 persona 综合判定
```

## 8.2 Phase 4 飞升杠杆（peer_author 二级强化）

> 数据源：
> - [config/chapter_signature_audit.yaml](../../../../config/chapter_signature_audit.yaml)
> - [config/information_choreography.yaml](../../../../config/information_choreography.yaml)
> - [config/rhythm_engineering.yaml](../../../../config/rhythm_engineering.yaml)
> - [config/emotion_choreography.yaml](../../../../config/emotion_choreography.yaml)
> - [config/quality_trend_dashboard.yaml](../../../../config/quality_trend_dashboard.yaml)

继 8.1 三杠杆之后，peer_author 再叠加 5 项硬检：

### D. chapter_signature_audit — 每章必有"截图段"

```
6 种 signature_type：
  golden_line / surgical_description / scene_climax_moment /
  twist_with_foreshadow_landing / micro_detail_punch / reaction_amplification_burst

per_chapter: ≥ 1 个 signature 命中
5_chapter_window: ≥ 3 种 signature_type 出现（不允许同质化）
```

### E. information_choreography — 悬念工程

```
reader_belief_audit: 所有"以为 X 实则 Y" 必须登记 + 5 章内开始偿付 + 10 章内完全偿付
open_question_ceiling: 同时活跃 open_questions ≤ 8
new_curiosity_per_chapter: 每章 ≥ 1 条新增（除 climax / wrap-up）

4 种信息模式：reader_knows / character_knows / both_know / neither_knows
每章混合使用，避免单一模式
```

### F. rhythm_engineering — 节奏锚点

```
4 种锚点：hard_stop / acceleration / delay / external_interrupt
per 1500 字 ≥ 4 锚点 + 覆盖 ≥ 3 种类型

段落长度分布：≤15字 30-50% / 16-80字 30-50% / ≥81字 ≤ 20%
句子长度分布：短 ≥ 40% / 中 30-50% / 长 ≤ 30%
```

### G. emotion_choreography — 情绪靠行为承载

```
5 种 expression_layer：physiological / behavioral / object_interaction / silence_pause / dialogue_minimal
每章主导情绪用 ≥ 2 种 layer 承载

禁用形容词标签（叙述中）：
  愤怒 / 悲伤 / 紧张 / 慌张 / 高兴 / 害羞 / 害怕 / 厌恶 / 心慌 / 兴奋
  → 出现 = 0 是阈值（对白中除外）

章节情绪曲线：compress → release → aftermath（每章必有完整弧）
```

### H. quality_trend_dashboard — 长篇趋势监控（每 10 章一次）

```
监控指标：
  - 4 persona 滚动均分
  - 反模式触发频率
  - 伏笔偿付率（< 0.50 + 累计 > 30 章 = 红色告警）
  - signature_type 多样性
  - voice_drift（vs ch1 相似度 < 0.85 = 红色告警）
  - open_questions 数量 > 8 = 黄色告警

报告落档：dashboard/window-{N}-{M}.md
```

## 9. Rejection Repair Loop — 平台拒稿驱动整改

> 用户从平台拿到拒稿原文 → 自动转换为 cause_id → 触发整改循环。
> 数据源：[config/platform_profiles.yaml](../../../config/platform_profiles.yaml) `rejection_signals_to_cause_map`
>        + [config/rejection_repair_playbook.yaml](../../../config/rejection_repair_playbook.yaml)

### 流程

```
1. 接收平台拒稿原文（中文短语，如"开篇切入点比较普通"）
2. 解析 → platform_profiles[X].rejection_signals_to_cause_map[原文] → cause_id
3. 查 rejection_repair_playbook.causes[cause_id]
   ├─ typical_root_causes  → 用于诊断
   ├─ diagnosis_checklist  → critic 逐条评估
   ├─ repair_actions       → editor 按 priority 顺序执行
   ├─ replacement_strategy → 具体增删动作
   └─ validation_check     → critic 重审通过条件
4. editor 重写
5. critic 跑 validation_check
   ├─ 通过 → status: approved
   └─ 失败 → 用 priority: 2 actions 再试一次
6. 两次失败 → accept_on_stall + status: rework
```

### 编排原则

- editor 改写时**只用 `repair_actions`**，不允许 LLM 现编策略（保证可解释、可复现）
- 单次整改不允许扩写 > 原章 30%（防止信息稀释）
- 每次整改都必须输出一份 audit 到 `output/{slug}/audits/repair-ch{N}-r{R}.md`

## 8. Scores 真实性

- **写入 frontmatter 的 scores 必须是真实自评**，不可恒为 0.95 之类的"虚报好看分"
- 低于 0.70 的章应 `status: rework`，并在 [reviews/scene-reviews.md](../../../reviews/scene-reviews.md) 中记录原因
