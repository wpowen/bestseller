# BestSeller Framework — AI Context Reference

> This document is the complete design reference for the BestSeller AI novel generation system.
> Paste it into ChatGPT Custom GPT instructions, Cursor `.cursorrules`, or any AI assistant
> to enable context-aware development assistance without re-explaining the system.

---

## 1. System Overview

**BestSeller** is a distributed, multi-stage AI pipeline for generating production-quality long-form fiction (100 000–1 000 000+ words). It decomposes novel creation into a structured pipeline with persistent knowledge, quality gates, and iterative refinement — rather than a single LLM call.

**Core Philosophy:**
- **Pipeline > Single-shot** — Each novel is decomposed into Project → Volume → Chapter → Scene. Each level is independently executed, checkpointed, and recoverable on failure.
- **Knowledge-first** — Every scene writes persistent knowledge artifacts (Canon Facts, Timeline Events, Character State Snapshots) that all future scenes read as authoritative ground truth.
- **Constraints as quality** — Every chapter and scene has a *narrative contract* specifying exact deliverables (conflict, emotional movement, payoff density). The critic LLM scores output against these contracts.

**Project Hierarchy:**
```
Project
  └─ Volume  (major story arc, typically 30–60 chapters)
       └─ Chapter  (group of scenes, ~5000–7500 words)
            └─ Scene  (atomic prose unit, ~1200–2200 words)
```

---

## 2. Architecture

```
HTTP Client / Web UI (port 8787)
        │
   FastAPI REST API (port 8000)
        │
   ARQ Task Queue ←── Redis ──→ APScheduler (publishing cron)
        │
   ARQ Worker processes (×N)
        │
   Pipeline Orchestration Layer
        │
   ┌────┴──────────────────────────────────────────────┐
   │  Service Layer                                     │
   │  planner · drafts · reviews · knowledge · context  │
   │  retrieval · continuity · narrative · publishing   │
   └────────────────────┬──────────────────────────────┘
                        │
        PostgreSQL 16 + pgvector
        Redis (queue, cache, pubsub)
```

**Technology Stack:**

| Component | Technology |
|-----------|-----------|
| REST API | FastAPI + Uvicorn |
| Task queue | ARQ (Async Redis Queue) |
| Publishing scheduler | APScheduler + SQLAlchemy job store |
| Database | PostgreSQL 16 + pgvector (HNSW indexes) |
| Cache / PubSub | Redis |
| LLM gateway | LiteLLM (always via `services/llm.py`) |
| Schema migrations | Alembic |
| Config | Pydantic Settings + YAML layers |

**Key Services:**

| Service | File | Responsibility |
|---------|------|---------------|
| Pipeline orchestrator | `services/pipelines.py` | Run scene/chapter/project pipelines end-to-end |
| Draft generator | `services/drafts.py` | Build writer context, call LLM, validate output |
| Review & scoring | `services/reviews.py` | Multi-dimensional scoring, rewrite task creation |
| Planner | `services/planner.py` | Foundation plan, novel plan, volume plan |
| Knowledge extraction | `services/knowledge.py` | Post-draft Canon Facts, Timeline Events, State Snapshots |
| Context assembly | `services/context.py` | Build `SceneWriterContextPacket` (RAG) |
| Retrieval engine | `services/retrieval.py` | Hybrid search: vector + lexical + structural |
| Continuity | `services/continuity.py` | Fact monotonicity, chapter state snapshots |
| LLM gateway | `services/llm.py` | Role-based dispatch, circuit breaker, audit logging |
| Prompt packs | `services/prompt_packs.py` | Genre-specific prompt fragment loader |
| Publishing | `services/publishing/` | Multi-platform adapter (KDP, Qidian, Fanqie…) |

---

## 3. LLM Role System

**All LLM calls go through `complete_text()` in `services/llm.py`. Never call LiteLLM directly.**

| Role | Model | Temp | Max Tokens | Purpose |
|------|-------|------|-----------|---------|
| `planner` | claude-opus-4-5 | 0.82 | 16 000 | Foundation/novel/volume planning, multi-candidate reasoning |
| `writer` | claude-sonnet-4-5 | 0.85 | 8 000 | Prose generation, streaming enabled |
| `critic` | claude-haiku-4-5 | 0.25 | 2 000 | Deterministic scoring, review dimensions |
| `summarizer` | claude-haiku-4-5 | 0.20 | 1 500 | Knowledge compression, state extraction |
| `editor` | claude-sonnet-4-5 | 0.40 | 8 000 | Targeted scene rewrites preserving voice |

**LLM call request schema (`LLMCompletionRequest`):**
- `logical_role` — one of the 5 roles above
- `system_prompt`, `user_prompt`, `fallback_response`
- `project_id`, `workflow_run_id`, `step_run_id` — for audit traceability
- `metadata` — arbitrary dict for context labeling

**Resilience:**
- **Circuit breaker**: 5 consecutive failures → 60 s blackout, automatic recovery probe
- **Retry policy**: max 3 attempts, exponential backoff on `RateLimitError`, `APITimeoutError`, `ServiceUnavailableError`
- **Per-loop HTTP pooling**: one shared `httpx.AsyncClient` per event loop (eliminates TLS overhead)
- **Full audit trail**: every call logged to `llm_runs` table (model, role, tokens, latency, cost, prompt hash)

