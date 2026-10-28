"""P9: bounded segment expansion, physical replay and immutable v1 compatibility."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_fanqie_outline_contract_completion import (  # noqa: F401 - fixture dependencies
    acquired,
    case,
    complete,
    outline_case,
    protected_rows,
    store,
)

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import fanqie_beat_recovery as beat
from bestseller.services import fanqie_outline_contract_completion as completion
from bestseller.services.finite_book_budget import bind_run
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.retained_planning_generation import _Reader

# ruff: noqa: F811 -- imported pytest fixtures intentionally match fixture arguments.
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
FIXTURES = Path(__file__).parents[1] / "fixtures" / "fanqie_outline_completion"


def baseline_module():
    name = "_p9_frozen_outline_completion"
    if name not in sys.modules:
        source = subprocess.check_output(
            [
                "git",
                "show",
                "refs/tags/e2e17:src/bestseller/services/fanqie_outline_contract_completion.py",
            ],
            text=True,
        )
        spec = importlib.util.spec_from_loader(name, loader=None)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(
            compile(source, "refs/tags/e2e17:fanqie_outline_contract_completion.py", "exec"),
            module.__dict__,
        )
    return sys.modules[name]


def document(request):
    return decode_lossless_source(
        json.loads(request.user_prompt[request.user_prompt.index("{") :])["planning_document"]
    )


async def test_v1_native_requests_receipt_and_hashes_are_baseline_bytes(outline_case):
    c = outline_case
    baseline = baseline_module()
    async with acquired(c):
        original = await baseline.complete_missing_outline_contract(
            c.session,
            c.settings,
            project_id=c.project.id,
            outline_payload=deepcopy(c.base),
            requested_by="synthetic-native-outline-test",
        )
    await c.session.commit()
    requests = [beat._json(req.model_dump(mode="json")).encode() for req in c.requests]
    receipt_bytes = beat._json(original["_meta"]["outline_contract_completion"]).encode()
    rows = await protected_rows(c)
    result = await complete(c)
    assert beat._json(result["_meta"]["outline_contract_completion"]).encode() == receipt_bytes
    assert beat._json(result).encode() == beat._json(original).encode()
    assert [beat._json(req.model_dump(mode="json")).encode() for req in c.requests] == requests
    assert await protected_rows(c) == rows
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
        old_project, old_source = await baseline._sources(c.session, c.settings, c.project.id)
        assert beat._json(source).encode() == beat._json(old_source).encode()
        assert beat._sha(source) == beat._sha(old_source)
        receipt = original["_meta"]["outline_contract_completion"]
        replayed = await completion._verified_candidate(
            c.session, c.settings, project, source, receipt
        )
        old_replayed = await baseline._verified_candidate(
            c.session, c.settings, old_project, old_source, receipt
        )
        assert beat._json(replayed).encode() == beat._json(old_replayed).encode()


@pytest.mark.parametrize("outline_case", [5, 6, 7, 8], indirect=True)
async def test_every_segment_pointer_is_completed_and_physically_replayed(
    outline_case, monkeypatch
):
    c = outline_case
    original = deepcopy(c.base)
    prepared = []
    native_prepare = completion._prepare_capacity

    def prepare(request, settings, project):
        capacity = native_prepare(request, settings, project)
        prepared.append((request.metadata["outline_completion_part"], capacity))
        return capacity

    monkeypatch.setattr(completion, "_prepare_capacity", prepare)
    result = await complete(c)
    await c.session.commit()
    n = c.project.target_chapters
    assert [index for index, _ in prepared] == list(range(n))
    assert all(capacity["budget_passed"] and not capacity["truncated"] for _, capacity in prepared)
    receipt = result["_meta"]["outline_contract_completion"]
    assert receipt["schema"] == "fanqie-outline-contract-completion.v2"
    assert receipt["target_chapters"] == n
    assert receipt["ordered_chapter_numbers"] == list(range(1, n + 1))
    assert [part["part"] for part in receipt["parts"]] == list(range(n))
    for index, req in enumerate(c.requests):
        doc = document(req)
        assert req.prompt_version == "fanqie-plan-fields-v2"
        assert doc["target_chapters"] == n
        assert doc["ordered_chapter_numbers"] == list(range(1, n + 1))
        assert doc["source"]["original_beat"]["raw"] == c.raw
        assert doc["source"]["base_outline"] == original
        assert doc["source"]["segment_contract"]["target_chapters"] == n
        assert req.metadata["outline_completion_target_chapters"] == n
        assert req.metadata["outline_completion_ordered_chapter_numbers"] == list(range(1, n + 1))
        for pointer in doc["required_pointer_values"]:
            assert pointer.startswith(f"/chapters/{index}/")
            assert not completion._empty(completion._leaf(result, pointer))
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
        replay = await completion._verified_candidate(
            c.session, c.settings, project, source, receipt
        )
    assert beat._json(replay) == beat._json(result)
    assert beat._json(await complete(c)) == beat._json(result)
    assert len(c.requests) == n


@pytest.mark.parametrize("outline_case", [6, 8], indirect=True)
async def test_parts_bound_is_exactly_twice_the_trusted_count(outline_case):
    c = outline_case
    c.transform = lambda req, patch: (
        "{}" if req.metadata["outline_completion_attempt"] == 0 else beat._json(patch)
    )
    result = await complete(c)
    await c.session.commit()
    receipt = result["_meta"]["outline_contract_completion"]
    assert len(receipt["parts"]) == 2 * c.project.target_chapters
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
        assert (
            await completion._verified_candidate(c.session, c.settings, project, source, receipt)
            == result
        )
        bad = deepcopy(receipt)
        bad["parts"].append(deepcopy(bad["parts"][-1]))
        workflow = await c.session.get(WorkflowRunModel, UUID(receipt["workflow_run_id"]))
        workflow.metadata_json = {**workflow.metadata_json, "outline_contract_completion": bad}
        await c.session.commit()
        with pytest.raises(
            completion.OutlineContractCompletionError, match="derived_outline_parts"
        ):
            await completion._verified_candidate(c.session, c.settings, project, source, bad)


@pytest.mark.parametrize("n", [3, 9, True, 6.0])
async def test_invalid_target_rejected_before_noop_or_dispatch(outline_case, n):
    c = outline_case
    c.project.target_chapters = n
    await c.session.commit()

    # No DB roundtrip coercion can turn bool/float into a trusted integer here.
    async def sources(*args):
        return c.project, {"base_outline": c.base}

    from unittest.mock import patch

    with patch.object(completion, "_sources", sources):
        with pytest.raises(completion.OutlineContractCompletionError, match="segment_count"):
            await completion.complete_missing_outline_contract(
                c.session, c.settings, project_id=c.project.id, outline_payload=c.base
            )
    assert not c.requests


@pytest.mark.parametrize(
    "change",
    [
        "candidate_count",
        "candidate_order",
        "candidate_bool",
        "beat_order",
        "beat_count",
        "manifest_target",
        "creation_count",
        "creation_bool",
    ],
)
async def test_scope_requires_continuous_numbers_and_agreeing_sources(
    outline_case, monkeypatch, change
):
    c = outline_case
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
    source = deepcopy(source)
    if change == "candidate_count":
        source["base_outline"]["chapters"].pop()
    elif change == "candidate_order":
        source["base_outline"]["chapters"][-1]["chapter_number"] = 3
    elif change == "candidate_bool":
        source["base_outline"]["chapters"][0]["chapter_number"] = True
    elif change == "beat_order":
        source["original_beat"]["raw"]["beats"][0]["segment_number"] = 2
    elif change == "beat_count":
        source["original_beat"]["raw"]["beats"].pop()
    elif change == "manifest_target":
        source["selected_source"]["project_inputs"]["target_chapters"] = 6
    else:
        project.metadata_json = {
            **project.metadata_json,
            "segment_count": True if change == "creation_bool" else project.target_chapters + 1,
        }
    await c.session.commit()

    async def sources(*args):
        return project, source

    monkeypatch.setattr(completion, "_sources", sources)
    async with acquired(c):
        with pytest.raises(completion.OutlineContractCompletionError, match="segment"):
            await completion.complete_missing_outline_contract(
                c.session,
                c.settings,
                project_id=c.project.id,
                outline_payload=source["base_outline"],
            )
    assert not c.requests


@pytest.mark.parametrize("outline_case", [6, 8], indirect=True)
async def test_v2_receipt_cannot_self_authorize_count_order_or_schema(outline_case):
    c = outline_case
    result = await complete(c)
    await c.session.commit()
    receipt = result["_meta"]["outline_contract_completion"]
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
        workflow = await c.session.get(WorkflowRunModel, UUID(receipt["workflow_run_id"]))
        for key, value in [
            ("schema", completion.SCHEMA),
            ("schema", "unknown"),
            ("target_chapters", 4),
            ("target_chapters", float(c.project.target_chapters)),
            ("ordered_chapter_numbers", list(reversed(range(1, c.project.target_chapters + 1)))),
        ]:
            bad = {**deepcopy(receipt), key: value}
            workflow.metadata_json = {**workflow.metadata_json, "outline_contract_completion": bad}
            await c.session.commit()
            with pytest.raises(
                completion.OutlineContractCompletionError, match="derived_outline_receipt_changed"
            ):
                await completion._verified_candidate(c.session, c.settings, project, source, bad)


@pytest.mark.parametrize("outline_case", [6, 8], indirect=True)
async def test_context_capacity_blocks_before_reservation_and_preserves_full_source(outline_case):
    c = outline_case
    before = deepcopy(c.base)
    with bind_run("p9-test", project_id=c.project.id, stage="planning", capacity_limit=100):
        with pytest.raises(
            completion.OutlineContractCompletionError, match="capacity_blocked"
        ) as error:
            await complete(c)
    assert error.value.candidate == before
    assert not c.requests
    workflows = await _Reader(c.session).rows(
        WorkflowRunModel, WorkflowRunModel.workflow_type == completion.WORKFLOW_TYPE
    )
    assert len(workflows) == 1 and not workflows[0].metadata_json.get("dispatches")


async def test_capacity_counts_all_segments_and_checks_every_request(outline_case):
    c = outline_case
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
    sizes = []
    for n in (4, 6, 8):
        expanded = deepcopy(source)
        project.target_chapters = n
        expanded["selected_source"]["project_inputs"]["target_chapters"] = n
        for index in range(4, n):
            chapter = deepcopy(source["base_outline"]["chapters"][index % 4])
            chapter["chapter_number"] = index + 1
            chapter["title"] = f"Lens source {index}"
            expanded["base_outline"]["chapters"].append(chapter)
            item = deepcopy(source["original_beat"]["raw"]["beats"][index % 4])
            item["segment_number"] = index + 1
            expanded["original_beat"]["raw"]["beats"].append(item)
        if n != 4:
            expanded["segment_contract"] = {
                "target_chapters": n,
                "ordered_chapter_numbers": list(range(1, n + 1)),
            }
        diagnostics = completion._diagnose(
            expanded["base_outline"], project, c.settings, c.assets["cast_spec"].content
        )
        req = completion._request(
            project,
            expanded,
            expanded["base_outline"],
            diagnostics,
            completion._targets(expanded["base_outline"], diagnostics, 0),
            UUID(int=44),
            0,
            0,
            None,
        )
        capacity = completion._prepare_capacity(req, c.settings, project)
        sizes.append(capacity["reserved_input_tokens"])
        assert document(req)["source"] == expanded
    assert sizes[0] < sizes[1] < sizes[2]


def fixed_v1_bundle():
    """Fixed synthetic receipt inputs; generated only with refs/tags/e2e17."""
    old = baseline_module()
    project = SimpleNamespace(id=UUID(int=1), target_chapters=4, metadata_json={})
    base = {"chapters": [{"chapter_number": i, "title": ""} for i in range(1, 5)]}
    source = {
        "selected_source": {
            "project_inputs": {"target_chapters": 4},
            "complete_foundation_content": {"cast_spec": {}},
        },
        "original_beat": {
            "raw": {"beats": [{"segment_number": i} for i in range(1, 5)]},
            "receipt": {"llm_run_id": str(UUID(int=2))},
        },
        "base_outline": base,
    }
    workflow_id = UUID(int=3)
    candidate, requests, parts, patches = deepcopy(base), [], [], []
    for index in range(4):
        diagnostics = fixed_diagnostics(candidate)
        targets = old._targets(candidate, diagnostics, index)
        req = old._request(
            project, source, candidate, diagnostics, targets, workflow_id, index, 0, None
        )
        requests.append(req.model_dump(mode="json"))
        patch = {f"/chapters/{index}/title": f"Lens {index + 1}"}
        patches.append(beat._json(patch))
        candidate = old._apply_patch(candidate, targets, patches[-1])
        parts.append(
            {
                "part": index,
                "attempt": 0,
                "paid_proof": {"llm_run_id": str(UUID(int=10 + index))},
                "capacity": {"budget_passed": True},
            }
        )
    receipt = {
        "schema": old.SCHEMA,
        "workflow_run_id": str(workflow_id),
        "source_sha256": beat._sha(source),
        "base_outline_sha256": beat._sha(base),
        "candidate_payload_sha256": beat._sha(candidate),
        "original_beat": source["original_beat"]["receipt"],
        "parts": parts,
        "native_blocking_diagnostics": [],
        "generation_method": "original_paid_beat_plus_model_missing_plan_fields",
        "generation_llm_run_ids": [
            str(UUID(int=2)),
            *[p["paid_proof"]["llm_run_id"] for p in parts],
        ],
        "single_provider_payload_claimed": False,
        "grade_asserted": False,
        "adoption_granted": False,
        "credit_granted": False,
    }
    candidate["_meta"] = {"outline_contract_completion": receipt}
    return {
        "source": source,
        "requests": requests,
        "patches": patches,
        "receipt": receipt,
        "candidate": candidate,
    }


def fixed_diagnostics(payload, *_):
    return [
        {
            "code": "TITLE_MISSING",
            "chapter_number": row["chapter_number"],
            "location": f"chapters[{index}].title",
        }
        for index, row in enumerate(payload["chapters"])
        if not row["title"]
    ]


async def test_fixed_v1_receipt_and_request_bytes_match_frozen_baseline(monkeypatch):
    fixture = json.loads((FIXTURES / "v1-fixed-receipt.json").read_text())
    assert beat._json(fixed_v1_bundle()).encode() == (
        FIXTURES / "v1-fixed-receipt.json"
    ).read_bytes().rstrip(b"\n")
    from bestseller.services import llm

    project = SimpleNamespace(id=UUID(int=1), target_chapters=4, metadata_json={})
    receipt = fixture["receipt"]
    workflow = SimpleNamespace(
        id=UUID(int=3),
        status="completed",
        workflow_type=completion.WORKFLOW_TYPE,
        project_id=project.id,
        scope_type="project",
        scope_id=project.id,
        metadata_json={"outline_contract_completion": receipt},
    )

    class Reader:
        def __init__(self, session):
            pass

        async def get(self, *args):
            return workflow

    async def paid(session, settings, project, run_id):
        index = int(UUID(str(run_id))) - 10
        return (
            SimpleNamespace(id=UUID(str(run_id))),
            llm.LLMCompletionRequest.model_validate(fixture["requests"][index]),
            SimpleNamespace(
                content=fixture["patches"][index],
                fallback_used=False,
                tool_calls=None,
                finish_reason="stop",
            ),
            receipt["parts"][index]["paid_proof"],
        )

    monkeypatch.setattr(beat, "_native_owner", lambda *args: None)
    for module in (baseline_module(), completion):
        monkeypatch.setattr(module, "_Reader", Reader)
        monkeypatch.setattr(module, "_paid", paid)
        monkeypatch.setattr(module, "_diagnose", fixed_diagnostics)
        monkeypatch.setattr(module, "_prepare_capacity", lambda *args: {"budget_passed": True})
        replay = await module._verified_candidate(None, None, project, fixture["source"], receipt)
        assert beat._json(replay).encode() == beat._json(fixture["candidate"]).encode()
        assert (
            beat._json(replay["_meta"]["outline_contract_completion"]).encode()
            == beat._json(receipt).encode()
        )


@pytest.mark.parametrize(
    "outline_case",
    [{"segments": 6, "complete": True}, {"segments": 8, "complete": True}],
    indirect=True,
)
async def test_complete_expanded_outline_is_noop(outline_case):
    c = outline_case
    before = await protected_rows(c)
    assert await complete(c) == c.base
    assert not c.requests and await protected_rows(c) == before


@pytest.mark.parametrize("outline_case", [6, 8], indirect=True)
async def test_v2_full_source_retention_overflow_is_explicit_and_never_truncated(outline_case):
    import hashlib

    c = outline_case
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
    source = deepcopy(source)
    source["complete_tail_probe"] = "".join(
        hashlib.sha256(str(index).encode()).hexdigest() for index in range(2600)
    )
    diagnostics = completion._diagnose(c.base, project, c.settings, c.assets["cast_spec"].content)
    req = completion._request(
        project,
        source,
        c.base,
        diagnostics,
        completion._targets(c.base, diagnostics, 0),
        UUID(int=44),
        0,
        0,
        None,
    )
    original_prompt = req.user_prompt
    with pytest.raises(
        completion.OutlineContractCompletionError, match="capacity_blocked:physical_retention"
    ):
        completion._prepare_capacity(req, c.settings, project)
    assert req.user_prompt == original_prompt and document(req)["source"] == source
    assert not c.requests


@pytest.mark.parametrize("outline_case", [8], indirect=True)
async def test_tail_unknown_keeps_paid_prefix_and_same_closed_slot(outline_case):
    from test_fanqie_outline_contract_completion import recover_completion_error

    c = outline_case
    c.transform = lambda req, patch: (
        TimeoutError("Unknown tail result")
        if req.metadata["outline_completion_part"] == 7
        else beat._json(patch)
    )
    with pytest.raises(completion.OutlineContractCompletionError) as first:
        await complete(c)
    workflow = await recover_completion_error(c, first.value)
    metadata = deepcopy(workflow.metadata_json)
    assert len(c.requests) == 8 and "7:0" in metadata["dispatches"]
    with pytest.raises(
        completion.OutlineContractCompletionError, match="unknown_or_changed_dispatch_never_retry"
    ) as repeat:
        await complete(c)
    same = await recover_completion_error(c, repeat.value)
    assert same.id == workflow.id and same.metadata_json["dispatches"] == metadata["dispatches"]
    assert len(c.requests) == 8


@pytest.mark.parametrize("outline_case", [6], indirect=True)
async def test_v2_native_gateway_retains_and_replays_actual_request(outline_case, monkeypatch):
    from test_fanqie_outline_contract_completion import NATIVE_COMPLETE_TEXT, authored_fields

    from bestseller.services import llm
    from bestseller.services.foundation_field_recovery import _paid
    from bestseller.services.planning_surgical_budget import resolve_request_role
    from bestseller.services.source_complete_retention import LEGACY_LIMIT, retention_limit

    c = outline_case
    async with acquired(c):
        project, source = await completion._sources(c.session, c.settings, c.project.id)
        # Use an existing native planning workflow solely as test physical custody.
        workflow_id = c.beat_run.workflow_run_id
        diagnostics = completion._diagnose(
            c.base, project, c.settings, c.assets["cast_spec"].content
        )
        targets = completion._targets(c.base, diagnostics, 5)
        req = completion._request(
            project, source, c.base, diagnostics, targets, workflow_id, 5, 0, None
        )
        completion._prepare_capacity(req, c.settings, project)
        role = resolve_request_role(c.settings, project, req)
        assert retention_limit(c.settings, req, audit=None, role=role) == LEGACY_LIMIT
        assert "source_complete_retention" not in req.metadata
        assert "planning_surgical_output_budget" not in req.metadata
        patch = {path: authored_fields(5)[path.rsplit("/", 1)[1]] for path in targets}
        sent = []

        async def wire_only(actual, actual_role):
            sent.append(actual)
            content = beat._json(patch)
            return content, 13, 7, "stop", None, {"role": "assistant", "content": content}

        monkeypatch.setattr(llm, "_call_litellm", wire_only)
        with llm.bind_project_prompt_audit(project.id, reason="P9 v2 native gateway fixture"):
            result = await NATIVE_COMPLETE_TEXT(c.session, c.settings, req)
        await c.session.commit()
        _, retained, paid, _ = await _paid(c.session, c.settings, project, result.llm_run_id)
        assert sent == [req] and paid.content == beat._json(patch)
        assert completion._same_request(retained, req)
        assert document(retained)["source"] == source and not c.requests
