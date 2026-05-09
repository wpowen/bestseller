# Qimao Signing Regeneration Framework Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Upgrade the generation framework so a book regenerated for Qimao is planned, drafted, reviewed, and repaired around Qimao signing-readiness instead of being corrected only after rejection.

**Architecture:** Convert the 26 Qimao “from rejection to signing” lessons and the current rejection reasons into a multi-stage contract. The contract starts at conception, becomes structured planning artifacts before chapter generation, is injected into draft/rewrite prompts, and is enforced by deterministic gates plus LLM review before the project can continue past the opening batch.

**Tech Stack:** Python 3, YAML methodology config, Typer CLI, SQLAlchemy project metadata, existing conception/planner/drafts/reviews/pipelines modules, deterministic validators, `uv run pytest`.

---

## What This Plan Is Solving

This is not a plan to polish one rejected manuscript.

The current rejection reasons:

- 文笔还有待提升
- 代入感较弱
- 开篇切入点比较普通
- 缺乏足够吸引力
- 故事叙述较为平淡

The Qimao experience posts show that these are symptoms of a generation-system problem:

- The framework can generate a coherent book, but it may not generate a Qimao-compatible opening.
- The framework can write chapters, but it does not yet hard-block “ordinary entry + weak immersion + flat narration” before the book expands.
- The framework can inject Qimao rules into writing prompts, but prompt guidance alone is not enough; the opening contract must exist as structured state and quality gates.
- A regenerated book needs Qimao logic from the first conception proposal, not as a late rewrite note.

The required change is therefore:

```text
Qimao signing experience
  -> framework contract
  -> conception constraints
  -> first-10k structured plan
  -> draft prompt injection
  -> deterministic opening gate
  -> LLM editor review
  -> automatic rewrite/replan loop
  -> only then continue generation
```

## Core Rules Extracted From the 26 Posts

These are the rules the framework must enforce.

### 1. Platform Fit Comes Before Creativity

Qimao is mobile free-reading. The opening must be faster than paid-platform slow burn, steadier than exaggerated short-video/new-media emotion, and focused on immediate reader reward.

Framework implication:

- Every Qimao project needs an explicit `platform_target = "七猫"` contract.
- The planner must reject slow opening concepts, pure worldbuilding openings, and “literary atmosphere first” openings.
- The framework should prefer mature genre frames with one clear twist, not cold-start experimentation.

### 2. The First Page Is a Signing Filter

The posts repeatedly compress “黄金三章” into “黄金一章/第一页”.

Framework implication:

- protagonist spotlight by 100 Chinese characters
- visible conflict by 200 Chinese characters
- core contradiction by 600 Chinese characters
- emotional hook by 2,000 Chinese characters
- mainline direction by 6,000 Chinese characters
- repeatable serial loop by 10,000 Chinese characters

### 3. Opening Is a Product Loop, Not a Paragraph

Signing-level opening means:

```text
forced situation -> protagonist action -> visible pressure -> small reward/cost -> next hook
```

Framework implication:

- Chapter 1 cannot start with background, normal day, travel, scenery, or explanatory setup.
- Chapter 2 must show the protagonist's differentiating edge through pressure.
- Chapter 3 must deliver a small payoff and open the next loop.

### 4. Immersion Is POV + Choice + Consequence

Weak immersion is not only prose style. It appears when the protagonist is not the lens of pressure and decision.

Framework implication:

- Drafts must be checked for protagonist agency in the first scene.
- Paragraphs should anchor in action, sensory detail, dialogue pressure, environment interaction, or immediate consequence.
- Early chapters should limit named characters and side branches.

### 5. “文笔弱” Is Mostly Non-Experiential Writing

For Qimao, better prose means concrete, readable, action-led prose, not ornate prose.

Framework implication:

- Replace abstract emotion and flat summary with physical action, sensory result, concrete social pressure, and consequences.
- Add a style gate for explanation-heavy openings.

