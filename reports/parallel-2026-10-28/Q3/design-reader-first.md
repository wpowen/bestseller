# Q3：读者优先的公共规则与证据分层设计

状态：设计稿，未接入生产。代码基线 `e2e17 / 419181a4`，日期 2026-10-28。静态路径以 Q3 worktree 行号为准；`source-trace.json` 保存函数区间和 SHA，并与读取当时主线工作区比较，已列函数一致。后续并行改动须按函数名重新定位。

## 1. 结论与证据边界

系统需要知道事实来自哪里、物件归谁、事情何时发生；读者需要看到人物想得到什么、如何行动、关系如何改变。把前者逐项交代成对白或旁白，会形成程序化文风。应保留事实约束，移走“必须在正文证明合规”的表达义务。

**Evidence**：下表的代码存在真实装配／转发路径，包含明确要求正文展示代价、账面所得和见证反应的指令。也存在“隐藏硬事实、不要向读者解释”的相反指令。**Inference**：这些竞争指令可能促使写手反复交代。**Unknown**：没有回放 fa14 每次真实请求、逐轮因果对照或真人盲评，不能声称静态搜索已经证明 fa14 的具体成因。

“没有任何读者质量检查”不能作为代码事实：仓库已有 reader panel、读者模拟和可选文风审校。缺口是把阅读体验作为独立结果评估、避免被流程合规淹没，以及建立有效校准；是否在 fa14 某轮实际生效，本任务没有证据。

## 2. 静态传播路径（所有路径只读）

以下路径相对仓库根目录；表中行号指实际构造／调用位置，不仅是函数定义。

