"""Predicate-phrase clusters must be advisory: detect/score but NEVER delete.

Found via real production-gate validation (2026-06-13): clusters whose members
are predicate phrases (瞳孔一缩 / 心头一紧 / 忽然明白 / 震惊) carried an
empty-string ``[""]`` suggestion, which the patcher treats as "delete the
phrase". On a real chapter that strips a load-bearing verb and breaks the
sentence — "他忽然意识到一件事。" → "他一件事。". These clusters now set
``advisory_only`` so the detector emits zero-suggestion spans and the patcher
leaves the prose untouched. Modifier clusters (缓缓 / 其实) keep deletion.
"""

from __future__ import annotations

from bestseller.services.ai_flavor.detector import detect
from bestseller.services.ai_flavor.patcher import apply_patches


def _gen_spans(text: str):
    return detect(text, language="zh").spans


def test_epiphany_cluster_detected_but_not_deleted() -> None:
    text = (
        "他忽然意识到一件事。走到巷口，他忽然明白了那句话的意思。"
        "看清那张脸，他突然明白整件事的来龙去脉。"
    )
    spans = _gen_spans(text)
    epi = [s for s in spans if s.category == "epiphany_announcement"]
    assert epi, "epiphany over-reliance should still be detected"
    assert all(s.suggestions == () for s in epi), "advisory: no delete suggestion"
    patched = apply_patches(text, spans, language="zh")
    # The verb phrase is never stripped — sentence stays intact.
    assert "他一件事" not in patched.patched_text
    assert "忽然意识到一件事" in patched.patched_text


def test_body_micro_action_not_deleted() -> None:
    text = "他瞳孔一缩。她心头一紧。他眉心一皱。他喉结一滚。"
    spans = _gen_spans(text)
    patched = apply_patches(text, spans, language="zh")
    # No "他。" / "她。" fragments left behind by a strip.
    assert "瞳孔一缩" in patched.patched_text


def test_emotion_label_not_deleted() -> None:
    text = "他震惊地看着她。心里一阵恐惧。他强压住愤怒。她满脸悲伤。错愕。"
    spans = _gen_spans(text)
    patched = apply_patches(text, spans, language="zh")
    assert "他震惊地看着她" in patched.patched_text


def test_modifier_cluster_still_deletes() -> None:
    # weak_adverb (缓缓/轻轻/深深) is a *modifier* — deleting it stays grammatical
    # and remains enabled.
    text = "他缓缓站起来，缓缓走过去，缓缓抬起头，缓缓看了一眼。"
    spans = _gen_spans(text)
    weak = [s for s in spans if s.category == "weak_adverb"]
    assert weak, "weak_adverb repetition should be detected"
    assert any(s.suggestions for s in weak), "modifier deletion stays enabled"
