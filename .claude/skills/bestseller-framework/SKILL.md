---
name: bestseller-framework
description: BestSeller AI 长篇小说生成框架。按需加载子文件；既支持在本仓库做系统开发（Mode A），也支持直接产出完整小说到 output/ai-generated/{slug}/（Mode B，最多 1000–2000 章）。
trigger_keywords:
  - bestseller
  - novel generation
  - 长篇小说
  - 小说生成
  - scene pipeline
  - chapter contract
  - canon facts
  - planner role
  - writer role
  - critic role
  - 焚心诀
  - mode b
  - story bible
  - volume plan
version: 2026.04.16
---

# BestSeller Framework Skill

> 本 skill 以**渐进披露**（progressive disclosure）组织：入口文件只做路由，按任务需要把对应子文件读入上下文。请严格按下方路由表加载，避免一次性加载全部文件。

---

## 0. 你的第一步：判定 Mode

先读 [modes.md](modes.md) 做二选一：

| 用户意图 | Mode | 你扮演的角色 |
|---------|------|------------|
| 开发这个仓库本身（写代码 / 修 bug / 加特性 / 看架构） | **A** | 熟悉 BestSeller 代码库的资深工程师 |
| 要求你**直接写小说**（"帮我写 30/100/500/1000 章 xxx"） | **B** | 扮演 planner → writer → critic → editor 的全流程 |

默认 Mode A；当用户句式含"帮我写 / 生成 / 创作一部 xx 章的小说"时切到 Mode B。

---

## 1. Mode 路由 · 按需加载

### Mode A — 开发协助

| 子任务 | 必读 | 选读 |
|-------|------|------|
| 架构 / 模块关系 | [architecture.md](architecture.md) | — |
| 修改 LLM 调用 | [architecture.md](architecture.md) § LLM Role System | [prompts/](prompts/) |
| 修改 scene / chapter pipeline | [architecture.md](architecture.md) § Pipeline Flow | [quality.md](quality.md) |
| 修改知识层（canon / timeline / snapshot） | [knowledge.md](knowledge.md) | — |
| 修改评分 / rewrite 逻辑 | [quality.md](quality.md) | — |
| 修改 output / 导出 | [output.md](output.md) | — |
| 修改 planner | [planning.md](planning.md) | [prompts/planner.md](prompts/planner.md) |

### Mode B — 直接写小说（自主执行）

**首读**：[orchestration.md](orchestration.md) —— **Orchestrator 状态机**。你不再是"一次写一步后等用户说继续"，而是按状态机**自主循环**，用 `progress.yaml` 做断点续跑，直到 `DONE`。

状态机总览（详见 orchestration.md）：

```
INIT → PLAN_{PREMISE|WORLD|CHARACTERS|VOLUME_PLAN|[ACT]|[WORLD_EXPANSION]|WRITING_PROFILE}
     → 每卷 { PLAN_VOL_README → 每章 { WRITE → REVIEW → [REWRITE×≤2] → EXTRACT → COMMIT → MILESTONE } }
     → EXPORT → DONE
```

**其他必读**（按状态机调用顺序）：

1. [planning.md](planning.md) —— 根据 target_chapters 决定是否进 PLAN_ACT / PLAN_WORLD_EXPANSION
2. 对应体量的 recipe：[recipes/30-chapters.md](recipes/30-chapters.md) / [100-chapters.md](recipes/100-chapters.md) / [500-chapters.md](recipes/500-chapters.md) / [1000-chapters.md](recipes/1000-chapters.md) / [2000-chapters.md](recipes/2000-chapters.md)
3. [output.md](output.md) —— 建立 `output/ai-generated/{slug}/` 目录（含 `progress.yaml`）
4. [templates/](templates/) —— 填 meta / README / story-bible / chapter frontmatter；**特别读** [templates/progress-state.md](templates/progress-state.md)（orchestrator 状态持久化）
5. [writing.md](writing.md) —— WRITE_CHAPTER 状态下 writer 的约束
6. [quality.md](quality.md) —— REVIEW_CHAPTER / REWRITE_CHAPTER 状态下的评分与重写
7. [knowledge.md](knowledge.md) —— EXTRACT_KNOWLEDGE / MILESTONE_CHECK 状态下的知识追加
8. [invariants.md](invariants.md) —— 任何状态都不可违反的红线