---

## 4. Pipeline Flow

### Scene Pipeline

```
1. Context Assembly
   build_scene_writer_context_from_models()
   ├─ Load SceneCardModel + ChapterContractModel
   ├─ Fetch CanonFacts for scene participants
   ├─ Fetch active PlotArcs, ArcBeats, Clues, Payoffs
   ├─ Fetch EmotionTrack, AntagonistPlan
   ├─ Hybrid retrieval (vector + lexical + structural)
   └─ Budget context: Tier 1 (always) → Tier 2 → Tier 3

2. Draft Generation
   generate_scene_draft()  [writer role, temp=0.85, streaming]
   ├─ Inject: writing profile, scene contract, context packet,
   │          world rules, prompt pack fragments
   ├─ Validate: no meta-leak, strip scaffolding echo, word count
   └─ Persist SceneDraftVersionModel (is_current=True)

3. Knowledge Propagation
   propagate_scene_discoveries()
   ├─ Extract Canon Facts via critic role
   ├─ Build Timeline Events
   ├─ Create CharacterStateSnapshots per participant
   └─ Index retrieval chunks (pgvector embeddings)

4. Review & Scoring
   review_scene_draft()  [critic role, temp=0.25]
   ├─ Score 5 dimensions (0–1 each)
   └─ If any dim < threshold → create RewriteTaskModel

5. Optional Rewrite
   rewrite_scene_from_task()  [editor role, temp=0.40]
   ├─ Rewrite task wrapped in === reference only === fence
   ├─ Max 2 revisions; accept best on stall
   └─ Re-score after each rewrite
```

### Chapter Pipeline
```
For each scene → run_scene_pipeline()
assemble_chapter_draft()           ← merge scene drafts
review_chapter_draft()             ← critic scores 4 chapter dimensions
extract_chapter_state_snapshot()   ← freeze hard facts for next chapter
checkpoint_commit()                ← atomic transaction boundary
```

### Project Pipeline
```
generate_foundation_plan()
  └─ generate_novel_plan() → chapter contracts + scene cards
       └─ For each chapter → run_chapter_pipeline()
            └─ Every 20 chapters → review_project_consistency()
                 └─ run_project_repair() if failures detected
                      └─ Export (Markdown / DOCX / EPUB)
                           └─ Publishing schedule activation
```

---

## 5. Knowledge & Continuity System

Every scene writes the following persistent artifacts that future scenes read:

| Artifact | Key Fields | Purpose |
|----------|-----------|---------|
| **Canon Facts** (`CanonFactModel`) | `subject`, `predicate`, `value_json`, `valid_from_chapter_no` | Monotonic ground truth — never contradicted |
| **Timeline Events** (`TimelineEventModel`) | `event_type`, `story_time_label`, `consequences` | Story chronology |
| **Character State Snapshots** (`CharacterStateSnapshotModel`) | `arc_state`, `emotional_state`, `physical_state`, `power_tier`, `trust_map`, `beliefs`, `knowledge` | Per-chapter character state |
| **Chapter State Snapshots** (`ChapterStateSnapshotModel`) | `facts` list of `HardFactContext` | Frozen end-of-chapter facts for next chapter's writer |

### Hybrid RAG Retrieval

**Weights: 60% vector + 20% lexical + 20% structural**

- Vector: pgvector HNSW index, 1024-dim embeddings (BAAI/bge-m3)
- Lexical: tokenized overlap — Latin trigrams, CJK bigrams
- Structural: source-type weighting (`scene_context > scene_draft > chapter_draft > character > canon_fact`)

Settings: `top_k=12`, `min_score=0.55`, `chunk_size=800`, `chunk_overlap=120`, `candidate_limit=40`

### Context Budget

| Tier | Always included? | Contents |
|------|----------------|---------|
| 1 | Yes | Scene/chapter contracts, writing methodology, participant Canon Facts |
| 2 | If token budget allows | Recent scene summaries, emotion tracks, antagonist plans |
| 3 | Lowest priority | Full story bible, plot arcs, retrieval results |

Token budget: `context_budget_tokens = 8000`. Rolling window: last 6 scenes.

---

## 6. Quality Gates

### Scene Review — 5 core dimensions
| Dimension | Default Threshold |
|-----------|-----------------|
| `hook_strength` | ≥ 0.70 |
| `conflict_clarity` | ≥ 0.70 |
| `emotional_movement` | ≥ 0.70 |
| `payoff_density` | ≥ 0.70 |
| `voice_consistency` | ≥ 0.70 |

Full review has 31 dimensions including `show_dont_tell`, `pov_consistency`, `methodology_compliance`, `thematic_resonance`, `worldbuilding_integration`, etc.

**Rewrite logic**: Any dimension below `scene_min_score` (default 0.70) → rewrite. Max 2 rewrites per scene. Minimum improvement delta required: 3%. If score plateaus → accept best draft (`accept_on_stall=True`).

### Chapter Review — 4 core dimensions
| Dimension | Default Threshold |
|-----------|-----------------|
| `main_plot_progression` | ≥ 0.75 |
| `subplot_progression` | ≥ 0.75 |
| `ending_hook_effectiveness` | ≥ 0.75 |
| `volume_mission_alignment` | ≥ 0.75 |

