# normal quickstart 全程脚本：inventory 与适配契约

本目录实现静态盘点、有限 SDK 返回的显式 builders、keyflow 协议、私有 wire 材料绑定及持久化分派。P3 提供底层 HTTPX transport 基建；主线程提供私有 blueprint、明确判断／提取材料，并负责 normal quickstart HTTP driver、业务接线及唯一演练。这里没有前缀导入、DB 操作、HTTP 客户端、服务启动、模型调用或 transport 自动安装。

## 交付 API

| 模块 | API | 作用 |
|---|---|---|
| `inventory.py` | `scan_repository(root)` | 只用 AST 读取服务源码；输出模板、动态表达式、文件/函数/行号、源 SHA 及文件级 tool/schema 符号线索。 |
| `inventory.py` | `changed_sources(root, inventory)` | 检查源文件新增、删除和字节漂移。集成树同时有其他 worker，清单是逐文件哈希快照，不是仓库锁。 |
| `contracts.py` | `TemplateCatalog(inventory).require(template)` | 只接受清单中的具体非历史名字；未知一律 `SCRIPT_TEMPLATE_UNCOVERED`。不把动态表达式变成通配通过。 |
| `contracts.py` | `CallIdentity.from_observation(observation, *, body_sha256=None, binding_hashes=None)` | 消费 P3 原生观察结果，不分配 UUID、不修改发送计数或账本。 |
| `contracts.py` | `tool_response(catalog, template, wire_body, *, tool_name, arguments, usage, ...)` | 对真实 SDK wire 中指定函数的 parameters 做结构校验，编码一份 function 返回。arguments 由 P3 算法提供；这里不填 pass/approval/grade。 |
| `contracts.py` | `text_response(catalog, template, content, *, usage, finish_reason, ...)` | 包装 P3 在运行时生成的正文或阶段 JSON；不提供默认正文或 verdict。 |
| `contracts.py` | `build_document(catalog, responses, *, allowed_endpoints, limits=...)` | 构造有限 P3 Tape，拒绝歧义 selector、无界调用序号、超限 payload。 |
| `contracts.py` | `prepare_p3_tape(document, *, catalog, audit_path=None)` | 显式调用时才 lazy import `e2e_sim.transport`，建立 Tape 并复用 `NativeTemplateObserver`；不安装 transport。 |
| `review.py` | `inspect_transport_source(source)` | AST 核对 P3 接口，不执行所给源码。 |
| `review.py` | `inspect_script_source(inventory, source)` | 提取直接模板声明与动态 entry；源码出现字符串不等于覆盖通过。 |
| `review.py` | `review_script_document(inventory, document, *, required_templates=(), limits=...)` | 区分未知模板、缺失必需模板、未映射可选候选及动态调用点；只返回元数据。 |
| `private_store.py` | `write_private_document(document, destination, *, private_root, repository_root, limits=...)` | 仅在显式调用时写私有 Tape；根目录和父目录须已存在且 0700，目标须全新、无软链接、位于所有 checkout 外，文件 0600。 |

## 模板与 tool/schema 的真实接缝

`template_inventory.json` 是完整的静态扫描产物；`TEMPLATE_INVENTORY.md` 是可读索引。`source_files[].tool_references/schema_references` 是文件级线索，不代表该文件每个模板都使用这些工具。精确 schema 以每次实际 SDK request 为准，禁止从示例响应、fallback 或旧书回执代替。

下表源码路径均省略共同前缀 `src/bestseller/`；未带 `services/` 的服务模块也位于 `src/bestseller/services/`。

