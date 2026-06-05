"""Genre-neutral signal-term source for the *deterministic* gates (R1).

Several deterministic gates (common_sense_gate, hook_echo_gate, exposition_density_gate,
…) historically hardcoded ONE detective book's vocabulary — its rule jargon
(认账/镜债/账线), its key objects (铜钱/罗盘/青囊) and even its cast names
(王建业/张建军/…) — into "universal" checks. For any other genre those checks either
never fire (coverage gap) or reference the wrong terms.

This module resolves a project's ``(genre, sub_genre, story_bible)`` into term banks
the gates can use instead of hardcoded detective terms. It binds to the same framework
infrastructure the judges use (so there is one genre source, not two):

* :func:`bestseller.services.genre_review_profiles.resolve_genre_review_profile`
  — genre-level conflict / hook / info keyword banks.
* :func:`bestseller.services.judge_genre_context.derive_specialist_rule_terms`
  — THIS book's own rule terms and key objects from its bible.

Gates that already receive ``genre``/``sub_genre`` can augment their hardcoded banks
(mirroring ``commercial_planning_readiness._augmented_concrete_pressure_terms``); gates
that also receive the bible get book-specific rule/object terms too.
"""

from __future__ import annotations

# ruff: noqa: ANN401, RUF001, E501

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class GenreSignalTerms:
    """Term banks for a genre + (optionally) this book's own bible-derived terms."""

    category_key: str
    conflict_terms: tuple[str, ...] = ()
    hook_terms: tuple[str, ...] = ()
    info_terms: tuple[str, ...] = ()
    #: This book's own rule/specialist jargon (from its bible). Empty when no bible.
    rule_terms: tuple[str, ...] = ()
    #: This book's own key objects / abilities (from its bible). Empty when no bible.
    object_terms: tuple[str, ...] = ()

    def all_terms(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                [
                    *self.conflict_terms, *self.hook_terms, *self.info_terms,
                    *self.rule_terms, *self.object_terms,
                ]
            )
        )


def _profile_terms(genre: str | None, sub_genre: str | None) -> tuple[str, GenreSignalTerms]:
    try:
        from bestseller.services.genre_review_profiles import resolve_genre_review_profile

        profile = resolve_genre_review_profile(str(genre or "general-fiction"), sub_genre)
        sk = profile.signal_keywords
        return profile.category_key, GenreSignalTerms(
            category_key=profile.category_key,
            conflict_terms=tuple(dict.fromkeys([*sk.conflict_terms_zh, *sk.conflict_terms_en])),
            hook_terms=tuple(dict.fromkeys([*sk.hook_terms_zh, *sk.hook_terms_en])),
            info_terms=tuple(dict.fromkeys([*sk.info_terms_zh, *sk.info_terms_en])),
        )
    except Exception:
        return "default", GenreSignalTerms(category_key="default")


@lru_cache(maxsize=64)
def _cached_profile_terms(genre: str | None, sub_genre: str | None) -> GenreSignalTerms:
    return _profile_terms(genre, sub_genre)[1]


def resolve_genre_signal_terms(
    *,
    genre: str | None,
    sub_genre: str | None = None,
    story_bible: Mapping[str, Any] | None = None,
) -> GenreSignalTerms:
    """Resolve term banks for a project. ``story_bible`` (optional) adds this book's
    own rule/object terms so a gate references the right vocabulary instead of one
    detective book's jargon."""

    base = _cached_profile_terms(genre, sub_genre)
    if not story_bible:
        return base
    from bestseller.services.judge_genre_context import (
        _derive_key_objects,
        derive_specialist_rule_terms,
    )

    return GenreSignalTerms(
        category_key=base.category_key,
        conflict_terms=base.conflict_terms,
        hook_terms=base.hook_terms,
        info_terms=base.info_terms,
        rule_terms=derive_specialist_rule_terms(story_bible),
        object_terms=_derive_key_objects(story_bible),
    )


__all__ = ["GenreSignalTerms", "resolve_genre_signal_terms"]
