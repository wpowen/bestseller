from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from bestseller.domain.decision_policy import DecisionPolicy
from bestseller.services.decision_policy import (
    build_decision_policy_block,
    cautious_survival_policy,
)
from bestseller.services.entry_registry import (
    entry_registry_from_dict,
    render_entry_registry_prompt_block,
)
from bestseller.services.entry_state_ledger import (
    apply_entry_events,
    render_entry_state_ledger_block,
)
from bestseller.services.entry_system_kernel import render_entry_system_kernel_prompt_block
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
    faction_ecology_context_block: str = ""
    relationship_agency_context_block: str = ""
    entry_system_context_block: str = ""
    entry_registry_context_block: str = ""
    entry_state_ledger_block: str = ""
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


_RELATIONSHIP_AGENCY_GENRE_MARKERS = (
    "romance",
    "romantasy",
    "paranormal romance",
    "dark romance",
    "reverse harem",
    "mafia romance",
    "enemies to lovers",
    "relationship",
    "female",
    "no-cp",
    "no_cp",
    "女频",
    "女性",
    "无cp",
    "无CP",
    "言情",
    "恋爱",
    "感情",
    "成长",
)


_FACTION_ECOLOGY_GENRE_MARKERS = (
    "strategy-worldbuilding",
    "kingdom-building",
    "base-building",
    "sect politics",
    "clan",
    "faction",
    "court",
    "political",
    "家族",
    "宗门",
    "门派",
    "势力",
    "阵营",
    "朝堂",
    "权谋",
    "机构",
    "公会",
    "基地",
    "领地",
)


_FACTION_CONTAINER_KEYS = (
    "factions",
    "faction_ecology",
    "faction_pressure",
    "sects",
    "clans",
    "organizations",
    "institutions",
    "forces",
)


