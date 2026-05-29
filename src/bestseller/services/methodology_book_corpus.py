"""Load and audit distilled writing-book methodology material."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

import yaml

from bestseller.services.methodology_book_taxonomy import (
    BookMethodologyTaxonomy,
    BookMethodologyVerifiability,
    normalized_claim_key,
    taxonomy_for_card,
)
from bestseller.services.methodology_cards import MethodologyCard, load_methodology_cards


@dataclass(frozen=True)
class BookMethodologyCandidateSignal:
    """Extra selection signals retained in the review JSONL candidates."""

    card_id: str
    confidence: float | None = None
    alignment_terms: tuple[str, ...] = ()
    operating_steps: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class BookMethodologyCorpusCard:
    """One card plus audit metadata used before runtime integration."""

    card: MethodologyCard
    source_key: str
    taxonomy: BookMethodologyTaxonomy
    confidence: float | None = None
    alignment_terms: tuple[str, ...] = ()
    operating_steps: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    duplicate_key: str = ""

    @property
    def card_id(self) -> str:
        return self.card.id

    def to_inventory_row(self) -> dict[str, Any]:
        return {
            "card_id": self.card.id,
            "source_key": self.source_key,
            "source_ids": list(self.card.source_ids),
            "category": self.card.category,
            "canonical_domain": self.taxonomy.domain,
            "scope": list(self.card.scope),
            "stage": list(self.card.stage),
            "verifiability": self.taxonomy.verifiability,
            "verifiability_reason": self.taxonomy.reason,
            "priority_lane": self.taxonomy.priority_lane.value,
            "confidence": self.confidence,
            "framework_bindings": list(self.card.framework_bindings),
            "gate_bindings": [
                {"gate": binding.gate, "default_mode": binding.default_mode}
                for binding in self.card.gate_bindings
            ],
            "required_contract_fields": list(self.card.required_contract_fields),
            "alignment_terms": list(self.alignment_terms),
            "duplicate_key": self.duplicate_key,
        }


@dataclass(frozen=True)
class BookMethodologyInventory:
    """Aggregate audit report for distilled book methodology material."""

    total_cards: int
    source_counts: dict[str, int]
    domain_counts: dict[str, int]
    category_counts: dict[str, int]
    stage_counts: dict[str, int]
    scope_counts: dict[str, int]
    verifiability_counts: dict[str, int]
    low_confidence_cards: tuple[str, ...] = ()
    duplicate_clusters: dict[str, list[str]] = field(default_factory=dict)
    prompt_cost_estimate: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cards": self.total_cards,
            "source_counts": self.source_counts,
            "domain_counts": self.domain_counts,
            "category_counts": self.category_counts,
            "stage_counts": self.stage_counts,
            "scope_counts": self.scope_counts,
            "verifiability_counts": self.verifiability_counts,
            "low_confidence_cards": list(self.low_confidence_cards),
            "duplicate_clusters": self.duplicate_clusters,
            "prompt_cost_estimate": self.prompt_cost_estimate,
        }


@dataclass(frozen=True)
class BookMethodologyCorpus:
    """Loaded book methodology corpus."""

    cards: tuple[BookMethodologyCorpusCard, ...]

    def inventory(self, *, low_confidence_threshold: float = 0.7) -> BookMethodologyInventory:
        source_counts: Counter[str] = Counter()
        domain_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        stage_counts: Counter[str] = Counter()
        scope_counts: Counter[str] = Counter()
        verifiability_counts: Counter[str] = Counter()
        duplicate_groups: dict[str, list[str]] = defaultdict(list)
        low_confidence: list[str] = []

        for item in self.cards:
            source_counts[item.source_key] += 1
            domain_counts[item.taxonomy.domain] += 1
            category_counts[str(item.card.category)] += 1
            verifiability_counts[item.taxonomy.verifiability] += 1
            for stage in item.card.stage:
                stage_counts[str(stage)] += 1
            for scope in item.card.scope:
                scope_counts[str(scope)] += 1
            duplicate_groups[item.duplicate_key].append(item.card.id)
            if item.confidence is not None and item.confidence < low_confidence_threshold:
                low_confidence.append(item.card.id)

        duplicate_clusters = {
            key: ids for key, ids in duplicate_groups.items() if key and len(ids) > 1
        }
        return BookMethodologyInventory(
            total_cards=len(self.cards),
            source_counts=_sorted_counter(source_counts),
            domain_counts=_sorted_counter(domain_counts),
            category_counts=_sorted_counter(category_counts),
            stage_counts=_sorted_counter(stage_counts),
            scope_counts=_sorted_counter(scope_counts),
            verifiability_counts=_sorted_counter(verifiability_counts),
            low_confidence_cards=tuple(sorted(low_confidence)),
            duplicate_clusters=dict(sorted(duplicate_clusters.items())),
            prompt_cost_estimate=estimate_prompt_cost(self.cards),
        )

    def rows(self) -> list[dict[str, Any]]:
        return [item.to_inventory_row() for item in self.cards]

    def select_core_candidates(
        self,
        *,
        per_domain_limit: int = 12,
        min_confidence: float = 0.8,
        include_verifiability: tuple[BookMethodologyVerifiability, ...] = ("strict", "heuristic"),
    ) -> tuple[BookMethodologyCorpusCard, ...]:
        """Select a balanced, observable first-pass core deck candidate set."""

        domain_rows: dict[str, list[BookMethodologyCorpusCard]] = defaultdict(list)
        for item in self.cards:
            if item.taxonomy.verifiability not in include_verifiability:
                continue
            if item.confidence is not None and item.confidence < min_confidence:
                continue
            domain_rows[item.taxonomy.domain].append(item)

        selected: list[BookMethodologyCorpusCard] = []
        for _domain, rows in sorted(domain_rows.items()):
            picked_keys: set[str] = set()
            source_counts: Counter[str] = Counter()
            rows_sorted = sorted(
                rows,
                key=lambda item: (
                    item.taxonomy.verifiability != "strict",
                    source_counts[item.source_key],
                    -(item.confidence or 0.0),
                    item.card.id,
                ),
            )
            for item in rows_sorted:
                if item.duplicate_key in picked_keys:
                    continue
                picked_keys.add(item.duplicate_key)
                source_counts[item.source_key] += 1
                selected.append(item)
                domain_selected = sum(
                    1 for picked in selected if picked.taxonomy.domain == item.taxonomy.domain
                )
                if domain_selected >= per_domain_limit:
                    break
        return tuple(selected)


def default_methodology_books_root() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "methodology_books"


def load_book_methodology_corpus(root: Path | None = None) -> BookMethodologyCorpus:
    """Load all reviewed writing-book card decks from ``data/methodology_books``."""

    base = root or default_methodology_books_root()
    cards: list[BookMethodologyCorpusCard] = []
    for deck_path in sorted(base.glob("source-*/methodology_cards.review.yaml")):
        source_key = deck_path.parent.name
        signals = _load_candidate_signals(deck_path.parent / "methodology_candidates.review.jsonl")
        deck = load_methodology_cards(deck_path)
        for card in deck.cards:
            signal = signals.get(card.id)
            alignment_terms = signal.alignment_terms if signal else ()
            taxonomy = taxonomy_for_card(card, alignment_terms=alignment_terms)
            cards.append(
                BookMethodologyCorpusCard(
                    card=card,
                    source_key=source_key,
                    taxonomy=taxonomy,
                    confidence=signal.confidence if signal else None,
                    alignment_terms=alignment_terms,
                    operating_steps=signal.operating_steps if signal else (),
                    conflicts_with=signal.conflicts_with if signal else (),
                    duplicate_key=_claim_cluster_id(card.core_claim),
                )
            )
    return BookMethodologyCorpus(cards=tuple(cards))


def write_book_methodology_analysis(
    *,
    root: Path | None = None,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Write inventory JSON and duplicate/domain cluster YAML for review."""

    base = root or default_methodology_books_root()
    out_dir = output_dir or base / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus = load_book_methodology_corpus(base)
    inventory = corpus.inventory()

    inventory_path = out_dir / "material_inventory.json"
    inventory_payload = {
        **inventory.to_dict(),
        "rows": corpus.rows(),
    }
    inventory_path.write_text(
        json.dumps(inventory_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    clusters_path = out_dir / "domain_clusters.yaml"
    clusters_path.write_text(
        yaml.safe_dump(
            _domain_cluster_payload(corpus),
            allow_unicode=True,
            sort_keys=True,
            width=100,
        ),
        encoding="utf-8",
    )
    return inventory_path, clusters_path


def build_core_deck_payload(corpus: BookMethodologyCorpus) -> dict[str, Any]:
    """Build a sanitized MethodologyCardDeck payload from balanced core candidates."""

    cards: list[dict[str, Any]] = []
    for item in corpus.select_core_candidates():
        card = item.card
        cards.append(
            {
                "id": core_book_methodology_card_id(card.id),
                "source_ids": list(card.source_ids),
                "title": _generic_card_title(item),
                "category": card.category,
                "scope": list(card.scope),
                "stage": list(card.stage),
                "core_claim": sanitize_book_methodology_text(card.core_claim),
                "anti_patterns": [
                    sanitize_book_methodology_text(text) for text in card.anti_patterns[:4]
                ],
                "required_contract_fields": list(card.required_contract_fields),
                "framework_bindings": list(card.framework_bindings),
                "gate_bindings": [
                    {
                        "gate": binding.gate,
                        "default_mode": (
                            "warn"
                            if item.taxonomy.verifiability == "strict"
                            else "advisory"
                        ),
                    }
                    for binding in card.gate_bindings[:2]
                ],
                "maturity": "draft",
            }
        )
    return {"cards": cards}


def build_core_profile_payload(
    corpus: BookMethodologyCorpus,
    *,
    profile_id: str = "books_core_v1",
) -> dict[str, Any]:
    """Build a MethodologyProfile payload for the generated core deck."""

    cards: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(corpus.select_core_candidates()):
        card_id = core_book_methodology_card_id(item.card.id)
        cards[card_id] = {
            "enabled": True,
            "gate_mode": "warn" if item.taxonomy.verifiability == "strict" else "advisory",
            "priority": 10 + index,
            "strict_when": [],
        }
    return {
        "profile_id": profile_id,
        "title": "Writing books core methodology",
        "source_set_id": "writing_methodology_books",
        "card_deck": "data/methodology_sources/books_core/cards.yaml",
        "default_mode": "advisory",
        "max_prompt_cards": 6,
        "pending_sources": [],
        "cards": cards,
    }


def estimate_prompt_cost(cards: Iterable[BookMethodologyCorpusCard]) -> dict[str, int]:
    """Estimate prompt cost for selected-card injections without an LLM call."""

    rows = list(cards)
    average_chars = 0
    if rows:
        total_chars = sum(
            len(item.card.core_claim) + 40 * len(item.card.required_contract_fields)
            for item in rows
        )
        average_chars = round(total_chars / len(rows))
    return {
        "average_card_chars": average_chars,
        "three_card_injection_tokens": _estimate_tokens(average_chars * 3),
        "eight_card_injection_tokens": _estimate_tokens(average_chars * 8),
        "draft_review_roundtrip_tokens": _estimate_tokens(average_chars * 6),
        "estimated_1000_chapter_three_card_tokens": _estimate_tokens(average_chars * 3) * 1000,
    }


def _domain_cluster_payload(corpus: BookMethodologyCorpus) -> dict[str, Any]:
    domain_rows: dict[str, list[BookMethodologyCorpusCard]] = defaultdict(list)
    for item in corpus.cards:
        domain_rows[item.taxonomy.domain].append(item)

    payload: dict[str, Any] = {}
    for domain, rows in sorted(domain_rows.items()):
        rows_sorted = sorted(
            rows,
            key=lambda item: (
                item.taxonomy.verifiability != "strict",
                -(item.confidence or 0.0),
                item.card.id,
            ),
        )
        payload[domain] = {
            "count": len(rows),
            "verifiability": dict(Counter(item.taxonomy.verifiability for item in rows)),
            "representatives": [
                {
                    "card_id": item.card.id,
                    "source_key": item.source_key,
                    "confidence": item.confidence,
                    "verifiability": item.taxonomy.verifiability,
                }
                for item in rows_sorted[:12]
            ],
        }
    return payload


def _load_candidate_signals(path: Path) -> dict[str, BookMethodologyCandidateSignal]:
    if not path.exists():
        return {}
    signals: dict[str, BookMethodologyCandidateSignal] = {}
    for row in _iter_jsonl(path):
        candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else [row]
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            card_id = _candidate_card_id(candidate)
            if not card_id:
                continue
            signals[card_id] = BookMethodologyCandidateSignal(
                card_id=card_id,
                confidence=_optional_float(candidate.get("confidence")),
                alignment_terms=_string_tuple(candidate.get("alignment_terms")),
                operating_steps=_string_tuple(candidate.get("operating_steps")),
                conflicts_with=_string_tuple(candidate.get("conflicts_with")),
            )
    return signals


def _candidate_card_id(candidate: Mapping[str, Any]) -> str:
    source_id = str(candidate.get("source_id") or "").strip()
    section_id = str(candidate.get("section_id") or "").strip()
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if not source_id or not section_id or not candidate_id:
        return ""
    return f"writing_books.{source_id}.{section_id}.{candidate_id}"


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            yield payload


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _estimate_tokens(char_count: int) -> int:
    return max(0, round(char_count / 1.8))


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _claim_cluster_id(core_claim: str) -> str:
    normalized = normalized_claim_key(core_claim)
    if not normalized:
        return ""
    return f"claim:{sha256(normalized.encode('utf-8')).hexdigest()[:16]}"


def sanitize_book_methodology_text(text: str) -> str:
    """Remove source-title-like phrasing from runtime-safe methodology text."""

    cleaned = text.strip()
    cleaned = _SOURCE_NAMED_METHOD_RE.sub("该递进式写作方法", cleaned)
    cleaned = _IN_SOURCE_METHOD_RE.sub("在该递进式写作方法中,", cleaned)
    return cleaned


def core_book_methodology_card_id(card_id: str) -> str:
    """Map a raw distilled-card id to the runtime core-deck id."""

    return card_id.replace("writing_books.", "books_core.", 1)


def _generic_card_title(item: BookMethodologyCorpusCard) -> str:
    suffix = item.card.id.rsplit(".", 1)[-1].replace("_", "-")[:42]
    return f"{item.taxonomy.domain} / {item.taxonomy.verifiability} / {suffix}"


_SOURCE_NAMED_METHOD_RE = re.compile(r"[\u4e00-\u9fff]{1,12}写作法")
_IN_SOURCE_METHOD_RE = re.compile(
    r"在[^\uff0c\u3002]{1,24}写作方法?中[\uff0c,]?"
)


__all__ = [
    "BookMethodologyCandidateSignal",
    "BookMethodologyCorpus",
    "BookMethodologyCorpusCard",
    "BookMethodologyInventory",
    "build_core_deck_payload",
    "build_core_profile_payload",
    "core_book_methodology_card_id",
    "default_methodology_books_root",
    "estimate_prompt_cost",
    "load_book_methodology_corpus",
    "sanitize_book_methodology_text",
    "write_book_methodology_analysis",
]
