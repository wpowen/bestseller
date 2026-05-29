# 书籍方法论注入 A/B Test 结果

测试日期：2026-05-29（Asia/Shanghai）

## 测试设计

目标：验证 `books_core_v1` 书籍方法论选卡注入后，是否能在同一短篇任务上带来可观测质量提升。

控制变量：

- 模型 profile：`xiaomi-mimo`
- 生成模型：`openai/mimo-v2.5-pro`
- 题材：都市悬疑 + 轻玄幻
- 标题：《雨夜旧账》
- 主角：林渊
- 篇幅要求：2800 到 4200 个中文汉字
- 输出限制：只输出小说正文，禁止结构标签、创作术语、评分语言
- 评估脚本：`scripts/methodology_books/run_short_story_pilot.py`

实验组：

| 组别 | 输出目录 | 说明 |
| --- | --- | --- |
| A / baseline | `output/methodology-book-pilot-baseline-20260528T162333Z/` | 不注入书籍方法论卡，只保留硬约束 |
| B / methodology | `output/methodology-book-pilot-methodology-20260528T162559Z/` | 注入 6 张 `books_core_v1` 方法论卡 |

B 组选卡：

- `books_core.source-0001.sec-0002.emotional_experience_core_001`
- `books_core.source-0002.sec-0024.five_step_scene_framework`
- `books_core.source-0004.sec-0002.internal_change_focus`
- `books_core.source-0002.sec-0022.scene_as_mini_story`
- `books_core.source-0003.sec-0024.distance_control_via_pov`
- `books_core.source-0004.sec-0009.pov_worldview_filter`

覆盖域：`character_arc`、`scene_causality`、`pov_prose`。

## 结果对比

这次对比以确定性评估指标为主。原因是两组原始 critic JSON 都在 MiMo 的 `finish_reason=length` 情况下未能稳定解析，因此不把 LLM critic 作为主证据。

| 指标 | A baseline | B methodology | B-A |
| --- | ---: | ---: | ---: |
| 综合质量分 | 0.754 | 0.871 | +0.117 |
| 确定性质量分 | 0.754 | 0.871 | +0.117 |
| scene_causality_score | 0.857 | 1.000 | +0.143 |
| setup_payoff_score | 0.500 | 0.333 | -0.167 |
| pov_stability_score | 0.765 | 0.935 | +0.170 |
| dialogue_ratio | 0.277 | 0.417 | +0.140 |
| ending_hook_score | 0.750 | 1.000 | +0.250 |
| anti_meta_leak_count | 0 | 0 | 0 |
| 中文正文长度 | 3530 | 2935 | -595 |
| story input tokens | 268 | 743 | +475 |
| story output tokens | 3274 | 3350 | +76 |
| story latency ms | 100306 | 93460 | -6846 |

## 观察结论

### 1. 方法论注入显著提升了场景因果

B 组 `scene_causality_score` 从 0.857 提升到 1.000。

这和注入卡的方向一致：B 组被明确要求内部执行“场景作为 mini story”和“五步场景框架”，生成结果中场景推进更像“目标 → 阻碍 → 选择 → 代价 → 新压力”的连续链，而不是单纯铺信息。

结论：`scene_causality` 是当前最值得继续接入 draft/scene contract 的方法论域。

### 2. 方法论注入显著提升了 POV 稳定性

B 组 `pov_stability_score` 从 0.765 提升到 0.935。

这说明 `distance_control_via_pov` 和 `pov_worldview_filter` 这类卡对正文有效。B 组更稳定地贴着林渊的感知、判断和身体反应推进，没有频繁跳到全知解释。

结论：`pov_prose` 应继续接入 `PROSE_SCENE` 和 review prose gate。

### 3. 方法论注入提升了结尾钩子

B 组 `ending_hook_score` 从 0.750 提升到 1.000。

B 组结尾把“母亲声音 / 老房子 / 新账单”作为下一步强压力，既完成本段真相揭示，又留下明确下一场行动方向。

结论：方法论卡对“阶段性兑现 + 下一步问题”的效果明确。

### 4. 对白比例明显提升

B 组 `dialogue_ratio` 从 0.277 提升到 0.417。

这不等于对白一定更好，但说明注入后模型更愿意用角色交锋承载信息，而不是只靠叙述说明。结合正文看，B 组“周国栋电话 / 典当行老头”承担了更多压力、解释和阻拦功能。

结论：对白与人物压力相关的方法论可以继续接入，但后续需要更细的 `dialogue_subtext` 评分，而不是只看比例。

### 5. setup/payoff 下降，是当前最大警讯

B 组 `setup_payoff_score` 从 0.500 降到 0.333。

这说明当前 B 组选卡没有覆盖 `setup_payoff`，导致场景、POV、结尾变强，但伏笔账本和偿付账本没有同步增强。

这也印证之前的集成判断：`setup_payoff` 不能只作为 prose prompt 里的软建议，必须绑定 planner 的 hook ledger / payoff ledger。

结论：下一步必须把 `setup_payoff` 从“可选提示”升级成 chapter/volume planning 的结构化账本，否则长篇质量提升会不稳定。

### 6. 没有出现方法论术语泄漏

两组 `anti_meta_leak_count` 都是 0。

这说明目前 prompt 里的“内部执行约束，禁止把卡片/字段/结构标签写进正文”有效。第一轮试点曾出现过结构标签泄漏，本次已经修正。

结论：后续所有方法论注入都必须保留 anti-meta guard。

## 成本结论

B 组 story input tokens 增加 475，主要来自 6 张 selected cards。

但 story output tokens 基本持平，B 组甚至生成延迟略低。这说明本次注入的运行成本可接受。不过这只是短篇测试，长篇 1000 章仍需要按 prompt budget 约束。

当前建议：

- 单章/单场默认注入 3 到 6 张。
- repair 场景注入 1 到 3 张。
- 不全量注入 771 张。

## 最终结论

本次 A/B test 支持以下结论：

> `books_core_v1` 方法论注入对短篇生成有正向效果，尤其提升场景因果、POV 稳定性、对白承载和结尾钩子。综合确定性质量分从 0.754 提升到 0.871，提升 0.117。

但结论边界也很明确：

- 这是单样本 A/B，不足以证明所有题材、所有章节稳定提升。
- LLM critic 在本次 fresh A/B 中因 MiMo 输出截断未能提供稳定 JSON，主证据是确定性指标。
- `setup_payoff` 下降，说明当前选卡对伏笔偿付覆盖不足。

## 下一步动作

1. `scene_causality`、`pov_prose`、`character_arc` 继续保留在 `PROSE_SCENE` 注入中。
2. 增加 selector 的 chapter/outline 阶段 bias，让 `setup_payoff` 在章纲和卷规划中更容易被选中。
3. 把 `setup_payoff` 绑定到 hook ledger / payoff ledger，不能只靠正文 prompt。
4. 修复或绕开 MiMo critic 长输出截断问题：critic prompt 必须更短，或单独用 deterministic judge 作为 A/B 主评估器。
5. 扩展到 3 个题材、每个题材 5 到 10 次短篇/章节样本，确认提升是否稳定。