### 6. Rejection Repair Must Rebuild Structure Before Polish

The posts consistently warn against only changing wording after rejection.

Framework implication:

- If rejection says “开篇普通/吸引力不足/叙述平淡”, the repair path must first replan the opening incident and golden-three loop.
- Rewrite tasks must carry structured rejection causes, not generic “please improve”.

## Target Regeneration Flow

After this plan is implemented, Qimao regeneration should behave like this:

1. User chooses Qimao target or a Qimao preset.
2. Conception produces only Qimao-compatible premises.
3. Planner generates a `qimao_opening_contract` before chapter outlines.
4. A deterministic planning gate checks the contract before any prose is written.
5. Chapter 1-3 are generated with explicit first-page and golden-three constraints.
6. A deterministic opening gate runs after Chapter 1 and after Chapter 3.
7. If the opening fails, the pipeline creates targeted rewrite/replan tasks and blocks continuation.
8. The project may continue past Chapter 3 only when the Qimao opening gate passes.
9. The first 10,000 words receive a second gate before the project generates the long body.

## Task 1: Promote Qimao Lessons Into a Regeneration Contract

**Files:**
- Modify: `config/writing_methodology.yaml`
- Modify: `src/bestseller/services/methodology.py`
- Test: `tests/unit/test_methodology_qimao.py`

**Step 1: Add config section**

Add a structured contract separate from the existing `qimao_signing_gate`:

```yaml
qimao_regeneration_contract:
  target_platform: 七猫
  purpose: 再生成时从立项开始满足七猫签约口径。
  non_negotiables:
    - 平台适配优先于作者自我表达。
    - 第一页必须有异常、危机、误会、侮辱、损失、利益冲突或被迫选择。
    - 前三章必须完成冲突、优势、小爽点、下一轮钩子。
    - 前一万字必须证明可重复连载循环。
    - 文笔提升优先靠动作、感官、环境交互和具体后果，不靠华丽修辞。
  rejection_cause_map:
    weak_prose: 文笔还有待提升
    weak_immersion: 代入感较弱
    ordinary_entry: 开篇切入点比较普通
    weak_attraction: 缺乏足够吸引力
    flat_narration: 故事叙述较为平淡
  regeneration_decision_order:
    - 先重选开篇事件
    - 再重设主角即时目标和损失
    - 再重排黄金三章爽点闭环
    - 再检查前一万字主线循环
    - 最后做文笔质感修复
```

**Step 2: Add loader models**

In `src/bestseller/services/methodology.py`, add:

```python
@dataclass(frozen=True)
class QimaoRegenerationContract:
    target_platform: str
    non_negotiables: tuple[str, ...]
    rejection_cause_map: dict[str, str]
    regeneration_decision_order: tuple[str, ...]
```

Add:

```python
def get_qimao_regeneration_contract() -> QimaoRegenerationContract:
    ...
```

**Step 3: Add prompt renderer**

Add:

```python
def render_qimao_regeneration_contract(
    *,
    platform_target: str | None,
    language: str | None,
    rejection_reasons: str | None = None,
) -> str:
    ...
```

This renderer should return an empty string unless the platform target is Qimao and the project is Chinese.

**Step 4: Tests**

Add tests proving:

- contract loads from YAML
- Qimao target renders the contract
- non-Qimao target does not render it
- English projects do not receive Chinese Qimao rules
- known rejection text renders the mapped causes

**Step 5: Verify**

Run:

```bash
uv run pytest tests/unit/test_methodology_qimao.py -q --no-cov
```

Expected: pass.

## Task 2: Add a Qimao Project Preset and Platform Target

**Files:**
- Modify: `src/bestseller/services/conception.py`
- Modify: `src/bestseller/services/writing_presets.py`
- Modify: `src/bestseller/services/writing_profile.py`
- Test: `tests/unit/test_conception_services.py` or existing relevant conception tests