### Project Consistency Audit
Runs every 20 chapters. Checks: character arc trajectory, Canon Fact monotonicity, clue→payoff ratios, knowledge state integrity (no character knows future-revealed info), relationship evolution, lore consistency, POV voice drift.

---

## 7. Database Schema Overview

**50+ tables in PostgreSQL 16.** UUID primary keys, JSONB for flexible fields, Alembic migrations.

| Group | Key Tables |
|-------|-----------|
| **Project Structure** | `ProjectModel` (with `lock_version` for optimistic concurrency), `VolumeModel`, `ChapterModel`, `SceneCardModel`, `StyleGuideModel` |
| **Planning Artifacts** | `PlanningArtifactVersionModel` — versioned BookSpec / CastSpec / WorldSpec / VolumePlan / ChapterOutline |
| **World Building** | `WorldRuleModel`, `LocationModel`, `FactionModel`, `CharacterModel`, `RelationshipModel`, `VolumeFrontierModel`, `DeferredRevealModel`, `ExpansionGateModel` |
| **Narrative Structure** | `PlotArcModel`, `ArcBeatModel`, `ClueModel`, `PayoffModel`, `EmotionTrackModel`, `AntagonistPlanModel`, `ChapterContractModel`, `SceneContractModel` |
| **Content Drafts** | `SceneDraftVersionModel`, `ChapterDraftVersionModel`, `SceneSummaryModel` |
| **Knowledge Layer** | `CanonFactModel`, `TimelineEventModel`, `CharacterStateSnapshotModel`, `ChapterStateSnapshotModel` |
| **Retrieval Index** | `RetrievalChunkModel` (pgvector embedding column, metadata JSONB) |
| **Workflow & Audit** | `WorkflowRunModel`, `WorkflowStepRunModel`, `LlmRunModel`, `ReviewReportModel`, `QualityScoreModel`, `RewriteTaskModel`, `RewriteImpactModel` |
| **Publishing** | `PublishingPlatformModel`, `PublishingScheduleModel`, `PublishingHistoryModel` |

**Schema conventions:**
- UUID primary keys via `UUIDPrimaryKeyMixin`
- `JSONB` for config, state, and metadata fields
- `lock_version` on `ProjectModel` for optimistic concurrency control
- `ON DELETE CASCADE` FK cascades for orphan cleanup
- Alembic migrations in `migrations/` (15+ versions, supports online upgrades)

---

## 8. Prompt Pack System

**24+ genre-specific YAML packs** in `config/prompt_packs/`. The system infers genre from title + metadata keywords via `resolve_prompt_pack()`, then loads the matching pack.

**Each pack contains:**
- `key`, `name`, `genres`, `tags` — identity and matching
- `anti_patterns` — common pitfalls to instruct the LLM to avoid
- `writing_profile_overrides` — per-genre POV, dialogue ratio, tense, tone
- `fragments` — 25+ named prompt text blocks injected into different LLM calls
- `obligatory_scenes` — required scenes with timing (act_1, act_2_midpoint, act_3) and detection keywords

**25+ fragment names:** `global_rules`, `planner_book_spec`, `planner_world_spec`, `planner_cast_spec`, `planner_volume_plan`, `planner_outline`, `scene_writer`, `scene_review`, `scene_rewrite`, `chapter_review`, `chapter_rewrite`, `structure_guidance`, `emotion_engineering`, `conflict_stakes`, `hook_design`, `core_loop`, `dialogue_rules`, `visual_writing`, `opening_rules`, `climax_design`, `pacing_guidance`, `character_design`, `reversal_design`, `reaction_amplification`.

---

## 9. Character System

**`CharacterModel`** — persistent sheet: `name`, `role`, `background`, `goal`, `fear`, `flaw`, `arc_trajectory`, `knowledge_state_json` (knows / falsely_believes / unaware_of), `voice_profile_json` (speech register, verbal tics, sentence style, emotional expression), `moral_framework_json`, `power_tier`, `is_pov_character`.

**`CharacterStateSnapshotModel`** — per-chapter snapshot:
- `arc_state`, `emotional_state`, `physical_state`, `power_tier`
- `trust_map` — dict of `character → relationship strength (−1 to 1)`
- `beliefs` — currently held beliefs
- `knowledge` — what this character knows at this point

The snapshot from chapter N is injected into chapter N+1's writer context, preventing cross-chapter character drift. The `voice_profile_json` is used by the critic to score `voice_consistency`.

---

## 10. Key File Map

