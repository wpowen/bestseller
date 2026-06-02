# 反常识钩子引擎 — Docker 构建后整体能力验证报告

> 版本：2026-06-01 · 验证对象：重建后的运行栈（api/web/worker×10/scheduler/db/redis 全 healthy）
> 验证方式：镜像内服务级冒烟 + 活体 HTTP API 全量扫描 + 真实生产函数（gate + propagation）运行级验证 + DB/日志核查
> 结论：**整体能力通过（PASS with findings）。核心链路在重建镜像中真实可用；发现 2 个中等 + 2 个低优先级问题，均不阻塞使用，建议下一轮修复。**

---

## 1. 验证结论速览

| 维度 | 结果 |
|---|---|
| 重建镜像是否含引擎 | ✅ 容器内加载 8 机制、`hook_engine` 配置齐全 |
| 引擎服务级是否可用 | ✅ 确定性生成、0 病句、各题材 top1 全部 ≥ 阈值 |
| 活体 HTTP API | ✅ `/api/writing-presets` 返回 57 题材 × 共 228 个候选，全部干净 |
| Gate（评分+门禁+回炉） | ✅ 真实 `_run_hook_strength_gate` 运行通过，stash 正确 |
| 传播（book/world/volume 注入） | ✅ 真实 `apply_hook_to_*` 全部命中 |
| 运行日志 | ✅ 无 hook 相关报错 |
| 发现的问题 | ⚠️ F1/F2 中等、F3/F4 低、F5 待补 |

---

## 2. 已验证 OK 的能力（含证据）

### 2.1 镜像内引擎（`docker exec bestseller-api-1`）
```
hook_engine: {enabled:True, min_h_norm:30.0, candidate_count:6,
              quickstart_candidate_count:4, rank_weight_*:0.62/0.28/0.10}
mechanisms: 8  [death_grows, forced_loss, emotion_value, hide_anti_trope,
                misunderstanding, fourth_disaster, rule_horror, profession_reversal]
broken one_liners: 0
都市 topH=32.0 pass=True | 玄幻 topH=34.56 pass=True | 悬疑 topH=36.0 pass=True
```

### 2.2 活体 HTTP API 全量扫描（`GET http://localhost:8787/api/writing-presets`，HTTP 200）
对返回的 `hook_candidates` 做全量统计（57 题材，228 候选）：
```
empty_genres        = 0
broken_grammar      = 0          （"必须必须 / 却必须越 / 却必须最"全部为 0）
top_below_threshold = 0          （所有题材 top1 H_norm ≥ 30）
over_len_one_liners = 0
top verdict 分布     = {expand:19, review:38}
top H_norm           = min 32.0 / mean 56.5 / max 100.0
```

### 2.3 Gate + 传播（运行级，调用真实生产函数，非重写）
用真实 `_run_hook_strength_gate` + `apply_hook_to_book_spec/world_spec/volume_plan`：
```
=== GATE ===   verdict=pass h_norm=36.0 passed=True
stashed hook_spec=True | hook_strength_gate=True
one_liner: 主角想证明清白，偏偏必须遵守反直觉规则，违背常识反而能活；赢来真相碎片，也付出同伴牺牲风险。
=== PROPAGATION ===
book.logline == one_liner         : True
book.series_engine.reader_promise : True
world hook rule present            : True  ['hook_rule_horror']
world.power_system.hard_limits     : 非空
vol1 anti_commonsense_escalation_axis : 规则层级
vol1 cost_paid                     : 同伴牺牲风险
vol2 cost_paid（保留+追加）         : 既有代价；同伴牺牲风险
```
**结论**：命题→大纲的字段穿透在运行时真实生效（logline、世界规则、hard_limits、逐卷升级轴、逐卷代价追加全部命中）。

### 2.4 环境
- 7 类服务 + 10 worker 全 healthy；PyYAML 已随镜像；0 个新迁移（用 `projects.metadata` JSONB 列）；日志无 hook 报错。

---

## 3. 发现的问题（按严重度）

