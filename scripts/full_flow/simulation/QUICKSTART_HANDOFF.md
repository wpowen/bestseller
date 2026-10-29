# normal quickstart builders：交接

P3 最终参考 `4fb4cc11`：53 模板 catalogue/compiler 从 complete-plan 出发，没有 normal quickstart 构思入口。用户交接的 example 为 7 模板／25 send、另缺 29 个回复；这里不把 catalogue 成员数当作实际覆盖率，也不运行该 example、P3 run.py 或真实应用。

## 已有精确 API

`quickstart_builders.py`：

- `BuilderContext(story, chapter_number=1, call_index=1, current_body=None, source_body_sha256=None, wire_body=None, expected_wire_sha256=None, protocol_subject=None, submissions={})`：纯运行时材料；代码中无书籍 UUID、正文文件或原 prompt。
- `builder_inventory()`：分别列出实际 producer builders、必须有明确物理回复的协议、有限动态章纲族。
- `blueprint_requirements()`：私有 story blueprint 所需字段及角色、章节、动作原子结构，不含故事正文。
- `build_payload(template, context)`：按具体模板生成阶段 payload；格式为 `json/text/asset:*` 或明确的 function 工具协议。
- `build_response(template, context, *, catalog, wire_body, usage, limits=...)`：编码为 P3 HTTPX transport response spec；function 参数使用当次 wire schema 校验。
- `require_builder(template)`：在 context resolver 之前拒绝未知模板，固定错误码 `SCRIPT_TEMPLATE_UNCOVERED`。

自动 producer 范围为构思架构、商业定位、market/character/world、定稿和部分聚焦修复、title 候选／修订、book/world/cast/volume 基础规划、正文动作原子组合。每个 `_repair` 是明确注册的独立名字，不提供无限重试槽。注册清单和输入结构见 `quickstart_builder_manifest.json`。

章纲仅展开 `planner_volume_1_chapter_outline[_batch_A_B]`，要求 `1<=A<=B<=chapter_count<=32`，并保留 runtime blueprint 内非空 causal/methodology contract 和 scenes。其他卷、source-bound 扩展和修复族没有通配 fallback。Cast 分部仅支持下面两个明确协议。正文按 runtime `prose_atoms` 的 action/reaction/result 组合一次；不重复、不补字到门限。也可由主线程提供当前私有正文。

## normal 入口的 SDK transport 接线

`runtime_adapter.py::create_runtime_tape(*, catalog, context_resolver, usage_resolver, allowed_endpoints, audit_path=None, limits=...)` 返回原 P3 `Tape` 的子类，不安装 transport、不启动服务。

回调签名：

- `context_resolver(observation, wire_body) -> BuilderContext`
- `usage_resolver(observation, wire_body) -> {prompt_tokens, completion_tokens}`，明确 synthetic 计数。

主线程在 normal quickstart web 服务及实际执行 worker 的进程内调用原 `install_scripted_http(tape)`。本目录不写 HTTP driver，不启动 P3 run.py，也不改 WebTaskManager／业务工作流。

每次发送继续使用原 `NativeTemplateObserver`；拿到真实模板、物理序号和真实 UUID 后，才生成单个有界条目，并调用 P3 原 `_choose` 匹配与审计。project/workflow 都不存在时，使用实际 wire 的 body SHA，绝不临时创造 UUID。既有 request hook、发送计数、账本、paid validators、review/adoption 都保持原调用路径。

Tape 文档在内存中逐个扩展。审计的 `script_builder_prefix` 记录每次有限文档前缀 revision 和 SHA；它不是事先冻结的整本静态 Tape SHA。最终 document 的各前缀可按 entry 顺序重建核对；完整响应文档只有主线程显式调用 `write_private_document` 时才会写入私有目录。audit 不包含 wire messages 或故事正文。

## 审查与知识回复

`submissions` 的 key 必须是 `template:physical_index`；主商窗审、意图审、规划准入、整书语义、标题判定和知识提取均需要 caller 明确给出 proposal。schema 中有 `pass`、枚举中有来源 ID，不构成自动填 pass 或挑一个来源的理由。缺少时 `SCRIPT_SOURCE_BOUND_REPLY_REQUIRED`。

当前正文相关的主审、商审、连续性与知识提取还要求 current_body 的精确 SHA 匹配 `source_body_sha256`；这只是 stale-body 防错，不授采用、付款或正式 grade 权限。知识 payload 由明确 extraction 提供，不从空数组制造闭合；全部原生来源／语义检查仍会独立运行。

## Native Cast 分部：仅私有 authored reply

新增确切名字 `planner_cast_person_part`、`planner_cast_links_part`，均为 JSON 文本，不是 `submit_cast_spec` 工具调用。入口仍为 `build_payload` / `build_response`；专项校验为 `cast_part_submission(template, context)`。它们是显式回复协议，不是按 schema 生成数据的 producer。加入下一节语义协议后、Q4 接入前，注册为 31 producer + 26 显式回复 + 43 keyflow = 100 个 literal 名字；当前 Q4 增量见文末。