| 阶段 | 模板或族 | 真实结构来源／运行时依赖 |
|---|---|---|
| 构思架构 | `story_architect_v1`、`story_architect_v1_repair` | `services/story_architect.py` → `domain/facets.py::StoryFacets`；文本 JSON，不是 function tool。 |
| 构思代理 | `conception_*`、`concept_tournament_*` | `services/conception.py::_llm_call_json`、`concept_tournament.py`；阶段专用 JSON，部分没有统一 Pydantic/tool schema；必须由 P3 按各消费者要求生成。JSON repair 名称保留独立槽。 |
| 构思意图审查 | `conception_intent_alignment` | `services/intent_review_submission.py::intent_review_submission_tools`；`submit_intent_review`，来源枚举来自当次 source。 |
| 基础规划 | `planner_book_spec/world_spec/cast_spec/volume_plan` | `services/source_bound_planning_submission.py::planning_submission_tool`；`submit_{kind}`。world/cast 原生对象、volume 原生 `volumes` wrapper、legacy `asset_json` 不能混用；依据 wire parameters。 |
| 人物分部 | `planner_cast_person_part`、`planner_cast_links_part` | `services/source_cast_parts.py::generate` 内 `produce`；逐人物／关系部分的运行时 schema，不能把一个人物当完整 cast。 |
| 章纲及恢复 | `planner_volume_{N}_chapter_outline[_batch_{A}_{B}]` 及修复族 | `services/planner.py::_generate_structured_artifact`、`domain/workflow.py::ChapterOutlineBatchInput`；卷号、范围和来源都由当次输入决定。这些动态名字目前保持待展开。 |
| 规划准入 | `planning_draft_admission`、`planner_outline_domain_field_corrections`、`planner_structured_field_correction` | `planning_admission_submission.py::planning_admission_tool`、`planning_domain_field_protocol.py`、`planning_schema_recovery.py`；目标路径、quote/source IDs 及当前投影绑定不能猜。 |
| 正文／重写 | `chapter_first_writer`、`scene_writer`、`chapter_rewrite*` | `services/drafts.py`、`services/reviews.py`；纯文本算法生成；字数、人物、规划和连续性仍由原生校验器决定。 |
| 主审 | `chapter_review` | `services/primary_review_submission.py::primary_review_submission_tools`；`submit_chapter_critique`，paragraph_number 枚举来自当前正文。citation tuple、覆盖和正文指纹仍由原生解析器校验。 |
| 商审 | `chapter_commercial_quality_judge` | `services/commercial_review_tool.py::review_submission_tools`；`submit_chapter_review`；current/prior/observation/constraint 来源 ID 及 compact pattern 均取当次 wire。独立采样槽不能合并成一个通过结论。 |
| 窗审／卷审 | `chapter_window_quality_judge`、`volume_quality_checkpoint_judge` | 对应 services 模块及 `domain/llm_quality_judge.py` 的结果解析；当前是文本 JSON，不假设 function tool。 |
| 知识 | `chapter_feedback_extraction*`、`chapter_state_snapshot*`、`bible_update_v1*`、`scene_bible_delta_v1` | `services/feedback.py`、`continuity.py`、`chapter_continuity_admission.py`、`story_bible.py`；真实人物名册、正文 lineage、快照／Bible 数据结构必须分别满足，空数组不等于知识闭合。 |
| 整书 | `fanqie_short_semantic_review`、`fanqie_long_semantic_review`、`book_ending` 等 | semantic 模块的 `native_request_fields` 和独立源精确校验；`services/book_native.py::call` 的工具名为 `submit_` + 实际 template，schema 为当次传入值。`book_request_purpose.py` 中族声明只作清单线索，不能取代实际请求身份。 |
| 标题／简介 | `title_tournament_*`、`title_platform_revision`、`title_story_grounded_revision`、`fanqie_short_title_semantic_judge`、`blurb_*` | `conception.py`、`native_title_operator/title_operator.py` 的 request dict、`fanqie_short_title_semantic_judge.py`；后者工具 `judge_current_publication_title`。标题、约束和引用必须来自当前书。 |

## 与 P3 e1b3ee3e 的接口核对

`p3_transport_contract.json` 保存该 Git 对象 transport 的源码 SHA 和 AST 签名核对结果，没有复制 P3 实现。接口为：