### 写某一"角色"的 Prompt 时

| 角色 | 文件 | 状态机触发点 |
|------|------|------------|
| **orchestrator**（调度）| [orchestration.md](orchestration.md) | 整个 Mode B 生命周期 |
| planner（规划 / 大纲 / 章节契约） | [prompts/planner.md](prompts/planner.md) | 所有 `PLAN_*` 状态 |
| writer（场景正文） | [prompts/writer.md](prompts/writer.md) | `WRITE_CHAPTER` |
| critic（评分 / 审校） | [prompts/critic.md](prompts/critic.md) | `REVIEW_CHAPTER` / `MILESTONE_CHECK` 审计 |
| editor（定点重写） | [prompts/editor.md](prompts/editor.md) | `REWRITE_CHAPTER` / `DRAIN_REPAIR_QUEUE` |
| summarizer（知识抽取） | [prompts/summarizer.md](prompts/summarizer.md) | `EXTRACT_KNOWLEDGE` / snapshot / rolling |

---

## 2. Hard Invariants（永不违反）

完整列表在 [invariants.md](invariants.md)。**最关键三条**：

1. **每章 ≥ 5000 字**。低于即强制 rewrite，禁止灌水；从场景/内心/对白方向扩写。
2. **Mode B 的全部输出必须写入 `output/ai-generated/{slug}/`**，绝不污染仓库源码目录。
3. **Canon Facts 只可追加**。改动既有条目视为破坏连续性。

---

## 3. 快速决策指引

- 用户没告诉你章节数 / 类型 / 书名？→ **问他**，不要猜。
- 已有 `output/ai-generated/*/progress.yaml`？→ **读它续跑**，不要重新初始化。
- 章节数 ≤ 50？→ 1 卷 1 幕；不写 act-plan.md / world-expansion.md。
- 章节数 > 50？→ **必须**写 act-plan.md。
- 卷数 > 3？→ **必须**写 world-expansion.md（含 DeferredReveal 追踪）。
- 一章草稿 < 5000 字？→ **立刻返工**，不进入 review 环节。
- 主角每卷都在赢？→ **停下**，检查 [planning.md § Win/Loss Rhythm](planning.md)。
- 角色预知了后续章节才揭示的事？→ **违反知识单调性**，返工。
- 工具调用 / 上下文快满？→ 保存 progress.yaml，告诉用户"已完成 N/T 章，说'继续'恢复"。

---

## 4. 同步资源（跨平台部署）

此 skill 同时适配 Claude Code / Cursor / ChatGPT Custom GPT / Gemini Gem / 通用 LLM。各平台装载方式详见 [docs/SKILL-INSTALLATION.md](../../../docs/SKILL-INSTALLATION.md)。

| 资源 | 位置 | 目标平台 |
|------|------|---------|
| 渐进披露 skill（本文件 + 子文件）| `.claude/skills/bestseller-framework/` | Claude Code |
| 按 glob 加载的 Cursor 规则 | `.cursor/rules/bestseller-*.mdc` (×5) | Cursor ≥ 0.45 |
| 单文件完整参考 | [docs/ai-context.md](../../../docs/ai-context.md) | ChatGPT / Gemini / Claude.ai Projects knowledge 文件 |
| 精简 system prompt（< 8000 字符）| [docs/ai-context-system-prompt.md](../../../docs/ai-context-system-prompt.md) | ChatGPT Custom GPT Instructions / Gemini Gem / API system message |
| 代码入口（Mode A） | [src/bestseller/services/pipelines.py](../../../src/bestseller/services/pipelines.py) | — |

**单一事实源**：`docs/ai-context.md`。其他平台的 prompt/rule 文件均应定期从这里重新生成——在 [docs/SKILL-INSTALLATION.md § 9](../../../docs/SKILL-INSTALLATION.md) 里有生成原则。

---

*本 skill 文档反映 BestSeller 2026-04 的框架设计。更新版本时改动 `version` 字段。*
