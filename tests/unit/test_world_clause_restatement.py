"""A semantic proposal is bounded evidence, never a deletion approval."""
# ruff: noqa: RUF001 -- Exact Chinese punctuation is part of the source fixture.

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_world_authority_cli import build_sources
from test_world_recompile_candidate import build, project

from bestseller.services import world_clause_restatement as restatement
from bestseller.services.story_rebuild_workspace import source_content_hash
from bestseller.services.world_authority import WorldAuthorityAuditError
from bestseller.services.world_cost_claims import cost_clause_index, reconcile_cost_claims
from bestseller.services.world_recompile_candidate import verify_world_recompile_candidate

pytestmark = pytest.mark.unit


def _bind_authored_world(p):
    """Declare literal author input and its canonical source, never a cache approval."""
    text = p.metadata_json["writing_profile"]["world"]["power_system_style"]
    p.metadata_json["creation_intent_contract"] = {
        "story_enhancers": {"cost_style": "minimal"},
        "field_sources": {"story_enhancers": "explicit", "hook_spec": "explicit"},
        "hook_spec": {
            "_story_enhancer_input_sources": {"cost_style": "explicit"},
            "hard_constraints": [text],
        },
    }
    content = {
        "writing_profile": deepcopy(p.metadata_json["writing_profile"]),
        "world_spec": {"power_system": {"hard_limits": text}},
    }
    p._source_bound_planning_canonical = {
        "id": "authored-world-fixture",
        "content_hash": source_content_hash(content),
        "content": content,
    }
    p.metadata_json["creation_canonical_snapshot_id"] = p._source_bound_planning_canonical["id"]
    p.metadata_json["creation_canonical_snapshot_hash"] = source_content_hash(content)


def inputs():
    p = project()
    p.metadata_json["writing_profile"]["world"]["power_system_style"] = (
        "Ink takes an hour;Overreach drains resolve and ruins the seal, without rank loss;"
        "Paper is consumed;"
    )
    _bind_authored_world(p)
    parent = build(p)
    sources = build_sources(p, world_content=parent["candidate_world"])
    clauses = cost_clause_index(sources)
    target = next(c for c in clauses if c["quote"].startswith("Overreach"))
    rows = []
    for clause in clauses:
        fact = {
            "effect": "no_cost_fact",
            "actor": "not_applicable",
            "usage_scope": "not_applicable",
            "polarity": "not_applicable",
        }
        if clause == target:
            fact = {
                "effect": "self_weakening",
                "actor": "protagonist",
                "usage_scope": "overreach",
                "polarity": "incurs",
            }
        rows.append({"clause_id": clause["clause_id"], "facts": [fact]})
    from bestseller.services.world_authority import world_authority_source_hash

    review = {
        "complete": True,
        "source_hash": world_authority_source_hash(sources),
        "clauses": rows,
    }
    diagnostic = reconcile_cost_claims([review, review], sources=sources)
    diagnostic.update(
        source_candidate_id="parent",
        source_candidate_hash=parent["candidate_hash"],
        source_input_hash=parent["input_hash"],
    )
    payload = {
        "source_hash": diagnostic["source_hash"],
        "clause_id": target["clause_id"],
        "replacement": "Overreach may ruin the seal, without rank loss;",
    }
    return {
        "parent": parent,
        "parent_report_id": "parent",
        "sources": sources,
        "diagnostic": diagnostic,
        "diagnostic_report_id": "diagnostic",
    }, payload


def test_restatement_recompiles_only_one_upstream_clause_without_mutation():
    kwargs, payload = inputs()
    original = deepcopy(kwargs)
    result = restatement.build_world_clause_restatement(payload, **kwargs)
    assert kwargs == original
    assert result["status"] == "needs_review"
    assert result["production_ready"] is result["promotion_allowed"] is False
    compiled = result["compiled_candidate"]
    verify_world_recompile_candidate(compiled)
    expected = deepcopy(kwargs["parent"]["frozen_inputs"])
    text = expected["metadata"]["writing_profile"]["world"]["power_system_style"]
    text = text.replace(result["target"]["quote"], payload["replacement"])
    expected["metadata"]["writing_profile"]["world"]["power_system_style"] = text
    assert compiled["frozen_inputs"] == expected
    assert compiled["candidate_world"]["power_system"]["hard_limits"] == text
    assert compiled["candidate_book"] == kwargs["parent"]["candidate_book"]
    restatement.verify_world_clause_restatement(result, **kwargs)
    with pytest.raises(ValueError, match="VERSION_MISMATCH"):
        verify_world_recompile_candidate(result)