_PREMIUM_STATE_LEDGER_KEYS = (
    "progression_events",
    "rule_events",
    "faction_reactions",
    "relationship_events",
    "agency_debts",
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


def _premium_state_ledger(container: Mapping[str, object]) -> dict[str, object]:
    return _as_mapping(container.get("premium_state_ledger"))


def _premium_state_snapshot(container: Mapping[str, object]) -> dict[str, object]:
    return _as_mapping(container.get("premium_state_snapshot"))


def _ledger_entries(container: Mapping[str, object], key: str) -> list[dict[str, object]]:
    ledger = _premium_state_ledger(container)
    entries: list[dict[str, object]] = []
    for raw in _as_sequence(ledger.get(key)):
        item = _as_mapping(raw)
        if item:
            entries.append({**item, "_source_key": f"premium_state_ledger.{key}"})
    return entries


def _snapshot_rule_entries(container: Mapping[str, object]) -> list[dict[str, object]]:
    snapshot = _premium_state_snapshot(container)
    rule_state = _as_mapping(snapshot.get("rule_state"))
    entries: list[dict[str, object]] = []
    for rule_key, raw in rule_state.items():
        item = _as_mapping(raw)
        if not item:
            continue
        entries.append(
            {
                "rule_code": str(rule_key),
                "name": item.get("name") or str(rule_key),
                "visible_effect": item.get("last_visible_effect"),
                "exploitation_potential": item.get("last_exploit"),
                "cost": item.get("last_cost"),
                "chapter_number": item.get("last_chapter"),
                "_source_key": "premium_state_snapshot.rule_state",
            }
        )
    return entries


def _snapshot_faction_entries(container: Mapping[str, object]) -> list[dict[str, object]]:
    snapshot = _premium_state_snapshot(container)
    entries: list[dict[str, object]] = []
    for raw in _as_sequence(snapshot.get("faction_pressure_queue")):
        item = _as_mapping(raw)
        faction = _clean_text(item.get("faction"))
        if not faction:
            continue
        entries.append(
            {
                "name": faction,
                "current_pressure": item.get("trigger"),
                "next_reaction": item.get("reaction"),
                "relationship_to_protagonist": item.get("stance_change"),
                "chapter_number": item.get("chapter_number"),
                "_source_key": "premium_state_snapshot.faction_pressure_queue",
            }
        )
    return entries


def _snapshot_relationship_entries(container: Mapping[str, object]) -> list[dict[str, object]]:
    snapshot = _premium_state_snapshot(container)
    entries: list[dict[str, object]] = []
    relationship_state = _as_mapping(snapshot.get("relationship_state"))
    for relationship_key, raw in relationship_state.items():
        item = _as_mapping(raw)
        if not item:
            continue
        if " -> " in str(relationship_key):
            character_a, character_b = str(relationship_key).split(" -> ", 1)
        else:
            character_a, character_b = str(relationship_key), ""
        axes = _as_mapping(item.get("axes"))
        axis_summary = "; ".join(
            f"{axis}: {state}"
            for axis, state in axes.items()
            if state not in (None, "")
        )
        entries.append(
            {
                "character_a": character_a,
                "character_b": character_b,
                "relationship_type": "state_snapshot",
                "tension_summary": axis_summary,
                "evolution_arc": item.get("last_active_choice"),
                "agency_cost": item.get("last_cost"),
                "chapter_number": item.get("last_chapter"),
                "_source_key": "premium_state_snapshot.relationship_state",
            }
        )

    for raw in _as_sequence(snapshot.get("open_agency_debts")):
        item = _as_mapping(raw)
        owner = _clean_text(item.get("owner"))
        debt = _clean_text(item.get("debt"))
        if not owner or not debt:
            continue
        entries.append(
            {
                "character_a": owner,
                "relationship_type": "agency_debt",
                "tension_summary": debt,
                "agency_cost": item.get("due_window"),
                "chapter_number": item.get("chapter_number"),
                "_source_key": "premium_state_snapshot.open_agency_debts",
            }
        )
    return entries


def _premium_state_ledger_report_warnings(
    metadata: Mapping[str, object],
) -> tuple[str, ...]:
    report = _as_mapping(metadata.get("premium_state_ledger_report"))
    if report.get("passed") is not False:
        return ()
    warnings: list[str] = []
    for raw in _as_sequence(report.get("findings"))[:8]:
        finding = _as_mapping(raw)
        code = _clean_text(finding.get("code"))
        if code:
            warnings.append(f"premium_state_ledger:{code}")
    return tuple(warnings)


def _build_entry_system_context_blocks(
    metadata: Mapping[str, object],
    story_bible_context: Mapping[str, object],
) -> tuple[str, str, str, tuple[str, ...]]:
    warnings: list[str] = []
    kernel_block = ""
    registry_block = ""
    ledger_block = ""
    kernel_payload = _as_mapping(
        metadata.get("entry_system_kernel") or story_bible_context.get("entry_system_kernel")
    )
    if kernel_payload:
        try:
            kernel_block = render_entry_system_kernel_prompt_block(kernel_payload)
        except Exception as exc:
            warnings.append(f"entry_system_kernel_invalid:{exc.__class__.__name__}")

    registry_payload = _as_mapping(
        metadata.get("entry_registry") or story_bible_context.get("entry_registry")
    )
    registry = None
    if registry_payload:
        try:
            registry = entry_registry_from_dict(registry_payload)
            registry_block = render_entry_registry_prompt_block(registry)
        except Exception as exc:
            warnings.append(f"entry_registry_invalid:{exc.__class__.__name__}")

    snapshot_payload = _as_mapping(
        metadata.get("entry_state_snapshot") or story_bible_context.get("entry_state_snapshot")
    )
    ledger_payload = _as_mapping(
        metadata.get("entry_state_ledger") or story_bible_context.get("entry_state_ledger")
    )
    if snapshot_payload:
        try:
            ledger_block = render_entry_state_ledger_block(snapshot_payload)
            if registry is not None:
                registry_block = render_entry_registry_prompt_block(
                    registry,
                    current_state=snapshot_payload,
                )
        except Exception as exc:
            warnings.append(f"entry_state_snapshot_invalid:{exc.__class__.__name__}")
    elif registry is not None and ledger_payload:
        events = (
            ledger_payload.get("events")
            or ledger_payload.get("entry_events")
            or ledger_payload.get("state_events")
        )
        try:
            snapshot = apply_entry_events(registry, _as_sequence(events))
            ledger_block = render_entry_state_ledger_block(snapshot)
            registry_block = render_entry_registry_prompt_block(
                registry,
                current_state=snapshot,
            )
        except Exception as exc:
            warnings.append(f"entry_state_ledger_invalid:{exc.__class__.__name__}")

    return kernel_block, registry_block, ledger_block, tuple(warnings)


def _extract_world_spec(metadata: Mapping[str, object]) -> dict[str, object]:
    premium_world_spec = _as_mapping(metadata.get("premium_world_spec"))
    if premium_world_spec:
        return premium_world_spec

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
    premium_cast_spec = _as_mapping(metadata.get("premium_cast_spec"))
    if premium_cast_spec:
        return premium_cast_spec

    cast_spec = _as_mapping(metadata.get("cast_spec"))
    if cast_spec:
        return cast_spec
    return _as_mapping(_as_mapping(metadata.get("book_spec")).get("cast_spec"))


def _extract_character_config(metadata: Mapping[str, object]) -> dict[str, object]:
    character_config = _as_mapping(metadata.get("character"))
    if character_config:
        return character_config
    return _as_mapping(_as_mapping(metadata.get("book_spec")).get("character"))


def _extract_volume_plan(metadata: Mapping[str, object]) -> list[object]:
    if "premium_volume_plan" in metadata:
        return _as_sequence(metadata.get("premium_volume_plan"))

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


def _should_emit_relationship_agency(
    *,
    genre: str | None,
    sub_genre: str | None,
    metadata: Mapping[str, object],
) -> bool:
    book_spec = _as_mapping(metadata.get("book_spec"))
    character_config = _extract_character_config(metadata)
    haystack_parts = [
        genre or "",
        sub_genre or "",
        str(metadata.get("category") or ""),
        str(metadata.get("prompt_pack_key") or ""),
        str(book_spec.get("genre") or ""),
        str(book_spec.get("sub_genre") or ""),
        str(character_config.get("romance_mode") or ""),
        str(character_config.get("relationship_tension") or ""),
    ]
    haystack = " ".join(haystack_parts).lower()
    return any(marker.lower() in haystack for marker in _RELATIONSHIP_AGENCY_GENRE_MARKERS)


def _should_emit_faction_ecology(
    *,
    genre: str | None,
    sub_genre: str | None,
    metadata: Mapping[str, object],
    world_spec: Mapping[str, object],
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
    return any(marker.lower() in haystack for marker in _FACTION_ECOLOGY_GENRE_MARKERS)


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
    entries.extend(_snapshot_rule_entries(container))
    entries.extend(_ledger_entries(container, "rule_events"))

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


def _render_progression_ledger_appendix(
    entries: tuple[dict[str, object], ...],
    *,
    language: str,
) -> str:
    if not entries:
        return ""
    is_zh = language.lower().startswith("zh")
    lines = ["【近期进阶状态变更】" if is_zh else "[RECENT PROGRESSION STATE CHANGES]"]
    for entry in entries[-8:]:
        chapter = entry.get("chapter_number")
        event_type = _first_text(entry, "event_type", "type", "kind") or "state_change"
        subject = _first_text(entry, "subject", "character", "owner")
        resource = _first_text(entry, "resource_key", "resource", "realm", "technique", "artifact")
        delta = entry.get("delta")
        cause = _first_text(entry, "cause", "reason", "trigger")
        cost = _first_text(entry, "cost", "price", "backlash")
        parts: list[str] = []
        if chapter:
            parts.append(f"Ch{chapter}")
        parts.append(event_type)
        if subject:
            parts.append(subject)
        if resource:
            parts.append(f"{'对象' if is_zh else 'target'}: {resource}")
        if delta not in (None, ""):
            parts.append(f"{'变化' if is_zh else 'delta'}: {delta}")
        if cause:
            parts.append(f"{'因果' if is_zh else 'cause'}: {cause}")
        if cost:
            parts.append(f"{'代价' if is_zh else 'cost'}: {cost}")
        lines.append(("• " if is_zh else "- ") + " | ".join(str(item) for item in parts))
    if is_zh:
        lines.append("硬规则: 后续章节必须承认以上资源、伤势、突破、功法或法宝状态变化。")
    else:
        lines.append(
            "Hard rule: future chapters must acknowledge the resource, injury, "
            "breakthrough, technique, or artifact state changes above.",
        )
    return "\n".join(lines)


def _render_progression_snapshot_appendix(
    metadata: Mapping[str, object],
    *,
    language: str,
) -> str:
    snapshot = _premium_state_snapshot(metadata)
    balances = _as_mapping(snapshot.get("resource_balances"))
    if not balances:
        return ""
    is_zh = language.lower().startswith("zh")
    lines = ["【权威进阶状态快照】" if is_zh else "[AUTHORITATIVE PROGRESSION SNAPSHOT]"]
    for owner, raw_resources in list(balances.items())[:8]:
        resources = _as_mapping(raw_resources)
        if not resources:
            continue
        resource_text = ", ".join(
            f"{resource}={amount}"
            for resource, amount in resources.items()
            if amount not in (None, "")
        )
        if resource_text:
            lines.append(("• " if is_zh else "- ") + f"{owner}: {resource_text}")
    if len(lines) == 1:
        return ""
    if is_zh:
        lines.append("硬规则: 后续章节必须以此快照为准处理资源余额和进阶条件。")
    else:
        lines.append(
            "Hard rule: future chapters must use this snapshot for resource "
            "balances and progression conditions.",
        )
    return "\n".join(lines)


def _relationship_entries_from_container(
    container: Mapping[str, object],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for key in (
        "relationship_contracts",
        "relationship_arcs",
        "relationships",
        "interpersonal_promises",
        "romance_contracts",
        "promises",
    ):
        for raw in _as_sequence(container.get(key)):
            item = _as_mapping(raw)
            if item:
                item = {**item, "_source_key": key}
                entries.append(item)
    entries.extend(_snapshot_relationship_entries(container))
    entries.extend(_ledger_entries(container, "relationship_events"))
    entries.extend(_ledger_entries(container, "agency_debts"))

    series_engine = _as_mapping(container.get("series_engine"))
    for key in (
        "relationship_contracts",
        "relationship_arcs",
        "relationships",
        "interpersonal_promises",
        "romance_contracts",
        "promises",
    ):
        for raw in _as_sequence(series_engine.get(key)):
            item = _as_mapping(raw)
            if item:
                item = {**item, "_source_key": f"series_engine.{key}"}
                entries.append(item)
    return entries


def _relationship_entries_from_cast(
    cast_spec: Mapping[str, object],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    protagonist = _protagonist_from_cast(cast_spec)
    protagonist_name = _clean_text(protagonist.get("name")) or "protagonist"

    for raw in _as_sequence(protagonist.get("relationships")):
        item = _as_mapping(raw)
        if not item:
            continue
        target = _first_text(item, "character", "character_name", "target", "name")
        entries.append(
            {
                **item,
                "_source_key": "cast_spec.protagonist.relationships",
                "character_a": protagonist_name,
                "character_b": target,
                "relationship_type": _first_text(item, "relationship_type", "type", "role"),
                "tension_summary": _first_text(
                    item,
                    "tension_summary",
                    "tension",
                    "private_reality",
                    "public_face",
                ),
            }
        )

    for raw in _as_sequence(cast_spec.get("supporting_cast")):
        item = _as_mapping(raw)
        if not item:
            continue
        relationship = _first_text(item, "relationship_to_protagonist", "relationship")
        evolution = _first_text(item, "evolution_arc", "arc_trajectory", "arc")
        role = _first_text(item, "role", "function")
        if not any((relationship, evolution, role)):
            continue
        entries.append(
            {
                **item,
                "_source_key": "cast_spec.supporting_cast",
                "character_a": _first_text(item, "name", "character") or "supporting cast",
                "character_b": protagonist_name,
                "relationship_type": role,
                "tension_summary": relationship,
                "evolution_arc": evolution,
            }
        )

    for raw in _as_sequence(cast_spec.get("conflict_map")):
        item = _as_mapping(raw)
        if not item:
            continue
        tension = _first_text(
            item,
            "tension",
            "conflict",
            "tension_summary",
            "stakes",
            "private_reality",
        )
        if not tension:
            continue
        entries.append(
            {
                **item,
                "_source_key": "cast_spec.conflict_map",
                "relationship_type": _first_text(item, "relationship_type", "type")
                or "conflict",
                "tension_summary": tension,
            }
        )
    return entries


def _extract_relationship_entries(
    *,
    metadata: Mapping[str, object],
    story_bible_context: Mapping[str, object],
    cast_spec: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    entries = _relationship_entries_from_container(metadata)
    book_spec = _as_mapping(metadata.get("book_spec"))
    entries.extend(_relationship_entries_from_container(book_spec))
    entries.extend(_relationship_entries_from_container(story_bible_context))
    entries.extend(_relationship_entries_from_cast(cast_spec))

    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for entry in entries:
        character_a = _first_text(
            entry,
            "character_a",
            "promisor_label",
            "from_character",
            "source_character",
        )
        character_b = _first_text(
            entry,
            "character_b",
            "promisee_label",
            "character",
            "target",
            "target_character",
        )
        anchor = _first_text(
            entry,
            "id",
            "relationship_id",
            "content",
            "tension_summary",
            "tension",
            "relationship_type",
        )
        dedupe_key = f"{character_a}|{character_b}|{anchor or len(deduped)}".lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(entry)
    return tuple(deduped)


def _relationship_mode_lines(
    *,
    metadata: Mapping[str, object],
    language: str,
) -> list[str]:
    is_zh = language.lower().startswith("zh")
    character_config = _extract_character_config(metadata)
    romance_mode = _clean_text(
        character_config.get("romance_mode") or metadata.get("romance_mode")
    )
    relationship_tension = _clean_text(
        character_config.get("relationship_tension") or metadata.get("relationship_tension")
    )
    lines: list[str] = []
    if romance_mode:
        label = "关系模式" if is_zh else "relationship mode"
        lines.append(f"{label}: {romance_mode}")
    if relationship_tension:
        label = "核心张力" if is_zh else "core tension"
        lines.append(f"{label}: {relationship_tension}")
    return lines


def _relationship_parties(entry: Mapping[str, object]) -> str:
    character_a = _first_text(
        entry,
        "character_a",
        "promisor_label",
        "from_character",
        "source_character",
    )
    character_b = _first_text(
        entry,
        "character_b",
        "promisee_label",
        "character",
        "target",
        "target_character",
    )
    if character_a and character_b:
        return f"{character_a} -> {character_b}"
    if character_a:
        return character_a
    if character_b:
        return character_b
    return _first_text(entry, "name", "title", "relationship_type", "type") or "relationship"


def _render_relationship_entry(entry: Mapping[str, object], *, is_zh: bool) -> str:
    parties = _relationship_parties(entry)
    relation_type = _first_text(entry, "relationship_type", "type", "kind", "role")
    tension = _first_text(
        entry,
        "tension_summary",
        "tension",
        "private_reality",
        "public_face",
        "relationship_to_protagonist",
        "content",
    )
    evolution = _first_text(entry, "evolution_arc", "arc_trajectory", "planned_shift")
    promise = _first_text(entry, "promise", "content", "oath", "commitment")
    agency_cost = _first_text(entry, "agency_cost", "cost", "price", "tradeoff")

    parts = [parties]
    if relation_type:
        parts.append(f"{'类型' if is_zh else 'type'}: {relation_type}")
    if tension:
        parts.append(f"{'张力' if is_zh else 'tension'}: {tension}")
    if evolution:
        parts.append(f"{'阶段变化' if is_zh else 'stage shift'}: {evolution}")
    if promise:
        parts.append(f"{'承诺' if is_zh else 'promise'}: {promise}")
    if agency_cost:
        parts.append(f"{'选择代价' if is_zh else 'agency cost'}: {agency_cost}")
    return ("• " if is_zh else "- ") + " | ".join(parts)


def _build_relationship_agency_context_block(
    *,
    metadata: Mapping[str, object],
    story_bible_context: Mapping[str, object],
    cast_spec: Mapping[str, object],
    genre: str | None,
    sub_genre: str | None,
    language: str,
) -> tuple[str, tuple[str, ...]]:
    explicit_block = _clean_text(
        metadata.get("relationship_agency_context_block")
        or metadata.get("relationship_agency_block")
        or metadata.get("romance_contract_block")
        or metadata.get("agency_contract_block"),
    )
    if explicit_block:
        return explicit_block, ()

    entries = _extract_relationship_entries(
        metadata=metadata,
        story_bible_context=story_bible_context,
        cast_spec=cast_spec,
    )
    if not entries:
        if _should_emit_relationship_agency(
            genre=genre,
            sub_genre=sub_genre,
            metadata=metadata,
        ):
            return "", ("relationship_agency_missing",)
        return "", ()

    is_zh = language.lower().startswith("zh")
    lines = (
        ["【关系张力与主角能动性约束】"]
        if is_zh
        else ["[RELATIONSHIP / AGENCY CONSTRAINTS]"]
    )
    lines.extend(_relationship_mode_lines(metadata=metadata, language=language))
    for entry in entries[:8]:
        lines.append(_render_relationship_entry(entry, is_zh=is_zh))
    if is_zh:
        lines.append(
            "硬规则: 每场关系戏必须改变距离/信任/权力/误会/承诺中的至少一项; "
            "主角必须有主动选择和代价; 无CP项目不得把成长动力滑向隐藏恋爱; "
            "恋爱/romantasy项目不得让关系原地暧昧。",
        )
    else:
        lines.append(
            "Hard rule: every relationship beat must change at least one of distance, "
            "trust, power, misunderstanding, or promise; the protagonist must make an "
            "active choice with a cost; no-CP projects must not drift into hidden romance; "
            "romance/romantasy projects must not let tension idle in static ambiguity.",
        )
    return "\n".join(lines), ()


def _faction_entries_from_container(container: Mapping[str, object]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for key in _FACTION_CONTAINER_KEYS:
        value = container.get(key)
        for raw in _as_sequence(value):
            item = _as_mapping(raw)
            if item:
                entries.append({**item, "_source_key": key})

        keyed = _as_mapping(value)
        if keyed and any(field in keyed for field in ("name", "goal", "method")):
            entries.append({**keyed, "_source_key": key})
        elif keyed:
            for name, raw in keyed.items():
                item = _as_mapping(raw)
                if item:
                    entries.append({"name": str(name), **item, "_source_key": key})

    series_engine = _as_mapping(container.get("series_engine"))
    for key in _FACTION_CONTAINER_KEYS:
        for raw in _as_sequence(series_engine.get(key)):
            item = _as_mapping(raw)
            if item:
                entries.append({**item, "_source_key": f"series_engine.{key}"})
    entries.extend(_snapshot_faction_entries(container))
    entries.extend(_ledger_entries(container, "faction_reactions"))
    return entries


def _extract_faction_entries(
    *,
    metadata: Mapping[str, object],
    story_bible_context: Mapping[str, object],
    world_spec: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    entries = _faction_entries_from_container(metadata)
    entries.extend(_faction_entries_from_container(_as_mapping(metadata.get("book_spec"))))
    entries.extend(_faction_entries_from_container(world_spec))
    entries.extend(_faction_entries_from_container(story_bible_context))

    active_factions = _as_sequence(story_bible_context.get("active_factions"))
    for raw_name in active_factions:
        name = _clean_text(raw_name)
        if name:
            entries.append({"name": name, "_source_key": "active_factions"})

    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for entry in entries:
        name = _first_text(entry, "name", "faction", "organization", "sect", "clan")
        anchor = _first_text(
            entry,
            "goal",
            "method",
            "relationship_to_protagonist",
            "current_pressure",
        )
        dedupe_key = f"{name or len(deduped)}|{anchor or ''}".lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(entry)
    return tuple(deduped)


def _render_faction_entry(entry: Mapping[str, object], *, is_zh: bool) -> str:
    name = _first_text(entry, "name", "faction", "organization", "sect", "clan") or "faction"
    goal = _first_text(entry, "goal", "interest", "core_goal", "agenda")
    method = _first_text(entry, "method", "strategy", "operating_method", "means")
    relation = _first_text(
        entry,
        "relationship_to_protagonist",
        "stance",
        "attitude",
        "relation",
    )
    internal_conflict = _first_text(
        entry,
        "internal_conflict",
        "internal_tension",
        "contradiction",
    )
    pressure = _first_text(entry, "current_pressure", "pressure", "active_pressure")
    reaction = _first_text(entry, "next_reaction", "reaction", "reaction_plan")
    resource = _first_text(entry, "resource_need", "resource", "scarce_resource")

    parts = [name]
    if goal:
        parts.append(f"{'目标' if is_zh else 'goal'}: {goal}")
    if method:
        parts.append(f"{'手段' if is_zh else 'method'}: {method}")
    if relation:
        parts.append(f"{'对主角关系' if is_zh else 'stance to protagonist'}: {relation}")
    if internal_conflict:
        parts.append(f"{'内部矛盾' if is_zh else 'internal conflict'}: {internal_conflict}")
    if resource:
        parts.append(f"{'资源需求' if is_zh else 'resource need'}: {resource}")
    if pressure:
        parts.append(f"{'当前压力' if is_zh else 'current pressure'}: {pressure}")
    if reaction:
        parts.append(f"{'下一步反应' if is_zh else 'next reaction'}: {reaction}")
    return ("• " if is_zh else "- ") + " | ".join(parts)


def _build_faction_ecology_context_block(
    *,
    metadata: Mapping[str, object],
    story_bible_context: Mapping[str, object],
    world_spec: Mapping[str, object],
    genre: str | None,
    sub_genre: str | None,
    language: str,
) -> tuple[str, tuple[str, ...]]:
    explicit_block = _clean_text(
        metadata.get("faction_ecology_context_block")
        or metadata.get("faction_ecology_block")
        or metadata.get("faction_reaction_block"),
    )
    if explicit_block:
        return explicit_block, ()

    entries = _extract_faction_entries(
        metadata=metadata,
        story_bible_context=story_bible_context,
        world_spec=world_spec,
    )
    if not entries:
        if _should_emit_faction_ecology(
            genre=genre,
            sub_genre=sub_genre,
            metadata=metadata,
            world_spec=world_spec,
        ):
            return "", ("faction_ecology_missing",)
        return "", ()

    is_zh = language.lower().startswith("zh")
    lines = (
        ["【阵营生态与反应压力约束】"]
        if is_zh
        else ["[FACTION ECOLOGY / REACTION CONSTRAINTS]"]
    )
    for entry in entries[:8]:
        lines.append(_render_faction_entry(entry, is_zh=is_zh))
    if is_zh:
        lines.append(
            "硬规则: 主角影响资源、身份、规则或公共声望时, 至少一个相关势力必须产生差异化反应, "
            "或明确说明为什么暂不反应; 不得只写“所有势力震惊”; 同一势力再次出现必须推进目标、"
            "手段、内部矛盾或对主角立场之一。",
        )
    else:
        lines.append(
            "Hard rule: when the protagonist affects resources, status, rules, or public "
            "reputation, at least one relevant faction must produce a differentiated "
            "reaction or a concrete reason for no reaction; do not write only 'all "
            "factions are shocked'; repeated faction appearances must advance goal, "
            "method, internal conflict, or stance toward the protagonist.",
        )
    return "\n".join(lines), ()


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

    warnings: list[str] = list(_premium_state_ledger_report_warnings(metadata))
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
    progression_snapshot_block = _render_progression_snapshot_appendix(
        metadata,
        language=language,
    )
    if progression_snapshot_block:
        progression_block = (
            f"{progression_block}\n{progression_snapshot_block}"
            if progression_block
            else progression_snapshot_block
        )
    progression_ledger_entries = (
        *_ledger_entries(metadata, "progression_events"),
        *_ledger_entries(story_context, "progression_events"),
    )
    progression_ledger_block = _render_progression_ledger_appendix(
        progression_ledger_entries,
        language=language,
    )
    if progression_ledger_block:
        progression_block = (
            f"{progression_block}\n{progression_ledger_block}"
            if progression_block
            else progression_ledger_block
        )

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
    faction_block, faction_warnings = _build_faction_ecology_context_block(
        metadata=metadata,
        story_bible_context=story_context,
        world_spec=world_spec,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    warnings.extend(faction_warnings)
    relationship_block, relationship_warnings = _build_relationship_agency_context_block(
        metadata=metadata,
        story_bible_context=story_context,
        cast_spec=cast_spec,
        genre=genre,
        sub_genre=sub_genre,
        language=language,
    )
    warnings.extend(relationship_warnings)
    (
        entry_system_block,
        entry_registry_block,
        entry_state_ledger_block,
        entry_warnings,
    ) = _build_entry_system_context_blocks(metadata, story_context)
    warnings.extend(entry_warnings)
    return PremiumGenreEngineBlocks(
        progression_context_block=progression_block,
        decision_policy_block=decision_block,
        rule_system_context_block=rule_block,
        faction_ecology_context_block=faction_block,
        relationship_agency_context_block=relationship_block,
        entry_system_context_block=entry_system_block,
        entry_registry_context_block=entry_registry_block,
        entry_state_ledger_block=entry_state_ledger_block,
        warnings=tuple(warnings),
    )


__all__ = [
    "PremiumGenreEngineBlocks",
    "build_premium_genre_engine_blocks",
]
