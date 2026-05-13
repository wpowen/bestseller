# Whole-Book Quality Verification Matrix

This matrix defines how the framework checks that a generated novel stays readable beyond the first chapters. The gate is language- and category-neutral; platform-specific references can enrich the prompt, but the checks apply to every generated novel unless `whole_book_quality_gate_disabled` is set.

| Layer | What It Protects | Runtime Signal | Failure Code | Required Repair |
| --- | --- | --- | --- | --- |
| Opening contract | The story promise is clear before drafting starts. | `opening_quality_contract` in project metadata. | `missing_opening_quality_contract` | Rebuild premise/opening promise before drafting. |
| Signing zone | Chapters 1-50 carry the highest signing and paid-reading burden. | `metrics.retention_zones.signing_zone` | `early_retention_hook_density_low`, `early_retention_turn_density_low` | Raise true hook density, limit no-hook streaks, and add frequent gains, reveals, costs, or emotional turns. |
| Extended entry zone | Chapters 51-100 still determine whether readers keep paying. | `metrics.retention_zones.extended_entry_zone` | `early_retention_hook_density_low`, `early_retention_turn_density_low` | Keep strong forward pull and a steady cadence of turns without forcing identical chapter shape. |
| Chapter function | Every chapter has a recognizable job, not a forced identical formula. | `whole_book_engagement_ledger[*].functional_shape` | `chapter_function_missing` | Pick the correct shape: proactive scene, reactive sequel, reveal turn, payoff resolution, or pressure setup. |
| Forward pull | A chapter that opens a new beat still points forward through hook or decision. | `has_hook`, `has_decision` | `chapter_hook_missing` | Replace the ending with an unresolved threat, reveal, choice, or next-step decision. |
| Chapter turn | Active chapters do not only delay; they change reader state. | `has_payoff`, `has_reveal`, `has_emotional_turn` | `chapter_payoff_missing` | Add a gain, cost, clue, leverage shift, emotional turn, or stage reward. |
| Rolling freshness | Five-to-ten chapter windows do not become repetitive or turn-starved. | Rolling ledger window. | `rolling_payoff_gap`, `rolling_repetition` | Reseed the window with new rewards, reveals, emotional turns, scene entries, reversals, and conflict shapes. |
| Arc closure | A story unit closes a meaningful mini-loop before moving on. | `volume_plan[*].arc_ranges` plus ledger tail. | `arc_payoff_missing` | Add a unit-level reveal, win/loss, status change, emotional turn, or solved subproblem. |
| Volume momentum | Volume or unit endings still create forward pull. | Final chapter record for the range. | `volume_momentum_drop` | Rebuild the end chapter so it both changes the current unit and opens the next one. |
| Rewrite task mapping | Failures create targeted repair work, not generic polishing. | `RewriteTaskModel.trigger_type == "whole_book_quality_gate"` | Strategy selected from finding code. | Use the mapped strategy: function, hook, payoff, freshness, closure, or momentum rewrite. |
| Pipeline blocking | Weak full-book quality stops continuation immediately. | `project.metadata_json["whole_book_quality_gate_blocked"]` | `Whole-book quality gate failed: ...` | Complete the rewrite task, then resume generation. |

## Implementation Hooks

- `evaluate_whole_book_quality(...)` produces the report, ledger, and metrics.
- `whole_book_quality_report_to_dict(...)` serializes the report into project/workflow metadata.
- `whole_book_quality_strategy_for_findings(...)` maps the highest-severity finding to a rewrite strategy.
- `build_whole_book_quality_rewrite_instructions(...)` creates the repair prompt and explicitly rejects surface-level polish.
- `run_project_pipeline(...)` updates the ledger after each chapter and blocks on high-severity whole-book failures.

## Acceptance Checks

- Chapters 1-3 can pass while chapter 4 still fails if it becomes flat.
- Chapters 1-50 must maintain high true-hook density and turn density because this is the signing/paid-reading decision zone.
- Chapters 51-100 retain elevated standards, though slightly less strict than the first 50.
- Reactive sequel chapters can pass without action-scene structure if they contain reaction, dilemma, and decision.
- A rolling window with no payoff, reveal, or emotional turn fails even if each chapter has surface activity.
- Repeated chapter openings fail as freshness loss.
- Volume/arc endings must change the current unit and preserve next-chapter momentum.
- The final repair artifact must be a rewrite task with the report and ledger attached.
