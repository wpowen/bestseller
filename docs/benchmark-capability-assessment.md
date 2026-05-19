# 精品样本对标与框架能力评估报告

Generated at: `2026-05-16T16:23:44+00:00`

## 样本概览

- 匿名样本数: 40
- 类别分布: `{"action-progression": 20, "base-building": 4, "eastern-aesthetic": 2, "esports-competition": 3, "otherworld-cross-system": 2, "relationship-driven": 2, "strategy-worldbuilding": 5, "suspense-mystery": 2}`
- 处理状态: `{"parse_ready": 40}`

## 团队工作流

```mermaid
flowchart LR
  Books["40本精品样本"] --> Private["私有解析/章节切分"]
  Private --> Distill["匿名结构蒸馏"]
  Distill --> Rubric["类别Benchmark Rubric"]
  Rubric --> Audit["框架能力审计"]
  Audit --> Matrix["能力矩阵"]
  Matrix --> Roadmap["优化路线图"]
```

## Benchmark Findings

| Category | Samples | Reader Promise | Core Engine | Reward Cadence |
| --- | ---: | --- | --- | --- |
| action-progression | 20 | 主角通过可验证代价和资源积累持续变强。 | 境界/能力瓶颈 -> 资源争夺 -> 对手升级 -> 阶段突破。 | 每 3-5 章需要一次可感知收益、线索或战力位移。 |
| base-building | 4 | 读者看到据点、资源和人群从脆弱状态逐步变强。 | 生存缺口 -> 资源调度 -> 建设选择 -> 外部压力验证。 | 每个建设收益必须带来新能力、新消耗或新威胁。 |
| eastern-aesthetic | 2 | 读者获得东方审美、志怪规则和诗性意象驱动的奇观与余韵。 | 意象/规则 -> 志怪事件 -> 人情代价 -> 审美化回响。 | 每个单元要交付新意象、新规则或一次情绪余韵兑现。 |
| esports-competition | 3 | 读者看到版本、战术、团队执行和比赛压力的连续博弈。 | 版本/对手情报 -> 训练或 BP -> 比赛执行 -> 战术复盘升级。 | 每场比赛要交付战术发现、配合进步或对手升级。 |
| otherworld-cross-system | 2 | 读者看到陌生规则、身份错位和系统收益如何重塑主角选择。 | 异界规则差异 -> 身份/系统约束 -> 任务或资源收益 -> 世界反噬。 | 任务收益必须伴随新规则、新债务或新敌意。 |
| relationship-driven | 2 | 读者追踪关系距离、误会、承诺和主动选择的真实变化。 | 欲望/边界 -> 冲突选择 -> 关系轴位移 -> 承诺兑现或延期。 | 每个关系场景必须改变信任、距离、权力或承诺状态。 |
| strategy-worldbuilding | 5 | 读者看到制度、战争、朝堂和资源博弈如何被主角撬动。 | 局势压力 -> 策略误判/布局 -> 派系反应 -> 战略后果扩大。 | 每个单元要交付一次局势重估、权力转移或战略反转。 |
| suspense-mystery | 2 | 读者能跟随规则、证据和误导逐步逼近真相。 | 异常/案件 -> 线索链 -> 误导线 -> 规则或真相反转。 | 每章至少推进一条线索、嫌疑、规则效果或认知反转。 |

## Category Hard Engines

