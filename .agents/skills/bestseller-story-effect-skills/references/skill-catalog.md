# Story Effect Skill Catalog Reference

The catalog is a routing surface. It should be compact enough to keep in Planner prompts, and it must not contain every full prompt contract.

## Available Skill Families

- `brainhole_engine`: novelty contrast and persona-safe high concept.
- `comedy_engine`: visible comedy through mismatch, escalation, reversal, and callback.
- `emotional_payoff_engine`: seeded emotional debt and concrete payoff action.
- `relationship_chemistry_engine`: friction, debt, rescue, trust shift, shared secret.
- `suspense_reveal_engine`: information gap, misdirection, partial reveal, chapter-end question.
- `hype_satisfaction_engine`: visible gain, reversal, status shift, reward, slapback.
- `moral_dilemma_engine`: costly value choice under protagonist growth pressure.
- `system_payoff_engine`: institutional, role, policy, or power-structure payoff.
- `tension_pressure_engine`: deadline, stakes, scarcity, public consequence, and narrowing choice pressure.
- `rhythm_pacing_engine`: batch tempo variation through scene size, quiet beats, reversals, and payoff spacing.
- `twist_reversal_engine`: earned expectation flip through prior seeds and changed interpretation.
- `callback_motif_engine`: object, phrase, joke, rule, wound, or image echo with changed meaning.
- `world_texture_engine`: lived-in places, objects, social rules, labor, sensory anchors, and material consequence.
- `wonder_awe_engine`: earned scale, beauty, impossibility, sacredness, or system magnitude.
- `danger_action_engine`: readable danger through threat geometry, forced movement, and tactical cost.
- `dialogue_spark_engine`: conflicting agendas, status play, subtext, interruption, and bargaining.
- `healing_grief_engine`: loss, repair, regret, forgiveness, and quiet recovery through concrete care.
- `romance_tenderness_engine`: optional tenderness, restrained intimacy, longing, and mutual recognition when explicitly selected.

## Expansion Rule

Full contracts are expanded only when a selected skill asks for them. For example, `brainhole_contract` appears only if `selected_effect_skills.primary` or `secondary` includes `brainhole_engine`.

Expanded contracts currently exist for:

- `brainhole_engine` -> `brainhole_contract`
- `tension_pressure_engine` -> `tension_pressure_contract`
- `rhythm_pacing_engine` -> `rhythm_pacing_contract`
- `callback_motif_engine` -> `callback_motif_contract`
- `world_texture_engine` -> `world_texture_contract`

Catalog-only entries still need clear `use_when`, `avoid_when`, `best_stage`, `output_contract`, and `misuse_guardrails`, but they must not inject full prompt contracts until a renderer is added.

## Mythic Workplace Defaults

For `mythic-workplace-effect-skills`:

- Opening: `brainhole_engine`, `comedy_engine`, `world_texture_engine`, `dialogue_spark_engine`, `relationship_chemistry_engine`
- Early-middle: `tension_pressure_engine`, `twist_reversal_engine`, `callback_motif_engine`, `hype_satisfaction_engine`
- Middle-late: `emotional_payoff_engine`, `moral_dilemma_engine`, `danger_action_engine`, `system_payoff_engine`
- Late: `system_payoff_engine`, `wonder_awe_engine`, `healing_grief_engine`, `callback_motif_engine`

`romance_tenderness_engine` remains optional and must be chosen explicitly by metadata/selection for mythic workplace projects.
