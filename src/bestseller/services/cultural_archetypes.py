from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.cultural_texture import CulturalTextureModule

DEFAULT_ARCHETYPE_DIR = Path("config/cultural_archetypes")


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def load_cultural_archetype(
    archetype_id: str,
    *,
    base_dir: Path = DEFAULT_ARCHETYPE_DIR,
) -> CulturalTextureModule:
    path = base_dir / f"{archetype_id}.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = _as_dict(payload)
    return CulturalTextureModule.model_validate(data)


def load_cultural_archetype_for_category(
    category: str,
    *,
    base_dir: Path = DEFAULT_ARCHETYPE_DIR,
) -> CulturalTextureModule | None:
    for path in sorted(base_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data = _as_dict(payload)
        categories = data.get("applicable_categories")
        if isinstance(categories, list) and category in categories:
            return CulturalTextureModule.model_validate(data)
    return None

