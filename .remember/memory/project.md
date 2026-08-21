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
- **已落地（2026-05-29）**：`render_methodology_block` 委托 `methodology_bridge`；`reader_quality_gate`（persona/字数真值/跨章重复/兑现台账）；`chapter_word_count_truth`；`scripts/eval_reader_persona_harness.py`。
- **深度融合落地（2026-05-29 第二轮）**：
  - 新 block code 接修复闭环：duplicate gate 改产出规范 code（`CHAPTER_OPENING_REPETITION`/`CROSS_CHAPTER_REPETITION`，复用既有 playbook）；`quality_repair_playbooks` 补 WORD_COUNT/PAYOFF/PERSONA playbook；PERSONA_* 进 `AUTO_REPAIR_RETENTION_CODES`；`drafts.maybe_prepare_chapter_auto_repair` 用 finding evidence 拼具体 hint。
  - Mode B 接流水线：`services/mode_b_bridge.py`（drive_mode_b_chapter/sync_progress_yaml/enqueue_repair_item）+ `scripts/mode_b_chapter_bridge.py`；`exports._resolve_chapter_export_path` 支持 mode_b `volumes/vol-NN/ch-NNN.md`；`modes.md`/`orchestration.md`/`orchestrator.mdc` WRITE_CHAPTER 改为经 bridge 调 `run_chapter_pipeline`。
  - LLM reader-judge：`services/reader_judge.py` → `grade_chapter(prose_quality_score=)`；配置 `reader_quality_gate.enable_llm_reader_judge`（默认 off）+ `reader_judge_audit_only`。
  - planner World/Cast/Volume 已有 `attach_planner_methodology`（bridge），无需再补；`story_bible_write_gate` 阈值收紧；里程碑失败入 progress.yaml `repair_queue`。
  - **payoff_ledger 与 below-target 改为 advisory 默认**（避免关键词启发式误杀 hook-heavy 章）：`block_payoff_ledger=False`、`block_below_target_length=False`；真正硬闸门是 3000 CJK 硬下限 + persona/reader-judge。
- **后续优先**：reader-judge 校准后再开 enforce；Prompt Contract Gate + 快照工具；Summarizer 补 hook/情绪摘要约束。

## AI-flavor / Prose Quality Root Causes (2026-07-23)
- **门禁奖励模板化**：`reviews.evaluate_scene_draft` 用 `_EMBODIED_EMOTION_TERMS`/`_TENSION_HOOK_TERMS` 密度加分；中文 AI 套话几乎不罚（`_AI_CLICHE_TERMS` 基本是英文）；`voice_consistency` 基线 0.74。
- **Prompt 过载致程序腔**：消融 `output/_prompt-ablation-final/` 生产框架 3.5 分 vs 精简基线 8.0；framework_user ≈38KB；禁用名单含角色名逼迂回。
- **读感裁判默认关**：`reader_quality_gate.enable_llm_reader_judge=false`；persona `prose_quality` 中性 0.7；LLM critic 轴无 AI 味。
- **ai_flavor block=50 过松**：score 32 仍 pass；deslop 主要在 block/特定 discourse；auto-repair 偏字数/钩子/重复，`DIALOGUE_AI_FLAVOR` 无 playbook。
- **文采/蒸馏未进硬路径**：`prose_craft` 已撤出 PROSE_SCENE；`enable_library_soft_reference=false`；pack `structure_guidance` 0/31、`opening_rules` 2/31。

## Framework Architecture Audit (2026-08-17)
- 横切默认已改为 opt-in（晚间修复）：`MarketPositioningConfig` 默认「未指定平台」；`enable_shuangwen_fusion=False`（loop pack 自动开）；`persist_qimao_opening_contract` / writer 合同块走 `opening_quality_gate_requested`（残留 contract 不够）；pack 推断去掉裸「升级」「奇幻」，西方/史诗奇幻 → `epic-fantasy`。账单/尸体不进 writer；folk-horror 仍是题材门控，`铜钱` 已从 TAIL_HOOK 删除。
- 签约路径仍在：显式 `opening_quality_gate_enabled` 或平台含七猫/起点/番茄。番茄仍是平台 preset / 末日 pack YAML，不是空书默认。
- 有意不改：`quality_mode=closure`、persona 软熔断、reader-judge 影子、scene 密度加分。回归 `tests/unit/test_genre_neutral_defaults.py`。
- 对照稿：`docs/框架深度排查与开源对照分析-20260817.md`（§0.2）。

