# Ch1 Opening Fix + Constraint Rebalance Verification

Date: 2026-05-27

## Implemented

- Fixed chapter length enforcement to count the full assembled chapter and block both under-length and over-length chapters.
- Set default Chinese hard max to 3000 CJK chars in the generation config and length stability constants.
- Promoted golden-three opening rules into the `PromptPlan.system` prefix.
- Added `opening_hook_density_gate` and wired it into post-assembly write safety.
- Added scene/chapter prompt compaction with contract de-duplication, staged leak slicing, and `<REPAIR_HINT>` wrapping.
- Added staged forbidden leak policy + loader for `output/exorcist-detective-1778051012`.
- Relaxed front-ten prompt language so phone/SMS are allowed as same-POV tools, while extra delivery NPCs remain blocked.
- Added `characters_off_screen_only`, required time anchors, and allowed relative time expressions to the pre-write constraint manifest.

## Evidence

```text
.venv/bin/pytest tests/unit/test_chapter_length_gate.py \
  tests/services/test_opening_hook_density_gate.py \
  tests/services/test_prompt_compactor.py \
  tests/services/test_forbidden_leaks_loader.py \
  tests/unit/test_chapter_constraint_manifest.py \
  tests/unit/test_prompt_constructor.py \
  tests/unit/test_pipeline_services.py::test_chapter_first_prompt_uses_publish_band_not_tight_target_delta \
  tests/unit/test_pipeline_services.py::test_chapter_first_prompt_enforces_scene_opening_and_front10_forbidden_terms \
  tests/unit/test_pipeline_services.py::test_chapter_first_prompt_adds_total_scene_budget_guardrail \
  -q --no-cov
88 passed in 1.79s
```

```text
CJK counter fixed: 5172
chapter has 5172 CJK chars, above hard max 3000 — must shrink
opening hook gate: ['OPENING_FLASHBACK_OVERUSE']
staged forbidden leaks loaded correctly
```

```text
.venv/bin/ruff check <new gate/loader/compactor/script/test files>
All checks passed

.venv/bin/python -m py_compile <changed service modules and script>
exit 0
```

## Not Executed

- Did not regenerate ch1/ch2/ch3 with live LLM.
- Did not run the human blind-read acceptance package.
- Did not mutate existing chapter contract JSON beyond adding the project-local staged forbidden leak policy file.
