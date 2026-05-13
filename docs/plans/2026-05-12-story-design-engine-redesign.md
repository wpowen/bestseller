# Story Design Engine Redesign Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Rebuild the planning layer so every book gets a project-specific story design engine driven by genre, length, reader promise, protagonist state, world rules, and current narrative state, instead of relying on generic fallback plot templates and post-hoc sameness audits.

**Architecture:** Introduce a `StoryDesignKernel` as the authoritative bridge between premise, characters, world rules, macro structure, volume plan, chapter beats, and scene cards. Cross-project sameness checks remain as diagnostics, but the main fix is upstream: the planner must generate a book-specific plot grammar and use that grammar to choose every later volume/chapter beat.

**Tech Stack:** Python, Pydantic, SQLAlchemy/Postgres JSONB, existing planning artifact versioning, YAML story grammar configs, pytest.

---

## Source Research

Primary source: `/Users/owen/Downloads/deep-research-report (1).md`.

The report's core claims that directly shape this redesign:

- Lines 5-8: story planning must have four linked layers: one-sentence premise, character/world system, macro structure, chapter/scene execution.
- Lines 6 and 72-78: do not depend on one structure model. Use macro structure for direction, Snowflake-style expansion for detail, plot tree for branches, beat tables for pacing, and reverse outline for revision.
- Lines 7 and 12: the minimum story loop is: who wants what, why they cannot get it, and what they lose if they fail.
- Lines 21-30 and 159-167: different project shapes need different outline depth and chapter duties. Short stories, long books, series, web serials, commercial publication, and literary work cannot share one planning contract.
- Lines 84-90 and 106-115: character design must be desire, need, obstacle, false belief, endpoint, and structural role. Antagonists are value competitors, not generic evil.
- Lines 126-153: worldbuilding must lock rules, power, resources, and risk mechanisms before lore details; every setting element must create choices and consequences.
- Lines 157-209: scenes need goal, conflict, outcome, reaction, dilemma, decision, information movement, and a real next hook.
- Lines 223-237 and 328-337: planning artifacts should be versioned and split into durable files/contracts; opening draft should be frozen only after reverse checks.
- Lines 353-357: every setting must explain how it creates conflict, every chapter must state what changed, and every subplot must justify why it belongs to the main line.

This means the current problem is not mainly "too many books are similar". Similarity is a symptom. The root problem is that the planner lacks a first-class story design decision system.

---

## Current Diagnosis

The current codebase already has many strong downstream components:

- `ProjectModel` has `theme_statement`, `dramatic_question`, `reader_contract_json`, and metadata storage in `src/bestseller/infra/db/models.py`.
- `PlotArcModel`, `ArcBeatModel`, `ClueModel`, `PayoffModel`, `ChapterContractModel`, and `SceneContractModel` exist in `src/bestseller/infra/db/models.py`.
- `planning_kernel.py` checks unique hook, long-arc capacity, volume differentiation, progression/rule/relationship engines.
- `narrative_lines.py` can validate overt, undercurrent, hidden, and core-axis narrative layers.
- `genre_review_profiles.py`, `config/novel_categories/*.yaml`, and prompt packs contain genre-specific review and writing guidance.

The limitation is upstream:

- `planning_kernel.py` evaluates readiness but does not design a book-specific plot grammar.
- `config/novel_categories/*.yaml` mostly provides fixed challenge evolution templates, not a decision grammar that changes with book state.
- `planner.py` still has generic fallback plot material, especially around character wounds, stakes, conflict forces, and chapter goals.
- Chapter outline fallback in `planner.py` builds chapters from a finite template pool, then adds a unique beat. That gives surface variation but not true plot design.
- Current prewrite directives are passed into outline prompts after the high-level plan is already mostly formed.
- Cross-project sameness detection can catch repeated outcomes, but it cannot make a weak planner produce richer story structures.

The architecture needs to move from:

```text
genre + premise -> fallback book/world/cast -> volume templates -> chapter templates -> audits
```

to:

```text
genre + premise + target shape -> StoryDesignKernel -> world/cast design shaped by kernel
-> volume strategy -> state-aware beat schedule -> chapter/scene contracts -> reverse outline gate
```

---

## Target Design

### 1. Story Shape Router

Add a deterministic router that classifies each project before planning:

```python
class StoryShape(BaseModel):
    length_class: Literal["short", "novella", "long", "very_long", "series"]
    publication_mode: Literal["web_serial", "commercial_book", "literary", "ip_development"]
    outline_depth: Literal["scene", "chapter", "volume_chapter_scene"]
    primary_duties: list[str]
    ending_contract: str
```

