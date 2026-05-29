"""Runtime selector for distilled writing-book methodology cards."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bestseller.services.methodology_book_corpus import (
    BookMethodologyCorpus,
    BookMethodologyCorpusCard,
    core_book_methodology_card_id,
    default_methodology_books_root,
    load_book_methodology_corpus,
    sanitize_book_methodology_text,
)
from bestseller.services.methodology_book_taxonomy import (
    BookMethodologyDomain,
    BookMethodologyVerifiability,
)

STAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "conception": ("planning",),
    "outline_book": ("planning",),
    "outline_volume": ("planning",),
    "outline_chapter": ("planning", "review"),
    "prose_scene": ("drafting",),
    "review": ("review",),
    "repair": ("repair", "revision", "review"),
    "health": ("health", "review"),
}

SCOPE_ALIASES: dict[str, tuple[str, ...]] = {
    "book": ("book",),
    "volume": ("volume", "book"),
    "chapter": ("chapter", "book"),
    "scene": ("scene", "chapter"),
    "project_health": ("project_health", "book"),
}

STAGE_DOMAIN_BIAS: dict[str, tuple[BookMethodologyDomain, ...]] = {
    "conception": ("premise_outline", "character_arc", "worldview_theme"),
    "outline_book": ("premise_outline", "character_arc", "setup_payoff", "project_health"),
    "outline_volume": ("setup_payoff", "scene_causality", "project_health"),
    "outline_chapter": ("scene_causality", "setup_payoff", "character_arc"),
    "prose_scene": ("scene_causality", "pov_prose", "dialogue_subtext", "character_arc"),
    "review": ("scene_causality", "pov_prose", "character_arc", "revision_loop"),
    "repair": ("revision_loop", "scene_causality", "pov_prose", "dialogue_subtext"),
    "health": ("project_health", "setup_payoff", "character_arc"),
}

QUALITY_METRIC_DOMAIN_REPAIR: dict[str, tuple[BookMethodologyDomain, ...]] = {
    "scene_causality_score": ("scene_causality",),
    "scene_causality_completeness": ("scene_causality",),
    "setup_payoff_score": ("setup_payoff",),
    "setup_payoff_closed_count": ("setup_payoff",),
    "hook_ledger_closure": ("setup_payoff",),
    "payoff_density": ("setup_payoff", "scene_causality"),
    "pov_stability_score": ("pov_prose",),
    "pov_distance_drift_ratio": ("pov_prose",),
    "dialogue_ratio": ("dialogue_subtext",),
    "dialogue_subtext_score": ("dialogue_subtext",),
    "ending_hook_score": ("setup_payoff", "opening_retention"),
    "character_want_need_coverage": ("character_arc",),
    "emotional_movement": ("character_arc",),
    "anti_meta_leak_score": ("pov_prose", "revision_loop"),
    "repair_trigger_rate": ("revision_loop",),
}

LOWER_IS_BETTER_METRICS = {
    "pov_distance_drift_ratio",
    "repair_trigger_rate",
}

DEFAULT_METRIC_THRESHOLD = 0.70


@dataclass(frozen=True)
class BookMethodologySelectionContext:
    """Selector input used by planner, draft, review, and repair flows."""

    stage: str
    scope: str
    chapter_no: int | None = None
    chapter_position: str | None = None
    category_intent: tuple[BookMethodologyDomain, ...] = ()
    project_context: Mapping[str, Any] | None = None
    max_cards: int = 6
    token_budget: int = 900


@dataclass(frozen=True)
class SelectedBookMethodologyCard:
    """Runtime-safe selected card with lineage and application hints."""

    card_id: str
    raw_card_id: str
    source_key: str
    canonical_domain: BookMethodologyDomain
    verifiability: BookMethodologyVerifiability
    confidence: float | None
    core_claim: str
    required_contract_fields: tuple[str, ...]
    framework_bindings: tuple[str, ...]
    application_hint: str
    why_selected: str

    def to_application(self, *, node_path: str) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "profile_id": "books_core_v1",
            "source_card_id": self.raw_card_id,
            "scope": node_path.split(".", 1)[0],
            "stage": ["planning", "drafting", "review"],
            "node_path": node_path,
            "required_contract_fields": list(self.required_contract_fields),
            "evidence_fields": _evidence_fields_for_node(node_path, self.required_contract_fields),
            "gate": f"book_methodology_{self.canonical_domain}",
            "mode": "audit_only" if self.verifiability == "strict" else "advisory",
            "measurement": ["book_methodology_selector", "chapter_llm_commercial_judge"],
        }


@dataclass(frozen=True)
class BookMethodologySelection:
    """Selector result with prompt block and lineage."""

    cards: tuple[SelectedBookMethodologyCard, ...]
    estimated_tokens: int
    strategy_domains: tuple[BookMethodologyDomain, ...] = ()
    deficit_domains: tuple[BookMethodologyDomain, ...] = ()

    @property
    def card_ids(self) -> tuple[str, ...]:
        return tuple(card.card_id for card in self.cards)

    def render_prompt_block(self, *, language: str = "zh-CN") -> str:
        if not self.cards:
            return ""
        is_en = str(language or "").lower().startswith("en")
        heading = (
            "Writing-book methodology cards"
            if is_en
            else "书籍方法论选卡(本章/本场必须优先落地)"
        )
        guard = (
            "Internal constraints only. Never print these card ids, labels, or fields in prose."
            if is_en
            else "仅作为内部执行约束: 正文中禁止出现卡片ID、方法论术语、字段名或结构标签。"
        )
        lines = [heading, guard]
        for card in self.cards:
            if is_en:
                line = (
                    f"- {card.card_id} [{card.canonical_domain}/{card.verifiability}]: "
                    f"{card.core_claim} Apply to: {card.application_hint}."
                )
            else:
                line = (
                    f"- {card.card_id} [{card.canonical_domain}/{card.verifiability}]: "
                    f"{card.core_claim} 落点: {card.application_hint}。"
                )
            if card.required_contract_fields:
                fields = ", ".join(card.required_contract_fields[:5])
                line += f" contract fields: {fields}."
            lines.append(line)
        return "\n".join(lines)


def select_book_methodology_cards(
    context: BookMethodologySelectionContext,
    *,
    corpus: BookMethodologyCorpus | None = None,
    root: Path | None = None,
) -> BookMethodologySelection:
    """Select a small, traceable set of book-methodology cards."""

    active_corpus = corpus or load_book_methodology_corpus(root or default_methodology_books_root())
    stage_values = STAGE_ALIASES.get(context.stage, (context.stage,))
    scope_values = SCOPE_ALIASES.get(context.scope, (context.scope,))
    domain_bias = context.category_intent or STAGE_DOMAIN_BIAS.get(context.stage, ())
    strategy_domains, deficit_domains = _strategy_domain_plan(context)
    if context.category_intent:
        strategy_domains = tuple(dict.fromkeys((*context.category_intent, *strategy_domains)))
    domain_bias = tuple(dict.fromkeys((*strategy_domains, *domain_bias)))
    ranked = _rank_candidates(
        active_corpus.cards,
        stage_values=stage_values,
        scope_values=scope_values,
        domain_bias=domain_bias,
        deficit_domains=deficit_domains,
        context=context,
    )
    selected: list[SelectedBookMethodologyCard] = []
    source_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    used_duplicate_keys: set[str] = set()
    used_tokens = 0

    for required_domain in deficit_domains:
        match = next(
            (
                row
                for row in ranked
                if row[1].taxonomy.domain == required_domain
                and row[1].duplicate_key not in used_duplicate_keys
            ),
            None,
        )
        if match is not None:
            _try_add_selected_card(
                selected,
                match[1],
                reason=match[2],
                source_counts=source_counts,
                domain_counts=domain_counts,
                used_duplicate_keys=used_duplicate_keys,
                used_tokens_ref=[used_tokens],
                token_budget=context.token_budget,
                max_cards=context.max_cards,
            )
            used_tokens = sum(_estimate_tokens(card.core_claim) + 30 for card in selected)

    for _score, item, reason in ranked:
        if item.duplicate_key in used_duplicate_keys:
            continue
        if source_counts[item.source_key] >= 2:
            continue
        if domain_counts[item.taxonomy.domain] >= 2:
            continue
        selected_card = _selected_card(item, reason=reason)
        tokens = _estimate_tokens(selected_card.core_claim) + 30
        if selected and used_tokens + tokens > context.token_budget:
            continue
        selected.append(selected_card)
        used_duplicate_keys.add(item.duplicate_key)
        source_counts[item.source_key] += 1
        domain_counts[item.taxonomy.domain] += 1
        used_tokens += tokens
        if len(selected) >= context.max_cards:
            break

    return BookMethodologySelection(
        cards=tuple(selected),
        estimated_tokens=used_tokens,
        strategy_domains=strategy_domains,
        deficit_domains=deficit_domains,
    )


def render_book_methodology_block(
    *,
    stage: str,
    scope: str,
    language: str = "zh-CN",
    chapter_no: int | None = None,
    chapter_position: str | None = None,
    category_intent: tuple[BookMethodologyDomain, ...] = (),
    project_context: Mapping[str, Any] | None = None,
    max_cards: int = 6,
    token_budget: int = 900,
) -> str:
    """Convenience renderer for prompt integrations."""

    context = BookMethodologySelectionContext(
        stage=stage,
        scope=scope,
        chapter_no=chapter_no,
        chapter_position=chapter_position,
        category_intent=category_intent,
        project_context=project_context,
        max_cards=max_cards,
        token_budget=token_budget,
    )
    return select_book_methodology_cards(context).render_prompt_block(language=language)


def _rank_candidates(
    cards: tuple[BookMethodologyCorpusCard, ...],
    *,
    stage_values: tuple[str, ...],
    scope_values: tuple[str, ...],
    domain_bias: tuple[BookMethodologyDomain, ...],
    deficit_domains: tuple[BookMethodologyDomain, ...],
    context: BookMethodologySelectionContext,
) -> list[tuple[float, BookMethodologyCorpusCard, str]]:
    ranked: list[tuple[float, BookMethodologyCorpusCard, str]] = []
    for item in cards:
        if not set(stage_values).intersection(item.card.stage):
            continue
        if not set(scope_values).intersection(item.card.scope):
            continue
        score = item.confidence or 0.65
        reasons = [f"stage={context.stage}", f"scope={context.scope}"]
        if item.taxonomy.verifiability == "strict":
            score += 0.20
            reasons.append("strict evidence")
        elif item.taxonomy.verifiability == "heuristic":
            score += 0.08
            reasons.append("judge evidence")
        else:
            score -= 0.25
            reasons.append("advisory only")
        if item.taxonomy.domain in domain_bias:
            bias_rank = domain_bias.index(item.taxonomy.domain)
            score += max(0.08, 0.28 - bias_rank * 0.035)
            reasons.append(f"strategy domain {item.taxonomy.domain}")
        if item.taxonomy.domain in deficit_domains:
            score += 0.36
            reasons.append(f"quality deficit {item.taxonomy.domain}")
        if context.chapter_no is not None and context.chapter_no <= 3:
            if item.taxonomy.domain == "opening_retention":
                score += 0.16
                reasons.append("opening chapter")
        ranked.append((score, item, "; ".join(reasons)))
    ranked.sort(key=lambda row: (-row[0], row[1].source_key, row[1].card.id))
    return ranked


def _try_add_selected_card(
    selected: list[SelectedBookMethodologyCard],
    item: BookMethodologyCorpusCard,
    *,
    reason: str,
    source_counts: Counter[str],
    domain_counts: Counter[str],
    used_duplicate_keys: set[str],
    used_tokens_ref: list[int],
    token_budget: int,
    max_cards: int,
) -> bool:
    if len(selected) >= max_cards:
        return False
    if len(selected) and used_tokens_ref[0] >= token_budget:
        return False
    if item.duplicate_key in used_duplicate_keys:
        return False
    if source_counts[item.source_key] >= 2:
        return False
    selected_card = _selected_card(item, reason=reason)
    tokens = _estimate_tokens(selected_card.core_claim) + 30
    if selected and used_tokens_ref[0] + tokens > token_budget:
        return False
    selected.append(selected_card)
    used_duplicate_keys.add(item.duplicate_key)
    source_counts[item.source_key] += 1
    domain_counts[item.taxonomy.domain] += 1
    used_tokens_ref[0] += tokens
    return True


def _strategy_domain_plan(
    context: BookMethodologySelectionContext,
) -> tuple[tuple[BookMethodologyDomain, ...], tuple[BookMethodologyDomain, ...]]:
    base = STAGE_DOMAIN_BIAS.get(context.stage, ())
    deficit_domains = _deficit_domains(context.project_context)
    return (
        tuple(dict.fromkeys((*deficit_domains, *base))),
        deficit_domains,
    )


def _deficit_domains(
    project_context: Mapping[str, Any] | None,
) -> tuple[BookMethodologyDomain, ...]:
    if not isinstance(project_context, Mapping):
        return ()
    domains: list[BookMethodologyDomain] = []
    explicit = project_context.get("quality_deficits")
    if isinstance(explicit, Mapping):
        for metric, score in explicit.items():
            if _metric_is_deficit(str(metric), score, project_context):
                domains.extend(QUALITY_METRIC_DOMAIN_REPAIR.get(str(metric), ()))
    elif isinstance(explicit, (list, tuple, set)):
        for metric in explicit:
            domains.extend(QUALITY_METRIC_DOMAIN_REPAIR.get(str(metric), ()))

    scores = project_context.get("metric_scores") or project_context.get("quality_scores")
    if isinstance(scores, Mapping):
        for metric, score in scores.items():
            if _metric_is_deficit(str(metric), score, project_context):
                domains.extend(QUALITY_METRIC_DOMAIN_REPAIR.get(str(metric), ()))
    return tuple(dict.fromkeys(domains))


def _metric_is_deficit(
    metric: str,
    score: object,
    project_context: Mapping[str, Any],
) -> bool:
    if metric not in QUALITY_METRIC_DOMAIN_REPAIR:
        return False
    try:
        value = float(score)
    except (TypeError, ValueError):
        return True
    thresholds = project_context.get("quality_thresholds")
    threshold = DEFAULT_METRIC_THRESHOLD
    if isinstance(thresholds, Mapping):
        try:
            threshold = float(thresholds.get(metric, threshold))
        except (TypeError, ValueError):
            threshold = DEFAULT_METRIC_THRESHOLD
    if metric in LOWER_IS_BETTER_METRICS:
        return value > threshold
    return value < threshold


def _selected_card(
    item: BookMethodologyCorpusCard,
    *,
    reason: str,
) -> SelectedBookMethodologyCard:
    return SelectedBookMethodologyCard(
        card_id=core_book_methodology_card_id(item.card.id),
        raw_card_id=item.card.id,
        source_key=item.source_key,
        canonical_domain=item.taxonomy.domain,
        verifiability=item.taxonomy.verifiability,
        confidence=item.confidence,
        core_claim=sanitize_book_methodology_text(item.card.core_claim),
        required_contract_fields=tuple(item.card.required_contract_fields),
        framework_bindings=tuple(item.card.framework_bindings),
        application_hint=_application_hint(item),
        why_selected=reason,
    )


def _application_hint(item: BookMethodologyCorpusCard) -> str:
    domain = item.taxonomy.domain
    if domain == "scene_causality":
        return "scene.methodology_contract.goal/obstacle/action/result"
    if domain == "setup_payoff":
        return "chapter.hook_ledger or chapter.payoff_ledger"
    if domain == "pov_prose":
        return "scene.prose.pov_distance and concrete sensory evidence"
    if domain == "character_arc":
        return "scene.methodology_contract.character_desire/choice/cost"
    if domain == "dialogue_subtext":
        return "scene.dialogue.intent/subtext/relationship_pressure"
    if domain == "revision_loop":
        return "rewrite_queue.repair_plan"
    if domain == "project_health":
        return "project_health.methodology_coverage"
    if domain == "opening_retention":
        return "chapter.opening_pressure and first_question"
    if domain == "worldview_theme":
        return "story_design_kernel.theme_or_world_rule_pressure"
    return "chapter_outline.methodology_applications"


def _evidence_fields_for_node(
    node_path: str,
    fields: tuple[str, ...],
) -> list[str]:
    if not fields:
        return [node_path]
    return [f"{node_path}.{field}" for field in fields[:6]]


def _estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 2.5))


__all__ = [
    "BookMethodologySelection",
    "BookMethodologySelectionContext",
    "SelectedBookMethodologyCard",
    "render_book_methodology_block",
    "select_book_methodology_cards",
]
