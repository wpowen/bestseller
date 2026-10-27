"""Cinematic-POV directive loader (``config/cinematic_pov.yaml``).

The highest-priority, always-on prose discipline: write the *experience*, not
the *information*. Camera follows the POV character in real time; the result is
a payoff the reader discovers, not a label announced up front; reaction shots
must carry legible meaning (an ambiguous gesture is an "invalid shot" / 水文).

Distilled from a line-by-line polishing session against real MiniMax-M3 output
(2026-06-13). Soft by nature — it shapes *how* the writer drafts; it is not a
gate and never blocks. Injected near the top of the PROSE_SCENE methodology so
the token budget never starves it.
"""

from __future__ import annotations

from functools import lru_cache

from bestseller.services.quality_levers._loader import as_str, load_yaml

_CONFIG_FILENAME = "cinematic_pov.yaml"


@lru_cache(maxsize=1)
def _load_directive() -> str:
    raw = load_yaml(_CONFIG_FILENAME)
    return as_str(raw.get("directive")).strip()


def render_cinematic_pov_block(*, language: str | None = "zh") -> str:
    """Return the writer-facing 镜头化·体验优先 directive.

    Returns an empty string for English drafts (the directive is tuned for
    Chinese prose); callers treat empty as "skip this block".
    """

    if language and str(language).strip().lower().startswith("en"):
        return ""
    return _load_directive()


__all__ = ["render_cinematic_pov_block"]
