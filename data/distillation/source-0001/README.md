# source-0001 Distillation Package

This directory is a repository-safe, anonymized sample package for one source
book. It does not store the source title, author, original path, or raw text.

## Files

| File | Count | Purpose |
|---|---:|---|
| `source_manifest.json` | 1 | Anonymous source registration and parse profile |
| `chapters.index.json` | 256 chapters | Redacted chapter index with private chunk pointers |
| `llm_jobs/chapter_jobs.index.jsonl` | 256 jobs | External LLM chapter-card job index |
| `chapter_cards.sample.jsonl` | 12 cards | Audited sample chapter cards for schema review |
| `volume_cards.jsonl` | 12 cards | Volume/arc-level aggregation |
| `book_design_card.json` | 1 | Single-book design fingerprint |
| `mechanism_candidates.jsonl` | 15 rows | Candidate reusable mechanisms before review |
| `material_entries.sample.jsonl` | 12 rows | Material-library review candidates |
| `anti_copy_ledger.json` | 1 | Source-specific no-copy policy |
| `grammar_patch.yaml` | 1 | Proposed story grammar patch |

## Private Counterpart

Raw chunks and prompt payloads are generated under:

```text
.distillation_private/source-0001/
```

That directory is ignored by git.

## Promotion Status

No row in this package is automatically promoted. Review order:

1. Validate `chapter_cards.sample.jsonl`.
2. Run external LLM extraction for all 256 chapter jobs.
3. Aggregate and compare against `volume_cards.jsonl`.
4. Review `mechanism_candidates.jsonl`.
5. Promote approved entries into `material_library`.
