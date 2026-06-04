"""Per-book switchable LLM model catalog.

Exposes a curated catalog of vendor/version models a book can be run on, and
resolves a project's chosen model (``project.metadata.llm_model_id``) into a
concrete override applied to every LLM role at request time.

The catalog is data (``config/model_catalog.yaml``); availability is computed
from whether the entry's ``api_key_env`` is present in the environment so the
UI can show which models are actually usable on this deployment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "model_catalog.yaml"

# Project metadata key holding the selected catalog entry id.
PROJECT_MODEL_ID_KEY = "llm_model_id"


class ModelCatalogEntry(BaseModel):
    """One switchable model (a concrete vendor + version)."""

    id: str
    display_name: str
    vendor: str
    version: str = ""
    model: str
    api_base: str | None = None
    api_key_env: str | None = None
    notes: str = ""
    available: bool = Field(
        default=True,
        description="True when api_key_env is present in the environment.",
    )


def _api_key_present(api_key_env: str | None) -> bool:
    if not api_key_env:
        return True  # no key required (e.g. local/mock)
    return bool(os.environ.get(api_key_env))


@lru_cache(maxsize=1)
def _load_catalog_raw() -> tuple[dict[str, Any], ...]:
    if not _CATALOG_PATH.exists():
        return ()
    data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8")) or {}
    models = data.get("models") if isinstance(data, Mapping) else None
    if not isinstance(models, list):
        return ()
    return tuple(m for m in models if isinstance(m, Mapping))


def load_model_catalog() -> list[ModelCatalogEntry]:
    """Return the catalog with per-entry availability resolved from the env."""
    entries: list[ModelCatalogEntry] = []
    for raw in _load_catalog_raw():
        try:
            entry = ModelCatalogEntry.model_validate(dict(raw))
        except Exception:
            continue
        entries.append(
            entry.model_copy(update={"available": _api_key_present(entry.api_key_env)})
        )
    return entries


def get_model_catalog_entry(model_id: str | None) -> ModelCatalogEntry | None:
    """Look up a catalog entry by id (availability resolved)."""
    if not model_id:
        return None
    for entry in load_model_catalog():
        if entry.id == model_id:
            return entry
    return None


def selected_model_id(project_metadata: Mapping[str, Any] | None) -> str | None:
    """Read the selected model id from project metadata, if any."""
    if not isinstance(project_metadata, Mapping):
        return None
    value = project_metadata.get(PROJECT_MODEL_ID_KEY)
    return str(value) if value else None


def resolve_project_model_entry(
    project_metadata: Mapping[str, Any] | None,
) -> ModelCatalogEntry | None:
    """Resolve a project's selected, *available* model entry, or None."""
    entry = get_model_catalog_entry(selected_model_id(project_metadata))
    if entry is None or not entry.available:
        return None
    return entry
