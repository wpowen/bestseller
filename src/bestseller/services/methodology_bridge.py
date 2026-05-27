"""Methodology fragment bridge.

Single source of truth: ``config/writing_methodology.yaml``. Each prompt pack
can override or extend the master methodology; when a pack lacks a fragment we
fall back to a generic version derived from the master file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from bestseller.services.methodology import load_methodology
from bestseller.services.prompt_packs import PromptPack

Phase = Literal["scene", "review", "planner", "prewrite", "judge"]

_MASTER_FALLBACK_BUILDERS: dict[tuple[Phase, str], str] = {}


def _register_fallback(phase: Phase, fragment_key: str, yaml_path: tuple[str, ...]) -> None:
    """Register a ``(phase, fragment_key)`` pair to a master yaml subtree."""
    _MASTER_FALLBACK_BUILDERS[(phase, fragment_key)] = "::".join(yaml_path)


# Planner-phase fallbacks.
_register_fallback("planner", "opening_rules", ("opening_system",))
_register_fallback("planner", "character_design", ("character_system",))
_register_fallback("planner", "reversal_design", ("reversal_system",))
_register_fallback("planner", "climax_design", ("climax_system",))
_register_fallback("planner", "core_loop", ("core_loop",))

# Scene-phase fallbacks.
_register_fallback("scene", "emotion_engineering", ("emotion_engineering",))
_register_fallback("scene", "conflict_stakes", ("conflict_system",))
_register_fallback("scene", "hook_design", ("hook_system",))
_register_fallback("scene", "core_loop", ("core_loop",))
_register_fallback("scene", "dialogue_rules", ("dialogue_system",))
_register_fallback("scene", "visual_writing", ("visual_writing",))
_register_fallback("scene", "pacing_guidance", ("pacing_system",))
_register_fallback("scene", "reaction_amplification", ("reaction_amplification",))

# Review-phase fallbacks.
_register_fallback("review", "emotion_engineering", ("emotion_engineering",))
_register_fallback("review", "conflict_stakes", ("conflict_system",))
_register_fallback("review", "hook_design", ("hook_system",))
_register_fallback("review", "core_loop", ("core_loop",))
_register_fallback("review", "pacing_guidance", ("pacing_system",))

# Prewrite-phase fallbacks.
_register_fallback("prewrite", "spring_model", ("emotion_engineering", "spring_model"))
_register_fallback("prewrite", "stakes_design", ("conflict_system", "stakes_design"))
_register_fallback("prewrite", "information_density", ("pacing_system", "information_density"))

# Judge-phase fallbacks.
_register_fallback("judge", "opening_rules", ("opening_system",))
_register_fallback("judge", "character_design", ("character_system",))
_register_fallback("judge", "reversal_design", ("reversal_system",))
_register_fallback("judge", "climax_design", ("climax_system",))
_register_fallback("judge", "spring_model", ("emotion_engineering", "spring_model"))
_register_fallback("judge", "stakes_design", ("conflict_system", "stakes_design"))
_register_fallback("judge", "hook_design", ("hook_system",))


def get_fragment(
    pack: PromptPack | None,
    *,
    phase: Phase,
    fragment_key: str,
) -> str:
    """Return the best available methodology fragment text."""
    if pack is not None:
        value = getattr(pack.fragments, fragment_key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    builder_path = _MASTER_FALLBACK_BUILDERS.get((phase, fragment_key))
    if not builder_path:
        return ""
    return _render_master_fragment(builder_path)


def get_fragments_for_phase(
    pack: PromptPack | None,
    *,
    phase: Phase,
) -> dict[str, str]:
    """Return all known fragments for a phase, keyed by fragment key."""
    fragments: dict[str, str] = {}
    for registered_phase, key in _MASTER_FALLBACK_BUILDERS:
        if registered_phase != phase:
            continue
        text = get_fragment(pack, phase=phase, fragment_key=key)
        if text:
            fragments[key] = text
    return fragments


def render_phase_block(
    pack: PromptPack | None,
    *,
    phase: Phase,
    heading: str = "写法方法论指导",
) -> str:
    """Render a combined methodology block for a phase with master fallback."""
    fragments = get_fragments_for_phase(pack, phase=phase)
    if not fragments:
        return ""
    sections = [f"【{key}】\n{value}" for key, value in fragments.items()]
    return f"## {heading}\n\n" + "\n\n".join(sections)


@lru_cache(maxsize=64)
def _render_master_fragment(path_spec: str) -> str:
    """Render a generic fragment from ``writing_methodology.yaml``."""
    master = load_methodology()
    if not master:
        return ""

    node: object = master
    for key in path_spec.split("::"):
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
        if node is None:
            return ""

    return _format_yaml_subtree(node)


def _format_yaml_subtree(node: object, depth: int = 0) -> str:
    """Pretty-format a yaml subtree as a prompt-readable bullet list."""
    indent = "  " * depth
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float, bool)):
        return str(node)
    if isinstance(node, list):
        lines: list[str] = []
        for item in node:
            if isinstance(item, dict):
                head = item.get("stage") or item.get("name") or item.get("key") or ""
                detail = item.get("description") or item.get("rule") or ""
                ratio = item.get("ratio")
                line = f"{indent}- "
                if head:
                    line += str(head)
                if ratio is not None:
                    line += f" ({float(ratio):.0%})"
                if detail:
                    line += f": {detail}"
                lines.append(line)
            else:
                lines.append(f"{indent}- {item}")
        return "\n".join(lines)
    if isinstance(node, dict):
        lines: list[str] = []
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                lines.append(f"{indent}{value.strip()}")
                continue
            if isinstance(value, (dict, list)):
                lines.append(f"{indent}■ {key}：")
                lines.append(_format_yaml_subtree(value, depth + 1))
            else:
                lines.append(f"{indent}- {key}: {value}")
        return "\n".join(lines)
    return ""


__all__ = [
    "Phase",
    "get_fragment",
    "get_fragments_for_phase",
    "render_phase_block",
]
