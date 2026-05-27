"""Detect whether chapter prose contains at least one signature screenshot moment."""

from __future__ import annotations

from dataclasses import dataclass
import re

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


@dataclass(frozen=True)
class SignatureHit:
    signature_type: str
    evidence: str


_APHORISM_WORDS = ("不是", "原来", "只要", "从来", "真相", "代价", "讽刺", "规矩")
_PHYSICAL_WORDS = ("手指", "嘴角", "瞳孔", "喉结", "掌心", "指节", "背脊", "冷汗")
_SENSORY_WORDS = ("冷", "热", "腥", "潮", "响", "气味", "疼", "光", "黑")
_REACTION_WORDS = ("愣住", "后退", "沉默", "发抖", "抬头", "看向", "攥紧", "屏住")


def evaluate_signature_audit(chapter_text: str) -> GateVerdict:
    hits = detect_signature_hits(chapter_text)
    findings: tuple[GateFinding, ...] = ()
    if not hits:
        findings = (
            GateFinding(
                code="SIGNATURE_TYPE_MISSING",
                severity="high",
                message="Chapter lacks a detectable signature moment.",
                repair_action=(
                    "Add at least one aphorism, vivid description, scene tableau, "
                    "reversal, detail echo, or amplified reaction beat."
                ),
            ),
        )
    return GateVerdict(
        gate_name="signature_audit",
        verdict="pass" if hits else "blocked",
        coverage=1.0 if hits else 0.0,
        findings=findings,
        metrics={
            "signature_hit_count": len(hits),
            "signature_types": [hit.signature_type for hit in hits],
        },
    )


def detect_signature_hits(chapter_text: str) -> tuple[SignatureHit, ...]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", chapter_text) if p.strip()]
    hits: list[SignatureHit] = []
    for paragraph in paragraphs:
        compact = re.sub(r"\s+", "", paragraph)
        if 8 <= len(compact) <= 30 and any(word in compact for word in _APHORISM_WORDS):
            hits.append(SignatureHit("aphorism", compact[:80]))
        if 30 <= len(compact) <= 120 and any(word in compact for word in _PHYSICAL_WORDS):
            hits.append(SignatureHit("vivid_description", compact[:100]))
        if 100 <= len(compact) <= 360 and _count_contains(compact, _SENSORY_WORDS) >= 2:
            hits.append(SignatureHit("signature_scene", compact[:120]))
        if _count_contains(compact, _REACTION_WORDS) >= 3:
            hits.append(SignatureHit("amplified_reaction", compact[:120]))
    closing = "".join(paragraphs[-2:])
    if any(word in closing for word in ("原来", "竟然", "不是")):
        hits.append(SignatureHit("reversal", closing[:120]))
    object_echo = _object_echo(chapter_text)
    if object_echo:
        hits.append(SignatureHit("detail_echo", object_echo))
    return tuple(hits)


def _count_contains(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for word in words if word in text)


def _object_echo(text: str) -> str:
    objects = ("铜钱", "罗盘", "青囊", "钥匙", "镜片", "账页", "名片", "回执")
    midpoint = max(len(text) // 2, 1)
    first = text[:midpoint]
    second = text[midpoint:]
    for obj in objects:
        if obj in first and obj in second:
            return obj
    return ""


__all__ = ["SignatureHit", "detect_signature_hits", "evaluate_signature_audit"]
