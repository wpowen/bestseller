# Recipe · 500 章（十卷五幕史诗）

> 体量：10 卷 × 50 章 × 每章 ≥ 5000 字 ≈ 250–300 万字
> 适合：中长篇仙侠 / 玄幻 / 奇幻史诗 / 长篇都市

## 拓扑

- `volumes = 10`
- `acts = 5`
- `supporting_cast = 8–12`
- `antagonist_forces = 3–5`（主反派 + 若干阶段性势力）
- `conflict_phases`：**六阶段全部走一轮**

## Volume × Act 映射（建议）

| Act | Vols | Ch 范围 | 冲突相 |
|-----|------|--------|-------|
| I | 01–02 | 1–100 | survival |
| II | 03–04 | 101–200 | political_intrigue |
| III | 05–06 | 201–300 | betrayal |
| IV | 07–08 | 301–400 | faction_war |
| V | 09–10 | 401–500 | existential_threat → internal_reckoning |

## Win/Loss 节奏

| Vol | 胜负 |
|-----|------|
| 01 | win |
| 02 | loss |
| 03 | win |
| 04 | loss |
| 05 | 中部转折（mixed 偏败）|
| 06 | loss |
| 07 | win（短暂高光）|
| 08 | loss |
| 09 | **major loss**（penultimate）|
| 10 | **win**（终胜）|

## 伏笔密度

- 全书 **80–120 条** clue → payoff
- 跨卷伏笔（DeferredReveal）**至少 20 条**，多数跨 3 卷以上才偿付
- 每卷自有 6–10 条卷内伏笔

## 快照 / 审计

- character snapshots：每 10 章
- consistency audit：每 20 章
- rolling summary：**每 25 章**强制压缩

压缩产出：

```
knowledge/rolling-summary/
├── summary-ch-001-025.md
├── summary-ch-026-050.md
├── ...
└── summary-ch-476-500.md
```

每压缩块 200–400 字 / 章位。

## World Expansion

- `volumes = 10 > 3` → world-expansion.md **必须**
- `VolumeFrontier × 10`：每卷推进 10% 世界可见度（第 V 卷累积 V × 10%）
- `DeferredReveal[]`：独立表；每条含 planted_ch / payoff_ch_target / category（mystery / prophecy / relic / lineage）
- `ExpansionGate[]`：标明每个世界模块开放条件（"主角突破 X 境" / "卷 Y 结束" / "某 NPC 登场"）

## Power / Stakes 梯度（如修真）

| Vol | 主角境界 | 战力梯度参考 |
|-----|----------|-------------|
| 01 | 炼气 | 县级 |
| 02 | 筑基 | 州级 |
| 03 | 金丹 | 宗门级 |
| 04 | 元婴 | 联盟级 |
| 05 | 化神 | 大陆级 |
| 06 | 渡劫 | 界外级 |
| 07–10 | 更高层 | 超越现有体系 |

每卷提升 1 境或 1–2 小境；每次突破必须带代价账。

## 推进批次建议

500 章体量，按 5 章一批 ≈ 100 批，按 10 章一批 ≈ 50 批。**强烈建议**先完成前两卷后暂停，由读者 / 用户审读再决定是否继续。

| 阶段 | 产出 | 建议节奏 |
|------|------|---------|
| 规划 | story-bible（全）+ 10 份 volume README + act-plan + world-expansion | 2–3 轮 |
| Vol-01 | ch 1–50 | 约 10 轮 |
| Review Gate A | 用户审读 + 可能调整 story-bible | 1 轮 |
| Vol-02…10 | 余下 | 约 90–180 轮 |
| Review Gate | 每 2 卷一次 | — |

## 必开审计节点

- Ch 20, 40, 60, …, 500（每 20 章）→ `reviews/consistency-audits.md`
- 每一次 audit 至少检查：canon monotonicity / knowledge integrity / character arc drift / clue→payoff ratio
