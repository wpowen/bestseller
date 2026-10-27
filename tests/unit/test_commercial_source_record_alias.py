"""Record aliases retain native source authority; no runtime/report dependencies."""

# ruff: noqa: RUF001 -- Chinese source prose is intentional.

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import socket

import pytest

from bestseller.services.commercial_review_wire import (
    ADAPTER_VERSION,
    WIRE_VERSION,
    parse_commercial_wire,
)
from bestseller.services.commercial_source_evidence import assemble_commercial_sources

pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).parents[1] / "fixtures"
RECORD = json.loads((FIXTURES / "commercial_source_record_alias.json").read_text())
RECORD_ID = RECORD["record"]["id"]
ALIAS = "SRC:" + RECORD_ID
CATALOG_ID = RECORD["catalog_id"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure wire tests must never connect to a provider or database")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def source_input(*, records=None, extra=None, rule=False):
    return {
        "chapter_number": 4,
        "content_md": "主桌有人问这一盘的分量。\n\n后厨听见了催菜声。",
        "previous_chapters": [{"chapter_number": 3, "body": "杜强按桌号传菜。"}],
        "source": {
            **(extra or {}),
            "adopted_continuity": {
                "source_bound_canon": records
                if records is not None
                else [
                    *({"text": f"Fixture observation {i}"} for i in range(7)),
                    deepcopy(RECORD["record"]),
                ]
            },
            **(
                {
                    "approved_chapter_contract": {
                        "text": "使用第三人称。",
                        "hard": True,
                        "source": "user",
                    }
                }
                if rule
                else {}
            ),
        },
    }


def report(src, *, source_id=ALIAS, kind="prior_fact_conflict", severity="critical"):
    body_id = next(key for key, entry in src.catalog.items() if entry["kind"] == "current")
    issue = {
        "code": "SAME_EVENT_QUANTITY",
        "severity": severity,
        "assessment_kind": kind,
        "body_id": body_id,
        "reason": "核对同一盘扣肉的分量，不把后续进展当上限。",
        "required_fix": "只核对引用事件的表述。",
    }
    if kind != "narrative_quality":
        issue["source_id"] = source_id
    if kind in {"prior_fact_conflict", "current_fact_conflict"}:
        issue["temporal_relation"] = "same_event_or_state"
    result = {
        "wire_version": WIRE_VERSION,
        "pass": False,
        "overall_score": 0.61,
        "dimension_scores": {"readability": 0.61},
        "issues": [issue],
        "rewrite_plan": {"scope": "chapter", "change": ["核对引用事件"]},
    }
    rules = [key for key, entry in src.catalog.items() if entry["kind"] == "constraint"]
    if rules:
        result["checks"] = {
            key: {"result": "PASS", "body_id": body_id, "reason": "原条款适用。"} for key in rules
        }
    return result


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


@pytest.mark.parametrize(
    "severity,group",
    [
        ("critical", "blocking_issues"),
        ("high", "blocking_issues"),
        ("medium", "audit_issues"),
        ("low", "audit_issues"),
    ],
)
def test_actual_record_alias_is_lossless_and_keeps_native_observation_authority(severity, group):
    src = assemble_commercial_sources(**source_input())
    entry = src.catalog[CATALOG_ID]
    assert entry["source_path"] == "/adopted_continuity/source_bound_canon/7"
    assert entry["kind"] == "observation" and entry["text"] == RECORD["record"]["evidence"]
    canonical = report(src, source_id=CATALOG_ID, severity=severity)
    expected, old_receipt, errors = parse_commercial_wire(encode(canonical), src)
    assert not errors
    payload = report(src, severity=severity)
    raw = encode(payload)
    before = deepcopy(src)
    domain, receipt, errors = parse_commercial_wire(raw, src)
    assert errors == () and receipt["status"] == "verified"
    assert domain == expected and src == before and json.loads(raw) == payload
    assert domain[group][0]["source_citations"] == [
        {"id": CATALOG_ID, "kind": "observation", "quote": RECORD["record"]["evidence"]}
    ]
    audit = receipt.pop("derived_source_record_aliases")
    assert audit == [
        {
            "schema": "commercial-source-record-alias.v1",
            "path": "/issues/0/source_id",
            "original_id": ALIAS,
            "catalog_id": CATALOG_ID,
            "record_pointer": RECORD["record_pointer"],
            "raw_report_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "source_binding": dict(src.receipt),
        }
    ]
    old_receipt["raw_report_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    assert receipt == old_receipt
    assert ADAPTER_VERSION == "commercial-review-adapter.v2"


def test_only_manifested_structural_references_are_expanded_not_authored_objects():
    src = assemble_commercial_sources(
        **source_input(
            extra={
                "retained_record_copy": deepcopy(RECORD["record"]),
                "authored_marker": {"$ref": "literal-data-not-a-source"},
            }
        )
    )
    assert src.receipt["structural_aliases"]
    domain, receipt, errors = parse_commercial_wire(encode(report(src)), src)
    assert not errors and domain["blocking_issues"][0]["source_citations"][0]["id"] == CATALOG_ID
    assert len(receipt["derived_source_record_aliases"]) == 1


def test_multiple_aliases_preserve_every_issue_and_original_severity_order():
    src = assemble_commercial_sources(**source_input())
    payload = report(src)
    payload["issues"].extend([deepcopy(payload["issues"][0]), deepcopy(payload["issues"][0])])
    payload["issues"][1]["severity"] = "low"
    payload["issues"][2]["code"] = "SECOND_SERIOUS_FINDING"
    domain, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert not errors
    assert [issue["code"] for issue in domain["blocking_issues"]] == [
        "SAME_EVENT_QUANTITY",
        "SECOND_SERIOUS_FINDING",
    ]
    assert len(domain["audit_issues"]) == 1 and receipt["issues_dropped"] == 0
    assert [audit["path"] for audit in receipt["derived_source_record_aliases"]] == [
        "/issues/0/source_id",
        "/issues/1/source_id",
        "/issues/2/source_id",
    ]


@pytest.mark.parametrize("passed", [False, True])
@pytest.mark.parametrize("relation", ["same_event_or_state", "unexplained_transition"])
def test_alias_changes_only_the_issue_source_identifier(passed, relation):
    src = assemble_commercial_sources(**source_input())
    payload = report(src)
    payload.update(
        {
            "pass": passed,
            "overall_score": 0.93,
            "dimension_scores": {"readability": 0.91, "continuity": 0.11},
        }
    )
    payload["issues"][0].update(
        {
            "temporal_relation": relation,
            "reason": "Literal " + ALIAS,
            "required_fix": "Retain " + ALIAS,
        }
    )
    payload["rewrite_plan"]["change"] = [ALIAS]
    canonical = deepcopy(payload)
    canonical["issues"][0]["source_id"] = CATALOG_ID
    expected, _, errors = parse_commercial_wire(encode(canonical), src)
    assert not errors
    raw = "```json\n" + encode(payload).strip() + "\n```"
    before = deepcopy(payload)
    domain, receipt, errors = parse_commercial_wire(raw, src)
    assert not errors and domain == expected and payload == before
    assert domain["pass"] is passed and domain["overall_score"] == 0.93
    assert receipt["raw_report_sha256"] == hashlib.sha256(raw.encode()).hexdigest()


def test_one_missing_source_rejects_entire_report_without_applying_a_partial_alias():
    src = assemble_commercial_sources(**source_input())
    payload = report(src)
    payload["issues"].append({**payload["issues"][0], "source_id": "SRC:foreign"})
    returned, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert errors and returned == payload and receipt["status"] == "rejected"
    assert "derived_source_record_aliases" not in receipt


@pytest.mark.parametrize(
    "identifier",
    [
        RECORD_ID,
        "src:" + RECORD_ID,
        "RULE:" + RECORD_ID,
        "PREV3:" + RECORD_ID,
        "D" + RECORD_ID,
        "SRC:" + RECORD_ID.upper(),
        "SRC:{" + RECORD_ID + "}",
        "SRC:" + RECORD_ID.replace("-", ""),
        "SRC:" + RECORD_ID + " ",
        "SRC:00000000-0000-0000-0000-000000000000",
    ],
)
def test_wrong_namespace_noncanonical_or_absent_record_stays_rejected(identifier):
    src = assemble_commercial_sources(**source_input())
    payload = report(src, source_id=identifier)
    returned, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert errors and returned == payload and receipt["status"] == "rejected"
    assert "derived_source_record_aliases" not in receipt


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_record",
        "different_record_same_id",
        "foreign_key",
        "json_string",
        "nested_fake_record",
        "uncatalogued_metadata",
        "literal_reference",
    ],
)
def test_no_unique_real_catalog_record_means_no_alias(fault):
    record = deepcopy(RECORD["record"])
    extra = None
    if fault == "duplicate_record":
        records = [record, deepcopy(record)]
    elif fault == "different_record_same_id":
        records = [record, {**record, "evidence": "另一个事件。"}]
    elif fault == "foreign_key":
        records = [{**record, "id": "different", "source_chapter_id": RECORD_ID}]
    elif fault == "json_string":
        records = [json.dumps(record, ensure_ascii=False)]
    elif fault == "nested_fake_record":
        records = [{"text": "嵌套数据不是记录id。", "nested": record}]
    elif fault == "uncatalogued_metadata":
        records, extra = [], {"retained_metadata": record}
    else:
        records = [{"$ref": "#/source_context/retained_metadata"}]
        extra = {"retained_metadata": record}
    src = assemble_commercial_sources(**source_input(records=records, extra=extra))
    payload = report(src)
    returned, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert errors and receipt["status"] == "rejected" and returned == payload


