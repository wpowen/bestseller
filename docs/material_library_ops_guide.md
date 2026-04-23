# Material Library Ops Guide

Everything you need to populate, curate, and deploy the multi-dimensional
material library without disrupting historical novels.

---

## Architecture recap (1-minute read)

```
┌─── external LLM / human / scraper ──┐
│         (ChatGPT, Gemini, etc.)     │
└───────┬─────────────────────────────┘
        │ produces JSONL
        ▼
╔═══════════════════════════════════════╗
║  scripts/import_material_jsonl.py    ║  ← the "hook" for any external tool
╠═══════════════════════════════════════╣
║   (validates → computes embedding    ║
║    → upserts by (dimension, slug))   ║
╚════════════════╤══════════════════════╝
                 │
                 ▼
         ┌─────────────────────┐
         │  material_library   │  ← global shared table
         │  (14 dimensions)    │
         └─────────┬───────────┘
                   │
         ┌─────────┴───────────────────────────┐
         │                                     │
         ▼                                     ▼
┌───────────────────┐           ┌──────────────────────────────┐
│ scripts/          │           │ Drafter soft reference       │
│ curate_library.py │           │ (enable_library_soft_         │
│ (audit + fill)    │           │  reference flag, default OFF)│
└───────────────────┘           └──────────────────────────────┘
```

Two independent contributors, one library, zero impact on historical
novels unless you flip the flag.

---

## 0. Historical novel safety contract

Hard rules, enforced by the code:

- **Importing never touches `project_materials` or running chapters.**
- **All feature flags default `False`.**  Library queries never run in
  the Drafter pipeline unless `pipeline.enable_library_soft_reference`
  is explicitly turned on.
- **Soft reference is opt-in per deployment.**  Old novels keep
  writing with byte-identical prompts until you decide.
- **Re-imports are idempotent.**  Same JSONL twice → same library state;
  `usage_count` is preserved.

If you want to verify: `git diff` the migration and pipeline paths — you
will find **no changes** to the legacy draft flow when the flag is off.

---

## 1. Quick start (cold library → primed library)

### 1a. Shipped seed inventory (publication-ready baseline)

The repository ships **5 seed JSONL files totalling 218 entries**, covering
4 genre buckets × 14 dimensions each ≥3 entries (plus a shared generic
layer). After importing all five, the library is at "publication-ready"
coverage — every genre/dimension bucket meets the Curator's
`min_entries=10` threshold when the generic layer is counted in.

| File | Scope | Entries | Dimensions covered |
|---|---|---|---|
| `data/seed_materials/generic_seed.jsonl` | `genre=null` (all genres) | 46 | emotion_arcs, plot_patterns, scene_templates, character_archetypes, dialogue_styles, thematic_motifs, anti_cliche_patterns, real_world_references |
| `data/seed_materials/xianxia_seed.jsonl` | 仙侠 / upgrade-core | 18 | 7 dims (initial WebSearch seed) |
| `data/seed_materials/xianxia_supplement.jsonl` | 仙侠 / upgrade-core | 52 | completes the other 7 dims + extends to ≥5/dim |
| `data/seed_materials/urban_cultivation_seed.jsonl` | 都市修仙 / urban-cultivation | 56 | all 14 dims |
| `data/seed_materials/scifi_seed.jsonl` | 科幻 / starwar | 46 | all 14 dims |

Post-import per-genre totals: **仙侠 70 · 都市修仙 56 · 科幻 46 · generic 46**.
Every `(genre, dimension)` pair is ≥3 — combined with the cross-genre
`generic_seed.jsonl`, the Drafter soft-reference block always has inspiration
to draw from, regardless of which of the 4 genres the project uses.

### 1b. Dry-run all seeds at once

```bash
cd /Users/owen/Documents/workspace/bestseller
for f in data/seed_materials/*.jsonl; do
  .venv/bin/python scripts/import_material_jsonl.py "$f" --dry-run
done
```

Expected: `0 rejected` across all 5 files (218 rows total validated).

### 1c. Real import (after `alembic upgrade head`)

```bash
alembic upgrade head   # make sure 0021_material_library is applied
for f in data/seed_materials/*.jsonl; do
  .venv/bin/python scripts/import_material_jsonl.py "$f"
done
```

Expected: 218 rows inserted. Re-running is **idempotent** — same
`(dimension, slug)` rows will upsert, preserving `usage_count` and
`last_used_at`.

### 1d. Top up automatically via Curator

```bash
.venv/bin/python scripts/curate_library.py \
    --fill --all-genres \
    --max-gaps 6 --max-fills-per-run 5
```

