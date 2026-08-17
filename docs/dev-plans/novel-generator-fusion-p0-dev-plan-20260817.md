# novel-generator 融合 P0 开发计划（五阶段）

**日期**: 2026-08-17
**上游分析**: `docs/research/novel-generator-skill-fusion-20260817.md`
**范围**: P0-1 mermaid 图解导出 / P0-2 章节契约回执核对 / P0-3 伏笔在飞存量观测。P1 教训前馈不在本次范围（需单独 A/B 设计）。

---

## 一、诊断（代码证据）

### P0-1 图解
- `story_bible_export.py:67 export_story_bible_to_disk` 写 6 个 markdown + raw JSON；纯读、不入 pipelines（docstring 明示 "do not wire it into pipelines.py"）。
- 数据源均已在手：`overview.relationships`（characters.md 已渲染成表格）、`overview.factions`、`world_spec.power_system.tiers`、`overview.volume_frontiers` + `overview.deferred_reveals`。
- 全库仅 2 处 mermaid（framework_self_closure / benchmark_capability_audit），书籍导出侧为零。

### P0-2 回执
- 契约下发面：`acceptance_contract.py` 把章级验收拆成 scene duty 注入写手 prompt；scene_cards 携带 `participants`（models.py:686 JSONB list）= 本章的角色声明。
- 回执缺口：没有任何确定性检查核对「声明的参与者是否真的在正文里出现并有戏」。`chapter_continuity_critic` 拿 participants 只做 LLM 侧连贯性参考（advisory）。
- **杀权风险面（决定架构）**：
  - `audit_chapter_prose` 的 `passed = not any(severity in {critical, high})`（deterministic_post_write_audit.py:85）；
  - 但 `reviews.py:9574 _deterministic_rewrite_violations` 把 report 的**全部 finding 不分严重度**转成语义修复指令；
  - `drafts.py:292` / `pipelines.py:1261` 的严重度过滤集是 `{critical, high, block, blocker}`。
  - 结论：往 `audit_chapter_prose` 加任何新 finding（含 medium）都会漏进 rewrite 修复通道 → **必须独立模块 + 独立 metadata key**。
- 落点：drafts.py 两个 audit 调用位（chapter-first ~10429、scene-assembly ~12448），均已有 try/except + metadata 盖戳惯例可镜像。

### P0-3 伏笔存量
- `foreshadowing.py:analyze_foreshadowing_density` 只算分布（acts/死区/逐条孤儿）；孤儿判定依赖每条线索自己的 `expected_payoff_by_chapter_number`，**没有全局在飞存量与最大龄期**——"每条都没超期但同时挂 14 个坑"不可见。
- 唯一调用方 `consistency.py:186 _check_foreshadowing_density` → ProjectConsistencyFinding（severity=medium，advisory，入项目级 ReviewReportModel，不进章级门禁）。
- ClueModel 字段齐备：`status / planted_in_chapter_number / actual_paid_off_chapter_number`（models.py:774-807）。
- 无既有单测覆盖 `analyze_foreshadowing_density`（tests/unit 无命中）。

## 二、计划（soft/additive、禁自伤、单一真源）

### P0-1：`story_bible_export.py` 新增 `diagrams.md`
- 新 renderer `_render_diagrams(...)`，输出 4 个 mermaid 块：
  1. 人物关系图 `graph TD`：节点=overview.characters（名+角色），边=overview.relationships（类型+强度）+ cast_spec.conflict_map（虚线边）
  2. 势力分布图 `graph LR`：factions ↔ 主角关系
  3. 等级体系图 `graph BT`：power_system.tiers 链，标注主角起始层
  4. 剧情时间线 `graph LR`：volume_frontiers 章程碑 + deferred_reveals 挂在揭示点
- 节点 id 全用生成的 ASCII（C0/F0/T0…），标签过 `_mermaid_label()` 消毒（引号/括号/换行/竖线，截断 24 字）——防坏语法。
- 任一图缺数据 → 该节渲染 `_(尚未生成)_` 文本，不输出残缺 mermaid。
- docstring 的 Output layout 同步补一行。**不动**其余 6 个文件的渲染（字节级不变）。