- `TAPE_SCHEMA = v3-scripted-provider-tape.v1`。
- `Tape(document, *, audit_path=None, template_observer=None)`、`Tape.from_file(...)`。
- `NativeObservation(prompt_template, call_index, project_id, workflow_run_id)`。
- `NativeTemplateObserver.__call__(request, native_call)`；序号按 `(project_id, template)` 实例内计数，**不是**每 workflow 或逻辑重试重新从 1 开始。
- `install_scripted_http(tape)` 返回可撤销上下文；同步、异步 SDK 都复用 P3 底层实现，包装层不另 patch。

构思阶段可以没有 project UUID，甚至没有 workflow UUID。没有 UUID 时须用真实 `wire_fingerprints(body)` 的精确 hash selector 绑定，不能制造 project 或固定到旧 fa14。hash 只用于匹配，不是 paid-origin、账本或批准证明。先前已运行的调用序号和重启后 observer 的重建由主线程/P3 协调，包装层不伪造 durable counter。

P3 Tape 的文档不能携带本模块自己的 schema/phase/coverage 字段；这些元数据放在清单和 review 返回值中。endpoint 必须由原角色配置生成精确 scheme/host/port/path allowlist。`auto/json/sse/timeout/disconnect` 与 `stop/length/tool_calls/content_filter` 原样保留；禁止把截断、过滤、未知发送修成 stop。

目标集成树的前缀工具若合入，应采用 P3 的 `tests/support/planning_prefix_capture.py` **及** `planning_prefix_exclusions.py`，不能换回 P2 SQLite-only 版本。本目录不导入或调用它们；本轮继续按前缀不可用、normal quickstart 后备路径交接。

## 未覆盖与复核边界

- P3 最终参考 `4fb4cc11`；其 catalogue 与示例不证明 normal quickstart 全程。实际绑定范围以 `quickstart_registry_status.json` 和 `QUICKSTART_HANDOFF.md` 为准。
- 动态模板表达式全部保留在清单；P3 最终脚本需给出实际有限名字和调用上限。无法展开的具体名字明确 `SCRIPT_TEMPLATE_UNCOVERED`，不允许通过宽泛前缀或 `.*` 规则补齐。
- 本目录不生成具体整书故事、审批、正式 grade/current/promoted 状态，也不改 gate、review、adoption、paid validators。tool response 只是 SDK 模型提案；schema 校验通过不代表语义、付费、采用、导出或完成闭环通过。
- `tool_response` 使用仓库原生模块同样使用的 `jsonschema`，不添加依赖；缺失时报 `SCRIPT_SCHEMA_VALIDATOR_UNAVAILABLE`。只校验 supplied schema，禁止远程 `$ref`；字符串 JSON 内的二次协议及语义规则仍交给原生消费者。
- 清单使用 Python 3.14 AST：系统 Python 3.9 不支持当前部分 f-string 语法。该选择仅用于源码盘点，不构成应用运行版本验收。

## 仅供主线程的验证入口

主线程可选择 `tests/e2e_sim/full_flow`，让既有隔离运行器负责执行；sidecar 不运行测试。Cast 用例延迟导入原生 schema/codec 定义，不启动业务服务、不连接 DB/Docker/HTTP/model。两个 Cast 文本协议及其私有材料契约见 `QUICKSTART_HANDOFF.md`。

主线程若单独收集，可在根目录使用以下 pytest 参数；本轮没有执行该命令：

```sh
PYTHONPATH=.:src:tests python -B -m pytest --noconftest --confcutdir=tests/e2e_sim/full_flow --no-cov tests/e2e_sim/full_flow
```

静态清单刷新只运行 AST scanner，输出只能在本 ownership 内；已有清单不会被 CLI 静默覆盖：

```sh
/opt/homebrew/bin/python3 -B -m scripts.full_flow.simulation.inventory --root . --output scripts/full_flow/simulation/template_inventory.next.json
```
