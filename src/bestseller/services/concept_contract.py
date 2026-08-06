"""Unified lineage contract for hook, long-form capacity, and story spine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import hashlib
import json
from typing import Any

from bestseller.services.seriality_capacity import (
    SERIALITY_PROOF_REQUIRED_MIN_CHAPTERS,
    evaluate_seriality_capacity,
)

CONCEPT_CONTRACT_VERSION = "concept-contract.v2"


class ConceptContractError(RuntimeError):
    code = "concept_contract_invalid"

    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = tuple(str(item) for item in violations)
        super().__init__("Concept contract invalid: " + "; ".join(self.violations))


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Sequence) or isinstance(value, bytes):
        return []
    return list(dict.fromkeys(_text(item) for item in value if _text(item)))


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _winner_mapping(winner: object) -> dict[str, Any]:
    if isinstance(winner, Mapping):
        return dict(winner)
    to_dict = getattr(winner, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


def _stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_concept_contract(
    *,
    winner: object,
    story_spine: Mapping[str, Any],
    target_chapters: int,
    genre: str,
    sub_genre: str,
) -> dict[str, Any]:
    """Build all three artifacts from one approved tournament champion."""

    selected = _winner_mapping(winner)
    repeatable_story_unit = _text(selected.get("repeatable_story_unit")) or _text(
        selected.get("mechanism")
    )
    unit_families = _items(selected.get("unit_families"))
    question_ladder = _items(selected.get("question_ladder"))
    renewal_sources = _items(selected.get("renewal_sources"))
    accumulation_tracks = _items(selected.get("accumulation_tracks"))
    phase_transitions = _items(selected.get("phase_transitions"))
    opposing_ecology = _items(selected.get("opposing_ecology"))
    endgame_direction = _text(selected.get("endgame_direction"))
    unit_frequency = _text(selected.get("unit_frequency"))
    unit_count_estimate = _nonnegative_int(selected.get("unit_count_estimate"))
    source = {
        "concept": _text(selected.get("concept")),
        "mechanism": _text(selected.get("mechanism")),
        "hook_question": _text(selected.get("hook_question")) or _text(story_spine.get("question")),
        "protagonist_identity": _text(selected.get("protagonist_identity"))
        or _text(story_spine.get("who")),
        "protagonist_private_desire": _text(selected.get("protagonist_private_desire"))
        or _text(story_spine.get("wants")),
        "protagonist_flaw": _text(selected.get("protagonist_flaw")),
        "core_abnormality": _text(selected.get("core_abnormality"))
        or _text(selected.get("mechanism")),
        "opening_crisis": _text(selected.get("opening_crisis"))
        or _text(story_spine.get("why_now")),
        "opponent_system": _text(selected.get("opponent_system"))
        or _text(story_spine.get("against")),
        "decision_proof": _text(selected.get("decision_proof")),
        "emotional_promise": _text(selected.get("emotional_promise")),
        "genre": _text(genre),
        "sub_genre": _text(sub_genre),
        "target_chapters": int(target_chapters),
        "repeatable_story_unit": repeatable_story_unit,
        "unit_families": unit_families,
        "unit_frequency": unit_frequency,
        "unit_count_estimate": unit_count_estimate,
        "renewal_sources": renewal_sources,
        "accumulation_tracks": accumulation_tracks,
        "phase_transitions": phase_transitions,
        "opposing_ecology": opposing_ecology,
        "mystery_ladder": question_ladder,
        "endgame_direction": endgame_direction,
    }
    input_hash = _stable_hash(source)
    champion_id = f"concept-{input_hash[:16]}"
    quality_evidence = {
        "schema_version": "concept-quality-evidence.v1",
        "approved": not bool(_text(selected.get("rejected_reason"))),
        "composite": selected.get("composite"),
        "hook_judge": {
            key.removeprefix("judge_"): selected.get(key)
            for key in (
                "judge_freshness",
                "judge_click",
                "judge_predictable",
                "judge_character_logic",
                "judge_mechanism_causality",
                "judge_genre_fidelity",
                "judge_plain_language",
                "judge_story_motion",
            )
        },
        "seriality_judge": (
            dict(selected.get("seriality_judge"))
            if isinstance(selected.get("seriality_judge"), Mapping)
            else {}
        ),
    }
    quality_evidence["evidence_hash"] = _stable_hash(quality_evidence)

    capacity_report = evaluate_seriality_capacity(
        {
            "repeatable_story_unit": repeatable_story_unit,
            "unit_families": unit_families,
            "unit_frequency": unit_frequency,
            "unit_count_estimate": unit_count_estimate,
            "renewal_sources": renewal_sources,
            "accumulation_tracks": accumulation_tracks,
            "phase_transitions": phase_transitions,
            "opposing_ecology": opposing_ecology,
            "mystery_ladder": question_ladder,
            "endgame_direction": endgame_direction,
        },
        target_chapters=int(target_chapters),
        require_phase_coverage=True,
    ).to_dict()

    common = {
        "champion_id": champion_id,
        "input_hash": input_hash,
        "target_chapters": int(target_chapters),
    }
    hook_card = {
        "schema_version": "hook-card.v2",
        **common,
        "approved": True,
        "one_liner": source["concept"],
        "story_motion": source["mechanism"],
        "protagonist": source["protagonist_identity"],
        "private_desire": source["protagonist_private_desire"],
        "protagonist_flaw": source["protagonist_flaw"],
        "abnormality": source["core_abnormality"],
        "opening_event": source["opening_crisis"],
        "central_contradiction": source["opponent_system"],
        "decision_proof": source["decision_proof"],
        "emotional_promise": source["emotional_promise"],
        "reader_question": source["hook_question"] or _text(story_spine.get("question")),
    }
    seriality_proof = {
        "schema_version": "seriality-proof.v2",
        **common,
        "repeatable_story_unit": repeatable_story_unit,
        "unit_families": unit_families,
        "unit_frequency": unit_frequency,
        "unit_count_estimate": unit_count_estimate,
        "renewal_sources": renewal_sources,
        "accumulation_tracks": accumulation_tracks,
        "phase_transitions": phase_transitions,
        "opposing_ecology": opposing_ecology,
        "mystery_ladder": question_ladder,
        "endgame_direction": endgame_direction,
        "capacity_report": capacity_report,
    }
    layered_spine = {
        **dict(story_spine),
        "schema_version": "story-spine.v2",
        **common,
        "core_reader_promise": source["concept"],
        "who": _text(selected.get("protagonist_identity")) or _text(story_spine.get("who")),
        "wants": _text(selected.get("protagonist_private_desire"))
        or _text(story_spine.get("wants")),
        "why_now": _text(selected.get("opening_crisis"))
        or _text(story_spine.get("why_now")),
        "against": _text(selected.get("opponent_system"))
        or _text(story_spine.get("against")),
        "long_term_desire": _text(selected.get("protagonist_private_desire"))
        or _text(story_spine.get("wants")),
        "terminal_question": endgame_direction or _text(story_spine.get("question")),
        "unit_engine_ref": repeatable_story_unit,
        "phase_desire_ladder": phase_transitions,
        "world_faction_evolution": opposing_ecology,
    }
    return {
        "schema_version": CONCEPT_CONTRACT_VERSION,
        **common,
        "genre": source["genre"],
        "sub_genre": source["sub_genre"],
        "quality_evidence": quality_evidence,
        "hook_card": hook_card,
        "seriality_proof": seriality_proof,
        "story_spine": layered_spine,
    }


def _concept_contract_hash_source(
    contract: Mapping[str, Any],
    *,
    target_chapters: int,
) -> dict[str, Any] | None:
    hook = contract.get("hook_card")
    proof = contract.get("seriality_proof")
    if not isinstance(hook, Mapping) or not isinstance(proof, Mapping):
        return None
    return {
        "concept": _text(hook.get("one_liner")),
        "mechanism": _text(hook.get("story_motion")),
        "hook_question": _text(hook.get("reader_question")),
        "protagonist_identity": _text(hook.get("protagonist")),
        "protagonist_private_desire": _text(hook.get("private_desire")),
        "protagonist_flaw": _text(hook.get("protagonist_flaw")),
        "core_abnormality": _text(hook.get("abnormality")),
        "opening_crisis": _text(hook.get("opening_event")),
        "opponent_system": _text(hook.get("central_contradiction")),
        "decision_proof": _text(hook.get("decision_proof")),
        "emotional_promise": _text(hook.get("emotional_promise")),
        "genre": _text(contract.get("genre")),
        "sub_genre": _text(contract.get("sub_genre")),
        "target_chapters": int(target_chapters),
        "repeatable_story_unit": _text(proof.get("repeatable_story_unit")),
        "unit_families": _items(proof.get("unit_families")),
        "unit_frequency": _text(proof.get("unit_frequency")),
        "unit_count_estimate": _nonnegative_int(proof.get("unit_count_estimate")),
        "renewal_sources": _items(proof.get("renewal_sources")),
        "accumulation_tracks": _items(proof.get("accumulation_tracks")),
        "phase_transitions": _items(proof.get("phase_transitions")),
        "opposing_ecology": _items(proof.get("opposing_ecology")),
        "mystery_ladder": _items(proof.get("mystery_ladder")),
        "endgame_direction": _text(proof.get("endgame_direction")),
    }


def reseal_concept_contract_lineage(
    contract: Mapping[str, Any],
    *,
    target_chapters: int,
) -> dict[str, Any]:
    """Recompute lineage after a deterministic creation-identity migration."""

    result = copy.deepcopy(dict(contract))
    source = _concept_contract_hash_source(
        result,
        target_chapters=target_chapters,
    )
    if source is None:
        raise ConceptContractError(["concept_contract 缺少可重新封印的 hook/proof"])
    input_hash = _stable_hash(source)
    champion_id = f"concept-{input_hash[:16]}"
    result.update(
        {
            "schema_version": CONCEPT_CONTRACT_VERSION,
            "input_hash": input_hash,
            "champion_id": champion_id,
            "target_chapters": int(target_chapters),
        }
    )
    for key in ("hook_card", "seriality_proof", "story_spine"):
        child = result.get(key)
        if not isinstance(child, Mapping):
            continue
        result[key] = {
            **dict(child),
            "input_hash": input_hash,
            "champion_id": champion_id,
            "target_chapters": int(target_chapters),
        }
    violations = validate_concept_contract(
        result,
        target_chapters=target_chapters,
    )
    if violations:
        raise ConceptContractError(violations)
    return result


def validate_concept_contract(
    contract: Mapping[str, Any] | None,
    *,
    target_chapters: int,
) -> list[str]:
    if not isinstance(contract, Mapping) or not contract:
        return ["concept_contract 缺失"]
    violations: list[str] = []
    if _text(contract.get("schema_version")) != CONCEPT_CONTRACT_VERSION:
        violations.append("concept_contract.schema_version 不是 v2")
    champion_id = _text(contract.get("champion_id"))
    input_hash = _text(contract.get("input_hash"))
    if int(contract.get("target_chapters") or 0) != int(target_chapters):
        violations.append("concept_contract.target_chapters 与项目目标不一致")
    for key in ("hook_card", "seriality_proof", "story_spine"):
        child = contract.get(key)
        if not isinstance(child, Mapping):
            violations.append(f"concept_contract.{key} 缺失")
            continue
        if _text(child.get("champion_id")) != champion_id:
            violations.append(f"concept_contract.{key}.champion_id 不一致")
        if _text(child.get("input_hash")) != input_hash:
            violations.append(f"concept_contract.{key}.input_hash 不一致")
        if int(child.get("target_chapters") or 0) != int(target_chapters):
            violations.append(f"concept_contract.{key}.target_chapters 不一致")
    hook = contract.get("hook_card")
    if isinstance(hook, Mapping):
        if not hook.get("approved") or not _text(hook.get("one_liner")):
            violations.append("hook_card 未批准或一句话为空")
        for field_name in (
            "protagonist",
            "private_desire",
            "abnormality",
            "opening_event",
            "central_contradiction",
            "decision_proof",
            "emotional_promise",
        ):
            if not _text(hook.get(field_name)):
                violations.append(f"hook_card.{field_name} 缺失")
        source = _concept_contract_hash_source(
            contract,
            target_chapters=target_chapters,
        )
        if source is not None:
            expected_hash = _stable_hash(source)
            if expected_hash != input_hash:
                violations.append("concept_contract.input_hash 与当前内容或目标篇幅不一致")
    proof = contract.get("seriality_proof")
    # Only demand the full capacity proof in the band where it is actually
    # generated. ``concept_tournament`` runs its seriality expansion / repair /
    # audit loop behind the same constant, so below it the engine kernel is
    # never asked for accumulation_tracks / phase_transitions — and without
    # accumulation tracks the measured ceiling is pinned at 50. Validating it
    # anyway made every 51–199-chapter book die with target_exceeds_capacity
    # after a full conception run (verified 2026-07-25 across 51/54/100/108/
    # 180/199; three shipped presets sit in that band). The measurement
    # function is intentionally left untouched — it is a ruler, not a policy.
    if (
        isinstance(proof, Mapping)
        and int(target_chapters) >= SERIALITY_PROOF_REQUIRED_MIN_CHAPTERS
    ):
        fresh_report = evaluate_seriality_capacity(
            proof,
            target_chapters=target_chapters,
            require_phase_coverage=True,
        )
        if not fresh_report.passed:
            violations.append(
                "seriality_proof 容量不足: " + "/".join(fresh_report.blocking_codes)
            )
    return violations


def require_valid_concept_contract(
    metadata: Mapping[str, Any] | None,
    *,
    target_chapters: int,
) -> dict[str, Any] | None:
    """Validate v2 projects; leave legacy projects compatible."""

    meta = metadata if isinstance(metadata, Mapping) else {}
    contract = meta.get("concept_contract")
    declared_v2 = bool(
        _text(meta.get("concept_contract_version")) == "2"
        or meta.get("concept_contract_required")
    )
    if not declared_v2 and not isinstance(contract, Mapping):
        return None
    violations = validate_concept_contract(
        contract if isinstance(contract, Mapping) else None,
        target_chapters=target_chapters,
    )
    if violations:
        raise ConceptContractError(violations)
    return dict(contract) if isinstance(contract, Mapping) else None


def require_conception_contract_for_target(
    contract: Mapping[str, Any] | None,
    *,
    target_chapters: int,
) -> dict[str, Any] | None:
    """Fail closed before project creation for newly conceived long books."""

    if not contract:
        if int(target_chapters) >= 200:
            raise ConceptContractError(
                [
                    f"目标为 {int(target_chapters)} 章，但没有候选通过一句话钩子与"
                    "长篇容量证明；禁止进入书籍规划"
                ]
            )
        return None
    violations = validate_concept_contract(contract, target_chapters=target_chapters)
    if violations:
        raise ConceptContractError(violations)
    return dict(contract)


def apply_concept_contract_to_book_spec(
    book_spec: Mapping[str, Any],
    contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = dict(book_spec)
    if not isinstance(contract, Mapping):
        return result
    proof = contract.get("seriality_proof")
    hook = contract.get("hook_card")
    if not isinstance(proof, Mapping) or not isinstance(hook, Mapping):
        return result
    meta = result.get("_meta")
    source_bound = bool(
        isinstance(meta, Mapping) and meta.get("source_bound_design")
    )
    if source_bound:
        # A source-bound BookSpec has already been compiled from the immutable
        # creation snapshot.  The concept contract is older, model-authored
        # evidence and may contain speculative unit families, opponents,
        # mysteries, or backstory.  Preserve only its lineage identifiers;
        # never let it become a second writer that overwrites canonical fields.
        result["concept_contract_lineage"] = {
            key: contract.get(key)
            for key in (
                "schema_version",
                "champion_id",
                "input_hash",
                "target_chapters",
            )
        }
        return result
    engine = result.get("series_engine")
    engine = dict(engine) if isinstance(engine, Mapping) else {}
    engine.setdefault("reader_promise", _text(hook.get("one_liner")))
    engine["opening_hook"] = _text(hook.get("one_liner"))
    engine["repeatable_story_unit"] = _text(proof.get("repeatable_story_unit"))
    engine["unit_frequency"] = _text(proof.get("unit_frequency"))
    engine["unit_count_estimate"] = _nonnegative_int(proof.get("unit_count_estimate"))
    for key in (
        "unit_families",
        "renewal_sources",
        "accumulation_tracks",
        "phase_transitions",
        "opposing_ecology",
        "mystery_ladder",
    ):
        engine[key] = _items(proof.get(key))
    engine["endgame_direction"] = _text(proof.get("endgame_direction"))
    result["series_engine"] = engine
    result["concept_contract_lineage"] = {
        key: contract.get(key)
        for key in (
            "schema_version",
            "champion_id",
            "input_hash",
            "target_chapters",
        )
    }
    return result


def render_concept_contract_block(
    contract: Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    if not isinstance(contract, Mapping):
        return ""
    hook = contract.get("hook_card")
    proof = contract.get("seriality_proof")
    spine = contract.get("story_spine")
    if not all(isinstance(item, Mapping) for item in (hook, proof, spine)):
        return ""
    assert isinstance(hook, Mapping) and isinstance(proof, Mapping) and isinstance(spine, Mapping)
    if language.startswith("en"):
        return (
            "[Approved concept contract — preserve lineage]\n"
            f"Opening hook: {_text(hook.get('one_liner'))}\n"
            f"Renewable story unit: {_text(proof.get('repeatable_story_unit'))}\n"
            f"Unit density: {_text(proof.get('unit_frequency'))}; "
            f"{_nonnegative_int(proof.get('unit_count_estimate'))} distinct units\n"
            f"Unit families: {'; '.join(_items(proof.get('unit_families')))}\n"
            f"Permanent accumulation: {'; '.join(_items(proof.get('accumulation_tracks')))}\n"
            f"Phase transformations: {' -> '.join(_items(proof.get('phase_transitions')))}\n"
            f"Terminal question: {_text(spine.get('terminal_question'))}\n"
        )
    return (
        "【已批准概念合同——不得更换故事身份】\n"
        f"开篇一句话：{_text(hook.get('one_liner'))}\n"
        f"可再生故事单元：{_text(proof.get('repeatable_story_unit'))}\n"
        f"单元密度：{_text(proof.get('unit_frequency'))}；"
        f"预计{_nonnegative_int(proof.get('unit_count_estimate'))}个互异单元\n"
        f"单元家族：{'；'.join(_items(proof.get('unit_families')))}\n"
        f"永久积累：{'；'.join(_items(proof.get('accumulation_tracks')))}\n"
        f"阶段质变：{'→'.join(_items(proof.get('phase_transitions')))}\n"
        f"终局问题：{_text(spine.get('terminal_question'))}\n"
        "钩子只约束开篇承诺；中后期必须通过故事单元、永久积累和阶段质变生长，"
        "禁止机械重复同一代价或误解。\n"
    )


def render_volume_seriality_execution_block(
    contract: Mapping[str, Any] | None,
    volume_entry: Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render only the current volume's approved seriality duties.

    The global hook is deliberately absent: after the opening, chapter planning
    should inherit the current phase, a transformed story unit, and irreversible
    state growth instead of replaying the same premise/cost forever.
    """

    if not isinstance(contract, Mapping) or not isinstance(volume_entry, Mapping):
        return ""
    phase = _text(volume_entry.get("seriality_phase_ref"))
    phase_id = _text(volume_entry.get("seriality_phase_id"))
    unit_family = _text(volume_entry.get("unit_family_ref"))
    unit_variant = _text(volume_entry.get("renewable_unit_variant"))
    raw_track_deltas = volume_entry.get("accumulation_track_deltas")
    track_deltas = (
        [
            f"{_text(item.get('track_ref'))} -> {_text(item.get('delta'))}"
            for item in raw_track_deltas
            if isinstance(item, Mapping)
            and _text(item.get("track_ref"))
            and _text(item.get("delta"))
        ]
        if isinstance(raw_track_deltas, Sequence)
        and not isinstance(raw_track_deltas, (str, bytes))
        else []
    )
    if not phase_id or not phase or not unit_family or not unit_variant or not track_deltas:
        return ""
    if language.startswith("en"):
        return (
            "[Current-volume seriality execution contract]\n"
            f"Phase: {phase_id} -> {phase}\n"
            f"Story-unit family: {unit_family}\n"
            f"Renewable unit variant: {unit_variant}\n"
            f"Irreversible accumulation targets: {'; '.join(track_deltas)}\n"
            "Each chapter must output `seriality_contract` with the exact phase_id and "
            "unit_family_ref, unit_instance_id, unit_variant_contribution, phase_progress, "
            "prior_state_refs, irreversible_state_after, no_reset_evidence, and "
            "accumulation_track_deltas [{track_ref, delta}]. A chapter may use an empty "
            "delta array while preparing a change, but "
            "the batch must visibly advance every target; do not restart the book's opening hook.\n"
        )
    return (
        "【本卷连载执行合同】\n"
        f"阶段质变：{phase_id} -> {phase}\n"
        f"故事单元家族：{unit_family}\n"
        f"故事单元变体：{unit_variant}\n"
        f"不可逆积累目标：{'；'.join(track_deltas)}\n"
        "每章必须输出 `seriality_contract`：逐字匹配本卷的 phase_id、unit_family_ref，以及"
        "unit_instance_id、unit_variant_contribution、phase_progress、prior_state_refs、"
        "irreversible_state_after、no_reset_evidence、accumulation_track_deltas [{track_ref, delta}]。"
        "单章铺垫变化时 accumulation_track_deltas 可以为空数组，但整个批次必须"
        "让全部目标发生可见推进；禁止重启开篇钩子、清零关系/资源/认知状态。\n"
    )


__all__ = [
    "CONCEPT_CONTRACT_VERSION",
    "ConceptContractError",
    "apply_concept_contract_to_book_spec",
    "build_concept_contract",
    "render_concept_contract_block",
    "render_volume_seriality_execution_block",
    "reseal_concept_contract_lineage",
    "require_conception_contract_for_target",
    "require_valid_concept_contract",
    "validate_concept_contract",
]
