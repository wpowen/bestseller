# Material Phase 4 Verify

- Implemented:
  - `material_advancement_gate`
  - `continuity_ledger_writer`
- Judge prompt wiring:
  - `chapter_llm_quality_judge`, `chapter_window_quality_judge`, and `volume_quality_judge` now require/encourage `material_advancement_score` when material obligations are available.
- Verification:
  - `uv run pytest tests/services/test_material_lifecycle.py tests/unit/test_book_lifecycle_quality_gate.py tests/services/test_judge_rubrics.py --no-cov` -> 14 passed.

