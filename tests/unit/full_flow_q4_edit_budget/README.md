# Q4 第二批：共享自动正文预算，main GREEN 后冻结供提交

main 已固证 `Q4-consumer-budget-red-20261028T154514004012Z.json`：
预算目录 105 个 setup ERROR，均为缺少 new primitive 的显式断言；该次模型调用为 0。
经用户授权后已新增 `src/bestseller/services/chapter_automatic_revision_budget.py`，
并在本目录补充关系、事务和只写边界用例。
main 随后在 `resume-owned-components-20261029T012443636528Z.json` 保留两项失败，
修复后的 `resume-edit-budget-fixed-20261029T015744789689Z.json` 已证明 **111 PASS / 4.08s**；
源码测试前后 SHA 均为 `516e90cb951c1260670467b64224d9d9457164d58c6f959e6d3673536405456d`。
本次仅折行 main 指定的 10 处 E501，前后 AST 完全一致，最长行 100 字符；
**source/tests 现停止写入供 main 提交。本 lane 未执行 DB/pytest，未重跑测试。**
source 独立消费者失败不属于本 lane；原始 RED 报告没有修改。

依据：`reports/parallel-2026-10-28/Q4/design-for-mainline.md` 第二节；
现有 `budget.py::ExecutionLedger.claim`、`retry_ledger.py::project_execution_scope`、
`reviews.py::_validate_rewrite_task_parent/_recheck_rewrite_parent`、
`review_source_ordering.py::_prepare_supersession_locked`、
`rewrite_outline_dispatch.py::execute_outline_alignment_dispatch`。

## 最小 API

```python
class AutomaticRevisionBudgetError(ValueError):
    code: str
    differences: list[dict]  # 类型差异/缺少原生 operation 的结构化诊断

async def reserve_chapter_automatic_revision(
    *,
    session_factory,  # 只创建独立、干净 AsyncSession；无默认全局 DB
    settings,        # 显式 AppSettings；不从环境重新解析连接或预算
    project_id: UUID,
    chapter_id: UUID,
    source_draft_id: UUID,
    operation_key: str,  # 已 durable 的 ExecutionOperationModel.key
) -> dict: ...
```

只有一个扣额 API；不另造事件注册、权限审批、账本、模型或采用框架。
拒绝额外参数，包括 ORM 对象、正文/hash、workflow/resume 包装、`event_id`、
`approved`、`reset`、limit。输入 ID 仅定位 DB 行，不构成权限。

返回 JSON receipt 的必要字段：

|字段|意义|
|---|---|
|`schema`|`chapter-automatic-revision-budget-receipt.v1`|
|`mode`|`legacy` / `reserved` / `already_reserved`|
|`event_key`|新协议事件的固定派生键；legacy 为 None|
|`round_no`|本 operation 原有序号；legacy 为 None|
|`rounds_used` / `rounds_remaining`|从有效 history 长度计算；legacy 为 None|
|`operation_key`|原生 key；不能充当发送/复用凭证|

没有质量结论、promoted/current 状态、许可 flag 或自动接受结果。
`already_reserved` 只说明原额仍在；Unknown、pending、stopped 原操作的恢复和
是否发送仍由原生网关决定，不能因该 receipt 再发一次。

## DB 绑定与持久化

1. 显式 factory 开 fresh transaction；先拒绝已有 transaction/dirty/new/deleted/
   identity-map 的 session。不能借外层 flush 取得未提交来源，不能隐式 commit 外层。
2. 沿用全局顺序 **Control → Project → Chapter → Draft → budget**。
   实际 Chapter 行 `SELECT ... FOR UPDATE`，重新读 metadata，禁止拿调用方缓存替代。
   Control pause/stop 拒绝新预占；不创建或修改控制行。budget/op 只读，不拿 financial
   写锁、不改计数/Unknown/owner。缺少 Control 时沿用既有“未设置控制”合同。
3. Project/Chapter/source 必须存在并互相匹配。scope 使用持久 Project 的
   `project_execution_scope`，包含合法 creation scope；operation 必须存在且 scope
   匹配。历史 op.input_hash 必须与持久行一致。不得以 owner/lease 新值开新额度。
4. `reader_first_binding` 无标记且无新 journal 时返回 legacy，不写 metadata。
   有损坏/未知/冲突 marker 拒绝；已有 journal 之后移除 marker 也拒绝，不能退回旧合同。
   正式新项目标记由 main 的项目创建入口写入，本 primitive 不迁移旧项目。
5. 第一个自动修复事件以 Chapter DB 中最早的普通稿本为冻结初稿源
   （`version_no` 升序、ID 作为确定顺序），读取完整 UTF-8 正文求 hash。
   不要求其具有 grade/current/promoted，也不因这些状态变化重置预算。
   无稿/空稿拒绝。调用方 source 可为后续候选，不能把它当冻结源。
