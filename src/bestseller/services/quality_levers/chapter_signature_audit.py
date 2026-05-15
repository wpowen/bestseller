"""Chapter Signature Audit loader (``config/chapter_signature_audit.yaml``).

Encodes the "every chapter must have at least one screenshot moment"
requirement. The pipeline integration is expected to:

#. Render :func:`render_chapter_signature_block` into the writer
   prompt (so the LLM knows which ``signature_type`` to plant)
#. Pass the writer's declared signature back into the critic stage
#. Have ``critic`` verify the strength via :class:`SignatureType.test_questions`
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


_CONFIG_FILENAME = "chapter_signature_audit.yaml"


@dataclass(frozen=True)
class SignatureType:
    """One ``signature_types.<id>`` entry."""

    type_id: str
    display_name: str
    description: str
    examples: tuple[str, ...]
    test_questions: tuple[str, ...]


@dataclass(frozen=True)
class ChapterSignatureConfig:
    """Typed view over the YAML."""

    version: str
    signature_types: dict[str, SignatureType]
    minimum_per_chapter: int
    recommended_per_chapter: dict[str, str]
    diversity_window_size: int
    diversity_min_types: int


def _parse_signature_type(type_id: str, raw: object) -> SignatureType:
    data = as_dict(raw)
    return SignatureType(
        type_id=type_id,
        display_name=as_str(data.get("display_name"), default=type_id),
        description=as_str(data.get("description")),
        examples=as_str_tuple(data.get("examples")),
        test_questions=as_str_tuple(data.get("test")),
    )


def _parse_recommended_per_chapter(raw: object) -> dict[str, str]:
    data = as_dict(raw)
    return {
        as_str(key): as_str(value)
        for key, value in data.items()
        if as_str(key) and as_str(value)
    }


@lru_cache(maxsize=1)
def load_chapter_signature_audit() -> ChapterSignatureConfig:
    """Return the typed view over ``chapter_signature_audit.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    types_raw = as_dict(raw.get("signature_types"))
    types: dict[str, SignatureType] = {}
    for type_id, type_raw in types_raw.items():
        canonical = as_str(type_id)
        if not canonical:
            continue
        types[canonical] = _parse_signature_type(canonical, type_raw)

    requirements = as_dict(raw.get("chapter_requirements"))
    minimum = as_dict(requirements.get("minimum_per_chapter"))
    recommended = as_dict(requirements.get("recommended_per_chapter"))
    diversity = as_dict(requirements.get("recommended_diversity"))

    return ChapterSignatureConfig(
        version=as_str(raw.get("version")),
        signature_types=types,
        minimum_per_chapter=as_int(minimum.get("threshold"), default=1),
        recommended_per_chapter=_parse_recommended_per_chapter(
            recommended.get("recommendation")
        ),
        diversity_window_size=_extract_window_size(diversity),
        diversity_min_types=_extract_min_types(diversity),
    )


def _extract_window_size(diversity_raw: object) -> int:
    """Pull ``连续 N 章`` from the recommended_diversity.rule string."""

    data = as_dict(diversity_raw)
    rule_text = as_str(data.get("rule"))
    digits = "".join(ch for ch in rule_text if ch.isdigit())
    if digits:
        try:
            return int(digits)
        except ValueError:
            return 5
    return 5


def _extract_min_types(diversity_raw: object) -> int:
    """The YAML doesn't expose this directly; we hard-code a sensible default."""

    return 3


def render_chapter_signature_block(*, chapter_role: str = "ordinary_chapter") -> str:
    """Render the writer-facing fragment for the signature contract.

    ``chapter_role`` is one of ``ordinary_chapter`` / ``hook_chapter`` /
    ``climax_chapter`` / ``milestone_chapter`` — the recommended
    signature count varies per role.
    """

    config = load_chapter_signature_audit()
    if not config.signature_types:
        return ""
    lines: list[str] = ["【chapter_signature_audit · 截图段契约】"]
    minimum = max(1, config.minimum_per_chapter)
    recommendation = config.recommended_per_chapter.get(chapter_role, "")
    lines.append(
        f"- 本章至少 {minimum} 个 signature 命中"
        + (f"（推荐 {recommendation}）" if recommendation else "")
    )
    type_lines = [
        f"  - {sig_type.type_id} ({sig_type.display_name}): {sig_type.description[:30]}…"
        for sig_type in config.signature_types.values()
    ]
    if type_lines:
        lines.append("- 可选 signature_type:\n" + "\n".join(type_lines))
    if config.diversity_window_size and config.diversity_min_types:
        lines.append(
            f"- 多样性: 连续 {config.diversity_window_size} 章 ≥ "
            f"{config.diversity_min_types} 种 signature_type"
        )
    return "\n".join(lines)
