# R4 / R5 / R6 实现方案(留待 E2E 环境 · 大模型验证)— 2026-06-05

> 这三项都**直接改写手提示词/生成管线 → 影响生成质量**。项目历史(memory:
> methodology-pipeline-quality-regression)证明:对提示词/管线的**无验证改动**曾导致
> 2026-05 末质量崩塌。故按用户决策:**不盲改**,在 E2E 环境(app 凭证+DB+可达判官模型)
> 边改边用稳定判官验证。本文把调研结论固化为可直接执行的方案,避免到时重新摸排。
>
> 前置:判官层题材中立化 + R2 判官模型可升档 已落地并提交(commit ef796ef)。
> E2E 入口脚本:`scripts/e2e_judge_genre_neutrality.py`(题材中立验证,可扩为收敛验证)。

---

## R4 — writer 提示词分层预算(防 75 块淹没信号)

**现状(已勘)**:`drafts.py` 多处装配 writer system_prompt(行 3098 / 5529 / 5587 / 7843 / 8990);
user prompt 经 `prompt_compactor.compact_user_prompt`(已有 lean-strip + 占位去噪)。审计(§1.2)结论:
75+ 块无差别拼接、无预算裁剪 → 本章内容与高价值方法论被淹没。

**方案(分层预算)**:
1. 给每个注入块打**优先级 tier**:
   - P0(永远保留):本章 scene cards / acceptance_contract / `render_ranking_self_check_block` / 禁忌词。
   - P1(高):方法论 framing(quality_levers / choreography / 风格锚 / 章节签名)。
   - P2(低):craft-theory / reference-corpora dump(已被 lean-strip 处理,作为兜底)。
2. 设 token 预算上限(按写手模型 max_tokens 的 ~55% 给 system,留足正文)。
3. 超预算时**从 P2→P1 逐级丢弃**,P0 绝不丢;`log` 丢弃了哪些块(no silent truncation)。
4. 把"方法论 framing 前置于约束 framing"(审计 §1.3:约束语气盖过方法论)。

**实现位置**:新增 `services/prompt_budget.py`(纯函数:`(blocks_with_tier, budget)→kept_blocks+dropped_log`),
在 drafts.py 各 system_prompt 装配点收口调用。**不改块内容,只排序+裁剪**。

**E2E 验证**:同一 ch1,开/关预算各生成一次 → 比较(a)system prompt token 数、(b)稳定判官 overall、
(c)methodology_compliance 维度。要求:token 降、分不降(最好升)。

---

## R5 — book_methodology / emotion_stack 接进长篇初稿(审计 §4-3 缺口)

**现状(已勘)**:
- `book_methodology`:`render_book_methodology_block` 存在(methodology_book_selector),且被
  `compile_methodology` 调用,但经 `_book_methodology_scope(stage)`(methodology_compiler.py:470)
  **scope 到特定 stage(修复路径)**,未进初稿 draft stage。
- `emotion_stack`:在 `fanqie_short_emotion_bank`(**短篇路径**),长篇无 render+注入。

**方案**:
1. book_methodology:在 `_book_methodology_scope` 增加初稿 draft stage(或在 drafts.py 的
   PROSE_SCENE 装配点直接注入 `render_book_methodology_block(scope=draft)`)。**加法**,低回归风险。
2. emotion_stack:写一个长篇版 `render_emotion_stack_block`(从 emotion_bank 取本章主导情绪的
   physiological/behavioral/object/silence/dialogue 五层承载),注入 writer system prompt(P1 tier)。

**E2E 验证**:确认真实 ch1 prompt 含这两块;稳定判官 methodology_compliance / emotion 相关维度不降。
注意与 R4 协同:R5 是"加块",必须在 R4 的预算框架内加(否则加剧 75 块淹没)。**R4 先于 R5**。

---

## R6 — editor 定点重写循环 × 稳定判官(0.87→0.92 收敛,审计 §4-1)

**现状(已勘)**:`reviews.py` 已接稳定判官(`judge_chapter_commercial_quality_stable`),
`chapter_llm_commercial_judge_block_on_failure` 失败时仅标 rewrite,**无收敛循环**。审计实测:
整章重写会退化(0.88→0.72);需"只补失败硬维度、保留其余"的定点重写。

**方案(新增收敛函数)** `services/targeted_rewrite_loop.py`:
```
async def converge_chapter(session, settings, *, chapter, draft, genre_context, max_rounds=3):
    result = await judge_chapter_commercial_quality_stable(...)         # 稳定判官给真信号
    best = (draft, result)
    for round in range(max_rounds):
        if result.passed: break
        failed_dims = [k for k,v in result.dimension_scores.items() if v < floor[k]]
        targets = failed_dims + [i for i in result.blocking_issues]
        new_draft = await editor_rewrite_targeted(... preserve=非failed部分, fix=targets,
                                                   instructions=result.rewrite_plan)  # 定点,非整章
        new_result = await judge_chapter_commercial_quality_stable(content=new_draft)
        if new_result.overall_score - best[1].overall_score < 0.03: break  # stall→accept best
        if new_result.overall_score > best[1].overall_score: best=(new_draft,new_result)
        result = new_result
    return best  # accept_on_stall
```
- editor 复用现有 `rewrite_escalation` / editor 角色,prompt 用 `=== reference only ===` 围栏,
  temp=0.40,**scope 限定到失败维度对应段落**(不整章重写)。
- 接入点:reviews.py 稳定判官失败分支,替换"仅标 rewrite"为"先跑 converge_chapter,仍不过再标"。

**E2E 验证(本项的硬验证)**:真实非探案 ch1,跑 converge_chapter,确认 overall 单调爬升
(0.87→0.92)而非退化;每个失败硬维度回到 floor 之上。**这是 R6 唯一可信的验收方式**。

---

## 执行顺序(E2E 环境)
1. 先跑 `scripts/e2e_judge_genre_neutrality.py` 确认题材中立 + R2 判官升档生效。
2. **R4**(预算框架)→ **R5**(在框架内加方法论块)→ 各用稳定判官比分验证。
3. **R6**(收敛循环)→ 验证 0.87→0.92 单调爬升。
4. 端到端跑通一本非探案短篇,每章稳定过门禁。
