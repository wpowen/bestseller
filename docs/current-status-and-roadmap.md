# BestSeller Current Status And Roadmap

更新时间：2026-03-19

## 1. 当前总体判断

项目已经完成第一版可用闭环，不再是文档或脚手架状态。

当前系统已经能执行：

- 项目创建
- premise 驱动的自动规划生成
- 故事圣经物化
- 章节/场景物化
- scene draft -> review -> rewrite -> re-review
- chapter assemble -> context -> review -> rewrite
- project pipeline / project autowrite
- canon / timeline / retrieval
- rewrite impacts / rewrite cascade
- project repair
- markdown / docx / epub / pdf 导出

当前系统更准确的定位是：

- 已完成：长篇小说生产系统的主链能力
- 已完成：CLI-first 可执行产品形态
- 已完成：PostgreSQL-first 存储和 workflow 审计
- 未完全完成：更强的正文质量和更复杂的策略层增强

## 2. 已完成的模块

### 2.1 工程与基础设施

- PostgreSQL + pgvector
- Alembic 迁移基线
- `.env` / `.env.local` / `config/default.yaml`
- 一键启动、停止、运行、验收脚本
- CLI 入口与命令分组

### 2.2 规划层

- `planning import`
- `planning generate`
- `book_spec / world_spec / cast_spec / volume_plan / chapter_outline_batch`
- `workflow materialize-story-bible`
- `workflow materialize-outline`

### 2.3 写作生产层

- `scene draft`
- `scene review`
- `scene rewrite`
- `scene pipeline`
- `chapter assemble`
- `chapter context`
- `chapter review`
- `chapter rewrite`
- `chapter pipeline`
- `project pipeline`
- `project autowrite`
- `project repair`

### 2.4 知识层

- `CanonFact`
- `TimelineEvent`
- `CharacterStateSnapshot`
- `retrieval refresh`
- `retrieval search`
- `scene context`
- `story-bible show`
- `project structure`

### 2.5 修订层

- `rewrite impacts`
- `rewrite cascade`
- 项目级 `project repair`
  - 扫描 pending/queued rewrite tasks
  - 自动 supersede 旧任务
  - 重跑受影响章节
  - 重新做项目审校
  - 在通过时重新导出整书 Markdown

### 2.6 导出层

- `export markdown`
- `export docx`
- `export epub`
- `export pdf`

## 3. 当前真实可用边界

系统当前已经支持两种使用方式：

### 3.1 规划导入式

适合先有人类规划，再由系统生成正文：

`planning import -> materialize-story-bible -> materialize-outline -> project pipeline`

### 3.2 自动整书式

适合从 premise 直接触发整本书：

`project autowrite`

这条链路当前已经在真实验收里跑通，能产出整书 `project.md`。

## 4. 当前仍属于增强项的部分

这些不是主链缺失，但仍然值得继续推进：

### 4.1 正文质量增强

- fallback 写法仍偏稳态模板化
- 真实模型下的提示装配还可以更强
- chapter / project 级风格一致性仍可继续加强

### 4.2 planner 增强

- premise -> planning 已可运行
- 但多轮 planner 迭代、人工确认点、方案比较还没系统化

### 4.3 retrieval 增强

- 当前检索链已能用
- 但 embedding 策略、混合召回、长书规模下的 chunk 策略仍可继续优化

### 4.4 修订自动化增强

- `rewrite impacts`
- `rewrite cascade`
- `project repair`

这三层已经落地，但更复杂的跨卷、跨角色知识冲突自动修补仍可继续增强。

## 5. 当前推荐的后续开发顺序

如果继续往“更强生产能力”推进，建议顺序如下：

1. 提升 scene/chapter 的真实模型写作质量
2. 增强 planner 的多轮确认和多方案比较
3. 强化 retrieval 的召回与上下文裁剪
4. 扩展 project repair，支持更多自动导出和更细的修订策略
5. 增加更强的项目级连续性规则

## 6. 当前验收基线

最近一次完整验收：

- 单测：`119 passed`
- 覆盖率：`82.48%`
- 编译检查：通过
- `./scripts/verify.sh`：通过

最近一次端到端验收输出目录：

- `output/verify-20260319231355`
- `output/verify-20260319231355-autowrite`
