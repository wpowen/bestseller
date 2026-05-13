# External LLM Distillation Protocol

Use this protocol when another model performs the extraction work.

The external model should receive private prompt payloads generated under
`.distillation_private/<source_id>/llm_payloads/`. The code repository stores
only the anonymized job index and normalized outputs.

## Chapter Card Task

System instruction:

```text
You extract reusable story-design mechanics from a source novel chapter.
Do not summarize prose. Do not preserve source-specific names. Do not imitate
style. Output exactly one JSON object matching the chapter_card schema.
All source-specific names, artifacts, places, organizations, techniques, and
unique event chains must be replaced with role labels.
```

Required output fields:

- `source_id`
- `abs_chapter_no`
- `chapter_function`
- `scene_functions`
- `state_changes`
- `reader_rewards`
- `setups`
- `payoffs`
- `open_hooks`
- `reusable_mechanisms`
- `non_reusable_specifics`
- `risk_flags`
- `confidence`

## Chapter Card Prompt Template

```text
SOURCE_ID: {source_id}
ABS_CHAPTER_NO: {abs_chapter_no}
VOLUME_NO: {volume_no}
CHAPTER_TITLE_REDACTED: {title_redacted}

SCHEMA:
{chapter_card_schema}

CHAPTER_TEXT:
{chapter_text}

Return only JSON. No markdown.
```

## Volume Aggregation Task

Input: 15-30 chapter cards.

Output: one `volume_card` JSON object.

The model should infer:

- arc function
- dominant engine
- state progression
- turning points
- setup/payoff rhythm
- reusable mechanisms
- failure modes

## Book Aggregation Task

Input: all volume cards plus sampled chapter cards.

Output:

- `book_design_card.json`
- `mechanism_candidates.jsonl`
- `anti_copy_ledger.json`

The model must distinguish:

- reusable mechanism
- source-specific expression
- outdated/risky trope
- genre grammar candidate

## Redaction Rules

External LLM output must never include:

- source book title
- source author
- character names
- faction names
- geographic names
- technique/artifact names
- unique exact incident chains
- long text excerpts

Allowed replacements:

- `protagonist`
- `host_body`
- `family_rival`
- `local_power_system`
- `high_level_beast`
- `royal_faction`
- `religious_authority`
- `forbidden_artifact`
- `source_world_method`

