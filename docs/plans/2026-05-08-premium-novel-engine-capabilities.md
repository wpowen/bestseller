# Premium Novel Engine Capability Plan

Date: 2026-05-08

## Goal

Upgrade BestSeller from a generic long-form novel production pipeline into a genre-aware engine that can repeatedly produce usable commercial-grade long novels in the structure family of proven web novels such as `凡人修仙传`, `诡秘之主`, `大奉打更人`, and selected newer high-concept works.

The target is not imitation of protected IP, names, plots, or prose. The target is to encode the production logic behind these works:

- stable genre engine
- explicit power and resource causality
- protagonist decision consistency
- faction and world ecology
- long-horizon setup and payoff
- chapter-level pursuit pressure
- batch-level quality gates

Direct publishing, monetization, platform upload, ads, and sales operations are out of scope for this plan.

## Current Implementation Status

Status on 2026-05-08:

- First engineering slice is implemented.
- `diversity_budget.hot_vocab` no longer crashes on malformed persisted chapter keys.
- `src/bestseller/domain/progression.py` now defines first-class progression data models.
- `src/bestseller/services/progression.py` now validates realm ladders, resource balances, technique prerequisites, artifact capabilities, and breakthrough causes.
- `src/bestseller/services/progression.py` now materializes progression context from `world_spec`, `cast_spec`, and `volume_plan`.
- `src/bestseller/domain/decision_policy.py` now defines protagonist decision policy, risk tolerance, preferred tactics, moral boundaries, forbidden behaviors, and decision audit models.
- `src/bestseller/services/decision_policy.py` now validates cautious-survivalist decisions, rejecting vanity risk, forbidden behavior, and moral-boundary violations.
- `src/bestseller/services/prompt_constructor.py` now has dedicated progression and decision-policy slots, rendered after the story-bible slice and before reader-contract/methodology sections.
- `src/bestseller/domain/context.py` now carries `progression_context_block` and `decision_policy_block` on the real `SceneWriterContextPacket`.
- `src/bestseller/services/drafts.py` now renders those blocks in `build_scene_draft_prompts`, so the live scene writer sees them, not only the L3 chapter prompt path.
- `src/bestseller/services/premium_genre_engine.py` now bridges persisted project metadata into prompt-ready premium blocks from `world_spec`, `cast_spec`, `volume_plan`, and optional decision-policy metadata.
- `src/bestseller/services/pipelines.py` now injects premium genre blocks into `shared_context` before `generate_scene_draft`.
- Rule-system projects now get `rule_system_context_block` from `project.metadata_json` or `shared_context.story_bible.world_rules`, so民俗悬疑/规则怪谈/无限流类项目 can expose visible rule effects, exploit paths, and costs to the writer.
- If a rule-system genre declares no usable rules, the premium engine records `rule_system_missing` and surfaces it through context warnings instead of silently falling back to generic prose.
- `tests/unit/test_progression_services.py` proves a xianxia unearned breakthrough fails and an earned breakthrough passes.
- `tests/unit/test_decision_policy.py` proves a cautious protagonist rejects public vanity duels and allows high risk when rare upside plus escape route is explicit.
- `tests/unit/test_premium_genre_engine.py` proves xianxia metadata produces progression context plus cautious protagonist policy.
- `tests/unit/test_premium_genre_engine.py` also proves rule-system metadata produces a writer-ready rule block.
- `tests/unit/test_hype_draft_plumbing.py` proves premium engine blocks land in the scene writer prompt.
- `tests/unit/test_pipeline_services.py::test_run_scene_pipeline_injects_premium_engine_blocks_into_writer_context` proves the real scene pipeline injects the blocks into writer context.

Latest verification:

