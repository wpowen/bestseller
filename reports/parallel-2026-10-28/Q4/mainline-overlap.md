# int/full-flow 期间新增改动：函数重叠说明

实现前int/full-flow固定为9bfde9a52cd70a147cbe9ead04a0763e01a7bb85，Q4七个允许服务路径日志/diff均空。收尾复查已前进至a84258c29d1a0e2eb58266d8c412b6cfac97e3f3，新增e8ccb558（bounded whole-book repair and native length/title contracts），drafts.py发生变化。检查快照：final-path-check.json、mainline-overlap-during-implementation.json。

|函数|主线改动|Q4处理|
|---|---|---|
|assemble_chapter_draft|原生整章篇幅/标题合同|Q4未改，整函数保留主线|
|generate_chapter_draft_once|原生整章篇幅/标题调用编排|Q4未改，整函数保留主线|
|build_chapter_first_draft_prompts|新增length_request参数；require_feasible_request/prompt_contract；使用冻结window并将长度回执放入protected word-count rules|**唯一共同修改函数**：Q4加入v3 source校验、reader binding传递和角色驱动因果呈现。不得整函数覆盖任一侧|

合入时保留主线length_request签名、可行性校验、context_packet只读复制、window分支和长度回执注入；同时保留Q4起始reader_first_writer_blocks校验、reader_first绑定向两种renderer传递、按字段角色呈现的因果说明、v3 promptblock源锚定。两者分别约束篇幅和叙事任务，没有授权任何一方削弱另一方。若patch上下文冲突，按上述局部块整合；不要为解决冲突删掉length_request或回退到旧固定篇幅band。

其余Q4函数与当前主线无符号交集；workflows仍仅大纲修复，drafts仍仅四个writer提示函数。Q4未合并、rebase、拣取、推送或改写int/full-flow。该文件是合入指引，不冒称已完成集成或已在主线运行测试。

主线合入后必须同时验证：length_request有/无、四维绑定有/无、中英文、legacy/compiled两种提示模式；不够的篇幅能力在发送前拒绝，旧批准不能升级v3，两个约束的回执均不得被压缩丢失。Q4离线测试仅证明refs/tags/e2e17基线上的本分支行为。
