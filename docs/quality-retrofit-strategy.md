# Quality Retrofit Strategy · 已写完章节如何提升

> **场景**：framework 装上 quality_levers 后，仓库里已经写完的 ≈ 2400 章
> 没有自动追溯。这份文档给出三层 retrofit 策略 + 决策树 + 可执行工具。

## 1. 真实存量（截至 2026-05-14）

| 项目 | 已写章数 | 平台 | retrofit audit 已跑 |
|------|---------|------|--------------------|
| xianxia-upgrade-1776137730 | 551 | qimao | ✅ 100 章 sample |
| female-no-cp-1776303225 | 491 | qimao | ⬜ |
| superhero-fiction-1776147970 | 467 | tomato (英文) | ⬜ |
| romantasy-1776330993 | 413 | tomato (英文) | ⬜ |
| superhero-fiction-1776301343 | 395 | tomato (英文) | ⬜ |
| exorcist-detective-1778051012 | ~50 | qimao | ⬜ |
| exorcist-detective-1778428166 | 50 | qimao | ✅ 50 章 |
| **合计** | **~2417** | | |

### Layer 1 sample 结果（青崖诡事 50 章 + 仙侠升级 100 章）

| 优先级 | 青崖诡事 50 章 | 仙侠升级 100 章 | 含义 |
|-------|--------------|-----------------|------|
| critical | 2.0% | 3.0% | 立即需要 surgical patch |
| high | 66.0% | 72.0% | 至少 2 个量化指标失败 |
| medium | 32.0% | 24.0% | 单一维度失败 |
| ok | 0.0% | 1.0% | 全部通过 |

**Top 失败原因（按出现频率）**：
- `flat_narration` × 100% — 节奏锚点不足（4 种锚点覆盖 < 3 种）
- `weak_attraction` × ~98% — pulse 词密度 < 1.0/300 字
- `weak_prose` × 60-68% — 抽象感官词或情绪标签
- `ai_voice` × 37-42% — banned patterns 命中

**结论**：LLM 默认输出有 2 个**系统性硬伤**（节奏单一 + 心率词不足），影响几乎所有章节。

---

## 2. 三层 Retrofit 策略

### Layer 1 — 纯量化 audit（零 LLM 成本，立即可跑）

**用什么**：[scripts/quality_levers_retrofit_audit.py](../scripts/quality_levers_retrofit_audit.py)

**做什么**：
- 7 个 detector 扫每章 → 量化打分
- 输出每章一行 CSV + 一份 summary.md
- 标 critical / high / medium / ok 优先级
- 每个失败项 → 映射到 `rejection_repair_playbook` cause_id

**成本**：
- LLM 调用 = **0**
- 时间 = 几秒/100 章

**用法**：
```bash
python scripts/quality_levers_retrofit_audit.py --slug <slug> --platform qimao
# 输出：
#   output/<slug>/audits/quality-retrofit/window-001-NNN.csv
#   output/<slug>/audits/quality-retrofit/summary.md
```

---

### Layer 2 — Surgical patch（中等 LLM 成本，按 priority 触发）

**用什么**：[scripts/quality_levers_retrofit_patch.py](../scripts/quality_levers_retrofit_patch.py)

**做什么**：读 Layer 1 输出的 CSV，针对每章的**具体失败项**调 editor LLM 做**点位修补**：

| 失败项 | editor LLM 指令（精细到段落） | 修改幅度 |
|-------|---------------------------|----------|
| `banned_patterns` 命中"X 一边 Y 一边" | 仅替换该句为"X 着。Y 着。" | ≤ 30 字 |
| `banned_patterns` 命中"那不是最要命的" | 替换为短句独段 + 视角切换 | ≤ 40 字 |
| `abstract_sensory` 命中"阴森 / 寂静" | 替换为具体物件描写 | ≤ 60 字 |
| `emotion_labels` 命中"愤怒 / 紧张" | 替换为动作 / 物件承载 | ≤ 60 字 |
| `dumping` 命中长心理独白 | 拆成"动作-停顿-动作" 3-5 段 | ≤ 200 字 |
| `pulse_density` < 1.0 | 在 3 个关键决策段落前各加 1 个 pulse 词 | ≤ 30 字 |

