from __future__ import annotations

import copy
import inspect
import json
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner

pytestmark = pytest.mark.unit


def _outline() -> dict:
    return {
        "batch_name": "volume-1-outline",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "The sealed locker",
                "goal": "Mira must recover the ledger before the guard returns.",
                "main_conflict": "The guard controls the only key.",
                "hook_description": "The ledger bears Mira's family seal.",
                "unrelated": {"keep": True},
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["Mira", "Guard"],
                        "purpose": {
                            "story": "Mira asks the guard to open the locker.",
                            "emotion": "Mira fears what the ledger will prove.",
                        },
                        "entry_state": {"summary": "The locker is sealed."},
                        "exit_state": {"summary": "The guard refuses."},
                    }
                ],
            }
        ],
    }


def _patch(*, field: str = "goal", value: object = "Mira trades her pass for the key") -> dict:
    return {
        "chapter_number": 1,
        "scope": "chapter",
        "field": field,
        "value": value,
    }


def test_strict_surgical_patch_preserves_unnamed_fields() -> None:
    original = _outline()
    before = copy.deepcopy(original)

    repaired = planner._apply_outline_commercial_patches(
        original,
        [_patch()],
        strict=True,
    )

    assert repaired["chapters"][0]["goal"] == "Mira trades her pass for the key"
    assert repaired["chapters"][0]["title"] == before["chapters"][0]["title"]
    assert repaired["chapters"][0]["unrelated"] == {"keep": True}
    assert repaired["chapters"][0]["scenes"] == before["chapters"][0]["scenes"]
    assert original == before


@pytest.mark.parametrize(
    ("patches", "message"),
    [
        ([], "empty patch"),
        ([_patch(field="title", value="Hijacked")], "invalid patch"),
        ([_patch(value="Mira must recover the ledger before the guard returns.")], "no change"),
        ([_patch()] * 13, "capacity"),
    ],
)
def test_strict_surgical_patch_fails_closed(patches: list[dict], message: str) -> None:
    with pytest.raises(planner.PlannerFallbackError, match=message):
        planner._apply_outline_commercial_patches(
            _outline(),
            patches,
            strict=True,
        )


def test_strict_surgical_patch_rejects_changes_that_cancel_out() -> None:
    original_goal = _outline()["chapters"][0]["goal"]
    with pytest.raises(planner.PlannerFallbackError, match="duplicate target"):
        planner._apply_outline_commercial_patches(
            _outline(),
            [
                _patch(value="Temporary replacement"),
                _patch(value=original_goal),
            ],
            strict=True,
        )


def test_in_loop_repairs_route_to_surgical_editor_after_first_rejection() -> None:
    assert planner._outline_commercial_repair_mode(
        previous_outline=None,
        directives=[],
        source_is_lossless=False,
    ) == "generate"
    assert planner._outline_commercial_repair_mode(
        previous_outline=_outline(),
        directives=["chapter 1 goal lacks a concrete trade"],
        source_is_lossless=True,
    ) == "surgical"


def test_compact_historical_baseline_is_not_used_for_surgical_repair() -> None:
    assert planner._outline_commercial_repair_mode(
        previous_outline={"chapters": [{"chapter_number": 1, "goal": "Truncated..."}]},
        directives=["chapter 1 goal lacks a concrete trade"],
        source_is_lossless=False,
    ) == "generate"


@pytest.mark.asyncio
async def test_first_candidate_generates_once_then_actual_surgical_editor_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = 0
    validated = 0
    repair_run_id = uuid4()
    project = ProjectModel(
        id=uuid4(),
        slug="bounded-repair",
        title="Bounded Repair",
        genre="fantasy",
        language="en",
        target_word_count=7800,
        target_chapters=3,
        metadata_json={},
    )

    async def generate_candidate():
        nonlocal generated
        generated += 1
        return _outline(), [uuid4()], [],

    async def fake_complete_text(_session, _settings, _request):
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps({"patches": [_patch()]}),
                "llm_run_id": repair_run_id,
                "provider": "openai",
                "fallback_used": False,
            },
        )()

    monkeypatch.setattr(planner, "complete_text", fake_complete_text)

    async def no_existing_titles(*_args, **_kwargs):
        return []

    def validate_candidate(payload, **kwargs):
        nonlocal validated
        validated += 1
        assert kwargs["expected_count"] == 1
        assert len(payload["chapters"]) == 1
        return copy.deepcopy(payload)

    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", no_existing_titles)
    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", validate_candidate)

    async def repair_candidate(source, directives):
        return await planner._repair_outline_commercial_findings_surgically(
            object(),
            object(),
            project=project,
            workflow_run_id=uuid4(),
            logical_name="volume_1_chapter_outline",
            outline_payload=source,
            directives=directives,
            source_report={"passed": False, "overall_score": 0.61},
            validation_context={
                "volume_number": 1,
                "expected_count": 1,
                "chapter_number_offset": 1,
                "cast_spec": {},
                "volume_entry": {"volume_number": 1, "chapter_count_target": 1},
            },
        )

    first, _, _, first_mode = await planner._produce_outline_commercial_candidate(
        previous_outline=None,
        directives=[],
        source_is_lossless=False,
        generate_candidate=generate_candidate,
        repair_candidate=repair_candidate,
    )
    revised, run_ids, _, revised_mode = await planner._produce_outline_commercial_candidate(
        previous_outline=first,
        directives=["chapter 1 goal lacks a concrete trade"],
        source_is_lossless=True,
        generate_candidate=generate_candidate,
        repair_candidate=repair_candidate,
    )

    assert generated == 1
    assert validated == 1
    assert first_mode == "generate"
    assert revised_mode == "surgical"
    assert revised["chapters"][0]["goal"] == "Mira trades her pass for the key"
    assert revised["chapters"][0]["unrelated"] == {"keep": True}
    assert revised["_meta"]["surgical_llm_run_id"] == str(repair_run_id)
    receipt = revised["_meta"]["surgical_repair_receipt"]
    assert receipt["before_candidate"] == first
    assert receipt["source_report"]["overall_score"] == 0.61
    assert run_ids == [repair_run_id]


