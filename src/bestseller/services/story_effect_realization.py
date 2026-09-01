"""Auditable realization gates for selected story-effect contracts.

Book-level effect selection is only a promise.  This module checks whether a
chapter turns that promise into observable story state.  It deliberately does
not use prose keywords: a chapter is accepted because its contract describes
an action and its consequence, not because it contains words such as "funny".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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


def _selected_effect_keys(chapter: Mapping[str, object]) -> set[str]:
    keys: set[str] = set()
    for field in ("selected_effect_skills", "story_effect_skill_selection", "effect_skills"):
        if field in chapter:
            keys.update(_collect_effect_keys(chapter[field]))
    # A few callers carry creation metadata under one of these containers.
    for field in ("metadata", "enhancers", "story_enhancers"):
        value = chapter.get(field)
        if isinstance(value, Mapping):
            keys.update(_collect_effect_keys(value))
    return keys


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
    selected = sorted(_selected_effect_keys(chapter))
    comedy_selected = _COMEDY_KEY in selected
    evidence, missing = _comedy_evidence(chapter) if comedy_selected else ({}, [])
    issues = ["comedy_contract_unrealized"] if comedy_selected and missing else []
    return {
        "passed": not issues,
        "issues": issues,
        "selected_effects": selected,
        "realized_effects": [_COMEDY_KEY] if comedy_selected and not missing else [],
        "missing": missing,
        "evidence": evidence,
    }


def _realized_contracts(chapter: Mapping[str, object]) -> set[str]:
    result = audit_story_effect_realization(chapter)
    return set(result["realized_effects"])


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


__all__ = ["audit_story_effect_realization", "compare_effect_contracts"]