**Step 1: Ensure writing profile carries Qimao target**

The generated `writing_profile.market` must include:

```json
{
  "platform_target": "七猫",
  "reader_promise": "...",
  "selling_points": ["..."],
  "hook_keywords": ["..."],
  "chapter_hook_strategy": "...",
  "opening_contract": "..."
}
```

**Step 2: Add Qimao preset override**

Add a preset or override that forces:

- mobile free-reading pace
- first-three-chapter high hook density
- minimum one payoff/emotional reward per chapter
- clear protagonist edge before Chapter 3
- no slow-burn opening

**Step 3: Conception prompt integration**

In `run_conception_pipeline`, pass the Qimao contract into:

- commercial positioning
- market proposal
- character proposal
- world proposal
- chief editor review
- finalizer

The finalizer must not return a premise unless it can answer:

```text
第一章为什么能点开？
前三章读者为什么继续？
前一万字可重复的爽点循环是什么？
```

**Step 4: Tests**

Use fallback/fake LLM outputs to prove a Qimao target produces `platform_target="七猫"` and a non-empty `opening_contract`.

## Task 3: Add a Pre-Generation Qimao Opening Contract Artifact

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Modify: `src/bestseller/domain/project.py` or relevant planning schema if needed
- Test: `tests/unit/test_planner_services.py`

**Step 1: Add artifact structure**

The planner must create a structured artifact before chapter outlines:

```json
{
  "qimao_opening_contract": {
    "opening_incident": "...",
    "first_page_conflict": "...",
    "protagonist_immediate_goal": "...",
    "visible_loss_if_fail": "...",
    "protagonist_edge": "...",
    "edge_limit": "...",
    "chapter_1_small_turn": "...",
    "chapter_2_reveal": "...",
    "chapter_3_payoff": "...",
    "first_10000_loop": "trigger -> action -> reward/cost -> next hook",
    "forbidden_opening_modes": ["background_exposition", "normal_day", "scenery_first"]
  }
}
```

**Step 2: Persist artifact**

Store it in `project.metadata_json["qimao_opening_contract"]`.

**Step 3: Planner prompt requirements**

The planner must produce the contract before the first volume/chapter outline. If it cannot, the fallback should be explicit and fail the gate instead of silently generating a generic outline.

**Step 4: Tests**

Prove:

- Qimao projects get `qimao_opening_contract`
- non-Qimao projects do not require it
- missing required fields fail validation

## Task 4: Add a Qimao Planning Gate

**Files:**
- Create: `src/bestseller/services/qimao_planning_gate.py`
- Test: `tests/unit/test_qimao_planning_gate.py`

**Step 1: Implement deterministic report**

Create:

```python
@dataclass(frozen=True)
class QimaoPlanningFinding:
    code: str
    severity: str
    message: str
    evidence: str

@dataclass(frozen=True)
class QimaoPlanningGateReport:
    passed: bool
    findings: tuple[QimaoPlanningFinding, ...]
```

**Step 2: Gate checks**

Fail if:

- no opening incident
- opening incident is ordinary setup/background/scenery/travel/waking
- protagonist immediate goal is missing
- visible loss if fail is missing
- protagonist edge is missing by Chapter 2
- Chapter 3 has no payoff
- first-10k loop is not a loop

**Step 3: Tests**

Create a bad fixture and a good fixture.

Run:

```bash
uv run pytest tests/unit/test_qimao_planning_gate.py -q --no-cov
```

## Task 5: Wire Planning Gate Into Project Pipeline Before Drafting

**Files:**
- Modify: `src/bestseller/services/pipelines.py`
- Test: `tests/unit/test_pipeline_services.py`

**Step 1: Insert gate after foundation/planning materialization**

In `run_project_pipeline`, after the planning artifacts are produced and before Chapter 1 scene drafting begins:

```python
if is_qimao_project(project):
    report = evaluate_qimao_planning_gate(project.metadata_json)
    project.metadata_json["qimao_planning_gate_report"] = report_to_dict(report)
    if not report.passed:
        block or request replan
```

**Step 2: Blocking behavior**

Do not generate Chapter 1 if planning gate has critical failures.

**Step 3: Replan behavior**

If a replan loop exists, create a replan task. If not, raise a clear error:

```text
Qimao planning gate failed: opening incident/mainline/payoff contract missing.
```

**Step 4: Tests**

Prove the pipeline does not proceed to drafting when the Qimao opening contract is missing.

## Task 6: Inject Qimao Contract Into Draft and Rewrite Prompts

**Files:**
- Modify: `src/bestseller/services/drafts.py`
- Modify: `src/bestseller/services/reviews.py`
- Modify: `src/bestseller/services/methodology.py`
- Test: `tests/unit/test_hype_draft_plumbing.py`
- Test: `tests/unit/test_review_services.py`

**Step 1: Draft prompt injection**

`build_scene_draft_prompts` must receive:

- `qimao_opening_contract`
- `editor_rejection_reasons`
- `qimao_regeneration_contract`

For Chapters 1-3, the prompt must include:

```text
本章不是自由发挥；必须执行 qimao_opening_contract 对应章节任务。
```

**Step 2: Rewrite prompt injection**

`build_chapter_rewrite_prompts` and `build_scene_rewrite_prompts` must include the same contract. If rejection reasons are present, they must be rendered as:

```text
这不是润色任务；优先重建切入点、代入、冲突、爽点闭环。
```

**Step 3: Tests**

Prove a Qimao Chapter 1 draft prompt contains:

- `七猫签约门槛`
- `七猫再生成合同`
- `qimao_opening_contract`
- first-page thresholds
- golden-three task

## Task 7: Add a Deterministic Qimao Opening Gate

**Files:**
- Create: `src/bestseller/services/qimao_opening_gate.py`
- Test: `tests/unit/test_qimao_opening_gate.py`

**Step 1: Gate scope**

Run on generated prose after:

- Chapter 1 draft
- Chapter 3 draft
- first 10,000 words

**Step 2: Checks**

The gate should be conservative and deterministic:

- protagonist appears in the first 100-150 Chinese characters
- first 300 Chinese characters contain conflict/action/dialogue pressure
- first 800 Chinese characters are not dominated by exposition
- named entities in first 1,000 Chinese characters are capped
- Chapter 1 ending has a concrete hook
- Chapter 1-3 contain at least one classifiable reward/payoff signal
- first 10,000 words have no chapter with zero conflict loop markers

**Step 3: Finding codes**

Use:

- `ordinary_entry`
- `weak_immersion`
- `weak_hook`
- `flat_narration`
- `weak_golden_three_payoff`
- `first_10k_loop_missing`

**Step 4: Tests**

Fixtures:

- bad: background/worldbuilding opening
- bad: protagonist absent opening
- bad: conflict-free chapter ending
- good: action/conflict opening with hook

Run:

```bash
uv run pytest tests/unit/test_qimao_opening_gate.py -q --no-cov
```

## Task 8: Convert Gate Failures Into Targeted Rewrite Tasks

**Files:**
- Modify: `src/bestseller/services/reviews.py`
- Modify: `src/bestseller/services/pipelines.py`
- Test: `tests/unit/test_review_services.py`
- Test: `tests/unit/test_pipeline_services.py`

**Step 1: Instruction builder**

Add a pure function:

```python
def build_qimao_opening_rewrite_instructions(
    findings: tuple[QimaoOpeningFinding, ...],
    *,
    chapter_number: int,
    opening_contract: dict[str, Any],
    rejection_reasons: str | None,
) -> str:
    ...
```

**Step 2: Strategy mapping**

Map findings to rewrite strategy:

