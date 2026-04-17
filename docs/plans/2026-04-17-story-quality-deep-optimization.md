# 故事质量深度优化方案（冲突 / 人物 / 场景 / 节奏 四维）

**日期**：2026-04-17  
**触发**：用户反馈 `output/female-no-cp-1776303225/`（89 章）章节相似度过高、情节雷同、人物公式化  
**已完成第一批（P1 + P5）**：上下文窗口扩大（`active_context_scenes` 6→12）、对数 lookback 系数 3→8、场景摘要切片 `[:4]→[:8]`、章节标题重复 bug 修复。  
**本计划范围**：在此基础之上的深度优化（冲突/人物/场景/节奏四维），基于 5 路调研的可落地方案。

---

## Context

第一批修复解决了"写手健忘"，但只是把"重复的半径"从 15 章扩大到 40 章。系统更深的问题是 **prompt 没有"冲突类型""场景目的""人物弧线""节奏位置"的结构化知识**——所以即便记得更多，也会继续写出结构相同、语义相同的内容。本计划是一次系统级的"语义约束层"注入。

目标：让系统生成的长篇在读者视角达到：

1. **冲突**：全书覆盖 7 大对抗对象 + 6 种层次 + 至少 1 种 emerging 冲突；相邻 3 场不同 (A,B,C) 轴。
2. **人物**：主要角色具备完整 Lie/Want/Need/Ghost 结构，并在百分位 beat 表上顺序触发。写作时穿过"感觉→感知→评判→决定→合理化"五层思考。
3. **场景**：24 类场景目的 taxonomy；7 维环境表；同一地点 ≤4 次且每次走完"情感重塑三步法"。
4. **节奏**：分形 Seven-Point 结构（书/卷/5章三层）；promise ledger 承诺账本；张力评分 + 喘息/冲击 2:1 配比；章末钩子 7 类不得连续同型。

---

## 第一部分：深度诊断回顾（基于读者 + 代码探查）

### 读者症状
- 情节四段式模板化（秘密→逼迫→选择→代价转移）
- 冲突仅 3 类（追捕/信息战/倒计时）
- 人物无成长，只在"解锁信息"
- 下水道等场景重复 4 次
- 核心信息（母亲身份）拖到 ch85+ 才揭示

### 系统根因
- 写手健忘（P1 已修复）
- 缺乏结构化的 scene-purpose / conflict-type / arc-beat / tension-position 标签
- 事前多样性约束只在第 1 场开头（仅 `opening_diversity_block`）
- 大纲模板池过小且同构
- 无承诺/兑现账本，核心信息流向不受控

---

## 第二部分：四大 Taxonomy（核心产物）

### 2.1 冲突 Taxonomy（4 轴正交）

**每场戏记录四元组 `(object, layer, nature, resolvability)`**：

- **Axis A 对抗对象（7 类）**：`self / person / group / society / nature / technology / supernatural_fate`
- **Axis B 冲突层次（6 类）**：`inner_desire / inner_identity / personal_relation / communal / institutional / cosmic`
- **Axis C 冲突性质（7 类）**：`antagonistic / cooperative_game / moral_dilemma / information_asymmetry / value_clash / resource_scarcity / temporal_irreversible`
- **Axis D 可解性（4 类）**：`resolvable / tragic_inevitable / dynamic_equilibrium / transformative`

**类型小说子池**（用户当前 "female_lead_no_cp"）：
- self_identity / sisterhood_fracture / female_lineage / gendered_bias / faction_politics / origin_mystery / career_path_choice / revenge_vs_letgo / refuse_savior_trope / intra_female_inequality / reformer_vs_beneficiary

**Emerging 冲突池（每 30 章注入一次）**：
- civilizational_scale / information_sovereignty / institution_vs_individual / algorithmic_fate / attention_economy / memory_ownership / cross_scale_ethics / post_truth / derivative_self

**切换规则**：
| 窗口 | 必须切换 |
|---|---|
| 相邻 1 场 | Axis A 或 B 至少变一个 |
| 相邻 3 场 | A/B/C 三轴各至少切换一次 |
| 相邻 5 场 | 必须出现 ≥1 次 internal 层 |
| 相邻 10 场 | 同 conflict_id 不得 ≥3 次 |
| 相邻 30 场 | 至少触发一次 cosmic/institutional/emerging.* |
| 全书 | 7 大对象必须各出现 ≥1 次 |

