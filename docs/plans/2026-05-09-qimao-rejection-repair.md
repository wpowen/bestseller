# Qimao Rejection Repair Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Turn the current rejection feedback into a repeatable repair workflow that rewrites the first 10,000 words toward Qimao signing readiness.

**Architecture:** Repair happens in two layers. The manuscript layer rewrites the opening incident, POV immersion, chapter propulsion, and prose texture. The framework layer adds a rejection-repair gate so future drafts and rewrite tasks receive the same explicit editor-facing constraints before generation, review, and repair.

**Tech Stack:** Python 3, Typer CLI, SQLAlchemy models, YAML methodology config, existing review/rewrite pipeline, `uv run pytest`.

---

## Rejection Diagnosis

Editor feedback:

- 文笔还有待提升
- 代入感较弱
- 开篇切入点比较普通
- 缺乏足够吸引力
- 故事叙述较为平淡

Framework interpretation:

These are not five unrelated issues. They describe one opening-retention failure chain:

1. The first screen does not create an abnormal situation, visible pressure, or forced choice.
2. The protagonist is not close enough to the reader's senses, decisions, losses, and immediate desire.
3. Paragraphs explain what is happening instead of making readers experience what changes.
4. Chapter beats move as narration instead of conflict: goal, obstacle, action, consequence, hook.
5. Prose polish cannot fix the draft until the opening incident and short payoff loop are rebuilt.

Qimao-specific implication:

- First 100 words: protagonist must be the spotlight.
- First 200 words: readable conflict must appear.
- First 600 words: core conflict must be legible.
- First 2,000 words: emotional hook must be formed.
- First 6,000 words: mainline goal, obstacle, and action direction must be clear.
- First 10,000 words: the story must prove a repeatable serial loop.

## Repair Standard

The revised first 10,000 words should pass this practical editor check:

- A reader can state in one sentence what the protagonist wants now.
- A reader can state what will be lost if the protagonist fails.
- A reader can feel the protagonist's pressure through action, sensory detail, and concrete consequence.
- Every scene has a visible conflict or information change.
- Every chapter contains a small emotional reward, power shift, reveal, or status movement.
- Every chapter ending creates a concrete reason to read the next chapter.
- The prose has fewer abstract judgments and more physical actions, dialogue pressure, and environment interaction.

## Manuscript Repair Plan

### Task 1: Build the First-10k Failure Map

**Files:**
- Read: `output/<project_slug>/chapter-001.md`
- Read: `output/<project_slug>/chapter-002.md`
- Read: `output/<project_slug>/chapter-003.md`
- Optional Read: `output/<project_slug>/chapter-004.md` through `output/<project_slug>/chapter-010.md`
- Create: `docs/reviews/<project_slug>-qimao-opening-diagnostic.md`

**Step 1: Mark the five hard positions**

For the first 10,000 words, record:

- first protagonist appearance
- first visible conflict
- first core conflict explanation through action
- first emotional hook
- first mainline goal statement
- first small payoff
- first chapter-end hook

**Step 2: Classify each rejection reason**

Use these codes:

- `ordinary_entry`: opening begins with normal setup, background, travel, waking, scenery, or explanation.
- `weak_immersion`: POV stays outside the protagonist's body, choices, senses, and immediate losses.
- `weak_hook`: no forced question, threat, humiliation, mistake, betrayal, deadline, or abnormality.
- `flat_narration`: paragraphs summarize events without goal, obstacle, action, consequence.
- `thin_prose`: prose relies on adjectives and explanations instead of action, sensory result, and dialogue pressure.

**Step 3: Write a one-page diagnostic**

Use this structure:

```markdown
# <project_slug> 七猫开篇拒稿诊断

## Verdict
当前稿不能只润色，需要重做第一章切入点，并连带重排前三章爽点循环。

## Hard Positions
- 主角登场：第 X 字，是否合格：
- 可感冲突：第 X 字，是否合格：
- 核心矛盾：第 X 字，是否合格：
- 情绪钩子：第 X 字，是否合格：
- 主线方向：第 X 字，是否合格：

## Failure Codes
- ordinary_entry:
- weak_immersion:
- weak_hook:
- flat_narration:
- thin_prose:

## Rewrite Priority
1. 换第一章切入点
2. 收紧主角视角
3. 重排前三章小爽点
4. 最后才做文笔润色
```