## Ranking Conception / Quality Research (2026-08-17)
- 生成书「平庸无逻辑不可读」首先是概念层均值回归，不是缺标签。铁律在 `concept_tournament.py`：不可自动补全 / 反共识非反处境 / 杂交新物种 / 概念自带长篇引擎。批量 12 候选才能压众数（单条玄幻死亡族 60% vs 一次 12 条 7.5%）；干涸保底仍会发货众数概念。
- 爽文是正交轴 `题材 × 爽度档`，不是官方标签。章级唯一共识：赢落到有名字的人脸上 + 被具体的人看见 + 账上留下能带走的东西。A 碾压 / B 智斗 / C 关系，不定档就判等于掷骰子。碾压密度是属性标记不是质量标记。
- 喜剧走 `shezhu-bailan-comedy` 时禁止「摆烂两章就打脸收割」。不要给喜剧默认开 shuangwen fusion。
- 优化顺序：构思强制锦标赛冠军且禁止保底众数 → logline 机制因果校准后硬拦 → reader-judge 校准后再 enforce 并去掉 scene 密度加分。分析画布：`canvases/ranking-book-conception-quality.canvas.tsx`。

## One-liner Conception Pipeline (2026-08-17)
- 生产路径 `engine_first`：12 条 raw pitch → rank → premise card（本轮不写钩子）→ HOOK_DISTILL 压成 30–75 字 → 八轴 → seriality → `winner.concept`。冠军进 `ctx.description` 高概念块和 `concept_contract.hook_card.one_liner`。
- 构思后 logline 12 轴 / story_appeal 当前 **advisory**（`block_expansion=false`，因误杀斗破/完美/诡秘）；定罪句式也不杀书。`verdict_from_approved_concept_contract` 可用八轴证据直接 EXPAND。
- **逻辑审计空转**：`_derive_conception_world_model` 恒返回空，`_audit_mechanism_causality` 因 `world_model is None` 直接跳过。
- **世界观断层**：BookSpec 注入 `render_concept_contract_block`；WorldSpec **不注入**。v2 会 `pop hook_spec`，`hook_spec_from_metadata` 对 v2 返回 None，`apply_hook_to_world_spec` 不跑。画布：`canvases/one-sentence-conception-pipeline.canvas.tsx`。
- 人工评测样本：12 条正例 + 4 条对照废案，画布 `canvases/one-liner-eval-set.canvas.tsx`。四问：可读 / 想点 / 逻辑 / 能否补全全书。先只看一句话。
- 用户评测（2026-08-17）：最大问题是结论先行 AI 味，其次是钩子无爽点/无点击欲。根因：章级 `negated_definition` 不跑构思；`plain_language` 只测好不好懂；`hook_pull` advisory；HOOK_DISTILL 教「先定义再演示」。榜单：书名才是 3 秒槽位；简介是试吃装（对白/弹窗）；爽点靠第三方反应。画布 `canvases/hook-ai-flavor-vs-board.canvas.tsx`。
- 第二波评测样本（场面+当场反应，带书名）：`canvases/one-liner-eval-set-r2.canvas.tsx`。
- 用户指出第二波仍无吸引力、AI 味足：根因是把构思句写成章首镜头模具。正确：构思=渴望种子（谁/凭什么赢/对谁算账）；书名=3秒；简介=试吃+四条信息。画布 `canvases/one-liner-role-vs-board.canvas.tsx`。
- 第三波评测：S01 撞《聚宝仙盆》；灯/印逻辑不清；摆烂「别逃课」无因果；摸鱼跨世界跳跃；都市打脸情节齐但句子不连。第四波改成一条因果链：`canvases/conception-eval-round4.canvas.tsx`。
- 本轮结果 V01–V06（去撞书、一句一因、都市改述职工号）：`canvases/conception-eval-result.canvas.tsx`。章级 detect 对短构思句打不出分，不能当已去 AI 味。
- 用户评 V01–V06 太短、不是卖点。加长为完整安利段 W01–W06（约 90–160 字）：`canvases/conception-selling-points.canvas.tsx`。章末钩 ≠ 卖点。
- 用户评 W01 无爽感：还药是报复循环不是金手指；「谁A就B」是定罪句。玄幻卖点必须有可升级外挂 + 当场多出能数的好处。画布 `canvases/xuanhuan-shuanggan-analysis.canvas.tsx`。
- 创意层修复计划：先改 HOOK_DISTILL（90–160 字卖点 + 金手指演示）和项目卡 golden_finger；蒸馏后跑定罪句式/negated_definition；无冠军不保底。不先开 logline 硬杀。画布 `canvases/conception-repair-plan.canvas.tsx`。