@pytest.mark.parametrize(
    "case",
    [
        "source",
        "candidate",
        "input",
        "parent_id",
        "hash",
        "clause",
        "extra",
        "empty",
        "noop",
        "terminator",
        "aliases",
        "multiple",
        "fake_conflicts",
    ],
)
def test_restatement_rejects_stale_unbound_or_unbounded_changes(case):
    kwargs, payload = inputs()
    if case == "source":
        kwargs["sources"]["world"] = {"power_system": {"hard_limits": "other"}}
    elif case in {"candidate", "input"}:
        key = "source_candidate_hash" if case == "candidate" else "source_input_hash"
        kwargs["diagnostic"][key] = "stale"
    elif case == "parent_id":
        kwargs["parent_report_id"] = "unrelated"
    elif case == "hash":
        payload["source_hash"] = "stale"
    elif case == "clause":
        payload["clause_id"] = "other"
    elif case == "extra":
        payload["approved"] = True
    elif case == "empty":
        payload["replacement"] = " "
    elif case == "noop":
        payload["replacement"] = kwargs["diagnostic"]["conflicts"][0]["quote"]
    elif case == "terminator":
        payload["replacement"] += "More."
    elif case == "aliases":
        from bestseller.services.story_rebuild_workspace import source_content_hash

        kwargs["parent"]["frozen_inputs"]["metadata"]["writing_profile"]["world"][
            "power_system_style"
        ] = "different"
        kwargs["parent"]["input_hash"] = source_content_hash(kwargs["parent"]["frozen_inputs"])
        kwargs["diagnostic"]["source_input_hash"] = kwargs["parent"]["input_hash"]
    else:
        for review in kwargs["diagnostic"]["reviews"]:
            target = next(c for c in review["clauses"] if c["clause_id"] == payload["clause_id"])
            if case == "multiple":
                other = next(c for c in review["clauses"] if c["clause_id"] != payload["clause_id"])
                other["facts"] = deepcopy(target["facts"])
            else:
                target["facts"][0]["effect"] = "material_consumption"
    with pytest.raises(ValueError):
        restatement.build_world_clause_restatement(payload, **kwargs)


