# 开发方案 · 主角身份分裂：一本书出厂带两个名字（2026-08-25）

## 一、现象

真机 `custom-xuanhuan-1787625194`《逐出师门我颠大》，构思产物内部分裂：

| 产物 | 名字 |
|---|---|
| logline / story_spine / dramatic_question / selling_points | **阿灶** |
| premise / synopsis / world_premise / golden_finger / world_spec.factions | **姜燎** |
| 身份层（identity_manifest / cast_spec / book_spec / 快照） | **阿灶** |

读者可见的**上架简介与 premise 介绍的是「姜燎」，正文身份骨架是「阿灶」**。
全 metadata：阿灶 231 次、姜燎 34 次。`identity_manifest` 已 locked，
`aliases: []` —— 姜燎连别名都没登记。

## 二、根因（两处，各自独立可复现）

### R1 一致性检查的比对清单里没有散文产物

`book_design.py` 的主角一致性检查只遍历**结构化身份产物**：

```
story_spine / concept_contract.story_spine / hook_card
concept_contract.hook_card / identity_manifest
```

本书这五个**全是「阿灶」**，与 `expected_name` 一致 → `issues: []`，零检出。
**`premise` / `synopsis` / `promotional_brief` 不在清单里**，而「姜燎」恰恰只住在那里。

> ⚠️ **`advisory_codes` 不是检出回执。** 它是无条件计算的策略标志：
> ```python
> advisory = frozenset({"protagonist_identity_mismatch"}) if _identity_mismatch_is_advisory(metadata) else frozenset()
> ```
> 在「有没有检出」之前就算好了。本书报告里同时出现
> `advisory_codes=["protagonist_identity_mismatch"]` 和 `issues: []`，
> 意思是「**如果**有分歧，它会按 advisory 处理」，**不是**「抓到了分歧然后放行」。
> 我第一遍把它读成了检出回执，据此得出「门抓到了却放行」的错误结论。
> **一个长得像回执、实际是策略标志的字段，本身就是应当消除的歧义。**

`_identity_mismatch_is_advisory` 的判据（`f94dcd5d`，规范名是否出现在
logline/premise/synopsis 拼成的 blob 里）只决定**已检出分歧的严重度**，
本案根本没走到它。但它同时有一个独立缺陷：判据是**blob 任意命中**——
本书 logline 含「阿灶」即命中，看不见 premise/synopsis 两处被「姜燎」占着。
即使 R1 修好把散文纳入比对，这条判据仍会把它降成 advisory。**两条都要修。**

### R2 premise 名字抽不出来 → 已批准材料被静默丢出决策

`book_design.py:_protagonist_name_from_text` 依赖
`_CJK_NAME_RE = ^([一-鿿]{2,4})(?=[，,、：:\s]|$|是)` ——
**名字后面必须跟逗号/顿号/冒号/空格/「是」**。实测：

| premise 首句 | 抽取 |
|---|---|
| `姜燎，十九岁，被逐出灶口。` | `姜燎` ✅ |
| `姜燎十九岁，被逐出灶口。` | `''` ❌ |
| `少年姜燎十九岁，被逐出灶口。` | `''` ❌ |
| `沈砚舟在龙渊镇支起馄饨摊。` | `''` ❌ |

中文最自然的「名+年龄」「名+在」全部漏网。抽空后，
`planner.py:_persist_creation_protagonist_choice` 里
`chosen = ... or premise_name or existing_name or ...` 这一档落空，
**premise 这份已批准材料被静默丢出决策**，名字改由别处的 LLM 结果决定。

两本书同一指纹：`沈砚舟在龙渊镇…`→空→沈砚舟来自别处；
`姜燎十九岁…`→空→阿灶来自别处。

> ⚠️ **不要把 R2 当成「regex 不够贪」去放宽**。原 docstring 明写
> 「Fails CLOSED on purpose — a wrong name is worse than no name here」，
> 这个判断是对的：抽错名字比抽不到更坏。放宽 regex 是打地鼠换靶。

## 三、修复项

| # | 项 | 类型 | 风险 |
|---|---|---|---|
| **G1a** | 把 `premise` / `synopsis` 纳入主角一致性比对清单——散文产物用的名字与规范名不一致时**产出 issue**（当前零检出） | 门 | 中 |
| **G1b** | `_identity_mismatch_is_advisory` 从「blob 任意命中」改为**逐产物计数**：规范名在**非空产物中占比不足半数**时判身份分裂，不得 advisory | 判据 | 中 |
| **G1c** | `advisory_codes` 只在真有对应 issue 时才写入，消除「像回执实为策略标志」的歧义 | 清理 | 低 |
| **G2** | `protagonist_identity_mismatch` 非 advisory 时挣**一次重生**（重跑身份消解），不发杀权；无论结论如何**都落回执** | 门 | 中 |
| **G3** | R2 不放宽 regex，改为**失败留痕**：`_protagonist_name_from_text` 抽空时写 `premise_name_extraction_failed` 回执，使「材料被丢弃」从静默变成可查 | 留痕 | 低 |
| **G4** | 规范名与 premise 名不一致时，把 premise 名**登记进 `identity_manifest.aliases`**（不改写正文，不发明名字） | 接线 | 低 |

**不做**：机械改写 premise/synopsis 正文里的人名（会破坏已通过 AI 味与债务族检验的文案）。

## 四、验收口径（写在跑书之前）

| 项 | 硬指标 |
|---|---|
| 判据 | 逐产物记名表落回执；规范名占比可查 |
| 回执语义 | `advisory_codes` 非空 ⇒ 必有同码 issue（可确定性核对） |
| 分裂书 | 本书历史数据回放 → `protagonist_identity_mismatch` **不再是 advisory** |
| 不误伤 | 既有 4 条契约全不变：沈小禾 0/3→分裂；温迟 3/3→advisory；用户显式选名→永不 advisory；构思正文缺失→退回 advisory |
| 假阳性 | 产物**根本没提任何名字**时不得判分裂（只按非空且含具体人名的产物计数） |
| 抽取器 | 四条真机首句全部留痕，**不放宽匹配** |
| 真机 | 新书 logline/premise/synopsis 主角名一致，或分歧被判并重生一次 |

**不算数的**：某一本书碰巧一致。只有**回执链**（逐产物记名→判定→重生→一致）算证据。
