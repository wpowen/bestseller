"""Book-level imagery-system lever (``config/imagery_system.yaml``).

Closes the real LitStyle gap the single-sentence ``prose_craft`` and single-scene
``scene_grounding`` levers cannot: a book-wide system of 2-3 core images that
*recur and advance in meaning*. Two pure stages (no LLM/DB here — the caller does
the LLM call, exactly like the judge):

* **design** — :func:`build_imagery_designer_prompt` + :func:`parse_imagery_artifact`
  produce a per-book ``ImagerySystemArtifact`` (≤3 images, each with a concrete
  carrier + emotion/theme function + first/transform/payoff). Stored in
  ``story_bible.imagery_system`` at conception.
* **recall** — :func:`render_imagery_system_block` injects a compact, soft
  per-chapter reminder ("if you use a core image this chapter, advance its
  meaning a step"). Empty string when no artifact exists → graceful no-op.

Anti-regression (same discipline as prose_craft/scene_grounding): ≤3 images,
concrete carriers only, modern genres use this-world objects (no 古风 filter),
soft — never a gate.
"""

# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from bestseller.services.quality_levers._loader import as_dict, as_str, as_str_tuple, load_yaml

_CONFIG_FILENAME = "imagery_system.yaml"
_MAX_IMAGES = 3


# ---------------------------------------------------------------------------
# Typed view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageEntry:
    """One core image: a concrete carrier + its emotion/theme functions + arc."""

    name: str
    carrier: str
    emotion_fn: str = ""
    theme_fn: str = ""
    first_appearance: str = ""
    transform: str = ""
    payoff: str = ""


@dataclass(frozen=True)
class ImagerySystemArtifact:
    """A book's designed imagery system (≤3 images)."""

    theme_core: str
    images: tuple[ImageEntry, ...]

    def __bool__(self) -> bool:
        return bool(self.images)


@dataclass(frozen=True)
class ImagerySystemConfig:
    technique_principle: str
    technique_structure: str
    technique_purple_risk: str
    designer_system: str
    designer_output_schema: str
    designer_user_template: str
    strong_genres: tuple[str, ...]
    careful_genres: tuple[str, ...]
    purple_guard_fixes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_imagery_system_config() -> ImagerySystemConfig:
    raw = load_yaml(_CONFIG_FILENAME)
    technique = as_dict(raw.get("technique"))
    designer = as_dict(raw.get("designer"))
    emphasis = as_dict(raw.get("genre_emphasis"))
    guard = raw.get("purple_guard")
    fixes: list[str] = []
    if isinstance(guard, (list, tuple)):
        for entry in guard:
            data = as_dict(entry)
            bad = as_str(data.get("bad"))
            if bad:
                fixes.append(bad)
    return ImagerySystemConfig(
        technique_principle=as_str(technique.get("principle")),
        technique_structure=as_str(technique.get("structure")),
        technique_purple_risk=as_str(technique.get("purple_risk")),
        designer_system=as_str(designer.get("system")),
        designer_output_schema=as_str(designer.get("output_schema")),
        designer_user_template=as_str(designer.get("user_template")),
        strong_genres=as_str_tuple(emphasis.get("strong")),
        careful_genres=as_str_tuple(emphasis.get("careful")),
        purple_guard_fixes=tuple(fixes),
    )


# ---------------------------------------------------------------------------
# Design-stage prompt + parse (pure; caller runs the LLM)
# ---------------------------------------------------------------------------


def build_imagery_designer_prompt(
    *, premise: str, genre: str, config: ImagerySystemConfig | None = None
) -> tuple[str, str]:
    """Return ``(system, user)`` for designing a book's imagery system."""

    config = config or load_imagery_system_config()
    system = config.designer_system + "\n输出 JSON schema：\n" + config.designer_output_schema
    user = config.designer_user_template.format(genre=genre or "", premise=premise or "")
    return system, user


