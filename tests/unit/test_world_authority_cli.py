from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel, ReviewReportModel
from bestseller.services.story_enhancers import COST_STYLE_DEFAULT

_SPEC = importlib.util.spec_from_file_location(
    "audit_world_authority_cli", Path(__file__).parents[2] / "scripts" / "audit_world_authority.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_CLI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CLI)

build_sources = _CLI.build_sources
production_guard_status = _CLI.production_guard_status
safe_summary = _CLI.safe_summary

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["ok", "stale", "tampered", "active", "commit_failed"])
async def test_cost_claims_reviews_selected_revision_not_old_world(monkeypatch, capsys, case):
    from test_world_recompile_candidate import project as sample_project

    from bestseller.services import world_cost_claims
    from bestseller.services.world_recompile_candidate import (
        build_world_recompile_candidate,
        revise_world_recompile_candidate,
    )

    project = _project()
    project.metadata_json = sample_project().metadata_json
    project.language = "zh-CN"
    artifact = _artifact(project)
    premise = SimpleNamespace(id=uuid4(), status="approved", content={"premise": "纪年开符铺。"})
    parent = build_world_recompile_candidate(
        project, premise=premise.content["premise"], source_artifact=artifact,
        premise_artifact_id=str(premise.id),
    )
    parent_report = ReviewReportModel(structured_output=parent)
    parent_report.id = uuid4()
    old = parent["frozen_inputs"]["metadata"]["writing_profile"]["world"]["power_system_style"]
    revision = revise_world_recompile_candidate(
        parent, parent_report_id=str(parent_report.id),
        edit={"path": ["writing_profile", "world", "power_system_style"],
              "before": old, "after": old.replace("手抖", ""), "rationale": "pending review"},
    )
    revision_report = ReviewReportModel(structured_output=revision)
    revision_report.id = uuid4()
    session = _SequencedSession([project, revision_report, parent_report, int(case == "active")])
    scope = _patch_run_dependencies(monkeypatch, session, artifact)
    scope.fail_commit = case == "commit_failed"
    calls = []

    async def control(*args):
        return SimpleNamespace(halted=True)

    async def latest(*args, **kwargs):
        return artifact if kwargs["artifact_type"].value == "world_spec" else premise

    async def audit(*args, **kwargs):
        calls.append(kwargs)
        return {"version": world_cost_claims.CLAIMS_VERSION, "status": "consistent"}

    monkeypatch.setattr(_CLI, "load_control_state", control)
    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    monkeypatch.setattr(world_cost_claims, "audit_world_cost_claims", audit)
    if case == "stale":
        project.title += "changed"
    if case == "tampered":
        revision["candidate_world"]["power_system"]["hard_limits"] = "forged"
    if case in {"stale", "tampered", "commit_failed"}:
        with pytest.raises((ValueError, RuntimeError)):
            await _CLI.run(slug=project.slug, mode="cost-claims", execute=True,
                           candidate_id=revision_report.id)
        if case != "commit_failed":
            assert calls == [] and session.added == []
        assert capsys.readouterr().out == ""
        return
    code = await _CLI.run(slug=project.slug, mode="cost-claims", execute=True,
                          candidate_id=revision_report.id)
    if case == "active":
        assert code == 3 and calls == [] and session.added == []
        return
    assert code == 4  # consistent cost facts are still not world approval
    assert calls[0]["sources"]["world"] == {
        key: revision["candidate_world"][key] for key in ("power_system", "rules")
    }
    saved = session.added[0]
    assert saved.target_type == "review_report"
    assert saved.target_id == revision_report.id
    assert saved.reviewer_type == "world_candidate_cost_claims"
    assert saved.structured_output["source_candidate_id"] == str(revision_report.id)
    assert saved.structured_output["source_candidate_hash"] == revision["candidate_hash"]
    assert saved.structured_output["promotion_allowed"] is False
    assert saved.verdict == "needs_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["ok", "stale", "active", "not_halted", "commit_failed"])
