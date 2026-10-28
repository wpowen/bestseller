# P1 口径、精简设计与证据限制（正文报告的技术附件）

所有源码路径相对本 worktree，版本为 e2e17 / 419181a41f29363263cee8a12d1694ace0bcef24。仅分析，不代表线上实现与标签版本完全相同。

## 口径与覆盖

- `basis=llm_runs` 是遥测记录；两本目标书以 execution_receipt.scope_id / conception_task_id 补回项目创建前记录，并按 llm_run.id 去重。`original` 对应 d407ca85-9a9b-4564-a033-6bc529e0dd76，`completed` 对应 fa14e681-2bd9-44f5-93d1-7f2ef9900d69；后者另含三个 continuation scopes。所有 NULL 项目记录均纳入扫描；仅有确证的两书构思记录移入生命周期，不把其余历史记录猜配到现存项目。
- `basis=execution_attempts` 是目标生命周期的实际账本尝试。按 attempt.id 计数；连接 operation_key，再从 transport_result 补回两次有 usage、却没有 LLM 遥测的完成调用。失败回执、业务 operation、transport_result 不另加调用；一笔 attempt 只算一次。
- 两种 basis 是同一花费的不同视图，不能相加。目标两书分别有202/362条遥测、204/373次账本尝试。LLM 遥测恢复标记2条仍对应原有付费回执，不能再当两次新增调用。两书的遥测 operation_key 都唯一。
- `input_tokens/output_tokens/measured_tokens` 只算已返回 usage；`unknown_reserved_tokens` 是实际 usage 缺失时保留的上界，不是已确认消耗。账本 `charged_tokens = known actual + retained reservation`。LLM basis 的 charged_tokens 只是记录 token 合计，不代表供应商账单；全库包含407条非付费 fallback，3,721,260个估算 tokens，可用 nonpaid_fallback_* 单独扣除。
- `truncated_calls` 依据 finish_reason=length/max_tokens/max_output_tokens；没有可解析响应的 timeout 不伪称截断。
- `explicit_retry_calls` 只计 metadata.attempt>1、semantic_repair_of、length_retry 有明确标记者，不是全部重试；其余模板名带 repair/rewrite 单列解释。`after_unknown_calls` 表示同章在某次 Unknown 审校之后发生的调用，只有时间关联，不能据此全部算浪费；不同正文版本必须重审。显式重试标记现已由retry-signals.json覆盖全库；after_unknown时间关联只覆盖目标书，other_projects/unattributed 的0代表未提取该关联，不代表没有后续调用。ledger 中无遥测的失败也没有该关联覆盖。metadata.verdict未发现unknown/fail标记，不可替代审校表Unknown。
- 阶段按模板语义优先、逻辑角色兜底。planning_draft_admission 等 critic 请求归规划；conception 中的 editor 修复仍归构思；整书语义和书名修订归整书；知识包括 bible、状态、反馈提取和证据对齐。未命名模板按 planner 归规划，但保留 `(missing)`。
- 主快照北京时间 2026-10-28 12:20:40；补充回读约12:20–12:35，均 repeatable_read + READ ONLY，statement_timeout=30s。各次读取数量与目标usage对齐。没有读正文/prompt、供应商账单、已禁私有目录或隔离库。
- completed 项目状态及四章 complete/ok 已回读；export_artifacts 两书均0条，completed 另有整书导出 metadata 回执类型/摘要。未读取导出文件内容，不以成本统计再次宣称出版/读者验收。

## 原书与完成书的重复花费

- 原书：7次主审和21次商业审校，基线四章一次主审+三商业样本为4+12次，额外3+9次均在第一章修订链；另有3次改稿。对应不同 primary_review_body_sha256，不能直接缓存掉。两次 length 中 conception_finalize 后确有同模板后续调用；商业 length 后没有同章同slot后续回执，不能假定已经恢复成功。
- 完成书：27次主审、83次已返回商业审校、25次改稿；账本另有1次主审/2次商业失败。商业已返回按章为18/32/12/21；主审6/11/4/6。相对首轮的23次主审、71次商业是修订/补样本/重评的额外量，不是71次相同请求重放。source hash多数不同，不能合并已变化的版本。
- 完成书10次 length：概念内核1、cast3、章纲手术修复2、商业3、整书语义1；其中9次后有同模板、同章/slot的后续记录（不证明同源）。原书2次length中1次有后续。
- 审校表 Unknown 原书2条、完成书12条；Unknown 后同章主审/商业调用分别17/90次，全阶段关联27/128次。这只是后续成本上界，不能归因为同一失败。仅找到完成书1组相同 template/version/prompt_hash 的窗口复审重复，以及原书1组画像点击重复；后者可能是有意独立投票，未定性为浪费。画像样本和商业slot必须保留独立性。
- 原书失败明确为 logline_gate / HTTP500 / MidStreamFallbackError / physical_sends=1，保留47,934。完成书未返回usage的10次含 TimeoutError、Timeout(408)、ValueError、ExecutionBudgetError、UnusableProviderResponse、CancelledError，详见 physical-attempts.csv。具体 error code 可用 HTTP 状态与 exception_type；没有错误码的记录保持空值，不编造。