**相似度公式**（写进 `deduplication.py`）：
```
sim(s_i, s_j) = 0.2*eq(A) + 0.3*eq(B) + 0.2*eq(C) + 0.3*eq(conflict_id)
# 相邻 3 场内 sim > 0.7 触发重写
```

### 2.2 人物成长弧 Taxonomy

**6 种 arc_type**（主角默认 `POSITIVE_CHANGE`）：
- `POSITIVE_CHANGE` / `FLAT` / `DISILLUSIONMENT` / `FALL` / `CORRUPTION` / `FLAT_NEGATIVE`

**每个主要角色必须填写的内在结构**：
```json
{
  "ghost": "过去决定性创伤事件",
  "wound": "由此形成的信条",
  "lie_believed": "角色相信的错误信念（核心）",
  "truth_to_learn": "小说要让他学到的真相",
  "want_external": "表层欲望（伪目标）",
  "need_internal": "真正需要（真目标）",
  "fatal_flaw": "致命缺陷",
  "fear_core": "核心恐惧",
  "desire_shadow": "阴影欲望（说不出口的）",
  "defense_mechanisms": ["防御机制 2-4 个"]
}
```

**百分位 beat 表**（长篇 N 章映射）：
| 百分位 | Beat | 触发约束 |
|---|---|---|
| 0-10% | Normal World | 展示 lie 的保护 |
| 15% | **Lie First Challenged** | 第一次有人/事说"你错了" |
| 25% | First Plot Point | 跨越门槛 + 立誓 |
| 37% | First Temptation | need 招手，被拒 |
| 50% | **Midpoint False Victory** | 用 lie 逻辑赢但埋伏笔 |
| 62% | Regression | 退回 lie，更极端 |
| 75% | **Dark Night** | want 崩塌，lie 彻底失败 |
| 80% | **Epiphany** | 承认 truth |
| 90% | Climax | 以 truth 完成 want 或放弃 want |
| 100% | New Equilibrium | 变形后的重演首章场景 |

**五层思考契约**（在决策点场景强制）：
1. SENSATION 身体感觉（不说"他怕"，写"胸口像压着湿棉被"）
2. PERCEPTION 选择性感知（由 lie/flaw 决定注意到什么）
3. JUDGMENT 评判（带 lie 的滤镜）
4. DECISION 决定
5. RATIONALIZATION 合理化（暴露 lie 的运作；lie 裂缝期写出"可是……"）

**Foil 与 Mirror**：
- Foil：反向 lie（不同价值观）
- Mirror：相同 ghost/flaw 但关键抉择分叉
- 主角的 lie 应部署 3-5 个角色以不同方式验证/挑战

### 2.3 场景 Taxonomy（24 类 + 7 维环境）

**24 类场景目的**（4 族）：
- A 结构位类（6）：Inciting / First Threshold / Midpoint Reversal / Crisis / Climax / Resolution
- B 动作推进类（6）：Pursuit / Infiltration / Heist / Battle / Rescue / Chase-with-Talk
- C 信息关系类（7）：Revelation / Confrontation / Negotiation / Bonding / Betrayal / Reconciliation / Alliance
- D 内在节奏类（5）：Reflection / Dilemma / Worldbuilding / Relief / Foreshadow

**规则**：一章 3-6 场，必须覆盖 A/B/C/D 中的 ≥3 族；近 5 场不得 purpose 重复。

**7 维环境表**：
| 维度 | 取值 |
|---|---|
| 物理空间 | 地下/室内密闭/室内开阔/高处露天/街巷/荒野/水域/交通工具内/阈限空间 |
| 时间段 | 黎明前黑暗/清晨/正午/午后/黄昏/入夜/深夜/凌晨 |
| 天气光照 | 烈日/阴霾/雨/雾/雪/风暴/人工光/完全黑暗 |
| 感官主导 | 视觉/听觉/嗅觉/触觉温度/味觉/本体觉 |
| 社交密度 | 独处/二人/三角/小组/人群匿名/虚拟在场 |
| 节奏尺度 | 实时/加速/慢动作/蒙太奇/闪回嵌套 |
| 垂直封闭度 | 深地下/地面/高处/空中 × 完全封闭/半封闭/开阔 |

**相邻约束**：新场景必须在 7 维中与前 1 场至少 3/7 维取值不同；与前 3 场任一场至少 2/7 维不同。

