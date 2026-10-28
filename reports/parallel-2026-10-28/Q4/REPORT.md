# Q4 实现与新方案实验进度（2026-10-28）

已实现Q4允许范围的人物/情感优先协议、两处严格读者审校与一次有界修复，以及来源绑定的v3写手提示词。付费实验没有启动：冻结证据确认fa14走短篇链，仍缺设计中归主线负责的Beat/合同补全及来源消费者接入，完整离线调用数尚未测得。实际付费发送0次，未形成新首章或甲乙盲读包；不能据局部测试宣称效果改善或预算可行。

## 实现和验证

- 新构思在生成前绑定reader-first-planning.v1、原输入SHA和配置SHA；无标记历史项目/旧回验仍走原协议。缺真实project/workflow身份则在首个模型步骤之前拒绝，主线须接好调用方。
- 骨架与完整章纲按四维单维≥0.75、均分≥0.78执行，明确**未校准，以用户盲读校准**。缺项、假引文、fallback、超容量与未知发送均不能通过。失败保留各维得分/具体理由/2—3方向；一次完整规划修复额度发送前保存，恢复、换workflow不重置，改过来源重审。
- 普通/来源绑定规划、中英文及写手提示词共享规则；完整事实、否定、精确时间、金额与来源保留。v3重核完整已审来源、字段角色、章节指针和原审校；旧批准不能静默升级。超容量长书在尚无完整批次适配时停Unknown，不裁成前十章冒充通过。
- 先失败测试再实现；汇总207项通过（Q4专属169、来源绑定回归38）；命令见implementation-verification.json、implementation-final-tests.log。planner扩展组当前17失败、固定基线18失败，无新增失败/错误指纹；不能称全部回归全绿。各子阶段RED/GREEN及独立审阅修正证据保留在本目录；日志仅保留用例、异常码、差异指纹与汇总，不收录完整prompt断言。验证属于离线合同与回归，不能替代真实模型、生产采用或真人质量证据。

## 主线冲突检查

基线refs/tags/e2e17=419181a41f29363263cee8a12d1694ace0bcef24。实现前同时固定int/full-flow=9bfde9a52cd70a147cbe9ead04a0763e01a7bb85、fix/g2b=ffb49f67a4e15d532fc3476bd5745774e59466c6；七个允许服务及methodology路径日志/diff均空，int工作区这些路径的未提交diff也空。当时无函数重叠。收尾时主线已到a84258c2，e8ccb558新增篇幅/标题合同，唯一函数交集为drafts.build_chapter_first_draft_prompts；Q4未覆盖主线，局部合入保留要求见mainline-overlap.md和对应JSON。

仅修改Q4七个允许服务、版本配置、专属测试和报告；workflows仅大纲修复函数、drafts仅四个writer提示函数。没有改主线/短篇专用模块、网关、预算、采用验证器或历史迁移清单。par/P2及128afbdd工具未改，不合并、不推送。

## 历史输入与实验门

historical-input-evidence.json记录Q1/Q2既有冻结来源。原意图39c7113c-6563-4d15-90d5-c6581df9659f：4段、8000字、fanqie_short；原seed仅保留71字符统计和SHA。旧第一章v1跨Q1/Q2逐字一致；旧章纲取首章前v1，未混入后来v4。历史写手模型openai/MiniMax-M3.1-Flash，采样因物理请求未保留仍Unknown，不抄用d407。

实验按20:55决定只跑新方案，旧臂用历史产物，模型/时间不同为已知偏差。完整scripted逻辑估算>120或Unknown不开始；开始后含失败/429/对称同尺评估的140物理发送按原生账本封顶。当前静态准入脚本明确不是全链计数器，experiment-readiness.json将实际完整逻辑数记null、付费启动false。未安装或宣称实测140围栏，没有消耗付费额度、连接生产DB或使用Docker。详情见experiment-budget.md。

## 交给主线

- design-for-mainline.md：意见分流、共享两轮正文修改、高潮保护、状态/UI方向选择与旧来源迁移验证。
- 条件接缝：短篇Beat/recovery、contract completion与最终大纲读者检查；入场及admission producer保存reader_first_source envelope；v3容量/来源证明消费者互认；scene运行调用方加载v3。缺任一项，新项目写作保持阻断，不借旧批准通过。
- 主线接好真实短篇路径后，才可继续完整离线计数→≤120预检→140硬围栏验证→付费新方案→两份同尺评估与盲包。无需重跑历史旧方案。

分支par/Q4；代码/测试与证据提交`27091a522edf430fc06da658bfe9cd79a3cbb98f`。最终207项通过，源码/测试SHA、能力边界和敏感检查见implementation-verification.json。正文、提示词与原始输出未进Git；报告只存统计、摘要和身份哈希。