async def test_world_revise_binds_parent_and_only_records_proposal(monkeypatch, capsys, case):
    from test_world_recompile_candidate import project as sample_project

    from bestseller.services.world_recompile_candidate import (
        build_world_recompile_candidate,
        verify_world_input_revision,
    )

    project = _project()
    project.metadata_json = sample_project().metadata_json
    project.language = "zh-CN"
    artifact = _artifact(project)
    premise = SimpleNamespace(id=uuid4(), status="approved", content={"premise": "纪年开符铺。"})
    parent = build_world_recompile_candidate(
        project, premise=premise.content["premise"], source_artifact=artifact,
        premise_artifact_id=str(premise.id),
    )
    report = ReviewReportModel(structured_output=parent)
    report.id = uuid4()
    session = _SequencedSession([project, report, int(case == "active")])
    scope = _patch_run_dependencies(monkeypatch, session, artifact)
    scope.fail_commit = case == "commit_failed"
    old = parent["frozen_inputs"]["metadata"]["writing_profile"]["world"]["power_system_style"]
    edit = {"path": ["writing_profile", "world", "power_system_style"],
            "before": old, "after": old.replace("手抖", ""), "rationale": "pending review"}
    if case == "stale":
        project.title += "changed"

    async def control(*args):
        return SimpleNamespace(halted=case != "not_halted")

    async def latest(*args, **kwargs):
        return artifact if kwargs["artifact_type"].value == "world_spec" else premise

    monkeypatch.setattr(_CLI, "load_control_state", control)
    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    if case == "commit_failed":
        with pytest.raises(RuntimeError, match="commit failed"):
            await _CLI.run(slug=project.slug, execute=True, mode="world-revise",
                           candidate_id=report.id, revision_edit=edit)
        assert capsys.readouterr().out == ""
        return
    code = await _CLI.run(slug=project.slug, execute=True, mode="world-revise",
                          candidate_id=report.id, revision_edit=edit)
    if case in {"active", "not_halted"}:
        assert code == 3 and session.added == []
        return
    assert code == 4
    assert len(session.added) == 1
    saved = session.added[0]
    assert isinstance(saved, ReviewReportModel)
    assert saved.reviewer_type == "world_input_revision_candidate"
    assert saved.verdict == "needs_review"
    assert saved.target_id == artifact.id
    if case == "stale":
        assert json.loads(capsys.readouterr().out)["status"] == "recorded_failure"
        assert "candidate_world" not in saved.structured_output
    else:
        verify_world_input_revision(saved.structured_output, parent=parent,
                                    parent_report_id=str(report.id))
        assert project.metadata_json["writing_profile"]["world"]["power_system_style"] == old


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"diagnostic_id": "old"}, {"candidate_id": "old"}])
async def test_recompile_does_not_silently_ignore_old_report_selection(kwargs):
    with pytest.raises(ValueError, match="RECOMPILE_FRESH_INPUTS_ONLY"):
        await _CLI.run(slug="x", mode="world-recompile", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("premise_status", ["approved", "draft", "missing"])
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_recompile_records_only_unapproved_candidate(
    monkeypatch, capsys, premise_status, fail_commit
):
    from bestseller.services import world_recompile_candidate as compiler

    project = _project()
    session = _Session(project)
    artifact = _artifact(project)
    scope = _patch_run_dependencies(monkeypatch, session, artifact)
    scope.fail_commit = fail_commit

    async def control(*args, **kwargs):
        return SimpleNamespace(halted=True)

    async def latest(*args, **kwargs):
        if kwargs["artifact_type"].value == "world_spec":
            return artifact
        if premise_status == "missing":
            return None
        return SimpleNamespace(id="premise", status=premise_status, content={"premise": "原文"})

    calls = []

    def build(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "needs_review", "candidate_hash": "candidate",
                "production_ready": False, "promotion_allowed": False, "new_llm_calls": 0}

    monkeypatch.setattr(_CLI, "load_control_state", control)
    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    monkeypatch.setattr(compiler, "build_world_recompile_candidate", build)
    monkeypatch.setattr(compiler, "verify_world_recompile_candidate", lambda result: None)
    if fail_commit:
        with pytest.raises(RuntimeError, match="commit failed"):
            await _CLI.run(slug=project.slug, execute=True, mode="world-recompile")
        assert not scope.committed
        assert capsys.readouterr().out == ""
        return
    assert await _CLI.run(slug=project.slug, execute=True, mode="world-recompile") == 4
    assert scope.committed
    assert len(session.added) == 1
    report = session.added[0]
    assert isinstance(report, ReviewReportModel)
    assert report.reviewer_type == "world_recompile_candidate"
    assert report.verdict == "needs_review"
    assert report.target_id == artifact.id
    assert report.llm_run_id is None
    output = json.loads(capsys.readouterr().out)
    assert output["report_id"] == str(report.id)
    if premise_status == "approved":
        assert calls[0]["premise"] == "原文"
        assert calls[0]["source_artifact"] is artifact
        assert output["candidate_hash"] == "candidate"
    else:
        assert calls == []
        assert output["status"] == "recorded_failure"


def _project() -> ProjectModel:
    project = ProjectModel(
        slug="audit-cli-book",
        title="审计 CLI",
        genre="fantasy",
        target_word_count=10000,
        target_chapters=3,
        status="planning",
        metadata_json={
            "creation_protagonist_name": "纪年",
            "story_enhancers": {"cost_style": "minimal", "wild_concept": False},
            "creation_intent_contract": {
                "story_enhancers": {"cost_style": "minimal", "wild_concept": False},
                "field_sources": {"story_enhancers": "explicit"},
                "hook_spec": {"_story_enhancer_input_sources": {"cost_style": "explicit"}},
            },
        },
    )
    project.id = uuid4()
    return project


def test_build_sources_keeps_creation_contract_and_world_exact() -> None:
    project = _project()
    world = {"rules": ["只允许当前境界"], "power_system": {"tiers": ["一阶"]}}
    sources = build_sources(project, world_content=world)

    assert sources["creation"]["creation_protagonist_name"] == "纪年"
    assert sources["creation"]["cost_style"] == "minimal"
    assert sources["creation"]["cost_style_origin"] == "explicit"
    assert "story_enhancers" not in sources["creation"]
    assert sources["world"] == world
    assert sources["world"] is not world


def test_default_cost_style_is_explicitly_carried() -> None:
    """Retain the historical node ID; omitted costs now carry unknown authority."""
    project = _project()
    project.metadata_json = {}
    sources = build_sources(project, world_content={"rules": "x"})
    assert sources["creation"]["cost_style"] == ""
    assert sources["creation"]["cost_style"] != COST_STYLE_DEFAULT
    assert sources["creation"]["cost_style_origin"] == "default"
    assert "cost_rule" in sources["creation"]
    assert sources["creation"]["cost_rule"] == ""


@pytest.mark.parametrize("origin", [None, "default", "derived", "legacy"])
def test_cost_cache_without_explicit_creation_source_is_not_authority(origin) -> None:
    project = _project()
    creation = project.metadata_json["creation_intent_contract"]
    creation["field_sources"] = {} if origin is None else {"story_enhancers": origin}
    sources = build_sources(project, world_content={"rules": "x"})
    assert sources["creation"]["cost_style"] == ""
    assert sources["creation"]["cost_rule"] == ""


def test_dry_run_summary_contains_ids_and_sizes_only() -> None:
    project = _project()
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="world_spec",
        version_no=4,
        status="approved",
        schema_version="1.0",
        content={"rules": "秘密规则"},
        created_by="test",
    )
    artifact.id = uuid4()
    sources = build_sources(project, world_content=artifact.content)
    summary = safe_summary(slug=project.slug, artifact=artifact, sources=sources)

    assert summary["slug"] == project.slug
    assert summary["artifact_id"] == str(artifact.id)
    assert summary["source_entries"] > 0
    assert "秘密规则" not in str(summary)