调用方必须给 `BuilderContext.wire_body`、精确 `expected_wire_sha256` 和 `submissions[template + ":" + global_ordinal]`，或让已有 `PrivateConfigResolver` 从对应的有限 `replies[].material` 槽提供。`story` 可以为空；这两个协议不从常规 actor blueprint 自动补字段。

- Person material 是完整的 `{"person": <explicit authored CharacterInput>}`。姓名须与当前 native `current_part.required_name/source_person.name` 精确一致，身份只读自该次 wire。使用 `source_cast_parts.person_schema` 与 `validate_part`，不经 Pydantic 默认填值、年龄强转或代词推断。
- Links material 明确提供 `antagonist_forces`、`conflict_map` 和 `layout.{protagonist,antagonist,supporting_cast}`。`null`、空数组和人物角色安排都由私有作者明确选择；layout 必须把本次 `already_generated_people` 的 ID 完整选中且各一次。按原生 `links_schema` / `validate_part` 校验后，只返回原 authored links，不返回组装后整份 cast，也不改已有 person。原生调用方继续执行 `assemble`；离线正向用例也直接用该 API 检查返回值。
- 未压缩 wire 可用既有 `$wire` + `decode_json` + `/already_generated_people/<index>/part_id` 显式绑定。对于压缩 wire，主线程可用原生 `cast_links_transport.decode` 读取 ID 后明确编排 material；builder 复用该 decoder 支持原生 lossless v1/v2/v3，并将 retry suffix 一并保留在精确 wire hash 绑定内。没有 schema enum 自动选人功能。
- 旧 catalogue 缺这两个名字时，仅本次局部 catalogue 增补确切 native 名字及源码位置；已有 catalogue 不变。未知 Cast 模板或 `_repair` 名字仍为 `SCRIPT_TEMPLATE_UNCOVERED`。

缺材料／错物理槽为 `SCRIPT_SOURCE_BOUND_REPLY_REQUIRED`；缺 wire／hash 漂移为 `SCRIPT_SOURCE_WIRE_MATCH_REQUIRED`；其他定点错误为 `SCRIPT_CAST_PART_{WIRE_INVALID,CONTRACT_MISMATCH,IDENTITY_MISMATCH,SCHEMA_MISMATCH,LAYOUT_MISMATCH}`。原生采用、语义审查、付款和写库权限不从这些校验产生。

## Source expansion 语义回复：两个明确版本

确切模板为 `planner_source_bound_expansion_semantic_review`；它仍是 JSON 文本，不是 function tool。`BuilderContext.protocol_subject` 必须明确给 `{"prompt_version":"4.2-stable-catalog"}` 或 `{"prompt_version":"4.3-field-presence"}`，并与当次 candidate catalogue 的字段观察协议一致。这里不向 SDK body 添加 prompt_version，也不猜测未来版本。

新增纯 API（都在既有 `quickstart_builders.py`）：

- `decode_source_review_wire(wire_body, *, expected_wire_sha256)`：先核验完整 physical wire（包括 retry suffix），返回私有 `data`、`hashes`、`identity_review_context`、`encoding`、`wire_sha256`。解码 literal 分节、`source_review_data=` 的 native lossless v1/v2/v3、catalog v1/v2，以及 string-pool 包裹的原生 lossless 容器；调用原生 decoder 校验完整性，不把未知容器当普通 JSON 或自动扩张 authored `$ref`。该返回值含源材料，调用方只能在私有运行上下文使用，不应写入公共清单。
- `source_review_submission(context)`：既有 `build_payload/build_response` 的专用分派。要求精确物理槽 `submissions[template + ":" + global_ordinal]` 和当前 wire hash。main 可先通过上面的 decoder 读取 hash 和 catalogue，再结合私有作者明确选定的证据与判断提供完整 material；decoder 不挑选 quote ID 或生成判断。

material 必须明确包含 `decision`、`core_preserved`、`core_conflicts`、`quality_findings`、`authority_snapshot_hash`、`candidate_hash`、`source_evidence`、`candidate_evidence`、`feedback`。保留作者的 revise/uncertain/false/null；不补空数组、不默认 pass。每个 changed field 两侧恰好各一条证据，且条目仅为 `field/quote_id/source_relation`；判断、finding 和 source_relation 禁止从 `$wire` 等绑定选择。feedback 最长 200 字符，quality_findings 最多 3 条且各不超过 120 字符，均来自原生任务约定。

hash 必须与原生头精确一致；candidate/baseline hash 还须与解码后的相应内容一致。identity-only baseline 的 authority_snapshot 是窄投影，不能代替完整原生 authority hash。出现 identity_review_context 时额外要求显式 boolean `protagonist_identity_supported`，False 不会被改成 True。

quote ID 必须存在于当次 catalogue；候选 ID 的字段归属须与 candidate_quote_field_bindings 一致。literal quote 仅做原生风格的字面／布局核对；4.3 的 sp_/cp_ 观察使用原生 `compile_field_presence_options` 重证字段、side、presence/type/value hash，区分缺失、null、空值。回复不附加 quote 文本或观察状态字段，不把结构观察变成故事许可。

