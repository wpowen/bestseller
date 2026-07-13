# Quality Remediation Current Change Inventory

Captured: 2026-07-10 (Asia/Shanghai)

## Scope boundary

- The worktree was already dirty before execution of the remediation master plan.
- Existing edits in `.agents/skills`, `.claude/skills`, `config/default.yaml`, core services, and unit tests belong to the user/recent development work and must be preserved.
- No reset, checkout, clean, or blanket formatting operation is allowed.
- New remediation edits must remain attributable by file and test.

## Initial worktree snapshot

- 52 tracked files were modified.
- The initial tracked diff contained approximately 1,638 insertions and 498 deletions.
- Relevant new files already present included:
  - `src/bestseller/services/ai_slop_blacklist.py`
  - `src/bestseller/services/golden_rules.py`
  - `src/bestseller/services/planning_concurrency.py`
  - `src/bestseller/services/prompt_assembly.py`
  - `tests/unit/test_prompt_assembly.py`
  - `tests/unit/test_prompt_block_uniqueness.py`

## G0 baseline failure

Command:

```bash
uv run pytest --no-cov -q \
  tests/unit/test_planner_services.py \
  tests/unit/test_publishing_scheduler.py \
  tests/unit/test_prompt_assembly.py \
  tests/unit/test_prompt_block_uniqueness.py \
  tests/unit/test_conception_services.py
```

Observed result:

```text
ImportError: cannot import name 'summarize_world_spec'
from 'bestseller.services.planning_context'

1 error during collection
```

This is the G0 reference failure. Planner import/collection must be restored before quality architecture changes are treated as testable.

## G0 repaired baseline

After the planner/scheduler/export/PDF, Conception isolation/degradation, and
prompt-boundary fixes, the combined Phase 0 suite completed successfully:

```text
614 passed in 179.25s
```

The combined run included planning summaries, planner services, publishing
scheduler, export API/content rendering, prompt assembly and uniqueness,
writer routing, Conception services/session isolation/mechanism deduplication,
and world-grounding coverage. The exact command and result are preserved in
`output/quality-eval/baseline-preflight/pytest-results.txt`.

An adversarial publishing review then found and closed additional starvation,
authentication, remote-delivery uncertainty, and cross-instance idempotency
gaps. The post-review affected subsets passed 37 and 67 tests respectively,
and the PostgreSQL migration remained a single linear head after downgrade /
upgrade verification.

## Attribution rule for subsequent batches

For every task record:

1. the failing test and expected failure reason;
2. the minimal implementation diff;
3. the passing target test command;
4. any unchanged pre-existing failure;
5. files modified by that task.