**同一地点情感重塑三步法**（同一 location ≤4 次，每次必走）：
1. 换价值轴（生死轴→信任轴→身份轴→权力轴）
2. 换感官通道 + 时间天气
3. 换社交拓扑与功能符号（通道→终点，藏身处→祭坛）

**Crowding vs Leaping**：
- 必须 Crowding：Climax / Crisis / Revelation / 首次进入新世界 / Confrontation 决定瞬间
- 必须 Leaping：旅行 / 训练 / 日常重复 / 信息重复传达
- 连续 3 场同模式后强制切换

### 2.4 节奏 Taxonomy（分形结构 + 承诺账本）

**分形 Seven-Point**（Dan Wells 的 Hook/PT1/Pinch1/Midpoint/Pinch2/PT2/Resolution 递归嵌套）：
- 书层面：整本一套
- 卷层面：每 25 章一套
- 章层面：每 5 章一套

**100 章 master beat sheet**（与人物弧线对齐）：

| 章节 % | Beat | 张力目标 |
|---|---|---|
| 5% | Hook / 开局钩子 | 6 |
| 10-20% | 世界展开 + 初次危机 | 4-6 |
| 25% | **PT1 卷一高潮** | 8 |
| 37% | Pinch 1 / First Temptation | 6 |
| 50% | **Midpoint 翻盘 + False Victory** | 9 |
| 62% | Pinch 2 / Regression | 8 |
| 66-70% | All is Lost | 3 外 / 9 内 |
| 75% | **PT2 Dark Night + Epiphany** | 7 |
| 76-85% | 反攻，伏笔兑现 | 7 |
| 86-95% | 终战 | 9-10 |
| 96-100% | 新均衡 + 续作钩子 | 5 |

**张力评分算法**（0-10）：
```
tension = 0.25*stakes + 0.20*conflict + 0.15*pace
        + 0.15*novelty + 0.15*emotion + 0.10*info_density
```
- 5 章滑动均值应在 4.0-6.5 摆动
- 10 章窗口标准差 ≥ 1.5（否则判定为"同节奏循环"）
- 喘息章 : 冲击章 = 2:1 基线

**承诺账本（Promise Ledger）**：
```
promise_ledger[i] = {
  promise_id, opened_at_ch, tension_debt,
  last_progress_ch, scheduled_payoff_ch
}
```
- 任何 promise 超 7 章无 progress → flag "停滞"
- 超承诺曲线上限未 payoff → flag "烂尾风险"

**章末钩子 7 类**：
- 悬念 / 转折 / 危机 / 启示 / 突发 / 情感 / 哲思
- 分布（100 章）：25% / 15% / 20% / 15% / 10% / 10% / 5%
- **禁止连续 3 章同型**

**J 型信息披露时刻表**：
| 秘密类型 | 首次暗示 | 部分揭示 | 完全揭示 |
|---|---|---|---|
| 主角身世 | 5% | 35% | 75% |
| 反派动机 | 20% | 55% | 85% |
| 核心设定真相 | 10% | 50% | 90% |
| 最终 Boss 身份 | 30% | 65% | 88% |

**支线节奏 ABACABAD**（主/感情/成长/外部危机）：
- A 线约 50%，B/C 各 ~20%，D ~10%
- 任何一条不得连续 8 章缺席
- 两条线高潮不得同章（除终章）

---

## 第三部分：数据模型现状 vs 新增

### 现有字段可直接利用的（无需 migration）
- `ChapterModel.hook_type` → 用于 cliffhanger 7 类 tagging
- `ChapterModel.main_conflict` + `chapter_emotion_arc` → 冲突与情感粗信息
- `SceneCardModel.purpose` (JSON) → 存场景目的四元组
- `SceneCardModel.metadata_json` → 存环境 7 维 / 价值极性 / beat_type
- `ChapterModel.metadata_json` → 存 tension_score / act_label / pacing_target
- `CharacterModel.flaw / fear / secret / arc_trajectory / arc_state` → 已有基础
- `CharacterStateSnapshotModel` → 每章快照（可承载 arc_stage / triggered_beats）
- `SceneContractModel.core_conflict / conflict_stakes / hook_type` → 已有 scaffold