### F1【中】reject 硬门禁实际上不可达——repair 会把垃圾命题"救活"
**现象**：构造极弱命题（`base_desire="喝水", reversal="偶尔会渴"`，其余全空；甚至 `"a"→"b"`），原始评分 `h_norm=0.11 verdict=reject`，但经过 gate 内的 `repair_hook_spec_once` 一次补强后 → `h_norm=40.82 verdict=review`，于是 **reject 检查（`verdict=="reject"` → 抛 `PlannerFallbackError`）永不触发**。
```
[trivial]   orig h=0.11(reject) -> repaired h=40.82(review)  reject-block fires? False
[empty_ish] orig h=0.11(reject) -> repaired h=40.82(review)  reject-block fires? False
```
**根因**：`repair_hook_spec_once` 对任意输入都注入**固定模板字段**（method/time/ban 限制、`同一对象重复触发收益衰减` 等反作弊、`权限提升/真相碎片` 奖励、`代价升级/误解升级` 升级轴）。评分按字段数量计 C/P/R/E，注入即拉满到 ~40。
**影响**：原计划"H_norm<15 不得进入写作"的红线对**用户自带命题**形同虚设；任何垃圾命题都会被强行修复通过。
**建议**：① reject 判定应基于 **repair 前**的分数（pre-repair verdict==reject 即拦），或 ② repair 仅对**引擎自生成**候选启用、对**用户自带 HookSpec**不自动注入，或 ③ 给 repair 设"最多把 reject 抬到 seed，不直接抬过 review"的上限。

### F2【中】repair/评分可被"结构注入"刷分，且空壳字段会向下传播
**现象**：F1 中 `"a"→"b"` 被修复成 h_norm=40.82，其分数完全来自注入的**与故事无关的模板字段**。这些 canned 限制/代价随后会经 `apply_hook_to_world_spec/volume_plan` 落到世界规则与卷代价里。
**根因**：H_norm 度量的是字段**数量/关键词命中**，不是语义质量；repair 通过加字段直接 game 了自己的指标。与 P1-4（free-text 抽取为正则粗估）同源。
**影响**：弱命题不会被拦，反而被"洗"成带通用约束的"合格"钩子，污染下游世界观/卷代价。
**建议**：repair 注入的字段打 `synthesized=true` 标记并在传播时降权/不外显；或对用户自带命题改用 LLM 语义抽取后再评分（对应原计划 P1-4 的 LLM 方案）。

### F3【低】跨题材一句话钩子大量逐字重复（模板化）
**现象**：228 个候选里有 **59 条 `one_liner` 在不同题材间完全逐字相同**，最高一句跨 6 个题材复用：
> "主角想活下去，偏偏越接近死亡或失败，越能获得真正的成长资源；赢来隐藏身份解锁，也付出寿命折损。"
**根因**：one_liner 为确定性模板拼接；去重 `duplicate_risk_fn` 只在**单题材生成内**比对（premise/title/history），catalog 跨题材未做去重。
**影响**：quickstart 多题材并排展示时，"让用户点进来"的新鲜感打折。
**建议**：① 目录层做跨题材 one_liner 去重；② 或落地原计划 Beta 的 LLM 扩写（同机制不同题材产出差异化文案）。

### F4【低】机制多样性偏斜
**现象**：57 题材 top1 的机制分布：`death_grows 15 / rule_horror 10 / profession_reversal 9 / emotion_value 9 / fourth_disaster 8 / misunderstanding 5 / hide_anti_trope 1 / forced_loss 0`。`forced_loss`（限制消费/亏钱流，调研报告重点机制之一）**从未成为任一题材的 top1**。
**根因**：`forced_loss` 的 reversal/奖励组合评分偏低 + 题材匹配未命中，被 combined_rank 压下去。
**影响**：调研里很强的"限制消费/迪化"类机制曝光不足。
**建议**：检查 `forced_loss` 的 `genres` 标签与评分参数；必要时在目录层做机制配额/轮转，保证机制覆盖面。

### F5【待补·非缺陷】全 LLM 驱动的 foundation_plan 尚未跑过一次真机冒烟
**说明**：本次用确定性方式验证了 gate + propagation 的真实生产函数（无需 LLM）。但"LLM 生成 book/world/volume → planner 把 hook block 注入 prompt → 产出真实成品"的完整链路，重建后**还没有跑过一个真实项目**（DB 中 47 个项目均为 5-31 旧数据，无 hook_spec）。
**建议**：用现有 DeepSeek/MiniMax key 跑一个 30 章 quickstart，验完后用附录脚本核查产出项目 `metadata.hook_spec` + book_spec.logline 一致性（即 L3 穿透审计的一次手动执行）。

---

## 4. 修复优先级建议

