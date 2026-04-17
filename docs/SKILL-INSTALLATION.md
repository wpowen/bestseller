# BestSeller Framework — Skill Installation Guide

将 BestSeller 框架作为共享 skill 装载到任何 AI 助手里。本文覆盖 5 类平台。

---

## 资源清单

| 文件 | 用途 | 体量 |
|------|------|-----:|
| [`.claude/skills/bestseller-framework/SKILL.md`](../.claude/skills/bestseller-framework/SKILL.md) | Claude Code 入口（带 frontmatter）| 5.7 KB |
| [`.claude/skills/bestseller-framework/orchestration.md`](../.claude/skills/bestseller-framework/orchestration.md) | **自主执行状态机**（Mode B 核心调度层） | 16 KB |
| [`.claude/skills/bestseller-framework/templates/progress-state.md`](../.claude/skills/bestseller-framework/templates/progress-state.md) | `progress.yaml` 模板（断点续跑状态源）| 5 KB |
| [`.claude/skills/bestseller-framework/`](../.claude/skills/bestseller-framework/)（目录） | Claude Code 渐进披露子文件 + recipes/ + templates/ + prompts/ | ~100 KB |
| [`.cursor/rules/bestseller-*.mdc`](../.cursor/rules/)（6 份） | Cursor 按 glob 加载的 Rule 集；含 **orchestrator** 自主调度层 | ~40 KB |
| [`docs/ai-context.md`](ai-context.md) | **完整便携参考**（全量设计 + 规则 + 状态机）| ~40 KB |
| [`docs/ai-context-system-prompt.md`](ai-context-system-prompt.md) | **精简 System Prompt**（< 8 000 字符，含 orchestrator 核心）| 8.0 KB |

---

## 1. Claude Code（自动生效）

**前置**：Claude Code ≥ 2026.01，已启用 `.claude/skills/` 扫描。

**操作**：无需手动操作。打开本仓库后，Claude Code 会：

1. 扫描 `.claude/skills/bestseller-framework/SKILL.md` 的 frontmatter
2. 匹配 `trigger_keywords`（`bestseller`, `长篇小说`, `mode b`, ...）后自动将 SKILL.md 注入上下文
3. 按 SKILL.md 的路由表**渐进披露**加载子文件（不是一次性全读）

**手动调用**：在会话里输入 `/bestseller-framework` 强制加载。

**验证**：

```bash
# 查看 skill 是否被识别
claude code --list-skills | grep bestseller
```

---

## 2. Cursor（按路径自动生效）

**前置**：Cursor ≥ 0.45，已启用 Rules for AI / `.cursor/rules/` 支持。

**操作**：无需手动操作。`.cursor/rules/` 下的 6 个 `.mdc` 文件会按 `globs` 字段自动生效：

| 规则 | 触发条件 |
|------|---------|
| `bestseller-core.mdc` | `alwaysApply: true`——所有会话默认加载（总纲）|
| `bestseller-orchestrator.mdc` | `alwaysApply: true`——Mode B **自主调度状态机**，驱动 planner→writer→critic→editor 循环直至完稿 |
| `bestseller-dev.mdc` | 编辑 `src/**/*.py` / `migrations/**/*.py` / `tests/**/*.py` |
| `bestseller-planning.mdc` | 编辑 `output/ai-generated/**/story-bible/**/*.md` / `meta.yaml` |
| `bestseller-writing.mdc` | 编辑 `output/ai-generated/**/volumes/**/ch-*.md` |
| `bestseller-output.mdc` | 编辑 `output/ai-generated/**` 目录结构 |

**验证**：在 Cursor 里按 `Cmd+Shift+J` → "Rules" 面板 → 应看到 6 条 BestSeller 规则。

**老版本 Cursor（< 0.45，只支持 `.cursorrules`）**：

```bash
# 把精简版当 .cursorrules 用
cp docs/ai-context-system-prompt.md .cursorrules
```

或直接粘贴 `docs/ai-context.md` 完整内容到 Cursor 设置里的 "Rules for AI" 文本框。

---

## 3. ChatGPT Custom GPT

**前置**：ChatGPT Plus / Team / Enterprise 账号。

**操作**：

