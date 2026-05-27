# 小说生成流水线加固与优化计划

> 范围：从题材 → 大纲 → 人物 → 卷纲 → 章纲 → 细纲 → 正文 全链路
> 输入：基于 2026-05-25 对 `src/bestseller/services/pipelines.py`（8539 行）、`workflows.py`（2198 行）、`self_heal.py`、68 个 `*_gate.py` 的代码审查
> 目标：消除"第一章反复生成失败"类死循环，收敛 310 个 service 的职责，让流水线可观测、可中断、可恢复

---

## 0. 现状速览

| 维度 | 数字 | 评估 |
|---|---|---|
| service 文件 | 310 | 过度膨胀，职责重叠 |
| `*_gate.py` | 68 | 多数无自愈/无人工兜底分类 |
| `run_chapter_pipeline` 单函数 | 2137 行 | 不可测试，事故源头 |
| `pipelines.py` | 8539 行 | 必须拆分 |
| `PipelineSettings` 字段 | ~200 | 实际引用 35，配置漂移 |
| `except Exception:` 静默吞噬 | 30+ | 自愈循环根因 |
| self_heal 识别的 `blocked_by_*` 码 | 10 / 68 gate | 大量 gate 一旦阻塞就成孤儿 |

---

## 1. P0：止血修复（必须先做）

### P0-1　第一章 qimao_opening_gate 死循环
- **现象**：`chapter_number == 1` 失败 → 抛 `ValueError` → self_heal 重排队 → 再触发同一 finding → 永久循环（"青囊不语问阴阳"复现）
- **代码位置**：[pipelines.py:1379-1473](src/bestseller/services/pipelines.py:1379)、[self_heal.py:609](src/bestseller/worker/self_heal.py:609)
- **方案**：
  - [ ] 在 `_enforce_qimao_opening_gate_after_chapter` 失败时为 chapter.metadata_json 写入 `qimao_opening_attempts` 计数
  - [ ] 超过 `qimao_opening_max_attempts`（建议 3）转 `production_state="needs_human_review"`，并把该状态加入 self_heal 的 `_project_resume_is_blocked` 短路条件
  - [ ] 把 `qimao_opening_gate` finding 的可修码 / 不可修码（如 protagonist_voice_missing 是 LLM 重生成无解的契约级问题）显式分类，写入 `chapter_auto_repair_repairable_codes`
- **验收**：人工构造一个永远不通过的 opening contract，跑流水线后项目应在 3 次后进入 `needs_human_review`，self_heal 日志显示 `skipped slug=… reason=qimao_opening_exhausted`

### P0-2　异常静默吞噬清单化
- **代码位置**：30+ 处 `except Exception:` —— 见 [pipelines.py:262/272/309/484/682/722/764/1083/1173/1704/1732/1779/1973/2087/2320/2584/2629/2682](src/bestseller/services/pipelines.py)
- **方案**：
  - [ ] 建立 `services/_exception_policy.py`：定义三类策略 `swallow_infra` / `surface_data_contract` / `fail_loud`
  - [ ] 用 grep 把所有 `except Exception:` 列入审计表，逐项标记策略（预计 ≥ 80% 应改为白名单异常）
  - [ ] Material Forge 失败（[pipelines.py:6624](src/bestseller/services/pipelines.py:6624)）：若 `enable_reference_style_generation=True`，必须 `raise`，不允许 legacy fallback
  - [ ] 3 个 `_ensure_*_backfill` 失败（[pipelines.py:484/682/722/764](src/bestseller/services/pipelines.py:484)）：缺失 kernel 时直接 fail loud，不允许下游裸跑
- **验收**：跑 `grep -n "except Exception" src/bestseller/services/pipelines.py` 后剩余条目全部带 `# policy: swallow_infra` 注释，且 PR 评审通过