| 编号 | 问题 | 优先级 | 方向 |
|---|---|---|---|
| F1 | reject 门禁不可达 | 中（建议本轮修） | reject 用 pre-repair 分数判定 / repair 不作用于用户自带 spec |
| F2 | 评分可被结构注入刷分 | 中 | repair 字段打标降权；用户命题走 LLM 语义评分 |
| F3 | 跨题材钩子逐字重复 | 低 | 目录层跨题材去重 / LLM 扩写 |
| F4 | 机制多样性偏斜 | 低 | 修 forced_loss 标签与评分 / 机制配额 |
| F5 | 全链路真机冒烟 | 待办 | 跑 1 个 30 章项目 + 穿透核查 |

**建议落地顺序**：F1 → F2 →（一次真机冒烟 F5）→ F3 → F4。F1/F2 同源（评分语义性），建议合并处理。

---

## 5. 一句话总评
**重建镜像后引擎真实可用、传播真实生效、无运行错误——核心能力 PASS。** 主要遗留是"评分只看结构不看语义"带来的两个连带问题：reject 红线被 repair 架空（F1）、弱命题被注入空壳字段刷分并污染下游（F2）；这两点对"用户自带弱命题"的场景影响最大，建议本轮一并修复。引擎自生成的主流程（mechanism 驱动）质量良好，可放心使用。

---

# 附录 C · 全链路真机验证（live end-to-end）

> 2026-06-01 通过生产路径 `POST /api/tasks/quickstart` 触发真实 LLM 生成（MiniMax-M3），
> 题材 `apocalypse-rule`（末日规则求生流），chapter_count=3，未预设 hook（验证引擎自动生成路径）。
> 项目 slug：`apocalypse-rule-1780305533`，task：`ae13b15f-…`。

## C.1 已实时确认（生产环境，真 LLM）

| 链路环节 | 证据 | 结论 |
|---|---|---|
| 1. 触发 | HTTP 202，autowrite 任务入队，worker 接管 | ✅ |
| 2. 构思（conception，多步真 LLM） | 走完 story_architect→commercial→market→character→world→review→creative_exploration→finalize，无错 | ✅ |
| 3. **钩子自动生成** | 项目 `metadata.hook_spec` 落地，机制 `fourth_disaster` | ✅ |
| 4. **强度门禁 gate** | `hook_strength_gate`: h_norm=**96.0**, verdict=**pass** | ✅ |
| 5. **传播→book_spec** | `book_spec.logline` **逐字等于** hook one_liner；`series_engine.reader_promise` 同步 | ✅ |
| 6. **传播→world_spec** | `rules` 含 `hook_fourth_disaster`；`power_system.hard_limits` 实装钩子限制+反作弊（"禁止用最直观捷径绕过代价；任务奖励不能凭空生成；复活或重试必须消耗世界资源；玩家行动受任务和资源上限约束；…NPC 不能失去主体性"） | ✅ |
| 7. 运行健康 | api/worker×10 全程无 hook 相关报错 | ✅ |

实测 hook 一句话钩子（生产产物）：
> 主角想抵抗入侵，偏偏必须利用不可控玩家或观众的混乱，才能拯救秩序；赢来异界资源、权限提升，也付出规则污染；每次成功都会留下公开误解或资源债务。

## C.2 全链路中再次确认 F2（repair 样板话外泄到成品）
上面这句**生产 logline** 末尾的 "；每次成功都会留下公开误解或资源债务" 正是 `repair_hook_spec_once` 注入的**通用样板代价**（非本书特有）。说明附录 B 的 F2 不仅是理论缺陷，已在真机产物里出现：repair 注入的空壳文本会**逐字写进对外卖点 logline**。**建议把 F2 提为本轮必修。**

## C.3 仍在后台采集（M3 吞吐慢，foundation 阶段 ~25min+）
- `volume_plan` 的逐卷 `anti_commonsense_escalation_axis` / `volume_resolution.cost_paid` 追加；
- 章节契约 `conflict_stakes / conflict_buffs` 是否呼应钩子代价/限制；
- 3 章正文生成 + 导出。

> 这两处传播用的是 `apply_hook_to_volume_plan` / `hook_outline_extra_constraints`，已在附录 2.3 容器内运行级验证通过（vol1 escalation_axis=规则层级、vol1/vol2 cost_paid 追加正确）。本次真机 live 采集仍在后台监控脚本 `/tmp/hook_e2e2.out` 中进行，待 task 终态自动落最终巡检。

## C.4 全链路阶段性结论
**核心链路（构思→钩子→门禁→book/world 传播）在生产环境真机跑通，零报错——全链路 PASS。** 唯一需注意的是 F2（repair 样板话进入对外 logline），已在真机复现，建议本轮修复。volume/chapter 两处传播机制已在容器内验证通过，真机 live 采集后台进行中。
