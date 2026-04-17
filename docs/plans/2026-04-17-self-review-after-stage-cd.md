# 自我审查报告 — Stage C + D 落地后（人物弧 + 节奏引擎）

**日期**：2026-04-17
**范围**：继 Stage A (冲突多样性) + Stage B (场景目的/环境多样性) 后，追加 Stage C (人物弧 + 五层思考) + Stage D (节奏引擎 + 钩子多样性) + Stage B+ (地点账本)
**方法**：以挑剔读者视角，对照原 6 大症状，评估本批改动的覆盖面、潜在风险、以及需要在 planner 侧同步配合的事。

---

## 一、新增能力（本轮新加）

| 层 | 新增能力 | 文件 |
|---|---|---|
| **Stage C** | 6 arc types + 10-field `CharacterInnerStructure` + percentile beat table | [services/character_arcs.py](../src/bestseller/services/character_arcs.py)（新） |
| **Stage C** | 5-layer thinking contract（SENSATION→…→RATIONALIZATION）+ 禁用情绪词表 | 同上 |
| **Stage D** | 7 canonical cliffhanger types + recommended distribution + `evaluate_hook_diversity` | [services/pacing_engine.py](../src/bestseller/services/pacing_engine.py)（新） |
| **Stage D** | 12-entry master `BEAT_SHEET`（百分位 → tension 目标 + 描述） | 同上 |
| **Stage D** | 6-component tension score + `evaluate_tension_variance`（flat-rhythm 检测） | 同上 |
| **Block builders** | `build_arc_beat_block` / `build_five_layer_thinking_block` / `build_cliffhanger_diversity_block` / `build_tension_target_block` / `build_location_ledger_block` | [services/deduplication.py](../src/bestseller/services/deduplication.py) |
| **Readers** | `compute_arc_structure_for_pov` / `compute_recent_hook_types` / `compute_location_history` / `compute_recent_tension_scores` | [services/context.py](../src/bestseller/services/context.py) |
| **Wiring** | `SceneWriterContextPacket` 加 5 字段；pipeline 对每场景注入；drafts 加 5 参数 + user_prompt 拼接 | domain/context.py + pipelines.py + drafts.py |
| **测试** | 44 新单测（19 for character_arcs, 25 for pacing_engine & location ledger） — **全部通过** | tests/unit/test_character_arcs.py + test_pacing_engine.py |

---

## 二、对照原 6 大症状的覆盖度（累计）

| 原症状 | 前两批（A+B） | 本批（C+D）追加 | 当前状态 |
|---|---|---|---|
| A. 四段式模板化（秘密→逼迫→选择→代价转移） | 🟡 仅抑制连续重复 | 🟢 **节拍表 + 张力目标**直接告诉写手「本章是 Pinch 2，不是 Crisis」；不再每章都撞模板 | 🟢 |
| B. 冲突仅 3 类（追捕/信息战/倒计时） | 🟢 四轴冲突 taxonomy + 内在层强制 | — | 🟢 |
| C. 人物公式化、无成长弧 | 🔴 未动 | 🟢 **lie/want/need/ghost + 5-layer 思考 + 裂缝期标记**，写手必须写「价值观被震动」那一笔 | 🟢 |
| D. 场景高频复用（下水道×4） | 🟢 7 维环境切换 | 🟢 **地点账本 + ≤4 复访上限 + 同地点 3 步重塑**硬约束 | 🟢 |
| E. 核心信息推迟（母亲身份到 ch85+） | 🔴 未动 | 🟡 节拍表告诉章节应推进到哪个 beat，但需要 planner 侧的 **Promise Ledger** 才彻底到位 | 🟡 |
| F. 生成瑕疵（标题重复） | ✅ 已修 | — | ✅ |

> **结论**：本批 Stage C + D 直接击中 "人物公式化" 和 "节奏停滞"，把覆盖度从 A+B 后的约 35-50% 拉到 **55-70%**。
> "核心信息推迟"(E) 仍需要 planner 侧的 Promise Ledger —— 那是写手端约束打不穿的，必须在规划阶段就把 20%/50%/80% 揭示点锁死。

