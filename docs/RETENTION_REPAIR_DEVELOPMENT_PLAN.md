# 留存率自修复框架 — 完整开发计划

> **目标**：让 BestSeller 框架的自修复流程能可靠地把"读完率断崖式跌落"的章节自动修补到榜单中位水平，且**严禁破坏 cast/outline/物理设定**。
>
> **当前状态**：核心 gate / blocks / prompt 注入大部分已就位，但有 4 个关键漏洞导致自修复闭环不可靠。本文件列出**所有剩余任务**，按优先级排序，含**文件路径 / 函数签名 / 测试要求 / 验收标准**。
>
> **执行方**：可由任何具备 Python 3.11+ 与 BestSeller 架构知识的大模型 / 工程师接手。所有任务都必须配单元测试 + 通过 `pytest tests/unit/`。
>
> **验收方**：人工触发 `bestseller chapter pipeline <slug> <N>` 跑 ≥ 3 个 chapter（含 ch1/2/3 + 一个中段），逐项核对验收标准。

---

## 已完成模块（绿色 = 已落地）

| 模块 | 文件 | 状态 |
|---|---|---|
| ✅ Voice DNA 提取/融合/对比 | `src/bestseller/services/voice_signature.py` + `domain/voice_dna.py` | 88% 覆盖 |
| ✅ Voice DNA 持久化 | `src/bestseller/services/voice_dna_repository.py` | 100% 覆盖 |
| ✅ Market Constraint Compiler | `src/bestseller/services/market_constraint_compiler.py` | 86% 覆盖 |
| ✅ Reader Persona Simulator | `src/bestseller/services/reader_persona_simulator.py` | 90% 覆盖 |
| ✅ Persona Feedback Repository | `src/bestseller/services/persona_feedback_repository.py` | 89% 覆盖 |
| ✅ Signature Scene Planner + 黄金三章 | `src/bestseller/services/signature_scene_planner.py` | 23 测试 |
| ✅ Concept Leap Generator | `src/bestseller/services/concept_leap.py` | 85% 覆盖 |
| ✅ Hook Echo Gate | `src/bestseller/services/hook_echo_gate.py` | 12 测试 |
| ✅ Exposition Density Gate | `src/bestseller/services/exposition_density_gate.py` | 13 测试 |
| ✅ Chapter Orchestrator | `src/bestseller/services/chapter_orchestrator.py` | 89% 覆盖 |
| ✅ Retention Safety Gate（post-assembly 评估器） | `src/bestseller/services/retention_safety_gate.py` | 10 测试 |
| ✅ Canon Guardrails Prompt 渲染器 | `src/bestseller/services/canon_guardrails.py` 末尾 `render_canon_guardrails_block` | 8 测试 |
| ✅ Bootstrap CLI / Maintenance CLI / Voice DNA CLI | `src/bestseller/cli/{book_writer,maintenance,voice_dna}.py` | 全过 |
| ✅ P1 blocks 注入 fresh-write prompt | `src/bestseller/services/drafts.py` `build_scene_draft_prompts` | 全过 |
| ✅ P1 blocks 注入 scene rewrite prompt | `src/bestseller/services/reviews.py` `build_scene_rewrite_prompts` | 已落地 |
| ✅ Pipelines.py 预写注入 6 个 block + 写后 grade_chapter | `src/bestseller/services/pipelines.py` 2700-2900 行 | 已落地 |

---

## 剩余任务（按 P0 → P2 排序）

### 🔴 P0 — 不修就无法闭环

#### Task A: 在 pipelines.py 写后调用 retention_safety_gate

**问题**：`retention_safety_gate.evaluate_retention_safety` 已实现但**从未被 pipeline 调用**。结果：章节写完后没有自动评估 hook echo / signature / exposition 合规性，因此 auto-repair 不会被触发。

**文件**：`src/bestseller/services/pipelines.py`