Inputs:

- `ProjectModel.genre`
- `ProjectModel.sub_genre`
- `ProjectModel.target_chapters`
- `ProjectModel.target_word_count`
- `ProjectModel.audience`
- `metadata_json.story_facets`
- prompt pack key

Examples:

- 20-chapter mystery: chapter-level outline plus key scene cards, strict fair-play clue chain.
- 300-chapter xianxia: volume/arc/batch/chapter hierarchy, power/resource ladder, periodic payoffs.
- romance: relationship stage ladder, public/private conflict, trust/debt/boundary changes.
- literary short: scene-level structure and theme loop, not forced serial cliffhangers.

### 2. StoryDesignKernel

Create `src/bestseller/services/story_design_kernel.py` with Pydantic models:

```python
class PremiseContract(BaseModel):
    one_sentence: str
    theme_question: str
    dramatic_question: str
    reader_promise: str
    unique_story_claim: str
    failure_cost: str

class CharacterConflictContract(BaseModel):
    protagonist_visible_goal: str
    protagonist_inner_need: str
    protagonist_false_belief: str
    protagonist_decision_policy: str
    antagonist_value_claim: str
    antagonist_goal: str
    value_collision: str
    irreversible_choice_points: list[str]

class WorldConflictContract(BaseModel):
    p0_rules: list[dict[str, str]]
    power_or_resource_flow: str
    risk_mechanism: str
    normal_people_impact: str
    why_easy_solution_fails: str
    rule_exploitation_paths: list[str]

class StructureStrategy(BaseModel):
    macro_models: list[str]
    selected_reason: str
    act_or_volume_beats: list[dict[str, str]]
    midpoint_function: str | None = None
    climax_choice: str
    resolution_image: str

class PlotTreeNode(BaseModel):
    code: str
    line_type: Literal["main", "subplot", "relationship", "mystery", "world", "theme"]
    promise: str
    dependency_on_mainline: str
    plant_window: str
    payoff_window: str
    failure_if_removed: str

class BeatScheduleItem(BaseModel):
    scope: Literal["volume", "chapter", "scene"]
    number: int
    beat_function: str
    change_vector: str
    causal_link: str
    character_choice: str
    world_rule_used: str
    information_movement: str
    emotional_movement: str
    reader_reward: str
    hook_or_aftereffect: str

class StoryDesignKernel(BaseModel):
    version: int = 1
    story_shape: StoryShape
    premise_contract: PremiseContract
    character_conflict: CharacterConflictContract
    world_conflict: WorldConflictContract
    structure_strategy: StructureStrategy
    plot_tree: list[PlotTreeNode]
    beat_schedule: list[BeatScheduleItem]
    reverse_outline_status: Literal["draft", "checked", "frozen"]
```

Persistence:

- Store latest kernel in `ProjectModel.metadata_json["story_design_kernel"]`.
- Also persist as `PlanningArtifactVersionModel` with `artifact_type="story_design_kernel"`.
- Do not create new DB tables in phase 1. Existing tables can later materialize its arcs/beats.

### 3. Story Grammar Configs

Create `config/story_design_grammars/*.yaml`.

Each grammar defines how plot should work for that category, not what plot should happen:

```yaml
key: suspense-mystery
required_contracts:
  - fair_play_clue_chain
  - suspect_or_explanation_space
  - evidence_decay_or_access_pressure
chapter_change_vectors:
  - clue_added
  - clue_reinterpreted
  - suspect_pressure_changed
  - alibi_broken
  - false_solution_formed
  - danger_cost_paid
forbidden_defaults:
  - every chapter reveals "truth"
  - final monologue explains all mysteries
  - evidence appears only when needed
state_variables:
  - open_questions
  - known_false_assumptions
  - clue_fairness_score
  - danger_pressure
```

Initial grammar files:

- `config/story_design_grammars/action-progression.yaml`
- `config/story_design_grammars/suspense-mystery.yaml`
- `config/story_design_grammars/relationship-driven.yaml`
- `config/story_design_grammars/female-growth-ncp.yaml`
- `config/story_design_grammars/base-building.yaml`
- `config/story_design_grammars/strategy-worldbuilding.yaml`
- `config/story_design_grammars/eastern-aesthetic.yaml`
- `config/story_design_grammars/esports-competition.yaml`
- `config/story_design_grammars/default.yaml`