```
src/bestseller/
├── api/
│   ├── app.py                     FastAPI application setup
│   └── routers/
│       ├── projects.py            Project CRUD  (GET/POST /api/v1/projects)
│       ├── pipelines.py           Pipeline triggers (POST /autowrite, /pipeline)
│       ├── tasks.py               ARQ task status (GET /tasks/{id})
│       ├── content.py             Artifact retrieval
│       ├── exports.py             Markdown/DOCX export
│       └── publishing.py          Platform + schedule management
│
├── services/
│   ├── pipelines.py               Master orchestration  [3000 lines]
│   ├── drafts.py                  Scene/chapter draft generation  [2900 lines]
│   ├── reviews.py                 Multi-dimensional scoring  [2000 lines]
│   ├── planner.py                 Planning (foundation/novel/volume)  [2000 lines]
│   ├── knowledge.py               Canon Facts, Timeline, Snapshots  [1000 lines]
│   ├── context.py                 Scene writer context / RAG  [2000 lines]
│   ├── retrieval.py               Hybrid search engine  [1500 lines]
│   ├── llm.py                     LLM gateway + circuit breaker  [1000 lines]
│   ├── prompt_packs.py            Genre-aware prompt fragment loader
│   ├── continuity.py              Fact monotonicity, chapter snapshots
│   ├── consistency.py             Project-level consistency audit
│   ├── narrative_tree.py          Hierarchical narrative index  [1000 lines]
│   ├── character_evolution.py     Character arc tracking
│   ├── voice_drift.py             POV voice consistency
│   ├── story_bible.py             Story bible management
│   ├── world_expansion.py         Volume frontier / deferred reveals
│   ├── deduplication.py           Cross-scene repetition detection
│   ├── rewrite_cascade.py         Cascading rewrites on scene change
│   ├── repair.py                  Auto-repair failed stages
│   └── publishing/
│       ├── base.py                Abstract PublishingAdapter
│       ├── registry.py            Adapter factory
│       └── adapters/              amazon_kdp.py, qidian.py, fanqie.py…
│
├── infra/db/
│   └── models.py                  All SQLAlchemy models  [1700 lines, 50+ tables]
│
├── scheduler/
│   ├── main.py                    APScheduler + Redis pubsub hot-reload
│   └── jobs.py                    publish_next_chapter() cron job
│
├── worker/
│   ├── main.py                    ARQ WorkerSettings (max_jobs=4, timeout=86400s)
│   └── tasks.py                   run_autowrite_task, run_chapter_pipeline_task…
│
├── web/server.py                  Embedded Web Studio UI server (port 8787)
├── settings.py                    Pydantic Settings schema + config loader
└── domain/
    ├── context.py                 SceneWriterContextPacket and sub-models
    ├── narrative.py               PlotArc, ArcBeat, Clue, Contract models
    └── review.py                  SceneReviewScores, ChapterReviewScores models

config/
├── default.yaml                   Base configuration (all tuneable values)
├── local.yaml                     Local overrides (gitignored)
├── prompt_packs/                  24+ genre YAML files
└── writing_methodology.yaml       Methodology rules injected into writer prompts

migrations/                        Alembic migration scripts (15+ versions)
docker-compose.yml                 7 services: DB, Redis, API, Worker, Scheduler, Web UI, MCP
```

---

## 11. Development Conventions

**Data patterns:**
- Never mutate existing objects in-place. Return new instances (immutable patterns).
- Use `JSONB` for config/state fields that may evolve; add typed columns only for indexed or frequently queried fields.

**LLM calls:**
- Always use `complete_text(request: LLMCompletionRequest)` — never instantiate LiteLLM or call provider APIs directly.
- Always provide a `fallback_response` so callers handle mock/circuit-open scenarios gracefully.
- Always set `project_id` and `workflow_run_id` on requests for full audit traceability.

**Database:**
- Use `checkpoint_commit()` (or `session.commit()`) after each scene to prevent long transactions and PostgreSQL snapshot bloat.
- New tables require a corresponding Alembic migration in `migrations/`.
- Use `session.refresh(obj)` after commits when you need updated DB-generated fields.

**Progress & SSE:**
- Report runtime progress via `RedisProgressReporter.report()` — never print/log for client-facing status.
- SSE endpoint: `GET /progress/{workflow_run_id}` streams from Redis pubsub channel `bestseller:workflow:{id}`.

**Quality gates:**
- New generation stages should emit `ReviewReportModel` and `QualityScoreModel` entries.
- Rewrite prompts must wrap strategy/instructions in `=== reference only ===` fences to prevent the LLM from echoing meta-language into output prose.

**Configuration:**
- New tuneable parameters belong in `settings.py` as Pydantic fields with documented defaults.
- Override at runtime via `BESTSELLER__<SECTION>__<KEY>` environment variables.
- Docker Compose passes secrets as env vars; never hardcode credentials in source.

---

## 12. Infrastructure Details

**Docker Compose services:**

| Service | Port | Memory Limit | Notes |
|---------|------|-------------|-------|
| PostgreSQL + pgvector | 5432 | 768 MB | `shared_buffers=128MB`, `work_mem=16MB`, `max_connections=100` |
| Redis | 6379 | 128 MB | `maxmemory=96mb`, `allkeys-lru` eviction, RDB persistence |
| FastAPI | 8000 | 512 MB | 1 Uvicorn worker, health check `/health` |
| ARQ Worker | — | 768 MB × N | Default 2 replicas, max 4 concurrent jobs, 24 h timeout |
| APScheduler | — | 256 MB | Hot-reload via Redis pubsub `bestseller:schedule:events` |
| Web Studio | 8787 | 2560 MB | `WEB_MAX_CONCURRENT_TASKS=5`, ~300–500 MB peak per autowrite |
| MCP Server | 3000 | 256 MB | OpenClaw MCP integration |

**Key configuration values (`config/default.yaml`):**