6. 首事件 key = SHA256(canonical JSON array
   `[schema, project_id, chapter_id, frozen_draft_id, frozen_body_sha256]`)，
   schema 为 `chapter-automatic-revision-budget.v1`；所有 UUID 为标准字符串；
   JSON 使用 UTF-8、`ensure_ascii=False,separators=(",", ":")`。
   后续重新读取原冻结 DB 行并核对，不用新正文/新 workflow/新 task 重新派生。
7. 在 `Chapter.metadata_json["chapter_automatic_revision_budget"]` 深复制追加：

   ```json
   {
     "schema": "chapter-automatic-revision-budget.v1",
     "project_id": "native UUID", "chapter_id": "native UUID",
     "event_key": "sha256",
     "frozen_source": {"draft_id": "native UUID", "body_sha256": "sha256"},
     "limit": 2,
     "history": [
       {"round_no": 1, "operation_key": "native key",
        "operation_scope_id": "native scope", "operation_input_sha256": "native hash",
        "source_draft_id": "native UUID", "source_body_sha256": "DB hash"}
     ]
   }
   ```

   提交成功才返回。相同 operation + 原来源/hash 重入不追加，即使两轮已满。
   同 key 换 source/hash/input_hash/scope 拒绝。不同 operation 共享两轮，第三次拒绝。
   不存“成功则退额”的状态，也没有释放 API；失败、Unknown、外层回滚均不返还。
   校验全部 history 的类型、序号、去重、上限、事件、来源和原生 op 绑定；损坏不能清空重建。
   当前 frozen DB 行或历史 source 原文字节被修改、缺失、跨项目都拒绝；历史不裁剪。
   只读检查 native budget 父行存在。已被同项目另一章节 journal 绑定的 operation
   不能再绑定本章；Project 锁使该检查与所有本 primitive 的追加串行。
8. metadata 整块 copy-on-write，保留其他 metadata、原稿、production blocker、
   质量状态及金融行。读回或 receipt 深复制不应向外泄漏可变 journal 引用。

建议错误码统一前缀 `automatic_revision_budget:`，后缀见测试；异常不得带正文/连接信息。
检查顺序：native 参数与 fresh session → Control/Project/Chapter → marker → journal
形状与内部自洽 → Draft 行锁/source 关系/frozen DB 原文 → budget/op 基础绑定 →
requested op 重入 CAS → 其余 history DB 绑定 → 新 operation pending → cap →
跨章节 operation 复用检查 → 追加/提交。请求的已记录 operation 换来源报
`operation_source_changed`，持久 input_hash 换值报 `operation_binding_changed`；
其余历史绑定损坏统一 `history_invalid`。内部 journal 的 frozen 声明自相矛盾为
`history_invalid`，合法声明对应 DB 原文缺失/变更为 `frozen_source_changed`；
非 frozen 的历史 DB 原文字节变更报 `history_source_changed`。
新 source 为空报 `source_empty`，缺少 native budget 父行报 `operation_budget_missing`。
DB/commit 失败只返回 `storage_unavailable` 异常，不暴露原始诊断，不返回预占 receipt。
history 整数守卫按精确 `type(...) is int` 校验，bool 不可混作序号/limit；
异常 `.differences` 给出字段、expected/actual 类型与安全的整数/布尔值。
缺少 operation 则给出原生 row 预期存在、实际 None 的 typediff。
对应测试经 `request.node.user_properties` 将 code/differences 留入 main 的 JUnit 回执。
fixture 参数已区分 native key 与 keyword override；注入损坏 JSON 时用 `flag_modified`
确保 `True == 1` 不会吞掉测试数据写入，并以 JSON 编码相等确认持久化后的真实类型。
factory 若直接返还一个带状态的 AsyncSession，会在进入 context 前拒绝，不关闭、
不回滚调用方 session；factory 自己包装的 context 由 factory 自己管理其退出。

## main 必须接线的事务接缝

原生 operation 的 claim 在 `llm.py::complete_text` 内发生，目前这些正文入口无法
在调用之前取得 durable key。**这是接线 Unknown，不能靠伪造 key/operation 行绕过。**
本目录 operation 行只用于普通 SQLite 扣额测试，不是 provider 许可。

最小接入点应在 native claim 已 durable、provider reserve/send 之前：
main 负责从既有 native ID/source CAS 接缝调用 primitive；既有 rewrite 来源、任务、
大纲、current-body CAS 仍先验并在候选提交时重验。receipt 本身不能证明这些权限。
不能在已生成候选后补扣额，也不能在新正文 attempt 中借旧 key 免费生成。
当前 `ExecutionOperationModel` 不存 source draft/chapter ID：本 primitive 从真实 DB
验证 source→chapter→project、op→project creation scope→budget，并校验每条已记录
source/hash/op input 关联；**未记录 key 最初对应哪一个请求 source，仍由 main 的
native claim/request CAS 接线证明**。不能把仅 scope 相同宣传为完整发送来源证明，
也不能据此采用未知/陈旧正文。本 primitive 不新增伪造的 op provenance 或授权记录。

