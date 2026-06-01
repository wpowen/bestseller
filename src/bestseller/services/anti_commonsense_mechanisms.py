from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.anti_commonsense_hook import HookMechanism

DEFAULT_HOOK_MECHANISMS_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "hook_mechanisms.yaml"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Hook mechanism config must be a mapping: {path}")
    return payload


@lru_cache(maxsize=8)
def load_hook_mechanisms(
    path: str | Path = DEFAULT_HOOK_MECHANISMS_PATH,
) -> tuple[HookMechanism, ...]:
    """Load and validate anti-commonsense hook mechanisms."""

    effective = Path(path)
    payload = _load_yaml(effective)
    raw_mechanisms = payload.get("mechanisms")
    if not isinstance(raw_mechanisms, list):
        raise ValueError(f"Hook mechanism config missing mechanisms list: {effective}")
    mechanisms = tuple(HookMechanism.model_validate(item) for item in raw_mechanisms)
    keys = [item.key for item in mechanisms]
    if len(keys) != len(set(keys)):
        raise ValueError("Hook mechanism keys must be unique")
    return mechanisms


def list_mechanisms() -> tuple[HookMechanism, ...]:
    return load_hook_mechanisms()


def get_mechanism(key: str) -> HookMechanism:
    normalized = str(key or "").strip()
    for mechanism in load_hook_mechanisms():
        if mechanism.key == normalized:
            return mechanism
    raise KeyError(f"Unknown hook mechanism: {key}")


def select_mechanisms_for_genre(
    genre: str | None,
    *,
    limit: int | None = None,
) -> tuple[HookMechanism, ...]:
    """Return genre-compatible mechanisms, falling back to the full catalogue."""

    text = str(genre or "").strip().lower()
    if not text:
        selected = list(load_hook_mechanisms())
    else:
        selected = [
            mechanism
            for mechanism in load_hook_mechanisms()
            if not mechanism.genres
            or any(_genre_tag_matches(text, tag) for tag in mechanism.genres)
        ]
        if not selected:
            selected = list(load_hook_mechanisms())
    selected.sort(key=lambda item: item.saturation_score)
    if limit is not None and limit > 0:
        selected = selected[:limit]
    return tuple(selected)


def _genre_tag_matches(genre_text: str, tag: str) -> bool:
    normalized_tag = str(tag or "").strip().lower()
    if not genre_text or not normalized_tag:
        return False
    if genre_text == normalized_tag:
        return True
    return normalized_tag in genre_text


__all__ = [
    "DEFAULT_HOOK_MECHANISMS_PATH",
    "get_mechanism",
    "list_mechanisms",
    "load_hook_mechanisms",
    "select_mechanisms_for_genre",
]
