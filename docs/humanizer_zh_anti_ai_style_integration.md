# Humanizer-zh anti-AI style integration

Date: 2026-06-02

## Source reviewed

- `op7418/Humanizer-zh` README and `SKILL.md`: <https://github.com/op7418/Humanizer-zh>
- The project states that it is a Chinese Claude Code skill translated from `blader/humanizer`, with utility ideas from `hardikpandya/stop-slop`.

## Transferable principles

Humanizer-zh is not a runtime library. Its reusable value is an editing rule set:

1. Delete filler and signpost phrases.
2. Break formulaic structures such as negated parallelism, triadic lists, and dramatic setup language.
3. Vary sentence rhythm.
4. Trust the reader instead of explaining the meaning of every event.
5. Replace quote-like "golden sentence" writing with concrete details.

For serialized fiction, these principles should not become whole-chapter rewrites. The safe integration shape is:

- Before generation: tell the writer to render meaning through action, props, dialogue, body response, and sensory changes.
- During generation: prohibit essay/assistant artifacts and inflated summary language.
- After chapter assembly: detect only the concrete spans or sentence patterns that match known AI-flavor rules.
- During repair: patch spans or sentence-sized windows, never reroll unrelated prose.

## Mapping into BestSeller

BestSeller already has the right surfaces:

- `src/bestseller/services/prompt_constructor.py::build_anti_slop_footer`
- `data/ai_flavor/patterns_zh.json`
- `src/bestseller/services/ai_flavor/detector.py`
- `src/bestseller/services/ai_flavor_gate.py`
- `src/bestseller/services/pipelines.py` chapter-level gate invocation

This integration adds Humanizer-zh rules to those surfaces rather than creating a parallel humanizer subsystem.

## Rule classes added

- `essay_signpost`: phrases such as "此外" and "值得一提的是".
- `inflated_significance`: patterns such as "作为……的证明" and abstract claims like "不断演变的格局".
- `promo_language`: promotional copy phrases such as "丰富的文化底蕴".
- `abstract_ai_word`: abstract AI high-frequency words such as "深入探讨".
- `negated_parallelism`: soft detection of "不仅仅是……而是……".
- `vague_attribution`: "专家认为", "观察者指出", and similar ungrounded attribution.
- `assistant_artifact`: chat residue and knowledge-cutoff disclaimers.
- `generic_positive_closer`: vague optimistic endings.

## Enforcement posture

Rules are intentionally tiered:

- Hard block plus sentence repair for assistant residue, vague attribution, inflated symbolic claims, and generic positive closers.
- Static replacement for removable essay words.
- Warn-only for patterns that can occasionally be valid in prose, such as negated parallelism and some abstract terms.
- Dialogue remains protected by the existing detector quote-range logic.

## Verification

Focused tests cover:

- Regex-based Humanizer-zh rule detection.
- Removal of assistant artifacts while preserving surrounding prose.
- Static deletion of essay signposts.
- Prompt footer injection of fiction-specific anti-AI style instructions.
