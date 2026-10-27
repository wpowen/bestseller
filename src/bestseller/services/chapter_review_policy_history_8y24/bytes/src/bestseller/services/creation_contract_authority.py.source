"""Project-owned creation constraints, separate from inferred genre defaults."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from bestseller.services.story_enhancers import (
    resolve_cost_style,
    resolve_explicit_story_enhancers,
)


def select_creation_contracts(metadata: Mapping[str, Any], *, source_bound: bool) -> dict[str, Any]:
    """Read explicit creation fields, never promote materialized caches.

    Missing provenance remains unknown. The source_bound argument is retained
    for callers; cached commercial/effect/ability labels cannot grant authority
    in either mode. World, identity and canon have independent fact channels.
    """
    configured = {}
    raw_config = metadata.get("creation_configuration")
    if raw_config is not None:
        from bestseller.services.creation_configuration import CreationConfiguration
        cfg = CreationConfiguration.model_validate(raw_config)
        if cfg.digest() != metadata.get("creation_configuration_hash"):
            raise ValueError("creation_configuration_hash_mismatch")
        configured["creation_configuration"] = cfg.model_dump(mode="json")
    creation = metadata.get("creation_intent_contract")
    if not isinstance(creation, Mapping):
        return configured
    sources = creation.get("field_sources")
    if not isinstance(sources, Mapping):
        return {}
    # Read the stored map directly: validating an old contract would fill in
    # missing provenance with model defaults, which is not author evidence.
    explicit = {
        key: deepcopy(creation[key])
        for key in (
            "language",
            "audience_orientation",
            "narrative_scale",
            "tone_preference",
            "pov",
            "creative_direction",
            "concept_seed",
            "configuration",
        )
        if key in creation and sources.get(key) == "explicit"
    }
    contracts: dict[str, Any] = dict(configured)
    hook = creation.get("hook_spec")
    if sources.get("hook_spec") == "explicit" and isinstance(hook, Mapping):
        explicit["hook_spec"] = {
            key: deepcopy(value)
            for key, value in hook.items()
            if key != "_story_enhancer_input_sources"
        }
    if sources.get("story_enhancers") == "explicit" and isinstance(
        creation.get("story_enhancers"), Mapping
    ):
        enhancers = resolve_explicit_story_enhancers(metadata).model_dump(
            mode="json",
            exclude={"cost_style"},
        )
        # The resolver also recognizes historical canonical explicit choices
        # of non-default costs. Do not narrow that existing evidence contract.
        cost_style = resolve_cost_style(metadata)
        if cost_style:
            enhancers["cost_style"] = cost_style
        explicit["story_enhancers"] = enhancers
        contracts["story_enhancers"] = deepcopy(enhancers)
    genre = creation.get("genre_intent") if sources.get("genre_intent") == "explicit" else None
    if isinstance(genre, Mapping):
        # Genre identity is explicit; taxonomy routing/defaults and nested
        # enhancer/tone caches do not inherit that field's provenance.
        selected_genre = {
            key: deepcopy(genre[key])
            for key in (
                "channel_key",
                "genre_key",
                "genre_label",
                "sub_genre_key",
                "sub_genre_label",
            )
            if key in genre
        }
        selected_genre["tags"] = (
            deepcopy(genre.get("user_tags") or []) if genre.get("tag_source") == "user" else []
        )
        explicit["genre_intent"] = selected_genre
        contracts["genre_intent_contract"] = deepcopy(selected_genre)
    if explicit:
        contracts["creation_intent_contract"] = explicit
    return contracts