未知版本、版本与观察类型不符，以及 wire/hash/evidence/material 错误分别为 `SCRIPT_SOURCE_REVIEW_{VERSION_UNCOVERED,VERSION_MISMATCH,WIRE_INVALID,HASH_MISMATCH,EVIDENCE_MISMATCH,MATERIAL_INVALID}`。未知后缀、证据修复模板、其他规划修复仍是 `SCRIPT_TEMPLATE_UNCOVERED`。旧 catalogue 只局部补入这一个确切名字，业务源、原生 guards、paid/review/adoption 和 checkpoint 不变。

## 新增 keyflow 与持久化 API

`keyflow_protocols.py::protocol_inventory/protocol_for/authored_payload` 新增 43 个明确 JSON 协议名字：11 个 tournament 模板、16 个 fidelity/packaging/seed-repair 基础协议及其 16 个原生 `_llm_call_json` repair 名字。动态 packaging 名字仅允许三个原生 base 的 `_candidate_v2_<20hex>[_repair]`；用当前 wire 上 title/synopsis 的原生摘要算法核对，不接受旧包装结论。轴、分数、passed、claims、修复内容全部由私有材料明确给出，缺字段不补 pass。

normal 路径应使用 `runtime_adapter.py::create_checkpointed_runtime_tape`，而不是无 checkpoint 的独立 matcher 接口。参数增加 `checkpoint=PhysicalOrdinalStore(...)`；context resolver 签名为 `(observation, wire_body, ticket)`。

`checkpoint.py::PhysicalOrdinalStore(path, *, private_root, repository_root, run_id, config_sha256, max_sends=4096)` 的 API：

- `initialize()`：显式创建或验证，已有文件绝不重置；无隐式初始化。
- `claim_send(template=..., native_operation_key=..., native_input_hash=..., native_attempt_id=..., wire_sha256=..., project_id=None, workflow_run_id=None)`：原 normal SDK 身份必须齐全；跨进程锁、原子 replace 和 fsync 保留全局序号；重复 attempt 拒绝。
- `record_reply(ordinal, response_sha256=...)`：只记录生成的响应 hash。
- `record_transport(ordinal, outcome=...)`：仅 `wire_complete/transport_fault/builder_blocked`；不表示 native settlement、付款或采用。SSE 仅在迭代器实际耗尽后记 wire_complete。
- `inspect()`：返回全部 metadata 与 unresolved ordinals；SIGKILL 后保留 reserved/reply_authored，不自动改为完成。

dispatcher 继续调用 P3 `NativeTemplateObserver` 取得真实模板和 UUID，但 fixture 的 `call_index` 使用共享文件的 global ordinal；P3 进程内 ordinal 不用作持久脚本槽。`ticket.template_ordinal` 也是从共享历史计数，私有配置可用它选择一个有限模板槽。原生 `_transport_send_count`、operation/input/attempt 和账本保持原样。

`private_config.py::PrivateConfigResolver.from_file(path, *, private_root, repository_root)` 只在显式调用时读取 0600 私有配置；返回对象可直接作为 context resolver。配置 schema 为 `normal-quickstart-private.v1`，含 `story` 和有限 `replies`。每个 reply 必须给明确 template、global_ordinal 或 template_ordinal，另给完整 wire_sha256 或明确 wire_assertions。没有 template-only 重复默认回复。

`wire_material.py::bind_authored_material(authored, wire_body, expected_wire_sha256=...)` 支持显式 `$wire` JSON Pointer、可选 decode_json/pointer、连续原文 quote、对象 key_index 和 `$object` 映射。来源 ID／引文不能直接伪造；passed/pass/verdict、scores、result 等判断不能从 wire/schema enum 自动选出，必须在私有配置中写明。没有从 schema 构造通过结论的 API。

## 仍待主线程提供／未覆盖

- tournament、seed fidelity、packaging、两个 Cast 分部及上述两版 source expansion 语义协议已实现；实际候选、轴分数、明确 verdict、引用、人物、links 和修复 payload 仍由私有 blueprint/replies 提供，不宣称已生成所有实际回复。证据修复、其他 identity/domain 修复及尚未注册的模板见 `quickstart_registry_status.json` 的逐名字清单。
- 非第一卷、英文 profile、未注册的 optional enhancer／repair；均报明确错误。
- 当前 33 个显式绑定协议提供 codec 与必要字段契约；实际审查判断、引用、提取、人物和 links 内容必须由私有 runtime 材料提供，不能据 registry 数字宣称这些回复已经补齐。
- normal quickstart HTTP driver、web/worker 进程安装、真实端到端采用／知识／导出闭合由主线程负责；本轮未运行应用、测试、Docker、HTTP 或模型。

主线程已报告 Cast GREEN `cast-parts-builders-green-20261028T145611043696Z.json`（51 PASS + 27 subtests），语义 RED 为 `source-expansion-semantic-script-red-20261028T151818354666Z.json`（69 FAIL + 10 PASS，含 subtests）。语义实现 GREEN 尚无此侧线程实际回执；sidecar 不运行测试或应用。静态 Ruff 与 AST 检查不代表运行时接线已通过。

## Q4：17 个确切模板合同，main GREEN 后冻结

