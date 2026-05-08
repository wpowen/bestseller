from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from bestseller.domain.decision_policy import DecisionPolicy
from bestseller.services.decision_policy import (
    build_decision_policy_block,
    cautious_survival_policy,
)
from bestseller.services.progression import (
    build_progression_context_block,
    materialize_progression_context,
    validate_realm_ladder,
)


@dataclass(frozen=True, slots=True)
class PremiumGenreEngineBlocks:
    """Pre-rendered premium genre blocks for the live scene writer context."""

    progression_context_block: str = ""
    decision_policy_block: str = ""
    rule_system_context_block: str = ""
    warnings: tuple[str, ...] = ()


_PROGRESSION_GENRE_MARKERS = (
    "xianxia",
    "cultivation",
    "修仙",
    "仙侠",
    "玄幻",
    "凡人",
    "升级",
    "progression",
    "litrpg",
    "system apocalypse",
    "系统",
)


_RULE_SYSTEM_GENRE_MARKERS = (
    "horror",
    "occult",
    "folk",
    "民俗",
    "灵异",
    "规则",
    "无限",
    "诡异",
    "捞尸",
    "风水",
)


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _extract_world_spec(metadata: Mapping[str, object]) -> dict[str, object]:
    world_spec = _as_mapping(metadata.get("world_spec"))
    if world_spec:
        return world_spec

    book_spec = _as_mapping(metadata.get("book_spec"))
    for key in ("world_spec", "world"):
        nested = _as_mapping(book_spec.get(key))
        if nested:
            return nested

    power_system = _as_mapping(metadata.get("power_system"))
    if not power_system:
        return {}
    return {
        "world_name": metadata.get("world_name"),
        "world_premise": metadata.get("world_premise"),
        "power_structure": metadata.get("power_structure"),
        "forbidden_zones": metadata.get("forbidden_zones"),
        "power_system": power_system,
    }


def _extract_cast_spec(metadata: Mapping[str, object]) -> dict[str, object]:
    cast_spec = _as_mapping(metadata.get("cast_spec"))
    if cast_spec:
        return cast_spec
    return _as_mapping(_as_mapping(metadata.get("book_spec")).get("cast_spec"))


def _extract_volume_plan(metadata: Mapping[str, object]) -> list[object]:
    volume_plan = _as_sequence(metadata.get("volume_plan"))
    if volume_plan:
        return volume_plan
    return _as_sequence(_as_mapping(metadata.get("book_spec")).get("volume_plan"))


def _protagonist_from_cast(cast_spec: Mapping[str, object]) -> dict[str, object]:
    return _as_mapping(cast_spec.get("protagonist"))


def _protagonist_name(cast_spec: Mapping[str, object]) -> str | None:
    return _clean_text(_protagonist_from_cast(cast_spec).get("name"))


def _explicit_decision_policy_data(
    metadata: Mapping[str, object],
    cast_spec: Mapping[str, object],
) -> dict[str, object]:
    for key in ("decision_policy", "protagonist_decision_policy"):
        policy_data = _as_mapping(metadata.get(key))
        if policy_data:
            return policy_data

    protagonist = _protagonist_from_cast(cast_spec)
    for key in ("decision_policy", "protagonist_decision_policy"):
        policy_data = _as_mapping(protagonist.get(key))
        if policy_data:
            return policy_data
    return _as_mapping(_as_mapping(protagonist.get("metadata")).get("decision_policy"))


def _should_default_to_cautious_survival(
    *,
    genre: str | None,
    sub_genre: str | None,
    metadata: Mapping[str, object],
    world_spec: Mapping[str, object],
) -> bool:
    book_spec = _as_mapping(metadata.get("book_spec"))
    power_system = _as_mapping(world_spec.get("power_system"))
    haystack_parts: list[str] = [
        genre or "",
        sub_genre or "",
        str(metadata.get("category") or ""),
        str(metadata.get("prompt_pack_key") or ""),
        str(book_spec.get("genre") or ""),
        str(book_spec.get("sub_genre") or ""),
        str(power_system.get("name") or ""),
    ]
    haystack = " ".join(haystack_parts).lower()
    return any(marker in haystack for marker in _PROGRESSION_GENRE_MARKERS)


