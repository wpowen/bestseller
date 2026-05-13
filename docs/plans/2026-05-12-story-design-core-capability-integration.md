# Story Design Core Capability Integration Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Make story design the core capability of BestSeller: every project should produce a book-specific, genre-compatible, state-aware plot system that can make the book readable, attractive, and structurally durable before prose generation begins.

**Architecture:** Add a Story Design Core above the existing planner/story-bible/narrative-graph pipeline. It becomes the source of truth for premise, character conflict, world rules, macro structure, plot tree, beat schedule, and state-driven chapter planning. Existing components such as `planning_kernel.py`, `narrative_lines.py`, `plot_arcs`, `arc_beats`, `clues`, `payoffs`, `chapter_contracts`, and whole-book quality gates become consumers and validators of that core.

**Tech Stack:** Python, Pydantic, SQLAlchemy/Postgres JSONB, PlanningArtifactVersion, YAML grammar configs, existing workflow/audit tables, pytest, fixture-based regression tests.

---

## Executive Position

The story design layer is not a convenience feature. It is the system's soul layer.

The current BestSeller system already has a working production chain:

```text
project -> premise -> book_spec/world_spec/cast_spec/volume_plan
-> story bible -> outline -> scene/chapter draft -> review/rewrite
-> canon/timeline/retrieval -> export
```

That chain can produce books, but the planning intelligence is still too close to "structured generation". The repeated parent/mother/family-loss/old-case issue is only one visible symptom. The deeper problem is:

- the system evaluates whether a plan is complete, but does not yet design a unique story engine for each book;
- genre/category configs provide templates and standards, but not a dynamic plot grammar;
- chapter planning can vary wording, but not reliably vary the underlying dramatic function;
- later gates catch flatness or repetition after planning has already gone wrong;
- editor/user product surfaces do not yet expose "why this book should be good" as a first-class artifact.

The right fix is not "add more anti-repetition rules". The right fix is to make every book pass through a structured story design process before world/cast/volume/chapter planning becomes final.

---

## Research Principles Imported

Primary research source: `/Users/owen/Downloads/deep-research-report (1).md`.

The report gives a clear design foundation:

- Lines 5-8: story planning has four dependent layers: one-sentence premise, character/world system, macro structure, chapter/scene execution.
- Lines 6 and 72-78: no single structure model is enough. Use macro structure, Snowflake expansion, plot tree, beat table, and reverse outline in different stages.
- Lines 7 and 12: a valid story must answer "who wants what, why they cannot get it, and what they lose if they fail".
- Lines 21-30: short story, long novel, series, web serial, commercial publication, and literary work need different outline depth.
- Lines 84-90: characters are desire, need, obstacle, false belief, and endpoint, not resume cards.
- Lines 106-115: antagonists should be value competitors; they need a coherent worldview that pressures the protagonist.
- Lines 126-153: worldbuilding must prioritize rules, power, resources, and risk mechanisms before lore detail.
- Lines 157-209: scene execution needs goal, conflict, result, reaction, dilemma, decision, information movement, and hook/aftereffect.
- Lines 223-237: planning artifacts need versioning and freeze points.
- Lines 353-357: every setting must create conflict, every chapter must change something, every subplot must justify its connection to the main line.

Translated into system design, this means BestSeller needs:

1. **Premise discipline**: every book starts from a falsifiable promise and failure cost.
2. **Character-value engine**: protagonist and antagonist must collide on values, not just plot roles.
3. **World-as-conflict**: setting rules must generate choices, costs, and constraints.
4. **Structure plurality**: different book shapes choose different structure models.
5. **Beat accountability**: each chapter/scene states what changed.
6. **Reverse verification**: after outline generation, the system proves the design still works.

---

## Current System Capability Map

### Existing Strengths

The current system already has a strong technical base:

- `ProjectModel` stores project metadata, theme, dramatic question, reader contract, and hype scheme in `src/bestseller/infra/db/models.py`.
- `PlanningArtifactVersionModel` gives versioned planning artifacts.
- `BookSpec`, `WorldSpec`, `CastSpec`, `VolumePlan`, and `ChapterOutlineBatch` are already generated and materialized.
- `PlotArcModel` / `ArcBeatModel` provide explicit narrative lines.
- `ClueModel` / `PayoffModel` provide setup-payoff tracking.
- `ChapterContractModel` / `SceneContractModel` provide draft-time execution contracts.
- `EmotionTrackModel` and `AntagonistPlanModel` cover relationship and opposition movement.
- `narrative_lines.py` validates overt/undercurrent/hidden/core-axis structure.
- `planning_kernel.py` checks readiness signals such as unique hook, long-arc capacity, and volume differentiation.
- `whole_book_quality` already thinks in terms of chapter function, forward pull, chapter turn, rolling freshness, arc closure, and volume momentum.
- `config/novel_categories/*.yaml`, `genre_review_profiles.py`, and prompt packs already contain topic-specific standards.

### Existing Gaps

The missing capability is not storage or pipeline execution. It is story design authority.

Current gaps:

- No first-class artifact says: "this is the book's unique story engine".
- `planning_kernel.py` is evaluative, not generative.
- Category YAML files contain challenge templates, but not a state machine of valid plot changes.
- `planner.py` fallback paths still create generic core wounds, generic stakes, generic conflict forces, and generic chapter goals.
- Prewrite directives arrive too late; they repair downstream planning instead of shaping upstream design.
- Whole-book gates run after chapters exist; by then, repair is expensive.
- Product/UI cannot yet show an editor "why this book works" in one cockpit.

The current pipeline is like a strong factory with an underpowered design office.

---

## Target Capability Model

BestSeller needs a new Story Design Core with six layers:

```text
L0 Project Shape
   Determines whether this is short, long, series, web serial, commercial, literary, IP-oriented.

L1 Story Design Kernel
   Defines the book's premise, failure cost, character value collision, world conflict rules,
   macro structure, plot tree, and beat schedule.

L2 Genre Story Grammar
   Defines valid plot changes for each category: progression, mystery, relationship,
   base-building, strategy, eastern aesthetic, esports, etc.

L3 Story State Snapshot
   Reads current project state: open promises, due payoffs, recent beat types,
   character/relationship/world changes, unresolved debts.

L4 Plan Compiler
   Compiles kernel + grammar + state into volume plans, chapter beat schedules,
   chapter outlines, and scene cards.

L5 Reverse Outline And Quality Gates
   Verifies that every chapter changes something, every subplot belongs,
   every world rule creates conflict, and every promised payoff has a path.
```

The product principle:

```text
Do not ask "how do we avoid similarity?" first.
Ask "given this book's promise, genre, state, and reader contract, what must happen next?"
```

---

## Integrated Architecture

### New Pipeline Shape

Current:

```text
premise -> book_spec -> world_spec -> cast_spec -> volume_plan
-> chapter_outline -> materialize -> draft/review/rewrite
```

Target:

```text
premise
-> story_shape_router
-> book_spec
-> story_design_kernel_v0
-> world_spec shaped by kernel
-> cast_spec shaped by kernel
-> story_design_kernel_v1 freeze
-> volume_plan from kernel.structure_strategy + plot_tree
-> reverse_outline_gate(volume)
-> story_state_snapshot
-> chapter_beat_schedule
-> chapter_outline from beat schedule
-> reverse_outline_gate(chapter batch)
-> materialize chapter/scene contracts
-> draft/review/rewrite
-> whole_book_quality updates state
```

### Placement In Current Codebase

New services:

- `src/bestseller/services/story_shape_router.py`
- `src/bestseller/services/story_design_kernel.py`
- `src/bestseller/services/story_design_grammars.py`
- `src/bestseller/services/story_state_snapshot.py`
- `src/bestseller/services/reverse_outline_gate.py`

New configs:

- `config/story_design_grammars/default.yaml`
- `config/story_design_grammars/action-progression.yaml`
- `config/story_design_grammars/suspense-mystery.yaml`
- `config/story_design_grammars/relationship-driven.yaml`
- `config/story_design_grammars/female-growth-ncp.yaml`
- `config/story_design_grammars/base-building.yaml`
- `config/story_design_grammars/strategy-worldbuilding.yaml`
- `config/story_design_grammars/eastern-aesthetic.yaml`
- `config/story_design_grammars/esports-competition.yaml`

Existing services to modify:

- `src/bestseller/services/planner.py`
- `src/bestseller/services/planning_kernel.py`
- `src/bestseller/services/workflows.py`
- `src/bestseller/services/context.py`
- `src/bestseller/services/narrative_tree.py`
- `src/bestseller/services/genre_review_profiles.py`
- `src/bestseller/services/projects.py`

Artifact integration:

- Add `ArtifactType.STORY_DESIGN_KERNEL`.
- Store latest kernel in `ProjectModel.metadata_json["story_design_kernel"]`.
- Version all kernel changes through `PlanningArtifactVersionModel`.
- Expose `/story-design/kernel`, `/story-design/plot-tree`, `/story-design/beat-schedule`, and `/story-design/state` in Narrative Tree.

No new DB tables are required in phase 1. Use JSONB artifacts first. Promote to dedicated tables only after the kernel stabilizes.

---

## Core Domain Contracts

### StoryDesignKernel

The kernel is the book's design source of truth.

Required sections:

- `story_shape`: length class, publication mode, outline depth, chapter duties.
- `premise_contract`: one sentence, theme question, dramatic question, reader promise, unique claim, failure cost.
- `character_conflict`: visible goal, inner need, false belief, decision policy, antagonist value claim, value collision.
- `world_conflict`: rules, power/resource flow, risk mechanism, why easy solution fails, rule exploitation paths.
- `structure_strategy`: selected macro models, why they fit, act/volume beats, midpoint function, climax choice, resolution image.
- `plot_tree`: main line and sublines with dependency on mainline.
- `beat_schedule`: planned beat functions and change vectors.
- `reverse_outline_status`: draft, checked, frozen.

The kernel should not be a prose summary. It should be a decision system.

### Story Grammar

Each grammar answers:

- What state variables matter in this genre?
- What counts as a meaningful chapter change?
- What reader rewards are valid?
- What chapter endings fit this project shape?
- What defaults are forbidden?
- What minimum design contracts are required?

Examples:

- Suspense: clue added, clue reframed, suspect pressure changed, alibi broken, false solution fails.
- Progression: resource gained/lost, ability boundary discovered, cost paid, enemy adapts, public status changes.
- Relationship: trust shifts, boundary tested, debt incurred, vulnerability exchanged, public/private mask cracks.
- Base-building: bottleneck solved, visible build step completed, governance tradeoff introduced, resource loop changes.
- Strategy: move, countermove, leverage shift, alliance reversal, legitimacy cost.

### StoryStateSnapshot

The snapshot makes planning compatible with the current book state.

It should include:

- current volume/chapter
- active promises
- due payoffs
- unresolved clue/resource/relationship debts
- active plot tree nodes
- protagonist state
- antagonist current move
- relationship state
- world rule state
- recent change vectors
- recent reader rewards
- blocked repetitions

This is the mechanism that prevents "book type changed, but planner keeps using old logic".

### Reverse Outline Gate

This gate asks editor-grade questions before materialization:

- Does every chapter change something?
- Does each chapter follow from a causal prior event?
- Does every subplot justify why it belongs?
- Does every world rule used by the plot create cost or constraint?
- Does each scene have goal/conflict/result or reaction/dilemma/decision?
- Does the outline match the selected project shape?
- Does a web serial chapter have forward pull?
- Does a literary chapter have perceptual/theme movement instead of forced cliffhanger?

---

## Product Design

### Product Surface: Story Design Cockpit