## 可选 short_economy_v1：98次是待验证工程目标

**现状结论：没有证据支持仅改现有配置就能完成四章且<102次。** 以下是需要开发/验证的 opt-in 设计，不是已实现配置，也不是完成保证。默认门禁、默认候选/样本数和现有配置文件均不修改；商业始终保留3个独立付费sample slots，故事/编辑各2票。任何批处理不得把一份响应的三答案当三个独立样本。

以原书204次为基线，互斥按阶段分摊，避免候选缩减和轴批处理重复计节省：

|阶段|原书|目标|省调用|原书已知tokens|目标tokens（估计）|
|---|---:|---:|---:|---:|---:|
|构思|90|37|53|566,537|250,000|
|规划|30|10|20|1,393,753|550,000|
|草稿|14|4|10|230,358|118,000|
|审校|46|34|12|1,215,488|1,100,000|
|改稿|3|3|0|85,651|86,000|
|知识|19|8|11|206,940|110,000|
|整书|2|2|0|4,341|4,500|
|导出|0|0|0|0|0|
|合计|204|98|106|3,703,068|2,218,500|

预计调用降52.0%、已知tokens降约40.1%；相对完成书373次/12,938,980已知tokens，98次仅是更乐观的对照，不能保证完成书的25次必要改稿也会变3次。不能把Unknown预留消失算成token质量收益。

具体设计及不能相加的子项：

1. 构思：新建任务选已有 complete，禁止恢复时改档位。源码已将其压到2个创意/1张完整卡，不计额外收益。可选档把 copywriting.n_candidates 从3设1（默认保持3），只减少探索广度，保留画像默认2样本及五轴/引文检查；五轴按同一简介同源批量输出5份类型化子回执，保留每轴判定。现有 n_candidates 能设，独立档接线和多轴批处理需改代码。独立子项3→1的干净路径估算省18次，25轴→5轴批量估算省20次，**二者重叠，不相加**。构思37次的整体目标要求保留概念赛约17次，简介约10次，其余source生成/同源核验约10次。后两者需要先做依赖图证明（人物links必须等待人物生成，不能提前合包），不能把依赖于上一步结果的核验提前合并；若保留独立默认样本后仍超额，37目标失败，必须如实上调总数。
2. 规划：新增 opt-in `planning_packet` 提交模式（**提议字段，不是现有配置键**）。同源人物的分片生成合包，9人物+1links+5字段纠正等按源码schema逐人校验；book/world/volume保持独立依赖边界。补齐源字段和输出容量后再发送，把30→10作为目标，省约0.84M tokens。现有修复30次并不全是可删除项；若大包导致截断/遗漏，回退后目标作废。主要需改现有source_cast_parts/规划接线；不建议为省调用开一个绕过事实源的新模块。
3. 草稿：可选档使用 chapter_first_writer；4章各生成一次，场景卡仍作为输入约束，正文采纳后再投影场景。不同时生成10份场景正文和4份章正文，省10次/约0.112M tokens。但已观察到的scene_writer可能是修订链的有用产物，不能直接删历史调用；必须验证完整章稿能够替代其功能，并保留回退和重审。是否已有合法开关须由后续实现核对；本次不宣称零代码可做到。
4. 审校：主审7次和商业21次全部保留，改稿后仍重新审。其余17次辅助判官按实际同源checkpoint合为5份含各检查器schema的评审packet，另留1次adjudication，46→34，省约0.115M tokens；需新 opt-in 调度接线。只合并不同检查维度；同一检查的独立样本仍分开。风险是错误相关性提高，必须用保留的缺陷样本验证召回，批响应缺项即Unknown。可缓存的仅是同正文、同合同、同模型与同sample slot的已完成回执。
5. 知识：可选 `knowledge_packet`（提议字段）将bible/状态/反馈及证据对齐做每章类型化联产，并保留每域校验和原Canon/Timeline单调性；19→8，省约0.097M tokens。反馈证据对齐如果依赖外部结果不能强行前移；需改现有knowledge/feedback接线，后者是本次禁改区，只提供设计。失败时不得吞掉知识闭环。
6. 改稿、整书、导出：不省必需门。按原书保留3次改稿、2次整书调用；导出确定性重读0次。额外修复必须记入预算，不能为达98而跳过。现有商业cohort/规划/Beat/已付费传输结果的精确复用属于已有能力，本设计不把其收益重复入账。