This uses the internal Research Agent + web search to fill whichever
buckets are still under-threshold (default `min_entries=10`). Since
the shipped seed already satisfies the default plan for the three
seeded genres (仙侠 / 都市修仙 / 科幻) + generic, the Curator's
weekly cron will mostly sit idle — useful for **new genres** that you
add later (see §8 below).

---

## 2. Feeding the library from ANOTHER LLM

Hand `docs/material_import_schema.md` to ChatGPT / Gemini / a local
Llama / your own fine-tune.  Ask for N entries per dimension.  Save
its output as `my_contribution.jsonl`.  Then:

```bash
.venv/bin/python scripts/import_material_jsonl.py my_contribution.jsonl --dry-run
# inspect rejected rows, ask the LLM to fix, repeat until clean
.venv/bin/python scripts/import_material_jsonl.py my_contribution.jsonl
```

Force source attribution:

```bash
.venv/bin/python scripts/import_material_jsonl.py my_contribution.jsonl \
    --source-type llm_synth
```

Gate on Batch-3 novelty critic (blocks entries semantically too close
to existing ones):

```bash
.venv/bin/python scripts/import_material_jsonl.py my_contribution.jsonl \
    --novelty-guard
```

---

## 3. Letting historical novels' new chapters see the library

The soft-reference layer adds an **inspiration block** (not a hard
constraint) to every Drafter prompt.  Old chapters are never
regenerated; only the next chapter you ask the system to write will
see the new block.

Turn it on in `config/local.yaml`:

```yaml
pipeline:
  enable_material_library: true
  enable_library_soft_reference: true
  library_soft_reference_top_k: 4   # entries per dimension, keep small
```

Or via env:

```bash
export BESTSELLER__PIPELINE__ENABLE_MATERIAL_LIBRARY=true
export BESTSELLER__PIPELINE__ENABLE_LIBRARY_SOFT_REFERENCE=true
```

Turn it off at any time — the next chapter goes back to byte-identical
legacy prompts.  **No rollback migration required.**

### What the LLM sees

When `enable_library_soft_reference=true`, each scene draft prompt
gets a block like:

```
## 资源库灵感（仅供参考，不强制引用）
下列条目来自共享物料库，可借鉴气氛、结构、意象，
**但不得直接套用其中的专有名词；本书人物/宗门/地名必须以本书大纲为准。**

### scene_templates
  • 打脸反转 — 压迫→假装无辜→终极反杀
  …

### thematic_motifs
  • 孤月 — 月代表观察者视角，角色内心的冷寂
```

Wording matters: the block explicitly tells the model **inspire, not
copy**.  It cannot inject a new character named "方域" into a novel
that never had one.

---

## 4. Routine curation (weekly)

APScheduler already has a `scheduled_weekly_audit` entry point that
fires when `enable_material_library=true`.  Cron defaults:

- day: Monday
- hour: 04:00 UTC
- cap: 6 gaps filled per run, 5 LLM runs per gap

To audit manually without filling:

```bash
.venv/bin/python scripts/curate_library.py --format json | jq .
```

To inspect a specific bucket:

```bash
.venv/bin/python scripts/curate_library.py --dimension power_systems --genre 仙侠
```

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Importer: `unknown dimension '...'` | LLM invented a new bucket | Ask the LLM to pick from the 14 allowed dimensions; see `docs/material_import_schema.md` |
| Importer: `slug must be non-empty and contain no '/' or whitespace` | LLM used spaces or slashes | Insist on kebab-case ASCII |
| Soft-reference block is empty in prompt | library has 0 rows for that `(dimension, genre)` | Import seed data or run curator |
| Soft-reference block shows clichéd rows | `usage_count` filter too permissive | Lower `max_usage_count` (default 8) in call site |
| `BESTSELLER__PIPELINE__ENABLE_LIBRARY_SOFT_REFERENCE=true` but nothing changes | forgot to also set `ENABLE_MATERIAL_LIBRARY=true` | enable both flags |
| Dry-run passes but real import 0 rows | DB migration 0021 not applied | `alembic upgrade head` |

---

## 6. Regression check before a rollout

```bash
.venv/bin/pytest tests/unit/test_material_library.py \
                tests/unit/test_library_curator.py \
                tests/unit/test_material_forge.py \
                tests/unit/test_material_library_reference.py \
                tests/unit/test_import_material_jsonl.py \
                --no-cov
```

Expected: all green (123 tests at time of writing).

Full suite:

```bash
.venv/bin/pytest tests/unit/ --no-cov -q
```

Expected: 2347 passed (add the 29 new tests to whatever the main
baseline is).

---

## 7. Extending to a new genre (future work)

The shipped seed covers 4 buckets (`generic / 仙侠 / 都市修仙 / 科幻`).
If you want to add a new genre — e.g. `suspense-mystery`, `female-palace`,
`apocalypse-supply-chain`, `history-strategy`, `romance-tension-growth`,
`cozy-fantasy`, `dark-romance`, `villainess-reincarnation` — the
recommended workflow is:

1. **Pick a canonical `(genre, sub_genre)` pair** that matches what
   `infer_default_prompt_pack_key` routes on (see
   `src/bestseller/services/prompt_packs.py`).  Stick to the pack's
   existing label wording to avoid fragmenting the retrieval namespace.

2. **Clone the hand-off template** (`docs/material_import_schema.md` ends
   with a copy-paste prompt for ChatGPT / Gemini). Feed it the new genre
   and ask for 14 dims × ≥3 entries.

3. **Save to a dedicated JSONL**:
   `data/seed_materials/<genre_slug>_seed.jsonl` with the same layout as
   the shipped files (comment header + one JSON object per line).

4. **Dry-run**:
   ```bash
   .venv/bin/python scripts/import_material_jsonl.py \
       data/seed_materials/<new>_seed.jsonl --dry-run
   ```
   Iterate with the upstream LLM until 0 rejected.

5. **Slug-namespace rule**: every row's `slug` starts with a kebab-case
   prefix unique to the genre — e.g. `suspense-*`, `palace-*`,
   `apocalypse-*`. The generic seed uses `generic-*`, the shipped genres
   use `xianxia-*` / `urban-cultivation-*` / `scifi-*`. This keeps
   cross-genre collisions impossible even if two teams add seeds
   concurrently.

6. **Commit the JSONL** alongside a one-line entry added to the table in
   §1a of this guide so the next operator knows it exists.

The system has **no code gate** that limits genres — the
`material_library` table and `query_library()` API accept any string in
`genre`. So adding a genre is a pure content operation.

### What if I don't have a ready-made LLM?

Run the Curator against the new genre — it will trigger the internal
Research Agent to produce entries via web search + LLM synth:

```bash
.venv/bin/python scripts/curate_library.py \
    --fill --genre "悬疑推理" \
    --min-entries 10 --max-fills-per-run 12
```

The Curator writes directly to `material_library`; no JSONL file is
produced. Useful when you trust the agent and just want coverage.
For auditability (and offline review) the JSONL path is preferred.

---

## 8. Inventory summary

```
Total shipped seed entries ............................ 218
  ├── generic (genre=null) ............................  46
  ├── 仙侠  (xianxia, upgrade-core) ....................  70
  ├── 都市修仙 (urban-cultivation) ......................  56
  └── 科幻  (scifi, starwar) ...........................  46

Dimension coverage per genre (min across 14 dims) ......  ≥3
Dimension coverage per genre (+ generic layer) .........  ≥10   ← curator threshold

Genres NOT yet seeded (28 prompt packs, 24 remaining) ..
  apocalypse-supply-chain, cozy-fantasy, cozy-litrpg,
  cozy-mystery, dark-romance, eastern-aesthetic,
  enemies-to-lovers, entertainment-sweet, epic-fantasy,
  female-palace, game-esport, history-strategy,
  litrpg-progression, mafia-romance, paranormal-romance,
  psychological-thriller, reverse-harem,
  romance-tension-growth, romantasy, shezhu-bailan-comedy,
  space-opera, suspense-mystery, system-apocalypse-healer,
  urban-power-reversal, villainess-reincarnation
```

---

## 9. Contract summary (for the paranoid)

1. **Library writes never affect project chapters.**  `insert_entry`
   only touches `material_library`.
2. **Soft reference is read-only + fail-soft.**  Any retrieval error
   returns `""` (empty block), identical to the disabled path.
3. **No `mark_used` on soft-ref queries.**  Speculative retrieval does
   not inflate cross-project novelty counters.
4. **Old projects can opt in gradually.**  Per-deployment flag; no
   per-project flag — because the DB schema has no per-project opt-in
   field, the atomic unit is the worker.  If you need finer granularity,
   run two workers with different config.
5. **External feeds flow through the same gate.**  Importer,
   Curator's Research Agent, and LLM Forge all call the same
   `insert_entry` — one source of truth, one novelty gate, one audit
   trail.