**修改位置**：`assemble_chapter_draft` 调用之后（约 3795-3820 行），目前那里已经接了 grade_chapter。在 grade_chapter 之前或之后插入 retention 评估。

**期望逻辑**：
```python
# 伪代码
if chapter_draft is not None and _orig_cfg.enabled:
    from bestseller.services.retention_safety_gate import (
        evaluate_retention_safety, stamp_retention_block_codes,
    )
    # 读上一章 draft 用于 hook echo
    prev_text = await _load_prev_chapter_draft_text(session, project, chapter_number)
    report = evaluate_retention_safety(
        chapter_position=chapter_number,
        chapter_text=chapter_draft.content_md or "",
        prev_chapter_text=prev_text,
        prev_chapter_position=chapter_number - 1 if chapter_number > 1 else None,
        total_chapters=int(getattr(project, "target_chapters", 0) or 500),
    )
    if report.has_critical:
        stamp_retention_block_codes(chapter, report)
        await session.flush()
        # 然后让既有 auto_repair_codes 路径接手
```

**测试**：
- `tests/integration/test_retention_safety_in_pipeline.py`（新建）
- 用 mocked LLM，确认 retention failure 会让 chapter.production_state="blocked" + auto_repair_last_block_codes 包含 HOOK_ECHO_MISSING
- 确认 retention passing 不会改变 chapter state

**验收**：
- 跑一次 ch2 pipeline，验证写完后日志里出现 `retention_safety_gate evaluated:` 行
- 验证 chapter.metadata_json["retention_gate_last_findings"] 字段被写入

---

#### Task B: 在 build_chapter_rewrite_prompts 中加 P1 blocks

**问题**：`build_chapter_rewrite_prompts`（reviews.py ~5696 行）是章节级（非场景级）的重写 prompt 构造器。当前**没有读 P1 blocks**。当 retention 触发的章节级重写发生时，LLM 看不到 canon/hook echo 约束。

**文件**：`src/bestseller/services/reviews.py`

**修改位置**：line 5696 附近的 `build_chapter_rewrite_prompts` 函数。

**参考**：line 1019 的 `build_scene_rewrite_prompts` 已经接好了，**复制相同 pattern**：
- 读 `context_packet.canon_guardrails_block`
- 读 `context_packet.hook_echo_block`
- 读 `context_packet.signature_scene_block`
- 读 `context_packet.voice_dna_block`
- 读 `context_packet.chapter_market_constraints_block`
- 读 `context_packet.exposition_density_block`
- 拼成 `_rewrite_p1_block` 字符串
- 在 user_prompt 的 zh/en 两个分支都注入到 current_draft 前面

**测试**：
- `tests/unit/test_chapter_rewrite_prompt_p1_wireup.py`（新建）
- 构造一个含上述 6 个 block 的 mock SceneWriterContextPacket（实际 ChapterWriterContextPacket）
- 调用 build_chapter_rewrite_prompts，assert 返回的 user_prompt 包含 6 个 block 的关键标识字符串（"正典守护"、"钩子回环"、"招牌场景指令"、"作者声纹"、"市场硬约束"、"铺垫节制"）

**验收**：
- 跑一次章节级 rewrite trace，检查 trace 文件的 context_blocks 字段含上述 6 项

---

#### Task C: prev_chapter_text 真实加载传入 prepare_chapter_context

**问题**：`prepare_chapter_context(slug, ch, prev_chapter_text=...)` 已接受 prev text，但 pipelines.py **没有把上一章的 current draft 文本传进去**。结果：hook_echo_report 永远是 None，hook_echo_block 永远不渲染。

**文件**：`src/bestseller/services/pipelines.py` ~2840 行（P1 注入块内）