| 要求与来源 | 进入函数与代码位置 | 到达写手／编辑／去 AI 味的方式与判断 |
|---|---|---|
| 来源权限、Canon/Timeline、一次性事件不可重播 | `services/reviews.py` 的 `build_scene_review_prompts`：2110–2124；`build_chapter_review_prompts`：2799–2810 | 先给 critic，要求指出来源叶子、对象、故事时点和正文锚点；这是**审校回执义务**，不是要求故事人物复述这些字段。下游可能把自由文本建议带给 editor。 |
| 审校必须实际引用正文 | `services/critic_evidence_gate.py::build_critic_evidence_prompt_suffix`：305–330；`reviews.py::build_chapter_review_prompts`：2836 | critic 的证据协议；段落目录、引用区间、覆盖统计本就应停留在回执。不能因缺引用而给正文补一句“已经核实”。 |
| 完整来源、当前采用稿、已选约束 | `reviews.py::review_chapter_draft`：9508；`rewrite_chapter_from_task`：11539；`services/lossless_rewrite_prompt.py::prepare_source_complete_prompt`：26–85 | 来源无损传输进入 review/editor 输入；应保留来源数据及哈希，区分输入可见性与正文表达义务。不得借分层删掉事实依据或恢复有损摘要。 |
| 旧审校策略和事实修复建议 | `reviews.py::build_scene_rewrite_prompts`：2437；`build_chapter_rewrite_prompts`：2950–3002、3293、3321 | `instructions`、`rewrite_strategy` 被序列化或置于 reference-only 围栏后送给编辑；围栏解决指令权限，**不自动解决“补解释”污染**。当前 3.1 分支在 3005–3022 允许写后续行动，不要求每个新细节已在前章发生。 |
| 修复任务的来源绑定 | `reviews.py::rewrite_source_binding`：11040–11064；`review_scene_draft`：6333；`rewrite_chapter_from_task`：11544；`rewrite_scene_from_task`：12832 | draft ID/hash、source-plan 绑定、表达指导回执；这些是系统记录。新协议不得刷新旧任务哈希使其冒充适用新稿。 |
| 人物变化、选择、代价、反应、状态 | `services/drafts.py::_render_character_causal_contract_block`：5508–5566，尤其 5543、5564；调用于 `_render_contract_section`：5434、`build_chapter_first_draft_prompts`：9937 | 明确要求“必须落成读者可见的……代价……状态变化”。应改成只呈现会改变当前选择的后果，不要求每个字段都在正文落句。 |
| 数字依据、物品归属、进入／退出状态 | `drafts.py::_render_whole_chapter_logic_contract`：8789–8819；`build_chapter_first_draft_prompts`：9956 | 已有隐藏硬事实并禁止清单解释；这是可保留的边界。事实正确≠每章重复确认物件仍在某处。 |
| 认知事实与待核验记录 | `drafts.py::_render_knowledge_state_section`：6294–6400，尤其 6395；`build_scene_draft_prompts`：7347；章级调用 9887 | 认知边界用于防止角色越权知道，不应变成角色说“来源已验证”的套话。待核验记录不升级为事实或叙述义务。 |
| 时间锚点与状态清单 | `services/chapter_constraint_manifest.py::compile_chapter_constraint_manifest`：181–197；`build_safe_prewrite_plan`：313；`render_constraint_manifest_block`：605–624；`render_prewrite_plan_block`：627–646；`drafts.py::generate_scene_draft`：12083–12100 | `allowed_time_anchors[:3]` 被转成 `time_anchors_to_use`，再带“正文必须严格执行”进入写手。**允许出现**被转换为**计划要使用**是具体设计风险；不是说每轮必然写出三个时刻。 |
| 时间错误修复 | `drafts.py::_retention_hint_for_codes`：14258；`services/continuity.py::check_time_regression`：2229–2333 | 真矛盾应局部替换错误时间词；正确的内部时间顺序可留在系统，无需额外插入报时。不能为了减少报时允许时间回退。 |
| 章内事实核验 → 强制修复 | `services/chapter_continuity_critic.py::audit_chapter_continuity`：221；`ContinuityFinding.as_repair_hint`：63–68；`services/pipelines.py::_chapter_continuity_repair_hints`：5779–5807；`_render_chapter_first_local_repair_instructions`：5822–5831 | 原本 advisory 的双证据矛盾，在其他门触发重写时搭载为“必须改掉”。新指令须写成局部纠错动作，禁止自动变成补全过程或新增见证人。 |
| 来源、转交人、可信原因和到场动机 | `drafts.py::_front10_contract_violations_for_content`：9178；`pipelines.py::_render_chapter_first_local_repair_instructions`：5856–5859 | 条件性要求：开篇媒介不能成为无因果捷径。应留故事必要的因果，系统“来源真实性”不等于人物要说转交流程。 |
| 代价、账目、见证三拍保全 | `drafts.py::render_hype_preservation_block`：12817–12872，尤其 12853–12866；进入 `build_chapter_first_draft_prompts`：9790、`reviews.py::build_chapter_rewrite_prompts`：3099 | 明确“旁观者可见、账面有得”和三拍缺一不可，去重不适用；对所有高潮强制套此模型，会压住亲密、失去、沉默等高潮。应保留有出处的真实兑现，移除普遍三拍格式义务。 |
| 同一要求进入去 AI 味 | `pipelines.py::run_chapter_pipeline`：13037；`services/deslop_revise.py::_revise_prose_deslop_inner`：771–785、`revise_prose_deslop`：548 | 将上行 `hype_preservation_block` 拼进 system prompt，优先于去重；`_EXTRA_SELF_CHECK`：82–84 另要求代价与关系必须来自已有授权事实。后者禁止补造过去事实合理，不能解读成禁止创作新的当下行动。 |
| 能力越级必须正文交代代价 | `services/prompt_constructor.py::build_hype_constraints`：735、781；`build_chapter_hype_blocks`：1161 | 应保留能力边界、让实际后果可感知，取消每次重述“代价提醒”的句式义务。 |
| 保管、见证的条件性模板 | `services/entry_system_kernel.py::_grade_ladders_for_taxonomy`：563；`services/material_density.py::_category_pack_spec`：1841、1849 | 分别有“解释来源、保管成本”和“代价见证者”模板；仅在对应资产被选中并进入上下文时传播，不能认定是全局审校规则。`drafts.py::build_scene_draft_prompts`：7404 会转发 entry-system block。 |