The key shift: each grammar must define valid `change_vectors`, `reader_rewards`, `state_variables`, and `forbidden_defaults`.

### 4. State-Driven Planning

Add `StoryStateSnapshot`:

```python
class StoryStateSnapshot(BaseModel):
    current_chapter: int
    current_volume: int
    open_goals: list[str]
    unresolved_debts: list[str]
    active_plot_tree_nodes: list[str]
    character_state_changes: list[str]
    relationship_state_changes: list[str]
    world_rule_state: list[str]
    recent_change_vectors: list[str]
    recent_reader_rewards: list[str]
    due_payoffs: list[str]
    blocked_repetitions: list[str]
```

Build it from:

- chapter rows
- `PlotArcModel` / `ArcBeatModel`
- `ClueModel` / `PayoffModel`
- `EmotionTrackModel`
- `AntagonistPlanModel`
- `DiversityBudget`
- whole-book engagement ledger

Before planning any new volume or outline batch, the planner must ask:

1. Which promises are open?
2. Which promises are due?
3. Which state variables changed recently?
4. Which change vector has been overused?
5. What kind of beat does this genre grammar need next?

This is the actual fix for "the book type and state have changed but planning is not compatible".

### 5. Reverse Outline Gate

Create `src/bestseller/services/reverse_outline_gate.py`.

The gate runs after volume plan and after each volume outline, before materialization:

```python
class ReverseOutlineFinding(BaseModel):
    code: str
    severity: Literal["critical", "high", "warning"]
    scope: str
    message: str
    repair_instruction: str

class ReverseOutlineReport(BaseModel):
    passed: bool
    findings: list[ReverseOutlineFinding]
    chapter_change_coverage: float
    causal_chain_coverage: float
    subplot_dependency_coverage: float
    world_conflict_usage_coverage: float
```

Checks:

- Every chapter has a `change_vector`.
- Every chapter states what changed in plot, character, information, relationship, resource, or status.
- Every subplot node has `dependency_on_mainline`.
- Every world rule used in chapters has cost or consequence.
- Every chapter has `causal_link`, not just "then this happens".
- At least one of `goal/conflict/result/reaction/decision` is concrete per scene.
- For web serials, chapter ending has forward pull.
- For literary/short modes, do not force web serial hooks; require thematic or perceptual turn instead.

### 6. Planner Integration

Modify `src/bestseller/services/planner.py`.

New high-level flow:

```text
1. Generate/repair BookSpec
2. Generate StoryDesignKernel draft from project + BookSpec + report-informed rules
3. Generate WorldSpec using StoryDesignKernel.world_conflict
4. Generate CastSpec using StoryDesignKernel.character_conflict
5. Finalize StoryDesignKernel using BookSpec + WorldSpec + CastSpec
6. Generate VolumePlan from StoryDesignKernel.structure_strategy + plot_tree
7. Run ReverseOutlineGate on VolumePlan
8. Generate ChapterOutlines from StoryDesignKernel.beat_schedule + StoryStateSnapshot
9. Run ReverseOutlineGate on chapter outline batch
10. Materialize only if gate passes or repair loop succeeds
```

Where to wire:

- Add `_story_design_kernel_prompts(...)`.
- Add `_generate_story_design_kernel_with_repair_loop(...)`.
- Call it after `_book_spec_prompts(...)`.
- Re-run/finalize it after world/cast are available.
- Pass `story_design_kernel` into `_volume_plan_prompts(...)`.
- Pass `story_design_kernel` and `StoryStateSnapshot` into `_chapter_outline_batch_prompts(...)`.
- Replace fallback chapter templates around `planner.py` chapter-goal generation with kernel-driven beat items.

### 7. Planning Kernel Upgrade

Modify `src/bestseller/services/planning_kernel.py`.

Add checks:

- `story_design_kernel_missing`
- `premise_contract_missing`
- `character_conflict_contract_missing`
- `world_conflict_contract_missing`
- `structure_strategy_missing`
- `plot_tree_dependency_missing`
- `chapter_change_vector_missing`
- `state_driven_planning_missing`

The readiness gate should not just say "missing unique hook"; it should say which design layer is absent.

Important: `prewrite_readiness_block_on_failure` is currently false in settings. Keep that default for migration, but log the missing layers and inject repair directives. Later, enable blocking for new projects only.

### 8. Category Compatibility

Update category files so each genre/category has an explicit story grammar.

