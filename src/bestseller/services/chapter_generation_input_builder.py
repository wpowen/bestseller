from __future__ import annotations

# ruff: noqa: ANN401
from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.domain.chapter_generation_input import ChapterGenerationInputBundle
from bestseller.services.chapter_llm_quality_judge import chapter_commercial_thresholds
from bestseller.services.methodology_application_gate import build_methodology_application_contract

_METHODOLOGY_BLOCK_ATTRS: tuple[str, ...] = (
    "reader_contract_block",
    "hype_constraints_block",
    "voice_dna_block",
    "dialogue_voice_block",
    "chapter_market_constraints_block",
    "signature_scene_block",
    "prior_persona_feedback_block",
    "hook_echo_block",
    "exposition_density_block",
    "canon_guardrails_block",
    "timeline_canon_block",
    "scene_coherence_block",
    "character_role_block",
    "chapter_length_block",
)

_REQUIRED_CONTEXT_KEYS: tuple[str, ...] = (
    "chapter.goal",
    "scenes",
    "scenes.purpose",
    "scenes.entry_exit",
    "scenes.methodology_contract",
    "story_bible",
    "chapter_acceptance_contract",
    "continuity.previous_scene_summaries",
    "quality_targets",
)

_TRANSIENT_CHAPTER_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "chapter_first_generation",
        "chapter_generation_input_bundle",
        "generation_input_bundle",
        "front10_regen_chapter_snapshot",
        "chapter_outline_readiness_report",
        "write_safety_gate_report",
        "quality_gate_report",
    }
)

_TRANSIENT_SCENE_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "auto_repair_adjusted_target_word_count",
        "auto_repair_block_codes",
        "auto_repair_hint",
        "auto_repair_length_scale",
        "auto_repair_min_scene_target_floor",
        "auto_repair_original_target_word_count",
        "auto_repair_scene_target_cap",
        "auto_repair_source_block_code",
        "auto_repair_target_word_count_clamped",
        "auto_repair_attempt",
        # Creative controls below must come from the current scene contract.
        # Leaving old top-level values in metadata lets previous failed
        # generations re-pollute the next framework run.
        "action_sequence",
        "cut_point",
        "ending_hook_payload",
        "gate_function",
        "information_control_mode",
        "reader_payoff",
        "relationship_debts",
        "signature_image",
        "visible_progress",
    }
)


