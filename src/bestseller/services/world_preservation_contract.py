"""Source-only, hash-bound anchors. Literal preservation is NOT semantic proof."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

from bestseller.services.story_rebuild_workspace import source_content_hash

PRESERVATION_VERSION = "world-preservation-anchors.v1"
DISJOINT_PRESERVATION_VERSION = "world-preservation-disjoint.v2"
ATOMIC_PRESERVATION_VERSION = "world-preservation-atomic.v3"
PRESERVATION_AXES = (
    "actor_scope",
    "trigger",
    "failure_outcome",
    "existing_protection",
    "unaffected_mechanism",
)


def build_preservation_response(
    payload: object,
    *,
    disjoint: bool,
    atomic: bool = False,
    source_hash: str,
    target: Mapping[str, Any],
    required_axes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Strict protocol parsing, shared by generation and raw-receipt replay."""
    if atomic:
        if not disjoint:
            raise ValueError("PRESERVATION_ATOMIC_REQUIRES_DISJOINT")
        return build_atomic_preservation_contract(
            payload, source_hash=source_hash, target=target, required_axes=required_axes
        )
    keys = {"source_hash", "clause_id", "obligations"}
    if disjoint:
        keys.add("amendment_quotes")
    if not isinstance(payload, Mapping) or set(payload) != keys:
        raise ValueError("PRESERVATION_INVALID_PAYLOAD")
    if disjoint and not isinstance(payload["amendment_quotes"], list):
        raise ValueError("PRESERVATION_INVALID_AMENDMENTS")
    return build_preservation_contract(
        {key: payload[key] for key in ("source_hash", "clause_id", "obligations")},
        source_hash=source_hash,
        target=target,
        required_axes=required_axes,
        amendment_quotes=payload["amendment_quotes"] if disjoint else None,
    )


