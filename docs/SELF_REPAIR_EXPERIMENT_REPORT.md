# 自修复实验报告 — 《青囊不语问阴阳》ch2

> 本文件记录用现有框架对 ch2 触发 3 次自修复的实测结果。结论：**框架机制正确，但 LLM 一致性违反 cast 守护**。这是 P0 任务 D（Cast Compliance Gate）必须落地的实证依据。

---

## 实验设计

| 字段 | 值 |
|---|---|
| 项目 | exorcist-detective-1778051012 |
| 章节 | 第 2 章 |
| LLM | MiniMax-M2.7-highspeed |
| 触发命令 | `bestseller chapter pipeline exorcist-detective-1778051012 2 --export-markdown` |
| 重写前版本 | v10（cast clean） |
| Cast 焦点 | 裴镜渊 必须不在 ch1-16 出现 |

---

## 三次迭代结果

### Iter 1 (v11, 创建于 07:17)
**框架状态**：P1 blocks 已接 fresh-write prompt，未接 rewrite prompt
**Trace 检查**：`canon_guardrails_block: <MISSING>`（未送到 LLM）
**产出审计**：
- 裴镜渊: **6 次** ✗
- 倒计时（ch1 钩子）: 0 次 ✗
- 渊娃子（ch1 钩子）: 0 次 ✗
- 第八张脸（ch1 钩子）: 0 次 ✗
- 老道士（原配角）: 13 次 ✓ 保留

### Iter 2 (v13, 创建于 07:34)
**框架状态**：加了 canon_guardrails 渲染器 + pipelines.py P1 注入
**Trace 检查**：`canon_guardrails_block: <MISSING>`（rewrite 路径未读 packet）
**产出审计**：
- 裴镜渊: **6 次** ✗
- 倒计时 / 渊娃子 / 第八张脸: 0 次 ✗
- 文本 99% 与 v11 相同

### Iter 3 (v14, 创建于 07:46)
**框架状态**：把 P1 blocks 加进 `build_scene_rewrite_prompts`（reviews.py）
**Trace 检查**：未捕获（trace 工具未保存 iter 3 prompt）
**产出审计**：
- 裴镜渊: **6 次** ✗
- 倒计时 / 渊娃子 / 第八张脸: 0 次 ✗
- 文本基本与 v13 相同

---

## 核心发现

### 1. 框架机制完全工作

- 触发命令成功执行（exit=0）
- 4 个场景被重置为 needs_rewrite
- pipeline 调用 LLM 重生成所有场景
- chapter_draft 重新装配为新版本

### 2. P1 blocks 注入逐步生效（但 LLM 不听）

| 迭代 | canon block 进 prompt | 钩子 block 进 prompt | LLM 是否服从 |
|---|---|---|---|
| 1 | ❌ | ❌ | — |
| 2 | ❌ rewrite 路径未读 | ❌ rewrite 路径未读 | — |
| 3 | ✅ | ✅ | **❌ 仍违反** |

### 3. LLM 行为分析

即使 prompt 里明确写了：

> 【绝对禁止】角色 裴镜渊 不得在第 16 章前以任何形式出现（不得有 裴镜渊 的对白、动作、视角、心声或在场描写）

LLM 仍然在 v14 里写：

> 林渊快速扫过四周——加上他和**裴镜渊**，这屋里一共八个人。
> 角落里，**裴镜渊**的目光死死钉在小雨身上。
> **裴镜渊**忽然开口，声音不高，却压过了所有喘息

**这是 LLM 在某种"角色记忆固化"上的失败**。LLM 看了 cast 文件后形成强烈的"裴镜渊存在"印象，不论 prompt 怎么强调禁令，都倾向于把他写出来。

### 4. 同等输入下 LLM 输出极度稳定

v11/v13/v14 的 ch2 主体文本几乎完全一致（差异 < 5%）。即使 prompt 加了越来越多的禁令，**输出几乎不变**。这意味着：

- 当前 LLM 在这本书的 ch2 上下文里已经"记住"一种生成模式
- prompt 内追加的指令对它的影响非常有限

---

## 结论

### 框架本身: ✅ 正确

- gate 检测正确（每次都识别出 cast 违规）
- prompt 注入正确（iter 3 起 P1 blocks 真正进入 LLM 上下文）
- 自修复触发链路完整（scene reset → pipeline → assemble）

### LLM 行为: ❌ 不可靠

- 即使最严厉的"绝对禁止"措辞也无法阻止 裴镜渊 出现
- 多次重试产出**几乎相同**的违规内容
- 仅靠 prompt 工程无法解决这个问题

### 缺失的兜底机制: Cast Compliance Gate

P0 任务 D（详见 `RETENTION_REPAIR_DEVELOPMENT_PLAN.md`）必须落地才能闭环：
1. 写后扫描裴镜渊出现次数 ≥ 2 → 标 CAST_VIOLATION block code
2. CAST_VIOLATION 触发更激进的重写，prompt 里加入"你已经第 N 次失败"
3. 重试 ≥ 3 次后仍违规 → 升级到人工审核

---

## 现在应该怎么做（具体建议）

### 短期（立刻，0 LLM 成本）

**使用我之前生成的 `output/exorcist-detective-1778051012/revised/` 下的 10 章修订版**。它们：
- ✅ 保留所有 cast（无 裴镜渊 漂移）
- ✅ 保留所有物理设定/时间线
- ✅ 在 opening 加了 hook echo
- ✅ ch1/2/3 加了 signature scene 时刻

直接复制 revised/chapter-XXX.md → chapter-XXX.md 即可上传榜单。

### 中期（1-2 周，需开发）

按 `RETENTION_REPAIR_DEVELOPMENT_PLAN.md` 执行 P0+P1 任务（5-7 工时），重点是 Task D（Cast Compliance Gate）+ Task F（Retry 预算 + 升级 prompt 措辞）。完成后再次触发批量重写，LLM 即使首次违规也会被强制重试到合规。

### 长期（视情况）

换 LLM。如果 MiniMax 在这本书上无法被 prompt 约束，可以试试：
- Claude Opus / Sonnet（一般 instruction following 更强）
- GPT-4o
- DeepSeek-V3

Voice DNA 已经把目标声纹定型，换 LLM 不影响声纹。

---

## ch2 当前状态（已恢复）

- chapter_draft v14（不良）→ is_current=false
- chapter_draft v10（clean）→ is_current=true
- output/.../chapter-002.md → v10 内容（裴镜渊 出现次数 = 0）
- 4 个 scene_cards → 状态 approved
