# Whole Book Quality Gates Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Build a whole-book quality gate so every generated novel is checked beyond the first 1-3 chapters for ongoing readability, payoff, freshness, and serial momentum.

**Architecture:** Add a deterministic whole-book engagement gate that evaluates each chapter, rolling windows, arc-like chapter groups, and volume ranges. Persist a quality ledger in project metadata and block continuation by creating targeted rewrite tasks when critical/high issues appear. Keep the gate language- and category-neutral: platform and genre can influence prompts, but the gate applies to every generated novel unless explicitly disabled.

**Tech Stack:** Python 3, dataclasses, SQLAlchemy models already used by pipelines, pytest unit tests, existing RewriteTaskModel.

---

## What This Plan Solves

The existing work prevents weak openings, but a book can still fail if chapters 4+ become flat, repetitive, payoff-starved, or disconnected from the core promise.

This plan adds checks for:

- every chapter having a clear narrative function rather than a forced identical loop
- chapters 1-50 carrying a stricter signing/paid-reading retention standard
- chapters 51-100 carrying an elevated extended-entry retention standard
- rolling 5-10 chapter windows not losing payoff, hook, or freshness
- arc-like groups completing a conflict/payoff mini-loop
- volume ranges carrying a distinct escalation and end-hook
- project metadata retaining a whole-book engagement ledger

## Task 1: Add Whole-Book Quality Gate Service

**Files:**
- Create: `src/bestseller/services/whole_book_quality_gate.py`
- Test: `tests/unit/test_whole_book_quality_gate.py`

**Step 1: Write failing tests**

Add tests for:

- good multi-chapter sample passes
- a chapter with no recognizable function fails `chapter_function_missing`
- a rolling window with no payoff fails `rolling_payoff_gap`
- repeated openings fail `rolling_repetition`
- a volume ending without hook/payoff fails `volume_momentum_drop`

**Step 2: Implement service**

Create dataclasses:

```python
@dataclass(frozen=True)
class WholeBookQualityFinding:
    code: str
    severity: str
    scope: str
    message: str
    evidence: str
    chapter_number: int | None = None
    volume_number: int | None = None

@dataclass(frozen=True)
class ChapterEngagementRecord:
    chapter_number: int
    has_conflict: bool
    has_action: bool
    has_payoff: bool
    has_hook: bool
    loop_score: int

@dataclass(frozen=True)
class WholeBookQualityReport:
    passed: bool
    findings: tuple[WholeBookQualityFinding, ...]
    ledger: tuple[ChapterEngagementRecord, ...]
    metrics: dict[str, Any]
```

Add:

```python
def evaluate_whole_book_quality(
    chapter_texts: Mapping[int, str] | Sequence[str],
    *,
    volume_plan: Any = None,
    rolling_window: int = 10,
) -> WholeBookQualityReport:
    ...
```

**Step 3: Serialization**

Add:

```python
def whole_book_quality_report_to_dict(report: WholeBookQualityReport) -> dict[str, Any]:
    ...
```

**Step 4: Verify**

Run:

```bash
uv run pytest tests/unit/test_whole_book_quality_gate.py -q --no-cov
```

## Task 2: Build Targeted Rewrite Instructions

**Files:**
- Modify: `src/bestseller/services/whole_book_quality_gate.py`
- Test: `tests/unit/test_whole_book_quality_gate.py`

**Step 1: Strategy mapping**

Map failures:

- `chapter_function_missing` -> `chapter_function_rewrite`
- `chapter_loop_missing` -> `chapter_serial_loop_rewrite` for legacy reports
- `chapter_hook_missing` -> `chapter_hook_rebuild`
- `chapter_payoff_missing` -> `chapter_payoff_rebuild`
- `rolling_payoff_gap` -> `rolling_payoff_reseed`
- `rolling_repetition` -> `rolling_freshness_rewrite`
- `arc_payoff_missing` -> `arc_closure_rewrite`
- `volume_momentum_drop` -> `volume_momentum_rebuild`

**Step 2: Instruction builder**

Add:

```python
def whole_book_quality_strategy_for_findings(findings) -> str:
    ...

def build_whole_book_quality_rewrite_instructions(
    findings,
    *,
    chapter_number: int,
    opening_quality_contract: dict[str, Any] | None = None,
) -> str:
    ...
```

The instruction must say this is not polish; it must identify the chapter function first, then rebuild the appropriate kind of momentum, payoff/turn, hook/decision, and freshness.

**Step 3: Verify**

Run:

```bash
uv run pytest tests/unit/test_whole_book_quality_gate.py -q --no-cov
```

## Task 3: Wire Whole-Book Gate Into Project Pipeline

**Files:**
- Modify: `src/bestseller/services/pipelines.py`
- Test: `tests/unit/test_pipeline_services.py`

**Step 1: Import gate**

Import:

```python
from bestseller.services.whole_book_quality_gate import (
    build_whole_book_quality_rewrite_instructions,
    evaluate_whole_book_quality,
    whole_book_quality_report_to_dict,
    whole_book_quality_strategy_for_findings,
)
```

**Step 2: Evaluate after each completed chapter**

In `run_project_pipeline`, after a chapter finishes and after opening gate, load its current draft and append to an in-memory `whole_book_quality_texts`.

Evaluate:

```python
report = evaluate_whole_book_quality(whole_book_quality_texts, volume_plan=project.metadata_json.get("volume_plan"))
```

Persist:

- `project.metadata_json["whole_book_quality_report"]`
- `project.metadata_json["whole_book_engagement_ledger"]`

**Step 3: Create rewrite task on high/critical failure**

If report fails, create `RewriteTaskModel`:

- `trigger_type="whole_book_quality_gate"`
- `rewrite_strategy` from mapping
- `priority=2`
- instructions from builder
- metadata includes report and ledger

Raise:

```text
Whole-book quality gate failed: ...
```

**Step 4: Test**

Add a pipeline test where chapters 1-3 pass but chapter 4 is flat. Assert:

- a `RewriteTaskModel` is created
- `trigger_type == "whole_book_quality_gate"`
- project metadata has `whole_book_quality_gate_blocked`

## Task 4: Add Verification Matrix Doc

**Files:**
- Create: `docs/whole-book-quality-verification-matrix.md`

Include rows:

- Opening contract
- Per-chapter narrative function
- Rolling payoff freshness
- Arc closure
- Volume momentum
- Rewrite task mapping
- Pipeline blocking

## Final Verification

Run:

```bash
uv run pytest \
  tests/unit/test_whole_book_quality_gate.py \
  tests/unit/test_pipeline_services.py::test_run_project_pipeline_creates_whole_book_quality_rewrite_task \
  -q --no-cov

uv run python -m py_compile \
  src/bestseller/services/whole_book_quality_gate.py \
  src/bestseller/services/pipelines.py
```

## Exit Criteria

The work is complete when:

- every generated novel can produce a whole-book engagement ledger
- a flat chapter after the opening is blocked
- rolling windows can detect payoff/freshness loss
- rewrite tasks are targeted, not generic polish
- tests prove the gate applies beyond chapters 1-3