@pytest.mark.asyncio
async def test_consecutive_surgical_repairs_do_not_reinject_prior_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        id=uuid4(), slug="receipt-chain", title="Receipt Chain", genre="fantasy",
        language="en", target_word_count=7800, target_chapters=1, metadata_json={},
    )
    requests = []
    responses = [
        {"patches": [_patch()]},
        {
            "patches": [
                _patch(
                    field="main_conflict",
                    value="The guard will burn the ledger when the bell rings.",
                )
            ]
        },
    ]

    async def fake_complete_text(_session, _settings, request):
        requests.append(request)
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps(responses[len(requests) - 1]),
                "llm_run_id": uuid4(),
                "provider": "openai",
                "fallback_used": False,
            },
        )()

    async def no_existing_titles(*_args, **_kwargs):
        return []

    monkeypatch.setattr(planner, "complete_text", fake_complete_text)
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", no_existing_titles)
    monkeypatch.setattr(
        planner,
        "_validate_generated_volume_outline_or_raise",
        lambda payload, **_kwargs: copy.deepcopy(payload),
    )
    context = {
        "volume_number": 1,
        "expected_count": 1,
        "chapter_number_offset": 1,
        "cast_spec": {},
        "volume_entry": {"volume_number": 1, "chapter_count_target": 1},
    }
    first, _ = await planner._repair_outline_commercial_findings_surgically(
        object(), object(), project=project, workflow_run_id=uuid4(),
        logical_name="volume_1_chapter_outline", outline_payload=_outline(),
        directives=["repair goal"], validation_context=context,
        source_report={"private_note": "OLD_REPORT_SECRET"},
    )
    first["_meta"]["surgical_repair_receipt"]["before_candidate"][
        "private_note"
    ] = "OLD_CANDIDATE_SECRET"
    second, _ = await planner._repair_outline_commercial_findings_surgically(
        object(), object(), project=project, workflow_run_id=uuid4(),
        logical_name="volume_1_chapter_outline", outline_payload=first,
        directives=["repair conflict"], validation_context=context,
        source_report={"overall_score": 0.64},
    )

    assert "OLD_REPORT_SECRET" not in requests[1].user_prompt
    assert "OLD_CANDIDATE_SECRET" not in requests[1].user_prompt
    receipt = second["_meta"]["surgical_repair_receipt"]
    assert "_meta" not in receipt["before_candidate"]
    assert "surgical_repair_receipt" not in json.dumps(receipt["before_candidate"])
    assert receipt["source_reference"]["parent_llm_run_id"]


@pytest.mark.asyncio
async def test_strict_surgical_repair_rejects_mixed_non_object_patches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        id=uuid4(), slug="invalid-patches", title="Invalid Patches", genre="fantasy",
        language="en", target_word_count=7800, target_chapters=1, metadata_json={},
    )

    async def fake_complete_text(_session, _settings, _request):
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps({"patches": [_patch(), "discard me"]}),
                "llm_run_id": uuid4(),
                "provider": "openai",
                "fallback_used": False,
            },
        )()

    monkeypatch.setattr(planner, "complete_text", fake_complete_text)
    with pytest.raises(planner.PlannerFallbackError, match="array of patch objects"):
        await planner._repair_outline_commercial_findings_surgically(
            object(), object(), project=project, workflow_run_id=uuid4(),
            logical_name="volume_1_chapter_outline", outline_payload=_outline(),
            directives=["repair goal"],
            validation_context={
                "volume_number": 1,
                "expected_count": 1,
                "chapter_number_offset": 1,
                "cast_spec": {},
                "volume_entry": {"volume_number": 1, "chapter_count_target": 1},
            },
        )