**修改逻辑**：
```python
# 在调用 _prepare_chapter_context 之前
_prev_text: str | None = None
if chapter_number >= 2:
    from sqlalchemy import select
    stmt = (
        select(ChapterDraftVersionModel.content_md)
        .join(ChapterModel, ChapterDraftVersionModel.chapter_id == ChapterModel.id)
        .where(
            ChapterModel.project_id == project.id,
            ChapterModel.chapter_number == chapter_number - 1,
            ChapterDraftVersionModel.is_current.is_(True),
        )
        .limit(1)
    )
    _prev_text = (await session.execute(stmt)).scalar_one_or_none()

_orig_ctx = _prepare_chapter_context(
    project.slug,
    chapter_number,
    output_base_dir=settings.output.base_dir,
    mode_b=_orig_mode_b,
    prev_chapter_text=_prev_text,  # ← 新增
)

# 然后渲染 hook_echo_block
if _orig_ctx.hook_echo_report is not None:
    shared_context.hook_echo_block = _orig_ctx.hook_echo_block(language=_orig_lang) or None
```

**测试**：
- `tests/integration/test_pipeline_hook_echo_injection.py`（新建）
- 准备两个 fake chapter drafts，跑 P1 injection，验证 shared_context.hook_echo_block 是非空字符串
- 验证字符串里包含上一章的具体 token

**验收**：
- 跑 ch2 pipeline，trace 里 hook_echo_block.chars > 0

---

#### Task D: Cast Compliance Gate（post-write）

**问题**：canon_guardrails 已在 prompt 里写"禁止 X"，但 LLM 仍可能生成 X（已实测验证：3 次迭代均失败）。需要后置硬性 gate 检测。

**新建文件**：`src/bestseller/services/cast_compliance_gate.py`

**核心 API**：
```python
@dataclass(frozen=True)
class CastViolation:
    subject: str
    chapter_position: int
    pattern_matched: str  # or "name_appears_before_threshold"
    severity: str  # "critical"
    detail: str

@dataclass(frozen=True)
class CastComplianceReport:
    chapter_position: int
    violations: tuple[CastViolation, ...]
    passed: bool

def check_cast_compliance(
    chapter_text: str,
    chapter_position: int,
    guardrails: CanonGuardrails,
) -> CastComplianceReport:
    """For each state_rule with applies_after_chapter > current chapter,
    if subject name (or forbidden_pattern regex) appears in text, raise
    a critical violation."""
```

**逻辑**：
- 对每条 state_rule：
  - 若 `applies_after_chapter is not None` 且 `chapter_position <= applies_after_chapter`
  - 检查 `forbidden_patterns` 里每个 regex 在 chapter_text 里命中
  - **额外**检查 subject 本身（如"裴镜渊"3 字）在 chapter_text 出现次数 ≥ 2（容忍 1 次"被提及"，但 ≥ 2 次视为"在场")
  - 命中即记录 CastViolation
- 对每个 `forbidden_terms`（绝对禁用词），命中即记录

**接入 auto-repair**：把 `CAST_VIOLATION` 加入 `AUTO_REPAIR_RETENTION_CODES`。

**测试**：`tests/unit/test_cast_compliance_gate.py`，至少 10 个测试。包括：
- 裴镜渊 在 ch2 出现 5 次 → critical violation
- 裴镜渊 在 ch20 出现 5 次 → passed（已过临界章）
- "守夜人" 词在任何章节出现 → critical（绝对禁用）
- 裴镜渊 在 ch2 仅作"账名"被提及 1 次 → passed（容忍阈值）

**验收**：
- 用 v11/v13 ch2 文本跑 gate，验证 CAST_VIOLATION 被记录
- 用 v10 ch2 文本跑 gate，验证 passed

---

### 🟡 P1 — 自修复质量

#### Task E: Hook Echo 语义匹配升级

**问题**：当前 hook_echo_gate 用**严格子串匹配**。LLM 用同义改写就漏掉。比如"上一章末尾留下'倒计时'"，LLM 改写"时间在倒着走"，不会被 gate 命中。

**升级方向（两选一）**：

**方案 A — 同义词表/embedding**（推荐先做）
- 维护一个小型同义词字典：`倒计时 ↔ 时间在走 / 倒数 / 时限`
- 用 `synonyms` 包或 jieba + custom dict
- 在 `check_hook_echo` 里同时匹配 token 本身和它的同义词