@pytest.mark.parametrize(
    "kind", ["current_fact_conflict", "constraint_violation", "narrative_quality"]
)
def test_alias_cannot_change_allowed_kind_or_invent_rule_authority(kind):
    src = assemble_commercial_sources(**source_input(rule=kind == "constraint_violation"))
    payload = report(src, kind=kind)
    if kind == "narrative_quality":
        payload["issues"][0]["source_id"] = ALIAS
    returned, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert errors and returned == payload and receipt["status"] == "rejected"


@pytest.mark.parametrize("place", ["body", "check_body", "rule_key"])
def test_alias_is_never_applied_to_body_checks_or_rule_keys(place):
    src = assemble_commercial_sources(**source_input(rule=place != "body"))
    payload = report(src, source_id=CATALOG_ID)
    if place == "body":
        payload["issues"][0]["body_id"] = ALIAS
    elif place == "check_body":
        next(iter(payload["checks"].values()))["body_id"] = ALIAS
    else:
        payload["checks"][ALIAS] = payload["checks"].pop(next(iter(payload["checks"])))
    returned, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert errors and returned == payload and receipt["status"] == "rejected"


@pytest.mark.parametrize(
    "fault",
    [
        "text",
        "text_rebound",
        "catalog_text",
        "catalog_path",
        "catalog_kind",
        "rendered_path",
        "rendered_kind",
        "duplicate_catalog",
        "duplicate_json_key",
        "body_binding",
        "source_binding",
        "semantic_binding",
        "previous_binding",
        "protocol_binding",
        "missing_alias_manifest",
        "cyclic_alias",
    ],
)
def test_source_packet_catalog_or_binding_drift_rejects_alias(fault):
    src = assemble_commercial_sources(**source_input(extra={"copy": deepcopy(RECORD["record"])}))
    packet, catalog, binding = json.loads(src.text), deepcopy(src.catalog), deepcopy(src.receipt)
    text = src.text
    if fault in {"text", "text_rebound"}:
        packet["current_body"] += " Changed body."
    elif fault.startswith("catalog_"):
        field = {"catalog_text": "text", "catalog_path": "source_path", "catalog_kind": "kind"}[
            fault
        ]
        catalog[CATALOG_ID][field] = "foreign"
    elif fault.startswith("rendered_"):
        entry = next(e for e in packet["citation_catalog"] if e["id"] == CATALOG_ID)
        entry["source_ref" if fault == "rendered_path" else "kind"] = "foreign"
    elif fault == "duplicate_catalog":
        packet["citation_catalog"].append(deepcopy(packet["citation_catalog"][-1]))
    elif fault.endswith("binding"):
        key = {
            "body_binding": "body_sha256",
            "source_binding": "source_sha256",
            "semantic_binding": "semantic_source_sha256",
            "previous_binding": "previous_sha256",
            "protocol_binding": "source_protocol_sha256",
        }[fault]
        binding[key] = "0" * 64
    elif fault == "missing_alias_manifest":
        binding["structural_aliases"] = []
    elif fault == "cyclic_alias":
        binding["structural_aliases"][0]["target"] = binding["structural_aliases"][0]["path"]
    if fault in {"text", "text_rebound", "rendered_path", "rendered_kind", "duplicate_catalog"}:
        text = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if fault != "text":
            binding["assembled_source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            binding["assembled_characters"] = len(text)
    if fault == "duplicate_json_key":
        text = text[:-1] + ',"current_body":"foreign"}'
        binding["assembled_source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    src = replace(src, text=text, catalog=catalog, receipt=binding)
    payload = report(src)
    returned, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert errors and returned == payload and receipt["status"] == "rejected"


@pytest.mark.parametrize("fault", ["score", "pass", "reason", "temporal_relation", "extra_field"])
def test_valid_alias_never_repairs_an_invalid_verdict_or_drops_other_errors(fault):
    src = assemble_commercial_sources(**source_input())
    payload = report(src)
    if fault == "score":
        payload["overall_score"] = True
    elif fault == "pass":
        payload["pass"] = "true"
    elif fault == "reason":
        payload["issues"][0]["reason"] = ""
    elif fault == "temporal_relation":
        payload["issues"][0]["temporal_relation"] = "later_progression"
    else:
        payload["issues"][0]["approved"] = True
    returned, receipt, errors = parse_commercial_wire(encode(payload), src)
    assert errors and returned == payload and receipt["status"] == "rejected"
    assert "derived_source_record_aliases" not in receipt


@pytest.mark.parametrize("where", ["catalog", "rendered", "both"])
def test_source_binding_never_equates_boolean_offsets_with_integer_zero(where):
    src = assemble_commercial_sources(**source_input())
    packet, catalog, binding = json.loads(src.text), deepcopy(src.catalog), deepcopy(src.receipt)
    current = next(key for key, entry in catalog.items() if entry["kind"] == "current")
    assert catalog[current]["start"] == 0
    if where in {"catalog", "both"}:
        catalog[current]["start"] = False
    if where in {"rendered", "both"}:
        next(entry for entry in packet["citation_catalog"] if entry["id"] == current)["start"] = (
            False
        )
    text = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    binding.update(
        assembled_source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        assembled_characters=len(text),
        rendered_catalog_characters=len(
            json.dumps(
                packet["citation_catalog"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        section_characters={
            key: len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            for key, value in packet.items()
        },
    )
    payload = report(src)
    returned, receipt, errors = parse_commercial_wire(
        encode(payload),
        replace(
            src,
            text=text,
            catalog=catalog,
            receipt=binding,
        ),
    )
    assert errors and returned == payload and receipt["status"] == "rejected"


def test_canonical_inputs_match_prechange_golden_results_field_for_field():
    vectors = json.loads((FIXTURES / "commercial_source_record_alias_canonical.json").read_text())
    assert len(vectors) >= 8
    for case in vectors:
        src = assemble_commercial_sources(**case["source_input"])
        domain, receipt, errors = parse_commercial_wire(case["raw"], src)
        assert [domain, receipt, list(errors)] == case["expected"], case["name"]
        assert "derived_source_record_aliases" not in receipt