def parse_imagery_artifact(
    payload: Mapping[str, Any] | None, *, max_images: int = _MAX_IMAGES
) -> ImagerySystemArtifact:
    """Parse a designer payload (or a stored bible blob) into an artifact (≤3)."""

    data = dict(payload) if isinstance(payload, Mapping) else {}
    images_raw = data.get("images")
    images: list[ImageEntry] = []
    if isinstance(images_raw, Sequence) and not isinstance(images_raw, (str, bytes, bytearray)):
        for entry in images_raw:
            d = as_dict(entry)
            name = as_str(d.get("name"))
            carrier = as_str(d.get("carrier"))
            if not (name or carrier):
                continue
            images.append(
                ImageEntry(
                    name=name or carrier,
                    carrier=carrier or name,
                    emotion_fn=as_str(d.get("emotion_fn")),
                    theme_fn=as_str(d.get("theme_fn")),
                    first_appearance=as_str(d.get("first_appearance")),
                    transform=as_str(d.get("transform")),
                    payoff=as_str(d.get("payoff")),
                )
            )
            if len(images) >= max_images:
                break
    return ImagerySystemArtifact(theme_core=as_str(data.get("theme_core")), images=tuple(images))


def extract_imagery_system(story_bible: Mapping[str, Any] | None) -> ImagerySystemArtifact:
    """Pull a designed imagery system out of ``story_bible.imagery_system``.

    Returns an empty artifact (falsy) when absent → the recall block no-ops.
    """

    if not isinstance(story_bible, Mapping):
        return ImagerySystemArtifact(theme_core="", images=())
    blob = story_bible.get("imagery_system")
    if isinstance(blob, Mapping):
        return parse_imagery_artifact(blob)
    return ImagerySystemArtifact(theme_core="", images=())


# ---------------------------------------------------------------------------
# Recall-stage render (pure, soft, ""-on-empty)
# ---------------------------------------------------------------------------


def _is_careful_genre(genre_terms: Sequence[str], config: ImagerySystemConfig) -> bool:
    return any(
        careful in term for term in genre_terms for careful in config.careful_genres
    )


def render_imagery_system_block(
    *,
    artifact: ImagerySystemArtifact | None,
    genre_terms: Sequence[str] = (),
    chapter_number: int = 1,
    config: ImagerySystemConfig | None = None,
) -> str:
    """Render the compact, soft per-chapter imagery-recall fragment.

    Returns ``""`` when there is no designed imagery system, so callers can append
    it unconditionally.
    """

    if not artifact or not artifact.images:
        return ""
    config = config or load_imagery_system_config()
    terms = tuple(str(t).strip() for t in genre_terms if str(t).strip())

    lines: list[str] = [
        "【意象系统 · 本书主意象（回返时让含义推进一步，soft，不强制每章出现）】",
    ]
    if artifact.theme_core:
        lines.append(f"主题核：{artifact.theme_core}")
    lines.append(
        "本书只守这 2-3 个主意象；本章若自然用到，让它比上一次的含义更进一层"
        "（别堆砌、≤3 个、别每章换新意象）："
    )
    # Spotlight one image this chapter (rotation) so emphasis varies, but list all.
    spotlight = (max(1, int(chapter_number)) - 1) % len(artifact.images)
    for idx, image in enumerate(artifact.images):
        fn = " → ".join(p for p in (image.emotion_fn, image.theme_fn) if p)
        line = f"- {image.name}（载体：{image.carrier}）"
        if fn:
            line += f"：{fn}"
        if idx == spotlight:
            line += "　【本章可优先回返】"
        lines.append(line)
    if _is_careful_genre(terms, config):
        lines.append("⚠ 本题材意象用本世界的物（不要套古风意象如孤舟/残月/江湖）。")
    else:
        lines.append("⚠ 意象载体要具体到能成像，忌空泛大词；一段最多 2 个意象并置。")
    return "\n".join(lines)


__all__ = [
    "ImageEntry",
    "ImagerySystemArtifact",
    "ImagerySystemConfig",
    "build_imagery_designer_prompt",
    "extract_imagery_system",
    "load_imagery_system_config",
    "parse_imagery_artifact",
    "render_imagery_system_block",
]