1. 访问 `chat.openai.com/gpts/editor`，创建新 GPT。
2. **Configure → Instructions**：粘贴 [`docs/ai-context-system-prompt.md`](ai-context-system-prompt.md) 内 HTML 注释之后的全部内容（字符数 7 990，低于 8 000 上限）。
3. **Knowledge → Upload files**：上传 [`docs/ai-context.md`](ai-context.md) 作为 Knowledge 文件（完整设计参考）。
4. （可选）上传以下作为附加 Knowledge：
   - `.claude/skills/bestseller-framework/planning.md` — 规划细则
   - `.claude/skills/bestseller-framework/writing.md` — 写作细则
   - `.claude/skills/bestseller-framework/prompts/*.md` — 各角色 prompt
5. **Capabilities**：建议启用 Code Interpreter（用于字数统计 / YAML 校验）。

**验证**：问 "你知道 BestSeller 框架的 5 个 LLM role 吗？" 应得到 planner/writer/critic/summarizer/editor 及其温度。

---

## 4. Google Gemini Gems

**前置**：Gemini Advanced 或 Workspace 账号。

**操作**：

1. 访问 `gemini.google.com/gems`，点 "Create new Gem"。
2. **Instructions**：粘贴 [`docs/ai-context-system-prompt.md`](ai-context-system-prompt.md)（Gem 支持最长 ~20 000 字符，精简版完全够；若想给更详细上下文，可贴完整 `ai-context.md`）。
3. **Knowledge**（如可用）：上传 `docs/ai-context.md` + 所有 `.claude/skills/bestseller-framework/*.md`。
4. Save Gem。

**验证**：启动 Gem 后输入 "我要写一本 100 章的玄幻小说"，应触发 Mode B 路由并给出分层规划。

---

## 5. 其他 LLM（DeepSeek / Qwen / Kimi / Doubao / Claude.ai Projects / 任意 OpenAI-compatible）

### 5.1 API 直接调用

```python
from openai import OpenAI  # 或 anthropic / dashscope / ...

with open("docs/ai-context-system-prompt.md") as f:
    # 去掉 HTML 注释头，剩余即 system prompt
    system = f.read().split("-->", 1)[1].strip()

client = OpenAI(...)
resp = client.chat.completions.create(
    model="deepseek-chat",  # 或 qwen-max / moonshot-v1 / doubao-pro / ...
    messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": "帮我写一本 30 章的玄幻小说"},
    ],
)
```

### 5.2 Web 界面（无 system prompt 字段时）

在聊天窗口首条消息粘贴：

> 以下是你的系统指令，必须严格遵守。后续的每一条消息都在此约束下回复：
>
> ---
>
> 【粘贴 `docs/ai-context-system-prompt.md` 的正文】
>
> ---
>
> 现在请确认你已理解上述约束，然后等待我的下一条指令。

### 5.3 Claude.ai Projects

1. 创建新 Project。
2. **Project instructions**：粘贴精简版 `docs/ai-context-system-prompt.md`。
3. **Project knowledge**：上传 `docs/ai-context.md` + `.claude/skills/bestseller-framework/` 目录下所有 md 文件。
4. Claude.ai 会在 Project 所有对话中自动应用 instructions + 在需要时检索 knowledge。

---

## 6. 自主执行循环（Orchestrator）使用方式

装载完成后，用户只需一句话即可触发全自主执行。**入参最小集** = `{ genre, title, target_chapters }`。orchestrator 会自己：

1. 创建 `output/ai-generated/{slug}/` 目录树 + `meta.yaml` + `progress.yaml`
2. 按状态机顺序产出 story-bible（premise/world/characters/volume-plan/...）
3. 进入章节循环：`WRITE → REVIEW → REWRITE(×≤2) → EXTRACT → COMMIT → MILESTONE`
4. 每章完成后**原子性**更新 6 个文件（ch 正文 / vol README 状态 / meta.yaml / canon / timeline / 条件性 snapshot）
5. 每 10 章写 character snapshot；每 20 章做 consistency audit；每 25 章压 rolling summary
6. 工具调用 / 上下文将满 → 自动保存进度，提示"说'继续'即可恢复"
7. 完成 target_chapters → 拼装 `exports/full-novel.md` → 输出完稿报告

### 6.1 各平台的触发指令

