"""Auditable realization gates for selected story-effect contracts.

Book-level effect selection is only a promise.  This module checks whether a
chapter turns that promise into observable story state.  It deliberately does
not use prose keywords: a chapter is accepted because its contract describes
an action and its consequence, not because it contains words such as "funny".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any
from uuid import UUID

_COMEDY_KEY = "comedy_engine"
_COMEDY_CONTRACT = "comedy_contract"
_COMEDY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("setup", ("setup",)),
    (
        "reversal_or_misalignment",
        ("reversal_or_misalignment", "reversal", "misalignment", "turning_point"),
    ),
    (
        "character_reaction",
        ("character_reaction", "reaction", "people_reaction", "social_reaction"),
    ),
    (
        "situation_change",
        ("situation_change", "situation_delta", "consequence", "outcome", "changed_state"),
    ),
)
_EVIDENCE_FIELDS = (
    "evidence_spans",
    "evidence_quotes",
    "prose_evidence",
    "observed_spans",
)
_EFFECT_SELECTION_FIELDS = (
    "primary",
    "secondary",
    "effect_skills",
    "selected_effect_skills",
)

STORY_EFFECT_REALIZATION_BLOCK_CODE = "STORY_EFFECT_REALIZATION_UNREALIZED"


def _nonempty(value: object) -> bool:
    """Return whether a contract value contains authored evidence.

    Empty containers and whitespace are not evidence.  We intentionally do
    not inspect the wording, so this remains usable across genres and
    languages.
    """

    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_nonempty(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_nonempty(item) for item in value)
    return True


def _effect_key(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for field in ("skill_key", "key", "engine", "id", "name"):
            candidate = value.get(field)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def _collect_effect_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        direct = _effect_key(value)
        if direct:
            keys.add(direct)
        for field in _EFFECT_SELECTION_FIELDS:
            if field in value:
                keys.update(_collect_effect_keys(value[field]))
        return keys
    if isinstance(value, (list, tuple, set)):
        for item in value:
            keys.update(_collect_effect_keys(item))
        return keys
    if isinstance(value, str) and value.strip():
        keys.add(value.strip())
    return keys


def _routed_effect_keys(chapter: Mapping[str, object]) -> set[str]:
    keys: set[str] = set()
    for field in ("selected_effect_skills", "story_effect_skill_selection", "effect_skills"):
        if field in chapter:
            keys.update(_collect_effect_keys(chapter[field]))
    # A few callers carry creation metadata under one of these containers.
    for field in ("metadata",):
        value = chapter.get(field)
        if isinstance(value, Mapping):
            keys.update(_collect_effect_keys(value))
    return keys


def resolve_effect_requirements(chapter: Mapping[str, object]) -> dict[str, object]:
    """Only explicit creation input can authorize a realization obligation.

    A palette chooses a goal, never a universal four-stage formula. Historical
    projections, labels and planner JSON are not proof of an author entry.
    The persisted adapter supplies project authority independently of chapter
    JSON. The existing creation contract records explicit/default/legacy input.
    """
    from bestseller.services.story_enhancers import resolve_story_enhancers

    project = chapter.get("_effect_project_metadata")
    authority = project if isinstance(project, Mapping) else chapter
    creation = authority.get("creation_intent_contract")
    creation = creation if isinstance(creation, Mapping) else {}
    field_sources = creation.get("field_sources")
    field_sources = field_sources if isinstance(field_sources, Mapping) else {}
    routed = _routed_effect_keys(chapter)
    palette: set[str] = set()
    if field_sources.get("story_enhancers") == "explicit":
        palette = set(resolve_story_enhancers({"creation_intent_contract": creation}).effect_skills)
    sources = {key: ["creation_intent_contract.field_sources.story_enhancers=explicit"] for key in routed & palette}
    strict: set[str] = set()
    # hook_spec is the existing provenance-tracked free-form author input.
    # Only explicit input of this exact contract opts into the stage formula.
    hook = creation.get("hook_spec")
    if field_sources.get("hook_spec") == "explicit" and isinstance(hook, Mapping):
        contracts = hook.get("effect_realization_contracts")
        contract = contracts.get(_COMEDY_KEY) if isinstance(contracts, Mapping) else None
        if isinstance(contract, Mapping):
            stages = contract.get("required_stages")
            if (
                contract.get("approved") is not False
                and isinstance(stages, (list, tuple))
                and {field for field, _ in _COMEDY_FIELDS}.issubset(stages)
                and _COMEDY_KEY in routed
            ):
                strict.add(_COMEDY_KEY)
                sources.setdefault(_COMEDY_KEY, []).append("creation_intent_contract.field_sources.hook_spec=explicit:effect_realization_contracts")
    eligible = sorted(sources)
    return {
        "version": "effect-requirement-source.v2",
        "required_effects": sorted(strict),
        "selected_effects": eligible,
        "diagnostic_effects": sorted(routed - strict),
        "advisory_effects": sorted(routed - set(eligible)),
        "book_effects": sorted(palette),
        "sources": sources,
        "authority_status": "explicit_creation_entry" if sources else "unverified_or_unselected",
    }


def _selected_effect_keys(chapter: Mapping[str, object]) -> set[str]:
    return set(resolve_effect_requirements(chapter)["required_effects"])


def _find_comedy_contract(chapter: Mapping[str, object]) -> Mapping[str, object] | None:
    candidates: list[object] = [chapter.get(_COMEDY_CONTRACT)]
    selected = chapter.get("selected_effect_skills")
    if isinstance(selected, Mapping):
        candidates.extend(
            (
                selected.get(_COMEDY_CONTRACT),
                selected.get(_COMEDY_KEY),
            )
        )
        expected = selected.get("expected_contracts")
        if isinstance(expected, Mapping):
            candidates.extend((expected.get(_COMEDY_CONTRACT), expected.get(_COMEDY_KEY)))
    expected = chapter.get("expected_contracts")
    if isinstance(expected, Mapping):
        candidates.extend((expected.get(_COMEDY_CONTRACT), expected.get(_COMEDY_KEY)))
    # Some outline adapters put the four fields directly in a named effect
    # object.  This is still a structured contract, not a prose keyword scan.
    if isinstance(selected, Mapping):
        candidates.append(selected)
    for candidate in candidates:
        if isinstance(candidate, Mapping) and _nonempty(candidate):
            return candidate
    return None


def _field_value(contract: Mapping[str, object], aliases: tuple[str, ...]) -> object:
    for field in aliases:
        if field in contract and _nonempty(contract[field]):
            return contract[field]
    return None


def _prose_text(chapter: Mapping[str, object]) -> str:
    """Collect only the chapter's authored prose, never its planning fields."""

    chunks: list[str] = []
    for field in ("prose", "text", "body", "content"):
        value = chapter.get(field)
        if isinstance(value, str) and value.strip():
            chunks.append(value)
    scenes = chapter.get("scenes")
    if isinstance(scenes, Sequence) and not isinstance(scenes, (str, bytes, bytearray)):
        for scene in scenes:
            if isinstance(scene, Mapping):
                for field in ("prose", "text", "body", "content"):
                    value = scene.get(field)
                    if isinstance(value, str) and value.strip():
                        chunks.append(value)
    return "\n".join(chunks)


