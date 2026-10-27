"""Explicit author selection for optional planning methods, separate from facts.

Global availability, genre/effect choices and generated kernel caches confer no
selection authority. This module never changes history, kernels or knowledge.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

CAPABILITIES = (
    "story_design_kernel",
    "emotion_driven_kernel",
    "entry_system_kernel",
    "public_emotion_kernel",
)
CONTRACT_SCHEMA = "planning-capabilities.v1"
RECEIPT_SCHEMA = "planning-capability-selection-receipt.v1"
SOURCE_PATH = "/creation_intent_contract/hook_spec/planning_capabilities"


class PlanningCapabilitySelectionError(ValueError):
    """An explicit selection or its availability cannot be safely interpreted."""


def _source_hash(source: Any, selection: Any, *, present: bool) -> str | None:  # noqa: ANN401
    # Bind actual relevant raw values, never runtime metadata or model caches.
    # Non-JSON unknown history has no usable digest and grants no selection.
    try:
        raw = json.dumps(
            {"hook_spec_source": source, "planning_capabilities_present": present,
             "planning_capabilities": selection},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None


def resolve_planning_capability(
    project_or_metadata: Any, capability: str, *, available: bool = True,  # noqa: ANN401
) -> dict[str, Any]:
    """Resolve one exact capability from the canonical explicit hook contract.

    The contract has exactly schema/selected; selected is any subset of the
    four capability names with actual bool values. Missing names stay unselected.
    No legacy mapping or self-claimed receipt
    is accepted. Missing/unknown provenance leaves historical data inactive.
    A selected capability that is unavailable is an error, not an implicit opt-out.
    This receipt proves selection only, never kernel validity or source truth.
    """
    if not isinstance(capability, str) or capability not in CAPABILITIES:
        raise PlanningCapabilitySelectionError("planning_capability_unknown")
    if type(available) is not bool:
        raise PlanningCapabilitySelectionError("planning_capability_availability_invalid")
    metadata = (project_or_metadata if isinstance(project_or_metadata, Mapping)
                else getattr(project_or_metadata, "metadata_json", None))
    metadata = metadata if isinstance(metadata, Mapping) else {}
    creation = metadata.get("creation_intent_contract")
    creation = creation if isinstance(creation, Mapping) else {}
    sources = creation.get("field_sources")
    source = sources.get("hook_spec") if isinstance(sources, Mapping) else None
    hook = creation.get("hook_spec")
    present = isinstance(hook, Mapping) and "planning_capabilities" in hook
    selection = hook.get("planning_capabilities") if present else None
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "capability": capability,
        "selected": False,
        "status": "not_applicable",
        "available": available,
        "reason": "author_selection_provenance_unknown",
        "source_path": SOURCE_PATH,
        "source_contract_sha256": _source_hash(source, selection, present=present),
    }
    if source != "explicit":
        return receipt
    if "hook_spec" in creation and not isinstance(hook, Mapping):
        raise PlanningCapabilitySelectionError("planning_capability_explicit_hook_invalid")
    if not present:
        receipt["reason"] = "no_explicit_capability_selection"
        return receipt
    if not isinstance(selection, Mapping) or set(selection) != {"schema", "selected"}:
        raise PlanningCapabilitySelectionError("planning_capability_contract_invalid")
    if selection["schema"] != CONTRACT_SCHEMA:
        raise PlanningCapabilitySelectionError("planning_capability_contract_schema_invalid")
    selected = selection["selected"]
    if not isinstance(selected, Mapping) or not set(selected) <= set(CAPABILITIES):
        raise PlanningCapabilitySelectionError("planning_capability_selected_keys_invalid")
    if any(type(value) is not bool for value in selected.values()):
        raise PlanningCapabilitySelectionError("planning_capability_selected_value_invalid")
    if receipt["source_contract_sha256"] is None:
        raise PlanningCapabilitySelectionError("planning_capability_source_not_json")
    if capability not in selected:
        receipt["reason"] = "not_explicitly_selected"
        return receipt
    if not selected[capability]:
        receipt["reason"] = "explicitly_unselected"
        return receipt
    if not available:
        raise PlanningCapabilitySelectionError("planning_capability_selected_but_unavailable")
    return {**receipt, "selected": True, "status": "selected", "reason": "explicit_author_selection"}


def planning_capability_selected(
    project_or_metadata: Any, capability: str, *, available: bool = True,  # noqa: ANN401
) -> bool:
    """Use the same strict resolution for generation, rendering and readiness."""
    return resolve_planning_capability(project_or_metadata, capability, available=available)["selected"]


__all__ = [
    "CAPABILITIES", "CONTRACT_SCHEMA", "RECEIPT_SCHEMA", "PlanningCapabilitySelectionError",
    "resolve_planning_capability", "planning_capability_selected",
]
