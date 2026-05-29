# Methodology Book Distillation Architecture

## Goal

Convert user-supplied writing craft books into executable BestSeller methodology assets:

```text
EPUB/TXT/HTML source
  -> private normalized sections
  -> repo-safe section job manifest
  -> LLM methodology candidates
  -> MethodologyCard deck
  -> MethodologyProfile
  -> planner / writer / critic / gate usage
```

The system must learn transferable writing methods, not store book summaries or
source prose.  Raw text, titles, authors, and source file paths stay under
`.methodology_private/`.  Repository artifacts store hashes, redacted indexes,
and abstract rules only.

## MVP Components

1. `methodology_book_distillation.prepare_methodology_book`
   - Parses supported book formats through the existing source parser.
   - Writes raw normalized text and per-section payloads to the private root.
   - Writes repo-safe manifests under `data/methodology_books/source-NNNN/`.

2. `llm_jobs/section_jobs.index.jsonl`
   - One job per parsed section.
   - Each job points to a private prompt payload and an expected candidate schema.

3. `methodology_candidate.schema.json`
   - LLM output contract for transferable methods.
   - Requires category, scope, stage, core claim, framework bindings, and confidence.

4. `candidates_to_methodology_cards`
   - Promotes reviewed candidates into the existing `MethodologyCardDeck`.
   - Keeps low-confidence candidates out of runtime profiles.

5. `validate_methodology_book_package`
   - Checks required repo files.
   - Verifies repo artifacts do not leak user paths, source file extensions, or source-site markers.

6. `methodology_book_llm.run_pending_methodology_section_jobs_parallel`
   - Reads pending section jobs and private prompt payloads.
   - Calls the framework's configured `summarizer` LLM through `complete_text`.
   - Writes reviewable grouped rows to `methodology_candidates.review.jsonl`.
   - Records failed jobs under `.methodology_private/errors/` without committing source text.

## Xiaomi MiMo Profile

The runtime profile key is `xiaomi-mimo`. It uses the Token Plan China
OpenAI-compatible endpoint:

```text
model: openai/mimo-v2.5-pro
api_base: https://token-plan-cn.xiaomimimo.com/v1
api_key_env: XIAOMI_MIMO_API_KEY
api_key_header: api-key
```

Example section extraction:

```bash
export XIAOMI_MIMO_API_KEY='tp-...'
python scripts/methodology_books/run_section_llm_jobs.py \
  --package-dir data/methodology_books/source-0001 \
  --runtime-profile xiaomi-mimo \
  --max-concurrency 2 \
  --write-cards
```

The raw source remains in `.methodology_private/`. The review output you inspect
is `data/methodology_books/source-NNNN/methodology_candidates.review.jsonl`; the
optional promoted draft cards are written to
`data/methodology_books/source-NNNN/methodology_cards.review.yaml`.

## Runtime Integration

Promoted cards should be grouped into a profile such as
`writing_books_core_v1.yaml`.  Existing profile rendering can then inject them
by stage/scope:

- Planning: book premise, snowflake expansion, character design, outline readiness.
- Drafting: scene design, POV distance, dialogue pressure, prose style controls.
- Review: setup/payoff, show-don't-tell, scene causality, revision pass gates.
- Health: longform control, method coverage, recurring violation patterns.

## Validation Strategy

The first verification layer is deterministic:

- Unit tests create a small synthetic writing-methodology source.
- The preparer writes private text outside repo-safe artifacts.
- The validator proves no raw title, user path, or source extension leaks to repo JSON.
- A reviewed candidate is converted into a native `MethodologyCardDeck`.

The second layer, outside unit tests, runs the CLI against real user-supplied
EPUBs using a temporary repo root to prove format parsing and job creation.