main 实际 RED：`simulation-q4-red-20261029T024305463988Z`，exit 1；随后 main 已报告 `simulation-q4-candidate-20261029T032237065255Z.json` exit 0/status 通过。原 `test_q4_reader_first.py` 的 24 个测试方法和全部负例保持原样。该批完成时 literal registry 为 **35 producer + 28 显式回复 + 54 keyflow = 117**；静态 inventory 的 242 项是旧源码目录快照，不表示运行覆盖。

`reader_first_protocols.py` 仅列出闭合映射：`TOURNAMENT_ALIASES` 11 项、`PRODUCER_ALIASES` 4 项（finalize/spine 各含一次 JSON repair）、`REVIEW_PROTOCOLS` 2 项。没有后缀通配推断。具体名字同步到 `quickstart_builder_manifest.json` 和 `quickstart_registry_status.json`。

入口仍为 `quickstart_builders.build_payload(template, context)` / `build_response(...)`。专项纯合同 API：

- `validate_instruction(template, context)`：检查实际 wire SHA、正确角色/stage 的完整 native 指令和 binding；返回核对过的 binding。
- `decode_review_wire(template, context)`：严格解析唯一 user JSON，返回完整 source 文档；校验 source SHA、当前 config SHA 和原始输入绑定。
- `review_submission(template, context)`：返回明确作者提供的原始 review payload。四维 score/reason、issues、options 不允许 `$wire` 或 schema 选值；每条 evidence 的 JSON Pointer/逐字 quote 由原生纯 parser 核验。合法低分原样返回；Unknown 拒绝，不返回 parser receipt 或写入采用权限。

所有新模板要求 `wire_body`、`expected_wire_sha256`、`protocol_subject.reader_first_planning`（包含原生 `protocol/config_sha256/user_input_sha256`）。11 个 tournament 使用新模板自己的 `submissions[actual_template + ":" + global_ordinal]`，复用既有 base 字段合同；不借用 base/上一序号回复。4 个 producer 使用已有私有 blueprint 转换，并额外校验新 marker。

两类 reader review 还要求独立作者回复槽和明确 `protocol_subject.prompt_version`：

| template | checkpoint | prompt_version |
| --- | --- | --- |
| `conception_reader_first_skeleton_v1` | `skeleton` | `reader-first-review.v1` |
| `planner_reader_first_outline_review` | `outline` | `rf-outline-review-v1` |

skeleton 保留完整 candidate 和原始输入全文，重新计算原始输入 SHA；outline 保留完整 book/world/cast/volume/outline/premise，核对 `original_input_binding`。当前 source 的整体 SHA 必须匹配当次 wire，旧 source 的判断不能复用。有限 hash/quote 引用可用显式 `$wire` 选择，实际判断仍由私有作者材料提供。

新旧 P3 catalogue 均通过局部副本补充这些已注册名字；实际模板、原始 project/workflow UUID、wire SHA 和持久化 global ordinal 继续用于匹配。checkpoint 与 HTTP transport 安装流程没有改变。

该批未覆盖的两种业务 repair 和短篇 annotation 由下面三个精确 codec 补充；原 17 模板函数没有修改。未注册名字仍报 `SCRIPT_TEMPLATE_UNCOVERED`。正文、原始剧本和运行数据由 main 放在私有路径。

## 三个 authored annotation/repair codec：main GREEN 后冻结

main 实际 RED `remaining-handoffs-red-20261029T034003051485Z`：本侧 137 FAIL（119 assertion + 18 ScriptContractError），根因为模板未注册。`test_q4_authored_repairs.py` 的 23 个测试方法及全部正负例保持原样；本侧仅静态检查。

新增 `q4_authored_repairs.py`，通过现有 `build_payload` / `build_response` / runtime dispatcher 进入；该批 registry 为 **35 producer + 31 显式回复 + 54 keyflow = 120**。main 随后报告 authored3 全套 GREEN，包含修正为外部私有 tmp 目录后的六个 restart 子例。只有下面三个 literal 名字：

| template | prompt_version | 专项 API |
| --- | --- | --- |
| `fanqie_reader_first_field_roles` | `fanqie-rf-field-roles-v1` | `annotation_submission(context)` |
| `conception_reader_first_repair_v1` | `reader-first-repair.v1` | `conception_repair_submission(context)` |
| `planner_reader_first_full_outline_commercial_surgical_repair` | `rf-outline-repair-v1` | `outline_repair_submission(context)` |

统一专项入口为 `authored_submission(template, context)`。每次必须给实际 `wire_body`、精确 `expected_wire_sha256`、`protocol_subject.reader_first_planning`、明确 `protocol_subject.prompt_version`，以及 `submissions[actual_template + ":" + global_ordinal]`。章纲修复另要求 `protocol_subject.source_sha256`，绑定当次完整来源的 native metadata SHA；不会从缺失值补造。

annotation 重新计算完整 outline 的 SHA 和 native leaf catalogue，包括空/null/布尔/数值，排除每层 `_meta`。每章必须恰好一次、每个 leaf 必须有作者明确角色；角色不得从 wire/schema 自动选择。原生 annotation_attempt 仅 0/1；rejected_response 只作数据，不可替代私有回复槽。输出仍是原始 roles JSON，不给 outline 写入 metadata。

