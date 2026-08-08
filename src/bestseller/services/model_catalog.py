"""Per-book switchable LLM model catalog.

Exposes a curated catalog of vendor/version models a book can be run on, and
resolves a project's chosen model (``project.metadata.llm_model_id``) into a
concrete override applied to every LLM role at request time.

The catalog is data (``config/model_catalog.yaml``); availability is computed
from whether the entry's ``api_key_env`` is present in the environment so the
UI can show which models are actually usable on this deployment.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
import yaml

from bestseller.settings import get_runtime_env_value

_CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "model_catalog.yaml"
logger = logging.getLogger(__name__)

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
    api_key_header: str | None = None
    notes: str = ""
    retired: bool = Field(
        default=False,
        description="True when the upstream removed this model (EOL/renamed); "
        "a retired entry is never available regardless of API keys.",
    )
    retired_reason: str = ""
    available: bool = Field(
        default=True,
        description="True when the entry is not retired, not runtime-dead, and "
        "its api_key_env is present in the environment.",
    )
    unavailable_reason: str | None = Field(
        default=None,
        description="Human-readable reason when available is False.",
    )


def _api_key_present(api_key_env: str | None) -> bool:
    if not api_key_env:
        return True  # no key required (e.g. local/mock)
    return bool(get_runtime_env_value(api_key_env))


# ── Runtime dead-model registry ─────────────────────────────────────────
#
# Models whose endpoint proved permanently gone during this process's
# lifetime (HTTP 410 Gone, or 404 model/function-not-found).  The LLM call
# layer reports in via ``mark_model_runtime_dead``; availability resolution
# reads the registry so selection gates and the UI stop offering a model the
# upstream has already removed, instead of letting every call silently
# degrade to fallback content.  Process-local by design: a restart re-probes.
_runtime_dead_models: dict[str, str] = {}

# Model ids we already warned about (selected-but-unavailable); avoids one
# warning line per LLM call for a book pinned to a dead/unconfigured model.
_warned_unavailable_selection: set[str] = set()


def mark_model_runtime_dead(model: str | None, reason: str) -> None:
    """Record that ``model`` (the litellm model string) is gone upstream."""
    if not model:
        return
    if model not in _runtime_dead_models:
        logger.error(
            "Model %s marked runtime-dead (calls will not be routed to it "
            "until restart): %s",
            model,
            reason,
        )
    _runtime_dead_models[model] = reason


def runtime_dead_reason(model: str | None) -> str | None:
    """Return the recorded dead-reason for a model string, if any."""
    if not model:
        return None
    return _runtime_dead_models.get(model)


def clear_runtime_dead_models() -> None:
    """Reset the runtime dead-model registry (tests / manual recovery)."""
    _runtime_dead_models.clear()
    _warned_unavailable_selection.clear()


def _resolve_availability(entry: ModelCatalogEntry) -> tuple[bool, str | None]:
    """Compute (available, unavailable_reason) for one catalog entry."""
    if entry.retired:
        reason = entry.retired_reason.strip() or "上游已移除该模型"
        return False, f"已下线：{reason}"
    dead_reason = runtime_dead_reason(entry.model)
    if dead_reason:
        return False, f"上游探测不可用：{dead_reason}"
    if not _api_key_present(entry.api_key_env):
        return False, f"未配置 API Key（{entry.api_key_env}）"
    return True, None


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
            logger.debug("Skipping invalid model catalog entry.", exc_info=True)
            continue
        available, unavailable_reason = _resolve_availability(entry)
        entries.append(
            entry.model_copy(
                update={
                    "available": available,
                    "unavailable_reason": unavailable_reason,
                }
            )
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
    """Resolve a project's selected, *available* model entry, or None.

    A selection that names a known-but-unavailable entry (retired, runtime-dead,
    or missing API key) is dropped, but loudly: silently reverting a book to the
    default model is exactly the "dead model, invisible degradation" failure
    mode this module guards against.
    """
    selected = selected_model_id(project_metadata)
    entry = get_model_catalog_entry(selected)
    if entry is None:
        return None
    if not entry.available:
        if selected not in _warned_unavailable_selection:
            _warned_unavailable_selection.add(selected)
            logger.warning(
                "Project-selected model %s is unavailable (%s) — using the "
                "configured default model instead.",
                selected,
                entry.unavailable_reason or "unavailable",
            )
        return None
    return entry