def build_atomic_preservation_contract(
    payload: object,
    *,
    source_hash: str,
    target: Mapping[str, Any],
    required_axes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Project a lossless, single-role partition; semantics remain unapproved.

    Connector labels are model proposals, not grammatical authority. They and
    every proposed relation must be checked by the pending semantic review.
    """
    if not isinstance(payload, Mapping) or set(payload) != {
        "source_hash",
        "clause_id",
        "atoms",
        "relations",
        "absence_reasons",
    }:
        raise ValueError("ATOMIC_INVALID_PAYLOAD")
    if payload["source_hash"] != source_hash or payload["clause_id"] != target["clause_id"]:
        raise ValueError("ATOMIC_STALE_SOURCE")
    atoms = payload["atoms"]
    if not isinstance(atoms, list) or not 2 <= len(atoms) <= 32:
        raise ValueError("ATOMIC_INVALID_ATOMS")
    by_id = {}
    groups: dict[str, list[dict[str, Any]]] = {
        role: [] for role in (*PRESERVATION_AXES, "amend_cause", "connector")
    }
    for atom in atoms:
        if not isinstance(atom, Mapping) or set(atom) != {"id", "quote", "role", "reason"}:
            raise ValueError("ATOMIC_INVALID_ATOM")
        atom_id, quote, role, reason = (atom[key] for key in ("id", "quote", "role", "reason"))
        if (
            not isinstance(atom_id, str)
            or not re.fullmatch(r"A[1-9][0-9]?", atom_id)
            or atom_id in by_id
            or not isinstance(role, str)
            or role not in groups
            or not isinstance(quote, str)
            or not quote
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 300
        ):
            raise ValueError("ATOMIC_INVALID_ATOM")
        if role != "connector" and (
            not any(c.isalnum() for c in quote) or target["quote"].count(quote) != 1
        ):
            raise ValueError("ATOMIC_AMBIGUOUS_FACT_SPAN")
        by_id[atom_id] = dict(atom)
        groups[role].append(dict(atom))
    if "".join(atom["quote"] for atom in atoms) != target["quote"]:
        raise ValueError("ATOMIC_SOURCE_NOT_LOSSLESS")
    if not groups["amend_cause"]:
        raise ValueError("ATOMIC_NO_AMENDMENT")
    relations = payload["relations"]
    if not isinstance(relations, list) or not 1 <= len(relations) <= 48:
        raise ValueError("ATOMIC_INVALID_RELATIONS")
    edges = set()
    related_ids: set[str] = set()
    for edge in relations:
        if not isinstance(edge, Mapping) or set(edge) != {"from", "to", "relation"}:
            raise ValueError("ATOMIC_INVALID_RELATION")
        values = tuple(edge[k] for k in ("from", "to", "relation"))
        if any(not isinstance(value, str) for value in values):
            raise ValueError("ATOMIC_INVALID_RELATION")
        source_id, dest_id, relation = values
        if (
            source_id not in by_id
            or dest_id not in by_id
            or source_id == dest_id
            or relation not in {"condition_for", "causes", "contrasts"}
            or values in edges
            or by_id[source_id]["role"] == "connector"
            or by_id[dest_id]["role"] == "connector"
        ):
            raise ValueError("ATOMIC_INVALID_RELATION")
        edges.add(values)
        related_ids.update((source_id, dest_id))
    if any(atom["id"] not in related_ids for atom in groups["amend_cause"]):
        raise ValueError("ATOMIC_UNLINKED_AMENDMENT")
    absence = payload["absence_reasons"]
    absent_axes = {axis for axis in PRESERVATION_AXES if not groups[axis]}
    if (
        not isinstance(absence, Mapping)
        or set(absence) != absent_axes
        or any(
            not isinstance(reason, str) or not reason.strip() or len(reason) > 600
            for reason in absence.values()
        )
    ):
        raise ValueError("ATOMIC_INVALID_ABSENCE")
    projected = {
        "source_hash": source_hash,
        "clause_id": target["clause_id"],
        "obligations": [
            {
                "axis": axis,
                "presence": "present" if groups[axis] else "absent",
                "before_quotes": [atom["quote"] for atom in groups[axis]],
                "reason": (
                    "Proposed source atoms: " + ",".join(a["id"] for a in groups[axis])
                    if groups[axis]
                    else absence[axis]
                ),
            }
            for axis in PRESERVATION_AXES
        ],
    }
    result = build_preservation_contract(
        projected,
        source_hash=source_hash,
        target=target,
        required_axes=required_axes,
        amendment_quotes=[atom["quote"] for atom in groups["amend_cause"]],
    )
    result.update(
        version=ATOMIC_PRESERVATION_VERSION,
        atomic_source=deepcopy(dict(payload)),
        unreviewed_connector_ids=[atom["id"] for atom in groups["connector"]],
        relation_semantics="unreviewed_source_proposal",
    )
    return result


def build_preservation_contract(
    payload: object,
    *,
    source_hash: str,
    target: Mapping[str, Any],
    required_axes: tuple[str, ...] = (),
    amendment_quotes: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "source_hash",
        "clause_id",
        "obligations",
    }:
        raise ValueError("PRESERVATION_INVALID_PAYLOAD")
    if payload["source_hash"] != source_hash or payload["clause_id"] != target["clause_id"]:
        raise ValueError("PRESERVATION_STALE_SOURCE")
    rows = payload["obligations"]
    if not isinstance(rows, list) or len(rows) != len(PRESERVATION_AXES):
        raise ValueError("PRESERVATION_INCOMPLETE_AXES")
    seen = set()
    normalized = {}
    absent = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "axis",
            "presence",
            "before_quotes",
            "reason",
        }:
            raise ValueError("PRESERVATION_INVALID_OBLIGATION")
        axis = row["axis"]
        if not isinstance(axis, str) or axis not in PRESERVATION_AXES or axis in seen:
            raise ValueError("PRESERVATION_DUPLICATE_OR_UNKNOWN_AXIS")
        seen.add(axis)
        reason = row["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 600:
            raise ValueError("PRESERVATION_MISSING_REASON")
        quotes = row["before_quotes"]
        if (
            not isinstance(quotes, list)
            or len(quotes) > 8
            or any(
                not isinstance(q, str) or not q.strip() or q not in target["quote"] for q in quotes
            )
        ):
            raise ValueError("PRESERVATION_FORGED_ANCHOR")
        if len(set(quotes)) != len(quotes):
            raise ValueError("PRESERVATION_DUPLICATE_ANCHOR")
        if row["presence"] == "present" and quotes:
            pass
        elif row["presence"] == "absent" and not quotes and axis not in required_axes:
            absent.append(axis)
        else:
            raise ValueError("PRESERVATION_UNPROVEN_PRESENCE_OR_ABSENCE")
        normalized[axis] = deepcopy(dict(row))
    if len(absent) == len(PRESERVATION_AXES):
        raise ValueError("PRESERVATION_NO_ANCHORS")
    result = {
        "version": PRESERVATION_VERSION,
        "source_hash": source_hash,
        "target_hash": source_content_hash(target),
        "clause_id": target["clause_id"],
        "obligations": [normalized[axis] for axis in PRESERVATION_AXES],
        "required_axes": list(required_axes),
        "unproven_absence_axes": sorted(absent),
        "status": "source_extraction_proposal",
        "semantic_review_required": True,
        "promotion_allowed": False,
    }
    if amendment_quotes is not None:
        if (
            not isinstance(amendment_quotes, list)
            or not 1 <= len(amendment_quotes) <= 8
            or any(
                not isinstance(q, str)
                or not any(c.isalnum() for c in q)
                or target["quote"].count(q) != 1
                for q in amendment_quotes
            )
            or len(set(amendment_quotes)) != len(amendment_quotes)
        ):
            raise ValueError("PRESERVATION_INVALID_AMENDMENTS")
        kept: set[int] = set()
        for row in result["obligations"]:
            for quote in row["before_quotes"]:
                start = target["quote"].find(quote)
                while start >= 0:
                    kept.update(range(start, start + len(quote)))
                    start = target["quote"].find(quote, start + 1)
        amended: set[int] = set()
        for quote in amendment_quotes:
            start = target["quote"].index(quote)
            positions = set(range(start, start + len(quote)))
            if positions & kept:
                raise ValueError("PRESERVATION_KEEP_AMEND_OVERLAP")
            if positions & amended:
                raise ValueError("PRESERVATION_AMENDMENT_OVERLAP")
            amended.update(positions)
        result["version"] = DISJOINT_PRESERVATION_VERSION
        result["amendment_quotes"] = list(amendment_quotes)
        result["amendment_authority"] = "proposed_cost_cause_removal_requires_semantic_review"
    return result


def verify_preservation_contract(
    contract: Mapping[str, Any],
    *,
    source_hash: str,
    target: Mapping[str, Any],
    required_axes: tuple[str, ...] = (),
) -> None:
    if contract.get("version") == ATOMIC_PRESERVATION_VERSION:
        rebuilt = build_atomic_preservation_contract(
            contract.get("atomic_source"),
            source_hash=source_hash,
            target=target,
            required_axes=required_axes,
        )
        if dict(contract) != rebuilt:
            raise ValueError("PRESERVATION_RECEIPT_MISMATCH")
        return
    rebuilt = build_preservation_contract(
        {key: contract.get(key) for key in ("source_hash", "clause_id", "obligations")},
        source_hash=source_hash,
        target=target,
        required_axes=required_axes,
        amendment_quotes=contract.get("amendment_quotes"),
    )
    if dict(contract) != rebuilt:
        raise ValueError("PRESERVATION_RECEIPT_MISMATCH")


def check_preservation_anchors(
    contract: Mapping[str, Any],
    replacement: str,
    *,
    source_hash: str,
    target: Mapping[str, Any],
    required_axes: tuple[str, ...] = (),
) -> dict[str, Any]:
    verify_preservation_contract(
        contract, source_hash=source_hash, target=target, required_axes=required_axes
    )
    if not isinstance(replacement, str) or not replacement.strip():
        raise ValueError("PRESERVATION_EMPTY_REPLACEMENT")
    missing = [
        row["axis"]
        for row in contract["obligations"]
        if any(quote not in replacement for quote in row["before_quotes"])
    ]
    unmodified = [q for q in contract.get("amendment_quotes", []) if q in replacement]
    result = {
        "status": "rejected" if missing or unmodified else "literal_anchors_present",
        "missing_axes": missing,
        "contract_hash": source_content_hash(contract),
        "unproven_absence_axes": list(contract["unproven_absence_axes"]),
        "semantic_review_required": True,
        "promotion_allowed": False,
        "evidence_boundary": "Literal presence only; absence, negation and causality unproven.",
    }
    if "amendment_quotes" in contract:
        result["unmodified_amendments"] = unmodified
    return result


def require_preservation_edit_space(
    contract: Mapping[str, Any], *, target: Mapping[str, Any]
) -> None:
    """Reject a fully locked clause before spending an editor call.

    This conservative check does not find a safe deletion or authorize any
    uncovered character. It detects only the proven over-constraint case.
    """
    text = target["quote"]
    covered: set[int] = set()
    for row in contract["obligations"]:
        for quote in row["before_quotes"]:
            start = text.find(quote)
            while start >= 0:
                covered.update(range(start, start + len(quote)))
                start = text.find(quote, start + 1)
    meaningful = {i for i, char in enumerate(text) if char.isalnum()}
    if meaningful and meaningful <= covered:
        raise ValueError("PRESERVATION_NO_SEMANTIC_EDIT_SPACE")