**Step 4: Verification**

The diagnostic is complete only if it identifies exact paragraphs to delete, keep, move, or rewrite.

### Task 2: Redesign Chapter 1 Entry Point

**Files:**
- Modify: `output/<project_slug>/chapter-001.md`
- Reference: `docs/reviews/<project_slug>-qimao-opening-diagnostic.md`

**Step 1: Choose a stronger first incident**

Replace ordinary setup with one of these entry shapes:

- public humiliation plus immediate counter-choice
- betrayal or false accusation that costs the protagonist something concrete
- abnormal discovery that forces action within the scene
- deadline, debt, order, inspection, chase, punishment, or loss
- protagonist using a small edge under pressure, then exposing a larger problem

**Step 2: Rewrite the first paragraph**

The first paragraph must contain:

- protagonist
- location or social situation
- immediate pressure
- an action or line of dialogue
- a question the reader wants answered

Bad opening pattern:

```text
背景介绍 + 世界观说明 + 主角状态描述 + 慢慢引出事件
```

Target opening pattern:

```text
动作/对白冲突 + 主角处境 + 具体损失 + 被迫选择
```

**Step 3: Rebuild the first 600 words**

Requirements:

- no standalone worldbuilding paragraph
- no more than 3-5 named characters
- the protagonist acts before the narrator explains
- conflict escalates at least once
- the reader sees what the protagonist may lose

**Step 4: Chapter 1 exit criteria**

Chapter 1 must end with:

- one small turn in the protagonist's favor or against them
- a clearer short-term target for Chapter 2
- an unanswered threat, opportunity, or information gap

### Task 3: Rebuild the Golden Three Chapters

**Files:**
- Modify: `output/<project_slug>/chapter-001.md`
- Modify: `output/<project_slug>/chapter-002.md`
- Modify: `output/<project_slug>/chapter-003.md`
- Create: `docs/reviews/<project_slug>-golden-three-repair-card.md`

**Step 1: Write the repair card**

```markdown
# 黄金三章修复卡

## Chapter 1
- 核心冲突：
- 主角欲望：
- 具体损失：
- 主角主动动作：
- 小爽点/小爆点：
- 章尾钩子：

## Chapter 2
- 承接钩子：
- 差异化优势/金手指：
- 能力边界：
- 短期目标：
- 新阻碍：
- 章尾钩子：

## Chapter 3
- 承接钩子：
- 反击或兑现：
- 第一个小高潮：
- 围观/关系/资源变化：
- 更大问题：
- 章尾钩子：
```

**Step 2: Rewrite Chapter 2**

Chapter 2 must reveal why this protagonist is worth following:

- show the protagonist's edge through a pressured choice
- make the edge useful but limited
- give a short-term objective that can be pursued in Chapter 3
- avoid turning the golden finger or premise into an explanation block

**Step 3: Rewrite Chapter 3**

Chapter 3 must deliver the first visible reward:

- status reversal
- resource gain
- truth reveal
- enemy miscalculation
- protagonist reputation shift
- relationship or power-distance change

**Step 4: Verification**

Read only chapter endings. If the next chapter's reason is not concrete, rewrite the ending before touching prose.

### Task 4: Convert Flat Narration into Conflict Loops

**Files:**
- Modify: `output/<project_slug>/chapter-001.md` through `output/<project_slug>/chapter-010.md`
- Create: `docs/reviews/<project_slug>-first-10k-loop-map.md`

**Step 1: Map each scene**

Each scene must fit:

```text
goal -> resistance -> escalation/info change -> reward/cost -> next hook
```

**Step 2: Delete non-moving paragraphs**

Delete or merge any paragraph that does not do at least one of:

- change protagonist goal
- increase obstacle
- reveal actionable information
- change relationship/power/status
- create cost
- create reward
- set up the next click

**Step 3: Add micro-conflicts to transition scenes**

Transition chapters still need:

