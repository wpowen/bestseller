"""Two literal reader-revision codecs for explicit private scripted answers.

The complete native request and metadata are pinned before any wire selection.
Native pure answer parsers check Unicode spans, hashes and exact field sets.
No body generation, inferred opinion, paid verification, DB write or receipt
creation occurs. An authored false judgment remains a valid false answer.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202 -- Explicit runtime JSON boundary.
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import re
from uuid import UUID

from . import reader_first_protocols as reader
from .contracts import canonical, digest, need
from .wire_material import bind_authored_material

FREEZE = "reader_revision_anchors"
VERIFY = "reader_revision_preservation"
PROTOCOLS = {FREEZE: "1.0", VERIFY: "1.0"}
SCHEMAS = {FREEZE: "reader-revision-anchors.v1", VERIFY: "reader-revision-preservation.v1"}
_ERRORS = {
    FREEZE: "SCRIPT_READER_REVISION_ANCHORS_INVALID",
    VERIFY: "SCRIPT_READER_REVISION_PRESERVATION_INVALID",
}
_SOURCE_BINDING = frozenset(
    {"project_id", "chapter_id", "source_draft_id", "source_body_sha256", "reader_first_binding"}
)
_CANDIDATE_BINDING = frozenset(
    {
        "candidate_llm_run_id",
        "candidate_body_sha256",
        "raw_body_sha256",
        "anchors_sha256",
        "derivation",
        "candidate_physical",
    }
)
_ANCHOR_FIELDS = frozenset({"anchor_id", "start", "end", "quote", "kind", "narrative_function"})
_RESULT_FIELDS = frozenset({"anchor_id", "preserved", "evidence", "reason"})
_REPLY_FIELDS = {
    FREEZE: frozenset({"schema", "source_body_sha256", "anchors"}),
    VERIFY: frozenset(
        {
            "schema",
            "source_body_sha256",
            "candidate_body_sha256",
            "anchors",
            "voice_preserved",
            "no_system_procedure_intrusion",
        }
    ),
}


def _native():
    from bestseller.services import reader_judge

    need(
        reader_judge._RR_ANCHORS == FREEZE
        and reader_judge._RR_VERIFICATIONS == VERIFY
        and reader_judge._RR_ANCHOR_SCHEMA == SCHEMAS[FREEZE]
        and reader_judge._RR_VERIFY_SCHEMA == SCHEMAS[VERIFY]
        and reader_judge._RR_KINDS
        == {"choice", "dialogue", "action", "relationship", "reveal", "consequence"},
        "SCRIPT_READER_REVISION_PROTOCOL_DRIFT",
    )
    return reader_judge


def _body_hash(text, code):
    need(type(text) is str and bool(text.strip()), code)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decode_revision_wire(template, context):
    """Decode the full native packet, bound to coordinator-supplied metadata.

    Derivation/physical metadata is compared losslessly, never interpreted as
    permission or replayed. Native workflow validators retain those decisions.
    """
    need(template in PROTOCOLS, "SCRIPT_TEMPLATE_UNCOVERED")
    _wire, messages = reader._wire(context)
    code, binding_code = (
        "SCRIPT_READER_REVISION_WIRE_INVALID",
        "SCRIPT_READER_REVISION_BINDING_MISMATCH",
    )
    subject = context.protocol_subject
    need(isinstance(subject, Mapping), binding_code)
    need(subject.get("logical_role") == "critic", "SCRIPT_READER_REVISION_ROLE_MISMATCH")
    need(
        subject.get("prompt_version") == PROTOCOLS[template],
        "SCRIPT_READER_REVISION_VERSION_MISMATCH",
    )
    marker = reader._expected_binding(context)
    expected = subject.get("reader_revision_binding")
    fields = _SOURCE_BINDING | (_CANDIDATE_BINDING if template == VERIFY else frozenset())
    need(isinstance(expected, Mapping) and set(expected) == fields, binding_code)
    need(expected["reader_first_binding"] == marker, binding_code)
    users = [message.get("content") for message in messages if message.get("role") == "user"]
    need(len(users) == 1 and type(users[0]) is str, code)
    try:
        document = reader._decoder().decode(users[0])
    except (ValueError, TypeError, RecursionError):
        need(False, code)
    packet_fields = fields | {"schema", "source_text"}
    if template == VERIFY:
        packet_fields |= {"candidate_text", "anchors"}
    need(
        type(document) is dict
        and set(document) == packet_fields
        and document.get("schema") == SCHEMAS[template] + ".request",
        code,
    )
    observed = {field: document[field] for field in fields}
    need(canonical(observed) == canonical(expected), binding_code)
    ids = {"project_id", "chapter_id", "source_draft_id"}
    hashes = {"source_body_sha256"}
    if template == VERIFY:
        ids.add("candidate_llm_run_id")
        hashes |= {"candidate_body_sha256", "raw_body_sha256", "anchors_sha256"}
        need(
            type(document["derivation"]) is dict
            and bool(document["derivation"])
            and type(document["candidate_physical"]) is dict
            and bool(document["candidate_physical"]),
            code,
        )
    try:
        for key in ids:
            value = document[key]
            need(type(value) is str and str(UUID(value)) == value, code)
    except (ValueError, TypeError, AttributeError):
        need(False, code)
    need(
        all(
            type(document[key]) is str and re.fullmatch(r"[0-9a-f]{64}", document[key])
            for key in hashes
        ),
        code,
    )
    need(document["source_body_sha256"] == _body_hash(document["source_text"], code), code)
    if template == VERIFY:
        need(
            document["candidate_body_sha256"] == _body_hash(document["candidate_text"], code)
            and document["anchors_sha256"] == digest(document["anchors"]),
            code,
        )
        # This parser envelope checks the supplied anchors only; it is never
        # emitted as a generated answer or a receipt.
        try:
            _native()._rr_anchor_answer(
                canonical(
                    {
                        "schema": SCHEMAS[FREEZE],
                        "source_body_sha256": document["source_body_sha256"],
                        "anchors": document["anchors"],
                    }
                ),
                document["source_text"],
            )
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            need(False, code)
    return deepcopy(document)


def authored_submission(template, context):
    """Return only explicitly supplied raw fields after native pure validation."""
    document = decode_revision_wire(template, context)
    material = context.submissions.get(template + ":" + str(context.call_index))
    need(isinstance(material, Mapping) and bool(material), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    code = _ERRORS[template]
    need(set(material) == _REPLY_FIELDS[template], code)
    need(len(canonical(material).encode()) <= 512_000, "SCRIPT_RESPONSE_UNBOUNDED")
    rows = material["anchors"]
    need(type(rows) is list and 1 <= len(rows) <= 8, code)
    if template == VERIFY:
        for key in ("voice_preserved", "no_system_procedure_intrusion"):
            need(not reader._has_binding(material[key]), "SCRIPT_VERDICT_MUST_BE_AUTHORED")
    for row in rows:
        fields = _ANCHOR_FIELDS if template == FREEZE else _RESULT_FIELDS
        need(isinstance(row, Mapping) and set(row) == fields, code)
        opinions = ("kind", "narrative_function") if template == FREEZE else ("preserved", "reason")
        for key in opinions:
            need(not reader._has_binding(row[key]), "SCRIPT_VERDICT_MUST_BE_AUTHORED")
        if template == VERIFY:
            evidence = row["evidence"]
            need(
                type(evidence) is list
                and len(evidence) <= 8
                and all(
                    isinstance(item, Mapping) and set(item) == {"start", "end", "quote"}
                    for item in evidence
                ),
                code,
            )
    payload, _bindings = bind_authored_material(
        material, context.wire_body, expected_wire_sha256=context.expected_wire_sha256
    )
    try:
        if template == FREEZE:
            checked = _native()._rr_anchor_answer(canonical(payload), document["source_text"])
        else:
            checked = _native()._rr_verification_answer(
                canonical(payload),
                document["source_text"],
                document["candidate_text"],
                document["anchors"],
            )
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        need(False, code)
    need(canonical(checked) == canonical(payload), code)
    return deepcopy(payload)
