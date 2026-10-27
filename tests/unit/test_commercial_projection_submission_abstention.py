"""Offline abstention proof checks, including optional retained v27 physical replay."""

# ruff: noqa: RUF001

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_commercial_action_projection as fixtures

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_judge_recovery import bind_commercial_slot_result
from bestseller.services.commercial_review_tool import submitted_review_payload
from bestseller.services.commercial_review_wire import parse_commercial_wire
from bestseller.services.commercial_source_evidence import (
    CommercialSources,
    commercial_judge_unavailable,
    resolve_commercial_source_pointer,
)
from bestseller.services.write_gate import GateConfig

pytestmark = pytest.mark.unit
stable = fixtures.stable


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def missing_slot(src, *, calls=None, valid_wire=False):
    retained = fixtures.slot(src, 2)
    envelope = json.loads(retained.raw_excerpt)
    raw = envelope["raw_report"] if valid_wire else "No structured review was submitted."
    completion = SimpleNamespace(tool_calls=calls, content=raw)
    raw, submission, submission_errors = submitted_review_payload(completion)
    parsed, adapter, wire_errors = parse_commercial_wire(raw, src)
    adapter.update(wire_validation_status=adapter["status"], status="rejected_submission")
    adapter["submission"] = submission
    try:
        result = quality_judge_result_from_mapping(
            parsed,
            scope="chapter",
            min_overall=0.82,
            min_dimensions={"readability": 0.8},
            llm_run_id=retained.llm_run_id,
        )
    except (ValueError, TypeError):
        result = LLMQualityJudgeResult(llm_run_id=retained.llm_run_id)
    errors = (*submission_errors, *wire_errors)
    result = commercial_judge_unavailable(
        diagnostic="commercial source evidence unverified: " + "; ".join(errors), result=result
    )
    return bind_commercial_slot_result(
        result,
        raw_report=raw,
        validation_errors=errors,
        source_receipt=src.receipt,
        sample_slot="chapter-quality.independent-v2:2",
        attempt=2,
        adapter_receipt=adapter,
        evaluation_contract=envelope["evaluation_contract"],
    )


@pytest.mark.parametrize("calls", [None, []], ids=["null", "empty-list"])
@pytest.mark.parametrize("valid_wire", [False, True], ids=["invalid-json", "content-only-json"])
async def test_majority_preserves_invalid_submission_as_abstention_without_regrading(
    stable, calls, valid_wire
):
    src = fixtures.sources()
    unavailable = missing_slot(src, calls=calls, valid_wire=valid_wire)
    original = await stable([fixtures.slot(src, 0), fixtures.slot(src, 1), unavailable])
    before = deepcopy(original.model_dump(mode="json"))
    source_before = deepcopy(src)
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=GateConfig({}), chapter_number=2
    )
    assert proof["status"] == "projected" and view is not None
    assert view["overall_score"] == original.overall_score == 0.91
    assert view["dimension_scores"] == original.dimension_scores
    assert view["pass"] is original.passed
    assert [s["status"] for s in proof["slots"]] == ["verified", "verified", "unknown"]
    abstention = proof["slots"][2]
    assert abstention["abstains"] and abstention["domain_issues_contributed"] == 0
    assert abstention["original_report"] == unavailable.model_dump(mode="json")
    assert abstention["original_report"]["raw_excerpt"] == unavailable.raw_excerpt
    assert unavailable.infrastructure_failure
    assert not any(i["code"] == "CHAPTER_JUDGE_UNAVAILABLE" for i in view["blocking_issues"])
    assert not proof["raw_cohort_modified"] and not proof["scores_modified"]
    assert original.model_dump(mode="json") == before and src == source_before


