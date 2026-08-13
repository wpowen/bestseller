"""Resolve taxonomy genres onto platform-side categories.

Fanqie uses an explicit mapping table (``genre_map`` in the config) because its
category vocabulary is platform-specific. Qimao categories reuse the classic
web-novel taxonomy, so a label-match set (taxonomy labels + config aliases)
is enough there.
"""

from __future__ import annotations

from bestseller.domain.market_validation import MarketCategoryRef
from bestseller.services.market_validation.config import (
    MarketValidationConfig,
    PlatformCategoryMapping,
)


def resolve_fanqie_categories(
    config: MarketValidationConfig,
    *,
    genre_key: str,
    sub_genre_key: str = "",
) -> list[MarketCategoryRef]:
    """Map a taxonomy genre (and optional sub-genre) to fanqie categories.

    Returns an empty list for unknown or unmapped genres — callers degrade
    the section instead of failing.
    """

    entry = config.genre_map.get((genre_key or "").strip())
    if entry is None:
        return []
    mapping: PlatformCategoryMapping | None = entry.fanqie
    if sub_genre_key:
        override = entry.sub_overrides.get(sub_genre_key.strip())
        if override is not None and override.fanqie is not None:
            mapping = override.fanqie
    if mapping is None or not mapping.categories:
        return []

    cat_ids = {
        item.label: item.cat_id
        for item in config.fanqie_categories.get(mapping.channel, ())
    }
    refs: list[MarketCategoryRef] = []
    for position, label in enumerate(mapping.categories):
        refs.append(
            MarketCategoryRef(
                platform="fanqie",
                channel_label=mapping.channel,
                category_label=label,
                cat_id=cat_ids.get(label, ""),
                weight=max(0.1, 1.0 - 0.3 * position),
            )
        )
    return refs


def qimao_match_labels(
    config: MarketValidationConfig,
    *,
    genre_key: str,
    genre_label: str,
    sub_genre_labels: tuple[str, ...] = (),
) -> set[str]:
    """Label set used to filter qimao ranking rows by category name."""

    labels = {label.strip() for label in (genre_label, *sub_genre_labels) if label.strip()}
    labels.update(
        alias.strip()
        for alias in config.qimao_label_aliases.get((genre_key or "").strip(), ())
        if alias.strip()
    )
    return labels