conception repair 核对当前 failed review、原始输入/config/source SHA、frozen_controls、完整 candidate 字段/类型/嵌套记录和固定选择；candidate 必须是私有明确材料且有实际变化。不会复制 wire candidate 当作修复或提供质量分数。

章纲修复的 `decode_outline_repair_wire(context)` 只接受两种明确 wire：普通规划完整来源文本尾段；短篇 `fanqie-reader-first-outline-repair.v1` JSON envelope。均核对完整 source SHA 和原生 patch protocol。patch 必须明确、有界、非空且产生改变；拒绝别名、新字段、非法 target、metadata、缺失/新增 leaf、continuity_reference 改动。原生纯 patch 函数只在脱离持久化的内存副本上用于核验；返回仍是 authored patches，不返回修复后的 artifact 或采用回执。

checkpoint/transport 代码没有修改；正负例保留 pending/transport_fault 后重建 dispatcher、原 attempt 拒重和新 global ordinal 缺少私有材料时阻断的检查。fixture state 不授 native retry/paid/adoption 权限。

短篇 `planner_reader_first_outline_review` 的 `selected_source/original_beat/base_outline/outline` 完整 source variant 已单独 RED/实现，仍使用明确 `rf-outline-review-v1`。`reader_revision_anchors` / `reader_revision_preservation` 两个 literal 模板亦已实现，角色 `critic`、版本 `1.0`。入口为 `reader_revision_protocols.decode_revision_wire` / `authored_submission`，完整 current source/candidate/body hash、UUID、anchors 和 native binding 均须匹配；私有 false 判断原样保留。

main 报告 `remaining-wiring-red-green-20261029T044321186665Z` 中两个新 codec 测试模块 GREEN，combined 198 PASS + 171 subtests；该组合仍有其他模块 RED，不将整份回执标为通过。当前 registry 精确为 **35 producer + 33 显式回复 + 54 keyflow = 122**。本轮不改这些已验证的实现或旧测试。

## L1 私有 authoring：只生成文件，尚非完整可运行覆盖

新增 `author_l1.py`，使用既有 `PrivateConfigResolver` 和 normal `web_entry.py`，没有另一套 transport/HTTP/pipeline。纯 API：

- `authoring_contract()`：字段要求和已定位的必经／条件模板，绝不返回默认判断。
- `compile_private_bundle(document)`：编译明确作者材料，保留低分和 false，逐项检查有限模板槽；返回 config、quickstart request、四段私有正文、执行计划和结构缺口。
- `read_private_authoring(path, *, repository_root)`：只接受 checkout 外、无 symlink、0700 父目录内的 0600 JSON；拒绝重复 key 和非有限数值。
- `write_private_bundle(bundle, destination, *, repository_root)`：只新建外部 0700 目录和 0600 文件，拒绝覆盖。已有 checkpoint 不读取、不重置。源文件 SHA、配置 SHA 和产物 SHA 留在该私有目录。

`l1_authoring.schema.json` 是私有输入结构；不从业务 JSON schema 造分数或填剧情。顶层必须明确给 `schema=normal-l1-private-authoring.v1`、`profile=ordinary-urban-family-four-part.reader-first.v1`、`original_input`、`quickstart`、`story`、`execution_plan`、`replies`。

`original_input` 是 main 保存的实际普通都市现实家庭情感输入；与 `quickstart.concept_seed` 逐字一致，不能靠截断获得匹配。4-part 当前正常入口需要 `creation_mode=fanqie_short`、`length_key=fanqie-short-8k`，不能只传 `chapter_count=4` 而保留默认六段 length key。另明确 POV、题材 selection 或 genre_key、实际 llm_model_id、production_profile（complete/research）、`stop_after_conception=false`、`draft_mode=false`。脚本仅计算原生 `quickstart-client.v2` 的种子长度/SHA manifest；不生成原生 grant、approval、grade、current、promoted 或既有 project identity。main 原生取得的 `execution_budget_grant_id` 只按原值保留。

`story` 必须给 manifest 中全部 31 个 story 字段、三名角色、四个有序章节以及每章的因果／方法合同、场景和 action/reaction/result 原子。原子可为私有字符串，或私有字符串与 `{story_pointer: "/protagonist/name"}` 等明确叶子引用的有序数组；编译器按原次序连接，再调用既有 `render_chapter` 一次。没有重复、补字或质量门槛回填。正文只能在明确授权的私有目录产生；本次授权的合成素材位置见下节。Git 中不放真实正文、原 prompt、私有剧本或预制正向评审。

`execution_plan` 每条只有 `template/template_ordinal/prompt_version/source_site`。每个模板的序号从 1 连续列到明确上限；不能只给模板名、通配或“无限 repair”。`replies` 每条与计划一一对应，提供 `chapter_number`、精确 wire SHA 或非空 `wire_assertions`、必要 `protocol_subject`，并对非 producer 给完整 `material`。template_ordinal 来自既有跨重启 `PhysicalOrdinalStore`，不是进程计数；运行时仍以 global ordinal 绑定具体 SDK 回复。可另外明确固定 global/native operation/input 选择条件，不能重复全局槽。