Add a Story Design section in Web Studio:

1. **One-Minute Promise**
   - one-sentence premise
   - reader promise
   - failure cost
   - why this book is different

2. **Character Engine**
   - protagonist visible goal
   - inner need
   - false belief
   - antagonist value claim
   - value collision
   - irreversible choices

3. **World Conflict Engine**
   - P0 rules
   - power/resource flow
   - risk mechanism
   - why easy solution fails
   - rule exploitation paths

4. **Structure And Plot Tree**
   - selected macro model
   - volume/act beats
   - mainline
   - sublines
   - plant/payoff windows

5. **Beat Schedule**
   - chapter number
   - beat function
   - change vector
   - reader reward
   - hook/aftereffect

6. **Story State**
   - open promises
   - due payoffs
   - recent repetition risk
   - next recommended beat type

7. **Editor Findings**
   - reverse outline issues
   - readiness failures
   - repair choices

### Product Workflows

#### New Project Flow

```text
Create project -> generate 3 story design candidates
-> compare candidates -> select/edit kernel
-> freeze kernel v1 -> generate story bible and volume plan
```

Candidate comparison should show:

- promise strength
- character conflict strength
- world-conflict uniqueness
- long-arc capacity
- complexity risk
- web-serial retention risk

#### Existing Project Repair Flow

```text
Select project -> derive kernel from current artifacts
-> reverse outline audit -> repair future plans only by default
-> optionally regenerate selected volume/chapter outlines
```

Default behavior must not rewrite existing prose.

#### Editor Review Flow

An editor should be able to answer in 10 minutes:

- What is the book promising?
- Why is this protagonist compelling?
- Why is the antagonist not generic?
- Why can this world generate 100+ chapters?
- What are the first 5 payoffs?
- Where does the first major turn happen?
- Which chapters currently do not change anything?

---

## Editor And User Perspective

### For Authors

The system should feel less like "fill a form" and more like "a story room".

Author-facing questions:

- Is this the story you meant to write?
- Which version of the protagonist is more compelling?
- Which conflict engine has more chapters in it?
- Are you writing for pursuit, payoff, emotion, intellectual puzzle, or atmosphere?
- Which promises must never be broken?

### For Editors

Editors need inspectability and intervention points.

Editor-facing controls:

- approve/reject StoryDesignKernel
- edit value collision
- change structure model
- mark subplot as disconnected
- demand stronger failure cost
- freeze/unfreeze outline version
- send kernel back to planner with explicit notes

### For Product Users

Users need confidence that generation is not random.

Visible product signals:

- Story Design Score
- Premise Clarity
- Character Conflict Strength
- World Conflict Utility
- Causal Chain Coverage
- Chapter Change Coverage
- Payoff Coverage
- State Responsiveness

These metrics should be explainable, not opaque.

---

## Testing Strategy

### Unit Tests

Create focused tests for:

- story shape routing
- grammar loading and validation
- kernel model validation
- fallback kernel generation
- reverse outline findings
- story state snapshot extraction
- planning kernel readiness codes
- prompt block rendering

Required test files:

- `tests/unit/test_story_shape_router.py`
- `tests/unit/test_story_design_kernel.py`
- `tests/unit/test_story_design_grammars.py`
- `tests/unit/test_story_state_snapshot.py`
- `tests/unit/test_reverse_outline_gate.py`
- `tests/unit/test_planner_story_design_kernel.py`
- `tests/unit/test_planning_kernel_story_design.py`

### Fixture Tests

Add fixture projects for:

- xianxia progression
- suspense mystery
- relationship-driven romance
- female growth no-CP
- base-building
- strategy worldbuilding
- eastern aesthetic
- esports competition

Each fixture must assert:

- valid `StoryDesignKernel`
- genre-specific grammar selected
- non-empty character value collision
- non-empty world conflict mechanism
- at least 5 unique chapter change vectors for long projects
- no generic family-loss/old-case default unless input explicitly asks for it

### Integration Tests

