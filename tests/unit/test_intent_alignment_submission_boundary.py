"""Offline conception -> real intent wrapper/parser boundary, no paid evidence.

Only the last complete_text boundary returns synthetic tool-call envelopes.
Synthetic origin/roots/UUIDs are protocol fixtures, never provider receipts,
literary grades or production authority. Other model/DB/network entrypoints
are tripwires. A BaseException stops immediately after intent review, before
unrelated quality gates; no unrelated gate receives a fabricated PASS.
"""

# ruff: noqa: RUF001 -- Chinese source text is deliberate fixture material.
from copy import deepcopy
import hashlib
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_conception_candidate_resume import review_state
from test_intent_review_submission_v3 import completion as synthetic_completion

from bestseller.services import conception, intent_alignment, llm
from bestseller.services import intent_review_submission as submission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.concept_contract import ConceptContractError
from bestseller.services.conception_checkpoints import build_conception_checkpoint
from bestseller.services.genre_intent_contract import GenreIntentContract
from bestseller.settings import LLMRoleSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = "conception:offline-intent-boundary"
TAG = "升级流"
ROLE = LLMRoleSettings(
    model="minimax/offline-test", max_tokens=8192, temperature=0.2, timeout_seconds=30
)


class ExternalCallTripwire(BaseException):
    """Cannot be swallowed by the application's advisory except Exception."""


class AfterIntentBoundary(BaseException):
    pass


class AfterIntentFailure(BaseException):
    def __init__(self, failure):
        super().__init__(failure["message"])
        self.conception_log = deepcopy(failure["conception_log"])
        self.failure = deepcopy(failure)


class NoDatabaseSession:
    def __getattr__(self, name):
        raise ExternalCallTripwire("unexpected DB/session operation: " + name)


@pytest.fixture(autouse=True)
def no_external_intent_io(monkeypatch):
    def denied(*args, **kwargs):
        raise ExternalCallTripwire("offline intent boundary forbids external IO/model")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(conception, "complete_text", denied)
    monkeypatch.setattr(submission, "complete_text", denied)


def assessment(request, verdict):
    """Source IDs come from this exact request, never from another candidate."""
    source = json.loads(request.user_prompt)
    anchor = next(row for row in source["catalog"] if row["source_path"] == "/candidate/premise")
    ref = {key: anchor[key] for key in ("source_path", "anchor_id")}
    return {
        "items": {
            tag: {
                "verdict": verdict,
                "reason": "Offline structural fixture.",
                "evidence": [] if verdict == "unknown" else [ref],
            }
            for tag in source["intent_tags"]
        },
        "counter_elements": [
            {**ref, "against": tag, "reason": "Offline contradiction fixture."}
            for tag in source["intent_tags"]
        ]
        if verdict == "fail"
        else [],
        "revise_direction": "保留原故事身份，明确既有修理技能的进步。" if verdict == "fail" else "",
        "cost_verdict": "not_requested" if source["cost_style"] == "standard" else "pass",
    }