- `./.venv/bin/python -m ruff check src/bestseller/domain/progression.py src/bestseller/services/progression.py tests/unit/test_progression_services.py` passes.
- `./.venv/bin/python -m ruff check src/bestseller/domain/decision_policy.py src/bestseller/services/decision_policy.py tests/unit/test_decision_policy.py src/bestseller/domain/progression.py src/bestseller/services/progression.py tests/unit/test_progression_services.py` passes.
- `./.venv/bin/python -m ruff check src/bestseller/services/premium_genre_engine.py tests/unit/test_premium_genre_engine.py src/bestseller/domain/context.py tests/unit/test_hype_draft_plumbing.py` passes.
- `./.venv/bin/python -m pytest tests/unit/test_premium_genre_engine.py tests/unit/test_hype_draft_plumbing.py tests/unit/test_pipeline_services.py::test_run_scene_pipeline_injects_premium_engine_blocks_into_writer_context tests/unit/test_prompt_constructor.py tests/unit/test_decision_policy.py tests/unit/test_progression_services.py -q --no-cov` passes with 69 tests.
- `./.venv/bin/python -m pytest tests/unit/test_decision_policy.py tests/unit/test_diversity_budget.py tests/unit/test_prompt_constructor.py tests/unit/test_progression_services.py tests/unit/test_story_bible_coercion.py -q --no-cov` passes with 157 tests.
- `./.venv/bin/python -m pytest -q --no-cov` passes with 3075 tests.
- `./.venv/bin/python -m pytest -q` currently fails only on the existing global coverage threshold: total coverage is below `--cov-fail-under=80` even though all behavior tests pass.

## Live Integration Map

The premium engine is now connected at the pre-draft scene-writing seam:

1. Planning/materialization stores `world_spec`, `cast_spec`, and `volume_plan` on `project.metadata_json`.
2. `run_scene_pipeline` builds a shared `SceneWriterContextPacket` once per scene.
3. `premium_genre_engine.build_premium_genre_engine_blocks` converts project metadata into:
   - `progression_context_block`
   - `decision_policy_block`
   - `rule_system_context_block`
4. `run_scene_pipeline` attaches those blocks to `shared_context`.
5. `generate_scene_draft` forwards the packet to `build_scene_draft_prompts`.
6. `build_scene_draft_prompts` injects the blocks before lower-tier diversity and craft guidance.

This means current xianxia/progression projects can use the new ability if their story bible has enough structured metadata. Current民俗悬疑/rule-system projects can also use the rule block if `world_rules` or rule-ledger metadata is present. The remaining gap is post-chapter state mutation: generated chapters do not yet automatically update resources, techniques, artifacts, bottlenecks, rules, costs, and decision events.

## Current Project Readiness Audit

Observed local outputs on 2026-05-08:

- `xianxia-upgrade-1776137730`: 551 exported chapters. Most aligned with the new progression/decision engine, but only future generation benefits automatically; existing exported chapters still contain historical audit issues. Needs DB metadata to include `world_spec.power_system`, `cast_spec.protagonist.power_tier`, resources, and `volume_plan`.
- `exorcist-detective-1778051012`: 16 exported chapters plus `story-bible/rule-ledger.md`. This project needs rule-system support more than cultivation support; the rule-system block was added specifically so future scenes can consume `world_rules`/rule-ledger state through the same writer context path.
- `female-no-cp-1776303225`: 481 exported chapters. Current premium slice helps only indirectly. It still needs a female-growth/no-CP relationship-agency engine: career/resource ladder, social pressure, non-romance bond network, and agency checks.
- `romantasy-1776330993`: 412 exported chapters. Current premium slice helps only if the world has hard magic/rules, but it still lacks romance/consent/tension/payoff contracts.
- `superhero-fiction-*`: long exported projects. Current premium slice can represent power constraints only if stored as `power_system`, but superhero fiction still needs ability-limit ledgers, civilian-cost contracts, villain-reaction ecology, and public-trust state.

Current audit conclusion:

- Not all target capabilities are complete.
- The framework can now start writing closer to ranking-style xianxia/progression and rule-system suspense, provided the story bible has structured state.
- It cannot yet honestly claim parity with all榜单作品 families. The missing engines are type-specific, not just prompt phrasing.

## Current Type Support Snapshot

Support levels after the current engineering slice:

