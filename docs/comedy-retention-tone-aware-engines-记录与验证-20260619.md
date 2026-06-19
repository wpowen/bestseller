# 喜剧留存崩塌 → 三引擎题材感知 — 记录与验证（2026-06-19）

## 触发 / 读者症状
新书《福星甩不掉》（社畜摆烂·气运团宠·反向喜剧，slug `fuxing-shuaibudiao`，pack 已正确路由
`shezhu-bailan-comedy`）前几章**抓不住人、留存≈0**。读者视角：
- 不好笑不治愈：冷脸 AI 客服念系统规则 + 倒计时 + 「我他妈一个被优化的废物」，焦虑苦,零笑点零暖点；
- 重复：每章「系统派工单→甩福气→反噬成更多好运→弹下一个工单倒计时」同构；
- ch3 收尾是「赢坑老赵/输坑孙叔，两边死路」的惊悚两难——轻喜剧最忌。

## 根因（设计/架构层题材盲，碾压正确的喜剧 pack）
喜剧 pack `shezhu-bailan-comedy.yaml` 指引完全正确（章末「低压力高期待」/每章≥3笑点+1温度瞬间/
**严禁2/3前高强度冲突**），但三个题材盲的张力引擎把它全覆盖：

1. **开篇原型池**：`diversity_budget.next_opening(pool=None)` 默认 = 整个 `OpeningArchetype` 枚举
   （[invariants.py](../src/bestseller/services/invariants.py)），张力簇主导且 **HUMILIATION 排第一** →
   前几章全开在「屈辱」。写手指令 [prompt_constructor.py:89](../src/bestseller/services/prompt_constructor.py)
   「第一个场景必须以屈辱开局，主角公开受辱」直接反治愈喜剧。
2. **钩子/悬念**：cliffhanger 默认全枚举 + 章 hook_type=countdown/urgent_crisis，倒计时威胁收尾，反「低压力高期待」。
3. **黄金三章可见损失回填**：[pipelines.py `_backfill_golden_three_visible_losses`](../src/bestseller/services/pipelines.py)
   对每本书前3章硬拼探案词「否则主角会失去本章关键证据，对手当场扩大优势」——喜剧无「证据/对手」。

→ 写手忠实渲染「屈辱开篇 + 倒计时危机 + 可见损失」骨架 → 焦虑解谜而非轻松团宠喜剧 → 留存崩。
本质与 prompt-pack 污染同源：框架留存/商业引擎按高张力男频题材写死，题材盲覆盖喜剧 pack。

## 修复（框架级，soft / additive，集中在 seed_invariants）
统一 tone 判定 + 低压力池（[invariants.py](../src/bestseller/services/invariants.py)）：
- `is_low_pressure_tone(prompt_pack_key, genre, sub_genre)`：pack 命中
  {shezhu-bailan-comedy, cozy-fantasy/litrpg/mystery, entertainment-sweet} 或 genre/sub 含
  喜剧/治愈/沙雕/摆烂/团宠/种田/日常/cozy/comedy/healing… 即低压力。
- `LOW_PRESSURE_OPENING_POOL` = (MUNDANE_DAY, CONTRAST, ENCOUNTER, SUDDEN_POWER, SECRET_REVEAL)——
  剔除屈辱/危机/退婚/驱逐/背叛/仪式打断/身份跌落。
- `LOW_PRESSURE_CLIFFHANGER_POLICY` = (NEW_CHARACTER, REVELATION, ENVIRONMENTAL, DECISION)——
  剔除 INTERNAL_CRISIS/INTRUSION/POWER_SHIFT 等高压。
- `seed_invariants` 新增 genre/sub_genre/prompt_pack_key 参数：低压力题材 `setdefault` 上述两池
  （**显式 override 永远优先**，非低压力题材保持原全枚举默认，零回归）。
- `_backfill_golden_three_visible_losses(low_pressure=)`：低压力题材改暖/喜剧式可见损失
  「否则这桩麻烦会外溢到身边人头上，主角越想撇清越被缠得更紧，场面越闹越尴尬」。
- 三处 seed_invariants 调用方（pipelines 主 seeding / planner fallback）+ backfill 调用方传 tone 上下文。

## 三层验证
- **L1**：`tests/unit/test_invariants.py::TestLowPressureTone`（分类器7例 + 喜剧暖池 + 张力全池 + 显式override优先）；
  `test_pipeline_services.py::test_visible_loss_backfill_is_tone_aware`。
- **L2**：test_invariants/test_diversity_budget/test_prompt_constructor（121）+ commercial_planning_readiness/
  golden_three_opening_wiring/opening_golden_chapter_gate/chapter_validator（65）+ backfill（1）= **187 passed，零回归**。
- **L3 真函数**：`.venv` 实跑 `seed_invariants(...shezhu-bailan-comedy)` → opening pool
  =[mundane_day,contrast,encounter,sudden_power,secret_reveal]、cliff=[new_character,revelation,environmental,decision]、
  HUMILIATION 已剔除；`choose_opening_archetype` ch1 → **mundane_day（日常被打破）**；
  backfill 低压力 → 暖式措辞；仙侠/悬疑保持全枚举。

## 影响面 / 后续
- 对所有**未来新建**喜剧/治愈/低压力书生效。
- 《福星甩不掉》已用旧（全张力）invariants seeded 完、ch1-12 已写——要让本书受益需：
  ①rebuild 部署 ②重新 seed 本书 invariants（低压池）③重写黄金三章。属部署+再生成，单列。
