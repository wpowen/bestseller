from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import planner


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_final_source_bound_review_rejects_post_review_personhood_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passing early review cannot authorize a later changed Cast payload."""

    project = SimpleNamespace(metadata_json={})
    monkeypatch.setattr(planner, "_source_bound_cast_enabled", lambda _: True)
    reviewed_payloads: list[dict[str, object]] = []

    async def fake_review(*args: object, **kwargs: object) -> dict[str, object]:
        candidate = kwargs["cast_derived"]
        assert isinstance(candidate, dict)
        reviewed_payloads.append(candidate)
        if candidate.get("protagonist", {}).get("unapproved_mechanism"):
            return {"decision": "revise", "reason": "core mechanism drift"}
        return {"decision": "pass", "llm_run_id": str(uuid4())}

    monkeypatch.setattr(planner, "_review_source_bound_expansion", fake_review)
    baseline = {"protagonist": {"name": "沈砚", "goal": "查清真相"}}
    candidate = {"protagonist": {"name": "沈砚", "goal": "查清真相"}}
    run_ids: list[object] = []

    await planner._require_final_source_bound_cast_review(
        None,
        SimpleNamespace(),
        project=project,
        book_spec={},
        world_baseline={},
        cast_baseline=baseline,
        world_derived={},
        cast_derived=candidate,
        workflow_run_id=uuid4(),
        llm_run_ids=run_ids,
    )
    assert len(reviewed_payloads) == 1

    # Simulate a later personhood/repair pass adding an unauthorized ability
    # premise after the first review.
    candidate["protagonist"]["unapproved_mechanism"] = "第二能力来源"
    with pytest.raises(planner.PlannerFallbackError, match="semantic review blocked"):
        await planner._require_final_source_bound_cast_review(
            None,
            SimpleNamespace(),
            project=project,
            book_spec={},
            world_baseline={},
            cast_baseline=baseline,
            world_derived={},
            cast_derived=candidate,
            workflow_run_id=uuid4(),
            llm_run_ids=run_ids,
        )
    assert len(reviewed_payloads) == 2
    assert reviewed_payloads[-1]["protagonist"]["unapproved_mechanism"] == "第二能力来源"


@pytest.mark.asyncio
async def test_final_cast_approval_helper_imports_only_reviewed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(metadata_json={})
    monkeypatch.setattr(planner, "_source_bound_cast_enabled", lambda _: True)
    imported: list[dict[str, object]] = []
    reports: list[dict[str, object]] = []

    async def fake_import(*args: object, **kwargs: object) -> object:
        payload = kwargs["payload"] if "payload" in kwargs else args[-1]
        content = payload.content
        imported.append(content)
        return SimpleNamespace(id=uuid4(), version_no=1)

    async def fake_review(*args: object, **kwargs: object) -> dict[str, object]:
        candidate = kwargs["cast_derived"]
        report = {
            "decision": "pass",
            "llm_run_id": str(uuid4()),
            "candidate_hash": planner._approved_payload_hash({"world_spec": {}, "cast_spec": candidate}),
        }
        reports.append(report)
        return report

    monkeypatch.setattr(planner, "import_planning_artifact", fake_import)
    monkeypatch.setattr(planner, "_review_source_bound_expansion", fake_review)
    candidate = {"protagonist": {"name": "沈砚", "flaw": "不肯求助"}}
    await planner._approve_final_source_bound_cast_artifact(
        None,
        SimpleNamespace(),
        project=project,
        project_slug="demo",
        book_spec={},
        world_baseline={},
        cast_baseline={"protagonist": {"name": "沈砚"}},
        world_derived={},
        cast_derived=candidate,
        workflow_run_id=uuid4(),
        llm_run_ids=[],
    )
    assert len(imported) == 1
    assert imported[0]["protagonist"] == candidate["protagonist"]
    assert imported[0]["_meta"]["source_bound_expansion"] == "reviewed"
    assert imported[0]["_meta"]["source_bound_expansion_review"] == "passed"
    assert imported[0]["_meta"]["source_bound_expansion_review_receipt"]["decision"] == "pass"
    assert planner._approved_payload_hash(
        {
            "world_spec": {},
            "cast_spec": planner._without_planning_meta(imported[0]),
        }
    ) == reports[0]["candidate_hash"]


@pytest.mark.asyncio
async def test_final_cast_approval_helper_rejects_before_any_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(metadata_json={})
    monkeypatch.setattr(planner, "_source_bound_cast_enabled", lambda _: True)
    import_calls = 0

    async def fake_import(*args: object, **kwargs: object) -> object:
        nonlocal import_calls
        import_calls += 1
        return SimpleNamespace(id=uuid4(), version_no=1)

    async def rejecting_review(*args: object, **kwargs: object) -> dict[str, object]:
        return {"decision": "revise", "reason": "post-repair mechanism drift"}

    monkeypatch.setattr(planner, "import_planning_artifact", fake_import)
    monkeypatch.setattr(planner, "_review_source_bound_expansion", rejecting_review)
    with pytest.raises(planner.PlannerFallbackError, match="semantic review blocked"):
        await planner._approve_final_source_bound_cast_artifact(
            None,
            SimpleNamespace(),
            project=project,
            project_slug="demo",
            book_spec={},
            world_baseline={},
            cast_baseline={"protagonist": {"name": "沈砚"}},
            world_derived={},
            cast_derived={"protagonist": {"name": "沈砚", "flaw": "漂移"}},
            workflow_run_id=uuid4(),
            llm_run_ids=[],
        )
    assert import_calls == 0


@pytest.mark.asyncio
async def test_final_cast_approval_allows_core_preserved_draft_with_needs_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(metadata_json={})
    monkeypatch.setattr(planner, "_source_bound_cast_enabled", lambda _: True)
    imported: list[dict[str, object]] = []

    async def fake_import(*args: object, **kwargs: object) -> object:
        payload = kwargs["payload"] if "payload" in kwargs else args[-1]
        imported.append(payload.content)
        return SimpleNamespace(id=uuid4(), version_no=1)

    async def draft_review(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "decision": "uncertain",
            "core_preserved": True,
            "core_conflicts": [],
            "draft_eligible": True,
            "llm_run_id": str(uuid4()),
            "raw_response_hash": "r" * 64,
            "authority_snapshot_hash": "a" * 64,
            "candidate_hash": "c" * 64,
            "authority_snapshot_hash_echo": "a" * 64,
            "candidate_hash_echo": "c" * 64,
            "changed_fields": ["cast.protagonist"],
            "source_evidence": [{"field": "cast.protagonist", "quote": "沈砚", "source_relation": "approved"}],
            "candidate_evidence": [{"field": "cast.protagonist", "quote": "沈砚", "source_relation": "preserved"}],
            "evidence_verified": True,
            "feedback": "具体化仍需正文质量复核",
        }

    monkeypatch.setattr(planner, "import_planning_artifact", fake_import)
    monkeypatch.setattr(planner, "_review_source_bound_expansion", draft_review)
    await planner._approve_final_source_bound_cast_artifact(
        None,
        SimpleNamespace(),
        project=project,
        project_slug="demo",
        book_spec={},
        world_baseline={},
        cast_baseline={"protagonist": {"name": "沈砚"}},
        world_derived={},
        cast_derived={"protagonist": {"name": "沈砚", "flaw": "不肯求助"}},
        workflow_run_id=uuid4(),
        llm_run_ids=[],
    )
    assert imported[0]["_meta"]["source_bound_expansion"] == "draft_eligible"
    assert imported[0]["_meta"]["source_bound_expansion_review"] == "needs_review"
    assert imported[0]["_meta"]["source_bound_expansion_draft_eligible"] is True


def test_source_bound_review_is_after_all_cast_repairs_before_final_cast_import() -> None:
    """Both planning entry points gate the final payload immediately before approval."""

    for function in (planner.generate_novel_plan, planner.generate_foundation_plan):
        source = inspect.getsource(function)
        review_pos = source.find("await _approve_final_source_bound_cast_artifact(")
        artifact_record_pos = source.find("artifact_records.append(", review_pos)
        assert review_pos >= 0
        assert artifact_record_pos >= 0
        assert review_pos < artifact_record_pos
        assert source.count("await _approve_final_source_bound_cast_artifact(") == 1


def test_final_approval_follows_every_cast_payload_assignment() -> None:
    for function in (planner.generate_novel_plan, planner.generate_foundation_plan):
        tree = ast.parse(inspect.getsource(function))
        writes = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "cast_spec_payload"
                          for target in node.targets)]
        approvals = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name)
                     and node.func.id == "_approve_final_source_bound_cast_artifact"]
        assert len(approvals) == 1
        # A previously reviewed row is consumed through the existing strict
        # equality checker. This is the sole permitted post-approval write;
        # every actual repair/selection must still precede final approval.
        post = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and node.lineno > approvals[0]
                and any(isinstance(target, ast.Name) and target.id == "cast_spec_payload"
                        for target in node.targets)]
        assert len(post) == 1
        value = post[0].value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
        assert value.func.id == "_approved_cast_payload_for_downstream"
        assert [ast.unparse(arg) for arg in value.args] == ["cast_spec_payload", "cast_artifact"]
        assert not value.keywords
        actual_repairs = [line for line in writes if line != post[0].lineno]
        assert actual_repairs and max(actual_repairs) < approvals[0]