- a small obstacle
- a small information gap
- a concrete decision
- a consequence that changes the next scene

**Step 4: Verification**

No chapter in the first 10 should have a scene whose only function is travel, explanation, waiting, scenery, or emotional restatement.

### Task 5: Repair Immersion and Prose Texture

**Files:**
- Modify: `output/<project_slug>/chapter-001.md` through `output/<project_slug>/chapter-010.md`

**Step 1: Close the camera**

For each important paragraph, make sure the sentence is anchored in:

- what the protagonist sees
- what the protagonist hears
- what pressure the body feels
- what choice the protagonist makes
- what consequence appears immediately

**Step 2: Replace abstract emotion**

Rewrite:

```text
他很愤怒。
她很紧张。
局面很危险。
众人十分震惊。
```

Into:

```text
他指节扣在桌沿，木刺扎进皮肉也没松手。
她把袖口攥出一团褶，开口时第一个字卡在喉咙里。
刀尖离他喉结只剩半寸，呼吸一重就能碰出血。
席间的杯盏声停了，连最爱起哄的人也往后退了一步。
```

**Step 3: Add environment interaction**

Characters cannot stand in a blank room. Make pressure touch the body:

- heat, cold, smell, dust, rain, blood, metal, paper, crowd, phone vibration, door sound
- use environment as conflict evidence, not decoration

**Step 4: Keep prose commercial**

Do not pursue ornate language. Qimao opening prose should be:

- specific
- direct
- sensory
- action-led
- emotionally readable

**Step 5: Verification**

In every 500 Chinese characters of Chapter 1, there should be at least one concrete action, one sensory or physical detail, and one change in pressure or information.

## Framework Integration Plan

### Task 6: Add Rejection-Repair Rules to Methodology Config

**Files:**
- Modify: `config/writing_methodology.yaml`
- Test: `tests/unit/test_methodology_qimao.py`

**Step 1: Write failing test**

Add a test that expects rejection repair rules to load:

```python
def test_qimao_rejection_repair_constraints_load_from_config() -> None:
    constraints = get_qimao_rejection_repair_constraints()

    assert "weak_immersion" in constraints.failure_codes
    assert "ordinary_entry" in constraints.failure_codes
    assert constraints.opening_rewrite_order[0] == "先换开篇切入点"
```

**Step 2: Add YAML section**

Add:

```yaml
qimao_rejection_repair_gate:
  failure_codes:
    ordinary_entry: 开篇切入普通，缺少异常/危机/误会/利益冲突。
    weak_immersion: 主角视角不近，读者感受不到选择、压力和损失。
    weak_hook: 没有足够吸引读者继续看的强问题。
    flat_narration: 叙述像流水账，缺少目标、阻碍、行动、后果。
    thin_prose: 文笔停留在解释和形容，缺少动作、感官和结果描写。
  opening_rewrite_order:
    - 先换开篇切入点
    - 再重排主角目标和冲突
    - 再补前三章爽点闭环
    - 最后润色文笔
```

**Step 3: Run test**

Run:

```bash
uv run pytest tests/unit/test_methodology_qimao.py -q --no-cov
```

Expected: new test fails before Python implementation.

### Task 7: Render Rejection-Repair Rules into Prompts

**Files:**
- Modify: `src/bestseller/services/methodology.py`
- Test: `tests/unit/test_methodology_qimao.py`

**Step 1: Add dataclass and loader**

```python
@dataclass(frozen=True)
class QimaoRejectionRepairConstraints:
    failure_codes: dict[str, str]
    opening_rewrite_order: tuple[str, ...]
    prose_repair_rules: tuple[str, ...]


def get_qimao_rejection_repair_constraints() -> QimaoRejectionRepairConstraints:
    cfg = load_methodology().get("qimao_rejection_repair_gate")
    cfg = cfg if isinstance(cfg, dict) else {}
    failure_codes = cfg.get("failure_codes")
    return QimaoRejectionRepairConstraints(
        failure_codes=failure_codes if isinstance(failure_codes, dict) else {},
        opening_rewrite_order=_as_tuple(cfg.get("opening_rewrite_order")),
        prose_repair_rules=_as_tuple(cfg.get("prose_repair_rules")),
    )
```

