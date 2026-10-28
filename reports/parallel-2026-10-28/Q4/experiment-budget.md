# Q4 新方案实验：140物理发送上限，尚未获准启动

2026-10-28 20:55决定已替代原80次、双臂重跑设计。只生成新方案；旧方案使用历史骨架、首章v1之前的完整规划及第一章v1。付费授权存在，但本轮未满足离线全链预检，实际付费发送和tokens均为0；完整逻辑请求数为Unknown，不能填0或拿局部测试次数代替。

## 1. 已核定输入

证据来自仓库外Q1/Q2冻结文件，只读且未改动。详情见historical-input-evidence.json：原始创建意图39c7113c-6563-4d15-90d5-c6581df9659f的concept_seed，原文71字符，SHA256为2a80fdac61da8f08389b89736d74eb09a3dede09dfc83c253c560bd86b711bb4；目标4段、8000字，契约入口fanqie_short。未找到原HTTP/UI提交，按已冻结实际链路判断。

历史planner、writer、critic审计均有openai/MiniMax-M3.1-Flash记录，输入选择为minimax-m3.1-flash。写手chapter_first，记录override16384；planner book/world override12288、volume8192，来源审查critic5000。override不是物理发送参数证明。fa14请求未保留，temperature/top_p/seed/reasoning仍Unknown；不抄用d407请求，也不把今天默认配置冒称生产当时配置。新实验配置必须另行冻结，并报告模型与时间偏差。

旧第一章v1在Q1/Q2两处逐字一致，SHA256为41e2c8180821731161c597e5a250dce455a19f68210fcaa82db3147ebc77e3ca。旧章纲使用首章之前的v1（第1—4章完整），不能用首章之后修订的v4混成旧臂。

## 2. 真实拓扑与当前阻塞

原链路：构思 → foundation → ideology kernel → Beat producer → segment projection → contract completion → 来源文件/持久化 → Bible/outline物化 → admission → graph → 首章初稿。普通planner章纲路径不是这条短篇路径的替代品。

Q4允许文件可改构思与普通planner/writer。fanqie_short_pipeline、fanqie_beat_recovery、fanqie_outline_contract_completion仍需主线消费同一绑定、提示版本、最终大纲读者审校及通过来源。只有主线完成这些接缝并通过原生来源消费者互认后，才可以运行完整的新短篇方案。

check-experiment-readiness.py仅作静态路径准入，明确不是scripted全链计数器；experiment-readiness.json/log记录STOP、完整逻辑数null、付费0。没有完整scripted新链执行证据，也没有已生成的新首章。因此没有制作虚假的甲乙盲包。

## 3. 启动次序与封顶

1. 实现与目标测试通过；主线接通原短篇入口，保留来源、硬约束、历史批准和UNKNOWN边界。
2. 在隔离环境用scripted假模型跑实际构思→全规划→章一初稿，所有logical complete_text请求逐项计数；解析修复、分片、审查及两份对称同尺评估均包括。估算>120即停；Unknown或不完整同样不启动。
3. 只有预检合格才发第一笔付费请求。按原生ExecutionLedger，在每次物理发送前耐久预留，总限140；失败/429也算，140后第141次拒绝。完成授权不能绕过本实验上限，换workflow/进程不清零，远端UNKNOWN不退额。
4. 新旧同尺评估分别生成报告，同一冻结rubric和模型配置；新方案生产审校与事后评估另列账。两份报告含骨架和首章，实际切分/调用数由容量决定，不能把两份报告强称必定两次发送。
5. 盲包甲乙随机、同字段摘要、同格式正文；KEY单存experiment/，正文/提示词仅仓库外0700目录，文件0600。旧臂为历史产物，模型和时间不同是已知偏差。

本轮没有验证实际140-send端到端硬围栏，不能把已有网关能力或零付费误写成限额已实测。预算/网关若需改动，交主线，不越过Q4文件范围。

## 4. 单次与实际读数

新增critic骨架输入≤12000 estimated tokens，大纲≤24000，输出4096；阈值单维0.75/均分0.78未校准。单次上界分别为0.012×P_in+0.004096×P_out、0.024×P_in+0.004096×P_out；规划修复另按真实请求计，单独计费reasoning另列。单价与实际usage未冻结时费用记Unknown，不能虚填0价。

|指标|本轮|
|---|---|
|完整scripted新链逻辑请求数|Unknown/未执行，未通过启动门|
|实际付费物理发送|0|
|付费input/output/reasoning tokens|0/0/0|
|已发付费消费|0（没有请求）|
|新首章、完整甲乙盲包、真人盲读|未形成/未执行|

局部离线测试仅证明版本、解析、来源、停止和额度消费分支；不能证明真实模型会给出人物情感更好的故事，也不能证明140次能完成。
