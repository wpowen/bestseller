"""Sensory Inventory loader (``config/sensory_inventory.yaml``).

Provides the per-scene-type "minimum sensory coverage" rule and the
banned-abstract-term list consumed by the detector module.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_int,
    as_str,
    as_str_tuple,
    load_yaml,
)


_CONFIG_FILENAME = "sensory_inventory.yaml"


@dataclass(frozen=True)
class SensoryAxis:
    """One of the seven sensory axes used to score scenes."""

    axis_id: str
    display: str
    require_specific: tuple[str, ...]


@dataclass(frozen=True)
class SceneTypeRequirement:
    """``scene_type_requirements.<id>`` — minimum sensory coverage per scene."""

    scene_type: str
    required_min: int
    must_include: tuple[str, ...]
    nice_to_have: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class SensoryInventoryConfig:
    """Typed view over the YAML."""

    version: str
    axes: dict[str, SensoryAxis]
    scene_type_requirements: dict[str, SceneTypeRequirement]
    banned_abstract_terms: tuple[str, ...]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_axis(axis_id: str, raw: object) -> SensoryAxis:
    data = as_dict(raw)
    return SensoryAxis(
        axis_id=axis_id,
        display=as_str(data.get("display"), default=axis_id),
        require_specific=as_str_tuple(data.get("require_specific")),
    )


def _parse_scene_type(scene_type: str, raw: object) -> SceneTypeRequirement:
    data = as_dict(raw)
    return SceneTypeRequirement(
        scene_type=scene_type,
        required_min=as_int(data.get("required_min"), default=3),
        must_include=as_str_tuple(data.get("must_include")),
        nice_to_have=as_str_tuple(data.get("nice_to_have")),
        rationale=as_str(data.get("rationale")),
    )


def _extract_banned_terms(raw: object) -> tuple[str, ...]:
    """Pull the ``critic_checks.abstraction_violation.banned_words`` list."""

    checks = as_dict(raw)
    abstraction = as_dict(checks.get("abstraction_violation"))
    banned_raw = abstraction.get("banned_words")
    if not isinstance(banned_raw, list):
        return ()
    # YAML entries look like ``阴森（用具体物件 / 影子 / 光线代替）``
    # — we only need the leading word for keyword matching.
    out: list[str] = []
    for entry in banned_raw:
        text = as_str(entry)
        if not text:
            continue
        head = text.split("（", 1)[0].split("(", 1)[0].strip()
        if head and head not in out:
            out.append(head)
    return tuple(out)


@lru_cache(maxsize=1)
def load_sensory_inventory() -> SensoryInventoryConfig:
    """Return the typed view over ``sensory_inventory.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    axes_raw = as_dict(raw.get("sensory_axes"))
    axes: dict[str, SensoryAxis] = {}
    for axis_id, axis_raw in axes_raw.items():
        canonical = as_str(axis_id)
        if not canonical:
            continue
        axes[canonical] = _parse_axis(canonical, axis_raw)

    scene_raw = as_dict(raw.get("scene_type_requirements"))
    scene_reqs: dict[str, SceneTypeRequirement] = {}
    for scene_type, req_raw in scene_raw.items():
        canonical = as_str(scene_type)
        if not canonical:
            continue
        scene_reqs[canonical] = _parse_scene_type(canonical, req_raw)

    return SensoryInventoryConfig(
        version=as_str(raw.get("version")),
        axes=axes,
        scene_type_requirements=scene_reqs,
        banned_abstract_terms=_extract_banned_terms(raw.get("critic_checks")),
    )


def get_scene_requirement(scene_type: str) -> SceneTypeRequirement | None:
    """Look up the minimum sensory coverage for one scene type."""

    if not scene_type:
        return None
    return load_sensory_inventory().scene_type_requirements.get(scene_type)


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_sensory_requirement_block(
    *,
    scene_type: str | None,
) -> str:
    """Render a writer-facing prompt fragment for the scene's sensory floor.

    Empty string when the scene type is missing / unknown.
    """

    requirement = get_scene_requirement(scene_type) if scene_type else None
    if requirement is None:
        return ""
    lines: list[str] = [
        f"【感官清单 · scene_type={requirement.scene_type}】",
        f"- 至少命中 {requirement.required_min} 项感官",
    ]
    if requirement.must_include:
        lines.append("- 必带: " + ", ".join(requirement.must_include))
    if requirement.nice_to_have:
        lines.append("- 可叠: " + ", ".join(requirement.nice_to_have))
    config = load_sensory_inventory()
    if config.banned_abstract_terms:
        lines.append(
            "- 叙述中禁用抽象词: "
            + ", ".join(config.banned_abstract_terms)
            + "（角色对白除外）"
        )
    return "\n".join(lines)