@pytest.mark.parametrize(
    ("halted", "active", "allowed", "reason"),
    [
        (False, 0, False, "production_must_be_halted"),
        (True, 1, False, "active_workflows_present"),
        (True, 0, True, "ok"),
    ],
)
def test_execute_guard_requires_pause_and_zero_active_workflows(
    halted: bool, active: int, allowed: bool, reason: str
) -> None:
    assert production_guard_status(halted=halted, active_workflows=active) == (
        allowed,
        reason,
    )


class _Session:
    def __init__(self, project: ProjectModel, *, active: int = 0) -> None:
        self._scalars = [project, active]
        self.added: list[object] = []
        self.flushed = False

    async def scalar(self, _statement: object) -> object:
        return self._scalars.pop(0)

    def add(self, value: object) -> None:
        value.id = uuid4()  # type: ignore[attr-defined]
        self.added.append(value)

    async def flush(self) -> None:
        self.flushed = True


class _SequencedSession(_Session):
    def __init__(self, scalars: list[object]) -> None:
        self._scalars = list(scalars)
        self.added = []
        self.flushed = False


class _SessionScope:
    def __init__(self, session: _Session, *, fail_commit: bool = False) -> None:
        self.session = session
        self.fail_commit = fail_commit
        self.committed = False

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.committed = True