跨 native commit 的调用顺序：

1. main 保存原任务、来源 CAS、必要 workflow step/checkpoint，显式提交并释放原
   Control/Project/Chapter/Draft 锁。外层未提交或 nested transaction 时拒绝接入；
   不可边持锁边等 fresh session，以免自阻塞。
2. 既有 native claim durable 后，fresh factory 按上述锁序重查并预占；提交并释放。
   若源行尚未提交，primitive 拒绝；不采用传入 body/ORM 快照补洞。
3. main 在原生发送边界重验取消/控制、source CAS 和 operation 原 owner/Unknown/
   回执状态。发生取消、冲突或 not_sent，已占额仍保留。后续付款账本保持原有合同。
4. outer session 再用 fresh DB 读回或显式 refresh 后合并 Chapter metadata；禁止
   用旧 Chapter metadata 整块覆盖掉独立提交的 journal。候选落库仍执行原 source CAS。

事实、商业、补字、去 AI 味、内部二编都走这一接缝，各候选一次；审校原回执恢复不走。
本阶段一个候选只支持一次原生生成操作；分片与多 physical calls 的预声明/总调用预算
接缝仍为 Unknown，必须拒绝未接好的多操作复用一轮，不实现切片框架。

## 范围与 Unknown

- 最小版本只覆盖 reader-first 新章节的首自动修复事件；自动 task/来源 supersession
  属于同事件。**人类明确另开事件没有可验证的原生授权记录接缝，尚不支持**；不能
  用 approval flag、用户文本、另一个 task/workflow ID 直接清账。main 确认 native
  授权契约后另补 RED，当前 cap 继续生效。
- SQLite 用例只用 `is_current=False,promotion_state=candidate` 的普通稿本，不制造
  graded/promoted/current 权限，不调用 models/reviews/deslop/drafts/pipelines。
- SQL 构造测试检查请求了真实 Chapter 行锁与先后次序；SQLite 不执行 PG 行锁。
  并发第三轮、与暂停竞争、外层 metadata 覆盖及真实 native send 前接线需要 main
  在其既有隔离 PG/原生入口执行 RED/GREEN，不能由这里的用例冒充完成。
- 不提供 journal 防恶意数据库管理员擦除的机制；现有所有 Chapter metadata 写者
  必须保留 journal。删除整个 journal 的外部写者属于 main 接线审计范围。

## mainselection GREEN 入口

main 在**现有获准的隔离测试环境**先核验测试库配置与库名，再运行：

```text
.venv/bin/pytest --no-cov -q tests/unit/full_flow_q4_edit_budget/test_shared_budget.py::test_public_api_accepts_native_ids_factory_settings_only
.venv/bin/pytest --no-cov -q tests/unit/full_flow_q4_edit_budget
```

不要新建 Docker/隔离环境，也不要设置生产连接串。原 `api` fixture 的缺模块断言已
作为真实 RED 保存；现在 main 用同入口检查实现，新增用例一起验收。若先发生依赖/
配置/fixture 错误，应记录并区分 setup 问题。此 lane 只做文件/语法静态检查；
没有执行上述命令、创建 SQLite 文件或接线实际发送。

## 只读接缝分析：main 的最小接线建议

截至本次读回，primitive 在服务目录中只有定义，尚无调用者；111 PASS 证明的是
primitive/fixture，不能宣称 native send 已受共享两轮限制。以下是交 main 的接线方案，
本 lane 没有修改 llm、reviews、pipelines，也不新增业务模块。

1. 复用现有 `LLMCompletionRequest` / `_ExecutionCall`：main 从原生项目/章节/稿本/
   task 校验接缝取得 IDs，保存已校验的请求来源关联。不可让调用方的 approved、
   event ID 或一条未核验 metadata 决定是否扣额；没有来源 ID 的正文修改应拒绝，
   不可因为 metadata 缺失就按普通调用放行。不同修订候选必须有不同 native operation；
   同一次原生操作的重入保持原 key，不因 workflow、恢复或包装换 key。
2. 推荐统一扣额点是 `llm.py::_paid_transport_attempt_impl` 的容量/原生上下文检查后、
   `call.ledger.reserve(...)` 前。此处已有 durable `call.claim.key` 与明确 settings；
   factory 从同一 `call.ledger.engine` 创建全新 AsyncSession，调用当前 primitive。
   `complete_text` 的 cached-result 分支在到达该点前已经返回，因此不会补扣历史结果。
   原生 retry/fallback 每次走该点，同 key 的预算预占保持幂等；财务与 Unknown fencing
   继续交既有 ledger，不能因 `already_reserved` 再批准发送。
