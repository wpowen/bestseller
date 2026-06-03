# Existing output AI-flavor audit

Date: 2026-06-02

## Scope

Scanned generated chapter-like markdown under `output/`, excluding non-prose surfaces:

- `audits/`
- `traces/`
- `story-bible/`
- `knowledge/`
- `diagnostics/`
- `listing/`
- `amazon/`
- `rejected-drafts/`
- `archive/`

The scan used the current framework detectors:

- `bestseller.services.ai_flavor.detect`
- `bestseller.services.anti_meta_gate.check_anti_meta_gate`
- `bestseller.services.show_dont_tell_gate.check_show_dont_tell_gate`

## Findings

### Span-level AI flavor

- Current chapter files scanned: 2542
- Files with span-level AI-flavor findings: 570

Highest concentration:

- `female-no-cp-1776303225`: 507 files, 263 hit files, 929 spans, max score 96.0
- `xianxia-upgrade-1776137730`: 288 files, 207 hit files, 606 spans, max score 64.0
- `exorcist-detective-1778428166`: 100 files, 56 hit files, 133 spans, max score 32.0
- `exorcist-detective-1778051012`: 108 files, 18 hit files, 38 spans, max score 20.0

Dominant categories:

- `weak_adverb`: 1647 spans
- `micro_expression`: 69 spans
- `time_marker`: 29 spans
- `dialogue_tag`: 10 spans
- `promo_language`: 1 span

Interpretation: current output is not mostly failing on Humanizer-zh's assistant/essay artifacts. The practical AI flavor is pattern lock: repeated weak adverbs, template facial reactions, and generic time transitions inside chapters.

### Anti-meta / summary language

After adding dialogue protection and reducing `余波` false positives:

- Chinese current files with anti-meta findings: 430
- Main terms/signals:
  - `ENDING_OUT_OF_SCENE`: 363
  - `这一章`: 47
  - `接下来`: 38
  - `所有人都在`: 19
  - `钩子`: 16

Interpretation: the strongest remaining workflow problem is chapter ending and boundary narration. Many chapters still end by summarizing state or reader-facing story effect instead of landing on an action, visible object, reveal, or live line.

### Show-don't-tell

After adding dialogue protection:

- Chinese current files with show-don't-tell findings: 94
- Main code:
  - `SHOW_DONT_TELL_MOTIVE_EXPLANATION`: 100 findings

Interpretation: the main issue is explanatory motive narration, especially sentences that tell the reader why a character knows/understands something and then spells out the causal conclusion.

## Repair strategy

### Existing output

1. Run span-level patching first for high-score chapters.
   - Safe for weak adverb clusters, time markers, and micro-expression excesses.
   - Example: `chapter-188.md` in `female-no-cp-1776303225` drops from score 96.0 to 0.0 with static span edits.

2. Do not use whole-chapter regeneration for these findings.
   - Whole rerolls risk continuity drift and new AI flavor.
   - Use local span edits or sentence-window rewrites.

3. Route anti-meta endings to last-3-to-5-sentence repair.
   - Replace summary/boundary language with one concrete scene image, action, object change, or reveal.

4. Route show-don't-tell findings to sentence-window rewrites.
   - Replace motive explanation with physical action, interrupted dialogue, prop interaction, or visible consequence.

### Future workflow

1. Keep AI-flavor detection after chapter assembly.
   - The existing `run_ai_flavor_gate` pipeline hook is the right place.

2. Add generation-side budgets to prompts.
   - The writer prompt now says to trust the reader and render meaning through action, props, dialogue, body response, and sensory change.

3. Protect dialogue in every prose-quality gate.
   - Implemented for `anti_meta_gate` and `show_dont_tell_gate` in this pass.
   - Already present in the span-level AI-flavor detector.

4. Treat findings by repair type.
   - Static patch: repeated weak adverbs, time markers, removable signposts.
   - Sentence-window rewrite: meta endings, show-don't-tell motive explanation.
   - Human/editor review: high residual score after patch, or semantic ambiguity.

5. Track per-book AI-flavor score distribution.
   - A generated book should not only pass chapter length and continuity gates; it should also have a low max score and low hit-file percentage on AI-flavor gates before export.
