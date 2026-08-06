"""Collect the settings that decide what "a correct chapter" means.

Separated from :mod:`book_runtime_guard` on purpose: the guard is generic
freeze/verify machinery, this module is the opinion about *which* knobs matter.
Keeping them apart means adding a newly-important setting to the contract does
not touch the hashing logic, and the guard stays unit-testable without app
settings.

Only include settings a chapter is actually judged against. Adding cosmetic or
per-run values (timestamps, worker ids, model routing that legitimately varies)
would make every run look like drift and train everyone to ignore the report.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["collect_book_contract"]


def collect_book_contract(
    settings: Any,
    project: Any = None,
) -> dict[str, Any]:
    """Snapshot the generation/validation contract as plain JSON-able values.

    Never raises: a missing attribute yields a partial contract rather than
    failing the run. A guard that can crash the pipeline is worse than the drift
    it detects.
    """

    contract: dict[str, Any] = {}

    generation = getattr(settings, "generation", None)
    pipeline = getattr(settings, "pipeline", None)

    contract["length_contract"] = {
        "words_per_chapter": _attr(generation, "words_per_chapter"),
        "language": _attr(generation, "language"),
    }

    # NOTE: ``writer_prompt_mode`` lives under ``generation`` while the profile
    # lives under ``pipeline``. Reading either from the wrong section silently
    # yields ``None``, which hashes consistently and makes the guard look
    # healthy while watching nothing — verified against real settings.
    contract["prose_prompt_profile"] = {
        "writer_prompt_mode": _attr(generation, "writer_prompt_mode"),
        "prose_prompt_profile": _attr(pipeline, "prose_prompt_profile"),
    }

    contract["repair_budgets"] = {
        "chapter_auto_repair_max_attempts": _attr(
            pipeline, "chapter_auto_repair_max_attempts"
        ),
        "chapter_auto_repair_total_max_attempts": _attr(
            pipeline, "chapter_auto_repair_total_max_attempts"
        ),
        "autonomous_quality_retrofit_max_attempts": _attr(
            pipeline, "autonomous_quality_retrofit_max_attempts"
        ),
    }

    if project is not None:
        metadata = getattr(project, "metadata_json", None) or {}
        if isinstance(metadata, Mapping):
            # Per-book overrides count as part of the contract: changing one
            # mid-run splits the book exactly like a global change does.
            contract["book_overrides"] = {
                key: metadata.get(key)
                for key in (
                    "prompt_pack_key",
                    "chapter_first_writer_aim",
                    "quality_profile",
                    "writer_model",
                )
                if metadata.get(key) is not None
            }

    return {key: value for key, value in contract.items() if value not in (None, {})}


def _attr(source: Any, name: str) -> Any:
    """Read a setting, tolerating absence.

    Deliberately ``getattr`` with a default rather than direct access: this runs
    against several settings shapes (real config, test doubles, older books) and
    a partial contract is far better than an exception inside a guard.
    """

    if source is None:
        return None
    try:
        value = getattr(source, name, None)
    except Exception:  # pragma: no cover — defensive
        logger.debug("could not read contract field %s", name, exc_info=True)
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items() if _is_scalar(v)}
    if isinstance(value, (list, tuple)):
        return [item for item in value if _is_scalar(item)]
    return str(value)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None
