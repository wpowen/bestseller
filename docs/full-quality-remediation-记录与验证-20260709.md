# 全量质量修复 · 记录与验证 · 2026-07-09

> 对应计划：调研报告后的 W0–W2 首批落地（质量主线优先，模型按 MiniMax-M3 校准）。

## 1. 根因（摘要）

1. Skill/文档仍写「每章 ≥5000」，与 runtime `1800–2600–3500` 冲突 → 规划/Mode B 误导。
2. Writer A/B 窗口结束后默认回落 **full**，抵消 lean 瘦身。
3. 整章 full regen 触发过宽（score≤0.62 / 二次 <0.72）→ M3 上重写退化。
4. 候选择优过粗；writer 预算未单独配置。

## 2. 已落地改动

### W0 SSOT
- `docs/chapter-word-ssot.md` — 字数单一真源说明
- `.claude` + `.agents` skill（planning/recipes/templates/prompts/modes 等）对齐 1800–2600–3500
- `tests/unit/test_chapter_word_ssot.py` — config / gate / settings 一致性

### W1 提示词
- `config/default.yaml` + `settings.GenerationSettings`：
  - `writer_prompt_mode: lean`（生产默认）
  - `writer_prompt_ab_winner: lean`
  - `writer_prompt_budget_tokens: 8000`
- `drafts._writer_prompt_mode_for_chapter`：A/B 结束后空 winner → **lean**（不再 full）
- `drafts` 生成路径：context 预算优先读 `writer_prompt_budget_tokens`
- `drafts._score_writer_candidate`：超长惩罚 + 中文 n-gram 多样性启发（M3 best-of 无二次 LLM）

### W2 质量环
- `pipelines._chapter_review_full_regeneration_reason`：
  - 灾难分 full regen：`score ≤ 0.50`（原 0.62）
  - 定点失败后再 full：`iterations ≥ 2` 且 `score < 0.65`（原 iter≥1 且 <0.72）
  - 中等低分走 **定点 rewrite**

## 3. 验证

### L1
```text
pytest tests/unit/test_writer_prompt_mode.py \
       tests/unit/test_chapter_word_ssot.py \
       tests/unit/test_pipeline_services.py::test_chapter_review_full_regeneration_for_very_low_score \
       --no-cov
→ 10 passed
```

### L2 / L3
- 本批未跑全量套件 / 真机 LLM（成本）。
- **建议你重建一本新书时**：确认 worker 镜像含本批代码（`docker compose build web worker && up -d`），观察：
  1. 章目标字数是否在 1800–3500
  2. 写手 prompt 模式是否 lean（llm_runs / debug）
  3. 商业分中等偏低时是否定点 rewrite 而非整章重开

## 4b. 第二批续作（同日）

| 项 | 改动 |
|----|------|
| Writer 四层 schema + 装配报告 | `services/prompt_assembly.py`；`drafts` 预算后写 `_LAST_PROMPT_ASSEMBLY_REPORT` |
| 指令优先级块 | 写入 scene writer system（中英）— 字数 > 冷读 > 展示 > 反AI > 反应放大(题材条件) |
| Outline 字数硬带 | `planner._outline_prompts` 注入 SSOT band；fallback 章目标 clamp 到 max |
| 开篇 exposition soft | ch1–5 critical 0.25→0.40，减轻世界观开篇误杀 |
| 反应放大题材化 | scene review 文案改为题材条件（不强制围观打脸） |
| 私货回归单测 | `test_prompt_assembly` 扫 live code（注释豁免） |

**L1 续**：`test_prompt_assembly.py` + 既有 SSOT/mode/regen 共 **22 passed**。

## 4. 仍未完成（工程债 / 内容债）

| 项 | 波次 |
|----|------|
| 方法论四路注入再压一轮（quality levers 与 compiler 合并） | W1 深 |
| H8–H10 题材内多样性内容扩容 | W3 |
| Prompt registry 全量索引 | W3 |
| 巨型文件拆分 / 检索 ANN | W4–W5 |

## 5. 部署提醒

改动在 `src/` + `config/`：若生产跑 Docker，需 **重建镜像** 后新书才吃到本批逻辑。在跑旧书勿中途热更踩踏。

---

## 6. 本书运营修复（custom-xianxia-1783601435 · 2026-07-09）

| 动作 | 结果 |
|------|------|
| 取消僵尸 `generate_volume_plan`×2 | status→failed（cancelled_zombie） |
| 保留写作管线 | project/chapter/scene_pipeline 继续 |
| metadata tags | 去掉「无敌流」；加「代价成长」 |
| tone_primary | 沙雕烟火底色 + 代价渐冷 |
| ch11 单字标题「甜」 | →「失甜」 |
| ch1–5 information_withheld/revealed | 空则 soft-fill |
| 框架：缺标题 soft-fill from goal | `planner._normalize_generated_outline_titles_or_fail` |
| 框架：黄金三章字段 soft-fill | `_soft_fill_golden_three_outline_fields` |
| 框架：outline 字数归一 | validate 路径调用 `_normalize_outline_word_targets` |

**L1**：`test_outline_title_soft_fill.py` + 既有相关 **19 passed**。

---

## 7. 框架能力修复（非单书 · 2026-07-09 续）

| 能力缺口 | 修复 |
|----------|------|
| 多 `generate_volume_plan` 僵尸并行 | `planning_concurrency.cancel_stale_planning_workflows`；novel/foundation/volume 入口调用；API 并发类型扩展 |
| BookSpec「第50章」vs 20 章 | `_sanitize_book_spec_against_project_scale` 改写超范围章号 |
| 锁死炼气仍打「无敌流」 | 同 sanitizer 剥 invincible tags |
| 截断 themes 模板句 | sanitizer 丢弃残句 |
| 单卷 conflict_phase=终局 | `_normalize_volume_plan_conflict_phases` 卷1强制 early phase |
| 细纲「每章至少3场」与 config max=3 冲突 | scene_count_contract 读 `scenes_per_chapter` |
| 反应放大硬杀非爽文 | `build_scene_review_prompts` 按 `genre_wants_reaction_amplification` 开关轴 |

**L1**：`test_framework_planning_guards.py` 等合计 **24 passed**。
