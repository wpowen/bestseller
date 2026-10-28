# Q4 writer delivery handoff

Writer implementation and offline verification are complete within the assigned scope. Runtime integration and the fa14 experiment are **not complete**. No Git commit was made.

## Scope and behavior

- Modified `chapter_writer_delivery.py` and exactly four writer prompt functions in `drafts.py`: `_render_contract_section`, `_render_character_causal_contract_block`, `build_scene_draft_prompts`, `build_chapter_first_draft_prompts`. Added only `tests/unit/test_q4_reader_first_delivery.py` plus these writer report/log files. No formatter, rewrite orchestration, historical source manifest, validator, mainline review/deslop or short-pipeline change.
- Unmarked projects retain v2 delivery; historical v1/v2 source selection keeps its original task/project/chapter scope. The v1 renderer and `reference_source_delivery_scope` ASTs are unchanged. Both languages and both writer entry points preserve complete original system/user bytes against HEAD functions.
- Marked projects require v3. The existing selected-plan admission guard remains in place. A missing selection, missing new source envelope, wrong protocol/input/config, old approval, stale full outline, changed chapter, invalid raw review or changed receipt blocks delivery. Both writer prompt builders require the typed v3 block and recheck its binding, hashes, roles, field pointers and a fresh parse of the retained raw review against its complete source.
- v3 includes the complete untouched approved chapter and its hash, entire readiness receipt hash, complete reviewed source and its hash, original raw review, full parsed reader receipt and its hash, final source scope and per-field provenance. Role channels contain pointers; the retained full source/raw review are explicitly audit data rather than a second set of chapter duties. Unknown authored fields, empty/null values, negations, precise times, amounts and original constraints survive. All metadata containers, including nested `_meta`, remain present and receive continuity pointers. Roles are explicitly authored before review, never inferred from words.

## Independent review corrections (final)

The initial implementation verified source and review at construction but only checked internal payload consistency at consumption. Independent review reproduced two real holes: a changed chapter goal with recomputed chapter/provenance hashes, and a nonexistent chapter pointer with recomputed field pointers, could retain the old review. Both reproductions were added before the fix and failed as expected. The payload now retains `review_source` and `raw_review`; construction and consumption share `_verified_reader_first_review`, which resolves the canonical unique chapter pointer, checks the exact complete chapter against that retained whole source, reparses the raw review against the whole source, and requires its exact passing receipt. Missing evidence, altered raw review, and rehashed whole-source substitutions also fail.

The complete chapter-first builder also discarded the returned binding and rendered its causal contract with legacy mandatory-visible-state wording. Chinese/English complete-builder tests reproduced this before the fix. The builder now keeps/passes the binding and version-gates the outer contract instruction; unchanged states retain their approved continuity role and exact amounts/times without forced announcements. Legacy rendering remains byte-identical. Scene runtime orchestration is untouched and remains a mainline integration requirement.

Final evidence superseding earlier intermediate GREEN counts:

- `writer-independent-review-red-20261028.log`: **4 failed**, both self-consistency exploits plus complete chapter-first output in both languages, before the correction.
- `writer-independent-review-final-green-20261028.log`: **133 passed** after all code fixes: **58** dedicated writer tests and the same **75** existing regressions, in one final run.
- `writer-independent-review-byte-comparison-20261028.log`: **6 exact system/user byte matches** against original HEAD functions: scene Chinese/English plus chapter-first Chinese/English full/lean, with a causal contract present.
- `writer-independent-review-readback-20261028.json`: final source/test SHA256 values, ownership AST check, unchanged historical v1 renderer/reference scope. Final `git diff --check` passed.

Retaining the complete review source/raw response increases v3 input size. The required block remains indivisible; capacity overflow must stop, never truncate audit/source evidence. Runtime capacity admission is still the unchanged mainline blocker below.

## Planner/source contract and mainline promotion

Coordinated directly via agent `01a182f0-1544-79b0-9dde-22200de75442` using native `send_input`. Read the actual planner producer/checkpoint functions and verified fixture compatibility by calling its real `_reader_first_outline_coverage` helper. This proves the format seam locally; it does not prove persisted runtime admission.

Planner produces each chapter with:

```text
_meta.reader_first_planning = full frozen project binding
_meta.writer_delivery_schema = approved-current-chapter-delivery.v3-reader-first
_meta.reader_first_field_roles = {every non-_meta leaf pointer: narrative_task|continuity_reference}
```