### 需要新增字段（通过 metadata_json 软性扩展，无 migration）
- `SceneCard.metadata_json.conflict_tuple` = (A, B, C, D)
- `SceneCard.metadata_json.env_7d` = 7 维标签
- `SceneCard.metadata_json.value_charge_in/out` = -1/0/+1
- `SceneCard.metadata_json.crowding_or_leaping`
- `Chapter.metadata_json.tension_score`
- `Chapter.metadata_json.beat_position` (e.g. "PT1", "Midpoint")
- `Character.metadata_json.lie_believed / truth_to_learn / want_external / need_internal / ghost / defense_mechanisms / arc_beats_triggered`

### 需要 migration 的字段（可延后到第二轮）
- 无必需；上述用 metadata_json 即可承接第一轮。

---

## 第四部分：实施阶段

### Stage A — 冲突多样性引擎（P2 核心，最高 ROI）
- 新建 `src/bestseller/services/conflict_taxonomy.py`：定义 4 轴枚举 + 类型池 + emerging 池 + 切换规则
- 在 `context.py` 新增 `compute_conflict_history()` → 从最近 N 场景的 `metadata_json.conflict_tuple` 读取
- 在 `deduplication.py` 新增 `build_conflict_diversity_block()` → 注入 prompt
- 在 `drafts.py` 的 `build_scene_draft_prompts()` 中注入 conflict block（**所有场景**，不仅第 1 场）
- planner 在生成 scene_contract 时为每场写入 `conflict_tuple`
- `deduplication.py` 新增 `conflict_similarity()` 相似度函数

### Stage B — 场景目的 + 环境维度多样性
- 新建 `src/bestseller/services/scene_taxonomy.py`：24 类 purpose + 7 维 env
- 新增 `compute_scene_purpose_history()` + `compute_env_history()`
- 新增 `build_scene_purpose_diversity_block()` + `build_env_diversity_block()`
- planner 生成每场时写入 `scene_purpose_id` + `env_7d`
- 同地点台账：新增 `location_ledger` 记录每个 location 已使用的 value_axis/sensory/social_topology

### Stage C — 人物成长弧 + 五层思考
- 新建 `src/bestseller/services/character_arcs.py`：定义 arc_type + beat 百分位表
- 扩展 `CharacterModel.metadata_json` 存 lie/want/need/ghost/defense_mechanisms
- 新增 `compute_arc_stage_for_chapter()` → 根据 chapter_number / total_chapters 确定当前 beat
- 新增 `build_arc_beat_block()` + `build_five_layer_thinking_block()` → 在 POV 角色的决策场景注入
- planner 在角色创建时生成完整 inner_structure
- 每章后更新 `CharacterStateSnapshot.metadata_json.triggered_beats`

### Stage D — 节奏引擎 + 钩子 taxonomy
- 新建 `src/bestseller/services/pacing_engine.py`：张力评分 + beat sheet 查询 + 承诺账本
- 扩展 `ChapterModel.metadata_json` 存 tension_score / beat_position / act_label
- 新增 `build_cliffhanger_diversity_block()` → 检查近 3 章 hook_type，禁止连续同型
- 新增 `build_tension_target_block()` → 告诉写手本章目标张力分
- planner 在生成 chapter_contract 时决定 beat_position + 预期 tension
- 生成后跑 `score_chapter_tension()` 回填 `metadata_json.tension_score`

### Stage E — planner 模板分层（P3）
- 重写 `planner.py:3146-3197` 的 `_goal_templates_zh`：从 20 个扩到 50-60 个
- 按 phase 分层（序章 5 / 上升 10 / 平台 15 / 高潮 12 / 结尾 8）
- `_pick_by_seed()` 按 `chapter_number / total_chapters` 和 `phase` 动态挑子池
- 引入具体角色名、主线名词（不再抽象）

---

## 第五部分：prompt 注入架构

每个场景 prompt 的"多样性约束"段将包含：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【本场冲突约束】
- 禁止冲突类型（近 N 场已用）：[info_asymmetry+追捕, temporal_irreversible+倒计时, ...]
- 候选冲突池（至少从中选一）：[value_clash, moral_dilemma, intra_female_inequality, ...]
- Axis 切换要求：A 或 B 至少切换一个
- 性质要求：本场倾向 transformative
- 禁止 Egri static / jumping

【本场景目的约束】
- 近 5 场 purpose：[Revelation, Confrontation, Pursuit, Revelation, Reflection]
- 本场必须属于 A/B/C/D 中未覆盖的族
- 候选：[Alliance, Worldbuilding, Dilemma, ...]