def build_chapter_generation_input_bundle(
    *,
    project: Any,
    chapter: Any,
    scenes: Sequence[Any],
    context_packet: Any,
    target_word_count: int,
) -> ChapterGenerationInputBundle:
    methodology_blocks = {
        attr: str(getattr(context_packet, attr, "") or "").strip()
        for attr in _METHODOLOGY_BLOCK_ATTRS
        if str(getattr(context_packet, attr, "") or "").strip()
    }
    scene_payloads = _scene_payloads(scenes)
    continuity = {
        "previous_scene_summaries": _dump_context_items(
            getattr(context_packet, "previous_scene_summaries", None) or []
        ),
        "recent_timeline_events": _dump_context_items(
            getattr(context_packet, "recent_timeline_events", None) or []
        ),
        "active_plot_arcs": _dump_context_items(
            getattr(context_packet, "active_plot_arcs", None) or []
        ),
        "active_arc_beats": _dump_context_items(
            getattr(context_packet, "active_arc_beats", None) or []
        ),
        "unresolved_clues": _dump_context_items(
            getattr(context_packet, "unresolved_clues", None) or []
        ),
        "planned_payoffs": _dump_context_items(
            getattr(context_packet, "planned_payoffs", None) or []
        ),
    }
    story_bible = _mapping_or_empty(getattr(context_packet, "story_bible", None))
    chapter_contract = _chapter_contract_payload(context_packet, chapter=chapter)
    quality_targets = {
        "target_word_count": target_word_count,
        "chapter_number": int(getattr(chapter, "chapter_number", 0) or 0),
        "golden_three": int(getattr(chapter, "chapter_number", 0) or 0) <= 3,
        "front_ten": int(getattr(chapter, "chapter_number", 0) or 0) <= 10,
        "assigned_hype_type": getattr(context_packet, "assigned_hype_type", None),
        "assigned_hype_recipe_key": getattr(context_packet, "assigned_hype_recipe_key", None),
        "assigned_hype_intensity": getattr(context_packet, "assigned_hype_intensity", None),
    }
    acceptance_contract = _acceptance_contract(
        chapter=chapter,
        scenes=scene_payloads,
        chapter_contract=chapter_contract,
        quality_targets=quality_targets,
        story_bible=story_bible,
    )
    missing = _missing_context_keys(
        chapter=chapter,
        scene_payloads=scene_payloads,
        story_bible=story_bible,
        acceptance_contract=acceptance_contract,
        continuity=continuity,
        quality_targets=quality_targets,
    )
    return ChapterGenerationInputBundle(
        project={
            "slug": getattr(project, "slug", None),
            "title": getattr(project, "title", None),
            "genre": getattr(project, "genre", None),
            "sub_genre": getattr(project, "sub_genre", None),
            "language": getattr(project, "language", None),
        },
        chapter={
            "chapter_number": getattr(chapter, "chapter_number", None),
            "title": getattr(chapter, "title", None),
            "goal": getattr(chapter, "chapter_goal", None),
            "opening_situation": getattr(chapter, "opening_situation", None),
            "main_conflict": getattr(chapter, "main_conflict", None),
            "hook_type": getattr(chapter, "hook_type", None),
            "hook_description": getattr(chapter, "hook_description", None),
            "metadata": _chapter_metadata_payload(chapter),
        },
        scenes=scene_payloads,
        methodology_blocks=methodology_blocks,
        continuity=continuity,
        story_bible=story_bible,
        chapter_contract=chapter_contract,
        acceptance_contract=acceptance_contract,
        quality_targets=quality_targets,
        required_context_keys=_REQUIRED_CONTEXT_KEYS,
        missing_context_keys=tuple(missing),
    )


def build_chapter_generation_input_stamp(
    bundle: ChapterGenerationInputBundle,
) -> dict[str, Any]:
    """Persist a small audit stamp without storing the full prompt payload.

    The full bundle can include story bible excerpts, scene cards, continuity
    packets, and prior metadata. Storing that object inside chapter metadata
    makes the next bundle recursively include the previous bundle, which can
    grow a normal chapter UPDATE into hundreds of MB.
    """

    acceptance = _mapping_or_empty(bundle.acceptance_contract)
    methodology = _mapping_or_empty(acceptance.get("methodology_application_contract"))
    return {
        "schema_version": "chapter-generation-input-stamp.v1",
        "bundle_schema_version": bundle.schema_version,
        "ready": bundle.ready,
        "coverage": bundle.coverage,
        "missing_context_keys": list(bundle.missing_context_keys),
        "scene_count": len(bundle.scenes),
        "methodology_profile_ids": list(methodology.get("profile_ids") or []),
        "methodology_application_count": len(
            _sequence_of_mappings(methodology.get("applications"))
        ),
        "chapter_contract_digest": _mapping_or_empty(
            methodology.get("chapter_contract_digest")
        ),
        "front_position_rules": _mapping_or_empty(acceptance.get("front_position_rules")),
        "llm_gate_thresholds": _mapping_or_empty(acceptance.get("llm_gate_thresholds")),
    }


def _chapter_metadata_payload(chapter: Any) -> dict[str, Any]:
    metadata = _mapping_or_empty(getattr(chapter, "metadata_json", None))
    return {
        key: value
        for key, value in metadata.items()
        if key not in _TRANSIENT_CHAPTER_METADATA_KEYS
    }


def _scene_metadata_payload(scene: Any) -> dict[str, Any]:
    metadata = _mapping_or_empty(getattr(scene, "metadata_json", None))
    return {
        key: value
        for key, value in metadata.items()
        if key not in _TRANSIENT_SCENE_METADATA_KEYS
    }