| 平台 | 启动 | 续跑 |
|------|------|------|
| **Claude Code** | 新会话里：`"帮我写一部 30 章的玄幻小说《焚心诀》"` | 下次会话：`"继续"`（自动读 progress.yaml） |
| **Cursor Agent Mode** | Agent 开启，粘贴同样指令；Agent 会连续多步执行直到工具配额 | 新会话：`"继续"` |
| **Cursor Chat（非 Agent）** | 同上，但每轮只跑 1–3 步，需反复说 `"继续"` | 同左 |
| **ChatGPT Custom GPT** | 粘贴指令；Code Interpreter 可维护 `.zip` 项目包；用户接力将文件粘回 | `"从 progress.yaml 继续"`（上传最新 zip） |
| **Gemini Gem / 通用 API** | 粘贴指令；LLM 输出每一步的预期文件内容 + `next_action`；由调用方（脚本或用户）落盘 | 传入最新 `progress.yaml` 内容作为 user message，LLM 读后继续 |

### 6.2 预期单轮输出形态

**启动轮**（用户首句 → orchestrator 先自检入参）：

```
User: 帮我写一部 30 章玄幻小说，主角江晚，主题复仇
Assistant:
  书名缺失。我建议三个候选：
    A. 《焚心诀》 — 聚焦内心灼烧的功法
    B. 《北疆落雪》 — 地理 + 意象
    C. 《残簪引》 — 遗物引线
  请确认，我将进入 PLAN_PREMISE。
```

**规划轮**：

```
User: 用《焚心诀》
Assistant:
  ▸ [init]     output/ai-generated/fen-xin-jue/     ✓
  ▸ [init]     meta.yaml                            ✓  (target=30, vols=1, acts=1)
  ▸ [init]     progress.yaml                        ✓  (state=PLAN_PREMISE)
  ▸ [plan]     premise.md                           ✓  (logline + 3-tier stakes)
  ▸ [plan]     world.md                             ✓  (rules=7, tiers=9, loc=6)
  ▸ [plan]     characters.md                        ✓  (1 主 + 4 配 + 2 反)
  ▸ [plan]     volume-plan.md                       ✓  (W·L·L·maj-L·W 节奏)
  ▸ [plan]     writing-profile.md                   ✓  (close-3rd / past / 冷峻)
  ▸ [plan]     vol-01 README                        ✓  (30 章 outline 就绪)
  Progress: 0/30 ch · plan complete · 进入 WRITE_CHAPTER(1)
```

**章节轮**（循环 N 章直到上限）：

```
  ▸ [ch-001]   drafting...                          ⋯
  ▸ [ch-001]   drafted                              ✓  (6184w, 4 scenes)
  ▸ [ch-001]   reviewed                             ✓  (0.82 / 0.78 / 0.81 / 0.73 / 0.88)
  ▸ [ch-001]   committed                            ✓  (canon+3 timeline+1)
  ▸ Progress: 1/30 (3%) · words 6184/180000
  ...
  ▸ [ch-007]   committed                            ✓
  ⚠ 接近工具上限，已保存进度。说"继续"即可恢复（下一章 ch-008）
```

**恢复轮**：

```
User: 继续
Assistant:
  读取 progress.yaml: state=ADVANCE_CHAPTER, next=WRITE_CHAPTER(8)
  ▸ [ch-008]   drafting...
  ...
```

**完稿轮**：

```
  ▸ [ch-030]   committed                            ✓
  ▸ [export]   exports/full-novel.md                ✓  (186 420 字)
  🎉 《焚心诀》完成：30/30 章 · 186 420 字 · 1 卷
     canon 127 条 · 重写 8 次（accept_on_stall: 0）
     一致性审计：1 次（PASS · clue→payoff 73%）
     交付：output/ai-generated/fen-xin-jue/exports/full-novel.md
  下一步可选：导出 epub / 做全量二次润色 / 针对某卷调风格
```

### 6.3 断点续跑的保证

- `progress.yaml` 是单一事实源；每步完成**立即**回写
- 上下文 / 工具预算耗尽时，orchestrator 主动保存并告知用户，不会留下半写状态
- 用户说"继续"时，orchestrator 读 progress.yaml → 若损坏则回退到磁盘实际文件状态重建 → 从 `next_action` 恢复
- 磁盘上的 `ch-NNN-*.md` 文件是最终来源；`meta.yaml.current_chapter` 和 `progress.yaml.state` 都以磁盘状态为准

### 6.4 何时 orchestrator 会停下问用户

- INIT 阶段缺 `genre` / `title` / `target_chapters`
- 某章连续 2 次扩写仍 < 5 000 字
- 某章连续 2 次 rewrite 仍未过阈值（此时 `accept_on_stall` 通常自动继续，**不**阻塞）
- consistency audit 修复 3 次仍失败
- 涉及戏剧结构的决策（如反派身份揭示时机）——orchestrator 在 `human_decision_pending` 里给 2–3 个候选 + tradeoff + 推荐

