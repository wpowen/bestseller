# Invariants — 永不违反

> 任一条目违反即停机回退。critic 发现违反应直接判不合格，不论其他维度分数多高。

## Mode B（写作）红线

- ❌ **每章字数不得低于本作品的 `platform_profile.pacing_preference.chapter_word_count` 下限**——七猫=2500，起点=3000，番茄=2000，未指定平台时默认 5000。低于即 `status: rework`；从场景 / 内心 / 对白扩写，不得以形容词灌水。
- ❌ 小说正文**不得写入 `output/ai-generated/{slug}/` 以外的任何路径**。
- ❌ 已有的 canon facts **不可修改**；只能用更高 `valid_from_chapter` 的 `supersedes` 条目覆盖。
- ❌ 任何角色**不得知道**在后续章节才揭示的事（知识单调性）。
- ❌ 同一 scene 的 rewrite **不得超过 2 次**；第 3 次即 `accept_on_stall`。
- ❌ `target_chapters > 50` **必须**写 `story-bible/act-plan.md`。
- ❌ `volumes > 3` **必须**写 `story-bible/world-expansion.md` 并跟踪 DeferredReveal。
- ❌ 主角**不得每卷全胜**；必须符合 [planning.md § Win/Loss Rhythm](planning.md)。
- ❌ 不得跳过规划阶段直接写正文（即 story-bible 未建立时不写 ch-001）。
- ❌ 不得以 "穿越 / 系统 / 金手指" 文本模式替代章节内容。

## Mode B（高敏感位置）红线

> 数据源：[config/chapter_position_profiles.yaml](../../../config/chapter_position_profiles.yaml)

- ❌ **第一章 (first_chapter) 8 项 hard_gates 任一失败即 `status: rework`**——不论其他评分多高。具体：
  - 主角必须在前 100 字进入聚光灯
  - 前 200 字必须有可感冲突
  - 前 500 字主角必须有"心率"外显（pulse_words 词表匹配）
  - 章末前必须完成一次可感小爽点
  - 不允许心理独白型信息倒斗（单段 > 150 字内心戏含多条背景）
  - 不允许冷面工具人主角（pulse_words 频率 < 1/300 字）
  - 不允许私设术语堆叠（首次出现 ≥ 6）
  - 章末 150 字内必须有勾子
- ❌ **前 3 章 (opening_window) 不得出现心理独白型信息倒斗**——单段 > 150 字内心戏 + 含 ≥ 2 条背景设定 / 阴谋分析 / 旧案回忆。
- ❌ **前 3 章 (golden_three_window) 不得副线抢戏**——副线场景 > 1 个或副线字数 > 1500。
- ❌ **前 10 章 (extended_opening_window) 主角不得连续失败**——必须有至少 1 次可感成长 / 收益 / 胜利节点。
- ❌ **卷末章 (volume_climax) volume_climax 事件必须在本章落地**——不得推迟。
- ❌ **首次能力觉醒章 (first_powerup_reveal) 必须配代价账 + 旁观反应**——缺一不可。
- ❌ **拒稿整改时不得 LLM 现编 repair 策略**——必须按 [config/rejection_repair_playbook.yaml](../../../config/rejection_repair_playbook.yaml) 的 `repair_actions` 顺序执行。

## Mode B（连续性）红线

> 数据源：[chapter_seam](../../../src/bestseller/services/chapter_seam.py) ·
>        [deduplication.detect_intra_chapter_stitched_drafts](../../../src/bestseller/services/deduplication.py) ·
>        [character_alias_canon](../../../src/bestseller/services/character_alias_canon.py) ·
>        [stance_continuity_docs](../../../src/bestseller/services/stance_continuity_docs.py)

- ❌ **章节断点连贯性**（第 2 章起）：前一章末尾的 open thread（location / participant / immediate_threat / body_state / unanswered_question）必须在本章开篇 300 字内得到承接、明示时间跳跃、明示空间转场，或被屏幕上解决。silent_drop = `status: rework`。修复：调 [chapter_seam.build_seam_bridge_repair_prompt](../../../src/bestseller/services/chapter_seam.py) 让 editor 插入 100-300 字 bridge 段落。
- ❌ **同章拼接稿检测**：同一章内若出现 ≥ 1 对事件签名相似度 ≥ 0.62 且共享 ≥ 2 命名参与者 + 1 关键道具的段落，视为双稿拼接。**禁止合并**（合并会留下道具/动作矛盾），必须**二选一删另一段**。修复：调 [deduplication.build_stitched_draft_repair_prompt](../../../src/bestseller/services/deduplication.py)。
- ❌ **角色名 Canon**：项目级 `story-bible/character-aliases.yaml` 是人名单一事实源。文本中出现的 2-3 字 Han 名变体必须在 canon 中登记为某 `canonical` 或 `aliases`；落入 `forbidden_collisions` 集合的名字（如 周元 ↔ 周元青）必须替换或显式拆分。
- ❌ **角色立场逆转必须有触发事件**：连续两次 character_snapshot 中若任一关系从 hostile → friendly 或反向（trust_map 从 ≤ -0.3 跳到 ≥ +0.3），过渡区间内的 `knowledge/timeline.md` 必须登记一条 reconciliation / betrayal / coercion / rescue / debt_event 事件。否则 `severity=violation`，必须补事件或回退立场。

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