| Category | Key | Default | Notes |
|----------|-----|---------|-------|
| Output | `target_total_words` | 150 000 | Target novel length |
| Output | `target_chapters` | 30 | Number of chapters |
| Output | `words_per_chapter.target` | 6 400 | Words per chapter |
| Output | `scenes_per_chapter.target` | 4 | Scenes per chapter |
| Quality | `scene_min_score` | 0.70 | Scene acceptance threshold |
| Quality | `chapter_coherence_min_score` | 0.75 | Chapter acceptance threshold |
| Quality | `max_scene_revisions` | 2 | Max rewrites per scene |
| Pipeline | `consistency_check_interval` | 20 | Chapters between audits |
| Retrieval | `top_k` | 12 | Chunks per retrieval query |
| Retrieval | `min_score` | 0.55 | Minimum relevance score |

---

## 13. How to Use This Document

### Claude Code
Already available as a project skill at `.claude/skills/bestseller-framework.md`. Auto-loaded in this repo; also invokable via `/bestseller-framework`.

### Cursor
Already available as a project rule at `.cursor/rules/bestseller-framework.mdc` (with `alwaysApply: true`). Cursor loads it automatically for every chat in this project. No setup needed — just open the repo in Cursor.

### ChatGPT (Custom GPT)
1. Open ChatGPT → Explore GPTs → Create → Configure
2. In **Instructions**, paste the full contents of this file
3. Name the GPT "BestSeller Dev Assistant"
4. Save and use for all BestSeller development questions

### Other AI tools
Include this file as a system prompt or context attachment. The document is self-contained and requires no codebase access to be useful.

---

## 14. Two Operating Modes

### Mode A — Development Assistance (default)
User asks about code, architecture, debugging, features — use sections 1–12 as reference. Call `complete_text()` not LiteLLM, commit checkpoints per scene, emit `ReviewReportModel` + `QualityScoreModel`.

### Mode B — Direct Novel Authoring
User asks you to **generate actual novel content** (e.g. "write me a xianxia novel with 100 chapters"). Act as the pipeline yourself: apply role separation (planner → writer → critic → editor), the planning hierarchy (section 15), quality gates, and write everything to `output/ai-generated/{novel-slug}/`. **Do not** call the running FastAPI backend or touch the database.

---

## 15. Planning Hierarchy (Mode B core)

### 15.1 Hierarchy from target chapters

| Target Chapters | Volumes | Acts | Notes |
|---|---|---|---|
| 1–50 | 1 | 1 | Single arc |
| 51–120 | 3–4 | 3 | Three-act |
| 121–300 | 5–6 | 4 | Four-act |
| 301–800 | 8–16 | 5 | Five-act epic |
| 801–1500 | 16–30 | 5–6 | Epic saga |
| 1500–2000+ | 30–40 | 6 | Multi-generational |

Volumes target ~50 chapters. `arc_batch_size = 12`. ActPlan required when `target_chapters > 50`.

### 15.2 Word budget (HARD CONSTRAINTS)

| Field | Min | Target | Max |
|-------|-----|--------|-----|
| **Words / chapter** | **5 000** | 6 400 | 9 000 |
| Words / scene | 1 200 | 1 600 | 2 200 |
| Scenes / chapter | 2 | 3–4 | 5 |

Draft < 5 000 words → **force rewrite**, expand via scenes/interiority/dialogue (no filler).

Scene count rules: post-climax aftermath = 2, climax/reversal = 4, default = 3.

### 15.3 Six-phase conflict evolution

| Phase | Code | Protagonist faces |
|-------|------|-------------------|
| 1 | `survival` | Basic threat, scraping by |
| 2 | `political_intrigue` | Hidden agendas, power games |
| 3 | `betrayal` | Trust shatters, allies turn |
| 4 | `faction_war` | Large-scale group conflict |
| 5 | `existential_threat` | World-ending stakes |
| 6 | `internal_reckoning` | Internal transformation |

### 15.4 Volume win/loss rhythm

Volume 1 → **win** (hook victory). Middle 40–70 % → mostly losses (crisis zone). Penultimate (N-1) → **major loss**. Final (N) → **win**. Elsewhere → alternate (even win, odd lose).

### 15.5 Chapter phase progression (within volume, position `p`)

| Position | Phase |
|----------|-------|
| 0–13 % | `hook` |
| 13–33 % | `setup` |
| 33–53 % | `escalation` |
| 53–73 % | `twist` |
| 73–87 % | `climax` |
| 87–100 % | `resolution_hook` |

---

## 16. Planning Workflow (Mode B — always before writing)

1. **Premise → BookSpec**: `protagonist{name,archetype,external_goal,internal_need,flaw,strength,fear}`, `logline`, `themes[]`, `reader_promise`, `stakes{personal,world}`, `three_act_structure`.

2. **WorldSpec**: `world_name`, `world_premise`, `rules[]{name,description,story_consequence,exploitation_potential}`, `power_system{tiers[],hard_limits,protagonist_starting_tier}`, `locations[]`, `factions[]`, `forbidden_zones`.

3. **CastSpec**: protagonist, antagonist, `antagonist_forces[]{name,force_type,active_volumes[],escalation_path}`, `supporting_cast[]` each with `voice_profile`, `knowledge_state{knows,falsely_believes,unaware_of}`, `moral_framework`, `arc_trajectory`, `conflict_map[]`.

