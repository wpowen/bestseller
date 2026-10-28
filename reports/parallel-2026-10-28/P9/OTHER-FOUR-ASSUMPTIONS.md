# 范围外四段假设与 G3 接缝（静态盘点，不改代码）

基线：`refs/tags/e2e17`。以下行号对应基线；只读检查及独立 agent 盘点。不访问主线/G2b 私有资料。G3 关系采用 P2 已确认设计第 4 节中的公开旧计划边界；当前 G3 实际差异 Unknown。

| 文件/函数（src/bestseller/services/ 下） | 观察与后续设计 | 与已知 G3 的关系 |
|---|---|---|
| `native_title_operator/title_operator.py::check_snapshot` 398–410 | 真正恰好四章：target=4、sources=[1,2,3,4]、四份独立草稿、四章知识闭合；同时固定项目/scope。保留历史授权，新 N 另走合法通用标题链，不能替换常量扩大授权。 | 标题、全书 manifest 授权语义接缝 |
| `native_publication_title.py::_manifest` 92–105 | 真正恰好四章及四个独立 draft/chapter ID，绑定原 scope。新 N 不复用四章批准。 | 标题/篇幅分流接缝 |
| `fanqie_short_semantic_selection_adapter.py::load_selection_parent` 149–170、`verify_parent_context` 374–383 | `original_four_whole_semantic_review` 是历史 parent 身份，不是一般章数上限；不能自动给新 N 任务授权。 | parent/task/source_plan_binding 语义重叠 |
| `planning_draft_admission.py::projection_audit_targets` 310–340、`_input` 503–516；`fanqie_outline_source_selection.py::admit_and_select_outline_source` 97–135 | 动态章/场景来源检查及 1..target 标记；连续子范围不等于全项目完整。联验第 8 章及所有 scene pointer、缺尾/缺 marker 拒绝。 | 准入→G3 source 绑定接缝；未证明当前文件冲突 |
| `workflows.py::materialize_chapter_outline_batch` 2814、3651；`materialized_outline_source_recovery.py::_read_sources` 81–103 | 遍历全部 batch.chapters，按实际物化范围核验。新增第 8 章场景/来源漂移集成测试，不改变硬门。 | 物化/全书 source 语义接缝 |
| `fanqie_segment_projection.py::project_segment_outline` 82–104；`fanqie_short_plan_resume.py::read_existing_complete_fanqie_plan` 224–324 | 原生 1..N 投影与完整 census；`four_current_foundation_required` 指 Book/World/Cast/Volume 四类，不是四章。联验 8 段和尾段漂移，禁止重新规划绕过既有结果。 | complete-plan→G3 入口接缝 |
| `adopted_knowledge_resume.py::resume_project_adopted_knowledge` 27–86、`read_adopted_knowledge` 102–165 | 按所选章/连续前缀读取，每个采用正文独立验证知识/Bible闭合；不是前四章即整书完成。尾章 KG 未闭合应阻塞交付。 | KG/采用正文 SHA 语义重叠 |
| `fanqie_short_pipeline.py::_load_chapter_drafts` 459–490、`_run_fanqie_segments` 524–537、assembly 666–680；`fanqie_short_source_export.py::_read_adopted_assembly` 244–328、publisher 606–724 | 主要动态 N；整书需完整采用集合、知识闭合、文件回读、有效审查且无 pending rewrite。7/8 不得完成，8 段正文新 SHA 必须重审。 | **公开旧 G3 明确共享文件**，由主线单 owner 联验 |
| `fanqie_short_semantic_inputs.py::build_current_inputs` 347–376；`book_closure.py::evaluate_book_closure` 66–111；`exports.py` 1097–1186 | 动态 manifest/target/census；部分 loader 仅枚举已有章，不单独提供完整性证明。保持末章缺失拒绝及全 N 文件一致。 | 整书 manifest/闭环语义重叠 |
| `fanqie_short_planner.py::_fallback_beat_sheet` 292；`fanqie_short_finalizer.py::finalize_fanqie_short_for_upload` 68–84 | max(target,4) 是领域下限；max_rounds=4 是修复次数。不能批量替换成 N；不启用 fallback 逃避硬准入。 | 次数/停滞/预算是共同不变量 |
| `whole_book_quality_gate.py::_early_retention_findings` 365–423 | 签约检查在五样本触发；no_hook_streak>=4 属 51–100 区间的质量阈值。不能因 6/8 段失败而降门。 | 全书审查语义接缝 |
| `planning_projection_consistency.py` 212、330–331；`chapter_scene_contract_materializer.py::_derive_gate_function` 676–686 | “4”分别是 pointer 深度和 scene_number>=4 的尾钩启发式，不是段数限制。 | 不作为 N 扩展修改项 |
| `planning_surgical_budget.py::applies` 16–22；`source_complete_retention.py::_role/retention_limit` 80–108、169–end | 当前只注册 v1 补全对；P9 v2 未冒用旧 producer。完整 v2 受普通 120k cap；更大保留额度和版本注册由集成方另立合同。 | producer/consumer 版本核验接缝，主线 G2/G3 签收 |

所有 `fanqie_short_*` 及上述范围外文件均无 P9 代码改动。此盘点不宣称 6/8 段已完成准入、物化、采用、知识、整书、标题或导出的端到端集成验收。
