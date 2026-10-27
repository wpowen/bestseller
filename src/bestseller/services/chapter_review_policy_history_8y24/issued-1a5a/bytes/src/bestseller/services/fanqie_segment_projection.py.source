"""Literal Beat-to-outline candidate projection; never a grade or author grant."""

# ruff: noqa: ANN401 -- Preserve opaque literal JSON fields without coercion.

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from bestseller.domain.fanqie_short import FanqieShortBeatSheet, segment_target_words
from bestseller.services.creation_contract_authority import select_creation_contracts
from bestseller.services.creation_intent_contract import contract_from_payload
from bestseller.services.prompt_source_policy import content_hash

POLICY = "fanqie-segment-projection.v1"
CONTRACTS = (
    "opening_contract",
    "unlock_contract",
    "ability_cost_contract",
    "payoff_contract",
    "closure_contract",
    "continuity_contract",
)


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"fanqie_projection_text_required:{key}")
    return value


def _selected_author(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = metadata.get("creation_intent_contract")
    if raw is None or metadata.get("creation_intent_contract_hash") is None:
        # Missing historical binding is unknown, never a new author selection.
        return {}
    contract = contract_from_payload(metadata)
    digest = metadata["creation_intent_contract_hash"]
    if content_hash(raw) != digest or contract.contract_hash() != digest:
        raise ValueError("fanqie_projection_creation_hash_mismatch")
    receipt = metadata.get("creation_input_receipt")
    if receipt is not None and (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != contract.schema_version
        or receipt.get("contract_hash") != digest
        or content_hash(receipt.get("field_sources")) != content_hash(raw.get("field_sources"))
    ):
        raise ValueError("fanqie_projection_creation_receipt_mismatch")
    return select_creation_contracts(metadata, source_bound=True)


def _scene_field(contracts: Mapping[str, Mapping[str, Any]], key: str, default: Any) -> Any:
    # Presence must be structured. A name mentioned in dialogue, a cast roster,
    # a logline or a phone call is not proof that the person attends the scene.
    values = [c[key] for c in contracts.values() if key in c]
    if not values:
        return deepcopy(default)
    if any(content_hash(v) != content_hash(values[0]) for v in values[1:]):
        raise ValueError(f"fanqie_projection_conflicting_scene_field:{key}")
    return deepcopy(values[0])


def _protagonist_flaw(book: Mapping[str, Any], cast: Mapping[str, Any]) -> str | None:
    values = []
    for source in (cast, book):
        protagonist = source.get("protagonist")
        if isinstance(protagonist, Mapping) and "flaw" in protagonist:
            value = protagonist["flaw"]
            if value is not None and not isinstance(value, str):
                raise ValueError("fanqie_projection_flaw_text_required")
            values.append(value)
    known = [v for v in values if v is not None and v != ""]
    if known and any(v != known[0] for v in known[1:]):
        raise ValueError("fanqie_projection_conflicting_protagonist_flaw")
    return known[0] if known else None


def project_segment_outline(
    project: object,
    beat_sheet: FanqieShortBeatSheet,
    *,
    book_spec: Mapping[str, Any] | None = None,
    cast_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map supplied values without generating absent narrative obligations.

    Inputs are candidate material, not paid/source/grade proof. Downstream
    validation, real admission, source selection and adoption remain mandatory.
    """
    book = deepcopy(dict(book_spec or {}))
    cast = deepcopy(dict(cast_spec or {}))
    metadata = getattr(project, "metadata_json", None) or {}
    if not isinstance(metadata, Mapping):
        raise ValueError("fanqie_projection_metadata_object_required")
    count = getattr(project, "target_chapters", None)
    if type(count) is not int or count < 1:
        raise ValueError("fanqie_projection_target_count_invalid")
    numbers = [b.segment_number for b in beat_sheet.beats]
    if numbers != list(range(1, count + 1)):
        raise ValueError("fanqie_projection_complete_ordered_beats_required")
    author = _selected_author(metadata)
    flaw = _protagonist_flaw(book, cast)
    words = segment_target_words(project.target_word_count, count)
    book_context = {
        key: deepcopy(book[key])
        for key in (
            "premise",
            "core_promise",
            "reader_promise",
            "main_thread",
            "logline",
            "stakes",
            "unique_hook",
            "theme_statement",
            "series_engine",
            "serialization",
        )
        if key in book
    }
    chapters = []
    for beat in beat_sheet.beats:
        source = beat.model_dump(mode="json")
        contracts = {key: deepcopy(source[key]) for key in CONTRACTS}
        opening = contracts["opening_contract"]
        unlock = contracts["unlock_contract"]
        ability = contracts["ability_cost_contract"]
        payoff = contracts["payoff_contract"]
        closure = contracts["closure_contract"]
        continuity = contracts["continuity_contract"]
        pressure = _text(opening, "public_pressure")
        opening_situation = _text(opening, "summary")
        hook = _text(unlock, "next_segment_trigger")
        visible_result = (
            _text(payoff, "visible_result") if "visible_result" in payoff else beat.payoff
        )
        participants = _scene_field(contracts, "participants", [])
        if participants is None:
            participants = []
        if not isinstance(participants, list) or any(
            not isinstance(v, str) or not v.strip() for v in participants
        ):
            raise ValueError("fanqie_projection_participants_array_required")
        action_sequence = _scene_field(contracts, "action_sequence", [])
        if action_sequence is None:
            action_sequence = []
        if not isinstance(action_sequence, list) or any(
            not isinstance(v, str) for v in action_sequence
        ):
            raise ValueError("fanqie_projection_action_array_required")
        scene_texts = {}
        for key in ("scene_type", "signature_image", "cut_point"):
            scene_texts[key] = _scene_field(contracts, key, None)
            if scene_texts[key] is not None and not isinstance(scene_texts[key], str):
                raise ValueError(f"fanqie_projection_text_required:{key}")
        scene_controls = {}
        for key, expected_type in (
            ("entry_state", dict),
            ("exit_state", dict),
            ("sensory_anchors", dict),
            ("key_dialogue_beats", list),
            ("forbidden_actions", list),
            ("relationship_debts", list),
        ):
            value = _scene_field(contracts, key, None)
            if value is not None:
                if not isinstance(value, expected_type) or (
                    expected_type is list and any(not isinstance(v, str) for v in value)
                ):
                    raise ValueError(f"fanqie_projection_scene_field_type:{key}")
                scene_controls[key] = value
        methodology = {
            "fanqie_short_v2": deepcopy(contracts),
            "fanqie_short_payoff": beat.payoff,
            "fanqie_short_emotional_turn": beat.emotional_turn,
            "thesis_vector": beat.thesis_vector,
            "book_source_context": deepcopy(book_context),
            "selected_creation_contracts": deepcopy(author),
        }
        causal = {
            "chapter_function": beat.beat_role,
            "thesis_vector": beat.thesis_vector,
            "pressure": pressure,
            "protagonist_choice": _text(closure, "irreversible_choice"),
            "visible_action_or_reaction": visible_result,
            "resistance": _text(opening, "resistance"),
            "cost_or_tradeoff": _text(ability, "cost"),
            "gain_or_reveal": visible_result,
            "state_change": _text(closure, "state_change"),
            "next_reader_desire": hook,
            "fanqie_short_v2": deepcopy(contracts),
        }
        chapters.append(
            {
                "chapter_number": beat.segment_number,
                "title": None,
                "chapter_goal": beat.purpose,
                "opening_situation": opening_situation,
                "opening_pressure": pressure,
                "main_conflict": pressure,
                "hook_description": hook,
                "tail_hook": hook,
                "protagonist_flaw": flaw,
                "visible_action_or_reaction": visible_result,
                "payoff": beat.payoff,
                "causal_contract": causal,
                "methodology_contract": methodology,
                "target_word_count": words,
                "volume_number": 1,
                "scenes": [
                    {
                        "scene_number": 1,
                        # Empty is deliberate: even "conflict" is not a known event.
                        "scene_type": scene_texts["scene_type"] or "",
                        "title": None,
                        "time_label": _text(continuity, "time_position"),
                        "participants": participants,
                        "purpose": {"story": beat.purpose, "emotion": beat.emotional_turn},
                        "conflict_stakes": _text(opening, "immediate_stakes"),
                        "information_control": _text(continuity, "fact_boundary"),
                        "signature_image": scene_texts["signature_image"],
                        "cut_point": scene_texts["cut_point"],
                        "action_sequence": action_sequence,
                        "methodology_contract": {
                            **deepcopy(methodology),
                            "conflict_stakes": _text(opening, "immediate_stakes"),
                            "information_control": _text(continuity, "fact_boundary"),
                            "signature_image": scene_texts["signature_image"],
                            "cut_point": scene_texts["cut_point"],
                        },
                        "entry_state": {"pressure": pressure} if pressure else {},
                        "exit_state": {"state_change": causal["state_change"]}
                        if causal["state_change"]
                        else {},
                        "target_word_count": words,
                        **scene_controls,
                    }
                ],
            }
        )
    return {
        "batch_name": "fanqie-short-segments",
        "chapters": chapters,
        "_meta": {
            "segment_projection": {
                "policy": POLICY,
                "typed_beat_sheet_sha256": content_hash(beat_sheet.model_dump(mode="json")),
                "book_input_sha256": content_hash(book),
                "cast_input_sha256": content_hash(cast),
                "selected_creation_contracts": deepcopy(author),
                "grade_asserted": False,
                "write_authorized": False,
                "paid_source_proof_asserted": False,
            }
        },
    }
