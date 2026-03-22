# World Expansion Boundaries Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Add a first-class “world expansion boundaries” layer so long-form novels can grow the world in stages without leaking future maps, factions, rules, or reveals into current chapters.

**Architecture:** Introduce four new project-level/volume-level entities: `WorldBackbone`, `VolumeFrontier`, `DeferredReveal`, and `ExpansionGate`. Materialize them from the existing `book_spec / world_spec / volume_plan`, expose them in story bible inspection and the narrative tree, and inject only currently visible frontier data into chapter/scene context packets.

**Tech Stack:** Python, Pydantic, SQLAlchemy async ORM, Alembic, PostgreSQL JSONB, existing BestSeller story-bible/narrative-tree/context services.

---

### Task 1: Add the data model layer

**Files:**
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/infra/db/models.py`
- Create: `/Users/owen/Documents/workspace/bestseller/migrations/versions/0007_world_expansion_boundaries.py`
- Test: `/Users/owen/Documents/workspace/bestseller/tests/unit/test_schema_services.py`

**Step 1: Write/extend tests to assert schema SQL includes the new tables**

Cover:
- `world_backbones`
- `volume_frontiers`
- `deferred_reveals`
- `expansion_gates`

**Step 2: Run the schema-targeted tests and confirm failure**

Run:
`pytest tests/unit/test_schema_services.py -q`

**Step 3: Add the minimal ORM models**

Required fields:
- `WorldBackboneModel`
  - `project_id`, `core_promise`, `main_line`, `hidden_line`, `emotional_line`, `theme_line`, `world_premise`, `antagonist_root`, `immutable_rules_json`, `expansion_principles_json`, `metadata_json`
- `VolumeFrontierModel`
  - `project_id`, `volume_id`, `volume_number`, `frontier_summary`, `entry_scope`, `visible_locations_json`, `visible_factions_json`, `visible_rule_codes_json`, `active_character_names_json`, `blocked_topics_json`, `next_frontier_hint`, `status`, `metadata_json`
- `DeferredRevealModel`
  - `project_id`, `reveal_code`, `title`, `reveal_type`, `summary`, `guardrail`, `earliest_volume_number`, `earliest_chapter_number`, `status`, `metadata_json`
- `ExpansionGateModel`
  - `project_id`, `gate_code`, `title`, `gate_type`, `summary`, `unlock_condition`, `target_scope`, `target_volume_number`, `target_chapter_number`, `status`, `metadata_json`

**Step 4: Add the Alembic migration**

Migration should create all four tables and their unique/index constraints.

**Step 5: Run the schema-targeted tests again**

Run:
`pytest tests/unit/test_schema_services.py -q`

---

### Task 2: Add read models and story-bible materialization

**Files:**
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/domain/story_bible.py`
- Create: `/Users/owen/Documents/workspace/bestseller/src/bestseller/services/world_expansion.py`
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/services/story_bible.py`
- Test: `/Users/owen/Documents/workspace/bestseller/tests/unit/test_story_bible_services.py`

**Step 1: Write tests for world expansion materialization**

Cover:
- book/world/volume data creates one backbone row
- each volume creates a frontier row
- volume reveals become deferred reveals
- volume handoff becomes expansion gates

**Step 2: Run the new tests and confirm failure**

Run:
`pytest tests/unit/test_story_bible_services.py -q`

**Step 3: Add Pydantic read models**

Add:
- `WorldBackboneRead`
- `VolumeFrontierRead`
- `DeferredRevealRead`
- `ExpansionGateRead`

Extend `StoryBibleOverview` to include these fields.

**Step 4: Implement `world_expansion.py`**

Functions:
- `upsert_world_backbone(...)`
- `upsert_volume_frontiers(...)`
- `upsert_deferred_reveals(...)`
- `upsert_expansion_gates(...)`
- `build_world_expansion_context(...)`

**Step 5: Call the new upserts during story bible materialization**

Wire into existing story-bible workflow after book/world/cast/volume upserts.

**Step 6: Extend story bible inspection**

`build_story_bible_overview(...)` should return the new boundary layer.

**Step 7: Re-run the tests**

Run:
`pytest tests/unit/test_story_bible_services.py -q`

---

### Task 3: Add visibility-aware narrative tree nodes

**Files:**
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/services/narrative_tree.py`
- Test: `/Users/owen/Documents/workspace/bestseller/tests/unit/test_narrative_tree_services.py`

**Step 1: Write tests for new tree paths**

Cover:
- `/world/backbone`
- `/world/frontiers`
- `/world/frontiers/volume-01`
- `/world/deferred-reveals`
- `/world/expansion-gates`

**Step 2: Run targeted tests and confirm failure**

Run:
`pytest tests/unit/test_narrative_tree_services.py -q`

**Step 3: Extend `rebuild_narrative_tree(...)`**

Add root nodes and child nodes for the four new entities.

**Step 4: Ensure deferred reveals are only searchable when visible**

Tree node metadata should preserve `earliest_volume_number` / `earliest_chapter_number`.

**Step 5: Re-run tests**

Run:
`pytest tests/unit/test_narrative_tree_services.py -q`

---

### Task 4: Inject boundary-aware context into scene/chapter writing

**Files:**
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/domain/context.py`
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/services/context.py`
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/services/drafts.py`
- Modify: `/Users/owen/Documents/workspace/bestseller/src/bestseller/services/reviews.py`
- Test: `/Users/owen/Documents/workspace/bestseller/tests/unit/test_context_services.py`

**Step 1: Write failing tests**

Cover:
- scene context contains `world_backbone`
- scene context contains only the current volume frontier
- future deferred reveals do not appear before their allowed position
- chapter context contains the same boundary layer

**Step 2: Run targeted tests and confirm failure**

Run:
`pytest tests/unit/test_context_services.py -q`

**Step 3: Extend context packet models**

Add:
- `world_backbone`
- `current_volume_frontier`
- `visible_deferred_reveals`
- `active_expansion_gates`

**Step 4: Wire `build_world_expansion_context(...)` into scene/chapter context**

Rules:
- backbone always available
- frontier chosen by current chapter volume
- deferred reveals filtered by current chapter/scene position
- gates filtered by target volume/chapter proximity

**Step 5: Add prompt guidance**

Writer/reviewer prompts should explicitly state:
- do not reveal beyond current frontier
- future reveals must stay hidden unless visible in context
- new places/rules/factions should respect expansion gates

**Step 6: Re-run targeted tests**

Run:
`pytest tests/unit/test_context_services.py -q`

---

### Task 5: Full regression verification

**Files:**
- Modify as needed from previous tasks
- Test: full unit suite

**Step 1: Run compile verification**

Run:
`.venv/bin/python -m compileall src tests`

**Step 2: Run the full unit suite**

Run:
`.venv/bin/python -m pytest tests/unit -q`

**Step 3: Run a smoke check on story-bible/narrative outputs if needed**

Suggested commands:
- `./scripts/run.sh workflow materialize-story-bible <project>`
- `./scripts/run.sh story-bible show <project>`
- `./scripts/run.sh workflow materialize-narrative-tree <project>`
- `./scripts/run.sh narrative tree-show <project>`

**Step 4: Update docs if behavior changed**

At minimum:
- `/Users/owen/Documents/workspace/bestseller/README.md`
- any architecture docs that mention context assembly or story bible boundaries