def _artifact(project: ProjectModel) -> PlanningArtifactVersionModel:
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="world_spec",
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"power_system": {"tiers": ["一阶"]}, "rules": ["规则"]},
        created_by="test",
    )
    artifact.id = uuid4()
    return artifact


def _patch_run_dependencies(monkeypatch: pytest.MonkeyPatch, session: _Session, artifact: object):
    scope = _SessionScope(session)
    monkeypatch.setattr(_CLI, "load_settings", lambda: object())
    monkeypatch.setattr(_CLI, "session_scope", lambda _settings: scope)

    async def latest(*args, **kwargs):
        return artifact

    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    return scope


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["source", "world-recompile"])
async def test_run_dry_run_does_not_call_audit_or_add_report(monkeypatch, capsys, mode):
    project = _project()
    session = _Session(project)
    artifact = _artifact(project)
    scope = _patch_run_dependencies(monkeypatch, session, artifact)
    calls: list[object] = []
    monkeypatch.setattr(_CLI, "audit_world_authority", lambda *args: calls.append(args))

    assert await _CLI.run(slug=project.slug, mode=mode) == 0
    assert calls == []
    assert session.added == []
    assert scope.committed is True
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["status"] == "dry_run"
    assert dry_run["mode"] == mode


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["source", "world-recompile"])
@pytest.mark.parametrize("halted,active", [(False, 0), (True, 1)])
async def test_run_guard_does_not_call_audit_or_add_report(
    monkeypatch, capsys, mode, halted, active
):
    project = _project()
    session = _Session(project, active=active)
    artifact = _artifact(project)
    scope = _patch_run_dependencies(monkeypatch, session, artifact)

    async def halted_state(*args):
        return SimpleNamespace(halted=halted)

    monkeypatch.setattr(_CLI, "load_control_state", halted_state)
    calls: list[object] = []
    monkeypatch.setattr(_CLI, "audit_world_authority", lambda *args: calls.append(args))

    assert await _CLI.run(slug=project.slug, execute=True, mode=mode) == 3
    assert calls == []
    assert session.added == []
    assert scope.committed is True
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_persists_full_exception_diagnostic_and_calls_once(monkeypatch, capsys):
    project = _project()
    session = _Session(project)
    artifact = _artifact(project)
    _patch_run_dependencies(monkeypatch, session, artifact)

    async def running_state(*args):
        return SimpleNamespace(halted=True)

    monkeypatch.setattr(_CLI, "load_control_state", running_state)
    diagnostic = {
        "version": _CLI.AUDIT_VERSION,
        "status": "review_required",
        "error": "WORLD_AUTHORITY_MISSING_EVIDENCE",
        "raw_reviews_for_diagnosis_only": [{"raw_excerpt": "keep me"}],
    }
    calls: list[object] = []

    async def fail_once(*args, **kwargs):
        calls.append((args, kwargs))
        raise _CLI.WorldAuthorityAuditError("discardable message", diagnostic=diagnostic)

    monkeypatch.setattr(_CLI, "audit_world_authority", fail_once)
    assert await _CLI.run(slug=project.slug, execute=True) == 4
    assert len(calls) == 1
    assert len(session.added) == 1
    assert session.added[0].structured_output == diagnostic
    output = json.loads(capsys.readouterr().out)
    assert output["audit_status"] == "review_required"
    assert output["verdict"] == "needs_review"
    assert output["status"] == "recorded_failure"