4. **VolumePlan**: for each volume `{volume_number, title, volume_theme, chapter_count_target, word_count_target, opening_state, volume_goal, volume_obstacle, volume_climax, volume_resolution{goal_achieved,cost_paid,new_threat_introduced}, key_reveals[], foreshadowing_planted[], foreshadowing_paid_off[], reader_hook_to_next, conflict_phase, primary_force_name}`.

5. **ActPlan** (if > 50 chapters): `{act_number, title, chapter_range, purpose, protagonist_arc_stage, world_state_at_start, world_state_at_end, key_scenes[]}`.

6. **ChapterOutline** (per volume, just-in-time): `{chapter_number, volume_number, chapter_goal, chapter_title, chapter_phase, conflict_phase, conflict_summary, scene_count, scenes[]{scene_type∈action|investigation|relationship|worldbuilding|comic_relief, hook_type∈information_gap|deadline|mystery|desire|threat, spotlight_character, summary, entry_state, exit_state, estimated_words, conflict_stakes}, estimated_chapter_words≥5000, pacing_mode∈build|accelerate|climax|breathe, emotion_phase∈compress|release, is_climax}`.

7. **World Expansion** (if > 3 volumes): `VolumeFrontier` per volume, `DeferredReveal[]` (plant 2+ volumes before payoff), `ExpansionGate[]`. Progressive visibility: volume V reveals `min(100%, V/total_volumes)` of the world.

8. **Only now — generate chapter prose.**

---

## 17. Scaling Recipes

| Novel size | Volumes × chapters | Acts | Phases | Cast |
|-----------|-------------------|------|--------|------|
| 30 ch | 1 × 30 | 1 | 1 | 2–3 support, 1 force |
| 100 ch | 4 × 25 | 3 | 3–4 | 4–6 support, 2 forces |
| 500 ch | 10 × 50 | 5 | All 6 | 8–12 support, 3–5 forces |
| 1000 ch | 20 × 50 | 6 | All 6 | 12–20 support, 5–8 forces |
| 2000 ch | 40 × 50 | 6 + nested | 6 × 2 cycles | Multi-generational |

**1000-chapter 6-act split**: Act 1 ch 1–170, Act 2A ch 171–330, Act 2B ch 331–670, Act 3A ch 671–830, Act 3B ch 831–950, Act 4 ch 951–1000.

**For > 300 chapters**: character snapshots every 5 chapters. Rolling-summary compression every 25 chapters. Consistency audit every 20 chapters.

**For > 1500 chapters**: generational time-skips allowed at volume boundaries; prior facts remain canon.

---

## 18. Output Directory Specification (Mode B)

**Root:** `output/ai-generated/{novel-slug}/` (pinyin for Chinese titles)

```
output/ai-generated/{novel-slug}/
├── README.md                       Top-level overview
├── meta.yaml                       target_chapters, current_chapter, word_count, volumes, acts
├── story-bible/
│   ├── premise.md                  Logline, pitch, stakes
│   ├── world.md                    Rules, power system, locations, factions
│   ├── characters.md               Cast sheets (goal/fear/voice/arc)
│   ├── plot-arcs.md                Main + subplots + clue→payoff table
│   ├── volume-plan.md              All volumes (win/loss rhythm marked)
│   ├── act-plan.md                 Required if target_chapters > 50
│   ├── world-expansion.md          VolumeFrontier + DeferredReveal + ExpansionGate (> 3 vols)
│   └── writing-profile.md          POV, tense, tone, dialogue ratio, taboo words
├── volumes/
│   ├── vol-01-{volume-slug}/
│   │   ├── README.md               Volume overview + chapter index
│   │   └── ch-NNN-{chapter-slug}.md
│   └── vol-NN-{volume-slug}/
├── knowledge/
│   ├── canon-facts.md              Append-only monotonic ground truth
│   ├── timeline.md                 Story-time chronology
│   ├── rolling-summary.md          Compressed old scenes (every 25 ch for long novels)
│   └── character-snapshots/
│       └── after-ch-NNN.md         Every 10 ch (every 5 for > 300-ch novels)
├── reviews/
│   ├── scene-reviews.md
│   ├── chapter-reviews.md
│   └── consistency-audits.md       Every 20 chapters
└── exports/
    ├── full-novel.md
    └── full-novel.epub             If requested
```

Naming: `vol-{NN}` (2 digits), `ch-{NNN}` (3 digits), slugs lowercase-hyphenated pinyin.

### Chapter frontmatter (required)

```yaml
---
volume: 1
chapter: 3
title: "初入宗门"
slug: "chu-ru-zong-men"
scenes: 4
word_count: 6400                # MUST be ≥ 5000
status: approved
revision: 1
chapter_phase: setup            # hook|setup|escalation|twist|climax|resolution_hook
conflict_phase: survival
pacing_mode: build              # build|accelerate|climax|breathe
scores:
  hook_strength: 0.82
  conflict_clarity: 0.78
  emotional_movement: 0.80
  payoff_density: 0.75
  voice_consistency: 0.88
contract:
  main_plot_progress: "主角通过考核正式成为宗门弟子"
  subplot_progress: "与二师兄初次冲突埋线"
  emotion_shift: "紧张 → 如释重负 → 警惕"
  hook: "二师兄离开时意味深长的一句话"
generated_at: "2026-04-16T12:00:00Z"
---

# 第三章 初入宗门

## 场景一 · 山门前的队列
[1600 字正文]

## 场景二 · 气感测试
[1800 字正文]

## 场景三 · 宿舍初见
[1500 字正文]

## 场景四 · 师兄的目光
[1500 字正文]
```

