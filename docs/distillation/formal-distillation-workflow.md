# Formal Novel Distillation Workflow

This workflow is for processing thousands of source books into reusable,
auditable story-design assets for BestSeller.

Hard rule: the code repository stores only anonymized, derived design assets.
Raw text, source filenames, book titles, author names, and external LLM
payloads that contain source text stay in `.distillation_private/` or a
database/object store with restricted access.

## 1. Storage Layout

### Private Staging

Local path, ignored by git:

```text
.distillation_private/
  source_title_hash.salt
  source_registry.private.json
  duplicate_sources.jsonl
  source-0001/
    raw/
      source.original.<format>
      source.normalized.txt
    chunks/
      chapter-0001.txt
      chapter-0002.txt
    llm_payloads/
      chapter-0001.prompt.json
      volume-001.prompt.json
```

This area may contain raw copyrighted text. It must never be committed.

### Repository Assets

Anonymized and reviewable:

```text
data/distillation/
  source_registry.index.json
  schemas/
    source_manifest.schema.json
    chapter_card.schema.json
    volume_card.schema.json
    book_design_card.schema.json
    anti_copy_ledger.schema.json
    llm_chapter_job.schema.json
  source-0001/
    source_manifest.json
    chapters.index.json
    llm_jobs/
      chapter_jobs.index.jsonl
    chapter_cards.sample.jsonl
    volume_cards.jsonl
    book_design_card.json
    mechanism_candidates.jsonl
    material_entries.sample.jsonl
    anti_copy_ledger.json
    grammar_patch.yaml
```

Repository files may mention anonymous source ids such as `source-0001`.
They must not mention the source book name, author, original filename, or
specific named entities from the source.

`source_registry.index.json` is repo-safe. It stores only salted HMAC title
keys, anonymous source ids, raw content hashes, and source formats. The HMAC
salt stays in `.distillation_private/`, so a committed title key cannot be
recomputed from a public book-name dictionary.

### Database Layer

For large-scale operation, store process state in DB tables. A first rollout
can use files only; DB promotion should use these logical tables:

| Table | Purpose |
|---|---|
| `distillation_sources` | anonymous source id, hash, title HMAC, format, import status, rights status |
| `distillation_chapters` | chapter index, private text pointer, char count, parse status |
| `distillation_llm_jobs` | model job payload pointer, retry count, output status |
| `distillation_chapter_cards` | normalized chapter-level design extraction |
| `distillation_volume_cards` | volume/arc-level aggregation |
| `distillation_book_cards` | single-book design fingerprint |
| `distillation_mechanism_candidates` | reusable mechanism candidates before review |
| `distillation_review_queue` | human approval state for promotion into `material_library` |

Only approved mechanism/material rows move into the existing
`material_library` table.

## 2. Pipeline Phases

### Phase A: Source Registration

Input: local `.txt`, `.md`, `.epub`, `.html`, `.mobi`, or `.azw3`.

Format rules:

- `.txt` / `.md`: decoded with UTF-8, UTF-16, GB18030/GBK, Big5/CP950,
  Shift-JIS, and EUC-JP attempts.
- `.epub`: parsed directly from the EPUB container, OPF metadata, and spine
  HTML/XHTML files.
- `.mobi` / `.azw3`: parsed through Calibre `ebook-convert`. If Calibre is not
  installed, preparation fails with an explicit error instead of producing
  low-quality binary text.
- Unsupported or unreadable files stay out of the distillation queue until
  converted to one of the supported formats.

Output:

- `source_manifest.json`
- private copy of normalized source text
- repo-safe source title HMAC registration

The manifest records:

- `source_id`
- raw SHA-256 hash
- source format
- encoding
- salted title HMAC for dedupe
- byte/char counts
- rights status
- whether source title/author are redacted
- parse policy

Same-title dedupe happens at registration. The normalized private title key
removes common edition noise such as "精校版", "全本", "完本", file extensions,
and bracketed annotations. By default, a second source with the same title key
is skipped and logged privately; `--dedupe-policy error` can be used for strict
batch gates, and `--dedupe-policy allow` is reserved for deliberate reprocessing.

### Phase B: Chapter Segmentation

Output:

- repo: `chapters.index.json`
- private: `chunks/chapter-xxxx.txt`

