"""Explicit authored concept/fidelity/packaging contracts, never auto-pass.

Source contracts: concept_tournament.py (_default_generator and parsers),
conception.py (_llm_call_json, _packaging_review_template), and
final_packaging_canon.py. Native validators remain the final authority.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201 -- Authored JSON protocol boundary.
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re

from . import reader_first_protocols
from .contracts import digest, need
from .wire_material import bind_authored_material

TOURNAMENT = {
    "concept_tournament_candidate": ("concept", "mechanism", "hook_question"),
    "concept_tournament_engine_kernel": (
        "protagonist_identity",
        "protagonist_private_desire",
        "protagonist_flaw",
        "core_abnormality",
        "current_goal",
        "effective_resistance",
        "failure_cost",
        "success_cost",
        "irreversible_change",
        "reader_promise",
        "scene_seeds",
        "post_reveal_scene_seeds",
        "deformable_loop",
        "expansion_axes",
        "story_architecture",
    ),
    "concept_tournament_raw_idea_pool": ("ideas",),
    "concept_tournament_raw_idea_rank": ("ranked",),
    # Judge axes vary with the actual native route/config; supplied JSON is
    # preserved, never completed with fabricated scores or passing axes.
    "concept_tournament_judge": (),
    "concept_tournament_story_judge": (),
    "concept_tournament_seriality": (
        "repeatable_story_unit",
        "core_promise_invariant",
        "unit_families",
        "progress_bar",
        "unit_frequency",
        "unit_count_estimate",
        "renewal_sources",
        "accumulation_tracks",
        "phase_transitions",
        "opposing_ecology",
        "endgame_direction",
    ),
    "concept_tournament_seriality_judge": (),
    "concept_tournament_finalist_judge": (),
    "concept_tournament_finalist_seriality_judge": (),
    "concept_tournament_premise_judge": ("verdicts",),
}
_FIDELITY = (
    "passed",
    "hard_conflicts",
    "compatible_extensions",
    "preserved_facts",
    "repair_directives",
)
CONCEPTION = {
    **dict.fromkeys(
        (
            "conception_preproject_materialized_seed_fidelity_judge",
            "conception_reconciled_seed_fidelity_judge",
            "conception_explicit_seed_fidelity_judge",
            "conception_final_material_seed_fidelity_judge",
        ),
        _FIDELITY,
    ),
    **dict.fromkeys(
        (
            "conception_preproject_materialized_seed_fidelity_arbitration",
            "conception_explicit_seed_fidelity_arbitration",
        ),
        (*_FIDELITY, "arbitration_reason"),
    ),
    "conception_explicit_seed_fidelity_repair": (
        "title",
        "premise",
        "synopsis",
        "writing_profile",
        "story_spine",
    ),
    "conception_final_packaging_seed_fidelity_judge": (
        "passed",
        "hard_conflicts",
        "repair_directives",
    ),
    "conception_final_packaging_seed_fidelity_arbitration": (
        "passed",
        "hard_conflicts",
        "repair_directives",
    ),
    "conception_final_packaging_canonical_quote_recovery": ("corrections", "unrecoverable"),
    "conception_final_packaging_seed_fidelity_repair": ("title_candidates", "synopsis"),
    "conception_final_packaging_seed_fidelity_repair_v2": (
        "title_candidates",
        "synopsis_candidates",
        "synopsis",
    ),
    "conception_final_packaging_focused_blurb_repair": ("synopsis",),
    "conception_final_packaging_focused_blurb_retry": ("synopsis",),
    "concept_tournament_winner_seed_repair": ("candidate",),
    "conception_concept_seed_final_premise_reconciliation": (
        "concept",
        "mechanism",
        "hook_question",
    ),
}
PROTOCOLS = {**TOURNAMENT, **CONCEPTION}
for _alias, _base in reader_first_protocols.TOURNAMENT_ALIASES.items():
    PROTOCOLS[_alias] = TOURNAMENT[_base]
for _name, _fields in CONCEPTION.items():
    PROTOCOLS[_name + "_repair"] = _fields
PACKAGING_BASES = frozenset(
    {
        "conception_final_packaging_seed_fidelity_judge",
        "conception_final_packaging_seed_fidelity_arbitration",
        "conception_final_packaging_canonical_quote_recovery",
    }
)


def protocol_for(template):
    if template in PROTOCOLS:
        return template, PROTOCOLS[template], None
    for base in PACKAGING_BASES:
        match = re.fullmatch(re.escape(base) + r"_candidate_v2_([0-9a-f]{20})(_repair)?", template)
        if match:
            return base, PROTOCOLS[base], match[1]
    need(False, "SCRIPT_TEMPLATE_UNCOVERED")


def recognizes(template):
    return template in PROTOCOLS or any(
        re.fullmatch(re.escape(base) + r"_candidate_v2_[0-9a-f]{20}(?:_repair)?", template)
        for base in PACKAGING_BASES
    )


def authored_payload(template, context):
    _base, required, subject_hash = protocol_for(template)
    if template in reader_first_protocols.TOURNAMENT_ALIASES:
        reader_first_protocols.validate_instruction(template, context)
    wire = context.wire_body
    need(
        isinstance(wire, Mapping) and context.expected_wire_sha256 == digest(wire),
        "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
    )
    if subject_hash is not None:
        subject = context.protocol_subject
        need(
            isinstance(subject, Mapping)
            and set(subject) == {"title", "synopsis"}
            and all(type(v) is str for v in subject.values()),
            "SCRIPT_PACKAGING_SUBJECT_REQUIRED",
        )
        actual = hashlib.sha256(
            json.dumps(dict(subject), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:20]
        need(subject_hash == actual, "SCRIPT_PACKAGING_SUBJECT_MISMATCH")
        strings = [m.get("content") for m in wire.get("messages", []) if isinstance(m, Mapping)]
        need(
            all(
                v and any(isinstance(text, str) and v in text for text in strings)
                for v in subject.values()
            ),
            "SCRIPT_PACKAGING_SUBJECT_NOT_ON_WIRE",
        )
    authored = context.submissions.get(template + ":" + str(context.call_index))
    need(isinstance(authored, Mapping) and bool(authored), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    payload, _receipt = bind_authored_material(
        authored, wire, expected_wire_sha256=context.expected_wire_sha256
    )
    need(set(required) <= set(payload), "SCRIPT_REPLY_FIELDS_REQUIRED")
    if "passed" in required:
        need(type(payload["passed"]) is bool, "SCRIPT_EXPLICIT_VERDICT_REQUIRED")
    return deepcopy(payload)


def protocol_inventory():
    return {
        "schema": "normal-quickstart-keyflow-contracts.v1",
        "protocols": {
            name: {
                "format": "json",
                "required_fields": list(fields),
                "source": "src/bestseller/services/concept_tournament.py"
                if name in TOURNAMENT or name in reader_first_protocols.TOURNAMENT_ALIASES
                else "src/bestseller/services/conception.py",
            }
            for name, fields in sorted(PROTOCOLS.items())
        },
        "packaging_subject_bases": sorted(PACKAGING_BASES),
        "explicit_authored_material_required": True,
        "verdicts_inferred": False,
    }