完整文件路径均有 `src/bestseller/` 前缀。**没有找到统一要求每章“封存／公证／见证”的全局 critic 条文**；不能凭 fa14 词频反推一个不存在的函数。规划模板和被选故事资产也可能贡献这种写法，需后续对冻结请求核对来源。

## 3. 分层协议：证据保持完整，正文只承载故事

拟议公共资产 `reader-first-rules.v1`（设计，不新建生产配置）：同一份类型化规则同时渲染 writer、editor、deslop、reader rubric 与确定性 probe；固定词表、匹配器版本、阈值和单位都只定义一次。当前实现入口为 `AuditConfig` 和 `audit_prose`，返回完整配置、`config_sha256`、`rule_version`、正文 SHA 与人工别名／对白注释 SHA。没有某本书的实体名、金额、道具白名单或项目分支。报告运行器里的项目 UUID 仅选择语料，不参与规则判断。

下一阶段拟新增 `NarrativeRequirementEnvelope v2`：

```json
{
  "protocol": "narrative-requirements.v2",
  "rules_version": "reader-first-rules.v1",
  "system_evidence": {"source_binding": {}, "custody": [], "timeline": [], "review_receipts": []},
  "story_facts": [{"id": "f1", "authority": "adopted", "event_time": "...", "fact": "..."}],
  "presentation_requests": [{"fact_id": "f1", "scope": "span", "plot_relevance": "改变当下人物选择", "source": "author_or_verified_defect"}]
}
```

- `system_evidence` 写入现有模型请求元数据、审校回执／审计记录；不作为正文待交代清单。来源／保管链、SHA、模型时刻和审校验证步骤不得下放给故事人物。已有系统回执完整保留。
- `story_facts` 中的归属、人物认知、数额和故事时间对写作仍有效。写手可看事实约束；不得把审校方证明事实有效的操作写成新增剧情。事实不一定需要解释句。
- `presentation_requests` 只在事实会改变情节、读者理解或作者明确要求时生效，须带具体范围和理由；“没有说明已核验”不构成理由。法庭、公证、交接本来就是情节时允许写，不能全局禁词。
- 原来“缺字段／缺来源／无法解析／超预算”等系统问题只产生 `not_evaluated/UNKNOWN` 和修复回执，不产生“给正文加封存、报时、签字”的改稿要求。真正的故事矛盾仍需局部纠正。
- 对 editor 传 `story_facts + 有范围的修改动作`，例如修正矛盾时间或删重复解释；引用的原文、证据目录和绑定保存在独立数据区。去 AI 味只接表达约束和已有高潮事实锚点，不得被“原样保留程序三拍”覆盖。

**拟改位置**：上表中 `drafts` 的两个合同 renderer 和 `render_hype_preservation_block`；manifest 的 allowed→plan 转换与两个 renderer；`prompt_constructor.build_hype_constraints`；`reviews` 的任务生产、两个 rewrite prompt builder、表达指导 adapter；`pipelines` 的连续性 hints→修复指令转换和 deslop 调用；`deslop_revise` 的保全块消费。`web/server.py`、`fanqie_short_*` 未来只展示分层回执或消费公共协议，本任务不修改这些文件。

**版本与兼容**：保留 critic `7.0-citation-tuple-submission` 的来源选择／锚点验证，拟另加 `presentation_protocol=v2`，不复用旧版本名；editor 新请求升为拟议 `3.4-reader-first-presentation`，现有 `3.1-lossless-source-time`、`3.2-source-selected-voice-editor`、`3.3-verified-fact-edit-scope` 分支保留可回放；writer 的现有 `1.0`、deslop 的 `deslop_revise` 加新模板版本和规则 SHA。旧任务不原地改绑，不重写历史 receipt；缺新字段时按 v1 回放，并在新任务上经 adapter 明确分类。无法分类的自由文本标为 `unclassified_legacy`，交审校确认，不猜测其能建立永久事实。