Examples:

Action progression:

- State variables: power ceiling, resource scarcity, enemy tier, injury/cost, public status.
- Chapter change vectors: new technique learned, resource won/lost, rule loophole found, enemy adapts, status changes.
- Invalid default: "lost important person" as universal wound.

Suspense mystery:

- State variables: clue set, false assumptions, evidence decay, suspect pressure, danger clock.
- Chapter change vectors: clue added, clue reframed, suspect cleared, contradiction appears, false solution fails.
- Invalid default: all books centered on old family case.

Relationship-driven:

- State variables: trust, distance, debt, boundary, public pressure, private knowledge.
- Chapter change vectors: boundary tested, debt incurred, vulnerability shown, public mask cracks, choice reveals value.
- Invalid default: all conflict from missing family/old betrayal.

Base-building:

- State variables: resource inputs, bottlenecks, worker morale, defense, governance, tech tree.
- Chapter change vectors: bottleneck solved, new bottleneck introduced, visible build step, governance tradeoff.
- Invalid default: mystery/truth reveal as main engine.

Strategy-worldbuilding:

- State variables: board position, leverage, factions, legitimacy, intelligence, cost.
- Chapter change vectors: move, countermove, feint exposed, alliance shifts, rule changed.
- Invalid default: opponents are passive pressure labels.

Eastern aesthetic:

- State variables: image system, season/ritual, moral pressure, relationship restraint, social etiquette.
- Chapter change vectors: perception shifts, ritual consequence, social meaning changes, inner choice.
- Invalid default: forced cliffhanger every chapter.

### 9. Existing Project Repair

Do not blindly rewrite prose.

For existing projects:

1. Generate a `StoryDesignKernel` from existing book/world/cast/volume/chapters.
2. Run reverse outline gate.
3. Produce a repair report:
   - missing design layers
   - chapters with no change vector
   - subplots disconnected from mainline
   - repeated state transitions
   - generic family-loss/old-case overuse if present
4. Only regenerate future volume/chapter plans by default.
5. Rewrite existing chapters only when the user explicitly asks.

### 10. Acceptance Criteria

The redesign is successful when:

- Five new projects in different categories produce visibly different `story_design_kernel` payloads before volume planning.
- A xianxia, a base-building novel, a relationship-driven novel, and a suspense novel no longer share the same protagonist wound, same world stakes, or same chapter change pattern.
- Every planned chapter has a non-empty `change_vector`, `causal_link`, `character_choice`, `reader_reward`, and `hook_or_aftereffect`.
- Every subplot has `dependency_on_mainline` and `failure_if_removed`.
- Every world rule used by the plot includes cost or consequence.
- Reverse outline gate catches a chapter that only "continues pressure" without changing anything.
- Cross-project audit becomes a release check, not the primary design mechanism.

---

## Implementation Tasks

### Task 1: Add Story Shape Router

**Files:**
- Create: `src/bestseller/services/story_shape_router.py`
- Create: `tests/unit/test_story_shape_router.py`

**Test cases:**

- 12 chapters -> `short`, scene-level or chapter-level depth.
- 80 chapters web audience -> `long`, chapter plus key-scene depth.
- 300 chapters -> `very_long` or `series`, volume/arc/batch/chapter depth.
- `audience="web-serial"` -> web serial chapter duties.
- literary prompt pack or audience -> thematic turn instead of forced cliffhanger.

### Task 2: Add StoryDesignKernel Models And Loader

**Files:**
- Create: `src/bestseller/services/story_design_kernel.py`
- Create: `tests/unit/test_story_design_kernel.py`

Implement Pydantic models and helpers:

- `story_design_kernel_to_dict(...)`
- `story_design_kernel_from_dict(...)`
- `validate_story_design_kernel(...)`
- `render_story_design_kernel_prompt_block(...)`

### Task 3: Add Story Grammar Configs

**Files:**
- Create: `config/story_design_grammars/default.yaml`
- Create one grammar YAML for each category listed above.
- Create: `src/bestseller/services/story_design_grammars.py`
- Create: `tests/unit/test_story_design_grammars.py`

Tests:

- every category resolves a grammar.
- every grammar has at least 5 `chapter_change_vectors`.
- every grammar has `state_variables`.
- every grammar has `forbidden_defaults`.

### Task 4: Generate StoryDesignKernel In Planner

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Test: `tests/unit/test_planner_story_design_kernel.py`

Add:

