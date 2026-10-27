"""Execute the real intent caller AST region, not a source-text assertion.

No provider or database. Only unrelated final-review stages are omitted. The
gateway boundary is replaced by preverified sample fixtures, while every call
still runs the frozen service's real request/source admission.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.services import budget, conception, conception_repair_guard
from bestseller.services import intent_review_submission as submission
from bestseller.services.concept_contract import ConceptContractError
from bestseller.services.genre_intent_contract import GenreIntentContract

pytestmark = pytest.mark.unit
SCOPE = "conception:offline-caller-test"
TAG = "payoff"


def _target_name(node):
    target = node.target if isinstance(node, ast.AnnAssign) else None
    return target.id if isinstance(target, ast.Name) else None


def _caller_region():
    path = Path(conception.__file__)
    tree = ast.parse(path.read_text())
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_review_conception_candidate"
    )
    outer = next(n for n in fn.body if isinstance(n, ast.Try))
    start = next(
        i
        for i, n in enumerate(outer.body)
        if _target_name(n) in {"_intent_repair_unresolved", "_intent_evidence_unavailable"}
    )
    end = next(
        i
        for i, n in enumerate(outer.body[start + 1 :], start + 1)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare)
        and isinstance(n.test.left, ast.Name)
        and n.test.left.id == "_intent_repair_unresolved"
    )
    setup = ast.parse("""
session, settings = None, None
state = env['state']
ctx = env['ctx']
genre_intent_contract = env['contract']
explicit_concept_seed = ''
premise, synopsis, story_spine = env['premise'], env['synopsis'], env['story_spine']
tags = env['tags']
conception_log, llm_run_ids = env['log'], env['ids']
_ap_genre, _ap_sub = 'Adventure', ''
""").body
    finish = ast.parse("""
env['after_intent_gate'] = True
return {'premise': premise, 'synopsis': synopsis, 'story_spine': story_spine,
        'tags': tags, 'log': conception_log, 'ids': llm_run_ids}