上线顺序为影子生成双份 prompt/receipt → 对相同输入比对事实约束完全性及表达义务差异 → 获得跨项目读者证据后显式启用 v2。不能把删证据当降噪，也不能以 v2 重跑为名补发本任务的模型调用。

## 4. 代表读者的独立审校（只设计）

评审对象是冻结的实际正文及少量必要前情、目标类型和本场承诺；不输入先前门禁通过数、商业总分、作者身份或“fa14 有程序腔”预设。正文是数据，不能改变审校协议。每条判断带正文 SHA、段落区间、阅读感受和最小建议；找不到具体位置则降低置信度或标未知。先独立读，再对照确定性指标，避免被词频带着找罪证。

| 维度 | 权重 | 0／2／4 的评分锚点 |
|---|---:|---|
| 吸引力与继续读的愿望 | 25% | 不关心后续／有问题但牵引弱／人物欲望与未答问题让我想继续 |
| 情绪曲线与高潮兑现 | 30% | 只有程序结算或口头宣布／有起伏但高潮被解释打断／压力、选择、后果与余波带来具体感受 |
| 人物区分度 | 20% | 主要人物可互换台词／偶有差异／欲望、语气、句长、回避方式随情境仍可辨认 |
| 重复交代负担 | 15% | 多次原地确认且无新意／局部重复／重复只用于升级、反转或有意回声 |
| 节奏与阅读阻力 | 10% | 大段停顿交代、高潮中断／冷热段失衡／快慢服务当前情绪与转折 |

综合分为加权分×25（0–100），单维仍保留，禁止高吸引力冲掉情绪失败。额外输出：按场景的情绪节点（欲望／压力／转折／兑现／余波），最想跳过的两个位置，以及去掉人物名后能否区分主要说话人。

**拟议阻断条件**（尚未启用）：至少两个独立证据范围支持同一严重缺陷，并且情绪或人物≤1／4、或总分<55；“契约明列的高潮没有兑现”须核对当前本章实际承诺，不能每章强求高潮。严重问题先要求一次有范围的修订，再评一次；意见冲突交人工。确定性高密度、一个敏感词、对白 overlap 阈值、provider 失败或回执格式错误均不能单独判文学不合格。格式和来源验证失败为 `UNKNOWN`，不能计分，也不能绕过现有硬约束。

**成本估计（不是当前报价）**：每章只做一次读者调用，输入约 5k–9k token（正文、必要前情、rubric），输出 1k–1.5k；三章整窗约 14k–22k 输入、2k–3k 输出。单章预算公式 `0.009 × 输入价/百万 + 0.0015 × 输出价/百万`；若内部规划假设输入 $1/M、输出 $4/M，则约 $0.015/章；一次重评使上限约翻倍。价格只是预算示例，启用前用当前 model catalog 与真实 usage 核价；本任务不联网询价、不发模型请求。运行时仍须经 `complete_text` 并同时带 `project_id`、`workflow_run_id`，余额不够则未知，禁止静默降级为通过。

**与现有审校的关系**：`commercial_novel_gate.evaluate_book_package`（177）及 `_check_golden_three`（722）判断包装、开篇结构和商业承诺；读者审校独立判断“读到这里的感受”，二者保留各自结论。已有 `reader_panel_judge.run_reader_panel`（72）按角色多次调用、返回问题列表，无五维分数；可复用其反馈归因格式，但不要额外叠三人 panel。`reviews.py:9229–9274` 和 `pipelines.py:13227–13266` 已有文风 advisory，不应声称项目完全没有文风评估；新维度先与其并列校准、同一问题按正文范围去重，不把同一审校意见当独立票数。

## 5. 给写手的公共约束（建议值，未变成门禁）

以下为待校准的编辑预算，不是文学生物常数。正式公共资产将同时导出 prompt 与审计配置；本轮确定性模块只量测，不内置这些数值为通过阈值。

