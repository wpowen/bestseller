"""Research-Skill loader for Research Agent, Library Curator, and Forges.

A **Skill** is a compact methodology document that tells an agent *how*
to approach a given genre — which material-library dimensions to
prioritise, what seed queries to fire at the search tools, which
authoritative references to trust, and which tropes to avoid.

The design deliberately mirrors ``config/prompt_packs/`` but is orthogonal:

* **Prompt packs** are *writing-craft* fragments injected into the writer
  prompt (scene_writer, chapter_review, ...).  They teach the writer
  *how to write a scene*.
* **Research skills** are *research-craft* methodology injected into the
  researcher/curator prompt.  They teach the agent *which sources to
  consult and which entities matter for this genre*.

File format
-----------

Skills live under ``config/research_skills/`` organised by
``<genre_slug>/<skill_key>.skill.md``.  The ``_common/`` folder holds
skills that apply to every genre (always loaded alongside any matched
skill).  Each file starts with YAML frontmatter and continues with the
free-form Markdown methodology body::

    ---
    key: xianxia-upgrade
    version: "1.0"
    name: 仙侠升级流研究方法论
    description: ...
    matches_genres: ["仙侠", "修真", "玄幻", "xianxia"]
    matches_sub_genres: ["upgrade", "sect", "宗门"]
    search_dimensions:
      - world_settings
      - power_systems
      - factions
      - character_archetypes
    seed_queries:
      world_settings:
        - "道教宇宙观 天界结构"
        - "仙侠小说 地理分布 差异"
      power_systems:
        - "仙侠 境界划分 对比 网文"
    authoritative_sources:
      - https://zh.wikipedia.org/wiki/道教
      - https://baike.baidu.com/item/仙侠小说
    taboo_patterns:
      - 方域                # global name collision
      - 废灵根 宗门
    ---

    # 方法论正文

    调研 xianxia-upgrade 类题材时，优先挖掘以下层次 ...

The skill is matched against ``(genre, sub_genre)`` using the same
token-normalisation rules as ``infer_default_prompt_pack_key``.  Every
skill flagged ``is_common: true`` is always applied (use for
process-level guidance like "cite sources").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


# ── Pydantic schema ────────────────────────────────────────────────────


class ResearchSkill(BaseModel):
    """Validated research methodology description."""

    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    version: str = Field(default="1.0", min_length=1, max_length=32)
    name: str = Field(min_length=1)
    description: str = Field(default="")
    matches_genres: list[str] = Field(default_factory=list)
    matches_sub_genres: list[str] = Field(default_factory=list)
    search_dimensions: list[str] = Field(default_factory=list)
    seed_queries: dict[str, list[str]] = Field(default_factory=dict)
    authoritative_sources: list[str] = Field(default_factory=list)
    taboo_patterns: list[str] = Field(default_factory=list)
    is_common: bool = Field(default=False)
    methodology_notes: str = Field(default="")

    @field_validator("matches_genres", "matches_sub_genres", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    @field_validator("seed_queries")
    @classmethod
    def _validate_seed_queries(
        cls, value: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        for dim, queries in value.items():
            if not isinstance(queries, list):
                raise ValueError(f"seed_queries[{dim!r}] must be a list of strings")
        return value


# ── Frontmatter parsing ────────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<meta>.*?)\n---\s*(?:\n(?P<body>.*))?\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class ParsedSkillFile:
    metadata: dict[str, Any]
    body: str


def _parse_skill_file(path: Path) -> ParsedSkillFile:
    """Split ``path`` into YAML frontmatter + Markdown body.

    Accepts three formats:

    1. Plain YAML — entire file is a YAML mapping (methodology_notes goes
       inside the mapping).
    2. Markdown with ``---`` frontmatter fences — preferred for skills
       with long prose methodology.
    3. Frontmatter-only (body absent) — equivalent to #1.
    """

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ParsedSkillFile({}, "")

    # ── Frontmatter form ──
    match = _FRONTMATTER_RE.match(text)
    if match:
        meta_block = match.group("meta")
        body = (match.group("body") or "").strip()
        metadata = yaml.safe_load(meta_block) or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"Skill {path}: frontmatter must be a mapping")
        return ParsedSkillFile(metadata, body)

    # ── Plain YAML form ──
    metadata = yaml.safe_load(text) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"Skill {path}: file must be YAML mapping or Markdown with frontmatter")
    body = ""
    return ParsedSkillFile(metadata, body)


def load_skill_file(path: Path) -> ResearchSkill:
    """Load and validate one ``.skill.md`` or ``.skill.yaml`` file."""
    parsed = _parse_skill_file(path)
    metadata = dict(parsed.metadata)
    if parsed.body and not metadata.get("methodology_notes"):
        metadata["methodology_notes"] = parsed.body
    try:
        return ResearchSkill.model_validate(metadata)
    except ValidationError as exc:
        raise ValueError(f"Skill {path} failed validation: {exc}") from exc


# ── Registry ───────────────────────────────────────────────────────────


def _default_skills_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "research_skills"


@lru_cache(maxsize=4)
def load_skill_registry(skills_dir: Path | None = None) -> dict[str, ResearchSkill]:
    """Scan ``config/research_skills/**/*.skill.{md,yaml,yml}`` and build registry."""

    if skills_dir is None:
        skills_dir = _default_skills_dir()
    registry: dict[str, ResearchSkill] = {}
    if not skills_dir.exists():
        logger.info("Research skills directory not found at %s", skills_dir)
        return registry
    patterns = ("*.skill.md", "*.skill.yaml", "*.skill.yml")
    for pattern in patterns:
        for path in sorted(skills_dir.rglob(pattern)):
            try:
                skill = load_skill_file(path)
            except ValueError as exc:
                logger.warning("Skipping invalid skill %s: %s", path, exc)
                continue
            if skill.key in registry:
                logger.warning(
                    "Duplicate skill key %r at %s (first wins)", skill.key, path
                )
                continue
            registry[skill.key] = skill
    return registry


def reset_skill_cache() -> None:
    """Clear the cached registry — useful for tests."""
    load_skill_registry.cache_clear()


# ── Genre matching ─────────────────────────────────────────────────────


def _normalise_tokens(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).lower()


def _matches(skill: ResearchSkill, genre: str, sub_genre: str | None) -> bool:
    if skill.is_common:
        return True
    label = _normalise_tokens(genre, sub_genre)
    if not label:
        return False
    for token in skill.matches_genres + skill.matches_sub_genres:
        if token.lower() in label:
            return True
    return False


def load_skills_for_genre(
    genre: str,
    sub_genre: str | None = None,
    *,
    skills_dir: Path | None = None,
) -> list[ResearchSkill]:
    """Return skills that apply to ``(genre, sub_genre)``.

    Always includes every ``is_common=true`` skill followed by the
    genre-specific matches in file-sort order.  Returns an empty list if
    nothing matches (caller decides whether that's fatal).
    """

    registry = load_skill_registry(skills_dir)
    commons = [s for s in registry.values() if s.is_common]
    specific = [
        s
        for s in registry.values()
        if not s.is_common and _matches(s, genre, sub_genre)
    ]
    return commons + specific


# ── Prompt rendering ───────────────────────────────────────────────────


def render_skills_prompt_block(
    skills: list[ResearchSkill],
    *,
    max_seed_queries_per_dim: int = 4,
    max_sources: int = 6,
    max_taboos: int = 8,
) -> str:
    """Format a set of skills as a Markdown block for injection into prompts.

    The output is deliberately compact — seed queries / sources / taboos
    are capped so a large skill set doesn't blow the context window.
    """

    if not skills:
        return ""

    lines: list[str] = ["## 研究方法论（Research Skills）"]
    for idx, skill in enumerate(skills, start=1):
        lines.append("")
        lines.append(f"### {idx}. {skill.name} (`{skill.key}`, v{skill.version})")
        if skill.description:
            lines.append(skill.description.strip())
        if skill.search_dimensions:
            lines.append(
                "**Search dimensions**: " + ", ".join(skill.search_dimensions)
            )
        if skill.seed_queries:
            lines.append("**Seed queries**:")
            for dim, queries in skill.seed_queries.items():
                capped = queries[:max_seed_queries_per_dim]
                joined = " / ".join(f"“{q}”" for q in capped)
                if joined:
                    lines.append(f"- `{dim}`: {joined}")
        if skill.authoritative_sources:
            lines.append("**Authoritative sources**:")
            for src in skill.authoritative_sources[:max_sources]:
                lines.append(f"- {src}")
        if skill.taboo_patterns:
            capped = skill.taboo_patterns[:max_taboos]
            lines.append("**Taboos (禁止使用)**: " + ", ".join(capped))
        if skill.methodology_notes:
            lines.append("")
            lines.append(skill.methodology_notes.strip())
    return "\n".join(lines).strip()


__all__ = [
    "ResearchSkill",
    "ParsedSkillFile",
    "load_skill_file",
    "load_skill_registry",
    "load_skills_for_genre",
    "reset_skill_cache",
    "render_skills_prompt_block",
]
