# Chapter Word Count SSOT

> Single source of truth for Chinese long-form chapter length.
> Last aligned: 2026-07-09 (full-quality remediation W0).

## Canonical band

| Field | Value | Meaning |
|-------|------:|---------|
| **min** | **1800** | Hard floor (zh chars) — below → rewrite |
| **target** | **2600** | Planning / writer aim |
| **max** | **3500** | Hard ceiling — above → rewrite / block export |

Scene band (with 3 scenes/chapter @ target 2600):

| Field | Value |
|-------|------:|
| min | 600 |
| target | 870 |
| max | 1150 |

## Code / config sources (must stay equal)

1. `config/default.yaml` → `generation.words_per_chapter` / `words_per_scene`
2. `src/bestseller/services/chapter_length_gate.py` → `DEFAULT_HARD_FLOOR_ZH_CHARS` / `DEFAULT_SOFT_WARNING_ZH_CHARS` / `DEFAULT_HARD_MAX_ZH_CHARS`
3. `src/bestseller/services/length_stability_gate.py` → `CHINESE_CHAPTER_HARD_MIN_WORDS` / `CHINESE_CHAPTER_HARD_MAX_WORDS`

Platform profiles (`platform_profiles.yaml`) may raise the **floor** (七猫 2500 / 起点 3000 / 番茄 2000) but must not exceed the product hard max of 3500 unless product explicitly revises this SSOT.

## Obsolete band (do not use)

`5000 / 6400 / 9000` was a historical Mode-B skill default. It **contradicts** the runtime gates and caused 2026-06 quality regressions (endless CHAPTER_TOO_SHORT / over-expansion churn). All skills under `.claude/skills/bestseller-framework/` and `.agents/skills/bestseller-framework/` were realigned to 1800–2600–3500.

## Mode B note

Mode B package layout differs (`output/ai-generated/{slug}/`), but chapter prose still goes through `run_chapter_pipeline` via `mode_b_bridge` / `scripts/mode_b_chapter_bridge.py` — same length contract.