def _evidence_spans(contract: Mapping[str, object]) -> Mapping[str, object] | None:
    for field in _EVIDENCE_FIELDS:
        value = contract.get(field)
        if isinstance(value, Mapping) and _nonempty(value):
            return value
    return None


def _evidence_value(spans: Mapping[str, object], canonical: str) -> object:
    aliases = dict(_COMEDY_FIELDS)[canonical]
    return _field_value(spans, (canonical, *aliases))


def _comedy_evidence(chapter: Mapping[str, object]) -> tuple[dict[str, object], list[str]]:
    contract = _find_comedy_contract(chapter)
    evidence: dict[str, object] = {}
    missing: list[str] = []
    if contract is None:
        return evidence, [field for field, _ in _COMEDY_FIELDS]
    spans = _evidence_spans(contract)
    prose = _prose_text(chapter)
    if spans is None:
        return evidence, [field for field, _ in _COMEDY_FIELDS] + ["evidence_spans"]
    for canonical, aliases in _COMEDY_FIELDS:
        value = _field_value(contract, aliases)
        quote = _evidence_value(spans, canonical)
        if value is None:
            missing.append(canonical)
        elif quote is None or not isinstance(quote, str) or quote not in prose:
            missing.append(f"{canonical}_evidence")
        else:
            evidence[canonical] = {"contract": value, "quote": quote}
    return evidence, missing


