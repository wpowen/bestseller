# 书籍方法论融合试点结果

生成时间：2026-05-28

## 试点入口

- 脚本：`scripts/methodology_books/run_short_story_pilot.py`
- 模型 profile：`xiaomi-mimo`
- 运行物料目录：`output/methodology-book-pilot-20260528T153929Z/`
- 生成文件：
  - `prompt.md`：带书籍方法论选卡的试点提示词
  - `selected_cards.md`：本次注入的 6 张卡
  - `short_story.md`：模型生成短篇
  - `quality_report.json`：确定性指标 + LLM 评审 + token/延迟

## 本次选卡

本次试点从 `books_core_v1` 选择 6 张 strict 卡：

- `books_core.source-0001.sec-0002.emotional_experience_core_001`
- `books_core.source-0002.sec-0024.five_step_scene_framework`
- `books_core.source-0004.sec-0002.internal_change_focus`
- `books_core.source-0002.sec-0022.scene_as_mini_story`
- `books_core.source-0003.sec-0024.distance_control_via_pov`
- `books_core.source-0004.sec-0009.pov_worldview_filter`

覆盖域：`character_arc`、`scene_causality`、`pov_prose`。

## 结果摘要

第二轮有效试点由 `openai/mimo-v2.5-pro` 生成，未走 fallback。

| 指标 | 分数 / 数值 |
| --- | ---: |
| 综合质量分 | 0.851 |
| 确定性质量分 | 0.792 |
| LLM 评审分 | 0.900 |
| 中文正文长度 | 3003 字 |
| dialogue_ratio | 0.271 |
| anti_meta_leak_count | 0 |
| anti_meta_leak_score | 1.000 |
| scene_causality_score | 0.714 |
| setup_payoff_score | 0.333 |
| pov_stability_score | 0.844 |
| ending_hook_score | 1.000 |

## 发现

1. 书籍方法论注入能明显改善场景目标、阻力、行动、代价、结果的可见性。第二轮正文没有输出方法论标签，说明提示词必须明确禁止“结构标签外泄”。
2. 视角和情绪体验卡对正文有效：故事基本稳定在林渊的有限视角内，危机和记忆触发都通过身体反应、现场动作、物件证据推进。
3. 伏笔 / 偿付仍是薄弱点。LLM 评审给出 0.9，但确定性指标只给 0.333，说明模型能“感觉闭环”，但框架级账本命中还不够硬。
4. 第一轮试点曾出现“目标/阻力/行动/代价/结果”直接进入正文的问题。因此后续 draft prompt 必须把方法论卡放进内部约束块，且加 anti-meta leak gate。

## 下一步融合动作

1. 把 `books_core_v1` 的 selected cards 固化到 chapter application lineage，planner 先选，draft/review/repair 复用同一组。
2. 对 `scene_causality` strict 卡增加 contract 字段检查：`goal`、`obstacle`、`action`、`cost`、`result` 缺一则 warn。
3. 对 `setup_payoff` 域增加 hook ledger / payoff ledger 的账本绑定，避免只在正文里“有感觉”但没有可审计字段。
4. 在 draft/rewrite prompt 加 anti-meta leak 禁令，并在 review gate 中检测结构标签外泄。
5. 后续 baseline 对比时用同一试点脚本跑未注入版本，比较 scene causality、anti-meta、setup/payoff、dialogue ratio 的差异。