The complete reviewed wrapper stores the exact artifact at `source.outline`. Planner persists `project.metadata_json.reader_first_outline_checkpoint.history` entries containing `binding`, `source`, `source_sha256`, `raw_review`, `receipt`, `coverage`, workflow/run identity and reservations. A successful entry is available for a future admission adapter; writer does not manufacture a readiness receipt or copy history into one.

The **new admitted readiness receipt producer, owned by mainline**, must expose this explicit envelope after validating current artifact selection, persisted checkpoint/quota/run provenance and complete coverage:

```text
receipt.reader_first_source = {
  schema: reader-first-writer-source.v1,
  binding: full frozen project binding,
  source_binding: {project_id, chapter_id, chapter_number, artifact_id},
  source: complete reviewed wrapper,
  chapter_pointer: /outline/chapters/<actual array index>,
  review: original raw reader-first-review.v1 response,
  reader_receipt: exact parse_reader_first_review(..., checkpoint=outline) result
}
```

Writer requires `source.outline == selected_artifact.content`, its complete hash equality, one uniquely matching chapter number, the exact canonical chapter pointer, the complete selected chapter hash, and a fresh passing parse of the raw review equal to the supplied receipt. Adding markers/roles after review changes the source and invalidates its receipt. Historical approvals must never be retrofitted.

Safe blocks remaining:

1. The admission/selection producers currently do not promote that envelope. Marked projects stop with `reader_first_delivery_full_artifact_binding_required` (or `reader_first_delivery_approved_source_required` when no plan is selected).
2. Even an explicitly supplied valid v3 block is rejected by unchanged `writer_source_capacity._delivery_binding` with `writer_source_capacity_verified_delivery_required`; a test asserts this. Mainline must integrate v3 deliberately across capacity and review/source-proof consumers, preserving current validation and retaining any authorized migration/reference proof. This implementation does not widen their accepted schemas.
3. The scene runtime caller still needs mainline wiring to load and pass admitted v3 blocks. Its prompt builder currently stops with `reader_first_delivery_v3_integration_required` when that wiring is absent. The chapter-first caller already loads delivery, then encounters the capacity gate above.
4. fa14 is frozen at **short4 / 8000**. Its conditional mainline files are outside Q4 ownership and untouched. No full-flow, short-chain, model-quality, human-readability, calibrated-threshold or experiment acceptance is claimed.

## Evidence

- `writer-delivery-red-20261028.log`: **32 failed, 2 passed**, before implementation; existing v1/v2 byte/provenance snapshots already passed.
- `writer-pointer-red-20261028.log`: **2 failed, 4 passed**, exposing post-construction field-pointer/hash drift before its fix.
- `writer-planner-seam-red-20261028.log`: **2 failed**, exposing full-artifact binding and recursive metadata compatibility before their fixes.
- `writer-delivery-green-20261028.log`: **50 passed** after implementation, including real planner coverage-format compatibility and the unchanged mainline capacity rejection.
- `writer-legacy-regression-20261028.log`: **75 passed** across existing delivery, scene/compiler, chapter-first/compiler and methodology source-boundary tests.
- `writer-legacy-byte-comparison-20261028.log`: exact original HEAD system/user bytes for scene and chapter-first in Chinese and English; those four hashes are also frozen in the dedicated tests.
- `writer-scope-readback-20261028.json`: pre-independent-review file SHA256 values and AST ownership readback; superseded by `writer-independent-review-readback-20261028.json` above.

Tests use the root `.venv/bin/python -m pytest --no-cov` from this worktree with `BESTSELLER__DATABASE__URL=sqlite:///:memory:` and `PYTHONPATH=src:tests`. The existing safety helper verifies SQLite `:memory:`; fixtures prohibit DB connections and sockets. Prompt tests use the existing safe settings loader to avoid generic YAML coercion of raw `:memory:`. The 75-test legacy run used an in-process pytest plugin with the same safety/settings fixture, without editing shared tests or application settings. No Docker, DB connection, network, model request or paid dispatch occurred. Disk availability was read with `df -k /` before each execution/edit step and remained above 5 GiB (lowest observed approximately 8.91 GiB).

Evidence is local/fixture only. Persisted readiness promotion, production/source-proof readback, real model execution and human acceptance remain Unknown/unrun.