**成本估算**（按现有 LLM 价格，假设每个修补点 ≈ $0.005）：

| 项目 | 章数 | 平均修补点/章 | 估算成本 |
|------|------|--------------|---------|
| xianxia-upgrade 551 | 75% high+critical = 413 章 | 3 | ≈ $6.2 |
| female-no-cp 491 | 75% | 3 | ≈ $5.5 |
| superhero-fiction 467 | 75% | 3 | ≈ $5.3 |
| romantasy 413 | 75% | 3 | ≈ $4.6 |
| superhero-fiction 395 | 75% | 3 | ≈ $4.4 |
| 青崖诡事 系列 100 | 75% | 3 | ≈ $1.1 |
| **合计 2400 章** | | | **≈ $27** |

**显著低于全重写**（全重写每章 $0.05 × 2400 = $120）。

---

### Layer 3 — 选择性全重写（高成本，仅 hero 章节）

**用什么**：手工触发现有 framework 的章节重写工作流

**做什么**：对极少数 critical 且涉及核心剧情的章节（如 ch1-ch10 签约样章、卷末 climax 章），让 framework 走完整的 writer → critic → editor 循环。

**成本估算**：每章 $0.05-0.20，按 100 章 hero 计 ≈ $5-20

**触发条件**：
- 章节是 first_chapter / volume_opener / volume_climax
- 且 Layer 2 surgical patch 后 critic min_score 仍 < 0.70
- 且章节剧情非常关键（影响后续 10+ 章）

---

## 3. 决策树

```
对每一本书：

[Step 1] 跑 Layer 1 retrofit audit
  ↓
  生成 window-001-NNN.csv + summary.md

[Step 2] 看 summary.md 的 priority 分布
  ↓
  critical 占比?
   ├─ < 5%   → 仅对 critical 章节做 Layer 2 surgical patch
   ├─ 5-15%  → 对 critical + high 章节做 Layer 2
   └─ > 15%  → 先检查 LLM 调用是否符合预期，再决定批量处理

[Step 3] 对 hero 章节（first_chapter / volume_opener / climax）
  ↓
  Layer 2 后再跑 critic
   ├─ pass        → 完成
   └─ 仍不达标    → Layer 3 全重写
```

---

## 4. 不建议做的事

| 反模式 | 原因 |
|--------|------|
| ❌ 一次性把 2400 章全 Layer 3 重写 | 成本爆炸 + 大概率引入新的 canon 矛盾 |
| ❌ 全部章节先全 Layer 2 | 33% 是 medium 优先级，单一维度失败不值得 LLM 成本 |
| ❌ 等 framework 全自动 | quality_levers 只能保护新写章节；存量必须 retrofit 显式触发 |
| ❌ 改完 chapter MD 不更新数据库 | `chapter_quality_reports` / `chapter_audit_findings` 会和文件失同步 |

---

## 5. 推荐执行顺序

1. **本周内**：用 Layer 1 把所有 2400 章扫一遍，得到全局图谱（成本 = 0，时间 < 5 分钟）
2. **下一步**：对每本书 ≤ 5 章的 critical chapters 做 Layer 2 dry-run，确认修补建议合理
3. **再下一步**：开启 Layer 2 实际 LLM 调用，先 1 本小说（如青崖诡事 50 章）作为 pilot
4. **最后**：对 hero 章节（每本书 ch1-ch5 + 卷末章 ≈ 100 章）评估是否要 Layer 3

---

## 6. retrofit 之外：未来新章自动达标

quality_levers 已经接入 [services/drafts.py](../src/bestseller/services/drafts.py) + [services/reviews.py](../src/bestseller/services/reviews.py)。
**重 build worker container 之后**，所有新生成的章节都会自动满足这套规则——retrofit 只是历史债的偿付。

**所以 retrofit 工作量是有限的**：完成后再写的章节不会再添新债。