### Canon Facts (append-only)

```markdown
# Canon Facts

## Chapter 1
- **{subject: 林风}** `has_cultivation_level` = "炼气三层" (valid_from_ch=1)
- **{subject: 天灵宗}** `location` = "北疆苍茫山脉" (valid_from_ch=1)

## Chapter 7
- **{subject: 林风}** `has_cultivation_level` = "炼气四层" (valid_from_ch=7, supersedes ch=1)
```

---

## 19. Mode B Workflow — Autonomous Orchestrator

Mode B is not a "write one step and wait" loop. It is driven by an **orchestrator** state machine that autonomously runs the full lifecycle from a single user instruction ("write me an N-chapter xxx novel") to a finished book, persisting state in `progress.yaml` so any session can resume from the last checkpoint.

### 19.1 State Machine

```
INIT
 → PLAN_PREMISE → PLAN_WORLD → PLAN_CHARACTERS → PLAN_VOLUME_PLAN
   (→ PLAN_ACT if target_chapters > 50)
   (→ PLAN_WORLD_EXPANSION if volumes > 3)
 → PLAN_WRITING_PROFILE
 → for each volume v:
     PLAN_VOL_README(v)
     → for each chapter c in v:
         WRITE_CHAPTER(c)            [role: writer]
         → REVIEW_CHAPTER(c)         [role: critic]
         → REWRITE_CHAPTER(c) × ≤ 2  [role: editor]
         → EXTRACT_KNOWLEDGE(c)      [role: summarizer]
         → COMMIT_CHAPTER(c)
         → MILESTONE_CHECK(c)
             snapshot  every 10 ch
             rolling   every 25 ch
             audit     every 20 ch
         → ADVANCE_CHAPTER
     → ADVANCE_VOLUME
 → EXPORT (exports/full-novel.md)
 → DONE (emit completion report)
```

### 19.2 Loop Controller (pseudocode)

```
loop:
    p = read progress.yaml
    if p.state == DONE:             report → END
    if p.human_decision_pending:    ask user → END
    if tool_budget_exhausted or context_near_limit:
        save progress.yaml
        report "Completed {c}/{T} chapters. Say 'continue' to resume." → END

    execute(p.state)                # per state contract below
    validate(output)                # on failure: retry; 3× fails → escalate
    advance_state()                 # atomic write to progress.yaml
    emit_progress_line()
```

### 19.3 State Contracts (summary)

| state | role | reads | produces | validates |
|-------|------|-------|----------|-----------|
| INIT | — | user instruction | directory skeleton + meta.yaml + progress.yaml | genre/title/target_chapters all present |
| PLAN_PREMISE | planner | meta.yaml | story-bible/premise.md | BookSpec fields complete |
| PLAN_WORLD | planner | premise | world.md | rules≥5; tiers aligned with protagonist arc |
| PLAN_CHARACTERS | planner | premise+world | characters.md | ≥1 antagonist; knowledge_state per char |
| PLAN_VOLUME_PLAN | planner | above 3 | volume-plan.md | W/L rhythm (open-win, penult-loss, final-win) |
| PLAN_ACT | planner | volume-plan | act-plan.md | only if >50 ch; ranges contiguous |
| PLAN_WORLD_EXPANSION | planner | volume-plan | world-expansion.md | only if >3 vols; ≥1 DeferredReveal |
| PLAN_WRITING_PROFILE | planner | all above | writing-profile.md | POV/tense/taboo complete |
| PLAN_VOL_README(v) | planner | volume-plan + prev vol exit | volumes/vol-NN/README.md | outlines count == vol chapters |
| WRITE_CHAPTER(c) | writer | profile + chars + outline + prev tail + recent 50 canon | ch-NNN.md | **≥ 5000 words**; frontmatter complete |
| REVIEW_CHAPTER(c) | critic | draft | scores filled + reviews/chapter-reviews.md | scene 5-dim ≥ 0.70; chapter 4-dim ≥ 0.75 |
| REWRITE_CHAPTER(c) | editor | RewriteTask (fenced `=== reference only ===`) | scope-only rewrite | didn't touch non-scope elements |
| EXTRACT_KNOWLEDGE(c) | summarizer | final draft + canon-facts | appended canon + timeline | no silent canon overwrite; use `supersedes` |
| COMMIT_CHAPTER(c) | — | — | vol README status + meta.yaml.current_chapter | atomic; rollback on failure |
| MILESTONE_CHECK(c) | summarizer/critic | — | conditional: snapshot/rolling/audit | audit passes or items pushed to repair_queue |
| ADVANCE_CHAPTER | — | — | progress.yaml update | — |
| ADVANCE_VOLUME | — | — | progress.yaml update | — |
| DRAIN_REPAIR_QUEUE | editor | repair_queue items | fixed chapters | no new canon conflicts |
| EXPORT | — | all ch files | exports/full-novel.md | chapters == target; words ≈ target ±10% |
| DONE | — | — | completion report | — |