- `ordinary_entry` -> `qimao_opening_incident_rewrite`
- `weak_immersion` -> `qimao_pov_immersion_rewrite`
- `weak_hook` -> `qimao_hook_rebuild`
- `flat_narration` -> `qimao_conflict_loop_rewrite`
- `weak_golden_three_payoff` -> `qimao_golden_three_payoff_rewrite`

**Step 3: Create rewrite tasks**

When gate fails, create `RewriteTaskModel` with:

- trigger_type: `qimao_opening_gate`
- rewrite_strategy: mapped strategy
- priority: high
- instructions: generated targeted instructions
- metadata: findings and contract snapshot

**Step 4: Blocking behavior**

Pipeline must not continue beyond Chapter 3 when the Qimao opening gate has critical findings.

## Task 9: Add First-10k Batch Gate Before Long-Form Continuation

**Files:**
- Modify: `src/bestseller/services/pipelines.py`
- Modify: `src/bestseller/services/commercial_novel_gate.py` or create `src/bestseller/services/qimao_first_10k_gate.py`
- Test: `tests/unit/test_pipeline_services.py`
- Test: `tests/unit/test_qimao_opening_gate.py`

**Step 1: Batch checkpoint**

After the generated output reaches roughly 10,000 Chinese characters or after the first 5-10 chapters, run:

```python
evaluate_qimao_first_10k_gate(chapter_texts, opening_contract)
```

**Step 2: Checks**

Fail if:

- protagonist goal is not repeated/reinforced
- main conflict is replaced by side branches
- more than one chapter has no visible conflict/reward/hook loop
- no clear payoff appears in first three chapters
- prose remains explanation-heavy

**Step 3: Pipeline behavior**

If failed:

- generate repair tasks for affected chapters
- do not continue generating the long body

## Task 10: Add CLI Diagnostics for Qimao Regeneration

**Files:**
- Modify: `src/bestseller/cli/main.py`
- Test: `tests/unit/test_cli.py`

**Step 1: Commands**

Add:

```bash
uv run bestseller qimao planning-gate <project_slug>
uv run bestseller qimao opening-gate <project_slug>
uv run bestseller qimao first-10k-gate <project_slug>
```

**Step 2: JSON output**

Each command emits:

```json
{
  "project_slug": "...",
  "gate": "qimao_opening_gate",
  "passed": false,
  "findings": [...],
  "recommended_actions": [...]
}
```

**Step 3: Tests**

Mock service calls and verify CLI JSON shape.

## Task 11: Regeneration Acceptance Test Fixture

**Files:**
- Create: `tests/unit/test_qimao_regeneration_contract.py`
- Create or modify fixtures under `tests/fixtures/`

**Step 1: Good fixture**

A good generated opening fixture must include:

- immediate protagonist action
- visible pressure
- concrete loss if fail
- protagonist edge by Chapter 2
- payoff by Chapter 3
- hook at each chapter end

**Step 2: Bad fixture**

Bad fixture mirrors the rejection:

- ordinary setting introduction
- weak protagonist POV
- mostly explanation
- no concrete first-page conflict
- no first-three payoff

**Step 3: Tests**

Run the planning gate and opening gate on both fixtures. Good must pass; bad must fail with the same codes as the rejection.

## Task 12: Regenerate a Qimao Candidate From Scratch

**Files/Outputs:**
- Output: `output/<new_qimao_project_slug>/chapter-001.md` through first 10,000 words
- Output: `output/<new_qimao_project_slug>/qimao-gate-report.json`
- Output: `docs/reviews/<new_qimao_project_slug>-qimao-regeneration-report.md`

**Step 1: Create with Qimao target**

Run the normal generation entrypoint with Qimao platform metadata or preset.

**Step 2: Confirm planning gate**

Before writing prose, confirm:

- `project.metadata_json["qimao_opening_contract"]` exists
- planning gate passes

**Step 3: Generate first batch only**