@pytest.mark.asyncio
async def test_run_conflict_returns_nonzero_and_never_approves(monkeypatch, capsys):
    project = _project()
    session = _Session(project)
    artifact = _artifact(project)
    _patch_run_dependencies(monkeypatch, session, artifact)

    async def running_state(*args):
        return SimpleNamespace(halted=True)

    async def conflict(*args, **kwargs):
        return {"status": "conflict", "semantic_review_required": True}

    monkeypatch.setattr(_CLI, "load_control_state", running_state)
    monkeypatch.setattr(_CLI, "audit_world_authority", conflict)
    assert await _CLI.run(slug=project.slug, execute=True) == 4
    output = json.loads(capsys.readouterr().out)
    assert output["audit_status"] == "conflict"
    assert output["verdict"] == "needs_review"


@pytest.mark.asyncio
async def test_run_cost_claims_routes_report_and_stdout_mode(monkeypatch, capsys):
    project = _project()
    session = _Session(project)
    artifact = _artifact(project)
    _patch_run_dependencies(monkeypatch, session, artifact)

    async def running_state(*args):
        return SimpleNamespace(halted=True)

    calls: list[object] = []

    async def cost_claims(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "review_required", "semantic_review_required": True}

    module = ModuleType("bestseller.services.world_cost_claims")
    module.CLAIMS_VERSION = "claims-test"
    module.audit_world_cost_claims = cost_claims
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(_CLI, "load_control_state", running_state)

    assert await _CLI.run(slug=project.slug, execute=True, mode="cost-claims") == 4
    assert len(calls) == 1
    report = session.added[0]
    assert report.reviewer_type == "world_cost_claims_diagnostic"
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "cost-claims"
    assert output["audit_status"] == "review_required"
    assert output["verdict"] == "needs_review"


@pytest.mark.asyncio
async def test_run_does_not_print_recorded_when_commit_fails(monkeypatch, capsys):
    project = _project()
    session = _Session(project)
    artifact = _artifact(project)
    scope = _SessionScope(session, fail_commit=True)
    monkeypatch.setattr(_CLI, "load_settings", lambda: object())
    monkeypatch.setattr(_CLI, "session_scope", lambda _settings: scope)

    async def latest(*args, **kwargs):
        return artifact

    async def running_state(*args):
        return SimpleNamespace(halted=True)

    async def consistent(*args, **kwargs):
        return {"status": "consistent", "semantic_review_required": True}

    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    monkeypatch.setattr(_CLI, "load_control_state", running_state)
    monkeypatch.setattr(_CLI, "audit_world_authority", consistent)
    with pytest.raises(RuntimeError, match="commit failed"):
        await _CLI.run(slug=project.slug, execute=True)
    assert "recorded" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_source_patch_requires_bound_cost_claim_report_and_no_model_call(monkeypatch):
    project = _project()
    artifact = _artifact(project)
    session = _SequencedSession([project, None])
    scope = _SessionScope(session)
    monkeypatch.setattr(_CLI, "load_settings", lambda: object())
    monkeypatch.setattr(_CLI, "session_scope", lambda _settings: scope)

    async def latest(*args, **kwargs):
        return artifact

    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    calls: list[object] = []
    monkeypatch.setattr(_CLI, "audit_world_authority", lambda *args: calls.append(args))

    with pytest.raises(ValueError, match="SOURCE_PATCH_DIAGNOSTIC_NOT_BOUND"):
        await _CLI.run(
            slug=project.slug,
            mode="source-patch",
            diagnostic_id=str(uuid4()),
        )
    assert calls == []
    assert session.added == []


