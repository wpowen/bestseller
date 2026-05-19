# 质量门禁与人工复核修复执行计划

Generated at: `2026-05-19`

## 1. 执行原则

本轮修复按“先止血，再前置，再规模化”的顺序推进：

1. **P0 先止血**：先消除检测误判和错误修复指令，避免继续扩大无效人工复核。
2. **P1 前置阻断**：把源设定、平台、语言、canon 污染问题拦在章节生成前。
3. **P2 规模优化**：补齐类别蒸馏、卷级修复、dashboard 和读者体验型门禁。

验收口径：

- 任何英文项目不得再因 CJK-only 字数/节奏检测得到 `char_count=0` 的 hard failure。
- 自动修复任务必须携带语言上下文，英文项目不得收到中文网文修复指令。
- invalid audit row 不允许进入自动修复任务计划。

## 2. 第一批 P0 修复状态

| 项 | 状态 | 代码入口 | 验收 |
| --- | --- | --- | --- |
| 语言感知字数检测 | Done | `src/bestseller/services/quality_levers/detectors.py` | `evaluate_word_count(..., language="en-US")` 使用英文词数 |
| 英文 pulse density | Done | `src/bestseller/services/quality_levers/detectors.py` | 英文使用 deterministic pressure words，不再用 CJK 字符分母 |
| 英文节奏门禁隔离 | Done | `src/bestseller/services/quality_levers/rhythm_engineering.py` | `audit_rhythm(..., language="en-US")` 返回 `applicable=False` 且不 hard fail |
| retrofit 语言参数与推断 | Done | `scripts/quality_levers_retrofit_audit.py` | CSV 输出 `language/audit_validity/count_unit/rhythm_applicable` |
| invalid audit 隔离 | Done | `src/bestseller/services/autonomous_book_repair.py` | `audit_validity=invalid*` 的行不会生成 repair task |
| 修复任务语言上下文 | Done | `src/bestseller/services/autonomous_book_repair.py` | `QualityRepairTaskSpec` 携带 `language`，metadata 写入 language |
| 英文修复指令 | Done | `src/bestseller/services/autonomous_book_repair.py` | 英文项目输出英文修复指令和 English-word 安全带 |

## 3. 已跑验证

```bash
uv run pytest tests/unit/test_quality_levers_detectors.py tests/unit/test_quality_levers_retrofit_audit.py tests/unit/test_autonomous_book_repair.py -q --no-cov
```

结果：`52 passed`

```bash
uv run pytest tests/unit/test_run_book_quality_closure.py tests/unit/test_book_quality_closure.py -q --no-cov
```

结果：`21 passed`

```bash
uv run pytest tests/unit/test_quality_levers_detectors.py tests/unit/test_quality_levers_retrofit_audit.py tests/unit/test_autonomous_book_repair.py tests/unit/test_run_book_quality_closure.py tests/unit/test_book_quality_closure.py -q --no-cov
```

结果：`73 passed`

```bash
uv run ruff check --select F,E9 src/bestseller/services/quality_levers/detectors.py src/bestseller/services/quality_levers/rhythm_engineering.py scripts/quality_levers_retrofit_audit.py src/bestseller/services/autonomous_book_repair.py tests/unit/test_quality_levers_detectors.py tests/unit/test_quality_levers_retrofit_audit.py tests/unit/test_autonomous_book_repair.py
```

结果：`All checks passed`

真实项目抽样：

```bash
uv run python scripts/quality_levers_retrofit_audit.py --slug romantasy-1776330993 --platform tomato --limit 1 --out-csv /tmp/bestseller-retrofit-english.csv --out-summary /tmp/bestseller-retrofit-english.md --json
```

结果：`ok=1`，CSV 中 `language=en-US`、`char_count=2488`、`count_unit=english_words`、`rhythm_applicable=False`。

```bash
uv run python scripts/quality_levers_retrofit_audit.py --slug exorcist-detective-1778428166 --platform qimao --limit 1 --out-csv /tmp/bestseller-retrofit-chinese.csv --out-summary /tmp/bestseller-retrofit-chinese.md --json
```

结果：中文项目仍走 `zh-CN/cjk_chars` 路径，原有中文质量门禁行为保留。

## 4. 下一批 P1 修复计划

### P1-A. 统一质量失败事件

状态：第一批 Done。已完成 retrofit audit / autonomous repair 路径的最小事件层。

目标：把 pipeline、quality gate、repair loop、human review 写入同一类 `QualityFailureEvent`。

最小字段：

- `slug`
- `chapter_number`
- `stage`
- `gate_id`
- `code`
- `severity`
- `language`
- `platform`
- `source_stage`
- `preventable_stage`
- `remediation_class`
- `evidence_ref`
- `repair_task_id`
- `human_review_reason`

预期收益：可以稳定计算 same-code repeat rate、detector false positive rate、source-stage prevention rate。