| Category | State Ledgers | Hard Gates | Chapter Updates | Fixture Benchmark |
| --- | --- | --- | --- | --- |
| action-progression | power_tier_state, resource_balances, opportunity_map, faction_pressure_queue | progression_causality_gate, resource_cost_gate, faction_reaction_gate | power_tier_delta, resource_delta, opportunity_delta, faction_reaction_delta | good-pass / bad-block |
| base-building | settlement_inventory, logistics_ledger, population_state, build_queue, external_demand_pressure | resource_conservation_gate, build_queue_gate, stakeholder_pressure_gate | inventory_delta, build_queue_delta, population_delta, demand_pressure_delta | good-pass / bad-block |
| eastern-aesthetic | image_meaning_chain, ritual_order_pressure, poetic_object_ledger, atmosphere_turn_ledger | image_plot_function_gate, ritual_pressure_gate, poetic_payoff_gate | image_meaning_delta, ritual_pressure_delta, poetic_object_delta | good-pass / bad-block |
| esports-competition | match_state, draft_bp_state, patch_meta, team_tactics, tournament_pressure | match_state_gate, bp_logic_gate, tactical_payoff_gate | match_state_delta, team_tactic_delta, opponent_adaptation_delta | good-pass / bad-block |
| female-growth-ncp | career_ladder, agency_debt_ledger, social_pressure_state, competence_growth_ledger | agency_preservation_gate, hidden_romance_drift_gate, career_progression_gate | career_delta, agency_debt_delta, social_pressure_delta | good-pass / bad-block |
| otherworld-cross-system | cross_system_mapping, identity_debt_ledger, exposure_cost_ledger, local_rule_audit | cross_system_boundary_gate, identity_debt_gate, exposure_cost_gate | rule_mapping_delta, identity_debt_delta, exposure_cost_delta | good-pass / bad-block |
| relationship-driven | relationship_state, intimacy_boundaries, misunderstanding_graph, promise_debt_ledger | relationship_distance_gate, agency_choice_gate, promise_payoff_gate | relationship_distance_delta, boundary_delta, promise_debt_delta | good-pass / bad-block |
| strategy-worldbuilding | faction_pressure_queue, institutional_agenda, logistics_ledger, treasury_state, battlefront_state | strategy_consequence_gate, institutional_pressure_gate, logistics_plausibility_gate | faction_move_delta, institutional_agenda_delta, resource_logistics_delta | good-pass / bad-block |
| suspense-mystery | rule_lattice, clue_chain, evidence_ledger, suspect_timeline, red_herring_ledger | fair_clue_gate, evidence_legality_gate, timeline_consistency_gate | clue_delta, suspect_state_delta, rule_reveal_delta, misdirection_delta | good-pass / bad-block |

## Sample Quality Parity Gate

- Required for ready: `True`
- Status: `defined_not_run`
- Thresholds: chapters >= 30, review >= 0.82, scorecard >= 80.0, reference-distance >= 0.72
- Claim rule: 未通过 sample_quality_parity_gate 的类别不得标记为 ready, 只能标记为 partial/prompt-only/unsupported。

## Capability Matrix

| Category | Samples | Overall | Category | Planning | State | Chapter | Whole-book | Gates | Repair | Anti-copy |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| action-progression | 20 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| base-building | 4 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| eastern-aesthetic | 2 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| esports-competition | 3 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| female-growth-ncp | 0 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| otherworld-cross-system | 2 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| relationship-driven | 2 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| science-fiction-progression | 0 | unsupported | prompt-only | prompt-only | unsupported | unsupported | unsupported | unsupported | unsupported | unsupported |
| strategy-worldbuilding | 5 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| suspense-mystery | 2 | partial | ready | partial | partial | partial | partial | partial | partial | partial |
| urban-contemporary | 0 | unsupported | prompt-only | prompt-only | unsupported | unsupported | unsupported | unsupported | unsupported | unsupported |
| wuxia-jianghu | 0 | unsupported | prompt-only | prompt-only | unsupported | unsupported | unsupported | unsupported | unsupported | unsupported |

## Gap Register