必须私有明确编写的内容包括：tournament 候选及每个 judge 的轴／分数／理由；seed/packaging 的 conflict、verdict 和逐项证据；Cast person 完整字段和基于 native wire ID 的 links；source expansion 两侧 hash、quote_id 和判断；两个 reader review 四维 score/reason/evidence/issues/options；short annotation 每个 native leaf 的角色；实际触发的修复 patches；章审／商审／连续性回复及知识 extraction。revision freeze 的 anchors/kind/narrative_function、verify 的 preserved/evidence/reason/voice/no-intrusion 同样由作者明确提供，不能从 schema 或 wire 自动选择判断。

`$wire` 仅支持现有 resolver 的 JSON Pointer、完整 JSON decode、quote/key_index/`$object`。带文本前后缀或 native lossless/string-pool 的包不能用普通 `decode_json` 假装解开；main 必须提供与实际 transport 对应的明确绑定方案，缺失时继续阻断。当前 wire hash 和 source/candidate/quote ID 最终仍由已冻结 codec 核验，authoring 结构检查不冒充 native 验证。

### 当前有限阻塞清单

| 模板／接口 | 实际调用条件 | 尚缺内容 |
| --- | --- | --- |
| `concept_methodology` | normal conception 默认启用方法选择，早于 tournament | 未注册；planner，request 未设置 prompt_version（默认 null）。明确作者方法材料已在私有包，native best-effort fallback 不算脚本覆盖 |
| `conception_source_effect_contract` | chapter_count>0 且原始 seed 非空，tournament 前 | 未注册；critic，request 未设置 prompt_version。四字段 applicability/applicability_reason/mechanism_quote/causal_mode 必须明确提供 |
| `planner_fanqie_beat_sheet` | 新短篇规划必经；`fanqie_beat_recovery.build_current_request` | 未注册；明确 `fq-beats-v1.reader-first-v1`，native lossless v4 `planning_document/current_output_contract`，完整作者 BeatSheet |
| `planner_structured_field_correction` | `complete_missing_outline_contract` 发现当前缺字段时 | 未注册；四段为 `fq-fields-v1.reader-first-v1`，每个 response_schema pointer 的明确类型和值 |
| `reader_judge` | 新 draft 的默认 readability judge；audit-only 仍发请求 | 未注册，确切为 `critic/1.2`；不是新增的 revision 两模板，不能吞掉 unknown 并算 L1 通过 |
| `planner_cast_field_correction` / `planner_source_bound_expansion_evidence_repair` | native cast 字段／source evidence 需要修复时 | 未注册，须保留为条件风险；不能复用普通正向回复 |
| `prewrite_plan_manifest` / `scene_writer` | 实际配置选择 scene 路径，或 deslop 使用 scene writer | 未注册；chapter-first 路径不能证明这些调用已覆盖 |
| 首章采用 HTTP 编排／停止与回读 | main 的正常任务执行 | 仓库现有 `web_entry.py` 只启动 normal server；没有 `--stop-after-first-adopted` 参数。需 main 按真实 task/adoption readback 协调 |

这里列的是实际 source calls 的有限缺口，不重复 242 项旧静态快照。仅 122 registry 不能生成完整成功剧本。本次已在明确授权的私有目录创作四段骨架、第一章正文和有限前缀，详见下节；后续材料尚未全部绑定为可分发槽，完整 first-adoption HTTP 编排仍由 main 负责。新书 budget consume 接线已在当前 `server.py` 可见，原生授权依旧由 main 准备与验证，authoring 不创建授权。

### main 审阅后的确切命令

在 integration 根目录，用 main 实际保存的 **0600 私有输入** 和尚不存在的外部输出目录替换下面两个路径：

```sh
python -B -m scripts.full_flow.simulation.author_l1 \
  --authoring-input /private/l1-authoring/authoring.json \
  --output-root /private/l1-prepared
```

这条命令只写 blueprint/replies/四段正文、quickstart request、source/artifact SHA 和 authoring status。出现缺模板／缺材料则 exit 2，只写 `script.pending.json`；不得重命名为 `script.json` 绕过检查。无结构阻塞才写 `script.json`，状态仍仅 `STRUCTURE_READY_FOR_MAIN_REVIEW`，native acceptance / 完整执行覆盖仍为 Unknown。当前已知必经 codec 缺口使它尚不能生成可启动的全程配置。

在 main 的隔离环境中，挂载 integration 为 `/fullflow`、私有运行目录为 `/private`，使用已验证的测试 PG/Redis 与 PYTHONPATH 后，既有 normal server 命令为：

```sh
python -B -m scripts.full_flow.web_entry \
  --mode simulation \
  --private-root /private/l1-prepared \
  --config /private/l1-prepared/script.json \
  --run-id l1-normal-four-part \
  --port 8787
```

这是现有服务器入口，不是缺失的全程 HTTP 编排命令。main 向 `POST /api/tasks/quickstart` 提交 `quickstart.request.json`，通过 `GET /api/tasks/{task_id}` 与 `GET /api/tasks/{task_id}/execution` 回读正常任务。不得以 HTTP 202、脚本回复、fixture 通过代替首章真实采用回读。SDK 截获 endpoint 固定为当前 web entry 的 `https://api.minimaxi.com:443/v1/chat/completions`；main 需核对所选正常模型路由，不能因 registry 已有模板就默认 endpoint 一致。