**方案 B — LLM-as-judge**
- 调用 critic LLM，给定 prev hook tokens 和 current chapter text，让它判断"是否被语义呼应"
- 成本高，作为 fallback

**文件**：`src/bestseller/services/hook_echo_gate.py`

**测试**：扩展现有 `tests/unit/test_hook_echo_gate.py`，新增 5 个测试覆盖同义改写场景。

**验收**：
- 跑修订版 ch2 vs 原版 ch1，hook echo coverage 从 35% 升到 ≥ 55%

---

#### Task F: Retention 重试预算 + 升级到人工审核

**问题**：现有 auto-repair 在 3 次重试后标 machine_blocked。但 retention 错误的重试需要独立预算（hook echo 比 length 难修），且需要"逐步收紧 prompt"——前 2 次正常重试，第 3 次加更严厉的提示词。

**文件**：
- `src/bestseller/services/quality_gates_config.py` — `OriginalityEngineConfig` 新增字段
- `src/bestseller/services/pipelines.py` — auto-repair 路径

**新增字段**：
```python
@dataclass(frozen=True)
class OriginalityEngineConfig:
    # 现有字段...
    retention_max_retries: int = 5
    retention_escalate_after: int = 3  # 第几次重试开始用 strict prompt
```

**逻辑**：
- 每次 retention 触发重试时，递增 `chapter.metadata_json["retention_retry_count"]`
- 若 retry_count > retention_escalate_after，在 prompt 里追加 "this is your N-th attempt, strictly comply or you will be replaced by manual revision"
- 若 retry_count > retention_max_retries，标 `requires_human_review=True`，停止自动重试

**测试**：`tests/unit/test_retention_retry_budget.py`

---

#### Task G: Signature Scene 语义合规检查

**问题**：现有 signature_scene compliance 只 check `must_include_line/image` 字符串是否在文本里。LLM 可能写出符合 archetype 精神但没用具体短语的场景。

**升级**：用一个轻量级 critic LLM 调用判断"本章是否兑现了 revelation/oath_bound 的 archetype 精神"。

**新文件**：`src/bestseller/services/signature_scene_critic.py`

**接入**：retention_safety_gate 调用 signature 合规时，先 substring check（快），不通过再调 critic（精确）。

---

### 🟢 P2 — 工具化与端到端

#### Task H: Maintenance CLI 加 retention-repair 命令

**目标**：让用户一条命令重写整段 problematic 章节。

**文件**：`src/bestseller/cli/maintenance.py`

**新命令**：
```bash
bestseller maintenance retention-repair \
  --slug exorcist-detective-1778051012 \
  --chapter 1 2 3 4 5 \
  --max-retries 3 \
  [--dry-run]
```

**逻辑**：
- 对每个指定章节：reset 所有场景为 needs_rewrite + 标 retention block codes
- 串行调 `run_chapter_pipeline`
- 每次跑完审计，记录 retention_gate_last_findings
- 输出最终 retention 通过/失败统计

**测试**：`tests/unit/test_maintenance_retention_repair_cli.py`（用 mocked pipeline）

---

#### Task I: Docker 镜像内含新代码

**问题**：当前 Docker 镜像是旧的，新代码只在本地 venv 生效。worker 池不会自动用新框架。

**修改**：
- `docker-compose.yml` 或 `Dockerfile`，确保 build 步骤 COPY 新文件
- 加 `scripts/rebuild_and_verify.sh`：
  ```bash
  docker-compose build worker api
  docker-compose up -d worker api
  docker exec bestseller-worker-1 python -m pytest /app/tests/unit/test_retention_safety_gate.py -q
  ```

**验收**：
- `docker exec bestseller-worker-1 python -c "from bestseller.services.retention_safety_gate import evaluate_retention_safety; print('OK')"` 输出 OK