- `GAP-001` `P1` `action-progression` 已建立类别硬引擎契约, 仍需把机会地图、资源变动、派系后续反应接入章节后自动状态折叠。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-002` `P1` `base-building` 已建立类别硬引擎契约, 仍需把 settlement inventory、物流、人口、建筑队列接入生成闭环。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-003` `P1` `eastern-aesthetic` 已建立类别硬引擎契约, 仍需把意象链、礼法压力和诗性物件账本接入 live pilot 验证。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-004` `P1` `esports-competition` 已建立类别硬引擎契约, 仍需把比赛状态、BP/版本、队伍战术和赛事压力接入 match ledger。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-005` `P1` `female-growth-ncp` 已建立类别硬引擎契约, 仍需把事业阶梯、社会压力和 agency debt 接入长篇状态闭环。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-006` `P1` `otherworld-cross-system` 已建立类别硬引擎契约, 仍需把跨体系映射、身份债务、异常暴露成本接入章节后状态更新。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-007` `P1` `relationship-driven` 已建立类别硬引擎契约, 仍需把亲密边界、误会拓扑、情感兑现节奏接入章节级 gate。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-008` `P2` `science-fiction-progression` science-fiction-progression 缺少一等 novel category YAML 验收: `config/novel_categories/science-fiction-progression.yaml` 存在，并被 resolver / tests 覆盖。
- `GAP-009` `P2` `science-fiction-progression` science-fiction-progression 缺少 genre review profile 验收: `resolve_genre_review_profile` 能解析到 `science-fiction-progression`，并有类别权重与失败信息测试。
- `GAP-010` `P2` `science-fiction-progression` science-fiction-progression 缺少 story design grammar 验收: `resolve_story_design_grammar(category_key='science-fiction-progression')` 返回专用 grammar。
- `GAP-011` `P1` `strategy-worldbuilding` 已建立类别硬引擎契约, 仍需把制度压力、战役物流、财政与朝堂议程接入真实章节闭环。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-012` `P1` `suspense-mystery` 已建立类别硬引擎契约, 仍需用 live 章节验证 rule lattice、证据合法性、嫌疑人/时间线公平性。 验收: 新增对应结构化模型、写前 gate、章节后状态更新、好/坏 fixture 测试。
- `GAP-013` `P2` `urban-contemporary` urban-contemporary 缺少一等 novel category YAML 验收: `config/novel_categories/urban-contemporary.yaml` 存在，并被 resolver / tests 覆盖。
- `GAP-014` `P2` `urban-contemporary` urban-contemporary 缺少 genre review profile 验收: `resolve_genre_review_profile` 能解析到 `urban-contemporary`，并有类别权重与失败信息测试。
- `GAP-015` `P2` `urban-contemporary` urban-contemporary 缺少 story design grammar 验收: `resolve_story_design_grammar(category_key='urban-contemporary')` 返回专用 grammar。
- `GAP-016` `P2` `wuxia-jianghu` wuxia-jianghu 缺少一等 novel category YAML 验收: `config/novel_categories/wuxia-jianghu.yaml` 存在，并被 resolver / tests 覆盖。
- `GAP-017` `P2` `wuxia-jianghu` wuxia-jianghu 缺少 genre review profile 验收: `resolve_genre_review_profile` 能解析到 `wuxia-jianghu`，并有类别权重与失败信息测试。
- `GAP-018` `P2` `wuxia-jianghu` wuxia-jianghu 缺少 story design grammar 验收: `resolve_story_design_grammar(category_key='wuxia-jianghu')` 返回专用 grammar。

## Optimization Roadmap

- `P0` 样本与 taxonomy 对齐: 固定 40 本匿名精品样本，并把 repo/private artifact 分离。；建立 canonical taxonomy bridge, 统一 category / review profile / grammar / bucket / prompt pack。；为每个样本类别生成 benchmark rubric 骨架和能力矩阵。
- `P1` 补齐类别硬引擎: 把 high-sample 弱项类别升级成一等 category 或明确并入上级 category。；为规则悬疑、异界系统、基建经营、电竞、都市职业、武侠江湖补状态模型和 gates。；把精品样本蒸馏结果转为好/坏 fixture benchmark。
- `P2` 生成闭环与榜单级验证: 跑 30 章 model pilot，对照 capability matrix 和 whole-book gate 输出。；把 sample_quality_parity_gate 设为 ready 结论的硬验收条件。；增加 reference-distance evaluator，验证机制相似但表达不近似。；把 repair loop 结果反哺 rubric 与 strategy weighting。

## 结论

当前框架已具备 taxonomy bridge、类别 hard-engine contract 和 good/bad fixture benchmark, 能把多数样本类别推进到 partial。但只有通过 sample_quality_parity_gate 的 30 章 live pilot, 才能宣称达到精品样本同等稳定度；未通过前不能标记为 ready。