已完成：

- 新增 `src/bestseller/services/quality_failure_events.py`。
- `failure_events_from_retrofit_row(...)` 可把 retrofit CSV 行转换为统一事件。
- `scripts/quality_levers_retrofit_audit.py` 输出 `platform` 字段，事件可带齐 platform/language。
- `QualityRepairTaskSpec.to_dict()` 与 rewrite task metadata 写入 `quality_failure_events`。
- `audit_validity=invalid*` 会归因到 `source_stage=detector`、`preventable_stage=metadata_validation`、`remediation_class=fix_detector_not_chapter`。

新增验证：

```bash
uv run pytest tests/unit/test_quality_levers_detectors.py tests/unit/test_quality_levers_retrofit_audit.py tests/unit/test_autonomous_book_repair.py tests/unit/test_quality_failure_events.py tests/unit/test_run_book_quality_closure.py tests/unit/test_book_quality_closure.py -q --no-cov
```

结果：`77 passed`

```bash
uv run ruff check --select F,E9 src/bestseller/services/quality_failure_events.py src/bestseller/services/quality_levers/detectors.py src/bestseller/services/quality_levers/rhythm_engineering.py scripts/quality_levers_retrofit_audit.py src/bestseller/services/autonomous_book_repair.py tests/unit/test_quality_failure_events.py tests/unit/test_quality_levers_detectors.py tests/unit/test_quality_levers_retrofit_audit.py tests/unit/test_autonomous_book_repair.py
```

结果：`All checks passed`

### P1-B. Source Artifact Audit

状态：基础服务 Done。已完成文件级源资产审计，下一步接入批次生成/修复入口。

目标：批次写作和章节修复前，先审计 listing/story bible/chapter outline/canon 是否污染。

第一批规则：

- 旧题材词/旧玩法词回流。
- 废弃角色、废弃关系、废弃组织回流。
- platform/language/category 不一致。
- canon state regression。
- chapter outline 与当前位置 profile 不匹配。

预期收益：把“框架设定错”从章节修复前移到源资产修复。

已完成：

- 新增 `src/bestseller/services/source_artifact_audit.py`。
- 支持发现 `project.md`、listing、story bible、outline、rules 等源资产，并跳过 `chapter-*.md` 正文。
- 支持默认旧设定污染词检查：`玩家`、`副本`、`主神`、`无限流`、`系统面板`、`任务奖励`、`游戏提示`。
- 支持 `expected_language`、`expected_platform`、`expected_category` 检查。
- 输出 `SourceArtifactAuditReport`，包含 `blocking_findings` 和可序列化 dict。

新增验证：

```bash
uv run pytest tests/unit/test_source_artifact_audit.py tests/unit/test_quality_failure_events.py tests/unit/test_quality_levers_retrofit_audit.py tests/unit/test_autonomous_book_repair.py -q --no-cov
```

结果：`30 passed`

```bash
uv run pytest tests/unit/test_quality_levers_detectors.py tests/unit/test_quality_levers_retrofit_audit.py tests/unit/test_autonomous_book_repair.py tests/unit/test_quality_failure_events.py tests/unit/test_source_artifact_audit.py tests/unit/test_run_book_quality_closure.py tests/unit/test_book_quality_closure.py -q --no-cov
```

结果：`82 passed`

真实项目只读抽样：

```bash
uv run python -c "from pathlib import Path; from bestseller.services.source_artifact_audit import audit_source_artifacts; r=audit_source_artifacts('exorcist-detective-1778051012', output_dir=Path('output')); print(r.passed, r.artifact_count, [(f.code, f.severity, f.evidence) for f in r.findings[:5]])"
```

结果：`False 2 [('SOURCE_FORBIDDEN_TERM', 'critical', {'term_counts': {'无限流': 1}})]`

### P1-C. Prewrite Readiness staged block

目标：新项目默认 block，存量高风险项目 block_on_critical，老项目允许 migration override。

配置策略：

- new project：`prewrite_readiness_block_on_failure=true`
- legacy high-risk：`block_on_critical`
- legacy override：写入 `legacy_risk_accepted`

预期收益：避免弱规划进入写作后再由章节门禁反复拦截。

## 5. 需要单独处理的存量数据

英文项目旧 retrofit 输出已经不可信，应做一次重新审计：

```bash
uv run python scripts/quality_levers_retrofit_audit.py --slug romantasy-1776330993 --platform tomato --language en-US
uv run python scripts/quality_levers_retrofit_audit.py --slug superhero-fiction-1776147970 --platform tomato --language en-US
uv run python scripts/quality_levers_retrofit_audit.py --slug superhero-fiction-1776301343 --platform tomato --language en-US
```

在重跑前，不应把旧 CSV 中的 `char_count=0`、`word_count underflow`、`pulse_count=0` 作为人工复核或自动重写依据。