---

## 7. 跨平台验证清单

装载完成后，在任一平台执行下列测试：

| # | 输入 | 期望行为 |
|---|------|---------|
| 1 | "BestSeller 的 5 个 LLM role 是什么？" | 列出 planner/writer/critic/summarizer/editor 及温度 0.82/0.85/0.25/0.20/0.40 |
| 2 | "帮我写第 1 章 8000 字的正文。" | 拒绝或先问 target_chapters / 先建 story-bible；不直接开写 |
| 3 | "主角心里毫无波动地看着前方。" | 指出 taboo word `内心毫无波动`；改写示范 |
| 4 | "我 500 章小说，要不要写 act-plan？" | 肯定，且指出超过 50 章强制；同时提醒 volumes > 3 需要 world-expansion |
| 5 | "我第 7 章只写了 3800 字。" | 指出 < 5000 字硬门槛；要求扩写 / 拒绝提交 |
| 6 | "给我看看第 1 章的新 canon fact，顺便修改一下第 3 章那条旧的。" | 拒绝修改既有条目；指示用 supersedes 追加新条目 |
| 7 | "帮我写一部 30 章的玄幻小说《焚心诀》" | **进入 orchestrator 状态机**：创建目录 + progress.yaml → 按顺序写 story-bible → 开始 ch-001 循环；每步输出单行进度 |
| 8 | （上一轮暂停后）"继续" | 读 progress.yaml → 从 `next_action` 恢复，不重新 INIT |
| 9 | "反派什么时候揭穿身份？让主角当场撕破脸？" | orchestrator 在 progress.yaml 填 `human_decision_pending`，给 2–3 候选 + tradeoffs + 推荐，停下等用户 |

9 项全部通过 → skill 装载成功。

---

## 8. 更新 skill

当 `.claude/skills/bestseller-framework/` 或 `docs/ai-context.md` 变更后：

1. **Claude Code / Cursor**：无动作，下次会话自动加载新内容
2. **ChatGPT Custom GPT**：回到 GPT Editor → 覆盖 Instructions 文本框 → 重新上传 Knowledge 文件
3. **Gemini Gem**：同上
4. **其他 LLM**：API 调用方自行更新 system message；Web 端重新粘贴首条提示

每次更新建议同步修改：

- `.claude/skills/bestseller-framework/SKILL.md` 的 `version` 字段（如 `2026.04.16` → `2026.05.01`）
- `docs/ai-context.md` 末尾的版本说明
- `docs/ai-context-system-prompt.md` 末尾的 footer 字符数

---

## 8. 常见问题

**Q：Cursor 0.45 以下看不到 `.mdc` 规则生效？**
A：降级到 `.cursorrules` 单文件，或升级 Cursor。精简版内容见 §2 末尾。

**Q：ChatGPT Custom GPT 粘贴 Instructions 报 "too long"？**
A：确认没把 HTML 注释头（`<!-- ... -->`）一起粘贴。实际可粘贴部分为 7 990 字符。

**Q：DeepSeek 用中文精简版还是英文？**
A：精简版以英文为主干 + 中文 taboo words，两者都保留；如需纯中文，基于 `.cursor/rules/bestseller-core.mdc` + `bestseller-planning.mdc` + `bestseller-writing.mdc` 三份拼接一个中文版 system prompt。

**Q：skill 之间冲突（比如 Cursor 规则与 Custom GPT 指令不一致）？**
A：以 `docs/ai-context.md` 为**单一事实源**。其他平台的文件都应定期从这里重新生成。

---

## 9. 精简版 System Prompt 的生成原则

如果未来需要为新平台再生成一份精简 prompt：

- **保留**：mode routing / hard invariants / pipeline summary / LLM roles / quality thresholds / output directory / hard-stop rules
- **压缩**：planning artifacts 细节（只留名字和触发条件）/ prompt 模板（单独做 Knowledge 文件）/ 数据库 schema（仅 Mode A 用）
- **剔除**：具体代码路径（除 `services/llm.py` 这种 API 级别的入口）/ Docker / Alembic 命令 / 测试细节
- **目标字数**：≤ 8 000 字符（ChatGPT Custom GPT 硬上限）

Source of truth: [`docs/ai-context.md`](ai-context.md)。
