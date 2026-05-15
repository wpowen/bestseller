# Quality Levers Retrofit · 全量 Audit 结果 (2026-05-14)

> **TL;DR**：已写完的 **1988 章** 用 Layer 1 量化 detectors 全扫一遍。
> 7% (136 章) critical / 88% (1754 章) high / 5% (98 章) medium / 0.2% (3 章) ok。
> 所有书 **100% 触发 flat_narration**（节奏锚点不足）+ ~98% 触发 weak_attraction
> （心率词不足）—— 这是 LLM 默认输出的**系统性硬伤**，所有存量章节都中招。

## 全书数据表

| 书 | 章数 | critical | high | medium | ok | Top 失败 |
|---|----:|---------:|-----:|-------:|---:|---|
| exorcist-detective-1778428166（青崖诡事，新）| 50 | 1 | 33 | 16 | 0 | flat_narration / weak_attraction / weak_prose |
| exorcist-detective-1778051012 | 50 | 3 | 40 | 7 | 0 | flat_narration / weak_attraction / ai_voice |
| xianxia-upgrade-1776137730（仙侠升级） | 100* | 3 | 72 | 24 | 1 | flat_narration / weak_attraction / weak_prose |
| female-no-cp-1776303225 | 491 | 129 | 312 | 48 | 2 | flat_narration / weak_attraction / weak_prose |
| romantasy-1776330993 | 413 | 0 | 413 | 0 | 0 | flat_narration / weak_attraction（detector 主要中文规则）|
| superhero-fiction-1776147970 | 489 | 0 | 489 | 0 | 0 | flat_narration / weak_attraction（英文）|
| superhero-fiction-1776301343 | 395 | 0 | 395 | 0 | 0 | flat_narration / weak_attraction（英文）|
| **合计** | **1988** | **136** | **1754** | **98** | **3** | |

\* xianxia-upgrade 总计 551 章，只 sample 前 100 章避免输出过大。

## 关键洞察

### 洞察 1：节奏锚点 + 心率词是绝对系统性问题

每本书 **100% 章节都缺**：
- `flat_narration` — 每 1500 字至少 4 个节奏锚点 + 覆盖 3 种类型（hard_stop / acceleration / delay / external_interrupt），存量章节几乎全部不达标
- `weak_attraction` — pulse_density ≥ 1.0/300 字，几乎全部不达标

**含义**：这不是某本书写得差，是 LLM 默认 prompt 没有把"节奏 + 心率"作为硬约束。
quality_levers 装上后，**新章节会自动满足**——retrofit 是历史债。

### 洞察 2：英文书 detector 部分失效

`romantasy / superhero-fiction` 三本英文书 critical = 0 是**误判**——
不是因为质量高，是因为：
- ban patterns（"一边...一边" 等）只匹配中文
- abstract_sensory 词（"阴森" 等）也是中文
- 英文章节绕过了一半检测器

**待办**：detector 加英文规则集（"It was not just X, but also Y" / "in a way that was both… and"
等），暂列为 P2。

### 洞察 3：女频项目 `female-no-cp` critical 占比 26% 是异常高

491 章里 129 章 critical，远高于其他书的 3%。建议优先看这本书的 patch-plan，
可能存在批量写作时的某种系统性错误。

## 可用工具

### Layer 1 — audit (零 LLM 成本)

```bash
python scripts/quality_levers_retrofit_audit.py --slug <slug> --platform qimao
# → output/<slug>/audits/quality-retrofit/window-NNN.csv + summary.md
```

### Layer 2 — patch plan (dry-run, 零 LLM 成本)

```bash
python scripts/quality_levers_retrofit_patch.py --slug <slug> --priority critical,high --dry-run
# → output/<slug>/audits/quality-retrofit/patch-plan.json
```

Patch plan 示例（青崖诡事 ch005）：
```json
{
  "chapter_number": 5,
  "priority": "high",
  "cause_ids": ["flat_narration", "weak_attraction", "ai_voice"],
  "patch_points": [
    {
      "cause_id": "ai_voice",
      "location": "paragraph 11",
      "issue_summary": "AI-voice pattern 'parallel_action' fired 1x",
      "snippet": "她一边跑一边在他耳边说话",
      "repair_action_summary": "按 rejection_repair_playbook.ai_voice 替换该模式...",
      "expected_max_chars_delta": 40
    }
  ]
}
```

### Layer 2 — execute (尚未实现，标 TODO)

`--execute` 参数已经预留但暂未实现 — 真正调用 editor LLM 改写每个 patch point。
需要：
- 复用 `services/editor.py` 的 LLM 调用
- 拿 patch point 的 `snippet` + `repair_action_summary` 拼成 editor system prompt
- 解析返回的 patched paragraph + 写回 `chapter-NNN.md`
- 重跑 Layer 1 audit 验证修补成功

预估实现工作量：200-300 行 Python + 单元测试。

## 推荐执行序列

```
本周：
1. 看《青崖诡事》patch-plan.json 抽查 5 个 patch point 是否合理
2. 看 female-no-cp 异常高 critical 占比的根因（采样 5 章手工分析）
3. 决定是否实现 Layer 2 --execute

下周（如果 Layer 2 实现）：
4. 拿青崖诡事 50 章做 pilot LLM 修补（成本 ≈ $0.24）
5. 重新 Layer 1 audit 验证 critical → ok / medium

后续：
6. 对其他 6 本书按 priority=critical 先做 surgical patch
7. 评估英文项目要不要加 detector 英文规则
```

## 重要：retrofit 之外，新章自动达标

**rebuild worker / web / api 之后**，quality_levers 装备会自动对所有**新生成**的章节生效——
retrofit 仅处理历史债。

```bash
docker compose build worker api web
docker compose up -d worker api web
```

新章节的 LLM prompt 会自动叠加：
- platform 签约门槛（七猫 100/200/600/2000/6000 字硬指标）
- character_engine voice_dna + signature
- prose_style_anchor + anti-AI baseline
- sensory_inventory 必带感官
- chapter_signature 截图段契约
- rhythm + emotion 锚点契约
- information_choreography 悬念契约
