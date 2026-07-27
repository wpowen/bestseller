"""Optional short genre voice exemplars for lean writer prompts.

Design constraints (quality plan B4):
  * ≤400 CJK chars total when injected
  * Positive exemplars only — never blacklist priming
  * Default OFF so in-flight books are unaffected
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_MAX_CHARS = 400
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "voice_few_shots.yaml"


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def clear_voice_few_shot_cache() -> None:
    _load_raw.cache_clear()


def render_voice_few_shot(
    *,
    genre_key: str | None,
    language: str | None = None,
    enabled: bool = False,
    max_chars: int = _MAX_CHARS,
) -> str:
    """Return a short positive voice exemplar block, or empty string."""

    if not enabled:
        return ""
    if (language or "").lower().startswith("en"):
        return ""
    raw = _load_raw()
    examples = raw.get("examples") if isinstance(raw.get("examples"), dict) else {}
    key = str(genre_key or "").strip().lower().replace("_", "-")
    aliases = raw.get("aliases") if isinstance(raw.get("aliases"), dict) else {}
    resolved = str(aliases.get(key) or key)
    body = examples.get(resolved) or examples.get("default")
    if not isinstance(body, str) or not body.strip():
        return ""
    text = body.strip()
    if max_chars > 0 and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return (
        "# CONTEXT · 声口短范例（只学口气与节奏，禁止照抄情节或句子）\n"
        f"{text}\n"
    )


__all__ = ["clear_voice_few_shot_cache", "render_voice_few_shot"]
