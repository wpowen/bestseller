# Cursor Batch Execution Guide For Distillation

Use this guide after the pilot package shape is approved.

Goal: process thousands of books into anonymized distillation packages, run
external LLM chapter extraction, aggregate reviewed mechanisms, and promote
approved assets into BestSeller.

## Ground Rules

1. Never commit raw source text.
2. Never commit source titles, authors, original paths, or exact named entities.
3. Repository-safe output goes under `data/distillation/source-XXXX/`.
4. Raw chunks and LLM payloads go under `.distillation_private/source-XXXX/`.
5. Only `material_entries.review.jsonl` or aggregate `material_entries.review.jsonl`
   may be imported into `material_library`.
6. Grammar patches are installed only after review.
7. Same-title duplicates are skipped by default through the repo-safe
   `data/distillation/source_registry.index.json`; private title/debug details
   stay under `.distillation_private/`.

## Phase 1: Prepare Sources

For many files under one corpus root, use the parallel driver (process pool + registry
file lock inside ``prepare_source``):

```bash
python3 scripts/distillation/batch_prepare_corpus.py /path/to/corpus \\
  --genre-hint <题材> \\
  --workers 4 \\
  --dedupe-policy skip
```

Incremental state defaults to ``.distillation_private/corpus_prepare_state.jsonl``
(re-run skips finished fingerprints). Override with ``--state-file``.

Given a directory of raw books:

```text
/path/to/book_corpus/
```

Cursor should create deterministic source ids and run:

```bash
python3 scripts/distillation/prepare_source.py \
  /path/to/book_corpus/<file> \
  --source-id source-0002 \
  --dedupe-policy skip
```

Supported input formats:

- `.txt`, `.md`, `.markdown`
- `.html`, `.htm`, `.xhtml`
- `.epub`
- `.mobi`, `.azw3` when Calibre `ebook-convert` is installed

Supported text encodings include UTF-8, UTF-16, GB18030/GBK, Big5/CP950,
Shift-JIS, and EUC-JP. Unsupported files should be moved to an error queue and
reported, not force-parsed.

If the command returns `"skipped": true`, Cursor should not create LLM jobs for
that file. It should record the `duplicate_of` source id in the batch report and
continue with the next file.

Expected outputs per source:

```text
data/distillation/source-0002/source_manifest.json
data/distillation/source-0002/chapters.index.json
data/distillation/source-0002/llm_jobs/chapter_jobs.index.jsonl
.distillation_private/source-0002/chunks/*.txt
.distillation_private/source-0002/llm_payloads/*.prompt.json
```

Global repo-safe dedupe index:

```text
data/distillation/source_registry.index.json
```

Validate:

```bash
python3 scripts/distillation/validate_package.py data/distillation/source-0002
```

## Phase 2: External LLM Extraction

Cursor should read:

```text
data/distillation/source-0002/llm_jobs/chapter_jobs.index.jsonl
```

For each row:

1. Open `private_payload_ref`.
2. Send that JSON payload to the chosen external model.
3. Require JSON only, matching `data/distillation/schemas/chapter_card.schema.json`.
4. Append valid outputs to:

```text
data/distillation/source-0002/chapter_cards.jsonl
```

If the model returns invalid JSON, retry with the same payload plus the
validation error.

For unattended in-repo execution, use the full-auto driver. It processes source
directories in ascending `source-NNNN` order and can be resumed:

```bash
.venv/bin/python scripts/distillation/run_full_auto_distillation.py \
  --resume \
  --refresh-missing-craft-observations \
  --chapter-workers 4 \
  --import-mode none
```

`--refresh-missing-craft-observations` is only needed when backfilling the
anonymous author-craft layer for chapter cards created before this field existed.

For large libraries, split deterministic ranges:

```bash
.venv/bin/python scripts/distillation/run_full_auto_distillation.py \
  --resume \
  --source-start source-0241 \
  --source-end source-0480 \
  --refresh-missing-craft-observations \
  --chapter-workers 4 \
  --import-mode none
```

## Phase 3: Aggregation Per Source

After all chapter cards exist, Cursor should generate or call an aggregation
step to produce:

```text
data/distillation/source-0002/volume_cards.jsonl
data/distillation/source-0002/book_design_card.json
data/distillation/source-0002/author_craft_card.json
data/distillation/source-0002/mechanism_candidates.jsonl
data/distillation/source-0002/material_entries.review.jsonl
data/distillation/source-0002/anti_copy_ledger.json
data/distillation/source-0002/grammar_patch.yaml
```

