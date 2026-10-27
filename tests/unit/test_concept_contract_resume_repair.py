from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import concept_contract_resume_repair as repair


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, *, scalars=(), rows=()):
        self._scalars = list(scalars)
        self._rows = rows

    async def scalar(self, _statement):
        return self._scalars.pop(0) if self._scalars else None

    async def execute(self, _statement):
        return _Rows(self._rows)


def _contract() -> dict[str, object]:
    return {
        "schema_version": "concept-contract.v2",
        "input_hash": "new-hash",
        "champion_id": "concept-new",
        "hook_card": {"one_liner": "新钩子", "protagonist": "沈见微"},
        "story_spine": {"who": "沈见微", "wants": "还原火灾", "question": "谁下令封门"},
        "seriality_proof": {"capacity_report": {"passed": True}},
    }


async def _async_contract_builder(**_kwargs):
    return _contract()


def _project(metadata: dict[str, object] | None = None):
    return SimpleNamespace(
        id=uuid4(),
        title="测试书",
        language="zh-CN",
        genre="悬疑",
        sub_genre="推理",
        target_chapters=20,
        target_word_count=52_000,
        metadata_json=metadata or {
            "concept_seed": "用户原始故事",
            "premium_cast_spec": {"characters": ["被拒版本的角色"]},
            "premium_state_snapshot": {"goal": "被拒版本的目标"},
            "prewrite_readiness_report": {"passed": False},
            "premise": "沈见微在旧城档案馆修复火灾录音并追查封门命令",
            "synopsis": "她必须在爆破前还原证据链",
            "writing_profile": {"character": {"golden_finger": "修复录音"}},
            "creation_protagonist_name": "沈见微",
            "story_spine": {"who": "沈见微", "wants": "还原火灾", "question": "谁下令封门"},
            "concept_contract": {"schema_version": "concept-contract.v2", "story_architecture": {"old": True}},
        },
    )


@pytest.mark.asyncio
async def test_repair_rejects_non_empty_project() -> None:
    project = _project()
    session = _Session(scalars=[uuid4()])
    with pytest.raises(repair.ConceptContractResumeRepairError, match="zero chapters"):
        await repair.repair_existing_project_concept_contract(
            session, project, contract_builder=_async_contract_builder
        )


@pytest.mark.asyncio
async def test_repair_rejects_active_workflow() -> None:
    project = _project()
    active = SimpleNamespace(workflow_type="autowrite", status="running")
    session = _Session(scalars=[None, active])
    with pytest.raises(repair.ConceptContractResumeRepairError, match="active"):
        await repair.repair_existing_project_concept_contract(
            session, project, contract_builder=_async_contract_builder
        )


@pytest.mark.asyncio
async def test_repair_replaces_mirrors_rebuilds_snapshot_and_supersedes_derived_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(
        {
            "concept_seed": "用户原始故事",
            "premise": "沈见微在旧城档案馆修复火灾录音并追查封门命令",
            "synopsis": "她必须在爆破前还原证据链",
            "creation_protagonist_name": "沈见微",
            "story_spine": {"who": "沈见微", "wants": "还原火灾", "question": "谁下令封门"},
            "concept_contract": {"schema_version": "concept-contract.v2", "story_architecture": {"old": True}},
        }
    )
    old = SimpleNamespace(status="approved", notes=None, artifact_type="volume_plan")
    session = _Session(scalars=[None, None], rows=[old])
    monkeypatch.setattr(repair, "build_concept_contract", lambda **_: _contract())
    monkeypatch.setattr(repair, "validate_concept_contract", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        repair,
        "ensure_project_book_design_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(snapshot_id="snapshot-new"),
    )

    calls: list[dict[str, object]] = []

    async def builder(**kwargs):
        calls.append(kwargs)
        return {"concept": "新钩子", "mechanism": "修复录音", "story_architecture": {}}

    result = await repair.repair_existing_project_concept_contract(
        session, project, contract_builder=builder
    )

    assert result.status == "repaired"
    assert result.superseded_artifact_count == 1
    assert old.status == "superseded"
    assert "superseded_by_concept_contract_resume_repair" in old.notes
    assert project.metadata_json["concept_contract"] == _contract()
    assert project.metadata_json["hook_card"] == _contract()["hook_card"]
    assert project.metadata_json["story_spine"] == _contract()["story_spine"]
    assert project.metadata_json["seriality_proof"] == _contract()["seriality_proof"]
    assert project.metadata_json["concept_contract_repair"]["superseded"]["count"] == 1
    assert project.metadata_json["truth_version"] == 2
    assert project.metadata_json["truth_last_changed_artifact_type"] == "concept_contract"
    assert calls and calls[0]["concept_seed"] == "用户原始故事"
    assert calls[0]["winner"] == {}
    assert "premium_cast_spec" not in project.metadata_json
    assert "premium_state_snapshot" not in project.metadata_json
    assert "prewrite_readiness_report" not in project.metadata_json


@pytest.mark.asyncio
async def test_repair_is_idempotent_for_same_trusted_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project()
    session = _Session(scalars=[None, None, None, None], rows=[])
    monkeypatch.setattr(repair, "build_concept_contract", lambda **_: _contract())
    monkeypatch.setattr(repair, "validate_concept_contract", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        repair,
        "ensure_project_book_design_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(snapshot_id="snapshot-new"),
    )
    called = False

    async def builder(**_kwargs):
        nonlocal called
        called = True
        return {"concept": "新钩子", "mechanism": "修复录音"}

    first = await repair.repair_existing_project_concept_contract(
        session, project, contract_builder=builder
    )
    assert first.status == "repaired"
    called = False
    second = await repair.repair_existing_project_concept_contract(
        session, project, contract_builder=builder
    )
    assert second.status == "already_repaired"
    assert called is False


@pytest.mark.asyncio
async def test_production_wrapper_skips_legacy_book_without_explicit_seed() -> None:
    project = _project()
    project.metadata_json.pop("concept_seed", None)
    result = await repair.ensure_project_concept_contract_lineage(
        _Session(), SimpleNamespace(), project
    )
    assert result is None


@pytest.mark.asyncio
async def test_production_wrapper_skips_existing_book_with_chapters() -> None:
    project = _project()
    result = await repair.ensure_project_concept_contract_lineage(
        _Session(scalars=[uuid4()]), SimpleNamespace(), project
    )
    assert result is None


@pytest.mark.asyncio
async def test_production_wrapper_recognizes_conception_lineage_without_second_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    signature = repair._source_signature(repair._trusted_inputs(project))
    project.metadata_json["concept_contract"] = {
        **_contract(),
        "story_architecture": {
            "reconciliation_lineage": {"source_signature": signature}
        },
    }
    project.metadata_json["foundation_truth_status"] = "superseded_pending_materialization"
    monkeypatch.setattr(repair, "validate_concept_contract", lambda *_args, **_kwargs: [])
    session = _Session(scalars=[None, None, None])
    result = await repair.ensure_project_concept_contract_lineage(
        session, SimpleNamespace(), project
    )
    assert result is not None
    assert result.status == "already_repaired"
    assert result.force_foundation_replan is True
