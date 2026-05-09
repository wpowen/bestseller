from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

DEFAULT_PROFILE_FILENAME = "ranking-capability-profile.md"
DEFAULT_MAX_PROFILE_CHARS = 6000


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _truncate_profile(text: str, max_chars: int) -> str:
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 40].rstrip() + "\n...[profile truncated]"


def _render_list(label: str, values: object) -> list[str]:
    items = [_clean_text(item) for item in _as_sequence(values)]
    items = [item for item in items if item]
    if not items:
        return []
    return [f"- {label}: " + " / ".join(items)]


def _render_structured_profile(profile: Mapping[str, object]) -> str:
    lines: list[str] = []
    title = _clean_text(profile.get("title")) or _clean_text(profile.get("name"))
    if title:
        lines.append(f"# {title}")

    purpose = _clean_text(profile.get("purpose") or profile.get("usage"))
    if purpose:
        lines.append(purpose)

    lines.extend(_render_list("横向对标结构", profile.get("comparables")))
    lines.extend(_render_list("类型发动机", profile.get("engines")))
    lines.extend(_render_list("写前硬门禁", profile.get("hard_gates")))
    lines.extend(_render_list("批次验收标准", profile.get("batch_acceptance")))
    lines.extend(_render_list("下一批目标", profile.get("next_batch_goals")))

    current = _clean_text(profile.get("current_focus") or profile.get("current_status"))
    if current:
        lines.append(f"- 当前重点: {current}")
    return "\n".join(lines).strip()


def _profile_text_from_container(container: Mapping[str, object]) -> str:
    for key in (
        "ranking_capability_profile_block",
        "ranking_capability_profile_md",
        "commercial_capability_profile_block",
    ):
        text = _clean_text(container.get(key))
        if text:
            return text

    for key in (
        "ranking_capability_profile",
        "commercial_capability_profile",
        "benchmark_capability_profile",
    ):
        raw = container.get(key)
        text = _clean_text(raw)
        if text:
            return text
        profile = _as_mapping(raw)
        rendered = _render_structured_profile(profile) if profile else ""
        if rendered:
            return rendered
    return ""


def load_ranking_capability_profile_text(
    *,
    project_slug: str,
    project_metadata: Mapping[str, object] | None = None,
    story_bible_context: Mapping[str, object] | None = None,
    output_base_dir: str | Path | None = None,
    max_chars: int = DEFAULT_MAX_PROFILE_CHARS,
) -> str:
    """Load the strongest available ranking-capability profile for a project.

    Priority is deliberate:
    1. Project metadata, because it is DB-authoritative for live tasks.
    2. Story-bible context, because materialized context can carry per-volume overrides.
    3. Output package file, because recovered/current tasks may only have disk artifacts.
    """

    for container in (
        _as_mapping(project_metadata),
        _as_mapping(story_bible_context),
    ):
        text = _profile_text_from_container(container)
        if text:
            return _truncate_profile(text, max_chars)

    if output_base_dir is None:
        return ""
    slug = project_slug.strip()
    if not slug:
        return ""
    profile_path = (
        Path(output_base_dir)
        / slug
        / "story-bible"
        / DEFAULT_PROFILE_FILENAME
    )
    try:
        text = profile_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return _truncate_profile(text, max_chars)


def build_ranking_capability_profile_block(
    *,
    project_slug: str,
    project_metadata: Mapping[str, object] | None = None,
    story_bible_context: Mapping[str, object] | None = None,
    output_base_dir: str | Path | None = None,
    max_chars: int = DEFAULT_MAX_PROFILE_CHARS,
) -> str:
    text = load_ranking_capability_profile_text(
        project_slug=project_slug,
        project_metadata=project_metadata,
        story_bible_context=story_bible_context,
        output_base_dir=output_base_dir,
        max_chars=max_chars,
    )
    if not text:
        return ""
    return (
        "【榜单级能力 Profile】\n"
        "用途: 这是本书写前能力约束。正文不得复述本 profile 的条目, "
        "只能把约束落实为情节、规则、代价、关系变化和章末钩子。\n"
        f"{text}"
    )


def apply_ranking_capability_profile_to_context(
    context_packet: object,
    *,
    project_slug: str,
    project_metadata: Mapping[str, object] | None = None,
    story_bible_context: Mapping[str, object] | None = None,
    output_base_dir: str | Path | None = None,
) -> bool:
    if context_packet is None or getattr(
        context_packet,
        "ranking_capability_profile_block",
        None,
    ):
        return False
    block = build_ranking_capability_profile_block(
        project_slug=project_slug,
        project_metadata=project_metadata,
        story_bible_context=story_bible_context,
        output_base_dir=output_base_dir,
    )
    if not block:
        return False
    context_packet.ranking_capability_profile_block = block
    return True


__all__ = [
    "DEFAULT_PROFILE_FILENAME",
    "apply_ranking_capability_profile_to_context",
    "build_ranking_capability_profile_block",
    "load_ranking_capability_profile_text",
]