### 19.4 `progress.yaml` — Single Source of Truth

Persisted at `output/ai-generated/{slug}/progress.yaml`. Rewritten to disk **after every state transition**. Key fields:

```yaml
state: WRITE_CHAPTER              # current node in the state machine
next_action: write_chapter
next_action_args: {volume: 1, chapter: 7}
current_chapter: 7
current_volume: 1
target_chapters: 30
target_volumes: 1
stages: {init: done, plan_premise: done, plan_world: done, ...}
chapters:
  "001": {state: done, rewrite_attempts: 0, word_count: 6184, final_scores: {...}}
  "007": {state: drafting}
repair_queue: []                  # audit-generated fix tasks
failures: []                      # unresolved errors
human_decision_pending: null      # when non-null, orchestrator MUST stop
milestones: {character_snapshots_written: [...], consistency_audits: [...]}
resource_usage: {tool_calls_this_session: 14, ...}
```

### 19.5 Stop Conditions (MUST halt + notify user)

1. `state == DONE`
2. `INIT` missing `genre` / `title` / `target_chapters`
3. `human_decision_pending != null` (with 2–3 options + tradeoffs + recommended)
4. Same state fails validation 3 × → escalate
5. Tool/context budget nearing limit → save + ask for "continue"
6. Disk write failure → rollback + escalate if 3×

### 19.6 Resume Protocol (user says "continue")

1. Do **not** re-initialise. Read `progress.yaml` from disk.
2. If `progress.yaml` corrupt/missing: reconstruct from `meta.yaml` + `volumes/vol-NN/README.md` status column + actual `ch-NNN-*.md` files on disk (trust disk).
3. Resume from `next_action`; skip re-planning.

### 19.7 Failure Escalation Policy

| trigger | handling |
|---------|----------|
| chapter word_count < 5000 after 2 expansions | editor adds scene; if still short → `human_decision_pending` |
| chapter scores < threshold after 2 rewrites | `accept_on_stall`, mark `approved_with_debt`, continue |
| consistency audit failure + 3 repair attempts | `human_decision_pending` |
| disk write failure | rollback chapter; escalate if 3× |

### 19.8 Atomic Chapter Commit (never half-state)

Each `COMMIT_CHAPTER` must update all six in one transaction — partial state is forbidden, and the orchestrator must never report "chapter done" unless all six applied:
1. `volumes/vol-NN/ch-NNN-*.md` (new file)
2. `volumes/vol-NN/README.md` (status column → approved)
3. `meta.yaml.current_chapter` (+ current_volume if crossing)
4. `knowledge/canon-facts.md` (append)
5. `knowledge/timeline.md` (append)
6. Conditional: snapshot @ 10 / rolling @ 25 / audit @ 20

### 19.9 Platform Autonomy Matrix

| Platform | Autonomy | How |
|----------|----------|-----|
| Claude Code | Fully autonomous | Orchestrator loops within a session; tool budget limit → auto-save + ask to continue |
| Cursor Agent Mode | Fully autonomous | Same; `.cursor/rules/bestseller-orchestrator.mdc` enables loop |
| Cursor Chat (non-agent) | Semi-autonomous | User says "continue"; LLM executes 1–3 steps, saves, stops |
| ChatGPT Custom GPT | Semi-autonomous | No native FS; either Code Interpreter maintains a zipped project, or user pastes files back |
| Gemini Gem / generic LLM | Semi-autonomous | Emits the expected file content + next-state notice; user forwards |

### 19.10 Progress Line Format (emitted per step)

```
▸ [plan]    volume-plan.md           ✓  (W·L·L·maj-L·W)
▸ [ch-001]  drafting...              ⋯  (~6200 words planned)
▸ [ch-001]  drafted                  ✓  (6184 words, 4 scenes)
▸ [ch-001]  reviewed                 ✓  (hook=0.82 conf=0.78 emo=0.81 pay=0.73 voice=0.88)
▸ [ch-001]  committed                ✓  (canon+3 timeline+1)
▸ Progress: 1/30 chapters (3 %) · vol 1/1 · words 6 184 / 180 000
```

Every 10 chapters emit milestone summary; every 20 chapters emit consistency audit result.

---

---

## 20. Invariants (NEVER break)

- ❌ **Never produce a chapter under 5 000 words.** Expand via scenes/interiority/dialogue — never pad with filler.
- ❌ Never write novel content outside `output/ai-generated/{slug}/`.
- ❌ Never modify existing Canon Facts — only append new entries with higher `valid_from_ch`.
- ❌ Never let a character know something revealed in a later chapter.
- ❌ Never exceed 2 rewrites on the same scene — accept best version.
- ❌ Never skip planning — for > 50 ch, ActPlan mandatory; for > 3 vols, DeferredReveal tracking mandatory.
- ❌ Never let the protagonist win every volume — follow the win/loss rhythm (15.4).
- ✅ Always keep `meta.yaml` and volume READMEs in sync with disk.
- ✅ Always write real scores in chapter frontmatter.
- ✅ Always plant foreshadowing 2+ volumes before its payoff for long novels.

---

*This document reflects the BestSeller system as of April 2026.*
