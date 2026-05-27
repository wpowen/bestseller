# Project Memory

## User Preferences
- README 应能单独读懂：架构分层思路、端到端数据流、核心不变量（LLM 网关、事务边界、门控）；与 `docs/architecture.md` 互补而非重复全文。
- Reply in Chinese.
- Prioritize complete, executable outputs over partial snippets.
- Keep code modular, cohesive, and maintainable.
- Include clear comments only where logic is non-obvious.
- Consider robustness, error handling, and security in implementations.
- Avoid creating extra Markdown documentation unless explicitly requested.
- 仓库内文档会持续增删改；**不要**假设某段「固定说法」永远成立，也**不要**在回复、注释或 PR 里恢复已从当前文档中删除的表述。说明流程或对外承诺时以**当前仓库里的文档与代码**为准；若上下文里是旧版本措辞，应改为与现稿一致后再引用。
- **哪些书已做过蒸馏（Phase 1 prepare）**：不要在本文件里罗列书名（语料会变、文档会变）。以磁盘为准：批量跑书记在 ``.distillation_private/corpus_prepare_state.jsonl``（每行 JSON：`status` 为 ``ok`` / ``skipped_duplicate`` / ``skipped_sibling_format`` 等）；全局去重在 ``data/distillation/source_registry.index.json``；每本书产物在 ``data/distillation/source-NNNN/`` 与 ``.distillation_private/source-NNNN/``。用户问「已经蒸馏了哪些」时应**读上述文件或列目录**再回答，而不是凭聊天记忆列举。
- **已成功 prepare 的清单导出**：``python3 scripts/distillation/export_distilled_ok_manifest.py`` → 默认 ``.distillation_private/reports/distilled_ok_manifest.jsonl``；``--exclude-extensions mobi,azw3`` 可筛扩展名。MOBI/AZW3：仓库内执行 ``uv sync --extra distillation`` 安装 PyPI ``mobi``（无 Calibre 时走 Python 解压），或安装 Calibre。语料内同书名多格式时优先 **TXT** 再 EPUB（见 ``distillation_corpus.py``）。
- **Phase 2 章节卡片（大模型）**：``python3 scripts/distillation/run_chapter_llm_jobs.py --package-dir data/distillation/source-NNNN``；经 ``complete_text``（``summarizer``）写 ``chapter_cards.jsonl``，需数据库与 LLM 配置；可用 ``--limit`` 试跑。
- **Phase 3 单书聚合（大模型）**：``python3 scripts/distillation/aggregate_source_package.py --package-dir data/distillation/source-NNNN``（需已有完整 ``chapter_cards.jsonl`` 与章节对齐）；产出 volume/book/mechanism/material/anti_copy/grammar；失败写 ``.distillation_private/errors/``。
- **全流程无人值守（后台）**：``nohup uv run python scripts/distillation/run_full_auto_distillation.py --repo-root . --private-root .distillation_private --import-mode dry-run --allow-reviewed-promotion --chapter-workers 4 --resume >> .distillation_private/reports/full_auto_distillation_daemon.log 2>&1 &``；PID 写入 ``.distillation_private/reports/full_auto_distillation_daemon.pid``。当前实现下 **dry-run 与 live 一样** 需要 ``--allow-reviewed-promotion``（会写 ``material_entries.active.jsonl`` 等推广产物）；仅跑章节/聚合不写 active 时用 ``--import-mode none``。

## Prompt / Methodology Audit (2026-05-27, 系统级排查)
- **双轨注入（核心缺口）**：`methodology_bridge.render_phase_block` 有 YAML 回退；`prompt_packs.render_methodology_block` **无回退**。Writer/Critic/Editor 仍用后者 → **22/29 pack 在 scene/review 阶段方法论块为空**（bridge 可补 ~3400 字）。Planner BookSpec/Outline 已用 bridge；World/Cast/Volume **未**注入 bridge。
- **Pack 覆盖率**：29 pack 中 `structure_guidance` 29/29 缺失且**从未注入 writer**；`opening_rules` 27/29 缺失；`segment_writer` 28/29 缺失。B 类 `planner_*` 已从模型删除，`_planner_fragment_or_ref` 常空。
- **已修复路径**：`chapter_constraint_manifest`（prewrite）、`chapter_llm_quality_judge`/`outline_llm_judge`（judge）已接 `get_fragment`。
- **Mode B 断层**：`.claude/skills/.../prompts/*.md` 与 `services/` 流水线未打通。
- **可观测性**：默认 `llm_runs.prompt_hash`；全量 prompt 需 `BESTSELLER_TRACE_SCENE_PROMPTS=full`。
- **后续优先（P0）**：`render_methodology_block` 委托 bridge；Planner 中间阶段补 bridge；Prompt Contract Gate + 快照工具；Summarizer 补 hook/情绪摘要约束。

## Working Conventions
- For this repository, prefer adding runnable planning artifacts under `examples/planning/` when building story content through the framework.
- When user requests novel writing via specific skill, deliver both planning artifacts and full chapter prose in project files (not only outlines).
- For full-length novel generation requests, place final readable deliverables under `output/ai-generated/<novel-slug>/` with volume/chapter structure.
- Keep `.audit-reports/backups/` out of version control; do not commit backup chapter files to GitHub.
- **蒸馏数据不上 GitHub**：``data/distillation/source-*/``、``aggregates/``、``source_registry.index.json`` 已在 ``.gitignore``；仅保留 ``data/distillation/schemas/`` 等契约文件入仓。私有状态仍在 ``.distillation_private/``。
- **实现计划文档不上 GitHub**：``docs/plans/`` 整目录忽略（如 ``2026-05-15-content-entry-optimization.md``）；对外文档用 ``docs/architecture.md`` 等已入仓文件。
- **本地测试/审计产物不上 GitHub**：仓库根 ``audits/``（框架跑批日志、baseline JSON、``*-framework-run-*`` 等）；``.playwright-cli/``（Playwright CLI 会话快照与 console log）。正式 audit 仍走 ``output/<slug>/audits/``（已由 ``output/`` 忽略）。
