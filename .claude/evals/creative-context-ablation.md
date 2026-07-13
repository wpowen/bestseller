# EVAL: Creative Context Ablation

## Objective

Find the smallest prompt/Skill/context combination that reliably produces a
better complete novel concept than a bare one-line prompt. The experiment must
measure marginal value. It must not assume that the existing pipeline is the
target architecture.

## Controlled Variables

- Generator model: identical for every arm.
- Judge model: identical for every comparison.
- Sampling count and token ceiling: identical for every generative arm.
- Input intent: identical within a task.
- Judging: anonymous, pairwise, and position-swapped.
- Invalid judge output: `INVALID`; never replace it with a passing score.

## Ablation Ladder

1. `L0_bare`: the user's one-line request only.
2. `L1_story_package`: add a compact whole-story output brief.
3. `L2_decision`: add a first-person rational-choice audit for major decisions.
4. `L3_selected_skill`: add one compact, selected brainhole Skill contract.
5. `L4_one_revision`: run one diagnosis-and-rewrite pass on the L3 result.

Each level adds exactly one attributable capability. No full Skill catalog,
market profile, hook formula pool, methodology library, or multi-gate stack may
be injected.

## Judge Dimensions

Score each candidate from 1 to 10 on exactly five dimensions:

1. `click_and_freshness`: immediate curiosity plus non-template specificity.
2. `causal_coherence`: setting, rule, cost, conflict, and escalation form one chain.
3. `character_intelligence`: protagonist and opponent choices are locally rational.
4. `serial_engine`: the idea can generate varied long-form conflict without repetition.
5. `emotional_promise`: the reader knows what feeling and relationship payoff to expect.

## Hard Rejection Conditions

- The hook is a generic formula that could fit many unrelated books.
- The protagonist ignores an obvious safer or cheaper option without a hard reason.
- The core ability, cost, and main conflict do not causally interact.
- Novelty is only a label collision and has no plot consequence.
- The opening incident does not force a meaningful choice.
- The long-form engine merely repeats the same case, fight, or progress-bar action.

## Promotion Rule

A treatment is promoted only when:

- both position-swapped judgements select the same treatment winner; and
- it wins at least 60% of valid sample comparisons against the preceding level; and
- it introduces no hard-rejection regression on any task; and
- its gain is not explained only by longer output.

If position-swapped judgements disagree, record `UNSTABLE`, not a win.

## Execution Phases

### Pilot

- One generic xianxia task.
- Two samples per arm.
- Compare adjacent ladder levels.

### Confirmation

- Generic xianxia, occupational supernatural suspense, and relationship-driven urban fiction.
- At least three samples for the surviving arms.
- Compare the winning compact treatment against `L0_bare` and the current production output.

## Required Artifacts

- Exact prompt for every arm.
- Candidate text and token usage.
- Anonymous judge packets.
- Position-swapped verdicts and parse failures.
- Revealed arm mapping after judgement.
- Marginal win rate and token cost by level.
- A keep/remove/revise recommendation for every added context block.