@pytest.fixture
def intent_boundary(monkeypatch):
    from bestseller.services import conception_repair_guard

    state = review_state()
    state["chapter_count"] = 4
    genre = GenreIntentContract(
        genre_key="urban",
        genre_label="都市",
        sub_genre_key="industry",
        sub_genre_label="行业",
        prompt_pack_key="urban",
        source="user",
        tag_source="user",
        allowed_modernity="modern",
        user_tags=(TAG,),
        default_tags=("默认分类",),
        tags=(TAG, "默认分类"),
        explicit_enhancers={"cost_style": "minimal"},
    )
    state["genre_intent_contract"] = genre.model_dump(mode="json")
    state["ctx"]["genre_intent_contract"] = genre.model_dump(mode="json")
    state["ctx"]["creation_intent_contract"] = {
        "field_sources": {"story_enhancers": "explicit"},
        "story_enhancers": {"cost_style": "minimal"},
    }
    state["synopsis"] = "标签：都市+慢热\n\n修理工发现机器故障并与厂长交涉。"
    fixture = SimpleNamespace(
        state=state,
        requests=[],
        responses=[],
        failures=[],
        editor_calls=[],
        joint_calls=[],
        stopped=[],
        structure_calls=[],
        initial=["pass", "pass"],
        recheck="pass",
        same_root=False,
        same_run=False,
        malformed=False,
        wrong_effective_hash=False,
        missing_counter=False,
        recheck_counter_count=1,
        stale_recheck=False,
        fixed={
            "premise": "修理工用新学会的检修法找出故障，拒绝签署虚假检修单。",
            "synopsis": "修理工修好机器，厂长签字确认他的检修技术已取得进步。",
            "story_spine": {"who": "修理工", "against": "厂长"},
        },
    )
    shared_run = uuid4()
    workflow = uuid4()

    async def gateway(session, settings, request):
        assert isinstance(session, NoDatabaseSession) or session is None
        assert request.prompt_template == "conception_intent_alignment"
        source = json.loads(request.user_prompt)
        namespace = llm._semantic_operation_namespace.get()
        assert namespace is not None and namespace[0].startswith("intent-review:")
        verdict = (
            fixture.recheck
            if source["phase"] == "recheck"
            else fixture.initial[request.sample_slot]
        )
        # Real request builder + real parser remain active. Only native gateway
        # settlement is replaced; the synthetic root is explicitly test-owned.
        data = assessment(request, verdict)
        if fixture.missing_counter:
            data["counter_elements"] = []
        if (
            source["phase"] == "recheck"
            and verdict == "fail"
            and fixture.recheck_counter_count == 2
        ):
            second = next(
                row for row in source["catalog"] if row["source_path"] == "/candidate/synopsis"
            )
            data["counter_elements"].append(
                {
                    "source_path": second["source_path"],
                    "anchor_id": second["anchor_id"],
                    "against": TAG,
                    "reason": "Offline second source-bound contradiction.",
                }
            )
        result = synthetic_completion(
            request.model_copy(update={"workflow_run_id": request.workflow_run_id or workflow}),
            data,
        )
        effective = llm._effective_input_hash(request, ROLE)
        root = (
            "offline-root:"
            + hashlib.sha256(
                (namespace[0] + ":sample:" + str(request.sample_slot)).encode()
            ).hexdigest()
        )
        result.metadata["execution_receipt"].update(
            scope_id=SCOPE, operation_key="offline-root:duplicate" if fixture.same_root else root
        )
        if fixture.same_run:
            result.llm_run_id = shared_run
        if fixture.malformed:
            result.tool_calls = []
        if fixture.wrong_effective_hash:
            result.metadata["effective_input_receipt"]["hash"] = "0" * 64
        if source["phase"] == "recheck" and fixture.stale_recheck:
            result = deepcopy(fixture.responses[0])
        fixture.responses.append(deepcopy(result))
        fixture.requests.append(
            {
                "request": deepcopy(request),
                "source": source,
                "effective_input": llm._effective_input_payload(request, ROLE),
                "effective_hash": effective,
                "namespace": namespace,
                "root": result.metadata["execution_receipt"]["operation_key"],
                "run_id": result.llm_run_id,
            }
        )
        return result

    async def editor(session, settings, **kwargs):
        if kwargs.get("template") != "conception_intent_repair" or kwargs.get("role") != "editor":
            raise ExternalCallTripwire("unexpected conception model boundary")
        fixture.editor_calls.append(deepcopy(kwargs))
        return deepcopy(fixture.fixed), [uuid4()]

    async def joint(*args, **kwargs):
        fixture.joint_calls.append(deepcopy(kwargs))
        # Deliberately refuses joint approval. Recheck alone cannot approve a
        # repaired book, and no unrelated quality score is loosened here.
        return {"passed": False, "status": "offline_joint_gate_not_approved"}

    def stop(**kwargs):
        fixture.stopped.append(deepcopy(kwargs))
        raise AfterIntentBoundary

    async def next_repair(session, settings, state, diagnosis, **kwargs):
        fixture.structure_calls.append(deepcopy(diagnosis))
        fixture.failures.append(deepcopy(state["last_failure"]))
        raise AfterIntentFailure(state["last_failure"])

    monkeypatch.setattr(submission, "complete_text", gateway)
    monkeypatch.setattr(conception, "_llm_call_json", editor)
    monkeypatch.setattr(conception_repair_guard, "review_conception_repair", joint)
    monkeypatch.setattr(conception, "_conception_ontology_story_surface", stop)
    monkeypatch.setattr(conception, "_repair_conception_candidate_structure", next_repair)

    async def run():
        checkpoint = build_conception_checkpoint(fixture.state, input_hash="offline-intent-input")
        with bind_execution_scope(SCOPE):
            return await conception.run_conception_pipeline(
                NoDatabaseSession(),
                None,
                genre_key="urban",
                chapter_count=fixture.state["chapter_count"],
                resume_checkpoint=checkpoint,
                checkpoint_input_hash="offline-intent-input",
            )

    fixture.run = run
    return fixture


def reports(error):
    return [
        row["report"]
        for row in error.conception_log
        if row.get("agent") == "intent_alignment_sample"
    ]


async def test_public_forwarder_keeps_bound_ids_and_real_request_parser(intent_boundary):
    f = intent_boundary
    pid, wid = uuid4(), uuid4()
    with bind_execution_scope(SCOPE):
        report = await intent_alignment.evaluate_intent_alignment_sample(
            None,
            None,
            candidate={k: f.state[k] for k in ("premise", "synopsis", "story_spine")},
            user_controls={
                "genre_intent_contract": f.state["genre_intent_contract"],
                "creation_intent_contract": f.state["ctx"]["creation_intent_contract"],
            },
            intent_tags=[TAG],
            genre_label="都市",
            cost_style="minimal",
            phase="recheck",
            sample_slot=0,
            project_id=pid,
            workflow_run_id=wid,
        )
    # The fixture must preserve the caller's explicit origin, too.
    assert f.requests[0]["request"].project_id == pid
    assert f.requests[0]["request"].workflow_run_id == wid
    assert report["status"] == "verified"
    assert report["receipt"]["phase"] == "recheck"