def _scene_payloads(scenes: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    payloads: list[dict[str, Any]] = []
    previous_exit_state: Mapping[str, Any] | None = None
    for scene in scenes:
        payload = _scene_payload(scene, previous_exit_state=previous_exit_state)
        payloads.append(payload)
        previous_exit_state = _mapping_or_empty(getattr(scene, "exit_state", None))
    return tuple(payloads)


def _scene_payload(
    scene: Any,
    *,
    previous_exit_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = _scene_metadata_payload(scene)
    purpose = _mapping_or_empty(getattr(scene, "purpose", None))
    methodology_contract = _mapping_or_empty(metadata.get("methodology_contract"))
    scene_contract = _mapping_or_empty(metadata.get("scene_contract"))
    hook_payload = (
        getattr(scene, "hook_requirement", None)
        or purpose.get("reader_hook")
        or scene_contract.get("exit_hook")
        or methodology_contract.get("cut_point")
        or methodology_contract.get("breakpoint")
    )
    visible_progress = (
        methodology_contract.get("visible_progress")
        or methodology_contract.get("visible_action_or_reaction")
        or purpose.get("story")
    )
    reader_payoff = (
        methodology_contract.get("reader_payoff")
        or methodology_contract.get("signature_image")
        or scene_contract.get("visible_object")
        or purpose.get("reader_hook")
    )
    return {
        "scene_number": getattr(scene, "scene_number", None),
        "title": getattr(scene, "title", None),
        "scene_type": getattr(scene, "scene_type", None),
        "time_label": getattr(scene, "time_label", None),
        "participants": list(getattr(scene, "participants", None) or []),
        "purpose": purpose,
        "entry_state": _mapping_or_empty(getattr(scene, "entry_state", None)),
        "exit_state": _mapping_or_empty(getattr(scene, "exit_state", None)),
        "transition_contract": {
            "previous_exit_state": dict(previous_exit_state or {}),
            "entry_state": _mapping_or_empty(getattr(scene, "entry_state", None)),
            "exit_state": _mapping_or_empty(getattr(scene, "exit_state", None)),
            "must_bridge_location_or_time_change": bool(previous_exit_state),
            "forbid_blank_cut": True,
            "forbid_horizontal_rule_separator": True,
        },
        "target_word_count": getattr(scene, "target_word_count", None),
        "key_dialogue_beats": list(getattr(scene, "key_dialogue_beats", None) or []),
        "sensory_anchors": _mapping_or_empty(getattr(scene, "sensory_anchors", None)),
        "forbidden_actions": list(getattr(scene, "forbidden_actions", None) or []),
        "methodology_contract": methodology_contract,
        "signature_image": (
            methodology_contract.get("signature_image")
            or scene_contract.get("visible_object")
        ),
        "cut_point": (
            methodology_contract.get("cut_point")
            or methodology_contract.get("breakpoint")
        ),
        "action_sequence": methodology_contract.get("action_sequence"),
        "relationship_debts": methodology_contract.get("relationship_debts"),
        "information_control_mode": (
            methodology_contract.get("information_control_mode")
            or methodology_contract.get("reveal_mode")
        ),
        "gate_function": (
            methodology_contract.get("gate_function")
            or _default_scene_gate_function(
                scene_number=int(getattr(scene, "scene_number", 0) or 0),
                scene_type=str(getattr(scene, "scene_type", "") or ""),
            )
        ),
        "reader_payoff": reader_payoff,
        "visible_progress": visible_progress,
        "ending_hook_payload": hook_payload,
    }


def _chapter_contract_payload(context_packet: Any, *, chapter: Any | None = None) -> dict[str, Any]:
    chapter_contract = getattr(context_packet, "chapter_contract", None)
    if chapter_contract is None:
        payload: dict[str, Any] = {}
    elif hasattr(chapter_contract, "model_dump"):
        payload = chapter_contract.model_dump(mode="json")
        payload = dict(payload) if isinstance(payload, Mapping) else {}
    else:
        payload = _mapping_or_empty(chapter_contract)
    return _merge_chapter_metadata_contract(payload, chapter=chapter)


def _merge_chapter_metadata_contract(
    chapter_contract: Mapping[str, Any],
    *,
    chapter: Any | None,
) -> dict[str, Any]:
    merged = dict(chapter_contract)
    if chapter is None:
        return merged

    metadata = _mapping_or_empty(getattr(chapter, "metadata_json", None))
    methodology_contract = _mapping_or_empty(metadata.get("methodology_contract"))
    for key, value in methodology_contract.items():
        if not _has_value(value):
            continue
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            nested = dict(existing)
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    whole_chapter_logic_contract = _mapping_or_empty(
        metadata.get("whole_chapter_logic_contract")
    )
    if whole_chapter_logic_contract:
        merged["whole_chapter_logic_contract"] = whole_chapter_logic_contract
    return merged


def _default_scene_gate_function(*, scene_number: int, scene_type: str) -> str:
    normalized_type = scene_type.lower()
    if scene_number <= 1 or "opening" in normalized_type or "hook" in normalized_type:
        return "opening_pull: first-page pressure, protagonist flaw, and immediate action"
    if "reveal" in normalized_type:
        return "main_plot_progression: release one concrete new fact that changes the next action"
    if "conflict" in normalized_type or "pressure" in normalized_type:
        return "commercial_pull: force a visible choice, cost, or rule reversal"
    if scene_number >= 4 or "hook" in normalized_type:
        return (
            "ending_hook_effectiveness: deliver a new visual threat or evidence, "
            "not a repeated summary"
        )
    return "continuity: bridge prior beat into a visible new progress step"


def _acceptance_contract(
    *,
    chapter: Any,
    scenes: tuple[Mapping[str, Any], ...],
    chapter_contract: Mapping[str, Any],
    quality_targets: Mapping[str, Any],
    story_bible: Mapping[str, Any],
) -> dict[str, Any]:
    chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
    llm_min_overall, llm_min_dimensions = chapter_commercial_thresholds(chapter_number)
    rule_threshold = 0.75
    must_deliver = [
        {
            "label": "chapter_goal",
            "value": str(getattr(chapter, "chapter_goal", "") or "").strip(),
            "gate_dimension": "main_plot_progression",
        },
        {
            "label": "main_conflict",
            "value": str(getattr(chapter, "main_conflict", "") or "").strip(),
            "gate_dimension": "main_plot_progression",
        },
        {
            "label": "information_release",
            "value": str(chapter_contract.get("information_release") or "").strip(),
            "gate_dimension": "contract_alignment",
        },
        {
            "label": "closing_hook",
            "value": str(
                chapter_contract.get("closing_hook")
                or getattr(chapter, "hook_description", "")
                or ""
            ).strip(),
            "gate_dimension": "ending_hook_effectiveness",
        },
    ]
    scene_gate_targets = [
        {
            "scene_number": scene.get("scene_number"),
            "title": scene.get("title"),
            "gate_function": scene.get("gate_function"),
            "visible_progress": scene.get("visible_progress"),
            "reader_payoff": scene.get("reader_payoff"),
            "ending_hook_payload": scene.get("ending_hook_payload"),
        }
        for scene in scenes
    ]
    knowledge_boundary_contract = _knowledge_boundary_contract(story_bible, scenes=scenes)
    object_signal_contract = _object_signal_contract(chapter)
    methodology_application_contract = build_methodology_application_contract(
        chapter_number=chapter_number,
        chapter_title=str(getattr(chapter, "title", "") or "").strip(),
        chapter_contract=chapter_contract,
        scene_cards=scenes,
    )
    return {
        "schema_version": "chapter-acceptance-contract.v1",
        "chapter_number": chapter_number,
        "target_word_count": quality_targets.get("target_word_count"),
        "rule_gate_thresholds": {
            "overall": rule_threshold,
            "main_plot_progression": rule_threshold,
            "ending_hook_effectiveness": rule_threshold,
            "volume_mission_alignment": rule_threshold,
            "contract_alignment": rule_threshold,
        },
        "llm_gate_thresholds": {
            "overall": llm_min_overall,
            "dimensions": dict(llm_min_dimensions),
        },
        "front_position_rules": {
            "golden_three": bool(quality_targets.get("golden_three")),
            "front_ten": bool(quality_targets.get("front_ten")),
            "opening_must_start_with_pressure": chapter_number <= 3,
            "ending_hook_must_add_new_information": chapter_number <= 10,
            "ending_must_land_on_completed_scene_frame": True,
            "non_expert_rule_knowledge_must_be_limited": chapter_number <= 10,
            "real_world_evidence_must_be_plausible_or_marked_impossible": chapter_number <= 10,
            "object_signal_meaning_must_be_stable": chapter_number <= 10,
        },
        "ending_frame_contract": {
            "rule": "最后一句必须落在具体场景内的完成画面帧、人物动作、物件变化或选择点。",
            "forbidden": [
                "只停在抽象设定句",
                "只停在未落地的对白",
                "只停在正在进行的动作",
                "用总结/说明/作者口吻收束",
            ],
            "required_if_dialogue_hook": (
                "如果章末钩子是对白，必须在对白后追加一个现场动作或物件变化作为最后画面。"
            ),
        },
        "must_deliver": [item for item in must_deliver if item["value"]],
        "scene_gate_targets": scene_gate_targets,
        "knowledge_boundary_contract": knowledge_boundary_contract,
        "object_signal_contract": object_signal_contract,
        "methodology_application_contract": methodology_application_contract,
        "pass_condition": (
            "正文必须让 rule_gate_thresholds 与 llm_gate_thresholds 均可通过；"
            "如果篇幅不足，优先保留 must_deliver 与 scene_gate_targets。"
        ),
    }


def _derive_specialist_rule_terms(story_bible: Mapping[str, Any]) -> list[str]:
    """Pull THIS book's own specialist / rule terminology out of its worldview so the
    knowledge-boundary contract references the right terms — instead of hardcoding one
    detective book's jargon (认账/镜债/账线…) into every project."""

    terms: list[str] = []

    def _collect(value: Any, depth: int = 0) -> None:
        if depth > 3 or len(terms) >= 24:
            return
        if isinstance(value, str):
            t = value.strip()
            if 2 <= len(t) <= 8 and t not in terms:
                terms.append(t)
        elif isinstance(value, Mapping):
            # Prefer the names/keys of systems/rules/terms over free prose.
            for key in ("name", "term", "title", "key", "label"):
                if isinstance(value.get(key), str):
                    _collect(value[key], depth + 1)
            for nested_key in (
                "terms",
                "rules",
                "systems",
                "power_system",
                "power_systems",
                "entries",
                "items",
                "glossary",
            ):
                if nested_key in value:
                    _collect(value[nested_key], depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                _collect(item, depth + 1)

    for field in (
        "worldview_kernel",
        "worldview",
        "power_system",
        "power_systems",
        "systems",
        "rules",
        "rule",
        "glossary",
        "terminology",
        "rule_terms",
        "specialist_terms",
        "key_terms",
    ):
        if field in story_bible:
            _collect(story_bible.get(field))
    return list(dict.fromkeys(terms))


def _knowledge_boundary_contract(
    story_bible: Mapping[str, Any],
    *,
    scenes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    participants = _sequence_of_mappings(story_bible.get("participants"))
    if not participants:
        participants = _sequence_of_mappings(story_bible.get("characters"))
    specialist_names = [
        str(item.get("name") or "").strip()
        for item in participants
        if (
            str(item.get("role") or "").lower()
            in {"protagonist", "mentor", "exorcist", "expert"}
            or bool(item.get("is_pov_character"))
        )
    ]
    specialist_names = [name for name in specialist_names if name]
    if not specialist_names:
        specialist_names = _fallback_scene_explainers(scenes)
    # THIS book's own specialist terms (genre-neutral, derived from its worldview).
    specialist_rule_terms = _derive_specialist_rule_terms(story_bible)
    explainer_label = "、".join(specialist_names) if specialist_names else "本书的专业 / 主角角色"
    return {
        "rule": (
            "Only specialist/protagonist characters may infer or name this book's hidden "
            "rule mechanics (whatever they are for this genre/setting). Lay characters can "
            "describe visible symptoms, fear, memories, or hearsay, but cannot accurately "
            "explain rule terms unless the prose shows teaching, possession, or coercion."
        ),
        "specialist_rule_terms": specialist_rule_terms,
        "allowed_explainers": specialist_names,
        "lay_character_rule": (
            f"非专业 / 普通角色（{explainer_label} 以外）只能说自己看见了什么、害怕什么、"
            "被谁警告过什么；不能自然地讲出本书设定中的专业 / 超自然规则术语的完整逻辑，"
            "除非正文已写明被传授 / 附身 / 胁迫。"
        ),
    }


def _fallback_scene_explainers(scenes: Sequence[Mapping[str, Any]]) -> list[str]:
    """Use the first recurring scene participant as POV fallback when bible roles are absent."""
    seen: list[str] = []
    for scene in scenes:
        participants = scene.get("participants")
        if not isinstance(participants, Sequence) or isinstance(participants, (str, bytes)):
            continue
        for participant in participants:
            name = str(participant or "").strip()
            if name and name not in seen:
                seen.append(name)
    return seen[:1]


def _object_signal_contract(chapter: Any) -> dict[str, Any]:
    metadata = _mapping_or_empty(getattr(chapter, "metadata_json", None))
    existing = metadata.get("object_signal_contract")
    if isinstance(existing, Mapping):
        return dict(existing)
    return {
        "rule": (
            "Magic objects must produce interpretable, varied signals. Repeated heat alone "
            "is not enough; each signal needs a visible meaning, cost, and limit."
        ),
        "forbidden_shortcut": (
            "Do not reduce this book's key objects/abilities to a repeated '发烫' "
            "(or any single sensory tic) as the only engine of discovery."
        ),
        "preferred_signals": [
            "cold/weight change for direction",
            "crack/chip for cost",
            "blood spot or line for debt contact",
            "needle or shadow offset for location",
        ],
    }


def _missing_context_keys(
    *,
    chapter: Any,
    scene_payloads: tuple[Mapping[str, Any], ...],
    story_bible: Mapping[str, Any],
    acceptance_contract: Mapping[str, Any],
    continuity: Mapping[str, Any],
    quality_targets: Mapping[str, Any],
) -> list[str]:
    missing: list[str] = []
    if not str(getattr(chapter, "chapter_goal", "") or "").strip():
        missing.append("chapter.goal")
    if not scene_payloads:
        missing.append("scenes")
    if scene_payloads and any(not scene.get("purpose") for scene in scene_payloads):
        missing.append("scenes.purpose")
    if scene_payloads and any(
        not scene.get("entry_state") or not scene.get("exit_state")
        for scene in scene_payloads
    ):
        missing.append("scenes.entry_exit")
    if scene_payloads and any(not scene.get("methodology_contract") for scene in scene_payloads):
        missing.append("scenes.methodology_contract")
    if not story_bible:
        missing.append("story_bible")
    if not acceptance_contract.get("must_deliver") or not acceptance_contract.get(
        "scene_gate_targets"
    ):
        missing.append("chapter_acceptance_contract")
    chapter_number = int(getattr(chapter, "chapter_number", 0) or 0)
    if chapter_number > 1 and not continuity.get("previous_scene_summaries"):
        missing.append("continuity.previous_scene_summaries")
    if not quality_targets.get("target_word_count"):
        missing.append("quality_targets")
    return missing


def _dump_context_items(items: Sequence[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items[:12]:
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
            result.append(dict(payload) if isinstance(payload, Mapping) else {"value": payload})
        elif isinstance(item, Mapping):
            result.append(dict(item))
        else:
            result.append({"value": str(item)})
    return result


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _sequence_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]