### P0-3　Resume gap 永久空洞
- **代码位置**：[pipelines.py:6582](src/bestseller/services/pipelines.py:6582) `contiguous_prefix_max` 截断后无回路
- **方案**：
  - [ ] 截断时把缺口章节号写入 `project.metadata_json["deferred_chapter_numbers"]`
  - [ ] 新增 `services/deferred_chapter_recovery.py`，每个 project pipeline 入口检查并优先回填
  - [ ] self_heal 把 `deferred_chapter_numbers` 非空作为 stuck 信号
- **验收**：单测构造 chapters=[1..50, 101..150]，跑两轮 pipeline，第二轮应能处理 deferred 切片

---

## 2. P1：架构治理（防止重蹈覆辙）

### P1-1　拆 `run_chapter_pipeline`（2137 行 → 4 段）
- **代码位置**：[pipelines.py:4222](src/bestseller/services/pipelines.py:4222)
- **方案**（按 phase 拆，命名建议）：
  - [ ] `chapter_phase_a_pre_gate(...)`：outline_readiness + predraft_quality
  - [ ] `chapter_phase_b_scene_loop(...)`：逐 scene + 装配
  - [ ] `chapter_phase_c_post_gates(...)`：length / retention / fanqie / qimao / whole_book
  - [ ] `chapter_phase_d_auto_repair(...)`：自愈循环
  - [ ] 主函数只做 workflow_run 状态机 + 4 个 phase 编排
- **验收**：每个 phase ≤ 400 行；4 个 phase 各自有独立单测；`pytest tests/unit/test_chapter_pipeline_phases/` 覆盖率 ≥ 80%

### P1-2　PipelineSettings 收敛
- **代码位置**：[settings.py:199-429](src/bestseller/settings.py:199)
- **方案**：
  - [ ] 写脚本 `scripts/audit_pipeline_settings.py`：枚举所有字段 vs `grep settings.pipeline.*` 引用，输出漂移表
  - [ ] 删除未引用字段（≥ 160 个候选）
  - [ ] 启动期加 `validate_pipeline_settings(settings)`：缺失被引用字段直接抛错
- **验收**：`audit_pipeline_settings.py` 输出 "0 orphan fields, 0 missing references"

### P1-3　Gate 注册表与"可修分类"统一
- **现状**：68 个 gate，每个自己往 `chapter.metadata_json` 塞 `blocked_by_*`，self_heal 只硬编码识别 10 个
- **方案**：
  - [ ] 新增 `services/gate_registry.py`：每个 gate 注册 `(name, severity, repair_strategy: auto|rewrite_task|human_review)`
  - [ ] 替换 self_heal [pipelines.py:396-404](src/bestseller/services/pipelines.py:396) 的硬编码白名单为 `gate_registry.is_auto_resumable(code)`
  - [ ] 给现有 68 个 gate 逐一登记（脚本辅助：先 grep `blocked_by_` 自动列出所有码）
- **验收**：新加 gate 必须在 registry 注册，否则 import-time 校验失败；`tests/unit/test_gate_registry_coverage.py` 强制 100% 覆盖

### P1-4　Outline 五合一
- **现状**：`outline_density_gate` / `outline_specificity_gate` / `outline_llm_judge` / `outline_reveal_alignment_gate` / `chapter_outline_readiness_gate` 各跑各的 LLM
- **方案**：
  - [ ] 合并为 `outline_quality_gate.py`，子检查 `density / specificity / reveal_alignment / readiness`，共享一次 LLM 调用 + 结构化输出
  - [ ] 保留旧文件做 thin re-export，下个版本删除
- **验收**：单章 outline 评审 LLM token 消耗下降 ≥ 60%

### P1-5　Character / Voice 系列合并
- **现状**：9 个 `character_*` + 5 个 `dialogue_voice_*` + 2 个 `voice_*`
- **方案**：
  - [ ] `character_lifecycle.py` 吞并 `character_evolution.py` / `character_arcs.py`（同一对象不同视图）
  - [ ] `voice_profile.py` 吞并 `dialogue_voice_profile / dialogue_voice_blocks / voice_signature`
