# P9：规划合同补全支持 4–8 段

已在隔离分支 `par/P9` 实现补全模块的 4–8 段支持，6/8 段能够生成并物理回放到最后一段。四段继续 v1，来源、请求、回执和 hash 与 `refs/tags/e2e17` 字节对照通过。最终模块 72 项测试、规划相关交付 112 项测试全部通过；这是本地隔离夹具证据，尚不代表整书生产链或真实模型验收。

**范围与实现。** 工作树 `scratch/par-worktrees/P9`，基线 `refs/tags/e2e17`（419181a41f29363263cee8a12d1694ace0bcef24）。唯一业务改动为 `src/bestseller/services/fanqie_outline_contract_completion.py`；其余改动仅测试、固定夹具与本报告目录。未修改其他业务文件，无合并、推送、Docker、项目数据库或外部模型调用；测试仅使用现有 pytest 隔离 SQLite 夹具。

- `complete_missing_outline_contract`、`_verified_candidate`：由同一可信 N 驱动生成与回放，要求真正整数且 4≤N≤8；项目目标、存在时的创建 `segment_count`、完整物理 Beat 数与候选数一致，原序章号严格为 1..N。回执最多 `2N` parts；仍每章最多两次，保留原 paid slots、Unknown 和失败恢复语义。
- `_sources`、新增 `_segment_target/_segment_binding/_segment_count/_receipt_segment_binding`：统一合同校验；仅非四段来源增加合同绑定，包括存在时的创建段数，避免回放从回执自报 N 取得权威。`_request` 与新回执采用 v2，显式绑定 N 和有序章号；四段不增加键、不改变原 prompt/version/source/hash。
- `_prepare_capacity`：每个请求核算完整来源及此前已补全候选。共享保留器只注册 v1，故 v2 使用普通请求现有 120,000 字符上限及实际模型/finite-run 窗口；装不下在预留和发送前报 `full_source_capacity_blocked:physical_retention/context_window`，不截断、不调高公共 cap、不伪装 v1。

**验证结果。** 所有模块均整文件执行，未删旧断言或按测试名筛选交付模块。

| 验证 | 结果与证据 |
|---|---|
| 先红后绿 | `red-behavior.log`：22 failed / 2 passed，复现四段限制和缺少合同拒绝；最终 `module-final.log`：**72 passed**（既有 41、新增 31）。 |
| v1 不变 | 基线原实现既有测试 **41 passed**（`baseline-existing.log`）；同一原生来源比较请求、source/dispatch hashes、回执及物理回放；固定 v1 回执夹具逐字节对照。 |
| 新段数/拒绝/容量 | 5/6/7/8 全章 pointer、6/8 恰好 2N 回放与超限、3/9/bool/float、缺章/乱序/重复、目标/创建合同/回执不一致、新 N no-op；8 段尾部 Unknown 不重购；完整来源容量增长、窗口/保留超限；v2 原生网关替身保留及 `_paid` 回读。复核后 8 个合同拒绝用例再次通过（`review-scope-final.log`）。 |
| delivery_required 规划相关 | `test_normal_pipeline_prompt_audit_entry`、`test_commercial_truncated_source_receipt`、`test_task_projection_adoption_progress`、`test_execution_result_durability`，均为 `tests/unit/` 整模块，**112 passed**（`delivery-planning.log`）。 |
| 静态检查 | Ruff、`git diff --check` 通过；固定夹具 hashes 在 `verification.json`；独立只读复核无生产代码问题，指出的测试快照/owner 隔离问题已修正并重跑。 |

**范围外“4 段”及 G3。** 详细文件/函数/基线行号见 [OTHER-FOUR-ASSUMPTIONS.md](OTHER-FOUR-ASSUMPTIONS.md)，本轮全部只读：

1. 真正固定四章：`native_title_operator/title_operator.py::check_snapshot` 与 `native_publication_title.py::_manifest`，均绑定历史项目/scope、四份草稿与知识闭合；保留历史授权，新 N 另走合法标题链。与 G3 标题/全文 manifest 授权语义重叠。
2. `fanqie_short_semantic_selection_adapter.py` 的 `original_four_whole_semantic_review` 是历史 parent 身份；不得扩成新 N 授权，与 G3 task/parent/source_plan_binding 重叠。
3. 准入、来源选择、物化、complete-plan reader、知识、book_closure、整书/导出相关路径主要已动态遍历 N；需主线补第 8 段缺失/漂移/KG/文件回读联验。`fanqie_short_pipeline.py`、`fanqie_short_source_export.py` 是公开旧 G3 明确共享文件，P9 未改。
4. 四类 foundation、pointer 深度、四次修复预算、连续无钩子阈值和 fallback 的领域下限不是四段限制，不批量替换。v2 公共 producer/consumer 注册与更大来源保留额度也留给主线集成。

**Evidence / Inference / Unknown。** 代码、字节和隔离回归为 Evidence；G3 接缝依据 P2 已确认设计与公开旧计划，当前 G3 实际逐行改动 Unknown。未验证新 N 的真实准入/物化/采用/知识/标题/整书导出，不宣称生产已贯通。复现命令与中间失败归因见 [TESTING.md](TESTING.md)。
