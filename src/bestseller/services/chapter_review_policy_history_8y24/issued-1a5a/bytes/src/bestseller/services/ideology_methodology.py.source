"""Source-bound effective methodology, independent of cost choices and old caches."""

# ruff: noqa: ANN401 -- Dynamic ORM captures and serialized domain values.
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any

_FIELDS = frozenset(
    {
        "cosmic_premise",
        "thesis_statement",
        "core_question",
        "sub_themes",
        "primary_motif",
        "secondary_motifs",
        "hidden_endgame_motif",
        "belief_arc",
        "cost_system",
        "motif_to_world_bindings",
        "per_volume_thesis_pressure",
        "forbidden_resolutions",
    }
)
_LAYERS = frozenset({"cosmic_order", "subject_choice", "cognitive_crisis", "ethical_reversal"})


def _payload(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value or {})


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def resolve_ideology_methodology_source(
    kernel: Any,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return only exact author clauses; unselected structure is not an obligation.

    A canonical explicit hook entry is the existing author input boundary. No
    top-level source label, approved AI artifact, cost option, seed, or cached
    receipt can grant this authority. This function never mutates a source row.
    """
    from bestseller.domain.ideology import IdeologyKernel

    raw = _payload(kernel)
    metadata = metadata or {}
    creation = metadata.get("creation_intent_contract")
    creation = dict(creation) if isinstance(creation, Mapping) else {}
    sources = creation.get("field_sources")
    hook = creation.get("hook_spec")
    contract = hook.get("ideology_contract") if isinstance(hook, Mapping) else None
    authored = (
        isinstance(sources, Mapping)
        and sources.get("hook_spec") == "explicit"
        and contract is not None
    )
    fields: dict[str, Any] = {}
    layers: list[str] = []
    roles: list[str] = []
    if authored:
        if (
            not isinstance(contract, Mapping)
            or contract.get("version") != "ideology-author-contract.v1"
        ):
            raise ValueError("invalid explicit ideology contract version")
        if set(contract) - {"version", "fields", "required_layers", "required_secondary_roles"}:
            raise ValueError("unknown explicit ideology contract fields")
        candidate = contract.get("fields", {})
        if not isinstance(candidate, Mapping) or set(candidate) - _FIELDS:
            raise ValueError("invalid explicit ideology field scope")
        # Validate, but retain precisely the supplied fields and strings. Never
        # synthesize a missing layer, belief transition, motif, or ending.
        IdeologyKernel.model_validate(candidate)
        fields = deepcopy(dict(candidate))
        for key, allowed in (
            ("required_layers", _LAYERS),
            ("required_secondary_roles", {"action", "suspense"}),
        ):
            value = contract.get(key, [])
            if not isinstance(value, list) or any(
                not isinstance(v, str) or v not in allowed for v in value
            ):
                raise ValueError(f"invalid explicit ideology {key}")
            if key == "required_layers":
                layers = sorted(set(value))
            else:
                roles = sorted(set(value))
    # If a current cache is supplied, refuse a different old source. A typed
    # argument may be a lossless hydration of that cache; receipt still binds
    # the original raw bytes' canonical content, not the normalized surrogate.
    sdk = metadata.get("story_design_kernel")
    current = sdk.get("ideology_kernel") if isinstance(sdk, Mapping) else None
    if current is None:
        current = metadata.get("ideology_kernel")
    binding = "argument_only"
    if current is not None:
        if not isinstance(current, Mapping):
            raise ValueError("invalid current ideology cache")
        same = kernel is None or _hash(raw) == _hash(dict(current))
        if not same and isinstance(kernel, IdeologyKernel):
            same = raw == IdeologyKernel.model_validate(current).model_dump(mode="json")
        if not same:
            raise ValueError("ideology source is not the current exact cache")
        raw = dict(current)
        binding = "current_cache_match"
    return {
        "version": "ideology-method-source.v1",
        "authority": "explicit_author_entry" if authored else "unknown",
        "source_kernel_sha256": _hash(raw),
        "source_creation_sha256": _hash(creation),
        "source_contract_sha256": _hash(dict(contract)) if authored else _hash({}),
        "effective_fields": fields,
        "required_layers": layers,
        "required_secondary_roles": roles,
        "suppressed_fields": sorted(key for key in _FIELDS if raw.get(key) and key not in fields),
        "cache_binding": binding,
    }


def project_ideology_kernel(
    kernel: Any, metadata: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    from bestseller.services.story_enhancers import resolve_cost_style

    receipt = resolve_ideology_methodology_source(kernel, metadata)
    return {
        "version": 2,
        **deepcopy(receipt["effective_fields"]),
        "cost_style": resolve_cost_style(metadata),
    }


def build_ideology_methodology_supersession(
    project: Any,
    source_artifact: Any,
    *,
    expected_source_sha256: str,
    expected_creation_sha256: str,
) -> dict[str, Any]:
    """Exact approved-artifact dry-run; no migration of original kernels/facts."""
    if not getattr(project, "id", None) or str(source_artifact.project_id) != str(project.id):
        raise ValueError("ideology methodology project mismatch")
    if not getattr(source_artifact, "id", None) or source_artifact.status != "approved":
        raise ValueError("ideology methodology requires an exact approved artifact")
    content = source_artifact.content
    if not isinstance(content, Mapping):
        raise ValueError("ideology methodology artifact content missing")
    if source_artifact.artifact_type == "story_design_kernel":
        kernel = content.get("ideology_kernel")
    elif source_artifact.artifact_type == "ideology_kernel":
        kernel = content
    else:
        raise ValueError("ideology methodology artifact type mismatch")
    if not isinstance(kernel, Mapping):
        raise ValueError("ideology methodology source missing")
    receipt = resolve_ideology_methodology_source(kernel, project.metadata_json or {})
    if receipt["source_kernel_sha256"] != expected_source_sha256:
        raise ValueError("ideology methodology source hash mismatch")
    if receipt["source_creation_sha256"] != expected_creation_sha256:
        raise ValueError("ideology methodology creation hash mismatch")
    from bestseller.domain.ideology import (
        render_ideology_compact_block,
        render_ideology_kernel_prompt_block,
    )

    receipt.update(
        {
            "project_id": str(project.id),
            "source_artifact_id": str(source_artifact.id),
            "source_artifact_sha256": _hash(dict(content)),
            "dry_run": True,
            "writes": [],
            "effective_projection": project_ideology_kernel(kernel, project.metadata_json or {}),
            "history_preserved": True,
            "effective_prompt_full": render_ideology_kernel_prompt_block(
                kernel, metadata=project.metadata_json or {}
            ),
            "effective_prompt_compact": render_ideology_compact_block(
                kernel, metadata=project.metadata_json or {}
            ),
        }
    )
    receipt["idempotency_key"] = "ideology-method-source:" + _hash(receipt)
    return receipt
