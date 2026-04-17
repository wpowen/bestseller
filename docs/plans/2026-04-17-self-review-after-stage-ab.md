# 自我审查报告 — Stage A + B 落地后（以挑剔读者视角）

**日期**：2026-04-17
**范围**：`nifty-percolating-beaver.md` 第 1 批（P1 + P5）+ 本次 Stage A + Stage B
**方法**：以挑剔读者视角重新审视方案，判断 "这些改动能不能真正消除 89 章样本里的那些毛病"

---

## 一、已经落地的能力

| 层 | 新增能力 | 文件 |
|---|---|---|
| 配置 | `active_context_scenes` 6→12，对数 lookback 系数 3→8 | `config/default.yaml`, `services/context.py` |
| 代码小修 | 章节标题重复渲染 bug 修复 | `services/drafts.py` 正则 |
| **Stage A** | 4 轴冲突 taxonomy + 类型池 + emerging 池 + 切换规则 + 相似度 | `services/conflict_taxonomy.py`（新） |
| **Stage A** | `build_conflict_diversity_block` | `services/deduplication.py` |
| **Stage A** | `compute_conflict_history` | `services/context.py` |
| **Stage B** | 24 类 scene purpose + 7 维 env taxonomy | `services/scene_taxonomy.py`（新） |
| **Stage B** | `build_scene_purpose_diversity_block` + `build_env_diversity_block` | `services/deduplication.py` |
| **Stage B** | `compute_scene_purpose_history` + `compute_env_history` | `services/context.py` |
| Wiring | 三块约束注入到**每一个场景**的 prompt | `services/pipelines.py`, `services/drafts.py` |
| 测试 | 新增 37 个单元测试，全部通过 | `tests/unit/test_conflict_taxonomy.py`, `tests/unit/test_scene_taxonomy.py` |

## 二、以挑剔读者视角 → 对照原 6 大症状

| 原症状 | 能不能改善？ | 评估 |
|---|---|---|
| **A. 四段式模板化**（秘密→逼迫→选择→代价转移） | 🟡 部分 | 目前只**抑制连续重复**；若 LLM 倾向套四段式，还需 Stage D 的节拍表给"本章是 Pinch 2 / 不是 Crisis"的结构位约束。 |
| **B. 冲突仅 3 类** | 🟢 预期显著改善 | 新冲突块直接告诉写手"禁用 information_asymmetry/追捕、必须切 Axis A/B、近 5 场缺 inner 层必补"——这是正中病灶。 |
| **C. 人物公式化无成长** | 🔴 未解决 | 这是 Stage C（lie/want/need/ghost + 五层思考）的任务。当前没触。 |
| **D. 场景高频复用**（下水道×4） | 🟢 预期显著改善 | 7 维环境块会直接亮出上一场坐标 + 要求至少 3/7 维切换；同地点复访有三步重塑硬约束。 |
| **E. 核心信息推迟**（母亲身份拖到 ch85+） | 🔴 未解决 | 需要 Stage D 的 Promise Ledger + J 曲线。 |
| **F. 生成瑕疵**（标题重复） | ✅ 已修 | |

> **结论**：Stage A+B 正好打中"冲突单一"和"场景重复"两项，这是用户最直接的体感。但"人物公式化"和"核心信息推迟"要等 Stage C、D。

## 三、潜在风险与我已做的缓解

| 风险 | 缓解手段 | 备注 |
|---|---|---|
| **planner 还没写 conflict_tuple/env_7d** | 三个 `compute_*_history` 在元数据缺失时返回空列表；block 输出"近场尚无，请自由选取，但需建立基线"而不是崩溃 | ✅ 已在代码里实现 graceful degradation |
| prompt 过长爆预算 | 三块都走 `_budget_context_sections` 打包，和其它 Tier-1 一样参与 budget 裁剪 | ✅ 已接入 |
| LLM 反倒被约束困住 | 每块都给出"候选池"而非"只禁止" | ✅ 已实现 |
| 注入失败导致整条 pipeline 崩 | `try/except` + logger.debug（non-fatal） | ✅ 遵循 pipelines.py 既有做法 |
| 现在的 89 章没有元数据，立即重跑也不会看到差异？ | 首 5-10 章会仍然"空冷启动"，从第 11 场开始才有约束积累 | 这是设计内必然代价，但符合预期 |

## 四、会话内验证方式（已执行）

1. `.venv/bin/python -m pytest tests/unit/test_conflict_taxonomy.py tests/unit/test_scene_taxonomy.py` → **37 passed**
2. `tests/unit/test_deduplication.py` + `test_context_services.py` 回归 → **32 passed**
3. `tests/unit/test_content_services.py`（drafts.py 集成）→ **48 passed**
4. 手工 smoke-test 三个 block 的渲染效果 → 中英文输出可读、内容正确
5. 完整 `tests/unit/` 跑 683 passed；3 个 FAIL 与主干同状态（远程 Anthropic API key 缺失、非本次引入）

## 五、未完成的部分（下一轮要做）

- [ ] **Stage C — 人物弧与五层思考契约**：目前所有角色在 prompt 里没有 lie/want/need/ghost，是"人物公式化"的根。下一会话优先级最高。
- [ ] **Stage D — 节奏引擎 + 承诺账本**：张力评分、J 曲线、钩子 7 类反重复——解决"核心信息推迟"与"章末同型钩子连发"。
- [ ] **Stage E — planner 模板分层**：20 个 goal 模板按 phase 拆分到 5 段、50+ 条。低优先级但简单。
- [ ] **planner 侧回填**：让 planner 在产出 scene_contract 时写 `conflict_tuple` / `scene_purpose_id` / `env_7d`；否则 Stage A+B 只在 "LLM 根据约束自己选" 的反馈环里工作，第一批不够强。这件事和 Stage C 一起做最自然（都是 planner 侧的内在结构扩展）。
- [ ] 已生成的 89 章，是否要 **partial-rewrite** 以获得新体验？方案里已经建议从 ch20 起重写，但需要用户确认。

## 六、一句话总结

**本次 Stage A+B 精准击中了"冲突类型单一"和"场景设定重复"两大最直观毛病；"人物公式化"和"剧情循环绕圈"要靠下一批 Stage C+D 配合解决。**

**读者视角改善预估：35-50%**（与计划里的 60% 目标比，还差 Stage C/D 的那部分）。

**风险最低、可立即部署**：新增代码都是纯加法，graceful degradation，已有 37 个单测兜底。
