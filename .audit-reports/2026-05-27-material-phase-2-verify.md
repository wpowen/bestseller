# Material Phase 2 Verify

- Implemented:
  - `scripts/auto_fix_deprecated_references.py`
  - `scripts/dedupe_character_files.py`
  - `scripts/cleanup_stale_forbidden_terms.py`
- Verification:
  - `uv run ruff check ...` -> passed for new scripts and services.
  - `uv run python scripts/auto_fix_deprecated_references.py --project-dir output/exorcist-detective-1778051012` -> dry-run lists canonical replacements and manual-review items.
  - `uv run python scripts/dedupe_character_files.py --project-dir output/exorcist-detective-1778051012` -> dry-run lists duplicate character files, including the 林正淳 variants.
  - `uv run python scripts/cleanup_stale_forbidden_terms.py --project-dir output/exorcist-detective-1778051012` -> dry-run reports stale/cross-book forbidden terms.
- Note: output materials were not rewritten during verification; scripts default to dry-run and require `--apply`.