**Step 2: Add renderer**

```python
def render_qimao_rejection_repair_rules(
    *,
    rejection_reasons: str | None,
    chapter_number: int,
    platform_target: str | None,
    language: str | None,
) -> str:
    if not _is_qimao_target(platform_target):
        return ""
    if (language or "").lower().startswith("en"):
        return ""
    text = (rejection_reasons or "").strip()
    if not text:
        return ""
    constraints = get_qimao_rejection_repair_constraints()
    if not constraints.failure_codes:
        return ""
    lines = ["【七猫拒稿修复指令】"]
    lines.append(f"- 编辑拒稿原因：{text}")
    lines.append("- 修复顺序：" + " -> ".join(constraints.opening_rewrite_order))
    lines.append("- 本章必须避免的问题：")
    for code, description in constraints.failure_codes.items():
        lines.append(f"  - {code}: {description}")
    if chapter_number <= 3:
        lines.append("- 黄金三章优先级：切入点、主角代入、短期冲突、小爽点、章尾钩子。")
    return "\n".join(lines)
```

**Step 3: Extend existing renderer**

Extend `render_methodology_scene_rules` with:

```python
rejection_reasons: str | None = None
```

Append `render_qimao_rejection_repair_rules(...)` after the existing Qimao signing block.

**Step 4: Tests**

Add:

```python
def test_qimao_rejection_repair_rules_render_when_rejection_reason_present() -> None:
    block = render_methodology_scene_rules(
        chapter_number=1,
        is_opening=True,
        platform_target="七猫小说",
        language="zh-CN",
        rejection_reasons="文笔待提升，代入感弱，开篇普通，叙述平淡。",
    )

    assert "七猫拒稿修复指令" in block
    assert "weak_immersion" in block
    assert "ordinary_entry" in block
```

**Step 5: Run tests**

```bash
uv run pytest tests/unit/test_methodology_qimao.py -q --no-cov
```

Expected: pass.

### Task 8: Pass Rejection Reasons from Project Metadata

**Files:**
- Modify: `src/bestseller/services/drafts.py`
- Modify: `src/bestseller/services/reviews.py`
- Test: `tests/unit/test_hype_draft_plumbing.py` or `tests/unit/test_review_services.py`

**Step 1: Store rejection text convention**

Use:

```python
project.metadata_json["editor_rejection_reasons"] = [
    "文笔还有待提升",
    "代入感较弱",
    "开篇的切入点比较普通",
    "缺乏足够的吸引力",
    "故事的叙述较为平淡",
]
```

**Step 2: Add helper**

In `methodology.py` or a local helper:

```python
def normalize_rejection_reasons(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "；".join(str(item).strip() for item in value if str(item).strip())
    return ""
```

**Step 3: Pass into prompt construction**

In `drafts.py`, where `render_methodology_scene_rules(...)` is called, pass:

```python
rejection_reasons=normalize_rejection_reasons(
    (project.metadata_json or {}).get("editor_rejection_reasons")
),
```

Do the same in both `reviews.py` rewrite prompt builders.

**Step 4: Test**

Assert a fake project with Qimao platform and rejection metadata produces prompt text containing:

- `七猫拒稿修复指令`
- `开篇切入点`
- `代入`
- `叙述平淡`

### Task 9: Add Deterministic Opening-Rejection Gate

**Files:**
- Create: `src/bestseller/services/opening_rejection_gate.py`
- Create: `tests/unit/test_opening_rejection_gate.py`

**Step 1: Write data models**

```python
@dataclass(frozen=True)
class OpeningRejectionFinding:
    code: str
    severity: str
    message: str
    evidence: str


@dataclass(frozen=True)
class OpeningRejectionGateReport:
    passed: bool
    findings: tuple[OpeningRejectionFinding, ...]
```

**Step 2: Implement pure checks**

Checks should be deterministic and conservative:

- opening paragraph too long and exposition-heavy
- no dialogue/action/conflict marker in first 300 Chinese characters
- too many named entities in first 1,000 Chinese characters
- too many abstract emotion words compared with action verbs
- chapter end lacks question, threat, decision, reveal, or unfinished action marker