@pytest.mark.asyncio
async def test_source_patch_uses_bound_report_once_and_keeps_assets_unchanged(monkeypatch, capsys):
    project = _project()
    artifact = _artifact(project)
    report = ReviewReportModel(
        project_id=project.id,
        target_type="planning_artifact",
        target_id=artifact.id,
        reviewer_type="world_cost_claims_diagnostic",
        verdict="needs_review",
        severity_max="high",
        structured_output={"version": "1.0-cost-facts", "status": "conflict"},
    )
    report.id = uuid4()
    session = _SequencedSession([project, report, 0])
    scope = _SessionScope(session)
    monkeypatch.setattr(_CLI, "load_settings", lambda: object())
    monkeypatch.setattr(_CLI, "session_scope", lambda _settings: scope)

    async def latest(*args, **kwargs):
        return artifact

    async def running_state(*args):
        return SimpleNamespace(halted=True)

    async def propose(*args, **kwargs):
        return {
            "version": "1.0-source-clause-candidate",
            "status": "candidate_pending_review",
            "candidate_hash": "candidate-hash",
            "candidate_sources": {"world": {"rules": "candidate body"}},
            "semantic_review_required": True,
        }

    module = ModuleType("bestseller.services.world_source_patch")
    module.PATCH_VERSION = "1.0-source-clause-candidate"
    module.propose_world_source_patch = propose
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(_CLI, "get_latest_planning_artifact", latest)
    monkeypatch.setattr(_CLI, "load_control_state", running_state)
    original_metadata = dict(project.metadata_json)
    original_world = dict(artifact.content)

    assert (
        await _CLI.run(
            slug=project.slug,
            execute=True,
            mode="source-patch",
            diagnostic_id=str(report.id),
        )
        == 4
    )
    assert session.added[0].reviewer_type == "world_source_patch_candidate"
    assert session.added[0].verdict == "needs_review"
    assert session.added[0].structured_output["source_diagnostic_id"] == str(report.id)
    assert session.added[0].structured_output["source_artifact_id"] == str(artifact.id)
    assert project.metadata_json == original_metadata
    assert artifact.content == original_world
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "source-patch"
    assert output["candidate_hash"] == "candidate-hash"
    assert "candidate body" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("halted,active,expected", [(True, 0, 4), (False, 0, 3), (True, 1, 3)])
async def test_patch_review_binds_lineage_and_never_promotes(
    monkeypatch, capsys, halted, active, expected
):
    project = _project()
    artifact = _artifact(project)
    diagnostic_id, candidate_id = uuid4(), uuid4()
    diagnostic = SimpleNamespace(structured_output={"status": "conflict"})
    candidate = SimpleNamespace(
        structured_output={
            "source_diagnostic_id": str(diagnostic_id),
            "source_artifact_id": str(artifact.id),
            "candidate_hash": "candidate-hash",
        }
    )
    session = _SequencedSession([project, candidate, diagnostic, active])
    _patch_run_dependencies(monkeypatch, session, artifact)
    calls = []

    async def state(*args):
        return SimpleNamespace(halted=halted)

    async def review(*args, **kwargs):
        calls.append(kwargs)
        return {
            "status": "semantic_checks_passed",
            "promotion_allowed": False,
            "candidate_hash": "candidate-hash",
        }

    module = ModuleType("bestseller.services.world_source_patch_review")
    module.REVIEW_VERSION = "review-test"
    module.review_world_source_patch = review
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(_CLI, "load_control_state", state)
    before = json.dumps(artifact.content)
    assert (
        await _CLI.run(
            slug=project.slug, execute=True, mode="patch-review", candidate_id=str(candidate_id)
        )
        == expected
    )
    if expected == 4:
        assert len(calls) == 1
        assert calls[0]["candidate"] == candidate.structured_output
        saved = session.added[0]
        assert saved.reviewer_type == "world_source_patch_semantic_review"
        assert saved.verdict == "needs_review"
        assert saved.structured_output["source_candidate_id"] == str(candidate_id)
        assert saved.structured_output["source_diagnostic_id"] == str(diagnostic_id)
    else:
        assert calls == []
        assert session.added == []
    assert json.dumps(artifact.content) == before
    assert json.loads(capsys.readouterr().out)["status"] in {"recorded", "blocked"}


@pytest.mark.asyncio
async def test_patch_review_rejects_stale_candidate_artifact_before_call(monkeypatch):
    project = _project()
    artifact = _artifact(project)
    candidate = SimpleNamespace(
        structured_output={"source_diagnostic_id": str(uuid4()), "source_artifact_id": str(uuid4())}
    )
    session = _SequencedSession([project, candidate])
    _patch_run_dependencies(monkeypatch, session, artifact)
    with pytest.raises(ValueError, match="SOURCE_PATCH_STALE_ARTIFACT"):
        await _CLI.run(
            slug=project.slug, execute=True, mode="patch-review", candidate_id=str(uuid4())
        )
    assert session.added == []