Generate Chapters 1-3 first. Do not allow the project to continue until:

- Chapter 1 gate passes
- golden-three gate passes

**Step 4: Generate to first 10,000 words**

Continue only after the opening passes. Then run first-10k gate.

**Step 5: Produce regeneration report**

Report must include:

- how the new opening avoids the rejection reasons
- the first-page conflict
- the protagonist immediate goal/loss
- Chapter 1-3 payoff loop
- first-10k loop
- remaining risks

## Task 13: Final Verification Matrix

**Files:**
- Create: `docs/qimao-regeneration-verification-matrix.md`

**Step 1: Matrix rows**

Include:

| Requirement | Source | Framework Enforcement | Test | Status |
| --- | --- | --- | --- | --- |
| 平台适配 | 26篇经验 | Qimao preset + conception prompts | test_conception | pending |
| 第一页冲突 | 26篇经验/拒稿原因 | qimao_opening_gate | test_qimao_opening_gate | pending |
| 代入感 | 拒稿原因 | prompt + prose gate | test_qimao_opening_gate | pending |
| 前三章爽点 | 26篇经验 | hype/golden-three gate | existing + new tests | pending |
| 前一万字循环 | 26篇经验 | first-10k gate | new tests | pending |
| 拒稿原因修复 | 用户反馈 | rewrite task mapping | review tests | pending |

**Step 2: Exit criteria**

The framework is ready for Qimao regeneration only when:

- Qimao planning gate passes before prose generation.
- Chapter 1 deterministic opening gate passes.
- Golden-three gate passes.
- First-10k gate passes.
- Rewrite tasks are targeted to rejection causes, not generic polish.
- A regenerated candidate has a report showing exactly how it avoids the rejection chain.

## Implementation Order

Implement in this order:

1. Config + methodology contract (Task 1)
2. Qimao preset/conception integration (Task 2)
3. Planner opening contract artifact (Task 3)
4. Planning gate (Task 4)
5. Pipeline planning gate block (Task 5)
6. Draft/rewrite prompt injection (Task 6)
7. Opening gate (Task 7)
8. Rewrite task mapping (Task 8)
9. First-10k gate (Task 9)
10. CLI diagnostics (Task 10)
11. Fixture tests (Task 11)
12. Regenerate candidate (Task 12)
13. Verification matrix (Task 13)

## Verification Commands

Focused:

```bash
uv run pytest tests/unit/test_methodology_qimao.py -q --no-cov
uv run pytest tests/unit/test_qimao_planning_gate.py -q --no-cov
uv run pytest tests/unit/test_qimao_opening_gate.py -q --no-cov
uv run pytest tests/unit/test_qimao_regeneration_contract.py -q --no-cov
```

Integration:

```bash
uv run pytest tests/unit/test_pipeline_services.py tests/unit/test_review_services.py tests/unit/test_cli.py -q --no-cov
```

Syntax:

```bash
uv run python -m py_compile \
  src/bestseller/services/methodology.py \
  src/bestseller/services/conception.py \
  src/bestseller/services/planner.py \
  src/bestseller/services/pipelines.py \
  src/bestseller/services/drafts.py \
  src/bestseller/services/reviews.py \
  src/bestseller/services/qimao_planning_gate.py \
  src/bestseller/services/qimao_opening_gate.py \
  src/bestseller/cli/main.py
```

Manual regeneration acceptance:

```bash
uv run bestseller qimao planning-gate <project_slug>
uv run bestseller qimao opening-gate <project_slug>
uv run bestseller qimao first-10k-gate <project_slug>
```

## Non-Goals

- Do not copy any Qimao post text or any ranking-book protected expression.
- Do not optimize for literary prose at the cost of first-page conflict.
- Do not allow a Qimao project to continue long-form generation when Chapter 1-3 fail.
- Do not treat editor rejection as a single rewrite instruction; it must become structured causes and gates.
