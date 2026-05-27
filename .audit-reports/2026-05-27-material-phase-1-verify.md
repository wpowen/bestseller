# Material Phase 1 Verify

- Implemented: `material_entity_registry`, `material_reference_scanner`, `material_referential_integrity_gate`.
- Integration: `scripts/book_lifecycle_quality_gate.py` now injects material referential integrity when a project dir is available.
- Verification:
  - `uv run pytest tests/services/test_material_lifecycle.py --no-cov` -> 7 passed.
  - `uv run python` gate smoke on `output/exorcist-detective-1778051012` -> `blocked`.
- Required findings confirmed:
  - `story-bible/series-brief.md:14` references deprecated `林逸`.
  - `obsidian-vault/人物/林渊.md:54` references deprecated `裴镜渊`.
  - `obsidian-vault/人物/林渊.md:47` references deprecated `周德昌`.

