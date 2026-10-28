# P9 验证复现

基线固定使用 `refs/tags/e2e17`。在 P9 根目录执行，依赖复用主工作区已有 `.venv`，无新增安装。先读 `tests/support/database_safety.py`：仅接受显式 test/tmp/ci 库名或 pytest 管理的 SQLite 文件，启动及实际连接均有保护。此处设置名含 `_test` 的 SQLite URL，实际模块夹具另建 pytest 临时文件；无 PostgreSQL、外部数据库、Docker或模型服务。

```sh
export BESTSELLER__DATABASE__URL=sqlite+aiosqlite:////private/tmp/p9_config_test.sqlite3
export PYTHONPATH=src:tests:tests/unit
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/pytest --no-cov -q \
  tests/unit/test_fanqie_outline_contract_completion.py \
  tests/unit/test_fanqie_outline_contract_completion_segments.py
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/pytest --no-cov -q \
  tests/unit/test_normal_pipeline_prompt_audit_entry.py \
  tests/unit/test_commercial_truncated_source_receipt.py \
  tests/unit/test_task_projection_adoption_progress.py \
  tests/unit/test_execution_result_durability.py
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/ruff check \
  src/bestseller/services/fanqie_outline_contract_completion.py \
  tests/unit/test_fanqie_outline_contract_completion.py \
  tests/unit/test_fanqie_outline_contract_completion_segments.py
git diff --check
```

- `baseline-existing.log`：在独立 Python 进程中 `git show refs/tags/e2e17:src/bestseller/services/fanqie_outline_contract_completion.py`，将该源码执行到同名模块 globals，再用 pytest.main 执行原有整模块；41 passed。无工作树源码切换。
- 新测试 `test_v1_native_requests_receipt_and_hashes_are_baseline_bytes` 使用独立加载的基线模块，在同一夹具完成 v1 后由 P9 完整重入，比较序列化回执/候选/原生请求、全部持久行和来源 hash，且验证没有新增模型请求。
- `tests/fixtures/fanqie_outline_completion/v1-fixed-receipt.json` 是仅由基线 `_request/_targets/_apply_patch` 生成的固定 UUID、title-only 四段合成回执/请求束。不是生产回执。测试验证文件逐字节与基线重建一致，再分别由基线和 P9 回放到相同候选；真实 gates 与容量/物理文件的覆盖由原生 SQLite 测试单独提供。fixture 文件和回执 hash 见 verification.json。
- 每个 5–8 段生成请求的实际完整来源及 tail pointer 均检查；真实 `_prepare_capacity` 逐请求运行，v2 在现有 legacy cap 下使用原生网关但替换 `_call_litellm` 网络层，验证保留文件和 `_paid` 物理回读。fixture 的 socket 连接禁止器仍生效。
- `delivery-planning.log` 选择 delivery_required 中与规划入口、来源回执、任务规划进度和物理结果持久性有关的四个完整模块；其余纯整书/取消/安全边界模块未在本任务扩大执行，不宣称 delivery_required 全清单通过。

保留的中间日志：

| 日志 | 归因 |
|---|---|
| red.log | 首次 `:memory:` 环境值被配置 YAML 解析成 mapping，24 setup errors；没有进入行为测试。改为 test 文件名后重跑。 |
| red-behavior.log | 生产代码仍为基线：22 failed / 2 passed，先红证据。 |
| green-01.log | 64 passed / 2 failed：测试修改 ORM 元数据后未提交，触发 clean_session_required。 |
| green-02.log | 69 passed / 2 failed：测试仍修改 ORM 对象，但 `_sources` 返回的是独立只读快照，实际测试对象未变，先被 owner 保护挡住。改为修改目标快照。 |
| segments-final.log | 新增测试 31 passed。 |
| module-final.log | 整模块 72 passed；随后仅将合同拒绝测试加固为动态 target+1 和显式 acquired owner，8 个受影响用例重跑通过 review-scope-final.log。 |
| native-v2-gateway.log | v2 原生网关单独验证 1 passed（该项已包括在72项）。 |
| lint-01/02/final.log | 首轮格式/导入诊断已修正，最终 All checks passed。 |

独立 agent 只读盘点范围外四段假设；另一 agent 复核生产 diff，未发现生产代码正确性缺陷，对旧日志中测试对象问题的意见已纳入更强拒绝用例并重跑。