**Step 3: Test bad fixture**

```python
def test_opening_rejection_gate_flags_flat_exposition() -> None:
    text = "青云大陆幅员辽阔，宗门林立。李玄出生在一个普通村庄..."

    report = evaluate_opening_rejection_gate(text, chapter_number=1)

    assert not report.passed
    assert any(f.code == "ordinary_entry" for f in report.findings)
    assert any(f.code == "flat_narration" for f in report.findings)
```

**Step 4: Test good fixture**

```python
def test_opening_rejection_gate_allows_action_conflict_opening() -> None:
    text = "戒尺砸下来时，李玄正把那张伪造的欠契按进烛火里..."

    report = evaluate_opening_rejection_gate(text, chapter_number=1)

    assert report.passed
```

**Step 5: Run tests**

```bash
uv run pytest tests/unit/test_opening_rejection_gate.py -q --no-cov
```

Expected: pass.

### Task 10: Expose a CLI Diagnostic Command

**Files:**
- Modify: `src/bestseller/cli/main.py`
- Test: `tests/unit/test_cli.py`

**Step 1: Add command**

Add a command under `chapter_app`:

```python
@chapter_app.command("opening-gate")
def chapter_opening_gate(project_slug: str, chapter_number: int = 1) -> None:
    """Run deterministic Qimao opening-rejection gate for one chapter."""
```

**Step 2: Load current chapter draft**

Use existing session patterns in `chapter_review` and `chapter_rewrite`.

**Step 3: Emit JSON**

Return:

```json
{
  "project_slug": "...",
  "chapter_number": 1,
  "passed": false,
  "findings": [
    {
      "code": "ordinary_entry",
      "severity": "critical",
      "message": "...",
      "evidence": "..."
    }
  ]
}
```

**Step 4: Test CLI**

Add a focused CLI test that calls the command against a fixture chapter or mocks the service.

**Step 5: Run tests**

```bash
uv run pytest tests/unit/test_opening_rejection_gate.py tests/unit/test_cli.py -q --no-cov
```

Expected: pass.

### Task 11: Create Targeted Rewrite Tasks for the First Three Chapters

**Files:**
- Modify or create helper in: `src/bestseller/services/opening_rejection_gate.py`
- Optional Modify: `src/bestseller/cli/main.py`
- Test: `tests/unit/test_opening_rejection_gate.py`

**Step 1: Create instruction builder**

```python
def build_qimao_rejection_rewrite_instruction(
    *,
    chapter_number: int,
    rejection_reasons: str,
    findings: tuple[OpeningRejectionFinding, ...],
) -> str:
    ...
```

**Step 2: Chapter-specific instruction rules**

Chapter 1:

- replace ordinary entry with conflict/abnormality
- protagonist appears in first 100 words
- conflict appears in first 200 words
- core conflict legible by first 600 words
- end with short-term goal and hook

Chapter 2:

- reveal protagonist edge through action
- show edge limitation
- escalate conflict
- end with a concrete next target

Chapter 3:

- deliver first small payoff
- show consequence through witnesses, resource, status, or threat change
- open the next loop

**Step 3: Test output**

Assert Chapter 1 instruction includes:

- `前100字`
- `前200字`
- `第一章切入点`
- `代入感`
- `章尾钩子`

### Task 12: Run the Repair Loop

**Files:**
- Modify: current DB rewrite tasks
- Output: `output/<project_slug>/chapter-001.md` through `output/<project_slug>/chapter-003.md`
- Output: `docs/reviews/<project_slug>-qimao-repair-report.md`

**Step 1: Store rejection reasons on project metadata**

Set:

```json
{
  "editor_rejection_reasons": [
    "文笔还有待提升",
    "代入感较弱",
    "开篇的切入点比较普通",
    "缺乏足够的吸引力",
    "故事的叙述较为平淡"
  ]
}
```

**Step 2: Run opening gate**

```bash
uv run bestseller chapter opening-gate <project_slug> 1
```

Expected: initial draft should fail with concrete findings.

**Step 3: Run chapter review**

