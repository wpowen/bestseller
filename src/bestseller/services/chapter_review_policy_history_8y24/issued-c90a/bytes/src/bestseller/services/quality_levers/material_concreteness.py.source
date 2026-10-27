"""Material Concreteness loader + detector (``config/material_concreteness.yaml``).

Layer 3 of the scene-grounding work. A/B experiments (see
``docs/scene-grounding-cinematic-narration-2026-06.md`` §9) proved the dominant
lever for essay-like prose is **upstream material concreteness**, not the writer
prompt: feeding the same writer abstract §default mechanism material ("商业类型
状态引擎 / 状态变化规则 / 品类承诺") produced ~14× the authorial-intrusion density
of concrete, book-specific material.

This module ships the two complementary pieces:

* :func:`render_concretization_directive` — the writer-side **fix**: a soft
  PROSE_SCENE prompt block telling the writer to *instantiate* abstract
  mechanism material into the book's concrete people / objects / actions before
  writing, and keep mechanism vocabulary out of the prose. Anchored to the
  book's existing canon so it cannot drift into inventing conflicting nouns.
* :func:`detect_material_abstractness` — the planning-time **measurement / soft
  gate**: scans the bible / ``material_reference_block`` text for abstract
  mechanism markers and §default-* slug references. Runs on the *material*, not
  the prose (the writer paraphrases the jargon away, so prose contains ~0
  mechanism terms while the bible is full of them).

Soft only — nothing here feeds a hard gate / must_rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from bestseller.services.quality_levers._loader import (
    as_dict,
    as_str,
    as_str_tuple,
    load_yaml,
)

_CONFIG_FILENAME = "material_concreteness.yaml"
_CJK_RE = re.compile(r"[一-鿿]")


@dataclass(frozen=True)
class ConcretizationDirective:
    header: str
    body: str
    position_hint: str


@dataclass(frozen=True)
class MaterialConcretenessConfig:
    version: str
    directive: ConcretizationDirective
    abstract_markers: tuple[str, ...]
    default_slug_marker: str
    abstract_markers_per_kchars: float
    default_slug_ratio_ceiling: float


def _parse_directive(raw: object) -> ConcretizationDirective:
    data = as_dict(raw)
    return ConcretizationDirective(
        header=as_str(data.get("header")),
        body=as_str(data.get("body")),
        position_hint=as_str(data.get("position_hint")),
    )


@lru_cache(maxsize=1)
def load_material_concreteness() -> MaterialConcretenessConfig:
    """Return the typed view over ``material_concreteness.yaml``."""

    raw = load_yaml(_CONFIG_FILENAME)
    thresholds = as_dict(raw.get("detector_thresholds"))

    def _flt(key: str, default: float) -> float:
        try:
            return float(thresholds.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    return MaterialConcretenessConfig(
        version=as_str(raw.get("version")),
        directive=_parse_directive(raw.get("concretization_directive")),
        abstract_markers=as_str_tuple(raw.get("abstract_markers")),
        default_slug_marker=as_str(raw.get("default_slug_marker"), default="default-"),
        abstract_markers_per_kchars=_flt("abstract_markers_per_kchars", 8.0),
        default_slug_ratio_ceiling=_flt("default_slug_ratio_ceiling", 0.6),
    )


# ---------------------------------------------------------------------------
# Writer-side fix: concretization directive
# ---------------------------------------------------------------------------


def render_concretization_directive(
    *,
    genre_terms: tuple[str, ...] | list[str] = (),
    chapter_number: int = 1,
) -> str:
    """Render the compact, soft writer-facing concretization directive.

    ``genre_terms`` / ``chapter_number`` are accepted for call-site parity with
    the other ``render_*_block`` levers; the directive itself is genre-neutral.
    Returns ``""`` if the config carries no directive body.
    """

    config = load_material_concreteness()
    directive = config.directive
    if not directive.body:
        return ""
    lines = [directive.header or "【物料具体化】"]
    lines.append(directive.body.strip())
    if directive.position_hint:
        lines.append(f"位置：{directive.position_hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Planning-side measurement: material abstractness detector (runs on BIBLE)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterialAbstractnessResult:
    """Abstractness of a book's material / bible (NOT of prose)."""

    passed: bool
    marker_hits: int
    marker_density_per_kchars: float
    marker_threshold: float
    default_slug_refs: int
    total_slug_refs: int
    default_slug_ratio: float
    default_slug_ceiling: float
    examples: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "marker_density_per_kchars": self.marker_density_per_kchars,
            "marker_threshold": self.marker_threshold,
            "marker_hits": self.marker_hits,
            "default_slug_refs": self.default_slug_refs,
            "total_slug_refs": self.total_slug_refs,
            "default_slug_ratio": self.default_slug_ratio,
            "default_slug_ceiling": self.default_slug_ceiling,
            "examples": list(self.examples),
        }


# A §-reference looks like "§world_settings/<uuid>/<slug>". Count how many of
# those slugs are the generic genre-neutral "default-*" fallbacks.
_SLUG_REF_RE = re.compile(r"§[^\s/]+/[^\s/]+/([^\s：:，,。)\]]+)")


def detect_material_abstractness(
    material_text: str,
    *,
    marker_threshold_per_kchars: float | None = None,
    default_slug_ceiling: float | None = None,
) -> MaterialAbstractnessResult:
    """Measure how abstract a book's material / bible text is.

    Two signals:

    * **mechanism-marker density** — generic §default vocabulary
      ("商业类型状态引擎 / 状态变化规则 / 品类承诺 …") per 1000 CJK chars;
    * **default-slug ratio** — fraction of ``§dim/uuid/slug`` references whose
      slug is a ``default-*`` genre-neutral fallback.

    Either exceeding its threshold ⇒ material is too abstract and should be
    concretized before prose generation. Soft: callers decide what to do.
    """

    config = load_material_concreteness()
    marker_threshold = (
        config.abstract_markers_per_kchars
        if marker_threshold_per_kchars is None
        else marker_threshold_per_kchars
    )
    slug_ceiling = (
        config.default_slug_ratio_ceiling
        if default_slug_ceiling is None
        else default_slug_ceiling
    )
    text = material_text or ""
    cjk = len(_CJK_RE.findall(text))

    hits = 0
    examples: list[str] = []
    for marker in config.abstract_markers:
        count = text.count(marker)
        if count > 0:
            hits += count
            if len(examples) < 6:
                examples.append(marker)
    density = (hits / cjk * 1000.0) if cjk else 0.0

    slugs = _SLUG_REF_RE.findall(text)
    total_slugs = len(slugs)
    default_slugs = sum(1 for slug in slugs if slug.startswith(config.default_slug_marker))
    slug_ratio = (default_slugs / total_slugs) if total_slugs else 0.0

    passed = density <= marker_threshold and (
        total_slugs == 0 or slug_ratio <= slug_ceiling
    )
    return MaterialAbstractnessResult(
        passed=passed,
        marker_hits=hits,
        marker_density_per_kchars=round(density, 2),
        marker_threshold=marker_threshold,
        default_slug_refs=default_slugs,
        total_slug_refs=total_slugs,
        default_slug_ratio=round(slug_ratio, 3),
        default_slug_ceiling=slug_ceiling,
        examples=tuple(examples),
    )


__all__ = [
    "ConcretizationDirective",
    "MaterialAbstractnessResult",
    "MaterialConcretenessConfig",
    "detect_material_abstractness",
    "load_material_concreteness",
    "render_concretization_directive",
]