@pytest.mark.asyncio
async def test_lower_scoring_candidate_does_not_cross_wire_report_or_directives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stronger = _outline()
    stronger["chapters"][0]["goal"] = "STRONGER_CANDIDATE needs a concrete price."
    stronger_report = {
        "passed": False,
        "overall_score": 0.71,
        "repair_directives": ["REPAIR_STRONGER_PRICE"],
    }
    weaker = _outline()
    weaker["chapters"][0]["goal"] = "WEAKER_CANDIDATE lacks a deadline."
    weaker_report = {
        "passed": False,
        "overall_score": 0.49,
        "repair_directives": ["REPAIR_WEAKER_DEADLINE"],
    }

    source, report, directives = planner._select_outline_repair_source_and_directives(
        best_payload=stronger,
        best_report=stronger_report,
        candidate_payload=weaker,
        candidate_report=weaker_report,
        project_metadata={},
        round_idx=0,
        max_rounds=2,
    )
    assert source == stronger
    assert {key: value for key, value in report.items()
            if key != "rejected_repair_feedback"} == stronger_report
    assert report["rejected_repair_feedback"]["report"] == weaker_report
    assert directives == ["REPAIR_STRONGER_PRICE"]

    requests = []

    async def fake_complete_text(_session, _settings, request):
        requests.append(request)
        return type(
            "CompletionStub",
            (),
            {
                "content": json.dumps({"patches": [_patch()]}),
                "llm_run_id": uuid4(),
                "provider": "openai",
                "fallback_used": False,
            },
        )()

    async def no_existing_titles(*_args, **_kwargs):
        return []

    monkeypatch.setattr(planner, "complete_text", fake_complete_text)
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", no_existing_titles)
    monkeypatch.setattr(
        planner,
        "_validate_generated_volume_outline_or_raise",
        lambda payload, **_kwargs: copy.deepcopy(payload),
    )
    project = ProjectModel(
        id=uuid4(), slug="paired-repair", title="Paired Repair", genre="fantasy",
        language="en", target_word_count=7800, target_chapters=1, metadata_json={},
    )
    repaired, _ = await planner._repair_outline_commercial_findings_surgically(
        object(), object(), project=project, workflow_run_id=uuid4(),
        logical_name="volume_1_chapter_outline", outline_payload=source or {},
        directives=directives, source_report=report,
        validation_context={
            "volume_number": 1,
            "expected_count": 1,
            "chapter_number_offset": 1,
            "cast_spec": {},
            "volume_entry": {"volume_number": 1, "chapter_count_target": 1},
        },
    )
    assert "STRONGER_CANDIDATE" in requests[0].user_prompt
    assert "REPAIR_STRONGER_PRICE" in requests[0].user_prompt
    assert "WEAKER_CANDIDATE" not in requests[0].user_prompt
    assert "REPAIR_WEAKER_DEADLINE" not in requests[0].user_prompt
    receipt = repaired["_meta"]["surgical_repair_receipt"]
    assert receipt["before_candidate"] == stronger
    assert receipt["source_report"] == report
    assert receipt["rejected_repair_feedback"]["report"] == weaker_report


def test_lower_scoring_passed_candidate_stops_failed_commercial_repair() -> None:
    failed = _outline()
    failed["chapters"][0]["goal"] = "HIGH_SCORE_FAILED"
    passed = _outline()
    passed["chapters"][0]["goal"] = "LOW_SCORE_PASSED"

    source, report, directives = planner._select_outline_repair_source_and_directives(
        best_payload=failed,
        best_report={
            "passed": False,
            "overall_score": 0.91,
            "repair_directives": ["STALE_FAILURE_DIRECTIVE"],
        },
        candidate_payload=passed,
        candidate_report={"passed": True, "overall_score": 0.83},
        project_metadata={},
        round_idx=1,
        max_rounds=3,
    )

    assert source == passed
    assert report == {"passed": True, "overall_score": 0.83}
    assert directives == []


def test_both_outline_loops_use_the_shared_repair_mode() -> None:
    assert "_produce_outline_commercial_candidate" in inspect.getsource(
        planner.generate_novel_plan
    )
    assert "_produce_outline_commercial_candidate" in inspect.getsource(
        planner.generate_volume_plan
    )
    assert "_select_outline_repair_source_and_directives" in inspect.getsource(
        planner.generate_novel_plan
    )
    assert "_select_outline_repair_source_and_directives" in inspect.getsource(
        planner.generate_volume_plan
    )


def test_normal_planning_reuse_policy_remains_enabled() -> None:
    source = inspect.getsource(planner._generate_structured_artifact)
    assert "planning_artifact_reuse_enabled" in source
    assert "_planning_artifact_reuse_allowed" in source
