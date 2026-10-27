# T2.0 在途操作迁移 · 进行中

迁移遵循同一原则：保留原 operation key、input hash、attempt、原创建时间和费用。新排序不能把旧调用变成一次新的收费。当前已完成口径与处理规则；全项目及500章书数量仍为 **Unknown**，Docker查询超时，未读取生产或把202条LLM索引冒充全量账本。此任务尚未通过。

## 可复现盘点口径

- 在既有一致性隔离快照、只读事务中扫描全部项目，不加LIMIT。以`execution_operations`为操作根，关联budget与attempt；分别统计`pending/unknown/stopped`，排除同表的`transport_result`等回执。操作按不同key计数，attempt另计。
- 归属从原LLM receipt 的operation key+scope、原paid_call_origin及workflow.project_id交叉核对；缺失或冲突归Unknown，不信任当前恢复owner或scope前缀。另列ledger completed但正文/知识/业务尚未闭合的窗口。
- R：native report/cycle确认的审校；W：scene_writer/scene_writer_regen/chapter_first_writer；F：chapter_feedback_extraction及repair；P：后续卷cast_expansion。模板只作候选，须核原始归属；链可重叠，总数不直接相加。F0-21未证可达单列Unknown。
- 按project×链×budget/operation/attempt状态输出；`projects.target_chapters=500`的全部项目逐一列出，不取第一条。目前数量/分布/业务闭合状态均Unknown，待隔离PG恢复后补齐SELECT与整表前后指纹证据。

## 部署后的处理与E22

1. 原物理request/response及完整native paid链可证：按记录字节核对，保持原操作身份，当前适用性单独复验；不重排历史来源。
2. 未发送且无法证明旧输入：先持久登记摘要方案迁移及旧/新绑定，显式形成新操作；不得把旧attempt计入新预算或覆盖旧记录。
3. 已发送且结果Unknown，或发送状态无法证明：保持Unknown，阻止自动重发/新付费；由原有reconciliation处理，不造成功/新credit。
4. E22覆盖未发送迁移、可复用completed、sent-Unknown、缺失/冲突proof、重复resume及崩溃重入；断言不静默重付、不清Unknown、不重置counter。迁移写入尚未实现，G2不部署、不付费。

盘点脚本已准备：协调工作区 scratch/inspect-g2b-ledger.py；当前仅语法核验，未连接数据库。全量账本分类与projectless祖先scope仍须隔离库native复验，不能用模板候选分组替代F0已证影响。
