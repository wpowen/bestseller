# T2.0 在途操作迁移 · 进行中

迁移遵循同一原则：保留原 operation key、input hash、attempt、原创建时间和费用。新排序不能把旧调用变成一次新的收费。2026-10-28 的隔离只读盘点已取得全部20条未闭合操作（stopped16、unknown4），并以原生失败回执连接16条实际模板。迁移规则的运行验证仍待E22；不能把盘点通过当作迁移已生效。

## 可复现盘点口径

- 在既有一致性隔离快照、只读事务中扫描全部项目，不加LIMIT。以`execution_operations`为操作根，关联budget与attempt；分别统计`pending/unknown/stopped`，排除同表的`transport_result`等回执。操作按不同key计数，attempt另计。
- 归属从原LLM receipt 的operation key+scope、原paid_call_origin及workflow.project_id交叉核对；缺失或冲突归Unknown，不信任当前恢复owner或scope前缀。另列ledger completed但正文/知识/业务尚未闭合的窗口。
- R：native report/cycle确认的审校；W：scene_writer/scene_writer_regen/chapter_first_writer；F：chapter_feedback_extraction及repair；P：后续卷cast_expansion。模板只作候选，须核原始归属；链可重叠，总数不直接相加。F0-21未证可达单列Unknown。
- 按project×链×budget/operation/attempt状态输出；scope为conception的11条、continuation的9条只是预算标签。阶段交接后仍沿用scope，不允许据前缀认定不受F0影响。

## 原生盘点结果

- 7条构思模板：调用链不进入18个F0 reader；其中4条Unknown保持原状态，不因与F0无关而允许重试。
- 4条正文/审校模板：1条chapter_rewrite_repair（项目d9c76f6d）、2条chapter_commercial_quality_judge、1条chapter_review（项目fa14e681），全部stopped，按R/W来源变化纳入迁移保护。
- 5条项目规划/规划准入操作：已确认不是构思；具体F0输入可达性未闭合，保守保护，不能宣称豁免。
- 4条无足够原生模板证据：保持归属Unknown，禁止根据无LlmRun或无attempt推断未发送。
- 500章任务在快照无Project；其scope有1次调用、13,657 tokens和1条stopped操作。没有证据把这次调用等同于该stopped操作。

部署前20条原operation均保持原绑定与终态。缺失历史输入/阶段证明时拒绝自动切换摘要方案，不能通过生成另一个operation绕开stopped/Unknown。完成无新增调用的原输入复验，不代表授权派发新调用。

## 部署后的处理与E22

1. 原物理request/response及完整native paid链可证：按记录字节核对，保持原操作身份，当前适用性单独复验；不重排历史来源。
2. 未发送且无法证明旧输入：先持久登记摘要方案迁移及旧/新绑定，显式形成新操作；不得把旧attempt计入新预算或覆盖旧记录。
3. 已发送且结果Unknown，或发送状态无法证明：保持Unknown，阻止自动重发/新付费；由原有reconciliation处理，不造成功/新credit。
4. E22覆盖未发送迁移、可复用completed、sent-Unknown、缺失/冲突proof、重复resume及崩溃重入；断言不静默重付、不清Unknown、不重置counter。迁移写入尚未实现，G2不部署、不付费。

可复核证据位于协调工作区 reports/v3-repair-execution-2026-10-27/g2/：ledger-native-20261028T022332735206Z.json、operation-stage-diagnostic-rescope-20261028T040312017384Z.json、LEDGER-MIGRATION-CLASSIFICATION-v2.json。补充阶段诊断为6 SELECT/1 SHOW，零模型、零写库、零生产访问。完整20条key和回执摘要见机器清单；E22在G2b-2完成。