## Working Conventions
- **交接文档（2026-07-24）**：完整进展/未完成项见 `docs/plans/2026-07-24-quality-remediation-handoff.md`（给下一任模型）。结项卡在真书验收（≥10章）与 B1/B2 校准，不是 A1–A5 代码。
- **Phase A 正文止血已落地（2026-07-24）**：`prose_prompt_profile` 默认/配置均为 `lean`；chapter-first lean 用 `render_compact_writer_discipline`；`reviews` 保留 density 抬分并加同词复读+中文套话罚分；`ai_flavor` block_cn=38 且 warn 带强制 deslop；短章 playbook 去感官灌水并补 `DIALOGUE_AI_FLAVOR`。单测 `tests/unit/test_quality_remediation_phase_a.py`。Docker 经 override 热挂载 `src/config`。
- **Phase B/C 剩余项已落地（2026-07-24）**：B1 `reader_judge` 六轴（含 `ai_taste`/`human_voice`）+ 真正 `audit_only` 接线（默认 enable=false、audit_only=true、enforce_voice=false，不影响在跑书）；C2 终稿/导出可读已存 dimensions；C3 voice 返工高原记 `voice_debt` 软过；C1 lean 场景合同去掉 `action_sequence`；B3 `/api/projects/{slug}/chapters/{n}/prompt-manifest` + quickstart「生效片段」；B4 `enable_voice_few_shot`（默认 off）+ `config/voice_few_shots.yaml`；B2 `scripts/lean_vs_full_pairwise_arena.py`。单测 `tests/unit/test_quality_remediation_phase_b.py`。
- **外部对标+可执行方案（2026-07-23）**：GitHub（autonovel / AI-Novel-Writing-Assistant 等）与 V2EX/36氪共识：plan→write→后置审；短纪律+物料；过检测≠人味。可执行冲刺见 canvas `research-backed-executable-plan.canvas.tsx`。
- **全书质量全链路评审（2026-07-23）**：根因不是缺去 AI 味工具，而是评分/门禁奖励模板化（身体词密度抬 emotion/hook）、writer prompt 过载（消融框架 3.5 vs 精简 8.0）、读感 judge 默认关、AI-flavor block=50 过松、auto-repair 修 KPI 不修文笔。`prose_prompt_profile` 默认仍 `full` 与 `writer_prompt_mode=lean` 冲突。完整报告见 Cursor canvas `book-quality-full-lifecycle-review.canvas.tsx`。
- **Writer prompt 装配双旋钮（2026-07-23）**：`generation.writer_prompt_mode=lean` 只驱动 scene 路径 `compact_user_prompt(lean=…)`；chapter-first 块裁剪看 `pipeline.prose_prompt_profile`（默认 `full`）。消融最优指令带 175–633 字；`render_anti_ai_voice_discipline(scope=chapter)` 单块已 ~832 字。ideal：system≤700、user=物料/短 beats/短 bans；验收/市场块留给门禁。
- **架构设计展厅**：`src/bestseller/web/novel_architecture_course.html`（路由 `/architecture-course`，支持 `?embed=1`）。已整合进 quickstart 顶部 Tab「架构设计」（`#architecture` 深链）；iframe 嵌入全屏展厅。本地 override 挂载 `./src/bestseller/web` 到 web 容器以便热更 HTML。
- For this repository, prefer adding runnable planning artifacts under `examples/planning/` when building story content through the framework.
- When user requests novel writing via specific skill, deliver both planning artifacts and full chapter prose in project files (not only outlines).
- For full-length novel generation requests, place final readable deliverables under `output/ai-generated/<novel-slug>/` with volume/chapter structure.
- Keep `.audit-reports/backups/` out of version control; do not commit backup chapter files to GitHub.
- **蒸馏数据不上 GitHub**：``data/distillation/source-*/``、``aggregates/``、``source_registry.index.json`` 已在 ``.gitignore``；仅保留 ``data/distillation/schemas/`` 等契约文件入仓。私有状态仍在 ``.distillation_private/``。
- **实现计划文档不上 GitHub**：``docs/plans/`` 整目录忽略（如 ``2026-05-15-content-entry-optimization.md``）；对外文档用 ``docs/architecture.md`` 等已入仓文件。
- **本地测试/审计产物不上 GitHub**：仓库根 ``audits/``（框架跑批日志、baseline JSON、``*-framework-run-*`` 等）；``.playwright-cli/``（Playwright CLI 会话快照与 console log）。正式 audit 仍走 ``output/<slug>/audits/``（已由 ``output/`` 忽略）。
