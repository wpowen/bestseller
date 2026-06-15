---
name: bestseller-brainhole-engine
description: Generate persona-safe high-concept "brainhole" story contracts for BestSeller planning, especially when novelty must track protagonist growth and chapter-level plot consequence.
---

# BestSeller Brainhole Engine

Use this skill when BestSeller needs a reusable novelty generator rather than a one-off idea list. The output must be usable by Planner, outline repair, or human story design.

In the BestSeller framework this skill is selectable through the Story Effect Skill Catalog. Do not make `brainhole_contract` a permanent requirement for every chapter; expand it only when Planner selects `brainhole_engine` for the current chapter or planning pass.

## Core Mechanism

Every brainhole is generated from five inputs:

1. Persona card: what the audience already knows about the character.
2. Modern system card: a current-life process, metric, rule, platform, complaint, KPI, queue, contract, job, or workflow.
3. Persona-safe contrast: pressure the character's core, but do not casually rewrite it.
4. Protagonist growth stage: the protagonist's current capability and authority determine what decision is legal.
5. Plot consequence: the joke must change role, relationship, resource, exposure risk, system pressure, or future choice.

## Required Contract

For each proposed chapter or event, output `brainhole_contract` with:

- `one_sentence_sell`
- `character_core_used`
- `modern_system`
- `contrast_mechanism`
- `visible_comedy`
- `serious_underbelly`
- `plot_consequence`
- `protagonist_decision`
- `growth_stage_fit`
- `risk_check`

## Persona Card Fields

- `name`
- `public_memory`
- `core_invariants`
- `elastic_zones`
- `forbidden_moves`
- `safe_contrast_moves`
- `modern_system_matches`
- `audience_risk`

For example, 哮天犬 can complain, negotiate, burn out, ask for transfer, or demand clearer duties, but cannot simply become disloyal unless the cause, pain, and repair path are explicit.

## Growth Gate

Match the protagonist's allowed action to the current stage:

- Opening: observe, interview, recommend, offer temporary trial.
- Early-middle: assign role, probation, mediate conflict.
- Middle-late: transfer, dismiss with cause, create role, change policy.
- Late: redesign system, public arbitration, institutional bargain.

Reject ideas where the protagonist solves the event by authority they have not earned yet.

## Generation Modes

- `book_matrix`: create the recurring contrast axes and modern-system pool for a book.
- `character_pool`: build persona cards for mythic or public-memory characters.
- `chapter_contract`: generate 3-10 chapter-level brainhole contracts.
- `repair`: replace flat or unsafe ideas with persona-safe contrast ideas.
- `audit`: flag hard personality breaks, offensive jokes, trend-only references, and growth-stage violations.

## Quality Bar

A valid idea should be surprising in the setup, inevitable after explanation, funny on the surface, serious underneath, and consequential in the plot.