def _should_emit_rule_system(
    *,
    genre: str | None,
    sub_genre: str | None,
    metadata: Mapping[str, object],
) -> bool:
    book_spec = _as_mapping(metadata.get("book_spec"))
    haystack_parts = [
        genre or "",
        sub_genre or "",
        str(metadata.get("category") or ""),
        str(metadata.get("prompt_pack_key") or ""),
        str(book_spec.get("genre") or ""),
        str(book_spec.get("sub_genre") or ""),
    ]
    haystack = " ".join(haystack_parts).lower()
    return any(marker in haystack for marker in _RULE_SYSTEM_GENRE_MARKERS)


def _build_decision_policy_block(
    *,
    metadata: Mapping[str, object],
    cast_spec: Mapping[str, object],
    world_spec: Mapping[str, object],
    genre: str | None,
    sub_genre: str | None,
    language: str,
) -> tuple[str, tuple[str, ...]]:
    explicit_block = _clean_text(metadata.get("decision_policy_block"))
    if explicit_block:
        return explicit_block, ()

    protagonist_name = _protagonist_name(cast_spec)
    policy_data = _explicit_decision_policy_data(metadata, cast_spec)
    if policy_data:
        candidate = {**policy_data}
        if protagonist_name and not candidate.get("character_name"):
            candidate["character_name"] = protagonist_name
        try:
            policy = DecisionPolicy.model_validate(candidate)
        except Exception as exc:
            return "", (f"decision_policy_invalid:{exc.__class__.__name__}",)
        return build_decision_policy_block(policy, language=language), ()

    if (
        protagonist_name
        and _should_default_to_cautious_survival(
            genre=genre,
            sub_genre=sub_genre,
            metadata=metadata,
            world_spec=world_spec,
        )
    ):
        return (
            build_decision_policy_block(
                cautious_survival_policy(protagonist_name),
                language=language,
            ),
            (),
        )
    return "", ()


