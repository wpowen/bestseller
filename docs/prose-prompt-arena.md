# 正文提示词横向实验 Runbook

本文档用于验证：同一份场景资源下，正文 writer prompt 塞入哪些策略信息，才能更稳定地产出抓人、爽点明确、悬念足、AI 味低的正文。

实验不直接判断“哪套理论最好”，而是把 20 套不同正文 prompt 策略放到同一场景、同一 writer model、同一盲评标准下横向比较，再把人工最优结果反推回生产 prompt、大纲和细纲。

## 1. 输入要求

需要一个真实 scene writer prompt trace，优先使用运行时导出的 trace：

```bash
output/<project>/traces/<scene-prompt>.json
```

trace 至少要包含：

- `prompts.system`
- `prompts.user`
- `project`
- `chapter`
- `scene`

工具会从这些字段中抽取当前场景资源摘要，包括章节/场景、因果合同、方法论钩子、爽点/悬疑合同、物料资源、世界规则等。

## 2. 预检模型可用性

先确认 20 个策略、writer 和 judge 调用数，以及 API key 是否齐：

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --trace output/<project>/traces/<scene-prompt>.json \
  --writer-model minimax-m3 \
  --writer-model qwen3.7-plus-coding-plan \
  --judge-model minimax-m3 \
  --judge-model deepseek-v4-flash \
  --preflight
```

常见缺失环境变量：

- `MINIMAX_API_KEY`
- `QWEN_CODING_PLAN_API_KEY`
- `DEEPSEEK_API_KEY`

`--preflight` 会同时打印 `next_steps`：

- `live_run`：key 齐全时直接运行完整 writer/judge 横评
- `external_prompt_package`：key 不齐时先导出 20 个 prompt 包，交给外部模型生成正文
- `missing_env`：当前缺失的 key 名称

如果 key 暂时没有，也可以走外部手工生成/导入流程。

## 3. 导出 20 个正文 Prompt

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --trace output/<project>/traces/<scene-prompt>.json \
  --out output/prose-prompt-arena/<run-id> \
  --prompts-only
```

输出：

- `prompts/*.json`：20 个正文 prompt 变体
- `writer-prompts/*.md`：按 `strategy_id__model` 生成的外部 writer 可复制 prompt，已标明输出文件名
- `prompt-manifest.json`：策略清单、导入约束、原始 prompt 方法论应用诊断
- `external-prompt-handoff.md`：给外部 writer 的交接说明

20 个策略包括控制组、黄金三章开篇钩子、读者问题链、爽点交付优先、悬疑揭示阶梯、章末悬念锁定、场景合同可见化、去 AI 味等。
其中“原始 prompt 方法论应用诊断”只判断方法论是否被落成可执行写作约束：

- `operationalized`：已有类似“第一段必须”“最后 120 字”“压迫/选择/执行/反馈”的硬约束
- `mentioned_only`：只出现“黄金三章”“爽点”“悬念”等概念，容易读起来像没真正使用
- `missing`：该维度在原始 prompt 中未明显出现

这个诊断不替代盲测，只用于解释为什么要做对应策略横评。

## 4. 外部 Writer 生成正文

优先打开 `writer-prompts/<strategy_id>__<model>.md`，里面已经包含 system prompt、user prompt
和 `save_output_as` 文件名。不要改 prompt 内容；只把模型输出正文保存到对应 `.md`。

如果在外部用 MiniMax M3 和 Qwen 3.7 跑正文，每个策略每个模型保存一个 `.md`：

```text
external-drafts/
  production_control__minimax-m3.md
  production_control__qwen3.7-plus-coding-plan.md
  golden_three_opening__minimax-m3.md
  golden_three_opening__qwen3.7-plus-coding-plan.md
  ...
```

如果同一策略同一模型多采样，增加 sample 后缀：

```text
golden_three_opening__minimax-m3__s2.md
```

导回正文：

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --prompt-manifest output/prose-prompt-arena/<run-id>/prompt-manifest.json \
  --out output/prose-prompt-arena/<run-id> \
  --import-drafts external-drafts \
  --skip-judging