---

#### Task J: 集成测试 — 完整 retention 闭环

**新建文件**：`tests/integration/test_retention_full_loop.py`

**测试场景**：
1. Mock LLM 生成一个"hook echo 失败"的 ch2
2. 跑 pipeline → 应触发 retention_safety_gate → 应触发 auto-repair → 应在新的 prompt 里看到 hook_echo_block
3. Mock LLM 第二次返回一个"hook echo 成功"的 ch2 → pipeline 应判定 passed → chapter status="complete", production_state="ok"

**验收**：CI 跑通这个 test。

---

## 验收清单（所有 P0/P1 任务完成后）

跑下列命令并核对输出：

```bash
# 1. 全量单测过
.venv/bin/python -m pytest tests/unit/ --no-cov -q
# 期望: ≥ 4600 passed

# 2. 集成测试过
.venv/bin/python -m pytest tests/integration/ --no-cov -q
# 期望: 所有 retention loop tests 通过

# 3. 重置 ch2 + 跑 pipeline
docker exec bestseller-db-1 psql -U bestseller -d bestseller -c "
  UPDATE scene_cards SET status='needs_rewrite' WHERE chapter_id=(...) AND chapter_number=2;
"
.venv/bin/bestseller chapter pipeline exorcist-detective-1778051012 2

# 4. 检验产出
.venv/bin/python /tmp/audit_qingnang_revised.py
# 期望:
#   - ch2 Hook Echo coverage ≥ 50%
#   - ch2 无 裴镜渊 出现（cast compliance pass）
#   - ch1/2/3 signature scene mandate 全部命中
```

---

## 风险与限制（必须诚实告知用户）

1. **LLM 的"听话度"不可保证**：即使 prompt 写得很严，某些 LLM 在某些上下文下仍会违反规则。Cast Compliance Gate (Task D) 是兜底，但代价是更多重试。

2. **Hook Echo 语义匹配难做完美**：方案 A（同义词）只能覆盖常见改写；方案 B（LLM judge）成本高且有自身可靠性问题。建议把 60% coverage 作为可达目标，而非 80%。

3. **自修复无法替代真实读者数据**：本框架的"榜单级"是基于 gate 的代理指标，最终留存率只能由番茄/起点真实数据验证。**强烈建议**：每次发书后回喂真实读者曲线到框架，校准 persona simulator 的权重。

4. **章节连贯性永远是脆弱的**：哪怕 P0+P1 全部做完，也无法保证 LLM 不在某次重写中破坏跨章逻辑。**强烈建议**：每次 retention 重写后做人工 spot check，不要无监督批量重写。

---

## 总工作量估算

| 任务 | 工作量 | 难度 |
|---|---|---|
| A (post-write 评估器接入) | 0.5 天 | 低 |
| B (chapter rewrite prompt 接入) | 0.3 天 | 低 |
| C (prev_chapter_text 传值) | 0.3 天 | 低 |
| D (Cast Compliance Gate) | 1 天 | 中 |
| E (Hook Echo 语义) | 1-2 天 | 中 |
| F (Retry 预算) | 0.5 天 | 低 |
| G (Signature 语义) | 1 天 | 中 |
| H (CLI 命令) | 0.3 天 | 低 |
| I (Docker rebuild) | 0.3 天 | 低 |
| J (集成测试) | 0.5 天 | 中 |
| **合计** | **5-7 天** | |

---

## 完成后给我（验收方）的产物清单

1. 全量单测通过截图
2. 集成测试通过截图
3. 三次完整 ch2 pipeline 运行的 trace JSON（含 hook_echo / canon / signature 三个 block 都有 chars > 0）
4. ch2 输出的 v15 文本（或更新版本号），手动 diff 与 v10 的差异
5. retention_gate_last_findings 字段在 ch2 metadata 上的 JSON dump
6. Docker 内 `python -c "from bestseller.services.cast_compliance_gate import check_cast_compliance; print('OK')"` 输出
