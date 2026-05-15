# Worldview Kernel Data Contract

## Purpose

`WorldviewKernel` is the framework-level operating contract for a book's world.
It is not a prompt note and not a one-off setting bible.  Every downstream
artifact must be able to consume it as data:

```text
DistilledStrategyCard
-> DistilledWorldviewBridge
-> StoryDesignKernel.worldview_kernel
-> VolumePlan worldview progression fields
-> ChapterOutlineInput worldview execution fields
-> worldview_progression_gate / worldview_compliance_gate
```

The goal is simple: if the world is removed, volume plans and chapter outlines
must break.  If they still work unchanged, the worldbuilding is decorative.

## What WorldviewKernel Owns

`WorldviewKernel` owns the book-specific rules that make the world behave:

- `premise` and `uniqueness_principle`: why this world is structurally different.
- `invariants`: hard rules and violation costs.
- `systems`: power, law, resource, information, social, or technology systems.
- `factions`: pressure engines with resources and agenda.
- `locations`: repeatable story machines, not scenery labels.
- `reveal_ladder`: staged world truths with earliest volume/chapter gates.
- `integration_contract`: how chapters and volumes must use the world.
- `distilled_mechanism_bindings`: source-abstract mechanisms adapted to this book.
- `state_variables`: measurable world states that must change.
- `asset_ledger`: valuable world assets with cost and exposure risk.
- `authority_claims`: legitimacy claims over rules, places, assets, or people.
- `scene_templates`: repeatable scene patterns that execute world rules.
- `anti_copy_boundaries`: blocked source-like combinations or phrases.

Example adapted from `cross-system-rule-arbitrage`:

```json
{
  "state_variables": [
    {
      "key": "cross_system_understanding",
      "variable_type": "knowledge",
      "current_value": "主角只知道旧体系规则。",
      "desired_direction": "逐步理解新世界权威如何解释规则。",
      "change_triggers": ["公开审计听证", "破解规则冲突"],
      "failure_mode": "世界观变成背景说明而不是章节发动机。"
    }
  ],
  "asset_ledger": [
    {
      "key": "hidden_route_archive",
      "asset_type": "information",
      "value": "证明帝国篡改边境航线。",
      "cost": "使用档案会留下检索记录。",
      "exposure_risk": "审计庭会追踪异常访问。"
    }
  ],
  "authority_claims": [
    {
      "claimant": "帝国审计庭",
      "target": "边境航线解释权",
      "claim_basis": "帝国审计法",
      "legitimacy": "公开合法但掩盖篡改。",
      "escalation_path": "从核查升级到封港。"
    }
  ]
}
```

## What DistilledStrategyCard Owns

`DistilledStrategyCard` owns abstract strategy requirements derived from
distilled mature-novel data.  It must stay source-agnostic:

- selected mechanisms and their role.
- required state variables.
- required change vectors.
- reader reward mix.
- anti-copy boundaries.
- structured `worldview_bindings` produced for planning.

It does not own book-specific nouns by itself.  A mechanism like
`cross-system-rule-arbitrage` becomes useful only after it is transformed into
this book's rule system, authority pressure, assets, and scene templates.

## What DistilledWorldviewBridge Owns

`DistilledWorldviewBridge` converts strategy-card material into a JSON payload
that can be copied into `WorldviewKernel`:

- `distilled_mechanism_bindings`
- `state_variables`
- `asset_ledger`
- `authority_claims`
- `scene_templates`
- `anti_copy_boundaries`

It is deterministic and pure.  It should not read files or query storage.  The
caller passes strategy card data and aggregate material rows in.

## Required VolumePlan Fields

Volume plans must show world progression before chapters are generated:

```json
{
  "world_state_targets": ["cross_system_understanding +1"],
  "active_authority_claims": ["帝国审计庭主张边境航线解释权"],
  "map_function": "灰港审计厅展示航线规则并制造审计庭压力",
  "world_asset_refs": ["hidden_route_archive"],
  "asset_risk_escalation": "档案使用从检索记录升级为封存追踪",
  "reveal_budget": 1
}
```

Repair expectation:

- `world_state_targets` must name registered `WorldviewKernel.state_variables`.
- `map_function` must describe resource anomaly, faction or authority pressure,
  rule demonstration, cost, or risk.
- repeated `world_asset_refs` must scale cost, exposure, or attention.
- `reveal_budget` should stay at or below the default per-volume budget unless
  the story design explicitly justifies a major reveal cluster.

## Required ChapterOutlineInput Fields

Chapter outlines must show world execution:

```json
{
  "world_rule_refs": ["route_rule_arbitrage"],
  "world_rule_landing": "主角用旧航线规则解释新审计漏洞，但留下检索记录。",
  "world_state_deltas": [
    {
      "key": "cross_system_understanding",
      "delta": "+1",
      "evidence": "公开听证中证明两套规则冲突。"
    }
  ],
  "world_asset_refs": ["hidden_route_archive"],
  "authority_claim_refs": ["边境航线解释权"],
  "world_scene_template_ref": "route-audit-hearing",
  "reveal_weight": 1,
  "anti_copy_boundary_notes": ["不能照搬来源作品的双修体系或宗门审判链。"]
}
```

Repair expectation:

- every `world_state_deltas[].key` must be registered.
- asset refs must show visible `cost` or `exposure_risk`.
- active faction pressure should bind to an authority claim.
- scene template refs should be registered.
- `reveal_weight` should not exceed the default chapter budget.
- anti-copy boundary hits are critical.

## Gates And Finding Codes

`worldview_progression_gate` runs at volume-plan level.

- `authority_ladder_flat`: adjacent volumes repeat the same authority pressure.
- `map_function_missing`: map/location function is only scenery.
- `state_variable_stalls`: registered world state variables never change.
- `asset_risk_not_scaled`: repeated asset use does not increase risk.
- `reveal_distribution_imbalanced`: too many major reveals cluster in one volume.

`worldview_compliance_gate` runs at chapter-outline level.

- `world_rule_not_grounded`
- `unregistered_world_rule_ref`
- `unregistered_world_location`
- `unregistered_world_faction`
- `world_reveal_leak`
- `world_state_delta_missing`
- `unregistered_world_state_variable`
- `world_asset_cost_missing`
- `world_asset_exposure_missing`
- `authority_claim_missing`
- `world_scene_template_missing`
- `world_reveal_budget_exceeded`
- `world_anti_copy_boundary_hit`

Both gates default to warn-only rollout in planner settings.  Blocking can be
enabled after canary books prove low false-positive rates.

## Adding New Distilled Mechanisms Safely

When adding a new mechanism:

1. Keep the mechanism abstract.  Do not encode source names, scene order, or
   recognizable source-specific combinations.
2. Define the expected state variables and change vectors.
3. Define at least one possible cost, exposure, or authority pressure.
4. Add anti-copy boundaries for source-like openings, object chains, named
   systems, or scene sequences.
5. Add tests at the bridge, kernel, planner, and gate layers.

Mechanisms should increase controllable world behavior.  They should not add
template prose.

## Anti-Copy Boundary Policy

Anti-copy boundaries are hard constraints, not style suggestions.

- Store them on `DistilledStrategyCard` and copy them into `WorldviewKernel`.
- Preserve them in story-design prompts.
- Surface them through chapter outline fields.
- Treat direct hits in title, goal, conflict, hook, or reveals as critical.

The system may learn mechanics from mature structures, but every project must
replace source-specific names, objects, scene order, power taxonomy, and opening
chains with book-specific equivalents.