```

这里必须使用 prompt 包自己的 `prompt-manifest.json`，不要重新用 `--trace` 构建默认策略；二代 prompt 包也用同一导入方式。
双 writer 文件名已经带模型标签时，不要再传 `--import-writer-model`；只有单模型 smoke 使用
`<strategy_id>.md` 文件名时才需要传它。
`prompt-manifest.json` 会声明期望 writer 覆盖，默认导入会要求每个策略都有 MiniMax M3 和
Qwen 3.7 两篇正文；单模型 smoke 需要显式传 `--allow-partial-import`。

默认要求所有 20 个策略都必须有正文。只在 smoke test 时使用 `--allow-partial-import`。

## 5. 导出盲评 Prompt

导入正文后，生成给 judge 模型的盲评包：

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --manifest output/prose-prompt-arena/<run-id>/manifest.json \
  --out output/prose-prompt-arena/<run-id> \
  --export-judge-prompts \
  --judge-model deepseek-v4-flash \
  --judge-model minimax-m3
```

输出：

- `judge-prompts/*.json`
- `external-judge-handoff.md`
- `judge-prompt-manifest.json`
- `judge-blind-map.private.json`

`judge-prompts` 只包含盲读编号和正文，不暴露 strategy id、draft id 或提示词策略名称。
盲读编号不是策略生成顺序，而是根据 draft id 稳定打乱后生成；不要自行假设 `A` 是控制组或第一个策略。
`judge-prompt-manifest.json` 会记录本轮期望 judge 模型；导回外部 judge JSON 时会据此检查每篇正文是否都有对应 judge 结果。

## 6. 外部 Judge 结果格式

每个 judge 输出一个 JSON 文件，建议文件名：

```text
external-judgements/
  A__deepseek-v4-flash.json
  A__minimax-m3.json
  B__deepseek-v4-flash.json
  ...
```

文件名请以 `external-judge-handoff.md` 里的结果文件名为准，因为盲读编号已被打乱。

JSON 必须覆盖全部评分维度：

```json
{
  "blind_label": "A",
  "judge_label": "deepseek-v4-flash",
  "scores": {
    "opening_hook": 8,
    "golden_three_fit": 7,
    "shuangwen_payoff": 8,
    "suspense_hook": 7,
    "scene_causality": 8,
    "character_embodiment": 7,
    "prose_texture": 8,
    "anti_ai_flavor": 8,
    "ending_hook": 7,
    "overall": 8
  },
  "winner_reason": "开篇问题明确，动作推动更强。",
  "risk_notes": ["中段仍有少量解释性句子。"]
}
```

内部 judge 和外部 judge 使用同一套 `scores` schema。外部 judge 额外必须在顶层原样带回
`blind_label` 和 `judge_label`，方便导回时恢复盲读映射。只返回 `overall` 不算完整盲评；
完成度审计会把它标为 pending。

## 7. 导回 Judge 结果并生成 HTML

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --manifest output/prose-prompt-arena/<run-id>/manifest.json \
  --import-judgements external-judgements \
  --out output/prose-prompt-arena/<run-id>
```

打开：

```text
output/prose-prompt-arena/<run-id>/report.html
```

HTML 页面包含：

- 场景资源摘要
- 盲读正文卡片
- 盲读工作台：展开/收起正文、只看未判定、显示全部、紧凑模式
- 人工选择：最优 / 可取部分 / 淘汰
- 本地保存和导出 `manual-selection.json`
- 策略揭示
- 盲评排名
- 维度缺口矩阵
- 大纲/细纲反推提示

人工横读时先不要打开“揭示策略和模型”“揭示判官分数和理由”。默认卡片只显示盲读编号、字数、sample 和正文；盲读编号也经过稳定打乱，这样可以避免 prompt 策略顺序、writer model 或 judge 分数影响主观判断。40 张卡片时，先用“紧凑模式”和“只看未判定”分批处理，最后再揭示策略。
如果没有任何方案值得标“最优”，把每张卡片标为“可取部分”或“淘汰”；顶部人工判定面板会显示“本轮暂未选出最优”，导出的 JSON 仍可进入第 9 步反推流程。

## 8. 完成度审计

在提交结论前跑审计。最终 `complete` 需要第 9 步已经生成
`manual-analysis/manual-selection-analysis.json` 和 `.md`；否则审计会保持
`pending_human_or_external`，因为还没有把人工判断反查成 prompt 策略和大纲/细纲检查项。
不传 `--expected-writer-model` / `--expected-judge-model` 时，审计默认按本实验目标检查
MiniMax M3 + Qwen 3.7 writer，以及 MiniMax M3 + DeepSeek judge；smoke test 才显式覆盖这些期望。

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --manifest output/prose-prompt-arena/<run-id>/manifest.json \
  --manual-selection output/prose-prompt-arena/<run-id>/manual-selection.json \
  --audit \
  --expected-strategies 20 \
  --expected-writer-model minimax-m3 \
  --expected-writer-model qwen3.7-plus-coding-plan \
  --expected-judge-model deepseek-v4-flash \
  --expected-judge-model minimax-m3
```