For now, the pilot package `source-0001` is the contract example.

## Phase 4: Aggregate Many Sources By Genre / Mechanism

Example for one genre cluster:

```bash
python3 scripts/distillation/aggregate_packages.py \
  data/distillation/source-0001 \
  data/distillation/source-0002 \
  data/distillation/source-0003 \
  --aggregate-key otherworld-cross-system \
  --output-dir data/distillation/aggregates/otherworld-cross-system
```

Expected aggregate outputs:

```text
data/distillation/aggregates/otherworld-cross-system/aggregate_manifest.json
data/distillation/aggregates/otherworld-cross-system/material_entries.review.jsonl
data/distillation/aggregates/otherworld-cross-system/mechanism_registry.jsonl
data/distillation/aggregates/otherworld-cross-system/author_craft_registry.jsonl
data/distillation/aggregates/otherworld-cross-system/book_design_registry.jsonl
data/distillation/aggregates/otherworld-cross-system/volume_design_paths.jsonl
data/distillation/aggregates/otherworld-cross-system/anti_copy_rules.json
data/distillation/aggregates/otherworld-cross-system/grammar_patch.yaml
```

`aggregate_manifest.json` must include `maturity_score`, `maturity_status`,
`book_design_rows`, `volume_design_rows`, `author_craft_rows`, and
`fallback_volume_rows`. Fallback volume rows are counted but quarantined out of
`volume_design_paths.jsonl`.

## Phase 5: Promotion

Dry-run material import:

```bash
.venv/bin/python scripts/import_material_jsonl.py \
  data/distillation/aggregates/otherworld-cross-system/material_entries.review.jsonl \
  --dry-run --format json
```

Real import only after review:

```bash
.venv/bin/python scripts/import_material_jsonl.py \
  data/distillation/aggregates/otherworld-cross-system/material_entries.review.jsonl \
  --source-type user_curated
```

Install grammar patch after review:

```bash
python3 scripts/distillation/install_grammar_patch.py \
  data/distillation/aggregates/otherworld-cross-system/grammar_patch.yaml \
  --apply
```

## Phase 6: Verification

Run focused tests:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_distillation_assets.py \
  tests/unit/test_story_design_grammars.py \
  -q --no-cov
```

Check grammar resolution:

```bash
PYTHONPATH=src python3 - <<'PY'
from bestseller.services.story_design_grammars import load_story_design_grammar_registry, resolve_story_design_grammar
load_story_design_grammar_registry.cache_clear()
print(resolve_story_design_grammar(genre="异界穿越").key)
PY
```

Expected:

```text
otherworld-cross-system
```

## Cursor Prompt

Use this prompt for Cursor when moving to batch execution:

```text
You are executing the BestSeller formal distillation workflow.

Read:
- docs/distillation/formal-distillation-workflow.md
- docs/distillation/external-llm-distillation-protocol.md
- docs/distillation/cursor-batch-execution-guide.md
- data/distillation/source-0001/README.md

Process the provided corpus directory. For each source file:
1. Assign a source id source-NNNN.
2. Run scripts/distillation/prepare_source.py with --dedupe-policy skip.
3. If the command returns skipped=true, record duplicate_of and continue.
4. Validate with scripts/distillation/validate_package.py.
5. Use llm_jobs/chapter_jobs.index.jsonl to call the configured external model.
6. Write chapter_cards.jsonl.
7. Generate volume_cards.jsonl, book_design_card.json, mechanism_candidates.jsonl,
   material_entries.review.jsonl, anti_copy_ledger.json, and grammar_patch.yaml.
   Also generate author_craft_card.json as an anonymized craft profile: POV
   distance, sentence rhythm, paragraphing, dialogue method, exposition placement,
   emotional temperature, and hook transitions. Do not imitate a named author,
   quote distinctive phrases, or preserve copyable style fingerprints.
8. Run validate_package again.

Do not commit raw text, source names, author names, or original paths.
Do not promote to material_library unless explicitly approved.
After each batch of 50 sources, aggregate by genre/mechanism key and report:
- source count
- skipped duplicate count
- unsupported/unparsed file count
- chapter count
- invalid LLM outputs
- material candidate count
- mechanism count
- redaction violations
```