私有 config 一旦被 `physical-ordinals.json` 固定，重启继续使用同一 run-id/config SHA。运行后遇到缺槽不可改配置、清 checkpoint 或重置 Unknown；保留原事件并由 main 处理。

新增 authoring 检查交 main 串行执行，sidecar 未运行：

```sh
.venv/bin/pytest --no-cov tests/e2e_sim/full_flow/test_l1_authoring.py
```

应连同冻结回归 `test_private_config.py`、`test_checkpointed_dispatch.py`、`test_q4_short_reader_review.py`、`test_reader_revision_codecs.py` 由 main 安排。新增 authoring 代码目前只有静态检查证据；L1 有效执行时间未由本脚本启动，不以准备耗时、跨日或等待计作跳过 L1。

## resumed：三个缺口和 authoring 完整性先交 main RED

本批只新增测试与交接元数据；122 registry 的可执行实现、`author_l1.py`、原有测试均保持冻结。按已约定的“先交失败测试，main 串行实际运行后再实现”，本侧未执行下面任何 selector，也不将静态预期称为实际 RED：

```sh
.venv/bin/pytest --no-cov \
  tests/e2e_sim/full_flow/test_l1_missing_codecs.py::ShortPlannerCodecRedTests \
  tests/e2e_sim/full_flow/test_l1_missing_codecs.py::ReaderJudgeCodecRedTests \
  tests/e2e_sim/full_flow/test_l1_authoring_readiness.py::L1AuthoringReadinessRedTests
```

共 23 个方法（13+6+4，另有 subtests）。两个短篇 codec 使用真实纯 `fanqie_beat_recovery.build_current_request` / `fanqie_outline_contract_completion._request` 生成 marked native lossless-v4 wire，版本只覆盖本轮四段的 `fq-beats-v1.reader-first-v1` / `fq-fields-v1.reader-first-v1`。新协议要求 `protocol_subject.logical_role/prompt_version/reader_first_planning/reader_first_stage/source_sha256` 与当前来源一致；原生 schema 仅用于核验明确作者材料，不能补字段。六／八段 v2、本轮未定义后缀均须明确报错。

Beat 私有 reply 需完整提供 `title/logline/pov/beats/unlock_milestone_segment/ideology_compact`，四段顺序恰好 1..4。每段明确给 `segment_number/beat_role/purpose/payoff/emotional_turn/thesis_vector` 和 `opening_contract/unlock_contract/ability_cost_contract/payoff_contract/closure_contract/continuity_contract`；作者可明确选择原生允许的空值，但 codec 不得替作者创建它们。字段补全 reply 则仅含当前 wire 的全部 target pointers，每项明确 typed value，不能越过 targets、修改已知值或把 schema default 当作者内容。

`reader_judge` 私有 reply 恰为 `opening_pull/payoff_density/emotional_impact/anti_abandon/ai_taste/human_voice/comment`。六维必须是作者明确给出的 0..1 数值（拒绝 bool、字符串强转、clamp、缺轴回填），comment 按当前 prompt 要求不超过 30 字；低分原样保留。要求完整 `current_body` 原字节 SHA，以及 `protocol_subject={logical_role:critic,prompt_version:1.2,text_cap_chars:<明确整数>}`；实际 user wire 必须等于原生 strip/cap 后的正文和原生前后缀，不能把被截断的 excerpt 当完整 source body。原生服务在失败时会 fallback neutral 0.7；脚本 codec 和 L1 审计不得把这个分支算成脚本成功覆盖。

另四个 authoring RED 针对编译结果被调用方改动：清空缺口、改正文、改 quickstart input、改有限计划。要求写盘前拒绝，不能仅凭可变 `report.blockers=[]` 输出 `script.json`。这是待 main 实测的工具完整性缺口，不更改任何 native gate。

### 正常入口到首章采用的最小 HTTP 命令序列

以下命令只交 main 审阅与执行；所有响应仍放外部私有目录。前提是 main 已验证 `script.json`、私有逐槽材料、实际 native 授权、模型 endpoint、隔离服务和源码 SHA。当前缺口未补齐时不要启动。`web_entry` 的启动命令见上节；它不是一次性全流程 CLI。

```sh
L1_PRIVATE=/private/l1-prepared
L1_BASE=http://127.0.0.1:8787

# http-client.curl 由 main 私有保存认证设置；无 token 的本地配置可为空。
# quickstart POST 只发一次。超时或无 task_id 时先协调回读，不能自动重发。
curl --fail-with-body --max-time 30 --config "$L1_PRIVATE/http-client.curl" \
  --header 'Content-Type: application/json' \
  --data-binary "@$L1_PRIVATE/quickstart.request.json" \
  --output "$L1_PRIVATE/quickstart.created.json" \
  "$L1_BASE/api/tasks/quickstart"

L1_TASK_ID="$(python -c 'import json,sys; v=json.load(open(sys.argv[1])); assert isinstance(v.get("task_id"),str) and v["task_id"]; print(v["task_id"])' "$L1_PRIVATE/quickstart.created.json")"

# main 有界轮询时每轮使用新的私有响应文件名，保留原始回执。
curl --fail-with-body --max-time 30 --config "$L1_PRIVATE/http-client.curl" \
  --output "$L1_PRIVATE/task-0001.json" "$L1_BASE/api/tasks/$L1_TASK_ID"
curl --fail-with-body --max-time 30 --config "$L1_PRIVATE/http-client.curl" \
  --output "$L1_PRIVATE/execution-0001.json" "$L1_BASE/api/tasks/$L1_TASK_ID/execution"
```

