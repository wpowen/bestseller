# Q4 planner handoff — 2026-10-28

Implemented and stable in `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/scratch/par-worktrees/Q4`. No commit. Only owned code files changed: `src/bestseller/services/planner.py`, the body of `workflows.py::_repair_chapter_outline_contract_inputs`, and new `tests/unit/test_q4_reader_first_planner.py`. Parent owns protocol/config; conception and writer remain other workers' responsibility. No production, database, Docker, network or model execution was performed. Test completions, sessions and run IDs are fixtures, not paid/runtime receipts.

## Implemented behavior

- Four foundation prompt builders, ordinary complete/volume outline builders including the source-bound early return, structured planner generation and outline surgical repair carry the frozen reader-first binding in system guidance. Guidance preserves explicit procedural genre intent and source facts, and addresses wants, conflict, voice and emotional payoff. No-marker prompt pairs remain byte-identical to pinned worktree HEAD in both languages. New structured calls have an independent short gateway version and binding-sensitive input identity; historical cache/retained-generation admission is disabled for this protocol. Existing strict-source output/capacity behavior is selected using the original base version.
- Outline prompts require model-authored chapter `_meta.reader_first_planning`, `writer_delivery_schema=approved-current-chapter-delivery.v3-reader-first`, and complete `reader_first_field_roles`. Every authored non-`_meta` leaf pointer must be classified, including null/empty containers; `_meta` is excluded recursively from role enumeration while retained completely in the reviewed source. No keyword classification or missing-role inference is implemented.
- Marked ordinary native outline validation uses Pydantic as a validation view and retains the original candidate. This is necessary because scene models drop unknown nested metadata. Marked validation does not use historical deterministic creative enrichment to invent missing choices, costs or custody/debt obligations. The workflow derivation function copies only explicitly authored contract fields; missing creative fields remain subject to existing gates.
- The reader checkpoint is attached to `generate_novel_plan` after complete candidate preparation and before bound outline artifact persistence; bound per-batch and per-volume approved imports are deferred until the complete checkpoint passes. It is also attached before progressive volume promotion/import. A partial progressive volume cannot claim full-book coverage.
- Full chapter range and scene range are checked, including chapters after ten; source and chapter hashes freeze the coverage record. The parent prompt helper rejects inputs exceeding 24k estimated tokens before sending. No source truncation or reader-review batching is implemented: oversized or incomplete books stop Unknown with reasons and directions. This is the bounded long-book fallback, not long-book integration evidence.
- Durable project metadata owns the checkpoint quota, independent of workflow/source changes: at most two reader dispatch reservations and one repair reservation. Every save re-locks the project row, checks checkpoint revision, persistent workflow ownership and nondecreasing counters, merges freshest unrelated metadata, then commits. No lock is held across model IO. A pending/interrupted reservation requires recovery and cannot automatically resend. These concurrency behaviors were tested using fake persistence; live PostgreSQL serialization remains unverified.
- A valid failed review uses one actual existing native surgical repair, followed by full-source reevaluation. The reservation is committed before dispatch. Transport retry/fallback attempts are bounded to one for checkpoint calls. Invalid/fallback/truncated review responses, capacity failures, exhausted quota and invalid native edits stop with dimension scores/reasons/options and preserve history. Parent parser preserves individually valid dimensions in Unknown responses.
- Review-finding P1 is fixed: reader-driven native surgery invokes `_validate_reader_first_native_outline` before final reader evaluation. It checks the entire candidate, iterates the complete volume plan, invokes the real `_validate_generated_volume_outline_or_raise` with cast identity, volume entry/count/offset and existing/candidate peer-volume titles, and rejects any authored mutation by validation. The prior generic patch-semantic check alone is insufficient and is no longer the only gate. Foundation source objects and author-designated continuity references must remain unchanged. New alias leaves or unauthored role changes fail coverage.
- Review-finding P2 is fixed: a crash after the failed-result commit but before repair reservation does not lose unused repair capacity. Resume reparses the retained raw failure against the same source/binding and continues the CAS repair only if `repairs_used==0` and a reevaluation allowance remains. An already used repair cannot dispatch again. Pending/Unknown cases still stop.
- The accepted repaired body replaces the final candidate; macro/rolling project projections are rebuilt from that accepted body rather than retaining old chapter anchors. Reader-first runtime projections live in project metadata rather than adding unauthored story leaves after source review.

## Exact writer handoff

The persisted checkpoint is `project.metadata_json.reader_first_outline_checkpoint`. Each history entry retains:

```text
binding
source                         COMPLETE reviewed wrapper
source_sha256                  canonical JSON SHA-256
raw_review                     ORIGINAL reader-first-review.v1 response text
receipt                        exact parent parse_reader_first_review result
coverage                       complete chapter/scene manifest and hashes
workflow_run_id
llm_run_id
```

The ordinary complete wrapper contains `book_spec`, `world_spec`, `cast_spec`, `volume_plan`, `outline`, `premise`, and `original_input_binding`. The progressive wrapper contains the same four foundation/volume keys and `outline`. Writer consumers should treat the entire retained wrapper as immutable and not reconstruct it from summaries.

