"""Pure POV projection from the existing, hash-bound explicit creation input.

This is neither a new author selection nor a migration. Persisted profiles,
origins, style guides and historical review evidence remain untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

from bestseller.domain.project import WritingProfile
from bestseller.services.content_origin_policy import content_hash, origin_record

POLICY = "explicit-author-pov.v1"
_PATH = "style.pov_type"
_ALIASES = {
    "first": "first-person",
    "first-person": "first-person",
    "second": "second",
    "second-person": "second",
    "third": "third",
    "third-person": "third",
    "third-limited": "third-limited",
    "third-person-limited": "third-limited",
    "close-third": "third-limited",
    "omniscient": "omniscient",
    "third-omniscient": "omniscient",
    "third-person-omniscient": "omniscient",
}


def _normalized(value: str) -> str:
    token = value.strip().casefold().replace("_", "-")
    # Free-form authored descriptions remain literal; do not infer a person
    # from a character name or a sentence fragment.
    return _ALIASES.get(token, value.strip())


def _selected_pov(metadata: Mapping[str, Any]) -> tuple[str, str] | None:
    raw = metadata.get("creation_intent_contract")
    if not isinstance(raw, Mapping):
        return None
    sources = raw.get("field_sources")
    if not isinstance(sources, Mapping) or sources.get("pov") != "explicit" or "pov" not in raw:
        return None
    selected_hash = metadata.get("creation_intent_contract_hash")
    if selected_hash is None:
        return None
    if not isinstance(selected_hash, str) or re.fullmatch("[0-9a-f]{64}", selected_hash) is None:
        raise ValueError("explicit_author_pov_hash_invalid")

    from bestseller.services.creation_contract_authority import select_creation_contracts
    from bestseller.services.creation_intent_contract import contract_from_payload

    try:
        contract = contract_from_payload(metadata)
        if content_hash(raw) != selected_hash or contract.contract_hash() != selected_hash:
            raise ValueError("explicit_author_pov_hash_mismatch")
        selected = select_creation_contracts(metadata, source_bound=True)
        value = selected["creation_intent_contract"]["pov"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("explicit_author_pov_contract_unverified") from exc
    if not isinstance(value, str) or not value.strip():
        raise ValueError("explicit_author_pov_value_invalid")
    receipt = metadata.get("creation_input_receipt")
    if receipt is not None and (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != contract.schema_version
        or receipt.get("contract_hash") != selected_hash
        or content_hash(receipt.get("field_sources")) != content_hash(sources)
    ):
        raise ValueError("explicit_author_pov_input_receipt_mismatch")
    return _normalized(value), selected_hash


def apply_explicit_author_pov(project: object, profile: WritingProfile) -> WritingProfile:
    """Apply only a proven explicit choice to the newly resolved runtime model."""
    metadata = getattr(project, "metadata_json", None)
    if not isinstance(metadata, Mapping):
        return profile
    selected = _selected_pov(metadata)
    if selected is None:
        return profile
    pov, selected_hash = selected
    if profile.interactive_fiction.enabled and pov != "second":
        raise ValueError("explicit_author_pov_interactive_mode_conflict")
    raw = metadata.get("writing_profile")
    style = raw.get("style") if isinstance(raw, Mapping) else None
    original = style.get("pov_type") if isinstance(style, Mapping) else None
    origins = metadata.get("writing_profile_origins")
    evidence = origins.get(_PATH) if isinstance(origins, Mapping) else None
    adopted = isinstance(evidence, Mapping) and evidence.get("adopted") is True
    if adopted:
        if not isinstance(original, str) or evidence.get("content_hash") != content_hash(original):
            raise ValueError("explicit_author_pov_adopted_source_unverified")
        if _normalized(original) != pov:
            raise ValueError("explicit_author_pov_adopted_choice_conflict")
    previous_value = original if original is not None else profile.style.pov_type
    previous_origin = (
        evidence if isinstance(evidence, Mapping) else profile._content_origins.get(_PATH)
    )
    profile.style.pov_type = pov
    profile._content_origins[_PATH] = {
        **origin_record(pov, kind="derived", adopted=adopted, producer=POLICY),
        "creation_intent_hash": selected_hash,
        "field_source": "explicit",
        "previous_value": deepcopy(previous_value),
        "previous_origin": deepcopy(previous_origin),
        "storage_modified": False,
    }
    return profile
