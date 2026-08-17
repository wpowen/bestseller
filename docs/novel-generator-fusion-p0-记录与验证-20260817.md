# novel-generator 融合 P0 — 记录与验证（2026-08-17）

**上游**: [融合分析](research/novel-generator-skill-fusion-20260817.md) · [开发计划](dev-plans/novel-generator-fusion-p0-dev-plan-20260817.md)
**标准**: [开发与验证标准](开发与验证标准-feature-lifecycle-20260618.md)（五阶段 + 三层验证）

## 交付物

| # | 改动 | 文件 | 性质 |
|---|------|------|------|
| P0-1 | story-bible 导出新增 `diagrams.md`（人物关系/势力分布/等级体系/剧情时间线 4 张 mermaid 图） | `story_bible_export.py`（+`_render_diagrams`/`_mermaid_label`） | 纯导出侧，additive |
| P0-2 | 章节契约回执：scene_cards 声明的 participants/locations vs 正文实际，确定性对账 | 新模块 `chapter_contract_receipt.py`；`drafts.py` 两个 audit 位接线 | **warn-only 留痕**，盖 `contract_receipt_latest` |
| P0-3 | 伏笔在飞存量观测：`open_clue_count / max_open_clue_age_chapters / open_clue_codes / overaged_clue_codes` | `foreshadowing.py` + `consistency.py` 两类 advisory finding | audit-only，阈值 5/30 **占位未标定** |

## 关键架构决策（含一处对上游分析的修订）

1. **回执不进 `audit_chapter_prose`**。取证：`reviews.py:_deterministic_rewrite_violations` 会把该报告**全部** finding（不分严重度）转成语义修复指令——加 medium finding 也会漏杀权进重写通道。故回执独立成模块 + 独立 metadata key，全库 grep 确认零消费方读 `contract_receipt_latest`（物理上无法阻断）。遵守「新检测器只挣重生和留痕」。
2. **v1 不改写手输出契约**。上游分析原设想"写手自述回执"；实做改为用 scene_cards 已有 `participants` 声明当契约面（避开「全 optional 契约把解析失败变空成功」+「新 LLM 输出必须真机冒烟」两颗雷）。写手自述留作 v2。
3. **P0-3 不动 `balance_score`**——新观测量不入分，历史读数保持可比。

## 三层验证结果

### L1 单测（20 个，全过）
- `test_foreshadowing_density.py`：在飞计数/龄期/聚合压力（每条线索单看都合规但同时挂 8 条 → 旧口径 0 报警、新口径可见）/**no-op 断言**（legacy 字段与 orphan 语义不变；total_chapters<1 早退新字段守默认）
- `test_chapter_contract_receipt.py`：missing/silent/覆盖率/别名括号/去姓氏回退/双字名不回退/位置对账/空 scenes 与空正文零捏造
- `test_story_bible_diagrams.py`：4 围栏配对/缺数据降级为占位不输出残缺 mermaid/volume_plan 回退/恶意字符消毒/标签截断

### L2 回归（95 相关 + 109 drafts 路径，全过，无需 stash 归因）
consistency / deterministic_post_write_audit / foreshadowing×2 / story_bible ×2 + chapter_first/draft/rewrite 全套。

### L3 真机端到端（live docker 栈，`scripts/verify_novel_generator_fusion_p0_e2e.py`）
对象：真书 `custom-xuanhuan-1786703729`（50 章现稿）。before=git stash 旧代码，after=新代码，同库同书对照：

```
[compare] premise/world/characters/volume-plan/plot-arcs/writing-profile.md: 全部 IDENTICAL（字节级）
[compare] diagrams.md: 4 mermaid block(s), fences closed=True   （仅 after 存在）
[compare] foreshadowing legacy fields (9): IDENTICAL
[compare] foreshadowing new fields: [max_open_clue_age_chapters, open_clue_codes, open_clue_count, overaged_clue_codes]
[compare] receipt(after): declared=1 missing=[] silent=[] clean=True
[VERDICT] PASS
```

- **零 token**：三条路径均纯确定性，llm_runs 计数前后 98880 不变。
- **零副作用**：projects/chapters/chapter_draft_versions/clues/llm_runs 五表计数前后一致；session 只读未 commit。
- **真书读数**：8 条 clue 中 2 条在飞、最老龄期 49 章（>30 上限）→ 新观测量在真数据上立即给出此前不可见的读数。

### 真机自测抓到并修掉的 bug（1 个）
首轮 after 阶段：ch50 声明参与者 `沈絮(阿缨)` 被误报 missing——声明面带括号别名而正文只用其中一个面。修复：`_ALIAS_SPLIT` 按 `()（）/、·` 拆候选面，matched_via 增加 `alias` 档；回归用例已固化（`test_alias_annotation_in_declaration_matches_either_surface`）。

## 后续（不在本次范围）
- 阈值标定：用 `.distillation_private` 人类语料跑在飞存量/龄期分位数，替换 5/30 占位。
- 回执数据积累一本书后评估 silent_participants 的信噪比，再议是否升格。
- P1 教训前馈（证据引文 ≤3 条 + 退休机制 + A/B）单独立项。
