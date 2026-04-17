# Invariants — 永不违反

> 任一条目违反即停机回退。critic 发现违反应直接判不合格，不论其他维度分数多高。

## Mode B（写作）红线

- ❌ **每章不得低于 5 000 字**。低于即 `status: rework`；从场景 / 内心 / 对白扩写，不得以形容词灌水。
- ❌ 小说正文**不得写入 `output/ai-generated/{slug}/` 以外的任何路径**。
- ❌ 已有的 canon facts **不可修改**；只能用更高 `valid_from_chapter` 的 `supersedes` 条目覆盖。
- ❌ 任何角色**不得知道**在后续章节才揭示的事（知识单调性）。
- ❌ 同一 scene 的 rewrite **不得超过 2 次**；第 3 次即 `accept_on_stall`。
- ❌ `target_chapters > 50` **必须**写 `story-bible/act-plan.md`。
- ❌ `volumes > 3` **必须**写 `story-bible/world-expansion.md` 并跟踪 DeferredReveal。
- ❌ 主角**不得每卷全胜**；必须符合 [planning.md § Win/Loss Rhythm](planning.md)。
- ❌ 不得跳过规划阶段直接写正文（即 story-bible 未建立时不写 ch-001）。
- ❌ 不得以 "穿越 / 系统 / 金手指" 文本模式替代章节内容。

## Mode B（结构）红线

- ✅ `meta.yaml` 与磁盘状态**始终同步**（`current_chapter` 指向最近一章已落盘且评分通过的编号）。
- ✅ Volume README 的章节索引**始终同步**（新章落盘前要改状态列）。
- ✅ 章节 frontmatter 的 `scores` 必须是**真实自评**（不得恒为 0.95）。
- ✅ 每章收笔**必须**追加 canon facts + timeline events；到 snapshot 周期必须写 snapshot 文件。
- ✅ 长篇必须在**偿付前 ≥ 2 卷**种下伏笔（DeferredReveal 追踪）。

## Mode A（开发）红线

- ❌ **不得直接调 LiteLLM**；所有 LLM 调用经 `services/llm.py::complete_text(LLMCompletionRequest)`。
- ❌ 不得跳过 `checkpoint_commit()`——场景之间必须切事务，否则引发 snapshot 膨胀。
- ❌ 不得在 `ReviewReportModel` / `QualityScoreModel` 之外记录质量数据——所有评分统一落表。
- ❌ 新增表**必须**配 Alembic migration；禁止运行期手改 DDL。
- ❌ 不得硬编码 secrets；通过 env `BESTSELLER__<SECTION>__<KEY>` 注入。
- ❌ rewrite prompt 的策略文本**必须**包在 `=== reference only ===` 围栏里，防止 LLM 把 meta 字符漏进正文。

## 跨 Mode 红线

- ❌ 不得混淆 Mode：用户让写小说时，**不要**去改仓库代码；用户问代码时，**不要**去 `output/ai-generated/` 生成章节。
- ❌ 不得隐瞒限制：若本轮 token 预算无法完成 N 章，**诚实披露**并按批次推进，不得悄悄截断。
- ❌ 不得伪造进度：章节未过 critic 就不写进 volume README 的 `approved`。

## 极少数例外处理

- 真的出现 canon fact 错误？→ 不改原条目。新增一条 `supersedes` 并在当章文末注明"作者纠错"。
- 真的需要切换 POV？→ **< 200 字** 的全知短段，独立段落，且该章 scores 会扣 `pov_consistency` 分，须接受。
- 用户明确要求"允许主角每卷全胜"？→ 可执行，但在 `meta.yaml` 标注 `win_loss_override: true` 并在 volume-plan 里写明。

---

*红线是纪律，不是风格。风格由 prompt pack 控制；纪律由本文件控制。*