- `outline_pointer=/outline`.
- `chapter_pointer=/outline/chapters/<index>`.
- `source.outline` must match the entire selected final artifact content, including all metadata.
- `source.outline.chapters[index]` must match the exact approved chapter and its unique chapter number.
- Use the admitted complete combined outline artifact for ordinary full-book scope. Deferred per-volume projections explicitly state that the combined artifact is required; they must not be promoted by reusing the combined source's receipt against a different artifact body.

Mainline must verify durable quotas, coverage, model-run provenance and current admission before copying an admitted history entry into a NEW readiness receipt's `reader_first_source` envelope and binding final project/chapter/artifact IDs. Q4 planner does not manufacture that readiness receipt. The writer consumer and existing `writer_source_capacity` v3 admission boundary remain mainline integration work; full retained v3 source must be budgeted without truncation.

## Verification

- Initial RED: `../planner-red-clean-20261028.log`: 18 expected behavior failures, no collection/setup errors.
- Independent findings RED: `planner-review-findings-red-20261028.log`: two reproduced defects; already-used repair control passed.
- Final targeted GREEN: `planner-review-fixed-green-20261028.log`: **69 passed**, comprising **36 dedicated planner tests + 33 targeted existing tests**. Includes a successful real native repair/full-source reevaluation, actual Pydantic and artifact-import boundaries with fake IO, domain-validator rejection, all-volume context/title coverage, exact legacy prompt pairs, stale receipt rejection, crash resume and CAS conflicts.
- The final command used the root runtime with explicit safe SQLite selection:

```sh
BESTSELLER__DATABASE__URL=sqlite+aiosqlite:// PYTHONPATH=src:tests \
/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest --no-cov -q \
  tests/unit/test_q4_reader_first_planner.py \
  tests/unit/test_planner_prompt_version_contract.py \
  tests/unit/test_planner_artifact_empty_shell_guard.py \
  tests/unit/test_planner_fragment_or_ref.py \
  tests/unit/test_planner_json_extraction_recovery.py
```

The originally requested raw `sqlite:///:memory:` was used for RED and early GREEN. Some older tests call settings loading without JSON quoting and interpret that raw value as a YAML mapping; the coordinator selected `sqlite+aiosqlite://` for final combined execution. No SQLite connection is required by these tests.

- Broader existing `test_planner_services.py`: current **115 passed / 17 failed**, pinned planner HEAD loaded into the same dependency tree **114 passed / 18 failed**. No new failing node names. This suite is **not green**. Parent independently compared exception fingerprints with no new fingerprints; see `planner-baseline-error-fingerprints-20261028.json`. No broader baseline fixes were attempted.
- Six actual legacy prompt-builder pairs are equal to pinned HEAD in both languages. Critical static diagnostics (`F821/F822/F823/F811/E9`) and `git diff --check` passed; full repository lint is not claimed clean.
- `planner-handoff-evidence-20261028.json` records exact owned-file hashes and function anchors. AST comparison proves all of `workflows.py` outside the owned function is unchanged.
- `../planner-precheck-20261028.json` pins `e2e17=419181a41f29363263cee8a12d1694ace0bcef24`, `int/full-flow=9bfde9a52cd70a147cbe9ead04a0763e01a7bb85`, `fix/g2b=ffb49f67a4e15d532fc3476bd5745774e59466c6`; scoped path logs/diffs were empty. Worktree HEAD is `f7cc7dc380907669fa7afd8891c17e6927e365e5`.
- `df -k /` preceded each work step; observed available space remained above 5 GiB.

## Seams not integrated / Unknown

1. **Actual fa14 route is blocked for both complete offline-flow and paid claims.** Frozen target is four segments / 8000 / `fanqie_short`. `generate_fanqie_beat_sheet` belongs to `fanqie_short_planner.py`, imported by `fanqie_short_pipeline.py`; it is not in owned `planner.py`. Its foundation → Beat → projection → `complete_missing_outline_contract` → persistence/materialization route bypasses ordinary outline generation/checkpoint. Mainline owns `fanqie_beat_recovery.build_current_request`, completion request, and short-pipeline before-write/persist integration. Those files were not changed.
2. Mainline owns legal task pause/resume mapping, durable accepted-source recovery/admission and readiness/v3 capacity integration. The checkpoint raises `ReaderFirstDirectionRequired`; no project/workflow status enum or orchestrator was changed. A reader pass is not a readiness envelope or a production release receipt.
3. Long-book reader batching/dependency admission is absent. Oversized full sources or partial progressive scopes stop Unknown. No cropped prefix is graded as complete.
4. No real model, real integration database, human reader calibration, production readback, full-flow request counting or paid experiment was executed. Physical budget 140 and offline >120 stop remain parent/mainline execution requirements, not a measured result of this patch.

Owned files are frozen after the final 69-test pass. Parent may sanitize NEW failure logs in place while preserving names, summaries and fingerprints; the report does not depend on retaining raw assertion/prompt excerpts.