- **验收**：character_* + voice_* 文件数从 16 降到 ≤ 8

---

## 3. P2：性能与成本

### P2-1　Backfill 并行化
- **代码位置**：[pipelines.py:6406-6424](src/bestseller/services/pipelines.py:6406)
- **方案**：
  - [ ] 三个 `_ensure_*_backfill` 用 `asyncio.gather` 并发
  - [ ] 同步检查它们对 project 的写入是否真的独立（emotion_kernel / public_emotion / entry_system）—— 否则加锁
- **验收**：单次 project_pipeline 启动时间下降 ≥ 30%

### P2-2　LLM Judge 结果缓存
- **现状**：单章经过 outline_judge → predraft_quality → chapter_llm_commercial → chapter_window → volume_checkpoint → whole_book 共 6 次 LLM 评审，零缓存
- **方案**：
  - [ ] 新增 `services/llm_judge_cache.py`：以 `(judge_name, sha256(prompt))` 为 key，PostgreSQL 表 `llm_judge_cache` 存 verdict
  - [ ] 所有 judge 统一走 `cached_judge(judge_name, prompt, executor)` 包装
  - [ ] 加 `--no-cache` CLI flag 用于强制重判
- **验收**：跑 100 章的项目 LLM judge 调用次数下降 ≥ 40%（命中重复章节窗口）

### P2-3　Scene 并行（谨慎）
- **代码位置**：[pipelines.py:4582](src/bestseller/services/pipelines.py:4582)
- **方案**：
  - [ ] 静态检查同章 scene 之间是否共享可变 state（如 `revealed_ledger`、`chase_debt_ledger`）
  - [ ] 若有则保留串行；若无则用 `asyncio.gather` + 子事务并行
- **验收**：判定结论写入 [ARCHITECTURE](docs/architecture.md)；若可并行，单章生成时间下降 ≥ 40%

---

## 4. P3：可观测性与运维

### P3-1　Pipeline trace ID
- [ ] 每次 `run_project_pipeline` 生成 UUID，贯穿所有 logger 字段（已部分通过 `workflow_run.id` 实现，需统一）
- [ ] 所有 `_emit_progress` 事件携带该 trace_id

### P3-2　Stuck 诊断 CLI
- [ ] 新增 `bestseller diagnose <slug>` 命令：输出当前 chapter 状态、最后 5 次 workflow_run、所有 `blocked_by_*` metadata、deferred chapters、self_heal 上次决策原因
- 目的：用户不必再让 AI 翻 Docker 日志

### P3-3　Skipped 测试归零
- [ ] 解决 [test_character_role_gate.py:187](tests/unit/test_character_role_gate.py:187)、[test_timeline_consistency_gate.py:182](tests/unit/test_timeline_consistency_gate.py:182) 等 6 个 `pytest.skip("...not present")`：要么把 fixture 入库，要么把测试改成 fixture 缺失即失败

---

## 5. 执行计划与里程碑

| 里程碑 | 包含 | 预计时间 | Exit Criteria |
|---|---|---|---|
| M0 止血 | P0-1, P0-2 关键 5 处, P0-3 | 1 周 | "青囊不语"类项目不再死循环；material forge 失败硬错 |
| M1 主结构 | P1-1, P1-3 | 2 周 | run_chapter_pipeline ≤ 500 行；gate_registry 100% 覆盖 |
| M2 收敛 | P0-2 剩余 + P1-2 + P1-4 + P1-5 | 2 周 | service 文件数 ≤ 230；PipelineSettings 漂移为 0 |
| M3 性能 | P2-1, P2-2, P2-3 | 1 周 | LLM 成本 -40%，单 project 启动 -30% |
| M4 运维 | P3-1, P3-2, P3-3 | 1 周 | `diagnose` 上线；skipped 测试归零 |

---

## 6. 修复 / 验证清单（可直接勾选）

