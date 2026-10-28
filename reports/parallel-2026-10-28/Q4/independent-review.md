# Q4 独立复核与修正

审阅只读源码并作内存复现，没有DB、网络或模型验证。结果与已知主线接缝分开：

|问题|处理和证据|
|---|---|
|writer块内重算哈希仍能沿用旧review；不存在的章节指针可自洽通过|已补RED并修正：消费时重核完整已审source/raw review/pointer；Q4report/writer-independent-review-final-green-20261028.log|
|chapter-first漏传reader-first绑定，因果块及外层仍要求状态显式表演|已补完整builder用例并修正版本分支，旧提示字节对比保留；同上日志|
|review引文数组指针接受00或非ASCII数字|protocol-pointer-red.log复现2失败；修复后严格JSONPointer，protocol-pointer-green.log通过|
|不完整维度响应暂停时丢掉其余合法得分和模型理由|protocol-pause-red.log复现；修复后保留逐维合法结果、Unknown缺项及模型方向，protocol-pause-green.log 20通过|
|planner reader-first修复未带原生来源验证上下文|已补完整候选的cast/volume/title原生验证；Q4report/planner-review-fixed-green-20261028.log|
|保存failed后、预留repair前崩溃，resume丢掉尚未消费的一次修复|已补保留raw失败重验与CAS预留；pending/Unknown仍禁止重发，同上日志69通过|

已知边界：scene运行编排、短篇Beat/completion、大纲物化/准入的v3 envelope、容量/来源证明消费者、UI状态映射和长书超容量批次适配按设计归主线；当前阻断，不是已完成集成。这些边界没有以手工构造的PromptBlock或较短普通长篇入口替代。
