"""Chapter contract receipt — 契约声明 vs 正文实际 的确定性对账。

Why this exists
---------------
The chapter pipeline hands the writer a contract (scene cards declare
``participants``; scene contracts may declare a location), but nothing ever
reconciled what the contract *declared* against what the prose *delivered*.
A declared character can silently vanish (never appears), or appear as
furniture (named once, no action, no dialogue) — both read as cast erosion
over a long run and were previously invisible.

This module closes that loop deterministically: no LLM call, no new writer
output format. The declaration side is what the scene cards already say; the
actual side is the assembled chapter text.

Kill-power: NONE by design
--------------------------
The receipt is stamped into ``chapter.metadata_json["contract_receipt_latest"]``
and logged. It must NOT be added to ``deterministic_post_write_audit`` —
``reviews._deterministic_rewrite_violations`` forwards *every* finding of that
report (regardless of severity) into the semantic-repair channel, so adding
findings there would grant this detector kill power. New detectors earn trust
by leaving traces first (2026-08-15 定案：新检测器只挣重生和留痕).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

# ruff: noqa: RUF001, RUF002 — Chinese punctuation is intentional.
import re
from typing import Any

# Evidence that a named character is *doing* something in a sentence, not just
# being mentioned. Deliberately includes speech verbs — dialogue is activity.
# This list serves a different purpose from the opening-pressure verb list in
# ``deterministic_post_write_audit`` (evidence-of-activity vs sensory pressure),
# so it is intentionally its own constant.
_ACTIVITY_VERBS = frozenset(
    "说 道 问 答 喊 叫 骂 笑 哭 吼 念 应 唤 "
    "动 握 推 撞 撕 压 按 转 闯 抓 扣 拽 提 砸 跑 退 站 起 落 抬 摁 贴 掀 踢 踩 "
    "拔 甩 扔 接 挡 避 躲 开 关 看 望 瞥 扫 盯 听 闻 摸 探 写 刻 咬 走 来 到 伸 "
    "指 举 抱 拉 放 拿 递 收 掏 抖 点 摇".split()
)

# Dialogue markers: a sentence containing the name plus one of these counts as
# the character having a voice on the page.
_DIALOGUE_MARKERS = ("「", "」", "“", "”", '"', "：", ":")

_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")

# Keys inside scene contracts that may carry a declared location.
_LOCATION_KEY_MARKERS = ("location", "setting", "place")


@dataclass(frozen=True)
class ChapterContractReceipt:
    """Deterministic reconciliation of contract declarations vs chapter prose."""

    chapter_number: int
    declared_participants: tuple[str, ...] = ()
    missing_participants: tuple[str, ...] = ()
    silent_participants: tuple[str, ...] = ()
    matched_via: dict[str, str] = field(default_factory=dict)
    declared_locations: tuple[str, ...] = ()
    missing_locations: tuple[str, ...] = ()
    participant_coverage: float = 1.0

    @property
    def clean(self) -> bool:
        return not (
            self.missing_participants
            or self.silent_participants
            or self.missing_locations
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "declared_participants": list(self.declared_participants),
            "missing_participants": list(self.missing_participants),
            "silent_participants": list(self.silent_participants),
            "matched_via": dict(self.matched_via),
            "declared_locations": list(self.declared_locations),
            "missing_locations": list(self.missing_locations),
            "participant_coverage": round(self.participant_coverage, 3),
            "clean": self.clean,
        }


def build_chapter_contract_receipt(
    *,
    chapter_text: str,
    chapter_number: int,
    scenes: Sequence[Any] = (),
) -> ChapterContractReceipt:
    """Reconcile declared participants/locations against the chapter prose.

    Pure and read-only. Empty text or no declarations degrade to a clean
    receipt (coverage 1.0) rather than manufacturing findings.
    """

    text = chapter_text or ""
    declared_participants = _declared_participants(scenes)
    declared_locations = _declared_locations(scenes)

    if not text or (not declared_participants and not declared_locations):
        return ChapterContractReceipt(
            chapter_number=int(chapter_number),
            declared_participants=declared_participants,
            declared_locations=declared_locations,
        )

    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]

    missing: list[str] = []
    silent: list[str] = []
    matched_via: dict[str, str] = {}
    for name in declared_participants:
        surface = _match_surface(name, text)
        if surface is None:
            missing.append(name)
            continue
        if surface == name:
            matched_via[name] = "full"
        elif surface in {c.strip() for c in _ALIAS_SPLIT.split(name)}:
            matched_via[name] = "alias"
        else:
            matched_via[name] = "given_name"
        if not _has_activity_evidence(surface, sentences):
            silent.append(name)

    present = len(declared_participants) - len(missing)
    coverage = (
        present / len(declared_participants) if declared_participants else 1.0
    )

    missing_locations = tuple(
        loc for loc in declared_locations if loc not in text
    )

    return ChapterContractReceipt(
        chapter_number=int(chapter_number),
        declared_participants=declared_participants,
        missing_participants=tuple(missing),
        silent_participants=tuple(silent),
        matched_via=matched_via,
        declared_locations=declared_locations,
        missing_locations=missing_locations,
        participant_coverage=coverage,
    )


def _declared_participants(scenes: Sequence[Any]) -> tuple[str, ...]:
    seen: list[str] = []
    for scene in scenes or ():
        for raw in getattr(scene, "participants", None) or ():
            name = str(raw or "").strip()
            if name and name not in seen:
                seen.append(name)
    return tuple(seen)


def _declared_locations(scenes: Sequence[Any]) -> tuple[str, ...]:
    seen: list[str] = []
    for scene in scenes or ():
        metadata = getattr(scene, "metadata_json", None)
        if not isinstance(metadata, Mapping):
            continue
        for contract_key in ("scene_contract", "methodology_contract"):
            contract = metadata.get(contract_key)
            if not isinstance(contract, Mapping):
                continue
            for key, value in contract.items():
                normalized_key = str(key).casefold()
                if not any(marker in normalized_key for marker in _LOCATION_KEY_MARKERS):
                    continue
                if isinstance(value, str) and value.strip():
                    loc = value.strip()
                    if loc not in seen:
                        seen.append(loc)
    return tuple(seen)


_ALIAS_SPLIT = re.compile(r"[()（）/、·]+")


def _match_surface(name: str, text: str) -> str | None:
    """Return the surface form of ``name`` found in ``text``, or None.

    Declared names may carry alias annotations — ``沈絮(阿缨)`` — so the
    declaration is first split into candidate surfaces. Chinese prose also
    frequently drops the surname (王小明 → 小明), so for CJK candidates of
    3+ chars the given-name suffix is accepted as a fallback; the receipt
    records that a non-full form matched.
    """

    if name in text:
        return name
    candidates = [c for c in _ALIAS_SPLIT.split(name) if len(c.strip()) >= 2]
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate in text:
            return candidate
    for candidate in candidates:
        candidate = candidate.strip()
        if len(candidate) >= 3 and re.fullmatch(r"[一-鿿]+", candidate):
            given = candidate[1:]
            if len(given) >= 2 and given in text:
                return given
    return None


def _has_activity_evidence(surface: str, sentences: Sequence[str]) -> bool:
    for sentence in sentences:
        if surface not in sentence:
            continue
        if any(marker in sentence for marker in _DIALOGUE_MARKERS):
            return True
        if any(verb in sentence for verb in _ACTIVITY_VERBS):
            return True
    return False


__all__ = [
    "ChapterContractReceipt",
    "build_chapter_contract_receipt",
]