@pytest.mark.parametrize(
    "fault",
    [
        "combined_missing_submission",
        "combined_missing_wire",
        "combined_extra",
        "combined_reordered",
        "submission_missing",
        "physical_hash_missing",
        "physical_hash_forged",
        "paid_run_missing",
        "calls_one",
        "calls_bool",
        "executed",
        "policy",
        "submission_extra",
        "status",
        "wire_status",
        "adapter_extra",
        "raw_hash",
        "source_binding",
        "adapter_source",
        "audit",
        "typed_grade",
        "typed_rewrite",
        "attempt",
    ],
)
async def test_two_valid_votes_never_waive_forged_or_missing_unknown_proof(stable, fault):
    src = fixtures.sources()
    result = missing_slot(src)
    report = result.model_dump(mode="json")
    e = json.loads(report["raw_excerpt"])
    adapter = e["adapter_receipt"]
    submission = adapter["submission"]
    if fault == "combined_missing_submission":
        e["validation_errors"].pop(0)
    elif fault == "combined_missing_wire":
        e["validation_errors"].pop()
    elif fault == "combined_extra":
        e["validation_errors"].append("invented")
    elif fault == "combined_reordered":
        e["validation_errors"].reverse()
    elif fault == "submission_missing":
        adapter.pop("submission")
    elif fault == "physical_hash_missing":
        submission.pop("tool_calls_sha256")
    elif fault == "physical_hash_forged":
        submission["tool_calls_sha256"] = "a" * 64
    elif fault == "paid_run_missing":
        report["llm_run_id"] = None
    elif fault == "calls_one":
        submission["calls_received"] = 1
    elif fault == "calls_bool":
        submission["calls_received"] = False
    elif fault == "executed":
        submission["tools_executed"] = 1
    elif fault == "policy":
        submission["policy"] = "unverified"
    elif fault == "submission_extra":
        submission["extra"] = True
    elif fault == "status":
        submission["status"] = "received"
    elif fault == "wire_status":
        adapter["wire_validation_status"] = "verified"
    elif fault == "adapter_extra":
        adapter["ignored_error"] = "forged"
    elif fault == "raw_hash":
        e["raw_report"] += " changed"
    elif fault == "source_binding":
        e["source_binding"]["body_sha256"] = "a" * 64
    elif fault == "adapter_source":
        adapter["source_binding"]["previous_sha256"] = "a" * 64
    elif fault == "audit":
        e["exact_source_audit"]["assembled_source_sha256"] = "a" * 64
    elif fault == "typed_grade":
        report["overall_score"] = 0.99
    elif fault == "typed_rewrite":
        report["rewrite_plan"]["scope"] = "chapter"
    elif fault == "attempt":
        e["attempt"] = 3
    report["raw_excerpt"] = json.dumps(e)
    bad = LLMQualityJudgeResult.model_validate(report)
    original = await stable([fixtures.slot(src, 0), fixtures.slot(src, 1), bad])
    before = original.model_dump(mode="json")
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=GateConfig({}), chapter_number=2
    )
    assert view is None and proof["status"] in {"blocked", "unsupported"}
    assert original.model_dump(mode="json") == before


async def test_abstention_cannot_supply_quorum_or_accept_another_source(stable):
    src = fixtures.sources()
    original = await stable(
        [fixtures.slot(src, 0), fixtures.slot(src, 1, unknown=True), missing_slot(src)]
    )
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=GateConfig({}), chapter_number=2
    )
    assert view is None and proof["reason"] == "insufficient_verified_quorum"
    original = await stable([fixtures.slot(src, 0), fixtures.slot(src, 1), missing_slot(src)])
    view, proof = project_commercial_actions(
        original,
        sources=replace(src, text=src.text + " "),
        gate_config=GateConfig({}),
        chapter_number=2,
    )
    assert view is None and proof["reason"] == "sources_changed"