@pytest.mark.asyncio
async def test_candidate_report_lookup_is_scoped_to_project_artifact_and_type():
    report_id, project_id, artifact_id = uuid4(), uuid4(), uuid4()
    captured = []

    class Session:
        async def scalar(self, statement):
            captured.append(statement)
            return None

    with pytest.raises(ValueError, match="DIAGNOSTIC_NOT_BOUND"):
        await _CLI._load_bound_diagnostic(
            Session(),
            diagnostic_id=report_id,
            project_id=project_id,
            artifact_id=artifact_id,
            reviewer_type="world_source_patch_candidate",
        )
    values = list(captured[0].compile().params.values())
    for required in (
        report_id,
        project_id,
        artifact_id,
        "planning_artifact",
        "world_source_patch_candidate",
        "needs_review",
    ):
        assert required in values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,kind",
    [
        ("candidate_pending_review", "world_source_patch_candidate"),
        ("review_required", "world_source_partition_diagnostic"),
    ],
)
async def test_partition_patch_preserves_binding_and_only_records_review(
    monkeypatch, capsys, status, kind
):
    project = _project()
    artifact = _artifact(project)
    diagnostic_id = uuid4()
    diagnostic = SimpleNamespace(structured_output={"status": "conflict"})
    session = _SequencedSession([project, diagnostic, 0])
    _patch_run_dependencies(monkeypatch, session, artifact)
    calls = []

    async def state(*args):
        return SimpleNamespace(halted=True)

    async def partition(*args, **kwargs):
        calls.append(kwargs)
        return {"status": status, "promotion_allowed": False}

    module = ModuleType("bestseller.services.world_source_partition")
    module.PARTITION_VERSION = "partition-fixture"
    module.propose_partition_patch = partition
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(_CLI, "load_control_state", state)
    assert (
        await _CLI.run(
            slug=project.slug, execute=True, mode="partition-patch", diagnostic_id=diagnostic_id
        )
        == 4
    )
    assert len(calls) == 1
    saved = session.added[0]
    assert saved.reviewer_type == kind
    assert saved.verdict == "needs_review"
    assert saved.structured_output["source_diagnostic_id"] == str(diagnostic_id)
    assert saved.structured_output["source_artifact_id"] == str(artifact.id)
    assert json.loads(capsys.readouterr().out)["mode"] == "partition-patch"


@pytest.mark.asyncio
@pytest.mark.parametrize("halted,active", [(True, 0), (False, 0), (True, 1)])
@pytest.mark.parametrize("shadow", [False, True])
async def test_partition_eval_is_guarded_and_never_approves_book(
    monkeypatch, capsys, halted, active, shadow
):
    from bestseller.services import world_partition_eval

    project = _project()
    artifact = _artifact(project)
    session = _Session(project, active=active)
    _patch_run_dependencies(monkeypatch, session, artifact)
    calls = []

    async def state(*args):
        return SimpleNamespace(halted=halted)

    async def evaluate(*args, **kwargs):
        calls.append(kwargs)
        return {
            "status": "reference_match",
            "source_hash": "fixture-source",
            "production_ready": False,
            "promotion_allowed": False,
        }

    monkeypatch.setattr(_CLI, "load_control_state", state)
    monkeypatch.setattr(world_partition_eval, "run_partition_eval", evaluate)
    allowed = halted and not active
    assert await _CLI.run(
        slug=project.slug, execute=True, mode="partition-eval", shadow_judge=shadow
    ) == (4 if allowed else 3)
    assert len(calls) == int(allowed)
    if allowed:
        saved = session.added[0]
        assert saved.reviewer_type == "world_partition_capability_diagnostic"
        assert saved.verdict == "needs_review"
        assert saved.structured_output["source_hash"] == "fixture-source"
        assert saved.structured_output["book_source_hash"] != "fixture-source"
        assert saved.structured_output["evaluated_scope"] == "synthetic_contrasts_not_book_source"
        assert calls[0]["project_id"] == project.id
        assert calls[0]["shadow_judge"] is shadow
        assert len(calls[0]["fixture"]["cases"]) == 6
    else:
        assert not session.added
    assert json.loads(capsys.readouterr().out)["status"] == ("recorded" if allowed else "blocked")


