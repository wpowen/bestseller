# EVAL: Long-Serial Hook Pipeline

## Objective

Prove that the production concept stage can generate a compelling one-sentence
hook and a non-repetitive long-serial engine before any book project or planning
workflow is created. Correct rejection is necessary but insufficient: viable
prompts must also succeed reliably.

## Arms

1. `current`: current dimension-heavy candidate prompt and unchanged gates.
2. `lean_story_package`: compact whole-story brief plus one selected brainhole
   principle; no catalog dump, volume outline, independent decision Agent, or
   repair stack in the initial generation call.

Both arms use the same candidate count, model route, hook judge, deterministic
seriality audit, long-form judge, thresholds, and random seed.

## Capability Evals

1. Generate a hook with a concrete protagonist action, distinctive rule or
   discovery, and an intelligent counterforce.
2. Avoid parallel repetition such as “do one product/case, get blocked once”.
3. Preserve locally rational protagonist and opponent decisions.
4. Prove at least `ceil(chapters / 3)` distinct 2-4 chapter micro-units for
   targets of 500 chapters or more.
5. Cover the complete chapter range with at least four rule-changing phases.
6. Keep the same reader promise from hook through endgame.
7. Stop without creating a project or workflow when any hard gate fails.

## Regression Evals

1. Missing or malformed judge output remains fail-closed.
2. A passed ConceptContract cannot be overturned by an unavailable duplicate
   logline judge.
3. `stop_after_conception` never starts planning.
4. Explicit `concept_seed` does not restore stale `concept_lab_bundle` or
   `hook_spec` context.
5. Hooks shorter than 18 characters, longer than 120 characters, or containing
   multiple paragraphs are rejected before promotion.

## Benchmark Groups

- `viable`: six cross-genre prompts expected to support 300-500 chapters.
- `weak_seed`: prompts whose visible mechanism is repetitive; the system must
  either transform the mechanism while preserving story identity or reject it.
- `anti`: frozen low-quality fixtures that every judge configuration must reject.

## Metrics

- `hook_pass@1`, `hook_pass@3`
- `serial_pass@1`, `serial_pass@3`
- `full_pass@1`, `full_pass@3`
- `pass^3` on anti fixtures and fail-closed regressions
- position-swapped pairwise win rate
- prompt characters, calls, latency, and output tokens per successful winner

## Promotion Rule

Promote `lean_story_package` only when all are true:

- viable `full_pass@3 >= 80%` and `full_pass@1 >= 50%`;
- anti and fail-closed regressions are `pass^3 = 100%`;
- at least 60% of valid position-swapped comparisons prefer Lean over Current;
- no winner has a hook hard axis or seriality axis below its production floor;
- median prompt characters and generation calls do not increase;
- a human review confirms that no promoted hook is merely a longer explanation
  of the same repetitive mechanism.

Invalid model or judge output is recorded as `INVALID`; it never receives a
default passing score.

## Required Artifacts

- exact prompts and configuration for both arms;
- every candidate and judge score;
- deterministic seriality reports;
- task-level pass@k and aggregate metrics;
- position-swapped verdicts;
- Docker task evidence proving project/workflow non-creation on failure;
- final keep/revise/remove decision.
