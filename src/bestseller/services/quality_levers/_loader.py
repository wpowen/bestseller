"""Shared YAML loader utilities for the ``quality_levers`` package.

These helpers centralise:

* the project-root-relative resolution of ``config/*.yaml`` files
* the ``functools.lru_cache`` reading discipline used by the legacy
  :mod:`bestseller.services.methodology` module
* generic ``yaml`` -> ``tuple[str, ...]`` coercion that tolerates the
  ``- key: value`` shorthand commonly used in the configs

Every loader in this package follows the same shape (see
:mod:`bestseller.services.methodology` for the reference implementation):

#. ``_<name>_config_path()``
#. ``load_<name>_raw()`` (cached)
#. typed ``get_<name>()`` -> ``frozen dataclass``
#. ``render_<name>_block(**kwargs)`` -> prompt fragment (``""`` when N/A)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _config_root() -> Path:
    """Return the absolute path to the repository ``config/`` directory.

    Resolves relative to this file so the loaders work regardless of
    the current working directory of the caller (FastAPI worker, CLI,
    pytest, etc.).
    """

    return Path(__file__).resolve().parents[4] / "config"


def config_path(filename: str) -> Path:
    """Return the absolute path of a config YAML file by name."""

    return _config_root() / filename


@lru_cache(maxsize=32)
def load_yaml(filename: str) -> dict[str, Any]:
    """Load and cache a YAML config file by filename.

    Returns an empty dict when the file is missing so callers can
    degrade gracefully — matching the established behaviour of
    :func:`bestseller.services.methodology.load_methodology`.
    """

    path = config_path(filename)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def as_tuple(value: Any) -> tuple[str, ...]:
    """Coerce an arbitrary YAML value into a tuple of clean strings.

    Mirrors the helper used in ``methodology.py``. Accepts:

    * ``str``   → ``(stripped,)`` or ``()`` when blank
    * ``list``/``tuple`` → each element stringified; nested dicts are
      flattened into ``"key: value"`` lines
    * anything else → ``()``
    """

    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                for key, val in item.items():
                    text = f"{key}: {val}".strip()
                    if text:
                        items.append(text)
                continue
            text = str(item).strip()
            if text:
                items.append(text)
        return tuple(items)
    return ()


def as_str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce ``value`` to ``tuple[str, ...]`` ignoring non-string children."""

    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return ()


def as_str(value: Any, default: str = "") -> str:
    """Coerce ``value`` to a clean string (or ``default`` when empty)."""

    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def as_int(value: Any, default: int = 0) -> int:
    """Coerce ``value`` to an ``int`` falling back to ``default`` on failure."""

    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a dict, else an empty dict."""

    return value if isinstance(value, dict) else {}


def normalize_platform_id(platform: str | None) -> str | None:
    """Map common synonyms (Chinese names, casing) onto canonical platform IDs.

    Returns ``None`` when ``platform`` is missing so callers can use a
    falsy check to skip platform-specific rendering.
    """

    if not platform:
        return None
    text = platform.strip().lower()
    if not text:
        return None
    if "qimao" in text or "七猫" in text:
        return "qimao"
    if "qidian" in text or "起点" in text:
        return "qidian"
    if "tomato" in text or "番茄" in text:
        return "tomato"
    if "fanqie" in text:
        return "tomato"
    return text


def project_is_english(language: str | None) -> bool:
    """Return ``True`` when the project language indicates English output."""

    return (language or "").lower().startswith("en")