| 对象 | 默认表达预算 | 故事相关例外与处置 |
|---|---|---|
| 具体钟点 | 每 1000 非空白字符≤2处，任一滑窗 500 字≤2处；转场优先用合理相对时间 | 倒计时、行程、时刻表等关键因果可超过；在系统备注必要性，正文无需解释“为何允许超限” |
| 代价提醒句 | 每场≤1处显式解释，同一代价在状态未变前不再提醒；每千字≤1处 | 新选择带来新后果、人物争执或误解升级可写，优先让后果发生，不强求“代价”这个词 |
| 同一物件／状态确认 | 1000字窗口内，首次交代后≤1次无变化重述 | 状态确实变化、人物误认、悬念回声可重复；例外须说明叙事作用，而不是机械避词 |
| 流程词与否定链 | 流程词先观察，不定全局硬上限；连续≥3否定分句提供编辑线索 | 调查、法庭、仪式或刻意声韵允许；反复换同义词不等于改好 |

人物声音表每个主要人物必须有：当场欲望、常用句长／停顿、词汇来源、直接还是绕说、避免说的东西、受压力时如何变形；至少两项与另一主要人物不同。差异来自身份、关系与行动，不靠给每人硬塞口头禅。只有角色当场在履行核验工作时才自然说核验语言；两人争夺、求助、挽留时，不得一律“确认状态—说明代价—签字交接”。对白重合度用于挑片段，不能替代角色判断；共享话题和引用原话另记。

情感场景先明确人物想从对方得到的具体回应，用行为、被打断的话、注意力落点和关系变化承载情绪；高潮允许停顿、失语、失去或没有旁观者。高潮落地后留必要余波，不立刻插账目、物件无变化确认或程序解释。保留会改变这一刻选择的事实即可；不强求每场都有胜负、见证人、金钱或“我终于明白”的总结。

## 6. 本轮检测器的实际边界与泛化验收

`services/prose_procedural_density.py` 仅导出 `audit_prose`、`AuditConfig`、`DialogueTurn`；无数据库／LLM／流程注册导入，无全局副作用，无 `passed/must_rewrite`。输出 `audit_only`，六项观察分开：合法具体时刻；同一文本对象＋规范化状态的重复；规范数值＋单位的重复；可替换字面流程词表（最长不重叠）；连续否定分句；明确说话人的字二元组 Jaccard。

窗口以原文 codepoint 计、率分母为非空白字符含标点；所有跨度半开且可回切原文；全角仅做单字符等长归一化。账目重复不代表错账，也不保证同一语义；跨章不自动累加状态或数值。状态仅支持显式主体和少量常见状态等价，任意换说法、代词、隐含状态不能可靠识别。对话默认至少2轮、40个净字符／人，且至少两名合格说话人才给重合度；无法确定的“他／她／对方”不建人物身份，样本不足是未知。

合成测试先失败再实现，覆盖时间多写法、中文／阿拉伯数、同义单位、物件改名、状态变化负例、窗口、空文本、可替换词表、否定习语、归属不足和不同声音。新增对抗用例先得到真实行为失败后修正；没有根据 fa14 的道具或金额增加规则。最终统计见 REPORT 和 `tests-final.log`。

跨项目验收要求：同一冻结规则配置跑 fa14、d407 前三章及另两个已采用项目；正文只在本机、采用版本和哈希可追溯，逐类有真阳性／误报／漏报抽查及未触发对照。词频有差异只是区分信号，不是精确率；若只在 fa14 命中，或其他书只是未触发而没有正例，不能宣布泛化通过。初次交付时因采用项目不足而待补证据；按2026-10-28补充口径，现已加入玄幻最新候选（候选，未采用）和《天机录》旧链路40章，并完成175条 `agent_editorial_not_human` 编辑判断，详见 `completion/precision-and-judgments.md`。真人回执留待后续，不阻塞本次交付。**泛化检验工作已完成，但六类规则作为通用写作质量检测器均不合格**：四类误报多或跨来源收益低，状态和对白另有覆盖／校准不足。因此维持 audit_only，不晋级门禁。