### P0-2：新模块 `chapter_contract_receipt.py`（回执=契约声明 vs 正文实际）
- 说明：相对上游分析文档的一处**设计修订**——v1 不要求写手新增自述输出（避开「全 optional 契约把解析失败变空成功」+ 新 LLM 输出必须真机冒烟两颗雷），改用 scene_cards 已有的 `participants` 声明作为「契约面」，正文作为「实际面」，确定性做差。写手自述可作 v2。
- 纯函数 `build_chapter_contract_receipt(*, chapter_text, chapter_number, scenes) -> ChapterContractReceipt`：
  - `missing_participants`：声明了但正文找不到（全名不中时，≥3 字中文名再试后 2 字，记录 matched_via）
  - `silent_participants`：名字出现，但没有任何一句同时含动作动词或对白标记（「“ " ：说 道）——出场了但没戏
  - `declared_locations`：从 scene.metadata_json 的 scene_contract/methodology_contract 里扫 location/location_name/setting/place 键；缺失则跳过该维度
  - `participant_coverage` 比率 + `to_dict()`
- 接线：drafts.py 两个 audit 位各加一段，镜像既有写法（独立 try/except + `logger.info`），盖 `chapter.metadata_json["contract_receipt_latest"]`。
- **杀权约束**：全库无任何消费方读该 key（实现后 grep 验证）→ 只留痕 + 日志，物理上无法阻断。遵守「新检测器只挣重生和留痕」。

### P0-3：`foreshadowing.py` 加在飞存量观测量
- `ForeshadowingDensityResult` 追加（带默认值，向后兼容）：`open_clue_count / max_open_clue_age_chapters / open_clue_codes(截 20) / overaged_clue_codes`。
- 模块常量 `OPEN_CLUE_SOFT_CAP = 5`、`OPEN_CLUE_MAX_AGE_CHAPTERS = 30`，注释标注**占位未标定**，待 `.distillation_private` 人类语料分位数校准。
- 在飞判定 = status ∉ {paid_off, cancelled} 且 actual 为 None 且 planted 已知；龄期 = total_chapters − planted。
- `consistency.py:_check_foreshadowing_density` 追加两类 finding（`foreshadowing_open_inventory` / `foreshadowing_overaged`），severity=medium，仅超阈值时产出——与既有死区/孤儿 finding 同级同通道，advisory。
- **不变式**：`balance_score` 计算零改动；未超阈值时输出与旧版逐字节一致。

## 三、验证（三层，缺一不可）

- **L1 单测**（3 个新文件）：
  - `test_foreshadowing_density.py`：在飞计数/龄期正确；**no-op 断言**（同输入下 balance_score 与既有字段和旧实现一致；低于阈值时 consistency 不产新 finding）
  - `test_chapter_contract_receipt.py`：missing/silent/覆盖率；空 scenes no-op；别名后缀匹配；对白/动作判定
  - `test_story_bible_diagrams.py`：四图语法骨架（``` mermaid 围栏配对、节点 id 无非法字符）；缺数据降级；**既有 6 文件渲染字节不变**
- **L2 回归**：跑 tests/unit 中 consistency / deterministic_post_write_audit / foreshadowing* / drafts 相关 + 新增；任何失败 `git stash` 干净树复跑归因。
- **L3 真机端到端**：live docker 栈，新脚本 `scripts/verify_novel_generator_fusion_p0_e2e.py`：对既有真书（读库）
  ① 导出 story-bible 断言 diagrams.md 生成且 mermaid 块完整；
  ② 取真章正文 + scene_cards 跑回执，打印 before/after（此前无回执 → 现在有结构化差）；
  ③ 取真 clue 行跑在飞存量，对照旧输出。
  零 token（全程无 LLM 调用，三条路径本身即纯确定性）；零副作用（只读 session、不 commit，结束核验 DB 无写入）。

## 四、文档与提交
- 本计划 + L3 结果记录入 docs/；README 索引登记。
- 提交拆分：`feat: story-bible 导出新增 mermaid 图解` / `feat: 章节契约回执核对(warn-only 留痕)` / `feat: 伏笔在飞存量观测(audit-only, 阈值未标定)`。