```bash
uv run bestseller chapter review <project_slug> 1
```

Expected: review creates a pending rewrite task if chapter does not pass.

**Step 4: Rewrite Chapter 1**

```bash
uv run bestseller chapter rewrite <project_slug> 1
```

Expected: rewritten draft is produced and remains inside word count gate.

**Step 5: Repeat for Chapters 2 and 3**

```bash
uv run bestseller chapter review <project_slug> 2
uv run bestseller chapter rewrite <project_slug> 2
uv run bestseller chapter review <project_slug> 3
uv run bestseller chapter rewrite <project_slug> 3
```

**Step 6: Re-run gate and manual read**

```bash
uv run bestseller chapter opening-gate <project_slug> 1
uv run bestseller chapter review <project_slug> 1
uv run bestseller chapter review <project_slug> 2
uv run bestseller chapter review <project_slug> 3
```

Expected:

- no `ordinary_entry` finding
- no `weak_hook` critical finding
- chapter review verdict should be pass or produce only local rewrite issues

### Task 13: First-10k Human Editorial Pass

**Files:**
- Read: `output/<project_slug>/chapter-001.md` through `output/<project_slug>/chapter-010.md`
- Create: `docs/reviews/<project_slug>-qimao-repair-report.md`

**Step 1: Read like an editor**

Do not score based on whether the idea is personally appealing. Score only:

- does it fit Qimao mobile free-reading rhythm
- does the protagonist drive the scene
- is the conflict immediate
- does each chapter create a reason to continue
- does the prose create experience instead of explanation

**Step 2: Fill report**

```markdown
# 七猫拒稿修复报告

## Original Rejection
- 文笔还有待提升
- 代入感较弱
- 开篇的切入点比较普通
- 缺乏足够的吸引力
- 故事的叙述较为平淡

## Repair Summary
- Chapter 1:
- Chapter 2:
- Chapter 3:
- Chapter 4-10:

## Qimao Gate
- 前100字主角聚光灯：
- 前200字可感冲突：
- 前600字核心矛盾：
- 前2000字情绪钩子：
- 前6000字主线方向：
- 前10000字循环证明：

## Remaining Risks
- ...

## Submission Verdict
ready / needs one more rewrite
```

**Step 3: Exit criteria**

The project is ready to resubmit only if:

- Chapter 1 no longer starts with setup, background, or normal daily flow.
- Chapter 1 gives a concrete reader question in the first page.
- Chapters 1-3 each contain conflict, protagonist action, and a visible result.
- First 10,000 words have no long explanation-only section.
- Prose is specific enough that scenes can be visualized without extra context.

## Execution Order

Recommended order:

1. Task 1: First-10k failure map
2. Task 2: Chapter 1 entry redesign
3. Task 3: Golden three repair card
4. Task 4: First-10k conflict-loop repair
5. Task 5: Immersion and prose repair
6. Task 6-8: Framework prompt integration
7. Task 9-11: Deterministic gate and CLI support
8. Task 12: Run repair loop
9. Task 13: Human editorial pass

Do not start with prose polishing. The current rejection says the opening experience is not compelling enough; prose polish is the last pass after the incident, POV, conflict, and payoff loop are rebuilt.

## Verification Commands

Focused tests:

```bash
uv run pytest tests/unit/test_methodology_qimao.py -q --no-cov
uv run pytest tests/unit/test_opening_rejection_gate.py -q --no-cov
uv run pytest tests/unit/test_cli.py -q --no-cov
```

Syntax check:

```bash
uv run python -m py_compile src/bestseller/services/methodology.py src/bestseller/services/drafts.py src/bestseller/services/reviews.py src/bestseller/services/opening_rejection_gate.py src/bestseller/cli/main.py
```

Behavioral manuscript checks:

```bash
uv run bestseller chapter opening-gate <project_slug> 1
uv run bestseller chapter review <project_slug> 1
uv run bestseller chapter review <project_slug> 2
uv run bestseller chapter review <project_slug> 3
```

Known repository note:

The default pytest configuration enforces global coverage. For focused feature validation during this repair, use `--no-cov`; run the broader suite separately when the existing coverage baseline is addressed.
