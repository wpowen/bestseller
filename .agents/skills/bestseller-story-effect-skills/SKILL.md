---
name: bestseller-story-effect-skills
description: Coordinate BestSeller story-effect skills through a compact catalog, Planner routing, and selected contract expansion.
---

# BestSeller Story Effect Skills

Use this skill when adding or tuning story richness abilities such as brainhole novelty, comedy, emotional payoff, relationship chemistry, suspense reveal, reader satisfaction, moral dilemma, system payoff, tension pressure, rhythm pacing, callback motifs, world texture, wonder/awe, danger/action, dialogue spark, healing grief, or optional romance tenderness.

## Architecture Rule

Do not inject every full skill contract into Planner. Use three layers:

1. Catalog: a short always-available list of skills and routing metadata.
2. Router: Planner chooses at most one primary and one secondary skill per chapter.
3. Expansion: only selected skills expand their full contract into the current planning prompt or outline contract.

## Catalog Entry

Each story effect skill needs:

- `skill_key`
- `effect_type`
- `description`
- `source_modules`
- `use_when`
- `avoid_when`
- `best_stage`
- `can_pair_with`
- `conflicts_with`
- `output_contract`
- `misuse_guardrails`
- `expansion_policy`

## Planner Output

Each chapter should output:

- `selected_effect_skills.primary`
- `selected_effect_skills.secondary`
- `selected_effect_skills.reason`
- `selected_effect_skills.growth_stage_fit`
- `selected_effect_skills.expected_contracts`

The selected contracts, not the catalog, decide which extra fields the chapter must include.

Expanded contracts currently exist for:

- `brainhole_engine` -> `brainhole_contract`
- `tension_pressure_engine` -> `tension_pressure_contract`
- `rhythm_pacing_engine` -> `rhythm_pacing_contract`
- `callback_motif_engine` -> `callback_motif_contract`
- `world_texture_engine` -> `world_texture_contract`

All other catalog entries are routing-only until their contract renderer is explicitly added. Do not infer or paste their full prompt contracts into Planner.

## Compatibility

Legacy projects without `story_effect_skill_catalog` metadata must not receive new story-effect requirements. Existing hook, payoff, emotion, and character-drama systems stay as foundation layers; story-effect skills coordinate them rather than replacing them.

## Default Mythic Workplace Bias

For 神仙招聘 / 都市神仙 / mythic workplace comedy:

- Opening: `brainhole_engine`, `comedy_engine`, `world_texture_engine`, `dialogue_spark_engine`, `relationship_chemistry_engine`
- Early-middle: `tension_pressure_engine`, `twist_reversal_engine`, `callback_motif_engine`, `hype_satisfaction_engine`
- Middle-late: `emotional_payoff_engine`, `moral_dilemma_engine`, `danger_action_engine`, `system_payoff_engine`
- Late: `system_payoff_engine`, `wonder_awe_engine`, `healing_grief_engine`, `callback_motif_engine`

These are preferences, not hardcoded chapter rules.

`romance_tenderness_engine` is optional for this catalog. It must not enter mythic-workplace default stage preferences unless project metadata or explicit selection asks for romance.