### M0 止血
- [ ] P0-1.a `_enforce_qimao_opening_gate_after_chapter` 写 attempts 计数
- [ ] P0-1.b 超限转 `needs_human_review`
- [ ] P0-1.c qimao finding 显式可修 / 不可修分类
- [ ] P0-1.d 单测：构造永不通过 contract，3 次后停
- [ ] P0-2.a 建立 `_exception_policy.py` 三类策略
- [ ] P0-2.b Material Forge 失败硬错（pipelines.py:6624）
- [ ] P0-2.c 3 个 backfill 失败硬错
- [ ] P0-2.d 全量审计 30+ except Exception，加 policy 注释
- [ ] P0-3.a 截断时写 `deferred_chapter_numbers`
- [ ] P0-3.b `deferred_chapter_recovery.py` 回填
- [ ] P0-3.c self_heal 识别 deferred 信号
- [ ] P0-3.d 单测：[1..50, 101..150] 两轮跑通

### M1 主结构
- [ ] P1-1.a 拆 chapter_phase_a_pre_gate
- [ ] P1-1.b 拆 chapter_phase_b_scene_loop
- [ ] P1-1.c 拆 chapter_phase_c_post_gates
- [ ] P1-1.d 拆 chapter_phase_d_auto_repair
- [ ] P1-1.e 每 phase 独立单测，覆盖率 ≥ 80%
- [ ] P1-3.a `gate_registry.py` 创建
- [ ] P1-3.b 68 个 gate 全部注册
- [ ] P1-3.c self_heal 替换硬编码白名单
- [ ] P1-3.d import-time 校验未注册 gate

### M2 收敛
- [ ] P0-2 剩余 except 审计完成
- [ ] P1-2.a `audit_pipeline_settings.py` 跑通
- [ ] P1-2.b 删除孤儿字段
- [ ] P1-2.c `validate_pipeline_settings` 启动校验
- [ ] P1-4.a `outline_quality_gate.py` 合并
- [ ] P1-4.b 旧 outline_*_gate thin re-export
- [ ] P1-5.a character 系合并到 lifecycle
- [ ] P1-5.b voice 系合并到 voice_profile

### M3 性能
- [ ] P2-1 backfill 并行
- [ ] P2-2.a `llm_judge_cache.py` + 表迁移
- [ ] P2-2.b 6 个 judge 走 cached_judge
- [ ] P2-2.c `--no-cache` CLI flag
- [ ] P2-3 scene 并行可行性结论 + 实施

### M4 运维
- [ ] P3-1 trace_id 统一
- [ ] P3-2 `bestseller diagnose <slug>`
- [ ] P3-3 skipped 测试归零

---

## 7. 风险与回滚

| 风险 | 触发条件 | 回滚策略 |
|---|---|---|
| gate_registry 漏注册导致全停 | M1 上线后某 gate 抛错 | 注册校验降级为 warn 一周再升级为 fail |
| outline 五合一引入回归 | M2 上线后 outline 阶段质量下降 | 保留旧 gate 文件，feature flag `enable_outline_quality_gate` 默认 off |
| LLM judge 缓存导致脏数据复用 | 上游 prompt schema 变更未 bump | cache key 加入 `judge_version`，每次合并 PR 修改 judge 必须 bump |
| 并行化引发 DB 死锁 | M3 P2-1 / P2-3 上线 | 异步 task 加 advisory lock；监控 `pg_stat_activity` |

---

## 8. 验证用项目

固定一组 fixture 项目，每次 PR 必须跑通：

| 项目 | 用于验证 |
|---|---|
| 青囊不语问阴阳 | P0-1 第一章死循环修复 |
| romantasy-1776330993 | P0-3 resume gap 修复 |
| superhero-fiction-1776147970 | P0-2 progressive_autowrite 静默 swallow 修复 |
| 任意新生成的 50 章项目 | M1 / M2 / M3 端到端性能与质量回归 |
