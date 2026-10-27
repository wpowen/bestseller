"""Full outline-review entry, real lossless preflight, inert completions only."""

# ruff: noqa: RUF001 -- Exact legacy protocol strings.

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest
from test_outline_source_brief_full_authority import _brief, _case

from bestseller.services import outline_llm_judge as judge
from bestseller.services import source_bound_planning_content as capacity
from bestseller.services.llm import LLMCompletionRequest, LLMCompletionResult, _effective_input_hash
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _outline(large=True):
    repeated = "Each spectral observation retains calibration, location and uncertainty. " * 120
    scenes = [
        {
            "scene_number": n,
            "purpose": {"story": repeated},
            "action_sequence": [repeated],
            "unknown_story": {"full": repeated, "literal": {"$ref": "/authored/source"}},
        }
        for n in range(1, 13 if large else 2)
    ]
    return {
        "batch_name": "Independent tide station",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "The plate",
                "goal": "Calibrate the plate.",
                "scenes": scenes,
            }
        ],
        "_meta": {"archived_note": repeated, "self_claim": "PRODUCER_DATA_NOT_AUTHORITY"},
        "late_unknown": {"complete": "LAST_UNCUT_OUTLINE_FIELD", "types": [False, 1, 1.0, None]},
    }


def _initial():
    long = "Retain the exact observation and all its original conditions. " * 8
    issues = [
        {
            "code": "FIXTURE_SOURCE_QUESTION",
            "severity": "critical",
            "evidence": "Fixture question, not a production grade.",
            "required_fix": long * 4 + "LAST_REQUIRED_FIX",
            "change": [f"Change {n}: " + long for n in range(24)],
            "preserve": [f"Preserve {n}: " + long for n in range(24)],
        },
        {
            "code": "FIXTURE_SECOND_QUESTION",
            "severity": "critical",
            "evidence": "A second fixture question.",
            "required_fix": "Keep its independent scope.",
        },
    ]
    return {
        "pass": False,
        "overall_score": 0.41,
        "dimension_scores": {},
        "blocking_issues": issues,
        "rewrite_plan": {
            "scope": "outline",
            "change": ["Fixture-only change"],
            "preserve": ["Fixture-only preservation"],
        },
    }


def _data(request):
    block = request.user_prompt.split("[COMPLETE REVIEW DATA]\n", 1)[1].split(
        "\n[/COMPLETE REVIEW DATA]", 1
    )[0]
    if "source_review_data=" in block:
        encoded_text = block.split("source_review_data=", 1)[1]
        encoded = json.loads(encoded_text)
        return decode_lossless_source(encoded)
    return json.loads(block)


def _spy_capacity(monkeypatch):
    checks = []
    real = capacity.assert_source_bound_planning_prompt_capacity

    def checked(system, user):
        receipt = real(system, user)
        checks.append((system, user, receipt))
        return receipt

    monkeypatch.setattr(capacity, "assert_source_bound_planning_prompt_capacity", checked)
    return checks


async def _run(monkeypatch, brief, outline, *, initial=None, checks=None, requests=None):
    requests = [] if requests is None else requests
    initial = _initial() if initial is None else initial

    async def completion(_session, _settings, request):
        if checks is not None:
            assert checks and checks[-1][:2] == (request.system_prompt, request.user_prompt)
            assert checks[-1][2]["truncated"] is False
        requests.append(request)
        if request.prompt_template == "outline_commercial_judge":
            payload = initial
        else:
            assert request.prompt_template == "outline_commercial_judge_consistency_adjudication"
            payload = {
                "adjudication_complete": True,
                "decisions": [
                    {
                        "index": n,
                        "decision": "CONFIRM",
                        "reason": "Inert fixture only.",
                        "required_fix": item["required_fix"],
                    }
                    for n, item in enumerate(initial["blocking_issues"], 1)
                ],
            }
        return LLMCompletionResult(
            content="```json\n" + json.dumps(payload) + "\n```",
            provider="mock",
            model_name="fixture",
            llm_run_id=uuid4(),
            finish_reason="stop",
        )

    monkeypatch.setattr(judge, "complete_text", completion)
    result = await judge.judge_outline_commercial_readiness(
        None,
        load_settings(),
        project_brief=brief,
        outline_payload=outline,
        workflow_run_id=uuid4(),
        project_id=uuid4(),
    )
    return requests, result