3. 原 caller checkpoint 必须先提交并释放锁。现有
   `_release_session_before_external_llm_call` 在 claim 前已提交，但 main 仍需确认
   实际修订来源/候选行已包含在该 checkpoint，且不是 managed/nested transaction。
   fresh 预占提交后，发送前再次跑原生 cancellation/owner/source CAS；失败也不退额。
   HTTPX `_guard_provider_http_send` 只检查当前 body-edit call 已绑定预占与同一 native key，
   不把 caller flag 当许可，也不在已付款之后重新创建一个扣额路径。
4. 接线按“是否返回新正文”判定，不能按 logical_role 或泛化 template 子串判断。
   例如 `content_validation` 使用 critic 却返回清理后的完整正文；
   `reader_chapter_issue_acceptance` 使用 editor 却是审校，不应扣正文轮数。
   已知正文入口与例外矩阵如下；后续新增未分类路径必须先 RED，不能默认免费放行。

|已读原生入口|main 接线要求|
|---|---|
|`reviews.py::rewrite_chapter_from_task` 的 `chapter_rewrite`、事实范围分支、商业任务|同一 Chapter 冻结事件，共享两轮|
|`chapter_rewrite_repair`、`chapter_rewrite_quality_retrofit_repair`|内部二编/语义修复也是新候选，每次扣额，不在初次外层只扣一次|
|`chapter_localized_opening_repair`、`chapter_localized_ending_repair`|局部改写同样扣额，不能因为范围小绕过|
|`deslop_revise.py::_revise_prose_deslop_inner`|正常去 AI 与补字分支都扣；其 template 是 `scene_writer`、version 为 `deslop_revise`，不能只匹配 deslop 名称|
|`pipelines.py` 与 `fanqie_short_pipeline.py` 调用的 `revise_prose_deslop`|必须把相同原生 Chapter/source 关联传入公共去 AI 入口，短篇不能漏掉|
|`chapter_revision.py` 的 `reader_chapter_polish`、`reader_chapter_issue_edit`|消费同一章 journal；source/meta-leak 后续编辑也不能另开事件|
|`reader_issue_segment_writer`、`reader_issue_segment_compress`|是正文；当前没有分片预声明合同，不能多次调用只扣一轮，最多仍受共享两轮约束|
|`reader_issue_segment_plan`、`reader_chapter_issue_acceptance`|分别是规划/审校，排除正文轮数；保留原生调用预算|
|`drafts.py::validate_and_clean_novel_content` 与 `reviews.py::_validate_and_clean_chapter_rewrite_content`|`content_validation` 返回正文，所有调用者都须保留原生 source 关联|
|`reviews.py::rewrite_scene_from_task`、`drafts.py::_regenerate_scene_until_valid` 的 `scene_writer_regen`|冻结初稿后的场景修订/回炉应从真实 SceneCard 解析 Chapter 并共享该章预算；不能保留场景独立免费池|
|初次 writer、只读审校/原回执恢复、提纲/Canon/知识 JSON 修复|分别按原生初稿与非正文合同处理，不据 role 名称误扣正文轮数|

**仅接 physical send 仍有两个漏口，不能以 111 PASS 宣称已覆盖：**

- `chapter_revision.py` 的 `humanize` 直接返回 `run_ai_flavor_gate(...).patched_text`；
  `reviews.py` 的 `candidate_trim/incumbent_trim` 会产生本地新候选，均可能没有 physical send。
  当前 API 要求真实已 durable 的 native operation，不能为这些路径伪造 LLM operation。
  reader-first 最小接法先在这些新候选入口 fail closed、保留原文/阻断和方向选择；
  若 main 要支持本地候选，再先交独立 RED 明确同一原生预算身份，不能悄悄免费采用。
- 内部失败候选和首次 ChapterDraft 入库前的场景回炉，可能只有内存正文/SceneDraft ID。
  API 不接受这些对象假充 ChapterDraft。main 需要先 checkpoint 普通、未采用的候选行并
  重查来源，或按尚未接通处理；不得伪造 current/promoted/grade 来凑 source。

main 最小入口 RED 应覆盖：五类跨分支第三候选发送前拒绝、critic 清理扣额/editor 审校
不扣额、内部二编和短篇去 AI 均扣、source 缺失拒绝、原 op 重入不重扣、Unknown 不重发/
不退额、缓存恢复不新增历史、本地 humanize/trim 不免费采用、旧项目无 journal 零写。
PG 并发与真实 native 发送接线的 GREEN 仍由 main 统一执行；本分析不扩展当前 API。