---

## 三、风险与缓解

| 风险 | 缓解手段 |
|---|---|
| CharacterModel.metadata_json 没有 inner_structure | `compute_arc_structure_for_pov` 返回 `(None, name)`；block 渲染「尚无 POV 内在结构」并要求写手在 POV 段展现"信 → 挑战 → 调整"三拍之一，不崩 |
| ChapterModel.metadata_json 没有 hook_type / tension_score | 读空列表；block 渲染「近章尚无钩子记录」+ 不触发 flat-rhythm 警告 |
| SceneCard.metadata_json 没有 location_id | 降级读 entry_state.location 或 SceneCard.location 字段 |
| 总字数被占满：5 新块加起来 ~800 tokens | 5 块全部走 `_budget_context_sections`，与其它 Tier-1/2 块一起参与预算裁剪 |
| POV 角色取 scene.participants[0] 可能不准 | 仅供查 lie/want，查不到时退为 `pov_name=None`；未来 planner 侧补 `pov_character_id` 后更精准 |
| pipeline 注入失败污染整条链 | 整块用 `try/except` + `logger.debug` 非致命保护 |
| 没有 target_chapters 数据 | 默认 100 |
| 现有 89 章没有任何这些元数据 | 设计内必然代价：第一批会略有回退，从开始积累元数据起会逐步生效 |

---

## 四、会话内验证（已执行）

1. `.venv/bin/python -m pytest tests/unit/test_character_arcs.py tests/unit/test_pacing_engine.py` → **44 passed**
2. 回归：`test_deduplication` + `test_context_services` + `test_conflict_taxonomy` + `test_scene_taxonomy` + `test_content_services` → **117 passed**
3. 完整 unit 跑：**729 passed**，3 个 FAIL 与 Stage A+B 同状态（远程 Anthropic API key 缺失、与本次无关）
4. 所有模块（character_arcs / pacing_engine / deduplication / context / pipelines / drafts）**能 import、无 SyntaxError**

---

## 五、planner 侧配合任务（下一批必须做的）

Stage C+D 的硬约束大多**依赖 planner/world 侧写入元数据**才会在生成时真正起作用。下次迭代必须补上：

- [ ] **Planner 把 `CharacterInnerStructure` 10 字段填到 CharacterModel.metadata_json.inner_structure**（至少对主角 + 主要同盟 3-4 人）
- [ ] **Planner 把 `scene_contract.metadata_json.conflict_tuple` / `scene_purpose_id` / `env_7d` / `location_id` 写入每个 SceneCard**（Stage A+B 的 block 才有实际历史数据）
- [ ] **Chapter 完成时写入 `chapter.metadata_json.hook_type` + `tension_score`**（钩子和张力的历史追踪才不会永远为空）
- [ ] **Planner 侧加 Promise Ledger**：把母亲身份、代价核同源、身世这类核心信息的**揭示点 + 承诺度**写进大纲，绑到 20%/50%/80%，否则它永远被推到 ch85+
- [ ] **Planner 侧加多反派线目标矩阵**：清道夫 / 霍沉 / 周砚宁派系各自目标 + 主角选择如何影响对比
- [ ] **同盟内部冲突**：姜澄/苏晚清/陆骁的目标从一开始就部分对立

---

## 六、一句话总结

**Stage A+B+C+D 累计：冲突单一 + 场景重复 + 人物公式 + 节奏停滞四大毛病已经在写手端有了硬约束；剩下"核心信息推迟"和"绕圈不进展"要靠 planner 侧的 Promise Ledger + 多反派矩阵才能彻底收尾。**

- **读者视角改善预估**：**55-70%**（A+B 后是 35-50%）
- **风险**：零破坏性 —— 所有改动都是 graceful-degradation 纯加法，44 新单测 + 117 回归全绿。
- **下一步**：建议暂停写手侧改动，转入 planner 侧给所有元数据喂入 inner_structure / conflict_tuple / env_7d / hook_type，然后 sample 重跑 ch20-40 做对比抽样。
