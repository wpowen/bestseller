# Phase C Outline And Coherence Audit

Date: 2026-05-27
Project: `exorcist-detective-1778051012`

## Result

PARTIAL / BLOCKED.

The new readers and validators run, but the current book artifacts still show
outline/reveal alignment and continuity failures.

## Lifecycle Findings

`scripts/explain_lifecycle_findings.py` reports `passed=False`,
`readiness_level=blocked`, with 11 findings:

- critical: `character_gate_evidence_missing`
- critical: `identity_registry_coverage_below_bar`
- high: `scorecard_below_lifecycle_bar`
- critical: `book_incomplete`
- critical: `planned_chapters_without_current_drafts`
- critical: `blocked_chapters_remaining`
- high: `repair_tasks_remaining`
- high: `length_stability_below_bar`
- critical: `whole_book_acceptance_not_passed`
- critical: `model_execution_unavailable`
- critical: `reference_distance_missing`

Key metrics: `current_chapters=71`, `planned_chapters=500`,
`draftless_chapters=29`, `blocked_chapters=2`, `quality_score=57.51`.

## Outline Specificity

`scripts/compare_outline_specificity.py` passes for baseline
`20260524-qingnang`:

```text
before: n=32 avg=1.000 min=1.000 max=1.000 below_min=0
after: n=42 avg=1.000 min=1.000 max=1.000 below_min=0
expanded: n=100 avg=1.000 min=1.000 max=1.000 below_min=0
final: n=42 avg=1.000 min=1.000 max=1.000 below_min=0
OK outline specificity final passes
```

## Reveal Alignment

`scripts/verify_volume_reveal_alignment.py` fails. Schedule IDs not unlocked by
volume-plan:

`kou_zhang_ren`, `yizhuang_take_mirror_record`, `yizhuang_name_price`,
`well_yizhuang_merge`, `sixth_night_missing_page`, `second_handler_signature`,
`well_seal_exchange_path`, `father_truth_first_layer`.

Volume-plan reveals missing from reveal-schedule:

`father_line_partial`, `false_executor_identity`, `old_city_recovery`,
`recovery_contract_enforced`.

## Runtime Continuity Checks

Current generated chapters still fail:

- `verify_first_sentence_diversity.py`: 5 repeated first-sentence templates exceed `max_repeats=2`.
- `verify_timeline_canon_compliance.py`: 230 timeline violations.
- `verify_hook_cadence.py`: 3 cadence violations.
- `verify_cast_promises_compliance.py`: pass.
