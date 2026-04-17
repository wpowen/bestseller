# Recipe · 1000 章（二十卷六幕长篇）

> 体量：20 卷 × 50 章 × 每章 ≥ 5000 字 ≈ 500–600 万字
> 适合：顶流网文 / 史诗仙侠 / 长篇玄幻 / 西幻长篇

## 拓扑

- `volumes = 20`
- `acts = 6`（完整六幕）
- `supporting_cast = 12–20`
- `antagonist_forces = 5–8`（分阶段登场）
- `conflict_phases`：**六阶段完整轮一遍**（可考虑 1 轮 + 半轮变奏）

## Volume × Act 划分（建议）

| Act | Vols | Ch 范围 | 说明 |
|-----|------|--------|------|
| I | 01–03 | 1–170 | 生存 / 启蒙 |
| II-A | 04–06 | 171–330 | 政争 / 初入更大舞台 |
| II-B | 07–13 | 331–670 | 背叛 / 势力混战 |
| III-A | 14–16 | 671–830 | 存亡危机 |
| III-B | 17–19 | 831–950 | 内省 / 道心重铸 |
| IV | 20 | 951–1000 | 终末与开放尾声 |

## Win/Loss 节奏

20 卷的胜负更复杂。参考序列（`W` = win，`L` = loss，`M` = mixed）：

```
W L W L M L M W L W  L W L L  M L  W L  L W
01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20
```

其中：
- vol-01 开局小胜
- vol-19 **major loss**（penultimate）
- vol-20 终胜（但留种）
- 14–18 为慢速下坠区，情感最重

## 伏笔密度

- 全书 **200+ 条** clue → payoff
- 长线（跨 5+ 卷）伏笔 15–25 条
- 中线（跨 2–4 卷）伏笔 40–60 条
- 短线（卷内）每卷 6–10 条

## 快照 / 审计 / 压缩

- character snapshots：每 5 章（共约 200 次）
- consistency audit：每 20 章
- rolling summary：每 25 章（共约 40 块）

每 100 章可触发一次 **meta-summary**：把前 100 章压成 ≤ 5000 字的"回看卷"，放在 `knowledge/meta-summaries/block-NNN-NNN.md`。

## World Expansion

- 20 卷 → world-expansion.md **必须**
- `VolumeFrontier × 20`：每卷解锁 1 个新地域 / 新势力 / 新规则
- `DeferredReveal[]`：独立表；最长线跨 10+ 卷
- `ExpansionGate[]`：必要时每 3 卷一道大闸（如"元婴境开，中州才可入"）
- **世界可见度**：第 V 卷展开 V/20 ≈ 5% × V 的世界蓝图

## Power / Stakes 梯度参考（修真类）

| Vol | 主角境界 | 战力区间 |
|-----|----------|---------|
| 01–02 | 炼气 | 县 / 乡 |
| 03–04 | 筑基 | 州 |
| 05–06 | 金丹 | 宗门 |
| 07–08 | 元婴 | 联盟 |
| 09–11 | 化神 | 大陆 |
| 12–14 | 渡劫 | 界内顶 |
| 15–17 | 界主 | 界外 |
| 18–20 | 虚无之上 | 跨界 |

## 推进批次建议

1000 章 × 5000 字 = 500 万字。在一次会话中是不可能完成的。

| 阶段 | 产出 | 建议批次数 |
|------|------|----------|
| 规划 | 全套 story-bible + 20 份 volume README + act-plan + world-expansion | 3–5 轮 |
| 前三卷（Act I） | ch 1–170 | 30–40 轮 |
| Review Gate A（读者必读）| — | 1 轮 |
| Act II-A | ch 171–330 | 30 轮 |
| Act II-B | ch 331–670 | 70 轮 |
| Act III-A/B | ch 671–950 | 55 轮 |
| Act IV | ch 951–1000 | 10 轮 |
| 审计节点 | 每 20 章 consistency audit | — |

每批次结束**必须**：
- 更新 meta.yaml / volume README / canon facts / timeline
- 如到 snapshot 周期，写 snapshot 文件
- 如到 rolling summary 周期，写 rolling summary
- 每 100 章可写 meta-summary

## 关键风险

- **角色漂移**：主角性格 voice 从卷 1 到卷 20 可能走样。audit 时对比首卷 voice_profile。
- **伏笔堆积**：planted 过多而 payoff 不足。每次 audit 必检查偿付率 ≥ 60%。
- **反派扁平化**：单一反派撑不到 1000 章。分阶段反派：vol-01–07 主反、vol-08–14 次主反、vol-15–20 真正终极。
- **节奏拖沓**：每 20 章强制出现一次真正的 climax 章（pacing=climax）。