@pytest.mark.parametrize(
    "fault", ["same_root", "same_run", "malformed", "wrong_effective_hash", "missing_counter"]
)
async def test_native_conception_refuses_nonindependent_or_invalid_evidence(intent_boundary, fault):
    f = intent_boundary
    setattr(f, fault, True)
    if fault == "missing_counter":
        f.initial = ["fail", "fail"]
    with pytest.raises(
        ConceptContractError, match="CONCEPTION_INTENT_EVIDENCE_UNRESOLVED"
    ) as caught:
        await f.run()
    assert len(f.requests) == 2 and not f.editor_calls and not f.joint_calls and not f.stopped
    assert len(reports(caught.value)) == 2
    if fault in {"malformed", "wrong_effective_hash", "missing_counter"}:
        assert all(row["status"] == "unknown" for row in reports(caught.value))
    if fault == "missing_counter":
        assert all(
            row["receipt"]["errors"] == ["intent_review:failed_intent_counter_missing"]
            for row in reports(caught.value)
        )


async def test_one_sample_objection_stays_advisory_without_editor(intent_boundary):
    f = intent_boundary
    f.initial = ["fail", "pass"]
    with pytest.raises(AfterIntentBoundary):
        await f.run()
    assert len(f.requests) == 2 and not f.editor_calls and not f.joint_calls
    assert len(f.stopped) == 1 and f.stopped[0]["premise"] == f.state["premise"]


async def test_recheck_pass_still_requires_independent_joint_approval(intent_boundary):
    f = intent_boundary
    f.initial = ["fail", "fail"]
    with pytest.raises(AfterIntentFailure, match="CONCEPTION_INTENT_REPAIR_UNRESOLVED") as caught:
        await f.run()
    assert len(f.requests) == 3 and len(f.joint_calls) == 1 and not f.stopped
    recheck = reports(caught.value)[-1]
    assert recheck["status"] == "verified" and recheck["failed_tags"] == []
    failure = next(
        row for row in caught.value.conception_log if row.get("agent") == "intent_repair"
    )
    assert failure["adopted"] is False and failure["reason"] == "joint_review_not_passed"
    assert f.joint_calls[0]["before"]["premise"] == f.state["premise"]
    assert f.joint_calls[0]["candidate"] == f.fixed
    assert len(f.structure_calls) == 1
    assert f.structure_calls[0]["source"] == "corroborated_intent"


async def test_duplicate_initial_counters_cannot_make_failed_recheck_adoptable(intent_boundary):
    f = intent_boundary
    f.initial, f.recheck = ["fail", "fail"], "fail"
    with pytest.raises(AfterIntentFailure) as caught:
        await f.run()
    votes = reports(caught.value)
    assert len(votes) == 3 and all(row["failed_tags"] == [TAG] for row in votes)
    failure = next(
        row for row in caught.value.conception_log if row.get("agent") == "intent_repair"
    )
    assert failure["adopted"] is False and not f.joint_calls and not f.stopped
    assert f.structure_calls[0]["report"]["failed_intents"] == [TAG]


async def test_recheck_rejects_retained_response_bound_to_original_candidate(intent_boundary):
    f = intent_boundary
    f.initial = ["fail", "fail"]
    f.stale_recheck = True
    with pytest.raises(AfterIntentFailure) as caught:
        await f.run()
    rows = reports(caught.value)
    assert len(rows) == 3 and rows[-1]["status"] == "unknown"
    assert rows[-1]["receipt"]["errors"] == ["intent_review:effective_input_changed"]
    assert rows[-1]["receipt"]["original"]["tool_calls"] == f.responses[0].tool_calls
    assert f.requests[-1]["source"]["candidate"] == f.fixed
    assert f.requests[-1]["effective_hash"] != f.requests[0]["effective_hash"]
    assert not f.joint_calls and not f.stopped and len(f.structure_calls) == 1


@pytest.mark.parametrize("damage", ["too_short", "missing_spine"])
async def test_structurally_invalid_editor_candidate_never_reaches_recheck(intent_boundary, damage):
    f = intent_boundary
    f.initial = ["fail", "fail"]
    if damage == "too_short":
        f.fixed["premise"] = "短"
    else:
        f.fixed["story_spine"].pop("against")
    with pytest.raises(AfterIntentFailure, match="CONCEPTION_INTENT_REPAIR_UNRESOLVED"):
        await f.run()
    assert len(f.editor_calls) == 1 and len(f.requests) == 2 and not f.joint_calls and not f.stopped
