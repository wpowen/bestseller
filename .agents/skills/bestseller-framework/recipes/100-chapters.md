# Recipe · 100 章（四卷三幕）

> 体量：3–4 卷 × 25–30 章 × 每章 ≥ 5000 字 ≈ 50–65 万字
> 适合：都市 / 仙侠中短篇 / 悬疑探案系列 / 成长向故事

## 拓扑

- `volumes = 4`（建议 4 × 25 章，或 3 × 33 + 1 收尾卷）
- `acts = 3`（三幕）
- `supporting_cast = 4–6`
- `antagonist_forces = 2`（主反派 + 一个辅助势力）
- `conflict_phases`：`survival → political_intrigue → betrayal → internal_reckoning`

## Volume × Act 映射

| Act | Volume | Ch 范围 | 核心任务 |
|-----|--------|--------|---------|
| I | vol-01 | 1–25 | 世界 / 人物 / 初冲突 |
| II-A | vol-02 | 26–50 | 冲突升级 / 盟友与敌人 |
| II-B | vol-03 | 51–75 | twist / 背叛 / 低谷 |
| III | vol-04 | 76–100 | 终决 / 内省 / 收束 |

## Win/Loss 节奏（四卷）

| 卷 | 胜负 |
|----|------|
| vol-01 | win（hook 闭环 + 入场成功）|
| vol-02 | mixed（2 胜 2 败）|
| vol-03 | **major loss**（penultimate）|
| vol-04 | **win**（终胜 + 留种）|

## Chapter Phase 分布（逐卷复用 6 节拍但稍作偏移）

每卷内部：
- 0–13% hook（卷首 3 章）
- 13–33% setup
- 33–53% escalation
- 53–73% twist
- 73–87% climax
- 87–100% resolution_hook（并与下一卷 hook 咬合）

**关键**：卷末的 `resolution_hook` = 下一卷的 `hook` 种子，不能分家。

## 伏笔密度

- 全书 **30–40 条** clue → payoff
- 每卷内部 6–10 条；跨卷 2–3 条
- 跨卷伏笔至少跨 1 卷再偿付

## 快照 / 审计

- character snapshots：每 10 章（ch 10, 20, 30, …, 100）
- consistency audit：每 20 章
- rolling summary：**不需要**（仍 < 300 章）

## World Expansion

- `volumes = 4 > 3` → **必须**写 `story-bible/world-expansion.md`
- `VolumeFrontier` × 4（每卷开新地点 / 新势力 / 新规则）
- `DeferredReveal[]` 跟踪：每一个跨卷伏笔登记 planted_at / payoff_target
- `ExpansionGate[]`：控制何时开放下一级世界信息（第 V 卷可见度 ≈ V/4）

## 推进批次建议

100 章 × 每章 5000+ 字 = 50+ 万字，需数十轮对话。

| 批次 | 章节 | 备注 |
|------|------|------|
| 0 | 全套 story-bible + 4 份 volume README | 规划 |
| 1–5 | ch 1–25（vol-01）| 每批 5 章 |
| 6–10 | ch 26–50（vol-02）| 每批 5 章 |
| 11–15 | ch 51–75（vol-03）| twist 卷，节奏敏感 |
| 16–20 | ch 76–100（vol-04）| 终局 |
| — | 每 20 章强制一致性审计 → reviews/ |

每批后落实：meta.yaml 更新 / volume README 更新 / canon facts 追加 / 如到 snapshot 周期则写 snapshot 文件。