- Cultivation survival / `凡人流`: partially supported. Realm ladder, current realm, resource ledger, active bottleneck, and cautious decision policy can now reach the writer prompt. Missing faction ecology, opportunity map, and post-chapter resource mutation.
- LitRPG / system progression: partially supported. Progression scaffolding is reusable, but stat sheets, quests, rewards, cooldowns, and system-law validation are not first-class yet.
- Rule-system mystery / `诡秘式`: weak partial support. Clues and payoffs exist, but rule lattice, pathway legality, ritual costs, and forbidden knowledge are still missing.
- Case/court/cultivation hybrid / `大奉式`: weak partial support. Case-like presets and narrative lines exist, but evidence chain, suspects, institution pressure, and case-to-conspiracy linkage are not first-class.
- Apocalypse/resource/base-building: prompt-level support. Resource scarcity is represented only for protagonist progression, not settlement inventory, logistics, faction needs, or territory state.
- Urban cultivation / black-tech rise: prompt-level to weak partial support. Power progression fallback works when metadata has `power_system`, but tech tree, business competitors, patents, capital, and public reaction are not modeled.
- Romance / dark romance / romantasy / reverse harem: prompt-level support. Relationship milestones exist, but consent/tension contracts, jealousy topology, intimacy boundaries, and romantic payoff cadence need a dedicated engine.
- Mystery / police / cozy mystery: partial support. Clue/payoff tools exist, but legality of evidence, suspect state, alibi, and reveal fairness need structured validators.
- Esports / game competition: prompt-level support. Match state, tournament bracket, team tactics, patch/meta changes, and viewer pressure are not first-class.
- Sci-fi / mecha / military / space opera: prompt-level support. World and fleet details can be prompted, but logistics, tech constraints, command doctrine, and battle-state ledgers are not modeled.
- Unstable-truth horror / `道诡式`: unsupported by design for now. The framework is truth-first; this type needs an opt-in truth-layer model before generation.

## Final Ranking Benchmark Protocol

When all core development phases are complete, run a separate benchmark against current ranking/top-performing books at the evaluation date. This must use up-to-date ranking data, not memory, because platform rankings change.

Scope:

- Select 3-5 current ranking works per target type, starting with cultivation survival, rule-system mystery, and case/court hybrid.
- Extract structural patterns only: premise contract, protagonist policy, progression engine, faction ecology, reveal cadence, chapter pursuit pressure, payoff windows, and volume escalation.
- Do not copy names, worlds, artifacts, characters, scene sequences, prose, or protected expression.

Benchmark questions:

- Can the framework represent the book's genre engine as structured state?
- Can it generate chapter plans that obey the same kind of causality and escalation?
- Can validators reject the common failure modes that would make the generated book feel fake: empty upgrades, static factions, random protagonist decisions, impossible abilities, missing payoff, and repetitive chapter loops?
- Can the prompt context expose the right constraints at the moment of writing, rather than only detecting problems after generation?
- Can a 30-chapter fixture pass pure validators and produce a repairable report when intentionally damaged?

Exit criteria before expanding to more types:

- At least one good fixture and one deliberately bad fixture per target type.
- Pure validators pass/fail the fixtures for the intended reasons.
- The premium book gate produces actionable repair items, not generic quality complaints.
- A final capability matrix shows which ranking-book structural patterns are supported, partially supported, or unsupported.

## Reference Patterns

### 1. `凡人流` cultivation progression

Core logic:

- ordinary-person viewpoint
- survival rationality instead of pure destiny
- resource scarcity as plot engine
- cultivation tiers with bottlenecks, costs, opportunities, and risks
- sects, clans, markets, forbidden zones, and higher realms as resource ecosystems
- slow compounding advantage rather than effortless power jumps

BestSeller can already express the outer shell through `xianxia-upgrade-core`, world expansion, continuity facts, and hype loops. It still lacks first-class models for realms, techniques, artifacts, resources, sect rank, opportunity nodes, and protagonist decision policy.

### 2. `诡秘式` rule-system mystery

Core logic:

- precise rule-based power system
- multiple advancement paths with hard constraints
- clues planted across long windows
- social institutions, history, and occult rules all tied to the same system
- the reader enjoys solving the rules, not only watching fights

BestSeller has narrative lines, clues, payoff tracking, and world rules. It still lacks a compact `rule lattice` model that can validate whether a reveal, ability, ritual, cost, or advancement is legal in the active world system.

### 3. `大奉式` hybrid genre stack

Core logic:

- case/investigation loop as chapter engine
- court/power/faction layer as long arc
- cultivation layer as escalation layer
- comic voice and relationship chemistry as retention layer
- each case reveals a piece of larger structure

BestSeller has conflict taxonomy, case-like genre presets, character arcs, and narrative lines. It still lacks a reusable `case arc` model that connects local mystery, evidence, suspects, institutional pressure, and long-arc reveal.

### 4. `道诡式` unstable truth

Core logic:

- truth is not always stable
- protagonist perception can be unreliable
- contradictions may be intentional narrative material
- reality layers compete instead of resolving immediately

BestSeller is currently truth-first. That is good for `凡人流` and most commercial long-form genres, but it cannot safely produce `道诡式` fiction without a controlled `truth-layer` model. This should be a later opt-in capability, not a default mode.

