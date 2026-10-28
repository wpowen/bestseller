# P1 提交内容自查 · 2026-10-28

结论：逐个审查原提交的29个文件（12,969,526 bytes），未发现正文、prompt原文、人物或作品文本、密钥、完整连接串。递归检查JSON/CSV及嵌入的meta JSON，结合提取字段白名单、非空正文容器、长字符串/中文值与凭证模式复核；未将模板名、版本号或哈希误当正文。此结论是已提交内容检查，不是生产库审计。

但原提交包含逐次调用标识、执行回执、项目/工作流关联及操作元数据，超出“统计、摘要和SHA”的最小交付。已先在仓库外完整私有存档并逐文件验证SHA，再删除16个明细/查询/采集文件；没有修改应用代码、配置、主工作区、数据库或其他分支，没有重新采集数据。

私有存档：`/Users/owen/.codex/private-artifacts/par_P1_self_audit_20261028_435ef9b0/`，目录0700、文件0600，位于仓库之外，不进Git；ARCHIVE_MANIFEST.json记录全部原文件SHA。原始明细只保存在本机，不在自查报告中复制字段值。

历史限制：`9410b7523af02e44cc372c93b21a19ea42dcd8eb`引入这些逐次运行明细；`435ef9b052936dded2ac5936de05413e12b53550`继承它们。**两提交将来均不得合并，P1分支历史也不得整体合并。** 本次新提交删除当前树内容，不会清除旧Git对象，不进行历史重写。不得将此问题描述为已确认泄露密钥或小说正文。

除三份核心交付外，原提交中≥10 KiB的大文件逐项如下（完整29文件清单、原字节数及SHA见FILE_INVENTORY.csv）：

|原文件|原字节数|当前处理|未来合并main|
|---|---:|---|---|
|snapshot.json|7,295,720|私有存档，Git删除|否|
|retry-signals.json|2,598,852|私有存档，Git删除；原为全库细分统计，仍非必要交付|否|
|supplement.json|1,043,664|私有存档，Git删除|否|
|reconciliation.json|910,923|私有存档，Git删除|否|
|physical-attempts.csv|136,050|私有存档，Git删除|否|
|discovery.json|25,890|私有存档，Git删除；原为schema/键名统计|否|
|DESIGN.md|12,959|保留人工设计摘要及证据位置更正|否；仅本次本机参考|
|analyze.py|12,088|私有存档，Git删除；避免提交非统计代码和失效重放入口|否|

其余被删除文件：ledger-summary.csv、queries.json、reconciliation-queries.json、supplement-queries.json、collect.py、discover.py、reconcile.py、retry-signals.py、supplement.py。完整disposition以FILE_INVENTORY.csv为准。保留的成本CSV、top10、summary、说明文档仅有统计/摘要/标识标签和SHA；默认均不合并main。三份核心交付也只能按清理后文件单独审查取用，不代表获准合并。

验证：核心两份CSV与私有存档逐字节一致；原账本和聚合数字未重算。逐文件审查结果见audit.json；当前保留文件SHA见SHA256SUMS。未读取生产数据库，未使用Docker或付费业务模型。
