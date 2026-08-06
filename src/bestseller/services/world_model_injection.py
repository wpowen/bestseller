"""Active-law selection + prose injection for the world model.

Downstream stages must OBEY the world model, but dumping all ~14 laws into every
prose prompt would overload it (see the prose-prompt-diet lesson). This module
selects only the laws relevant to the current chapter/scene and renders a compact
``enforcement`` block. Pure + fail-safe; the same selector feeds the consistency
gate so prose and gate read one source of truth.
"""

# ruff: noqa: E501, ANN401

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.domain.world_model import WorldLaw, WorldModel, world_model_from_dict
from bestseller.services.world_dimensions import _tokens

_DEFAULT_MAX_LAWS = 3


def extract_world_model(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Pull the stored world_model dict out of project metadata, if present.

    Looks both at the top level and inside ``story_design_kernel`` (where the
    planner attaches it).
    """

    if not isinstance(metadata, Mapping):
        return None
    def _approved(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping) or not value.get("world_laws"):
            return None
        if value.get("source_artifact_type") != "world_spec":
            return None
        if not str(value.get("source_artifact_hash") or "").strip():
            return None
        return dict(value)

    # The nested story kernel is produced after WorldSpec approval and is the
    # current canonical location.  Never let an old top-level free-derived model
    # outrank it merely because it was persisted earlier.
    sdk = metadata.get("story_design_kernel")
    if isinstance(sdk, Mapping):
        nested = _approved(sdk.get("world_model"))
        if nested:
            return nested
    return _approved(metadata.get("world_model"))


def _coerce_model(world_model: WorldModel | Mapping[str, Any] | None) -> WorldModel | None:
    if world_model is None:
        return None
    if isinstance(world_model, WorldModel):
        return world_model
    try:
        return world_model_from_dict(dict(world_model))
    except Exception:
        return None


def select_active_laws(
    world_model: WorldModel | Mapping[str, Any] | None,
    *,
    context_text: str = "",
    max_laws: int = _DEFAULT_MAX_LAWS,
) -> list[WorldLaw]:
    """Pick the laws most relevant to ``context_text`` (chapter/scene fields).

    Relevance = token overlap between the context and each law's dimension/delta.
    When the context gives no signal, fall back to the protagonist's fault-line
    laws plus the highest-specificity laws so a block is always available.
    """

    model = _coerce_model(world_model)
    if model is None or not model.world_laws:
        return []
    ctx = _tokens(context_text)

    scored: list[tuple[int, float, WorldLaw]] = []
    for law in model.world_laws:
        law_tokens = _tokens(f"{law.dimension} {law.delta} {law.story_use}")
        overlap = len(ctx & law_tokens) if ctx else 0
        scored.append((overlap, law.specificity, law))

    relevant = [s for s in scored if s[0] > 0]
    relevant.sort(key=lambda s: (s[0], s[1]), reverse=True)
    chosen = [law for _, _, law in relevant[:max_laws]]

    if len(chosen) < max_laws:
        # Backfill: protagonist fault-line laws, then highest-specificity laws.
        protagonist_refs: set[str] = set()
        for fl in model.fault_lines:
            if fl.used_by_protagonist:
                protagonist_refs.update(fl.world_law_refs)
        remaining = [law for _, _, law in sorted(scored, key=lambda s: s[1], reverse=True)]
        for law in remaining:
            if law in chosen:
                continue
            is_pref = law.dimension in protagonist_refs
            chosen.append(law)
            if len(chosen) >= max_laws:
                break
            if is_pref:
                continue
    return chosen[:max_laws]


def render_active_law_block(laws: list[WorldLaw], *, language: str = "zh") -> str:
    """Render selected laws' enforcement assertions as a compact prose-prompt block."""

    if not laws:
        return ""
    is_en = str(language or "").lower().startswith("en")
    head = (
        "World laws in force this chapter (the prose MUST obey; do not revert to the baseline without a stated reason):"
        if is_en
        else "本章生效的世界规律(正文必须遵守,不得无理由回退到基线常态):"
    )
    lines = [head]
    for law in laws:
        lines.append(f"- [{law.dimension}] {law.enforcement}")
    return "\n".join(lines)


def _context_text_from(chapter: Any, scene: Any) -> str:
    parts: list[str] = []
    for attr in ("chapter_goal", "main_conflict", "opening_situation", "location_tag", "title"):
        val = getattr(chapter, attr, None)
        if isinstance(val, str) and val:
            parts.append(val)
    for attr in ("scene_type",):
        val = getattr(scene, attr, None)
        if isinstance(val, str) and val:
            parts.append(val)
    purpose = getattr(scene, "purpose", None)
    if isinstance(purpose, Mapping):
        parts.extend(str(v) for v in purpose.values() if isinstance(v, str))
    info = getattr(chapter, "information_revealed", None)
    if isinstance(info, (list, tuple)):
        parts.extend(str(v) for v in info if isinstance(v, str))
    return " ".join(parts)


def build_active_law_prose_block_for_scene(
    project: Any,
    chapter: Any,
    scene: Any,
    *,
    language: str = "zh",
    max_laws: int = _DEFAULT_MAX_LAWS,
) -> str:
    """Read the project's world model and render the active-law block for a scene.

    Fully fail-safe — returns ``""`` when no world model is stored.
    """

    metadata = getattr(project, "metadata_json", None)
    world_model = extract_world_model(metadata)
    if not world_model:
        return ""
    context_text = _context_text_from(chapter, scene)
    laws = select_active_laws(world_model, context_text=context_text, max_laws=max_laws)
    return render_active_law_block(laws, language=language)


__all__ = [
    "build_active_law_prose_block_for_scene",
    "extract_world_model",
    "render_active_law_block",
    "select_active_laws",
]