Add end-to-end planning tests with feature flags:

```bash
pytest tests/integration/test_story_design_planning_flow.py -q
```

Assertions:

- planning creates `story_design_kernel` artifact before volume plan.
- world/cast/volume prompts include kernel prompt block.
- chapter outline metadata includes change vectors.
- reverse outline report is stored in workflow metadata.
- old projects can run in warning-only mode.

### Quality Regression Tests

Use a small matrix:

```text
4 genres x 2 lengths x 2 publication modes
```

Track:

- repeated wound/stakes rate
- chapter change vector diversity
- causal link coverage
- subplot dependency coverage
- due payoff coverage
- reverse outline pass rate

Cross-project sameness remains useful here, but only as an outcome metric.

### LLM Behavior Tests

Use golden prompt snapshots:

- BookSpec prompt includes story design requirement.
- WorldSpec prompt includes world-as-conflict requirement.
- CastSpec prompt includes value-collision requirement.
- VolumePlan prompt includes plot-tree dependency requirement.
- Outline prompt includes state snapshot and change-vector requirements.

Test the prompt text without calling a model.

---

## Architecture Governance

### Non-Negotiable Design Rules

1. StoryDesignKernel is upstream of WorldSpec, CastSpec, VolumePlan, and ChapterOutline.
2. Every genre grammar defines state variables and change vectors.
3. Every chapter outline must state what changed.
4. Every subplot must depend on the mainline.
5. Every world rule used in plot must impose cost, constraint, or leverage.
6. Every antagonist must carry a value claim.
7. Reverse outline gate runs before materialization.
8. Existing projects default to warning-only and future-plan repair.

### Feature Flags

Add settings:

```yaml
pipeline:
  enable_story_design_kernel: true
  story_design_kernel_candidate_count: 3
  enable_story_state_driven_planning: true
  enable_reverse_outline_gate: true
  reverse_outline_gate_block_on_failure: false
  story_design_require_kernel_for_new_projects: false
```

Rollout:

- phase 1: generate and store kernel, warning-only.
- phase 2: require kernel for new projects.
- phase 3: block materialization when reverse outline has critical issues.

---

## Implementation Plan

### Phase 0: Align Existing Docs And Flags

**Files:**
- Modify: `docs/current-status-and-roadmap.md`
- Modify: `docs/architecture.md`
- Modify: `src/bestseller/settings.py`

**Tasks:**

1. Add Story Design Core as the next top-level architecture pillar.
2. Add feature flags in settings.
3. Document that cross-project sameness audit is an outcome check, not core planning logic.

### Phase 1: Models, Router, Grammar

**Files:**
- Create: `src/bestseller/services/story_shape_router.py`
- Create: `src/bestseller/services/story_design_kernel.py`
- Create: `src/bestseller/services/story_design_grammars.py`
- Create: `config/story_design_grammars/*.yaml`
- Create tests listed above.

**Done when:**

- project shape can be derived deterministically.
- grammars load for every current category.
- kernel validation rejects missing premise/character/world/structure/plot-tree sections.

### Phase 2: Planner Generation And Persistence

**Files:**
- Modify: `src/bestseller/domain/enums.py`
- Modify: `src/bestseller/services/planner.py`
- Modify: `src/bestseller/services/projects.py`

**Tasks:**

1. Add `ArtifactType.STORY_DESIGN_KERNEL`.
2. Generate kernel after BookSpec.
3. Finalize kernel after WorldSpec and CastSpec.
4. Persist to metadata and artifact versions.
5. Include kernel in LLM run metadata for traceability.

**Done when:**

- every new planning run stores a kernel artifact.
- fallback path produces a non-generic kernel.
- old projects still plan if flag is off.

### Phase 3: Prompt Integration

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Modify: `src/bestseller/services/context.py`

**Tasks:**

1. Add `render_story_design_kernel_prompt_block`.
2. Inject kernel into WorldSpec prompts.
3. Inject kernel into CastSpec prompts.
4. Inject kernel into VolumePlan prompts.
5. Inject kernel into ChapterOutline prompts.