def test_actual_v27_five_physical_returns_and_original_cohort_replay_unchanged(monkeypatch):
    from bestseller import settings as settings_module
    from bestseller.services import llm

    def forbidden(*args, **kwargs):
        pytest.fail("Physical replay must never dispatch a model")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    root = Path(__file__).resolve().parents[2]
    diagnostic = (
        root
        / "reports/continuous-recovery-2026-10-16"
        / "20261017T154234685690Z-v27-current-review-unknown-diagnostic.json"
    )
    if not diagnostic.exists():
        pytest.skip("Optional retained local v27 evidence absent")
    data = json.loads(diagnostic.read_bytes())
    # Resolve only the configured local artifact directory; never load current story sources.
    artifact_dir = Path(settings_module.load_settings().artifact_store.local_dir)
    if not artifact_dir.is_absolute():
        artifact_dir = root / artifact_dir
    files = {diagnostic: sha(diagnostic.read_text())}
    physical = {}
    for row in data["runs"]:
        if row["prompt_template"] != "chapter_commercial_quality_judge":
            continue
        pair = [artifact_dir / row[key] for key in ("request_payload_ref", "response_payload_ref")]
        for path in pair:
            files[path] = sha(path.read_text())
        request, response = [json.loads(path.read_bytes()) for path in pair]
        user = request["messages"]["user"]
        assert not user["truncated"] and len(user["text"]) == user["original_chars"]
        marker = "完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n"
        offset = user["text"].index(marker) + len(marker)
        payload, end = json.JSONDecoder().raw_decode(user["text"][offset:])
        text = user["text"][offset : offset + end]
        retained_source = row["metadata_json"]["commercial_source_input_receipt"]
        assert sha(text) == retained_source["assembled_source_sha256"]
        assert payload["current_body"] == data["draft_content"]
        assert sha(payload["current_body"]) == retained_source["body_sha256"]
        catalog = {}
        for entry in payload["citation_catalog"]:
            resolved = resolve_commercial_source_pointer(payload, entry["source_ref"])
            if entry["kind"] in {"current", "prior"}:
                quote = resolved[entry["start"] : entry["end"]]
            elif entry["kind"] == "observation":
                quote = next(
                    (
                        resolved[k]
                        for k in ("text", "evidence", "value")
                        if isinstance(resolved, dict)
                        and isinstance(resolved.get(k), str)
                        and resolved[k].strip()
                    ),
                    encoded(resolved) if isinstance(resolved, dict) else str(resolved),
                )
            else:
                assert entry["kind"] == "constraint"
                quote = encoded(resolved)
            assert entry["locator_hint"] == quote[:32]
            catalog[entry["id"]] = {**entry, "text": quote}
        src = CommercialSources(text=text, catalog=catalog, receipt=retained_source)
        assert len(catalog) == retained_source["catalog_entries"]
        assert not response["content"]["truncated"]
        assert response["raw_message"].get("tool_calls") == response["tool_calls"]
        completion = SimpleNamespace(
            tool_calls=response["tool_calls"], content=response["content"]["text"]
        )
        raw, submission, submission_errors = submitted_review_payload(completion)
        _, _, wire_errors = parse_commercial_wire(raw, src)
        physical[row["id"]] = (raw, submission, [*submission_errors, *wire_errors], src)
    assert len(physical) == 5
    assert sorted(len(p[2]) for p in physical.values()) == [0, 0, 1, 2, 2]
    original = data["report"]["evidence_summary"]["llm_commercial_judge_original"]
    before = deepcopy(original)
    cohort = json.loads(original["raw_excerpt"])
    assert cohort["valid_samples"] == cohort["required_valid_samples"] == 2
    for sample in cohort["samples"]:
        raw, submission, errors, src = physical[sample["report"]["llm_run_id"]]
        envelope = json.loads(sample["report"]["raw_excerpt"])
        assert envelope["raw_report"] == raw
        assert envelope["validation_errors"] == errors
        assert envelope["adapter_receipt"]["submission"] == submission
    view, proof = project_commercial_actions(
        original, sources=src, gate_config=GateConfig({}), chapter_number=2
    )
    assert proof["status"] == "projected" and view is not None
    assert view["overall_score"] == original["overall_score"] == cohort["median_overall"] == 0.7
    assert view["dimension_scores"] == original["dimension_scores"] == cohort["median_dimensions"]
    assert view["pass"] is original["pass"] is False
    assert [s["status"] for s in proof["slots"]] == ["verified", "verified", "unknown"]
    assert proof["slots"][2]["original_report"] == cohort["samples"][2]["report"]
    assert original == before
    assert (
        data["report"]["evidence_summary"]["commercial_action_projection"]["reason"]
        == "submission_unverified"
    )
    assert all(sha(path.read_text()) == digest for path, digest in files.items())
    assert len(files) == 11