Repo index contains only anonymous metadata:

- absolute chapter number
- volume number / label
- detected boundary type
- title hash or redacted title features
- char count
- line count
- private chunk pointer

It does not store raw chapter text.

### Phase C: External LLM Chapter Extraction

For each chapter, generate one external LLM job.

Repo job index:

- `llm_jobs/chapter_jobs.index.jsonl`

Private payload:

- `.distillation_private/source-0001/llm_payloads/chapter-0001.prompt.json`

The private payload contains:

- system instruction
- schema
- chapter text
- neighboring chapter summaries if available

The LLM must output one `chapter_card` JSON object.

### Phase D: Chapter Card Normalization

Output:

- `chapter_cards.jsonl` in DB or private processing area
- optionally `chapter_cards.sample.jsonl` in repo for audited examples

Each chapter card must answer:

- what function this chapter serves
- what state changed
- what reader reward was paid
- what hook/debt remains
- what reusable mechanism was observed
- what source-specific content must not be reused

### Phase E: Volume Aggregation

Every 15-30 chapters, aggregate chapter cards into volume cards.

Output:

- `volume_cards.jsonl`

Each card captures:

- arc function
- dominant engine
- state progression curve
- turning point chapters
- setup/payoff rhythm
- failure modes

### Phase F: Book Design Card

Aggregate all volume cards and selected chapter cards.

Output:

- `book_design_card.json`

This is the single-book design fingerprint. It is not a plot summary.
It should describe the reusable engine and the boundaries of reuse.

### Phase G: Mechanism Candidate Mining

Output:

- `mechanism_candidates.jsonl`
- `anti_copy_ledger.json`

Mechanism candidates are reviewed before they become `material_library`
entries. The anti-copy ledger blocks source-specific copying.

### Phase H: Promotion

Approved rows become:

- `material_library` entries
- `config/story_design_grammars/*.yaml` patches
- quality gate rules

Promotion is explicit. Distillation alone must not affect running books.

## 3. Batch Workflow For Thousands Of Books

Recommended batch stages:

1. Register all files, detect supported formats, compute hashes, and dedupe titles.
2. Segment all books; unsupported formats go to a conversion/error queue.
3. Sample 5-10 books per genre for schema calibration.
4. Run chapter-card extraction in batches.
5. Aggregate by book.
6. Aggregate by genre.
7. Human-review only the top candidate mechanisms.
8. Promote approved mechanisms into `material_library`.
9. Run canary generation against 3-5 new book concepts.
10. Only then enable broader retrieval.

## 4. How New Books Use The Results

New books do not see source books. They see approved abstract mechanisms.

```text
approved mechanism
-> global material_library
-> project-specific Material Forge output
-> StoryDesignKernel
-> planner volume/chapter outline
-> drafter soft reference for future chapters
```

Existing books are unaffected unless a project is explicitly opted in.

## 5. Quality Gates

Every promoted mechanism must pass:

- abstraction gate: no plot retelling
- source-specific gate: no names, artifacts, geography, exact incident chain
- modernity gate: no outdated harmful reward patterns
- utility gate: can generate at least three distinct new-book variants
- novelty gate: does not clone an existing project material
- state gate: changes at least one tracked state variable

## 6. Executable Integration Commands

Validate one anonymized package:

```bash
python3 scripts/distillation/validate_package.py data/distillation/source-0001
```

Aggregate one or many packages into system-facing assets:

```bash
python3 scripts/distillation/aggregate_packages.py \
  data/distillation/source-0001 \
  --aggregate-key otherworld-cross-system \
  --output-dir data/distillation/aggregates/otherworld-cross-system
```

Dry-run material-library import:

```bash
.venv/bin/python scripts/import_material_jsonl.py \
  data/distillation/aggregates/otherworld-cross-system/material_entries.review.jsonl \
  --dry-run --format json
```

Install a reviewed grammar patch:

```bash
python3 scripts/distillation/install_grammar_patch.py \
  data/distillation/aggregates/otherworld-cross-system/grammar_patch.yaml \
  --apply
```

After installation, the grammar is loaded from:

```text
config/story_design_grammars/otherworld-cross-system.yaml
```

The current resolver maps `异界` / `otherworld` / `cross-system` projects to
this grammar.