def _rule_entries_from_container(container: Mapping[str, object]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for key in ("rule_ledger", "rules", "world_rules"):
        for raw in _as_sequence(container.get(key)):
            item = _as_mapping(raw)
            if item:
                entries.append(item)

    series_engine = _as_mapping(container.get("series_engine"))
    for key in ("rule_ledger", "rules", "world_rules"):
        for raw in _as_sequence(series_engine.get(key)):
            item = _as_mapping(raw)
            if item:
                entries.append(item)
    return entries


def _extract_rule_entries(
    metadata: Mapping[str, object],
    story_bible_context: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    entries = _rule_entries_from_container(metadata)
    book_spec = _as_mapping(metadata.get("book_spec"))
    entries.extend(_rule_entries_from_container(book_spec))
    entries.extend(_rule_entries_from_container(story_bible_context))

    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for entry in entries:
        rule_id = _clean_text(
            entry.get("id")
            or entry.get("rule_id")
            or entry.get("rule_code")
            or entry.get("code")
            or entry.get("name")
            or entry.get("rule")
        )
        name = _clean_text(entry.get("name") or entry.get("rule") or entry.get("title"))
        dedupe_key = (rule_id or name or str(len(deduped))).lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(entry)
    return tuple(deduped)


def _first_text(entry: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        text = _clean_text(entry.get(key))
        if text:
            return text
    return None


def _build_rule_system_context_block(
    *,
    metadata: Mapping[str, object],
    story_bible_context: Mapping[str, object],
    genre: str | None,
    sub_genre: str | None,
    language: str,
) -> tuple[str, tuple[str, ...]]:
    explicit_block = _clean_text(
        metadata.get("rule_system_context_block")
        or metadata.get("rule_system_block")
        or metadata.get("rule_lattice_block"),
    )
    if explicit_block:
        return explicit_block, ()

    entries = _extract_rule_entries(metadata, story_bible_context)
    if not entries:
        if _should_emit_rule_system(genre=genre, sub_genre=sub_genre, metadata=metadata):
            return "", ("rule_system_missing",)
        return "", ()

    is_zh = language.lower().startswith("zh")
    lines = ["【规则系统约束】" if is_zh else "[RULE SYSTEM CONSTRAINTS]"]
    for index, entry in enumerate(entries[:8], start=1):
        rule_id = _first_text(entry, "id", "rule_id", "rule_code", "code") or f"R-{index:03d}"
        name = _first_text(entry, "name", "rule", "title") or "unnamed rule"
        description = _first_text(entry, "description", "summary", "story_consequence")
        visible = _first_text(entry, "visible_effect", "surface_effect", "表层效果")
        exploit = _first_text(
            entry,
            "exploitation_potential",
            "solution",
            "loophole",
            "破局方法",
        )
        cost = _first_text(
            entry,
            "cost",
            "payoff",
            "cost_or_payoff",
            "future_backlash",
            "代价",
        )
        if is_zh:
            parts = [f"{rule_id}/{name}"]
            if description:
                parts.append(description)
            if visible:
                parts.append(f"可见效果: {visible}")
            if exploit:
                parts.append(f"破局路径: {exploit}")
            if cost:
                parts.append(f"代价/反噬: {cost}")
            lines.append("• " + " | ".join(parts))
        else:
            parts = [f"{rule_id}/{name}"]
            if description:
                parts.append(description)
            if visible:
                parts.append(f"visible effect: {visible}")
            if exploit:
                parts.append(f"exploit path: {exploit}")
            if cost:
                parts.append(f"cost/backlash: {cost}")
            lines.append("- " + " | ".join(parts))
    if is_zh:
        lines.append(
            "硬规则: 每条规则必须有可见效果、破局路径和代价/反噬; 不得只当氛围描写; "
            "同一规则再次出现必须升级信息、扩大影响或付出新代价。",
        )
    else:
        lines.append(
            "Hard rule: every rule must expose visible effect, exploit path, and cost/backlash; "
            "do not use rules as atmosphere only; repeated rules must add new information, "
            "larger impact, or a new cost.",
        )
    return "\n".join(lines), ()


def build_premium_genre_engine_blocks(
    *,
    project_metadata: Mapping[str, object] | None,
    story_bible_context: Mapping[str, object] | None = None,
    genre: str | None,
    sub_genre: str | None = None,
    language: str = "zh-CN",
    current_volume: int | None = None,
) -> PremiumGenreEngineBlocks:
    """Build prompt-ready blocks from project planning artifacts.

    This is the bridge between persisted story-bible metadata and the live
    ``SceneWriterContextPacket``. It is intentionally best-effort: legacy
    projects without structured specs get empty blocks instead of hard failures.
    """
    metadata = _as_mapping(project_metadata)
    story_context = _as_mapping(story_bible_context)
    if not metadata:
        metadata = {}

    warnings: list[str] = []
    world_spec = _extract_world_spec(metadata)
    cast_spec = _extract_cast_spec(metadata)
    volume_plan = _extract_volume_plan(metadata)

    progression_block = ""
    if world_spec:
        try:
            progression_context = materialize_progression_context(
                world_spec,
                cast_spec or None,
                volume_plan or None,
                current_volume=current_volume,
            )
            ladder_report = validate_realm_ladder(progression_context.system)
            if not ladder_report.passed:
                warnings.extend(
                    f"progression:{finding.code}" for finding in ladder_report.findings
                )
            progression_block = build_progression_context_block(
                progression_context,
                language=language,
            )
        except Exception as exc:
            warnings.append(f"progression_context_invalid:{exc.__class__.__name__}")

    decision_block, decision_warnings = _build_decision_policy_block(
        metadata=metadata,
        cast_spec=cast_spec,
        world_spec=world_spec,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    warnings.extend(decision_warnings)
    rule_block, rule_warnings = _build_rule_system_context_block(
        metadata=metadata,
        story_bible_context=story_context,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    warnings.extend(rule_warnings)
    return PremiumGenreEngineBlocks(
        progression_context_block=progression_block,
        decision_policy_block=decision_block,
        rule_system_context_block=rule_block,
        warnings=tuple(warnings),
    )


__all__ = [
    "PremiumGenreEngineBlocks",
    "build_premium_genre_engine_blocks",
]