- `_fallback_story_design_kernel(...)`
- `_story_design_kernel_prompts(...)`
- `_generate_story_design_kernel_with_repair_loop(...)`

Minimum behavior:

- Kernel must include premise, character conflict, world conflict, structure strategy, plot tree, and beat schedule.
- Fallback must derive from category grammar and project shape, not from generic lost-family/old-case templates.

### Task 5: Feed Kernel Into World/Cast/Volume Prompts

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Modify tests around prompt construction if present.

Prompt changes:

- WorldSpec must explain how rules create conflict according to `world_conflict`.
- CastSpec must create protagonist/antagonist value collision according to `character_conflict`.
- VolumePlan must follow `structure_strategy` and `plot_tree`.

### Task 6: Replace Chapter Fallback Templates With Beat Schedule

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Test: `tests/unit/test_chapter_outline_story_design.py`

Change fallback outline so a chapter is built from `BeatScheduleItem`, not a generic sentence pool.

Each fallback chapter must include:

- `beat_function`
- `change_vector`
- `causal_link`
- `character_choice`
- `world_rule_used`
- `information_movement`
- `emotional_movement`
- `reader_reward`
- `hook_or_aftereffect`

Map those fields into existing chapter/scene contract metadata so current downstream code can use them without schema migration.

### Task 7: Build StoryStateSnapshot

**Files:**
- Create: `src/bestseller/services/story_state_snapshot.py`
- Create: `tests/unit/test_story_state_snapshot.py`
- Modify: `src/bestseller/services/planner.py`

Snapshot should collect:

- recent chapters and recent outline beats
- open/due payoffs
- active plot arcs and arc beats
- relationship/emotion state
- world rule state
- recent change vectors and rewards

Use it before generating each volume outline and progressive volume plan.

### Task 8: Add Reverse Outline Gate

**Files:**
- Create: `src/bestseller/services/reverse_outline_gate.py`
- Create: `tests/unit/test_reverse_outline_gate.py`
- Modify: `src/bestseller/services/workflows.py`

Gate both:

- volume plan
- chapter outline batch

For phase 1, write findings into workflow metadata and repair loop prompt. Do not block old projects by default.

### Task 9: Upgrade Prewrite Readiness

**Files:**
- Modify: `src/bestseller/services/planning_kernel.py`
- Modify: `tests/unit/test_planning_kernel.py`

Add story-design-specific readiness checks and repair directives.

Expected new report examples:

- `story_design_kernel_missing`
- `character_conflict_contract_missing`
- `world_conflict_contract_missing`
- `plot_tree_dependency_missing`
- `chapter_change_vector_missing`

### Task 10: Existing Project Design Repair CLI

**Files:**
- Create: `scripts/audit_story_design_kernel.py`
- Create: `scripts/repair_story_design_kernel.py`
- Create: `tests/unit/test_story_design_repair_scripts.py`

CLI behavior:

```bash
python scripts/audit_story_design_kernel.py --project-slug <slug>
python scripts/repair_story_design_kernel.py --project-slug <slug> --dry-run
python scripts/repair_story_design_kernel.py --project-slug <slug> --future-only
```

Default repair should only create/fix planning artifacts. It must not rewrite draft prose unless a separate explicit flag is added later.

---

## Rollout Plan

1. Implement model/config/router without touching live planner behavior.
2. Wire kernel generation and persistence behind a feature flag.
3. Add prompt injection to world/cast/volume planning.
4. Replace fallback chapter outline generation with kernel beat schedule.
5. Add reverse outline gate in warning-only mode.
6. Run fixture projects for four categories and compare story kernels.
7. Enable blocking for new projects only.
8. Run existing project repair in dry-run mode, then selectively regenerate future plans.

Feature flags:

- `enable_story_design_kernel`
- `enable_story_state_driven_planning`
- `enable_reverse_outline_gate`
- `reverse_outline_gate_block_on_failure`

---

## What This Supersedes

The earlier diversity plan remains useful for detection and cleanup, but it should no longer be the main design strategy.

Keep:

- placeholder name detection
- generic stakes detection
- title/fallback repetition checks
- character alias bloat repair
- cross-project audit dashboard

Demote:

- cross-project trope budget as the primary mechanism.

Replace with:

- project-local story grammar
- state-driven chapter planning
- reverse outline gate
- story design kernel as the source of truth

The planner should not ask "is this too similar to another book?" first. It should ask "given this book's genre, promise, protagonist, world rules, and current state, what kind of story beat is required next?"