当前 `GET /api/tasks/{id}` 的 `chapter_word_stats` 由 `_load_adopted_chapter_progress` 原生回读。main 至少核对同一 task/project、`source="db"`、`adoption_evidence="verified"`、`adopted_chapter_numbers` 包含 1，且 `chapters` 的 number=1 条目有实际 `draft_id` 与 `acceptance`（promoted 或原生验证的 accepted_with_debt）。仅 completed count、导出文件、stage 文本、候选正文或 202 均不足。首次看到采用还需核对 SDK audit 无 `SCRIPT_TEMPLATE_UNCOVERED` / `builder_blocked`，未决发送不因这条进度而重置。

main 确认达到本轮边界后，可使用已有正常停止接口并继续回读：

```sh
curl --fail-with-body --max-time 30 --config "$L1_PRIVATE/http-client.curl" \
  --request POST --output "$L1_PRIVATE/stop.requested.json" \
  "$L1_BASE/api/tasks/$L1_TASK_ID/cancel"
curl --fail-with-body --max-time 30 --config "$L1_PRIVATE/http-client.curl" \
  --output "$L1_PRIVATE/execution.after-stop.json" \
  "$L1_BASE/api/tasks/$L1_TASK_ID/execution"
curl --fail-with-body --max-time 30 --config "$L1_PRIVATE/http-client.curl" \
  --output "$L1_PRIVATE/task.after-stop.json" "$L1_BASE/api/tasks/$L1_TASK_ID"
```

`cancel` 是原生控制请求，不是已静止证明；main 必须回读实际执行 owner、运行状态及未决 SDK ordinal。当前没有原子 `stop-after-first-adopted` 参数，GET 后到 cancel 生效前可能已有后续调用；需要精确“只到首章”的独占编排仍由 main 完成，不能把上述 HTTP 序列宣称为已经证明的全程 bounded runner。当前无一条可诚实标注“现已可直接全程运行”的命令。

## 本次明确授权的私有合成 fixture

位置：`/private/tmp/bestseller-l1-authored-20261029-herschel/`。目录 0700、12 个文件均 0600；原始输入、人物、正文和明确分数仅留该目录。`authoring.json` 已经过纯 `compile_private_bundle` 的结构处理，结果仍为 BLOCKED；没有清空 blockers。`script.json` 与 `script.compiler-pending.json` 的有限 config 字节相同，是按用户新授权单独交付的 **architect 前缀**，不是完整 compiler readiness。两个文件相同不表示 L1 可运行。

- 4 段骨架；第一章 2637 字符；第二至四段只有骨架和短场景原子，不声称完整后续正文。
- 3 个有限槽：`story_architect_v1` #1、`story_architect_v1_repair` #1/#2。每槽检查当前 wire 中完整私有 seed；当前完整 wire SHA / global physical ordinal 由既有 resolver 和 durable store 绑定，不预制 native UUID。
- `private-authored-replies.json` 有 13 份明确作者材料。带分数或 pass 的内容仅是合成 SDK 回复作者选择，不是 native 质量、grade 或采用证据；未解决的 source/quote/catalog identity 保持待绑定。
- 第一未覆盖模板 `concept_methodology`；下一处 `conception_source_effect_contract`。后续已知必经缺 `planner_fanqie_beat_sheet` / `reader_judge`，条件缺字段修复、cast/evidence repair、scene 路由等详见 `known-gaps.json`。未知仍报错，不通过 schema 填默认。

Config canonical SHA-256：`7e8ed83b55f13d67c7ce936ca3518f1131d5e63488b10eb20b1a291fbe7e5097`。每文件 SHA 见私有 `manifest.json`；source SHA 绑定本次检查快照，main 开跑前重新核对当前源码。纯文件编译/JSON/权限/SHA 回读已完成；pytest、native app、HTTP、DB、模型、演练均未执行。已有 codecs 和回归文件保持冻结。

main 复制完整私有包至新的外部 runroot 后，已有入口命令（仅前缀，不能标注全 L1 READY）：

```sh
python -B -m scripts.full_flow.web_entry --mode simulation \
  --private-root /private/l1-herschel \
  --config /private/l1-herschel/script.json \
  --run-id l1-herschel-20261029 --port 8787
```

随后使用现有正常 HTTP driver 提交 `quickstart.request.json`。main 取得实际 native grant，执行身份和采用回读；首次 unknown 即记录未覆盖边界，不接纳原生吞错 fallback 为 L1 通过。没有从准备耗时或跨日判定 L1 跳过。
