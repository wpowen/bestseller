"""Canonical taxonomy for distilled writing-book methodology cards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Literal

from bestseller.services.methodology_cards import MethodologyCard

BookMethodologyDomain = Literal[
    "character_arc",
    "dialogue_subtext",
    "opening_retention",
    "pov_prose",
    "premise_outline",
    "project_health",
    "revision_loop",
    "scene_causality",
    "setup_payoff",
    "worldview_theme",
]

BookMethodologyVerifiability = Literal["advisory_only", "heuristic", "strict"]


class BookMethodologyPriority(StrEnum):
    """Conflict priority lanes for methodology sources."""

    PLATFORM_REQUIRED = "platform_required"
    WRITING_METHODOLOGY = "writing_methodology.yaml"
    BOOK_CORE_DECK = "book_core_deck"
    BOOK_ADVISORY = "book_advisory"


CATEGORY_DOMAIN_MAP: dict[str, BookMethodologyDomain] = {
    "action_scene": "scene_causality",
    "character": "character_arc",
    "debt": "character_arc",
    "dialogue": "dialogue_subtext",
    "emotion_beat": "character_arc",
    "foreshadowing": "setup_payoff",
    "longform_control": "project_health",
    "mainline": "premise_outline",
    "opening": "opening_retention",
    "outline": "premise_outline",
    "pov": "pov_prose",
    "progression": "scene_causality",
    "prose_style": "pov_prose",
    "relationship": "character_arc",
    "revision": "revision_loop",
    "scene_design": "scene_causality",
    "surface_subtext": "dialogue_subtext",
    "theme": "worldview_theme",
    "timeline": "setup_payoff",
    "worldview": "worldview_theme",
}

ALIGNMENT_DOMAIN_HINTS: tuple[tuple[str, BookMethodologyDomain], ...] = (
    ("setup", "setup_payoff"),
    ("payoff", "setup_payoff"),
    ("foreshadow", "setup_payoff"),
    ("hook", "opening_retention"),
    ("opening", "opening_retention"),
    ("goal", "scene_causality"),
    ("obstacle", "scene_causality"),
    ("scene", "scene_causality"),
    ("sequel", "scene_causality"),
    ("action", "scene_causality"),
    ("reaction", "scene_causality"),
    ("want", "character_arc"),
    ("need", "character_arc"),
    ("character", "character_arc"),
    ("emotion", "character_arc"),
    ("dialogue", "dialogue_subtext"),
    ("subtext", "dialogue_subtext"),
    ("pov", "pov_prose"),
    ("show", "pov_prose"),
    ("style", "pov_prose"),
    ("outline", "premise_outline"),
    ("snowflake", "premise_outline"),
    ("theme", "worldview_theme"),
    ("world", "worldview_theme"),
    ("revision", "revision_loop"),
    ("repair", "revision_loop"),
    ("timeline", "setup_payoff"),
)

STRUCTURAL_FIELD_HINTS: frozenset[str] = frozenset(
    {
        "action",
        "antagonist",
        "chapter_goal",
        "choice",
        "closing_hook",
        "conflict",
        "cost",
        "evidence",
        "goal",
        "hook",
        "obstacle",
        "payoff",
        "pressure_stack",
        "protagonist",
        "result",
        "scene_breakdown",
        "scene_emotion_goal",
        "stakes",
        "timeline",
    }
)

ADVISORY_FIELD_HINTS: frozenset[str] = frozenset(
    {
        "creative_impulse",
        "flow_state",
        "intuition",
        "method_choice",
        "method_evaluation_criteria",
        "progress_metrics",
        "writer_engagement",
        "writer_preferences",
        "writing_process",
    }
)

STRICT_GATE_HINTS: tuple[str, ...] = (
    "completeness",
    "consistency",
    "contract",
    "existence",
    "ledger",
    "lock",
    "required",
    "validation",
)

HEURISTIC_GATE_HINTS: tuple[str, ...] = (
    "audience",
    "emotion",
    "impact",
    "resonance",
    "style",
)


@dataclass(frozen=True)
class BookMethodologyTaxonomy:
    """Normalized routing metadata for one book-methodology card."""

    domain: BookMethodologyDomain
    verifiability: BookMethodologyVerifiability
    priority_lane: BookMethodologyPriority
    reason: str


def canonical_domain_for_card(
    card: MethodologyCard,
    *,
    alignment_terms: tuple[str, ...] = (),
) -> BookMethodologyDomain:
    """Return the canonical runtime domain for a distilled book card."""

    category_domain = CATEGORY_DOMAIN_MAP.get(str(card.category))
    if category_domain:
        return category_domain

    haystack = " ".join([*alignment_terms, *card.framework_bindings, card.title]).lower()
    for needle, domain in ALIGNMENT_DOMAIN_HINTS:
        if needle in haystack:
            return domain
    return "scene_causality"


def infer_verifiability(
    card: MethodologyCard,
    *,
    alignment_terms: tuple[str, ...] = (),
) -> tuple[BookMethodologyVerifiability, str]:
    """Infer whether a card can be measured strictly, heuristically, or only advised."""

    fields = tuple(_normalize_token(field) for field in card.required_contract_fields)
    gates = tuple(binding.gate.lower() for binding in card.gate_bindings)
    bindings = tuple(binding.lower() for binding in card.framework_bindings)
    haystack = " ".join([*fields, *gates, *bindings, *alignment_terms]).lower()

    if not fields and not gates:
        return "advisory_only", "no required fields or gate bindings"

    if fields and all(field in ADVISORY_FIELD_HINTS for field in fields):
        return "advisory_only", "required fields describe author process, not story evidence"

    if any(hint in haystack for hint in STRICT_GATE_HINTS):
        return "strict", "gate or binding exposes structural validation hints"

    if any(field in STRUCTURAL_FIELD_HINTS for field in fields):
        return "strict", "required fields map to stable story contract fields"

    if any(hint in haystack for hint in HEURISTIC_GATE_HINTS):
        return "heuristic", "requires qualitative judge signal"

    if fields:
        return "heuristic", "has required fields but no deterministic checker hint"

    return "advisory_only", "only broad framework bindings are present"


def taxonomy_for_card(
    card: MethodologyCard,
    *,
    alignment_terms: tuple[str, ...] = (),
    core_deck: bool = False,
) -> BookMethodologyTaxonomy:
    """Build normalized taxonomy metadata for one book card."""

    domain = canonical_domain_for_card(card, alignment_terms=alignment_terms)
    verifiability, reason = infer_verifiability(card, alignment_terms=alignment_terms)
    priority = (
        BookMethodologyPriority.BOOK_CORE_DECK
        if core_deck and verifiability != "advisory_only"
        else BookMethodologyPriority.BOOK_ADVISORY
    )
    return BookMethodologyTaxonomy(
        domain=domain,
        verifiability=verifiability,
        priority_lane=priority,
        reason=reason,
    )


def normalized_claim_key(text: str) -> str:
    """Normalize a card claim into a stable duplicate-clustering key."""

    normalized = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", " ", text.lower()).strip()
    tokens = [
        _normalize_claim_token(token)
        for token in normalized.split()
        if token not in _STOP_WORDS
    ]
    return " ".join(tokens[:18])


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")


def _normalize_claim_token(value: str) -> str:
    if len(value) > 4 and value.endswith("s"):
        return value[:-1]
    return value


_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "be",
        "by",
        "can",
        "for",
        "in",
        "is",
        "of",
        "or",
        "the",
        "to",
        "with",
        "writing",
        "story",
        "should",
        "novel",
    }
)


__all__ = [
    "BookMethodologyDomain",
    "BookMethodologyPriority",
    "BookMethodologyTaxonomy",
    "BookMethodologyVerifiability",
    "canonical_domain_for_card",
    "infer_verifiability",
    "normalized_claim_key",
    "taxonomy_for_card",
]