98次的验收条件：相同四章8000字任务，逐阶段DAG能满足以上分配；所有默认门及独立样本保留；修改前固定缺陷集验证召回/误放；真实端到端<=101账本attempts、4章闭环、当前源审校通过和导出回读齐全。预算128次不是减半验收线。若额外改稿/失败使attempts≥102，精简目标未达成；停止/Unknown不能当完成。以上真实验证需要另行授权，本次未执行。

## 预算建议与数据验证

当前 settings.py:773 默认64/1M/3600秒。production_strategy.py:32附近的 reserve_quote 另用14+6N次、20,000+12,000N tokens；四章仅38/68,000，不是生产实测预算。scope初次持久化后不能用resume/新workflow重置。

建议按**已选择配置档的阶段DAG报价**，调用计数、measured usage、未决预留分开。D=max(N,ceil(W/2000))，ceil_k为向上取k整数倍。两样本同为N=4/W=8000，**不能识别真实章数/字数斜率**；以下仅短篇初始样本锚定容量包络，不保证覆盖所有修复，不是统计拟合，也不适用于直接外推长篇。

现状档（样本锚定建议，可能不足而停止）：

- C=ceil_16[1.25×(149+56D)]。
- T_usage=ceil_100000[1.25×(4,761,524+2,044,364D)]。
- R_unknown=ceil_100000[1.25×(7,186,524+756,144D)]。
- T_charged=ceil_100000[1.25×(11,948,048+2,800,508D)]，不是把先分别round的两项再相加。

149次=完成书构思94+规划55；56=其余224/4。相同方式拆已知tokens/保留预留。四章预测未留余量为373次/12,938,980已知/23,150,080计费，精确覆盖完成书，并高于原书204/3,703,068/3,751,002；留25%后为**480次/16.2M已知/29.0M计费上限**。这说明当前默认不足，**不是建议一次性授权消费29M**；高预留必须先核对实际生效payload/acceptance_proof，不可谎报成供应商usage。

精简档目标：C_lean=ceil_16[1.25×(47+12.75D)]，四章128次技术额度；T_usage_lean=ceil_100000[1.25×(800,000+354,625D)]，四章2.8M。未决预留不照搬完成书10.21M：使用每个有效请求的实际容量quote，取允许并发未决的Σreservation headroom；总上限为已知usage预算+该headroom。Unknown仍阻断，只有既有授权回执能结算；不得自动补额或增加重试。

上线前用更多真正完成的同配置档样本替代包络：DAG角色/模板计数×同模板输入/输出p90，额外修复率与预留单列；每份quote记录版本/来源SHA。输入tokens至少含固定指令与来源装配，不可仅按目标字数估输出token。可支配预算与技术上限、供应商账单分别处理。

## 执行与错误记录

- worktree首次创建exit255：sandbox不能创建 .git/refs/heads/par/P1；受审授权重试，显式refs/tags/e2e17成功。未修改main/fix/g2b等引用。
- 首次DB连接PermissionError（sandbox，无SQLSTATE），受审授权的本机只读连接成功；没有使用Docker。
- 初始rg指定了e2e17不存在的.env.example，exit2/ENOENT；改为仅查看现有文件名及键名，无凭证输出。
- 补充脚本import collector曾重复执行一次只读收集；已加__main__ guard，无生产写入。重复读取不算LLM调用。
- 离线聚合第一次exit1/TypeError：缺失prompt_version=None与str不能排序；统一为(missing)后成功。未改应用源码，无需新增应用测试；用独立CSV/账本对账验证交付。所有目标失败均未达3次。

## 2026-10-28 自查后的证据位置

上文提及的原始JSON、逐次CSV及查询/采集脚本已移至仓库外私有存档；引用仅解释原统计的来源，不代表这些明细仍允许进入Git。当前交付以聚合CSV、summary.json及SHA为准。旧提交9410b752、435ef9b0及其分支历史不得合并；逐文件审查和完整清单见AUDIT.md、FILE_INVENTORY.csv。
