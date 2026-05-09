# Planning Kernel And Prewrite Gate Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Make the novel platform's early planning layer authoritative enough to prevent samey books before drafting begins.

**Architecture:** Add a deterministic `ProjectPlanningKernel` service that normalizes conception, story facets, benchmark/profile, BookSpec, WorldSpec, CastSpec, and VolumePlan into one DB-persisted planning contract. Add a `PrewriteReadinessGate` that validates the kernel before chapter writing and records a report as project metadata plus a planning artifact. Integrate it into both initial project creation and novel-plan generation paths without adding a database migration.

**Tech Stack:** Python 3.11, SQLAlchemy async models, existing `ProjectModel.metadata_json`, `PlanningArtifactVersionModel`, pytest unit tests.

---

### Task 1: Add Planning Kernel Service

**Files:**
- Create: `src/bestseller/services/planning_kernel.py`
- Test: `tests/unit/test_planning_kernel.py`

**Step 1: Write failing tests**

Cover:
- Rich planning inputs produce a passed readiness report.
- Missing benchmark/unique hook/series engine produces blocking findings.
- A same-force volume plan produces a critical volume differentiation finding.
- Existing `ranking-capability-profile.md` text can be stored in the kernel metadata payload.

**Step 2: Implement minimal service**

Implement:
- `build_project_planning_kernel(...) -> dict[str, object]`
- `evaluate_prewrite_readiness(kernel, genre=None, sub_genre=None, target_chapters=None) -> PrewriteReadinessReport`
- `prewrite_readiness_report_to_dict(report)`

Checks:
- `benchmark_alignment`: must have benchmark works, comparables, or ranking profile text.
- `unique_hook`: must have StoryFacets, creative hook, or commercial brief unique hook.
- `series_engine`: must include reader promise and payoff/hook rhythm from BookSpec/ranking profile.
- `long_arc_capacity`: long books need volume plan capacity or at least 10 escalation anchors.
- `volume_differentiation`: repeated conflict phase or primary force becomes warning/critical.
- `genre_engine`: reuse `evaluate_premium_project_readiness` as a structural signal when enough metadata exists, but do not require post-write state ledgers at creation time.

**Step 3: Verify**

Run:

```bash
./.venv/bin/pytest tests/unit/test_planning_kernel.py -q --no-cov
```

Expected: all tests pass.

### Task 2: Persist Kernel During Project Creation

**Files:**
- Modify: `src/bestseller/services/projects.py`
- Test: `tests/unit/test_project_services.py`

**Step 1: Write failing test**

Create a project with `metadata={"premise": ..., "story_facets": ..., "commercial_brief": ...}` and assert:
- `project.metadata_json["planning_kernel"]` exists.
- `project.metadata_json["prewrite_readiness_report"]` exists.
- Initial report may fail because BookSpec/VolumePlan do not exist yet, but findings are actionable.

**Step 2: Implement**

After style guide and genre pack initialization, call the planning kernel builder with available metadata only and persist the report in `project.metadata_json`.

**Step 3: Verify**

Run:

```bash
./.venv/bin/pytest tests/unit/test_project_services.py -q --no-cov
```

### Task 3: Persist Full Kernel After Novel Plan Generation

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Test: `tests/unit/test_planning_kernel.py` or `tests/unit/test_pipeline_services.py`

**Step 1: Write failing test**

Use deterministic BookSpec/WorldSpec/CastSpec/VolumePlan payloads and assert the full kernel report passes after plan generation helper execution.

**Step 2: Implement**

After `volume_plan_payload` and plan validation, build the full planning kernel using:
- project metadata
- BookSpec
- WorldSpec
- CastSpec
- VolumePlan
- output-side ranking profile when available

Persist:
- `project.metadata_json["planning_kernel"]`
- `project.metadata_json["prewrite_readiness_report"]`

Also import a `PLAN_VALIDATION` artifact for the prewrite report so live project inspection can find it.

**Step 3: Verify**

Run:

```bash
./.venv/bin/pytest tests/unit/test_planning_kernel.py tests/unit/test_pipeline_services.py -q --no-cov
```

### Task 4: Progressive Plan Judge Hook

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Test: `tests/unit/test_pipeline_services.py`

**Step 1: Identify progressive path**

Ensure `generate_foundation_plan` and `generate_volume_plan` also call the same planning-kernel evaluator after foundation and per-volume outline generation.

**Step 2: Implement narrowly**

Do not block active production yet. Persist telemetry first:
- `prewrite_readiness_report`
- `planning_kernel`
- `plan_validation` artifact for foundation/full plan

**Step 3: Verify**

Run:

```bash
./.venv/bin/pytest tests/unit/test_pipeline_services.py -q --no-cov
```

### Task 5: Project-State Validation

**Files:**
- No source file required unless a defect is found.

**Step 1: Run tests**

```bash
./.venv/bin/python -m compileall -q src/bestseller/services/planning_kernel.py src/bestseller/services/projects.py src/bestseller/services/planner.py
./.venv/bin/pytest tests/unit/test_planning_kernel.py tests/unit/test_project_services.py tests/unit/test_pipeline_services.py -q --no-cov
```

**Step 2: Inspect current DB**

Check which live projects now have:
- `story_facets`
- `planning_kernel`
- `prewrite_readiness_report`
- `premium_book_gate_report`
- `plan_validation` artifacts

**Step 3: Report**

Summarize:
- implemented files
- verification evidence
- live DB gaps
- whether containers need rebuild/restart before runtime effect

