# BestSeller Toward A Strong Long-Form Novel — TODO

**日期**: 2026-03-20  
**目标**: 把当前“可运行长篇系统”推进到“更完整、可读、具备上榜潜力的长篇小说生产系统”

## 当前判断

- [x] 已完成主链：`premise -> planning -> story bible -> outline -> draft/review/rewrite -> canon/timeline -> export`
- [x] 已完成 PostgreSQL-first 架构
- [x] 已完成基本检索和上下文装配
- [x] 已完成显式叙事图谱第一阶段
- [ ] 未完成榜单级叙事质量控制
- [ ] 未完成更强的人物、伏笔、暗线、感情线编排

## P0 叙事架构补完

### P0.1 PlotArc 显式建模

- [x] 新增 `plot_arcs` 表
- [x] 新增 `arc_beats` 表
- [ ] 支持 `main_plot / hidden_plot / romance / revenge / growth / faction / mystery`
- [x] 每条线必须有 `promise / core_question / target_payoff / status`
- [x] 支持卷级、章级、场级挂载

### P0.2 Clue / Foreshadow / Payoff Ledger

- [x] 新增 `clues` 表
- [x] 新增 `payoffs` 表
- [x] 建立 `planted_in_scene / expected_payoff_by / actual_paid_off_in_scene`
- [ ] 建立“超期未回收伏笔”检测器
- [ ] 建立“提前泄露暗线”检测器

### P0.3 感情线和情绪线

- [ ] 新增 `relationship_arcs` 或 `emotion_tracks`
- [ ] 显式记录 `trust / attraction / distance / conflict / intimacy stage`
- [ ] 场景审校增加“情感推进是否有效”评分
- [ ] 支持感情线节奏规则：推进、拉扯、误会、对抗、确认、兑现

### P0.4 反派推进模型

- [ ] 新增 `antagonist_plans`
- [ ] 显式记录反派目标、当前动作、下一步反制
- [ ] 每卷至少有一轮“反派升级”节点
- [ ] 审校器检查“主角是否一直单向推进、反派是否失踪”

## P1 上下文系统升级

### P1.1 Narrative Tree

- [x] 定义叙事树导出格式
- [x] 建立 `/book /world /characters /arcs /volumes /chapters /scenes` 路径层级
- [x] 支持路径式确定性检索
- [x] 支持树节点摘要缓存

### P1.2 PageIndex-style Tree Search Adapter

- [x] 评估直接依赖 `VectifyAI/PageIndex` 的工程成本
- [x] 优先实现内部 `NarrativeTreeSearch` 抽象
- [x] 对 Story Bible / Arc Graph / Chapter Contract 输出树结构
- [x] 在 context assembler 中接入 `path retrieval -> tree search -> hybrid retrieval`

### P1.3 Context Assembler v2

- [x] scene writer 必须显式吃到相关 `PlotArc`
- [x] scene writer 必须显式吃到未回收 `Clue`
- [x] chapter writer 必须显式吃到本章承担的 arc beats
- [x] 加入“未来信息泄露”更严格过滤
- [x] 加入“人物认知边界”更严格过滤

## P1 规划系统升级

### P1.4 Planner 多轮确认

- [ ] `premise -> 3 套 book DNA 方案`
- [ ] `book DNA -> 2 套 volume strategy`
- [ ] 支持人工选择方案
- [ ] 支持自动对比：爽点密度、悬念密度、角色张力

### P1.5 Chapter / Scene Contract Compiler

- [x] 每章输出 `chapter contract`
- [x] 每场输出 `scene contract`
- [ ] contract 字段至少包括：
  - [x] 进入状态
  - [x] 核心冲突
  - [x] 情绪变化
  - [x] 信息释放
  - [x] arc beat
  - [x] 伏笔埋设/回收
  - [x] 尾钩
- [ ] draft 完成后自动对照 contract 做偏差审校

## P1 质量体系升级

### P1.6 Scene Quality Rubric v2

- [ ] 增加 `hook strength`
- [ ] 增加 `conflict clarity`
- [ ] 增加 `emotional movement`
- [ ] 增加 `payoff density`
- [ ] 增加 `voice consistency`

### P1.7 Chapter Quality Rubric v2

- [ ] 增加“本章是否推进主线”
- [ ] 增加“本章是否推进至少一条副线”
- [ ] 增加“本章尾钩是否成立”
- [ ] 增加“本章是否承担卷级任务”

### P1.8 Project Review v2

- [ ] 检查明线是否持续推进
- [ ] 检查暗线是否存在埋设和兑现
- [ ] 检查感情线是否断裂
- [ ] 检查角色弧光是否有台阶变化
- [ ] 检查世界规则是否前后自洽

## P2 可用性和生产能力

### P2.1 编辑工作台

- [ ] Web UI: Story Bible Explorer
- [ ] Web UI: Arc Graph Explorer
- [ ] Web UI: Clue / Payoff Ledger
- [ ] Web UI: Chapter / Scene status board
- [ ] Web UI: Review findings dashboard

### P2.2 导出层增强

- [ ] 带目录的长篇 Markdown 导出
- [ ] 带卷结构的 DOCX 模板
- [ ] EPUB 元数据和目录增强
- [ ] PDF 版式增强

### P2.3 评测与样书基线

- [ ] 建立 3 套样书 benchmark
- [ ] 建立“末日囤货 / 玄幻升级 / 都市悬疑”三种类型基线
- [ ] 建立人工评分标准
- [ ] 建立自动对比回归流程

## P2 真实模型质量优化

- [ ] planner / writer / critic / editor 模型分工细化
- [ ] prompt pack 版本化
- [ ] genre-specific prompt packs
- [ ] 长篇上下文预算和裁剪策略优化
- [ ] rewrite cost guardrail

## 本阶段推荐执行顺序

### Sprint A

- [x] `plot_arcs`
- [x] `arc_beats`
- [x] `clues`
- [x] `payoffs`
- [x] `chapter/scene contract`

### Sprint B

- [x] `Narrative Tree`
- [x] `path retrieval`
- [x] `tree search adapter`
- [x] `context assembler v2`

### Sprint C

- [ ] `emotion tracks`
- [ ] `antagonist plans`
- [ ] `project review v2`
- [ ] `sample-book evaluation suite`

## Done Definition

“较为完美的小说生产系统”至少要满足下面这些条件：

- [ ] 能显式管理明线、暗线、感情线、成长线
- [x] 能显式管理伏笔埋设与回收
- [ ] 能显式管理人物知识边界和人物弧光
- [ ] 能显式管理世界规则并进行规则冲突检测
- [x] 能显式管理卷级、章级、场级叙事任务
- [x] 即使 review 未通过，也能导出当前草稿供人工审阅
- [ ] 能对一本完整长篇输出结构化质量报告
- [ ] 能在不同题材上稳定生成“可读且连贯”的成书