【本场环境切换要求】
- 前一场 7 维：[室内密闭, 深夜, 雨, 听觉, 二人, 实时, 地下半封闭]
- 本场至少 3 维不同

【POV 角色内在状态（林鸢）】
- 当前 arc_stage：Regression（百分位 62%）
- lie：只有绝对力量才能保护
- truth：脆弱与联结才是真正力量
- 本场思考必须穿过五层（SENSATION→...→RATIONALIZATION）
- 因处 Regression 期，JUDGMENT 层应更极端地符合 lie
- 禁用情绪词：害怕 / 原谅 / 家

【本章节奏目标】
- beat_position：Pinch 2
- 目标张力分：7-8（近 5 章窗口均值已 5.6，需上扬）
- 章末钩子类型：危机型（近 3 章已用：危机/危机/悬念 → 禁用危机！改为转折或情感）
- 支线活跃度：主 60% / 感情 10% / 成长 30%

【承诺账本】
- 待兑现 promise：主角身世（opened@5%，已累积 tension_debt 80，应在 75% 兑现——逼近了）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 第六部分：分批次实施优先级

| 批次 | 内容 | 时长估计 | 读者感知改善 |
|---|---|---|---|
| 第 0 批（已完成） | P1 + P5（窗口扩大 + 标题 bug） | 2h | 20% |
| **第 1 批** | Stage A 冲突多样性 | 6h | 35% |
| **第 2 批** | Stage B 场景目的 + 环境维度 | 6h | 25% |
| **第 3 批** | Stage C 人物弧 + 五层思考 | 10h | 20% |
| **第 4 批** | Stage D 节奏引擎 + 钩子 | 8h | 15% |
| **第 5 批** | Stage E planner 模板分层 | 3h | 5% |

累计改善预估：**120%+**（实际是渐进式，后批次的绝对增量会递减，但乘法叠加效应明显）。

---

## 第七部分：验证与回归

### 单元验证
- 每个新 taxonomy 模块写 15-20 个单元测试
- 相似度公式做精度回归测试
- arc_stage 百分位映射单测

### 集成验证
- 重新生成 `female-no-cp-1776303225` 的 ch30-50（21 章）
- 用 `deduplication.py` 跑新旧对比的相似度矩阵
- 目标：sim>0.85 从 15-20% → <5%；sim 0.6-0.85 从 ~25% → <10%
- 人工抽样 5 章做读者感知打分（冲突多样性 / 人物鲜活度 / 场景新鲜感 / 节奏张弛）

### 读者感知打分表（1-10）
- 冲突不重复感
- 人物活过来感
- 场景画面感
- 节奏张弛感
- 信息密度舒适度
- 章末钩子吸引力
- 整体新鲜感

前 6 项均分 ≥ 7 视为达标。

---

## 第八部分：风险与缓解

| 风险 | 缓解 |
|---|---|
| prompt 过长超 token 限制 | 所有约束块走"白名单优先"（只列出禁止，候选池仅在有 slot 时列） |
| 初期 taxonomy 填写不全 → 新机制反而限制 LLM | 第一批先让 planner 在生成 contract 时回填，再用回填结果约束下一场 |
| 张力评分算法过拟合 | 前 2 批不启用自动回填，仅由 planner 手动赋值 |
| 承诺账本误判"烂尾" | 允许主动 acknowledge "延后兑现"的白名单 |
| 同地点三步法在小说主题不允许时过于机械 | 超过 4 次强制报错 → 人工决定是否豁免 |

---

## 第九部分：本次会话实施范围

本会话将实施 **第 1 批 + 第 2 批（Stage A + Stage B）** 的核心部分，并完成自我审查。第 3-5 批因涉及 planner 层较大改造且需要跨多次调试验证，记为后续会话的 follow-up。

具体本会话交付：
1. `conflict_taxonomy.py`（新文件，定义枚举 + 切换规则）
2. `scene_taxonomy.py`（新文件，定义 24 类 purpose + 7 维 env）
3. `deduplication.py` 扩展：`build_conflict_diversity_block`、`build_scene_purpose_diversity_block`、`build_env_diversity_block`
4. `context.py` 扩展：`compute_conflict_history`、`compute_scene_purpose_history`、`compute_env_history`
5. `drafts.py` 修改 `build_scene_draft_prompts`：把三个新 block 注入到**所有场景**（不只第 1 场）
6. 配套单元测试
7. 自我审查报告：以挑剔读者视角再评估方案健全性