@pytest.mark.asyncio
async def test_partition_eval_replay_binds_existing_report_without_model_call(monkeypatch, capsys):
    from bestseller.services import world_partition_eval

    project = _project()
    artifact = _artifact(project)
    original = {"status": "evaluation_failed"}
    diagnostic_id = uuid4()
    session = _SequencedSession([project, SimpleNamespace(structured_output=original), 0])
    scope = _patch_run_dependencies(monkeypatch, session, artifact)

    async def state(*args):
        return SimpleNamespace(halted=True)

    async def forbidden(*args, **kwargs):
        raise AssertionError("offline replay must not call a model")

    def replay(report, *, fixture):
        assert report == original
        assert len(fixture["cases"]) == 6
        return {
            "status": "evaluation_failed",
            "new_llm_calls": 0,
            "promotion_allowed": False,
            "production_ready": False,
        }

    monkeypatch.setattr(_CLI, "load_control_state", state)
    monkeypatch.setattr(world_partition_eval, "run_partition_eval", forbidden)
    monkeypatch.setattr(world_partition_eval, "replay_partition_eval", replay)
    assert (
        await _CLI.run(
            slug=project.slug, execute=True, mode="partition-eval", diagnostic_id=diagnostic_id
        )
        == 4
    )
    saved = session.added[0]
    assert saved.structured_output["replayed_from_report_id"] == str(diagnostic_id)
    assert saved.verdict == "needs_review"
    assert scope.committed
    assert json.loads(capsys.readouterr().out)["status"] == "recorded"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,diagnostic_id", [("source", None), ("partition-eval", "old-report")])
async def test_shadow_override_cannot_apply_to_book_repair_or_replay(mode, diagnostic_id):
    with pytest.raises(ValueError, match="SHADOW_JUDGE_ONLY"):
        await _CLI.run(slug="x", mode=mode, diagnostic_id=diagnostic_id, shadow_judge=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "halted,active,execute", [(True, 0, True), (False, 0, True), (True, 1, True), (True, 0, False)]
)
async def test_staged_eval_is_guarded_diagnostic_only(monkeypatch, capsys, halted, active, execute):
    project = _project()
    artifact = _artifact(project)
    session = _Session(project, active=active)
    scope = _patch_run_dependencies(monkeypatch, session, artifact)
    calls = []

    async def state(*args):
        return SimpleNamespace(halted=halted)

    async def evaluate(*args, **kwargs):
        calls.append(kwargs)
        return {
            "status": "reference_match",
            "source_hash": "fixture-source",
            "production_ready": False,
            "promotion_allowed": False,
        }

    module = ModuleType("bestseller.services.world_partition_staged_eval")
    module.STAGED_VERSION = "staged-fixture"
    module.run_staged_partition_eval = evaluate
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(_CLI, "load_control_state", state)
    allowed = execute and halted and not active
    assert await _CLI.run(slug=project.slug, execute=execute, mode="partition-staged-eval") == (
        0 if not execute else 4 if allowed else 3
    )
    assert len(calls) == int(allowed)
    assert scope.committed
    if allowed:
        saved = session.added[0]
        assert saved.reviewer_type == "world_partition_staged_capability_diagnostic"
        assert saved.verdict == "needs_review"
        assert saved.structured_output["source_hash"] == "fixture-source"
        assert saved.structured_output["book_source_hash"] != "fixture-source"
        assert saved.structured_output["evaluated_scope"] == "synthetic_contrasts_not_book_source"
        assert set(calls[0]) == {"fixture", "project_id"}
        assert calls[0]["project_id"] == project.id
        assert len(calls[0]["fixture"]["cases"]) == 6
    else:
        assert not session.added
    assert json.loads(capsys.readouterr().out)["status"] == (
        "dry_run" if not execute else "recorded" if allowed else "blocked"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs", [{"diagnostic_id": "old"}, {"candidate_id": "old"}, {"shadow_judge": True}]
)
async def test_staged_eval_rejects_unsupported_replay_or_route(kwargs):
    with pytest.raises(ValueError, match=r"STAGED_EVAL_FRESH_ONLY|SHADOW_JUDGE_ONLY"):
        await _CLI.run(slug="x", mode="partition-staged-eval", **kwargs)