def _observation_stage(
    observation: Mapping[str, object],
    canonical: str,
) -> tuple[str, str] | None:
    aliases = dict(_COMEDY_FIELDS)[canonical]
    value = _field_value(observation, (canonical, *aliases))
    if not isinstance(value, Mapping):
        return None
    description = str(
        value.get("description") or value.get("observed_change") or value.get("meaning") or ""
    ).strip()
    quote = str(value.get("quote") or value.get("evidence") or "").strip()
    if not description or not quote:
        return None
    return description, quote


def build_comedy_contract_from_observation(
    observation: Mapping[str, object],
    prose: str,
    *,
    planned_contract: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Build an auditable comedy contract from exact, ordered prose spans.

    The model is allowed to identify the semantic stages, but it cannot invent
    evidence: every quote must be a verbatim substring of the shipped prose and
    the four stages must occur in narrative order.  This turns a planning
    promise into replayable post-write evidence instead of trusting a label.
    """

    if not isinstance(observation, Mapping) or not isinstance(prose, str) or not prose:
        return None
    descriptions: dict[str, str] = {}
    evidence_spans: dict[str, str] = {}
    cursor = 0
    distinct_quotes: set[str] = set()
    for canonical, _aliases in _COMEDY_FIELDS:
        stage = _observation_stage(observation, canonical)
        if stage is None:
            return None
        description, quote = stage
        position = prose.find(quote, cursor)
        if position < 0:
            return None
        cursor = position + len(quote)
        descriptions[canonical] = description
        evidence_spans[canonical] = quote
        distinct_quotes.add(quote)
    # One sentence may legitimately carry two adjacent stages, but accepting
    # one duplicated quote for all four would turn the verifier into a label
    # echo.  Require at least three independently located evidence spans.
    if len(distinct_quotes) < 3:
        return None
    return {
        **(dict(planned_contract) if isinstance(planned_contract, Mapping) else {}),
        **descriptions,
        "evidence_spans": evidence_spans,
        "evidence_source": "post_write_verbatim_observation",
    }


def _parse_json_object(raw: str) -> Mapping[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


async def extract_story_effect_evidence(
    session: Any,
    settings: Any,
    *,
    chapter_payload: Mapping[str, object],
    prose: str,
    project_id: UUID,
    chapter_id: UUID,
    chapter_number: int,
    workflow_run_id: UUID | None,
    step_run_id: UUID | None = None,
) -> dict[str, object]:
    """Observe selected effects in prose and return validated evidence only."""

    selected = _selected_effect_keys(chapter_payload)
    if _COMEDY_KEY not in selected:
        return {"status": "not_selected", "contract": None, "llm_run_id": None}

    from bestseller.services.llm import LLMCompletionRequest, complete_text

    planned_contract = _find_comedy_contract(chapter_payload) or {}
    fallback = {
        canonical: {"description": "", "quote": ""} for canonical, _aliases in _COMEDY_FIELDS
    }
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="critic",
            system_prompt=(
                "你是小说喜剧效果的写后证据审计员。只根据已完成正文识别四个按顺序发生的阶段："
                "setup、reversal_or_misalignment、character_reaction、situation_change。"
                "每阶段返回 description 和 quote；quote 必须逐字复制正文中连续原句，不能改写、"
                "不能从计划合同抄取。若正文没有某阶段，该阶段两个字段都返回空字符串。"
                "只输出一个 JSON 对象，不解释。"
            ),
            user_prompt=(
                "返回结构："
                '{"setup":{"description":"","quote":""},'
                '"reversal_or_misalignment":{"description":"","quote":""},'
                '"character_reaction":{"description":"","quote":""},'
                '"situation_change":{"description":"","quote":""}}。\n\n'
                "计划中的喜剧兑现点只用于理解目标，不可当证据：\n"
                f"{json.dumps(planned_contract, ensure_ascii=False)}\n\n"
                f"章节正文：\n{prose}"
            ),
            fallback_response=json.dumps(fallback, ensure_ascii=False),
            prompt_template="story_effect_comedy_evidence",
            prompt_version="1.0",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            step_run_id=step_run_id,
            temperature_override=0.1,
            max_tokens_override=1200,
            metadata={
                "chapter_id": str(chapter_id),
                "chapter_number": int(chapter_number),
                "selected_effect": _COMEDY_KEY,
            },
        ),
    )
    observation = _parse_json_object(completion.content)
    contract = build_comedy_contract_from_observation(
        observation,
        prose,
        planned_contract=planned_contract,
    )
    return {
        "status": "verified" if contract is not None else "unrealized",
        "contract": contract,
        "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
        "provider": completion.provider,
        "model_name": completion.model_name,
    }


def audit_story_effect_realization(chapter: Mapping[str, object]) -> dict[str, object]:
    """Audit effects selected for one chapter.

    The comedy contract is considered realized only when all four observable
    stages exist: setup, reversal/misalignment, character reaction, and a
    changed situation.  Metadata or a route alone therefore cannot pass.
    """

    if not isinstance(chapter, Mapping):
        return {
            "passed": False,
            "issues": ["invalid_chapter"],
            "selected_effects": [],
            "realized_effects": [],
            "missing": [],
            "evidence": {},
        }
    requirements = resolve_effect_requirements(chapter)
    selected = requirements["selected_effects"]
    comedy_selected = _COMEDY_KEY in selected
    evidence, missing = _comedy_evidence(chapter) if comedy_selected else ({}, [])
    unrealized = comedy_selected and bool(missing)
    strict = _COMEDY_KEY in requirements["required_effects"]
    issues = ["comedy_contract_unrealized"] if strict and unrealized else []
    return {
        "passed": not issues,
        "issues": issues,
        "selected_effects": selected,
        "realized_effects": [_COMEDY_KEY] if comedy_selected and not missing else [],
        "missing": missing,
        "evidence": evidence,
        "requirement_provenance": requirements,
        "diagnostic_issues": ["comedy_contract_unrealized"] if unrealized and not strict else [],
        "editorial_debt": ["comedy_realization_unverified"] if unrealized and not strict else [],
    }


def _realized_contracts(chapter: Mapping[str, object]) -> set[str]:
    result = audit_story_effect_realization(chapter)
    required = result.get("requirement_provenance", {}).get("required_effects", [])
    return set(result["realized_effects"]) & set(required)


def compare_effect_contracts(
    before: Mapping[str, object], after: Mapping[str, object]
) -> dict[str, object]:
    """Detect deletion of an already-realized effect during rewrite.

    Only a realized contract in ``before`` is protected.  An incomplete draft
    is not frozen, which keeps the rewrite loop able to repair it.
    """

    before_realized = _realized_contracts(before) if isinstance(before, Mapping) else set()
    after_realized = _realized_contracts(after) if isinstance(after, Mapping) else set()
    deleted = sorted(before_realized - after_realized)
    return {
        "passed": not deleted,
        "issues": ["protected_effect_deleted"] if deleted else [],
        "deleted_effects": deleted,
        "before_realized_effects": sorted(before_realized),
        "after_realized_effects": sorted(after_realized),
    }


__all__ = [
    "STORY_EFFECT_REALIZATION_BLOCK_CODE",
    "audit_story_effect_realization",
    "build_comedy_contract_from_observation",
    "compare_effect_contracts",
    "extract_story_effect_evidence",
    "resolve_effect_requirements",
]