## Current Strengths

Existing capabilities that should be reused:

- `config/prompt_packs/xianxia-upgrade-core.yaml`: strong xianxia prompt policy and anti-pattern list.
- `writing_presets.py`: genre presets, platform expectations, reader promise, selling points, and hype decks.
- `world_expansion.py`: staged world disclosure and volume frontier.
- `continuity.py`: hard facts, time anchors, level/resource/location extraction.
- `setup_payoff_tracker.py`: compression-release debt detection for humiliation and payoff beats.
- `character_arcs.py`: inner arc, lie/want/need/ghost, and decision-scene thinking scaffold.
- `narrative_line_tracker.py`: chapter-level dominant narrative line tracking.
- `project_health.py`: overdue clues, overused hooks, golden-three checks, and setup-payoff debts.

## Critical Gaps

### Gap A: Genre systems are mostly prompt-level

The repo has strong xianxia prompt guidance, but cultivation mechanics are not stored as first-class structured truth. A long cultivation novel needs the system to know:

- what realms exist
- what bottlenecks exist
- what resources unlock which bottleneck
- which techniques require which realm
- which artifacts have known limits
- what sect rank grants or blocks
- why a breakthrough is earned now

### Gap B: Power progression lacks causal accounting

Current checks can catch level regression, but they cannot reliably block:

- empty upgrades
- unearned breakthroughs
- forgotten cultivation injuries
- suddenly usable artifacts
- resource rewards with no prior source
- techniques mastered without time, teacher, cost, or insight

### Gap C: Protagonist behavior is not yet a hard contract

The system can define a protagonist archetype and internal arc. It does not yet enforce a stable protagonist decision policy such as:

- cautious survivalist
- gambler hero
- institutional operator
- compassionate protector
- revenge-driven climber
- truth-seeking investigator

For `凡人流`, this matters more than decorative personality. The protagonist must make decisions in a recognizably consistent way under pressure.

### Gap D: Factions are static records, not living ecology

Current `FactionModel` can store faction facts, but the story engine does not yet simulate faction interests, resource needs, alliances, grudges, succession pressure, market control, or reactions to protagonist actions.

### Gap E: Long-arc reveals are tracked, but not enough by genre logic

There are clue and payoff tools, but the engine needs genre-specific reveal contracts:

- cultivation: realm secret, artifact origin, bloodline truth, sect betrayal
- mystery: clue, suspect, false lead, evidence chain, case solution
- occult: ritual cost, pathway law, taboo, historical inversion
- court/political: institution secret, official rank, faction bargain, hidden patron

### Gap F: Batch quality is not measured by book-type benchmark

Tests prove many local helpers. They do not yet prove that a generated xianxia book behaves like a usable xianxia book across 30, 80, or 180 chapters.

## Delivery Plan

### Phase 0: Test Baseline And Red Line

Purpose: restore trust before adding deeper capabilities.

Tasks:

- Fix current failing `diversity_budget` and `prompt_constructor` tests.
- Add a no-coverage quick target for fast capability validation if the full suite remains expensive.
- Add a dedicated `premium_engine` test marker for new genre-engine tests.
- Record current full-suite status after fixes.

Acceptance:

- `./.venv/bin/python -m pytest tests/unit/test_diversity_budget.py tests/unit/test_prompt_constructor.py -q` passes.
- Existing non-slow suite no longer has known regressions caused by this work.

### Phase 1: Structured Progression Core

Purpose: make cultivation and progression systems first-class data, not prompt notes.

New domain concepts:

- `PowerSystem`
- `PowerRealm`
- `ProgressionBottleneck`
- `Technique`
- `Artifact`
- `ResourceLedger`
- `OpportunityNode`
- `BreakthroughEvent`

Implementation scope:

- Add Pydantic domain models under `src/bestseller/domain/progression.py`.
- Add SQLAlchemy models and migration for project-scoped progression data.
- Add services under `src/bestseller/services/progression.py`.
- Materialize progression data from `world_spec`, `cast_spec`, and `volume_plan`.
- Add compact writer context block listing current realm, bottleneck, active resources, active artifacts, and allowed breakthrough conditions.

Rules:

- A realm advancement must reference at least one cause: resource, technique, insight, mentor, artifact, injury recovery, trial, or external event.
- A technique cannot be used before its prerequisite realm or prerequisite artifact is active.
- An artifact cannot solve a conflict beyond its declared known capability unless a reveal unlocks it.
- Resource spend and reward must be recorded.