@pytest.mark.parametrize("language", ["zh-CN", "en"])
async def test_full_entry_both_requests_keep_late_outline_all_proposals_and_source_roles(
    monkeypatch, language
):
    project, world = _case(language)
    brief = _brief(project, world)
    brief["prefix_evaluation_scope"] = {"approved_scope_only": [1], "full_window": [1, 8]}
    brief["deterministic_semantic_candidates"] = [
        {"code": "FIXTURE_DIAGNOSTIC_ONLY", "evidence": "LAST_UNCUT_OUTLINE_FIELD"}
    ]
    outline, initial = _outline(), _initial()
    before = deepcopy((brief, outline, initial))
    raw = json.dumps(outline, ensure_ascii=False, indent=2)
    assert raw.index("LAST_UNCUT_OUTLINE_FIELD") > 90000
    checks = _spy_capacity(monkeypatch)
    requests, result = await _run(monkeypatch, brief, outline, initial=initial, checks=checks)
    assert len(requests) == 2 and not result.passed and result.overall_score == 0.41
    assert requests[0].project_id == requests[1].project_id
    assert requests[0].workflow_run_id == requests[1].workflow_run_id
    data = [_data(r) for r in requests]
    for request, actual in zip(requests, data, strict=True):
        assert request.prompt_version == "v3-lossless-source-authority"
        actual_brief = deepcopy(actual["project_brief"])
        actual_brief.pop("diagnostic_evidence")
        assert actual_brief == before[0]
        assert actual["outline_payload"] == before[1]
        assert json.dumps(actual["outline_payload"], sort_keys=True) == json.dumps(
            before[1], sort_keys=True
        )
        assert actual["project_brief"]["book_design_snapshot"] == brief["book_design_snapshot"]
        assert (
            actual["project_brief"]["approved_world_authority"]["full_story_source"][
                "history_key_events"
            ]
            == world["history_key_events"]
        )
        assert actual["project_brief"]["reader_contract"] == project.reader_contract_json
        assert (
            actual["project_brief"]["approved_creation_contracts"]
            == brief["approved_creation_contracts"]
        )
        assert actual["project_brief"]["prefix_evaluation_scope"]["approved_scope_only"] == [1]
        assert actual["project_brief"]["diagnostic_evidence"]["diagnostic_only"] is True
        assert (
            actual["source_roles"]["canonical_author"]
            == "/project_brief/approved_creation_contracts"
        )
        assert (
            actual["source_roles"]["candidate_producer_provenance_not_story"]
            == "/outline_payload/_meta"
        )
        assert (
            "/project_brief/reader_contract"
            in actual["source_roles"]["advisory_editorial_not_canon"]
        )
        assert (
            actual["source_roles"]["untrusted_diagnostic_only"]
            == "/project_brief/diagnostic_evidence"
        )
        assert "Candidate _meta is producer provenance" in actual["interpretation"]
        assert request.metadata["review_data_sha256"] == judge.compute_judge_input_hash(actual)
        transport = request.metadata["source_transport"]
        assert transport["roundtrip_verified"] is True and transport["encoding"] != "identity"
        canonical_data = json.dumps(
            actual, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        assert (
            transport["data_receipt"]["original_sha256"]
            == hashlib.sha256(canonical_data.encode()).hexdigest()
        )
        assert (
            transport["rendered_prompt_sha256"]
            == hashlib.sha256(request.user_prompt.encode()).hexdigest()
        )
        assert request.metadata["full_outline_fields_preserved"] is True
        assert "TRUNCATED_AFTER_90000" not in request.user_prompt
    secondary_only = data[1].pop("untrusted_initial_blocking_issues")
    expected = [{"index": n, **item} for n, item in enumerate(before[2]["blocking_issues"], 1)]
    assert secondary_only == expected
    assert len(secondary_only[0]["change"]) == len(secondary_only[0]["preserve"]) == 24
    assert secondary_only[0]["required_fix"].endswith("LAST_REQUIRED_FIX")
    assert data[0] == data[1]
    context_hash = judge.compute_judge_input_hash(data[0])
    assert all(r.metadata["review_context_sha256"] == context_hash for r in requests)
    assert (brief, outline, initial) == before


async def test_uncompressible_outline_never_reaches_initial_completion(monkeypatch):
    project, world = _case()
    outline = _outline(False)
    outline["late_unknown"]["complete"] = "".join(
        hashlib.sha256(str(n).encode()).hexdigest() for n in range(2400)
    )
    sent = []

    async def forbidden(*args, **kwargs):
        sent.append(True)
        pytest.fail("A whole-request capacity rejection must precede completion")

    monkeypatch.setattr(judge, "complete_text", forbidden)
    with pytest.raises(capacity.SourceBoundPlanningCapacityError):
        await judge.judge_outline_commercial_readiness(
            None,
            load_settings(),
            project_brief=_brief(project, world),
            outline_payload=outline,
            workflow_run_id=uuid4(),
            project_id=uuid4(),
        )
    assert sent == []


async def test_irreducible_system_remainder_fails_before_initial_completion(monkeypatch):
    project, world = _case()
    monkeypatch.setattr(
        judge,
        "_render_outline_commercial_system_prompt",
        lambda **kwargs: "R" * (capacity.MAX_PROMPT_CHARS + 1),
    )
    sent = []

    async def forbidden(*args, **kwargs):
        sent.append(True)
        pytest.fail("Compressed source cannot bypass an oversized system")

    monkeypatch.setattr(judge, "complete_text", forbidden)
    with pytest.raises(capacity.SourceBoundPlanningCapacityError):
        await judge.judge_outline_commercial_readiness(
            None,
            load_settings(),
            project_brief=_brief(project, world),
            outline_payload=_outline(False),
            workflow_run_id=uuid4(),
            project_id=uuid4(),
        )
    assert sent == []


async def test_character_fitting_incompressible_source_output_reserve_rejects_before_send(
    monkeypatch,
):
    # One opaque scalar cannot be deduplicated internally by the existing codec.
    opaque = "".join(
        chr(0x4E00 + int(digest[offset : offset + 4], 16) % 0x5000)
        for n in range(4750)
        for digest in [hashlib.sha256(str(n).encode()).hexdigest()]
        for offset in range(0, 64, 4)
    )
    outline = {
        "chapters": [{"chapter_number": 1, "title": "An opaque plate"}],
        "late_unknown": opaque,
    }
    brief = {
        "language": "en",
        "genre": "science-fiction",
        "book_design_source_proof": {"source_payload_sha256": "a" * 64},
    }
    before = deepcopy((outline, brief))
    capacity.assert_source_bound_planning_prompt_capacity(
        "", json.dumps(outline, ensure_ascii=False)
    )
    sent = []

    async def forbidden(*args, **kwargs):
        sent.append(True)
        pytest.fail("Character fit cannot bypass the physical output reservation")

    monkeypatch.setattr(judge, "complete_text", forbidden)
    with pytest.raises(capacity.SourceBoundPlanningCapacityError) as error:
        await judge.judge_outline_commercial_readiness(
            None,
            load_settings(),
            project_brief=brief,
            outline_payload=outline,
            workflow_run_id=uuid4(),
            project_id=uuid4(),
        )
    receipt = error.value.receipt
    assert receipt["combined_chars"] < receipt["max_combined_chars"]
    assert receipt["estimated_tokens"] <= receipt["max_estimated_tokens"]
    assert receipt["reserved_input_tokens"] <= receipt["total_budget"]
    assert receipt["output_reserve_tokens"] == 8192
    assert (
        receipt["reserved_input_tokens"] + receipt["output_reserve_tokens"]
        > receipt["total_budget"]
    )
    assert receipt["budget_passed"] is False and receipt["truncated"] is False
    assert sent == [] and (outline, brief) == before


async def test_secondary_oversized_issue_raises_capacity_not_content_grade_without_redispatch(
    monkeypatch,
):
    project, world = _case()
    brief, outline, initial = _brief(project, world), _outline(False), _initial()
    initial["blocking_issues"][0]["required_fix"] = "".join(
        hashlib.sha256(str(n).encode()).hexdigest() for n in range(2400)
    )
    before = deepcopy((project.metadata_json, world, brief, outline, initial))
    requests = []
    with pytest.raises(capacity.SourceBoundPlanningCapacityError) as error:
        await _run(monkeypatch, brief, outline, initial=initial, requests=requests)
    assert len(requests) == 1
    assert requests[0].prompt_template == "outline_commercial_judge"
    assert error.value.receipt["truncated"] is False
    assert error.value.receipt["combined_chars"] > error.value.receipt["max_combined_chars"]
    assert (project.metadata_json, world, brief, outline, initial) == before
    assert initial["pass"] is False and initial["overall_score"] == 0.41


async def test_late_source_changes_effective_input_hashes_of_both_real_requests(monkeypatch):
    project, world = _case()
    first, _ = await _run(monkeypatch, _brief(project, world), _outline(False))
    outline = _outline(False)
    outline["late_unknown"]["complete"] += " A distinct final condition."
    second, _ = await _run(monkeypatch, _brief(project, world), outline)
    role = load_settings().llm.critic
    for old, new in zip(first, second, strict=True):
        new = new.model_copy(
            update={"project_id": old.project_id, "workflow_run_id": old.workflow_run_id}
        )
        assert old.metadata["review_context_sha256"] != new.metadata["review_context_sha256"]
        assert _effective_input_hash(old, role) != _effective_input_hash(new, role)


async def test_legacy_entry_and_direct_adjudication_keep_v2_without_new_preflight(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An unmarked legacy request must not enter the v3 helper")

    monkeypatch.setattr(judge, "_prepare_complete_outline_review_request", forbidden)
    requests, _ = await _run(
        monkeypatch, {"title": "An unmarked book", "language": "en"}, _outline(False)
    )
    assert len(requests) == 2
    assert all(r.prompt_version == "v2-source-authority" for r in requests)
    assert all("review_context_sha256" not in r.metadata for r in requests)
    assert all("[COMPLETE REVIEW DATA]" not in r.user_prompt for r in requests)
    assert "## 大纲数据（已压缩为评测关键字段）" in requests[0].user_prompt
    assert "初审问题（index 必须逐项裁决）" in requests[1].user_prompt


@pytest.mark.parametrize("occurrences", [0, 2])
def test_helper_rejects_nonunique_replacement_without_touching_request(occurrences):
    request = LLMCompletionRequest(
        logical_role="critic",
        system_prompt="Review.",
        user_prompt="PREFIX" + "DATA" * occurrences,
        fallback_response="{}",
        project_id=uuid4(),
        workflow_run_id=uuid4(),
    )
    before = request.model_dump(mode="json")
    with pytest.raises(ValueError, match="outline_complete_source_block_not_unique"):
        judge._prepare_complete_outline_review_request(
            request, shared_data={"exact": [1, False]}, data_block="DATA"
        )
    assert request.model_dump(mode="json") == before