输出：

- `experiment-audit.json`
- `experiment-audit.md`

状态含义：

- `complete`：20 策略、writer 覆盖、judge 覆盖、全维度评分、HTML、人工判断和反查报告都齐了；人工选出最优时会反推 prompt 策略，人工确认没有赢家时也可以成立，但必须所有盲读正文都已标记，并生成 `manual_no_winner` 分析和 `round2_outline_repair_*` 二代草案
- `pending_human_or_external`：结构齐，但缺外部正文、盲评、人工选择或选择反查报告
- `incomplete`：prompt/draft/html 等实验结构本身缺失

## 9. 人工最优反推

从 HTML 导出 `manual-selection.json` 后运行：

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --manifest output/prose-prompt-arena/<run-id>/manifest.json \
  --manual-selection output/prose-prompt-arena/<run-id>/manual-selection.json \
  --analysis-out output/prose-prompt-arena/<run-id>/manual-analysis
```

输出：

- `manual-selection-analysis.json`
- `manual-selection-analysis.md`

它会把盲读编号映射回：

- 具体策略 id
- 策略假设
- writer model
- prompt 路径
- draft 路径
- 可回灌生产 writer prompt 的候选规则
- 大纲/细纲反推检查项
- 二代策略草案：人工有效策略融合、最优策略蒸馏、弱维度补强，或无赢家时的 outline-repair 复测方案
- 下一轮实验建议

## 10. 如果 20 个策略都不够好

不要继续盲目新增更多 prompt 术语。先看维度缺口矩阵：

- 开篇钩子低：检查细纲是否有第一眼异常/危险/压力，而不是背景说明。
- 黄金三章低：检查前三章是否有欲望锁定、卖点显形、追读问题。
- 爽点低：检查是否有压迫、选择、执行、反馈四拍。
- 悬念低：检查是否有异常、误判、证据、反向验证，而不是一次性解释。
- 场景因果低：检查目标、阻力、代价、不可逆变化是否齐。
- 结尾钩子低：检查场景切点是否留下新问题、代价或强敌动作。
- 去 AI 味低：检查场景素材是否具体；素材空时模型容易回到总结和抽象评价。

如果多个维度同时低，优先补大纲/细纲场景合同，再用 5 个高信号策略复测，不要只在正文 prompt 里堆更多方法论。

`manual-selection-analysis.json` 中的 `next_round_strategy_proposals` 是下一轮实验的机器可读草案。它不会直接替换生产 prompt；先用同一 trace 小范围复测，再决定是否回灌生产 writer。
如果人工把所有方案都标为“淘汰”，二代草案会转成 `round2_outline_repair_*`：
先补开篇钩子、爽点反馈、章末问题等大纲/细纲字段，再用少量高信号策略复测。
这种“无赢家”分支也是有效实验结论；审计会要求所有盲读正文都有人工标记，并且已经生成
`manual-selection-analysis.json` / `.md`，否则仍保持 pending。

## 11. 物化二代 Prompt 包

如果人工选择和维度缺口已经产生 `manual-selection-analysis.json`，可以把二代策略草案直接转成下一轮可跑的 prompt 包：

```bash
.venv/bin/python scripts/prose_prompt_strategy_arena.py \
  --manifest output/prose-prompt-arena/<run-id>/manifest.json \
  --strategy-proposals output/prose-prompt-arena/<run-id>/manual-analysis/manual-selection-analysis.json \
  --out output/prose-prompt-arena/<run-id>/round2-prompts
```

输出：

- `round2-prompts/prompts/*.json`
- `round2-prompts/prompt-manifest.json`
- `round2-prompts/external-prompt-handoff.md`
- `round2-prompts/round2-source.json`

然后按第 4 步的外部 writer 导入方式继续跑。二代 prompt 包只代表实验候选，不代表生产 writer prompt 已经更新。