Acceptance:

- Unit tests cover realm ordering, prerequisite enforcement, resource spend/reward, artifact capability limits, and breakthrough validation.
- A sample xianxia fixture can reject an unearned breakthrough and pass an earned breakthrough.

### Phase 2: Protagonist Decision Policy

Purpose: make the protagonist act like the same person across hundreds of chapters.

New domain concepts:

- `DecisionPolicy`
- `PressureResponse`
- `RiskTolerance`
- `MoralBoundary`
- `PreferredTactic`
- `ForbiddenBehavior`
- `DecisionAudit`

Implementation scope:

- Add a protagonist decision contract to story bible materialization.
- Render a compact decision policy into scene writer context.
- Add a deterministic audit that checks major scene choices against the policy.
- Add an LLM critic fallback only for ambiguous cases.

Example for `凡人流`:

- risk tolerance: low unless upside is life-changing
- preferred tactics: observe, prepare, conceal, bargain, retreat, strike after certainty
- forbidden behavior: public bragging, reckless duel for vanity, free trust in strangers
- moral boundary: does not harm unrelated weak people for convenience

Acceptance:

- Tests reject a cautious protagonist accepting a public duel for vanity without sufficient cause.
- Tests allow the same protagonist to take high risk when resource upside, escape route, or life threat is explicit.

### Phase 3: Faction Ecology And Reaction Engine

Purpose: turn sects, clans, academies, courts, markets, guilds, and hidden organizations into pressure generators.

New domain concepts:

- `FactionInterest`
- `FactionResourceNeed`
- `FactionRelationship`
- `FactionRank`
- `FactionPressureEvent`
- `FactionReactionPlan`

Implementation scope:

- Extend faction materialization with interests, resources, rank ladder, taboos, and current pressure.
- Add post-chapter faction reaction extraction.
- Add next-chapter planning hints based on faction reaction.
- Add checks for static background factions that never react after major events.

Acceptance:

- If protagonist wins a sect trial, the relevant faction state changes.
- If protagonist takes a scarce resource, at least one faction reaction is queued or explicitly waived.
- Tests prevent repeated "all factions are shocked" reactions without differentiated interest.

### Phase 4: Genre Arc Templates

Purpose: provide structural engines for the four reference families.

Templates:

- `cultivation_survival`: `凡人流`
- `rule_mystery_progression`: `诡秘式`
- `case_court_cultivation`: `大奉式`
- `unstable_truth_horror`: `道诡式`, opt-in later

Implementation scope:

- Add `GenreArcTemplate` definitions under config.
- Add validators that check required arc objects per template.
- Add prompt constructor sections that are driven by template state, not static prose.

Minimum template requirements:

- `cultivation_survival`: realm ladder, resource scarcity, active bottleneck, faction pressure, opportunity map.
- `rule_mystery_progression`: rule lattice, clue queue, forbidden knowledge, advancement cost, historical mystery.
- `case_court_cultivation`: case arc, evidence chain, institutional pressure, comic relationship beat, long conspiracy reveal.
- `unstable_truth_horror`: truth layers, perception claims, contradiction classification, delayed resolution.

Acceptance:

- A project cannot select a template unless required objects exist or can be generated.
- Missing template objects block planning before chapter generation.

### Phase 5: Long-Horizon Payoff Contracts

Purpose: stop long books from becoming local chapter loops.

New capabilities:

- `PayoffContract` per clue, artifact, antagonist plan, faction debt, emotional promise, and realm bottleneck.
- Due window policy by genre.
- Payoff strength classification.
- Escalation if a due payoff is missed.

Implementation scope:

- Extend existing clue/setup payoff tools instead of replacing them.
- Add payoff contracts during planning and after chapter extraction.
- Surface due contracts to planner and writer context.
- Add a project-level gate before export completion.

Acceptance:

- A planted artifact origin must either pay off, renew, or be intentionally deferred before its due window expires.
- A bottleneck introduced as the current growth problem must either be solved, escalated, or replaced by a stronger bottleneck.

### Phase 6: Premium Book Gate

Purpose: decide whether a generated book is usable as a commercial novel, independent of platform publishing.

New service:

- `src/bestseller/services/premium_book_gate.py`

Gate dimensions:

- genre-engine completeness
- protagonist decision consistency
- progression causality
- faction ecology movement
- long-arc setup/payoff health
- chapter pursuit pressure
- opening promise delivery
- repetitive loop risk
- stale world or stale antagonist risk

Implementation scope:

- Reuse `commercial_novel_gate.py`, `project_health.py`, `scorecard.py`, and `volume_fingerprint.py`.
- Add a structured report with `passed`, `score`, `blocking_findings`, and `recommended_repair_actions`.
- Integrate the gate into project pipeline completion as a configurable final gate.

Acceptance:

- A book with empty upgrades fails.
- A book with repeated faction shock reactions fails.
- A book with stale protagonist decision policy warnings fails or requires repair.
- A passing sample fixture produces a stable JSON report.

### Phase 7: Batch Benchmark Harness

Purpose: prove the system can repeatedly produce usable genre books, not just one lucky output.

New examples:

- `examples/benchmarks/xianxia-survival-30ch`
- `examples/benchmarks/rule-mystery-30ch`
- `examples/benchmarks/case-court-30ch`

Benchmark runner:

- generate or load fixture projects
- run planning/materialization
- run pure validators
- optionally run mock LLM generation
- run premium book gate
- export benchmark report

Acceptance:

- `xianxia-survival-30ch` passes all pure progression and payoff gates.
- `rule-mystery-30ch` passes rule lattice and clue chain checks.
- `case-court-30ch` passes case arc and institution pressure checks.
- A deliberately bad fixture fails with actionable findings.

## Test Matrix

### Unit Tests

- progression model validation
- realm ordering
- resource ledger accounting
- artifact capability validation
- technique prerequisite validation
- breakthrough cause validation
- decision policy validation
- faction reaction differentiation
- payoff contract due window logic
- premium book gate scoring

### Integration Tests

- story bible materializes progression data
- chapter outline injects active bottleneck and opportunity nodes
- scene context includes current progression and decision policy
- post-chapter extraction updates resources, artifacts, faction reactions, and payoff contracts
- project pipeline blocks final completion when premium gate fails

### Golden Fixtures

Required fixtures:

- good xianxia: slow earned breakthrough
- bad xianxia: empty upgrade
- good rule mystery: legal rule reveal
- bad rule mystery: impossible ability use
- good hybrid case: evidence chain pays into long arc
- bad hybrid case: local case has no evidence or long-arc connection

### Commands

Initial targeted verification:

```bash
./.venv/bin/python -m pytest tests/unit/test_diversity_budget.py tests/unit/test_prompt_constructor.py -q
```

After Phase 1:

```bash
./.venv/bin/python -m pytest tests/unit/test_progression_services.py -q
```

After Phase 2:

```bash
./.venv/bin/python -m pytest tests/unit/test_decision_policy.py -q
```

After Phase 3:

```bash
./.venv/bin/python -m pytest tests/unit/test_faction_ecology.py -q
```

After Phase 6:

```bash
./.venv/bin/python -m pytest tests/unit/test_premium_book_gate.py tests/integration/test_premium_engine_pipeline.py -q
```

Final verification:

```bash
./.venv/bin/python -m pytest -m "not slow and not e2e" -q
```

## Implementation Order

1. Restore test baseline.
2. Add progression domain models and pure validators.
3. Add persistence and materialization for progression data.
4. Add protagonist decision policy and audit.
5. Add faction ecology and reaction engine.
6. Add genre arc templates.
7. Add long-horizon payoff contracts.
8. Add premium book gate.
9. Add batch benchmark harness.
10. Wire premium gate into pipeline completion.

## Non-Goals

- Do not copy named worlds, characters, sects, paths, artifacts, or plots from reference novels.
- Do not make `道诡式` unstable truth the default continuity model.
- Do not add direct platform publishing behavior.
- Do not bypass existing story bible, world expansion, continuity, scorecard, or project health systems.
- Do not solve quality by adding only longer prompts.

## First Engineering Slice

The first slice should be small enough to finish and verify:

1. Fix the current `diversity_budget.hot_vocab` regression.
2. Add `domain/progression.py` with pure data models.
3. Add `services/progression.py` with realm ordering, resource ledger, and breakthrough validation.
4. Add `tests/unit/test_progression_services.py`.
5. Add a xianxia fixture that proves unearned breakthrough fails and earned breakthrough passes.

This gives the project a concrete foundation for `凡人流` without forcing a large database migration first.