""").body
    wrapper = ast.AsyncFunctionDef(
        name="run_actual_intent_region",
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="env")],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=setup + outer.body[start : end + 1] + finish,
        decorator_list=[],
        returns=None,
    )
    namespace = dict(conception.__dict__)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            str(path),
            "exec",
        ),
        namespace,
    )
    return namespace[wrapper.name]


def _environment(*, requested=True, explicit_cost=True):
    contract = GenreIntentContract(
        genre_key="adventure",
        genre_label="Adventure",
        prompt_pack_key="general",
        tag_source="user",
        user_tags=(TAG,) if requested else (),
        tags=(TAG,) if requested else (),
    )
    creation = {"field_sources": {"genre_intent": "explicit"}}
    if explicit_cost:
        creation.update(
            field_sources={"genre_intent": "explicit", "story_enhancers": "explicit"},
            story_enhancers={"cost_style": "standard"},
            hook_spec={"_story_enhancer_input_sources": {"cost_style": "explicit"}},
        )
    return {
        "contract": contract,
        "ctx": {
            "language": "en",
            "genre_intent_contract": contract.model_dump(mode="json"),
            "creation_intent_contract": creation,
        },
        "premise": "A courier risks a crossing to rescue a rival.",
        "synopsis": "The rescued rival opens the port and acknowledges the courier.",
        "story_spine": {"who": "courier", "outcome": "entry earned by rescue"},
        "tags": [TAG] if requested else [],
        "log": [],
        "ids": [],
        "state": {
            "structure_repair_attempts": 1,
            "repair_quotas": {"final_packaging": 2},
            "structure_repair_protocol_attempts": {"v1": 1},
        },
        "after_intent_gate": False,
    }


def _sample(slot, *, verdict="pass", operation=None, run_id=None, scope=SCOPE, counters=0):
    # This fixture is the verified service boundary, not a financial permission.
    rows = [
        {
            "against": TAG,
            "quote": "A courier risks a crossing to rescue a rival.",
            "reason": f"source-bound interpretation {i}",
        }
        for i in range(counters)
    ]
    return {
        "status": "unknown" if verdict == "unknown" else "verified",
        "items": {}
        if verdict == "unknown"
        else {
            TAG: {
                "pass": verdict == "pass",
                "quote": "The rescued rival opens the port and acknowledges the courier.",
            }
        },
        "failed_tags": [TAG] if verdict == "fail" else [],
        "counter_elements": rows,
        "cost_violations": [],
        "revise_direction": "",
        "receipt": {
            "status": "unknown" if verdict == "unknown" else "verified",
            "sample_slot": slot,
            "scope_id": scope,
            "operation_key": operation or f"offline-root-{slot}",
            "llm_run_id": str(run_id or uuid4()),
            "original": {
                "tool_calls": [{"id": f"physical-fixture-{slot}"}],
                "content": "",
                "raw_message": None,
            },
        },
    }


def _objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)


@pytest.fixture
def wire(monkeypatch):
    calls, edits, guards = [], [], []
    planned = []

    async def judge(session, settings, **kwargs):
        calls.append(deepcopy(kwargs))
        # Producer-real input admission is essential: permissive mocks hid the
        # model_dump(tuple) versus strict JSON mismatch at the new boundary.
        submission.build_intent_review_request(**kwargs)
        item = planned.pop(0) if planned else _sample(kwargs["sample_slot"])
        if isinstance(item, Exception):
            raise item
        return deepcopy(item)

    async def edit(session, settings, **kwargs):
        edits.append(deepcopy(kwargs))
        candidate = {
            "premise": "A courier rescues a rival and receives public recognition.",
            "synopsis": "The rescued rival opens the port and acknowledges the courier.",
            "story_spine": {"who": "courier", "outcome": "entry earned by rescue"},
        }
        return candidate, [uuid4()]

    async def guard(session, settings, **kwargs):
        guards.append(deepcopy(kwargs))
        return {"passed": True, "llm_run_id": str(uuid4()), "regressions": []}

    monkeypatch.setattr(submission, "evaluate_intent_alignment_sample", judge)
    monkeypatch.setattr(conception_repair_guard, "review_conception_repair", guard)
    monkeypatch.setattr(budget, "current_execution_scope", lambda: SCOPE)
    run = _caller_region()
    run.__globals__["_llm_call_json"] = edit
    return SimpleNamespace(run=run, planned=planned, calls=calls, edits=edits, guards=guards)


async def test_independent_verified_pair_retained_no_edit_or_quota_change(wire):
    env = _environment()
    original = deepcopy(env)
    samples = [_sample(0), _sample(1)]
    wire.planned.extend(samples)
    result = await wire.run(env)
    assert [c["sample_slot"] for c in wire.calls] == [0, 1]
    assert [c["phase"] for c in wire.calls] == ["initial", "initial"]
    assert not wire.edits and not wire.guards
    assert result["premise"] == original["premise"]
    assert env["state"] == original["state"]
    audit = next(e for e in env["log"] if e.get("agent") == "intent_alignment")
    assert audit["votes"] == samples and audit["separate_paid_operations_verified"] is True
    assert set(result["ids"]) == {UUID(s["receipt"]["llm_run_id"]) for s in samples}


@pytest.mark.parametrize(
    "failure", ["duplicate_operation", "duplicate_run", "one_unknown", "wrong_scope"]
)
async def test_unavailable_or_nonindependent_pair_never_enters_editor(wire, failure):
    env = _environment()
    quota = deepcopy(env["state"])
    a, b = _sample(0), _sample(1)
    if failure == "duplicate_operation":
        b["receipt"]["operation_key"] = a["receipt"]["operation_key"]
    elif failure == "duplicate_run":
        b["receipt"]["llm_run_id"] = a["receipt"]["llm_run_id"]
    elif failure == "one_unknown":
        b = _sample(1, verdict="unknown")
    else:
        b["receipt"]["scope_id"] = "conception:other"
    wire.planned.extend([a, b])
    with pytest.raises(ConceptContractError, match="CONCEPTION_INTENT_EVIDENCE_UNRESOLVED") as exc:
        await wire.run(env)
    assert len(wire.calls) == 2 and not wire.edits and not wire.guards
    assert not env["after_intent_gate"] and env["state"] == quota
    assert any(obj.get("votes") == [a, b] for obj in _objects(exc.value.conception_log))


async def test_no_requested_intent_or_cost_does_not_invent_a_review(wire):
    env = _environment(requested=False, explicit_cost=False)
    original = deepcopy(env)
    result = await wire.run(env)
    assert wire.calls == wire.edits == wire.guards == []
    assert env["state"] == original["state"] and result["premise"] == original["premise"]
    scope = next(e for e in env["log"] if e.get("agent") == "intent_alignment_scope")
    assert scope["will_run"] is False and scope["cost_checked"] is False


async def test_unselected_cost_does_not_block_other_requested_intent_or_invent_cost_check(wire):
    env = _environment(explicit_cost=False)
    creation = deepcopy(env["ctx"]["creation_intent_contract"])
    await wire.run(env)
    assert len(wire.calls) == 2 and not wire.edits and not wire.guards
    assert env["ctx"]["creation_intent_contract"] == creation
    scope = next(e for e in env["log"] if e.get("agent") == "intent_alignment_scope")
    assert scope["will_run"] is True and scope["cost_checked"] is False


async def test_single_verified_objection_is_audit_not_double_conviction(wire):
    env = _environment()
    a, b = _sample(0, verdict="fail", counters=9), _sample(1)
    wire.planned.extend([a, b])
    await wire.run(env)
    assert not wire.edits and not wire.guards
    audit = next(e for e in env["log"] if e.get("agent") == "intent_alignment")
    assert audit["convicted_tags"] == []
    assert audit["unconfirmed_counter_elements"] == a["counter_elements"]
    assert audit["votes"] == [a, b]


async def test_source_admission_failure_is_unknown_before_editor(wire):
    env = _environment()
    env["ctx"]["creation_intent_contract"]["llm_run_ids"] = ["untrusted history"]
    quota = deepcopy(env["state"])
    with pytest.raises(ConceptContractError, match="CONCEPTION_INTENT_EVIDENCE_UNRESOLVED"):
        await wire.run(env)
    assert not wire.edits and not wire.guards and env["state"] == quota
    assert not env["after_intent_gate"]


async def test_control_resolution_failure_also_fails_closed_before_any_paid_work(wire):
    env = _environment()

    def unavailable(_ctx):
        raise ValueError("control source unavailable")

    wire.run.__globals__["resolve_cost_style"] = unavailable
    with pytest.raises(ConceptContractError, match="CONCEPTION_INTENT_EVIDENCE_UNRESOLVED"):
        await wire.run(env)
    assert not wire.calls and not wire.edits and not env["after_intent_gate"]


async def test_first_paid_receipt_survives_later_sample_exception(wire):
    env = _environment()
    first = _sample(0)
    wire.planned.extend([first, ValueError("second source unavailable")])
    with pytest.raises(ConceptContractError, match="CONCEPTION_INTENT_EVIDENCE_UNRESOLVED") as exc:
        await wire.run(env)
    assert not wire.edits and not wire.guards
    assert first["receipt"] in list(_objects(exc.value.conception_log))
    assert UUID(first["receipt"]["llm_run_id"]) in env["ids"]


async def test_budget_stop_propagates_without_retry_or_prose(wire):
    env = _environment()
    wire.planned.append(budget.ExecutionBudgetError("offline_stop", SCOPE))
    with pytest.raises(budget.ExecutionBudgetError):
        await wire.run(env)
    assert len(wire.calls) == 1 and not wire.edits and not env["after_intent_gate"]


async def test_double_failure_recheck_is_single_slot_and_regression_guard_required(wire):
    env = _environment()
    original_quota = deepcopy(env["state"])
    wire.planned.extend(
        [_sample(0, verdict="fail", counters=1), _sample(1, verdict="fail", counters=1), _sample(0)]
    )
    result = await wire.run(env)
    assert [(c["phase"], c["sample_slot"]) for c in wire.calls] == [
        ("initial", 0),
        ("initial", 1),
        ("recheck", 0),
    ]
    assert len(wire.edits) == len(wire.guards) == 1
    assert result["premise"] != env["premise"]
    assert env["state"] == original_quota
    joint = next(e for e in env["log"] if e.get("agent") == "intent_repair_joint_review")
    assert joint["intent_report"]["status"] == "verified"
    assert joint["regression_report"]["passed"] is True


async def test_unknown_recheck_does_not_adopt_or_call_regression_guard(wire):
    env = _environment()
    wire.planned.extend(
        [
            _sample(0, verdict="fail", counters=1),
            _sample(1, verdict="fail", counters=1),
            _sample(0, verdict="unknown"),
        ]
    )
    with pytest.raises(ConceptContractError, match="CONCEPTION_INTENT_REPAIR_UNRESOLVED") as exc:
        await wire.run(env)
    assert len(wire.edits) == 1 and not wire.guards and not env["after_intent_gate"]
    joint = next(
        e for e in exc.value.conception_log if e.get("agent") == "intent_repair_joint_review"
    )
    assert joint["intent_report"]["status"] == "unknown"
    assert not any(e.get("agent") == "intent_repair" and e.get("adopted") for e in env["log"])