def test_restatement_receipt_cannot_be_tampered_or_inherit_approval():
    kwargs, payload = inputs()
    result = restatement.build_world_clause_restatement(payload, **kwargs)
    for key, value in [
        ("production_ready", True),
        ("replacement", "new ability;"),
        ("diagnostic_hash", "different"),
    ]:
        altered = {**result, key: value}
        with pytest.raises(ValueError):
            restatement.verify_world_clause_restatement(altered, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("disjoint", [False, True])
@pytest.mark.parametrize(
    "case", ["valid", "bad_contract", "bad_editor", "fake_source", "locked", "unmodified"]
)
async def test_anchored_protocol_freezes_source_before_editor_and_does_not_retry(
    monkeypatch, case, disjoint
):
    import json

    from bestseller.services.world_preservation_contract import PRESERVATION_AXES

    kwargs, payload = inputs()
    quotes = {
        "actor_scope": [],
        "trigger": ["Overreach"],
        "failure_outcome": ["the seal"],
        "existing_protection": ["without rank loss"],
        "unaffected_mechanism": [],
    }
    source_payload = {
        "source_hash": payload["source_hash"],
        "clause_id": payload["clause_id"],
        "obligations": [
            {
                "axis": axis,
                "presence": "present" if quotes[axis] else "absent",
                "before_quotes": quotes[axis],
                "reason": "Source only",
            }
            for axis in PRESERVATION_AXES
        ],
    }
    if disjoint:
        source_payload["amendment_quotes"] = ["drains resolve"]
    if case == "bad_contract":
        source_payload["obligations"][1]["before_quotes"] = ["forged"]
    if case == "locked":
        source_payload["obligations"][1]["before_quotes"] = [restatement._target(**kwargs)["quote"]]
    if case == "bad_editor":
        payload["replacement"] = "Overreach may ruin the seal;"
    if case == "unmodified":
        payload["replacement"] = (
            "Overreach drains resolve and may ruin the seal, without rank loss;"
        )
    calls = []

    async def complete(*args):
        request = args[-1]
        calls.append(request)
        return SimpleNamespace(
            content=json.dumps(source_payload if len(calls) == 1 else payload),
            provider="openai",
            fallback_used=case == "fake_source",
            finish_reason="stop",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(restatement, "complete_text", complete)
    if case == "valid" or (case == "unmodified" and not disjoint):
        result = await restatement.propose_anchored_world_restatement(
            None,
            None,
            project_id=uuid4(),
            disjoint_amendments=disjoint,
            **kwargs,
        )
        assert result["version"] == (
            restatement.DISJOINT_RESTATEMENT_VERSION
            if disjoint
            else restatement.ANCHORED_RESTATEMENT_VERSION
        )
        assert result["literal_anchor_check"]["status"] == "literal_anchors_present"
        assert result["promotion_allowed"] is False
        restatement.verify_world_clause_restatement(result, **kwargs)
        if disjoint:
            for amendments in (["without rank loss"], ["forged"], None):
                tampered = deepcopy(result)
                tampered["preservation_generation_receipt"]["raw_response"] = json.dumps(
                    {**source_payload, "amendment_quotes": amendments}
                )
                with pytest.raises(ValueError):
                    restatement.verify_world_clause_restatement(tampered, **kwargs)
        result["preservation_generation_receipt"]["raw_response"] = "{}"
        with pytest.raises(ValueError):
            restatement.verify_world_clause_restatement(result, **kwargs)
    else:
        with pytest.raises(WorldAuthorityAuditError) as error:
            await restatement.propose_anchored_world_restatement(
                None,
                None,
                project_id=uuid4(),
                disjoint_amendments=disjoint,
                **kwargs,
            )
        assert error.value.diagnostic["preservation_generation_receipt"]["raw_response"]
        if case == "bad_editor":
            assert "MISSING_ANCHORS:existing_protection" in error.value.diagnostic["error"]
            assert error.value.diagnostic["generation_receipt"]["raw_response"]
        if case == "unmodified":
            assert error.value.diagnostic["error"] == "RESTATEMENT_UNMODIFIED_CAUSE"
        if case == "locked" and disjoint:
            assert error.value.diagnostic["error"] == "PRESERVATION_KEEP_AMEND_OVERLAP"
    assert len(calls) == (1 if case in {"bad_contract", "fake_source", "locked"} else 2)
    assert calls[0].logical_role == "critic"
    assert len(calls[0].prompt_version) <= 32
    if len(calls) == 2:
        assert calls[1].logical_role == "editor"
        assert "preservation_contract" in calls[1].user_prompt


def test_restatement_rejects_compiler_changes_to_other_world_fields(monkeypatch):
    kwargs, payload = inputs()
    compiler = restatement.build_world_recompile_candidate

    def changed(*args, **kwargs):
        result = compiler(*args, **kwargs)
        result["candidate_world"]["power_system"]["name"] = "unrelated new system"
        return result

    monkeypatch.setattr(restatement, "build_world_recompile_candidate", changed)
    with pytest.raises(ValueError, match="COMPILER_SCOPE_CHANGED"):
        restatement.build_world_clause_restatement(payload, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "fallback", "truncated", "invalid", "stale"])
async def test_one_editor_call_with_raw_receipt_no_retry(monkeypatch, case):
    kwargs, payload = inputs()
    calls = []
    import json

    async def complete(*args):
        calls.append(args[-1])
        return SimpleNamespace(
            content="bad-json" if case == "invalid" else json.dumps(payload),
            fallback_used=case == "fallback",
            finish_reason="length" if case == "truncated" else "stop",
            provider="openai",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(restatement, "complete_text", complete)
    if case == "stale":
        kwargs["diagnostic"]["source_input_hash"] = "old"
    if case == "valid":
        result = await restatement.propose_world_clause_restatement(
            None, None, project_id=uuid4(), **kwargs
        )
        assert result["generation_receipt"]["raw_response"] == json.dumps(payload)
        assert result["generation_receipt"]["fallback_used"] is False
        assert result["production_ready"] is False
        restatement.verify_world_clause_restatement(result, **kwargs)
        result["generation_receipt"]["raw_response"] = "{}"
        with pytest.raises(ValueError, match="GENERATION_RECEIPT_MISMATCH"):
            restatement.verify_world_clause_restatement(result, **kwargs)
    else:
        with pytest.raises(ValueError if case == "stale" else WorldAuthorityAuditError):
            await restatement.propose_world_clause_restatement(
                None, None, project_id=uuid4(), **kwargs
            )
    assert len(calls) == (0 if case == "stale" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", [
        "world-restate",
        "world-restate-anchored",
        "world-restate-disjoint",
        "world-restate-atomic",
    ]
)
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "dry_run",
        "active",
        "not_halted",
        "stale",
        "unbound",
        "duplicate",
        "concurrent",
        "commit_failed",
    ],
)
async def test_cli_restatement_is_bound_halted_idempotent_and_commit_checked(
    monkeypatch, capsys, case, mode
):
    from test_world_authority_cli import (
        _CLI,
        _artifact,
        _patch_run_dependencies,
        _project,
        _SequencedSession,
    )

    from bestseller.infra.db.models import ReviewReportModel
    from bestseller.services.world_recompile_candidate import (
        build_world_recompile_candidate,
        revise_world_recompile_candidate,
    )

    p = _project()
    p.metadata_json = project().metadata_json
    p.metadata_json["writing_profile"]["world"]["power_system_style"] = (
        "血符体系：制作需要纸；越阶后手抖乏力；不会退阶。"
    )
    p.language = "zh-CN"
    _bind_authored_world(p)
    artifact = _artifact(p)
    premise = SimpleNamespace(id=uuid4(), status="approved", content={"premise": "原设定"})
    parent = build_world_recompile_candidate(
        p,
        premise=premise.content["premise"],
        source_artifact=artifact,
        premise_artifact_id=str(premise.id),
    )
    parent_id, candidate_id, diagnostic_id = uuid4(), uuid4(), uuid4()
    before = parent["frozen_inputs"]["metadata"]["writing_profile"]["world"]["power_system_style"]
    revision = revise_world_recompile_candidate(
        parent,
        parent_report_id=str(parent_id),
        edit={
            "path": ["writing_profile", "world", "power_system_style"],
            "before": before,
            "after": before.replace("手抖", ""),
            "rationale": "pending semantic review",
        },
    )
    sources = build_sources(p, world_content=revision["candidate_world"])
    from bestseller.services.world_authority import world_authority_source_hash

    clauses = cost_clause_index(sources)
    target = next(c for c in clauses if c["quote"].startswith("越阶后"))
    rows = []
    for c in clauses:
        fact = {
            "effect": "no_cost_fact",
            "actor": "not_applicable",
            "usage_scope": "not_applicable",
            "polarity": "not_applicable",
        }
        if c == target:
            fact = {
                "effect": "self_weakening",
                "actor": "protagonist",
                "usage_scope": "overreach",
                "polarity": "incurs",
            }
        rows.append({"clause_id": c["clause_id"], "facts": [fact]})
    r = {"complete": True, "source_hash": world_authority_source_hash(sources), "clauses": rows}
    diagnostic = reconcile_cost_claims([r, r], sources=sources)
    diagnostic.update(
        source_candidate_id=str(candidate_id),
        source_candidate_hash=revision["candidate_hash"],
        source_input_hash=revision["input_hash"],
    )
    session = _SequencedSession(
        [
            p,
            SimpleNamespace(structured_output=revision),
            SimpleNamespace(structured_output=parent),
            SimpleNamespace(structured_output=revision),
            None if case == "unbound" else SimpleNamespace(structured_output=diagnostic),
            int(case == "active"),
            case != "concurrent",
            SimpleNamespace(id=uuid4()) if case == "duplicate" else None,
        ]
    )
    scope = _patch_run_dependencies(monkeypatch, session, artifact)
    scope.fail_commit = case == "commit_failed"

    async def latest(*args, **kwargs):
        return artifact if kwargs["artifact_type"].value == "world_spec" else premise

    async def control(*args):
        return SimpleNamespace(halted=case != "not_halted")

    calls = []

    async def propose(*args, **kwargs):
        assert kwargs.pop("atomic_facts", False) == (mode == "world-restate-atomic")
        assert kwargs.pop("disjoint_amendments", False) == (
            mode in {"world-restate-disjoint", "world-restate-atomic"}
        )
        calls.append(kwargs)
        kwargs.pop("project_id")
        return restatement.build_world_clause_restatement(
            {
                "source_hash": diagnostic["source_hash"],
                "clause_id": target["clause_id"],
                "replacement": "An overreach attempt may fail;".replace(";", target["quote"][-1]),
            },
            **kwargs,
        )

    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    monkeypatch.setattr(_CLI, "load_control_state", control)
    monkeypatch.setattr(restatement, "propose_world_clause_restatement", propose)
    monkeypatch.setattr(restatement, "propose_anchored_world_restatement", propose)
    if case == "stale":
        p.title += "changed"
    kwargs = {
        "slug": p.slug,
        "mode": mode,
        "execute": case != "dry_run",
        "candidate_id": candidate_id,
        "diagnostic_id": diagnostic_id,
    }
    if case in {"stale", "unbound", "commit_failed"}:
        with pytest.raises((ValueError, RuntimeError)):
            await _CLI.run(**kwargs)
        assert capsys.readouterr().out == ""
        assert len(calls) == int(case == "commit_failed")
        return
    code = await _CLI.run(**kwargs)
    if case == "valid":
        assert code == 4 and len(calls) == 1 and len(session.added) == 1
        saved = session.added[0]
        assert isinstance(saved, ReviewReportModel)
        assert saved.target_type == "review_report" and saved.target_id == candidate_id
        assert saved.reviewer_type == (
            "world_atomic_clause_restatement_candidate"
            if mode == "world-restate-atomic"
            else "world_disjoint_clause_restatement_candidate"
            if mode == "world-restate-disjoint"
            else "world_anchored_clause_restatement_candidate"
            if mode == "world-restate-anchored"
            else "world_clause_restatement_candidate"
        )
        assert saved.verdict == "needs_review"
        restatement.verify_world_clause_restatement(saved.structured_output, **calls[0])
    else:
        assert calls == [] and session.added == []
        assert code == (0 if case == "dry_run" else 4 if case == "duplicate" else 3)