**Done when:**

- prompts are shaped by story design before plan materialization.
- tests prove genre-specific grammar appears in outline prompts.

### Phase 4: State-Driven Planning

**Files:**
- Create: `src/bestseller/services/story_state_snapshot.py`
- Modify: `src/bestseller/services/planner.py`
- Modify: `src/bestseller/services/pipelines.py`

**Tasks:**

1. Build state snapshot from existing models.
2. Append snapshot to volume/chapter outline prompts.
3. Track recent change vectors and reader rewards.
4. Prevent repeated beat class in local windows unless explicitly justified.

**Done when:**

- planning future chapters reads current project state.
- a chapter after a payoff does not blindly schedule the same reveal/payoff again.

### Phase 5: Reverse Outline Gate

**Files:**
- Create: `src/bestseller/services/reverse_outline_gate.py`
- Modify: `src/bestseller/services/workflows.py`
- Modify: `src/bestseller/services/planning_kernel.py`

**Tasks:**

1. Validate volume plan.
2. Validate chapter outline batch.
3. Store report in workflow metadata.
4. Feed findings into repair loops.
5. Add warning-only mode for old projects.

**Done when:**

- chapters without change vectors are flagged.
- disconnected subplots are flagged.
- world rules without consequences are flagged.
- repair prompt is specific, not generic polish.

### Phase 6: Product Surface

**Files:**
- Web Studio files to be located before implementation.
- API schema files under `src/bestseller/api/schemas/`.
- Project service endpoints under current API/service layout.

**Tasks:**

1. Add endpoint to fetch StoryDesignKernel.
2. Add endpoint to update/edit kernel sections.
3. Add Story Design Cockpit.
4. Add reverse outline report panel.
5. Add candidate comparison panel for new project planning.

**Done when:**

- editor can inspect and edit story design before drafting.
- user can see why a generated plan is attractive or weak.

### Phase 7: Existing Project Migration

**Files:**
- Create: `scripts/audit_story_design_kernel.py`
- Create: `scripts/repair_story_design_kernel.py`

**Tasks:**

1. Derive kernel from existing artifacts.
2. Run reverse outline report.
3. Generate future-only repair plan.
4. Do not rewrite existing prose by default.

**Done when:**

- current projects can get a kernel without losing existing work.
- repair can target future chapters/volumes first.

---

## Acceptance Criteria

### Architecture Acceptance

- A planning run cannot reach VolumePlan without a StoryDesignKernel when the feature flag is enabled.
- StoryDesignKernel is versioned and recoverable.
- Narrative Tree exposes story design nodes.
- Context assembly can retrieve story design constraints.

### Product Acceptance

- An editor can understand the book's premise, value collision, world conflict, and structure in one screen.
- A user can compare at least two story design candidates before generating a full plan.
- Reverse outline findings are actionable and scoped.

### Quality Acceptance

- Every chapter outline has a change vector.
- Every chapter outline has a causal link.
- Every subplot has a mainline dependency.
- Every planned world rule use has a cost or consequence.
- Suspense projects show clue-state movement.
- Progression projects show resource/ability/status movement.
- Relationship projects show trust/debt/boundary movement.
- Base-building projects show visible system/building movement.

### Regression Acceptance

- Existing tests continue to pass.
- Existing project autowrite still works with flags off.
- New story-design-enabled fixtures pass.
- Repetition audit rate drops as a byproduct, not by hardcoding "avoid family loss".

---

## Strategic Outcome

After this work, BestSeller's planning layer should shift from:

```text
generate plausible novel-shaped artifacts
```

to:

```text
design a book-specific dramatic machine, then compile it into artifacts
```

That is the level needed for "千人千面". Different books should differ because their premise, protagonist, antagonist, world rules, genre grammar, state variables, and reader rewards are different, not because a post-processing audit forced them to avoid the same words.

