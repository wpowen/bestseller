"""Database contract tests for the derived-input apply boundary."""
# ruff: noqa: E501, RUF001, RUF005

from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
import json
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    BookProductionControlModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    ReviewReportModel,
)
from bestseller.services.book_design import ensure_project_book_design_snapshot
from bestseller.services.derived_revision import (
    build_derived_revision,
    revision_review_sources,
    verify_derived_revision_report,
)
from bestseller.services.derived_revision_apply import apply_reviewed_derived_revision
from bestseller.services.world_authority import world_authority_source_index
from bestseller.services.world_recompile_candidate import build_world_recompile_candidate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _url() -> str:
    value = os.environ.get("BESTSELLER__DATABASE__URL", "")
    if not value or "test" not in value.lower():
        pytest.skip("requires BESTSELLER__DATABASE__URL pointing at an isolated test database")
    return value


@pytest_asyncio.fixture
async def revision_db() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], ProjectModel, dict]]:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid4()
    slug = f"derived-revision-{project_id.hex[:12]}"
    metadata = {
        "book_design_snapshot_status": "locked",
        "book_design_snapshot": {
            "snapshot_id": "snapshot-1",
            "schema_version": "book-design.v1",
            "protagonist": {"name": "纪年"},
            "reader_promise": "开符铺翻身",
            "core_story_engine": "用怕字瞳做生意",
            "creation_intent": {
                "genre_intent": {
                    "genre_key": "xuanhuan",
                    "genre_label": "玄幻",
                    "prompt_pack_key": "xuanhuan-power-fantasy",
                    "source": "legacy_inferred",
                },
                "chapter_count": 50,
                "tone_preference": "轻松",
            },
            "tone": "轻松",
        },
        "story_enhancers": {"cost_style": "minimal"},
        "writing_profile": {
            "world": {"power_system_style": "越阶后手抖；不会退阶。"},
        },
        "growth_curve": "第50章完成正街对质并钉回符匾。",
    }
    premise_text = "纪年在山城开符铺，并在第五十章完成正街对质。"
    async with factory() as session:
        project = ProjectModel(
            id=project_id,
            slug=slug,
            title="山城符铺",
            language="zh-CN",
            genre="玄幻",
            target_word_count=100000,
            target_chapters=50,
            audience="男频",
            status="paused",
            metadata_json=metadata,
        )
        ensure_project_book_design_snapshot(
            project, protagonist_name="纪年", force_rebuild=True
        )
        session.add(project)
        await session.flush()
        control = BookProductionControlModel(
            project_id=project_id,
            desired_state="pause",
            command_serial=1,
            detail_json={},
        )
        premise = PlanningArtifactVersionModel(
            id=uuid4(), project_id=project_id, artifact_type=ArtifactType.PREMISE.value,
            version_no=1, status="approved", schema_version="1.0",
            content={"premise": premise_text},
        )
        world = PlanningArtifactVersionModel(
            id=uuid4(), project_id=project_id, artifact_type=ArtifactType.WORLD_SPEC.value,
            version_no=1, status="approved", schema_version="1.0",
            content={"_meta": {"source_snapshot_hash": project.metadata_json["book_design_snapshot_hash"]}, "rules": []},
        )
        session.add_all([control, premise, world])
        await session.commit()
    async with factory() as session:
        project = await session.get(ProjectModel, project_id)
        premise = await session.scalar(select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.PREMISE.value,
        ))
        world = await session.scalar(select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.artifact_type == ArtifactType.WORLD_SPEC.value,
        ))
        assert project and premise and world
        parent = build_world_recompile_candidate(
            project, premise=premise_text, source_artifact=world,
            premise_artifact_id=str(premise.id),
        )
        issues = [
            {"path": ["writing_profile", "world", "power_system_style"],
             "quote": "越阶后手抖；不会退阶。", "problem": "cost wording", "required_outcome": "retain protection"},
            {"path": ["growth_curve"], "quote": metadata["growth_curve"],
             "problem": "deadline wording", "required_outcome": "keep chapter 50 payoff"},
        ]
        edited = {
            "changes": [
                {"path": issues[0]["path"], "after": "越阶可能画符失败；不会退阶。", "reason": "保留失败和保护条款"},
                {"path": issues[1]["path"], "after": "第50章完成三方对质并钉回符匾。", "reason": "明确本期兑现点"},
            ]
        }
        report = build_derived_revision(parent, issues=issues, payload=edited)
        report["editor_receipt"] = {
            "llm_run_id": str(uuid4()), "provider": "openai", "model": "test-model",
            "finish_reason": "stop", "fallback_used": False,
            "raw_response": json.dumps(edited, ensure_ascii=False),
        }
        sources = world_authority_source_index(revision_review_sources(report))
        source_by_root = {
            root: next(source["source_id"] for source in sources if source["path"][0] == root)
            for root in ("before", "after", "authority")
        }
        checks = []
        for axis in ("mechanism_preservation", "cost_compatibility", "growth_deadline", "story_identity"):
            source_ids = (
                [source_by_root["before"], source_by_root["after"]]
                if axis == "mechanism_preservation"
                else [source_by_root["authority"], source_by_root["after"]]
            )
            checks.append({
                "axis": axis, "verdict": "pass", "reason": "fixture evidence",
                "source_ids": source_ids,
            })
        for _ in range(2):
            raw = {"review_input_hash": report["review_input_hash"], "checks": checks}
            report["reviews"] = report.get("reviews", []) + [{
                "llm_run_id": str(uuid4()), "provider": "openai", "model": "test-model",
                "finish_reason": "stop", "fallback_used": False,
                "raw_response": json.dumps(raw, ensure_ascii=False), "checks": deepcopy(checks),
            }]
        report["status"] = "review_passed"
        verify_derived_revision_report(report)
        for index, receipt in enumerate([report["editor_receipt"], *report["reviews"]]):
            role = "editor" if index == 0 else "critic"
            session.add(LlmRunModel(
                id=UUID(receipt["llm_run_id"]), project_id=project_id,
                logical_role=role, provider=receipt["provider"], model_name=receipt["model"],
                prompt_template=f"derived_input_revision_{role}", prompt_version=report["version"],
                finish_reason=receipt["finish_reason"], metadata_json={},
            ))
        review = ReviewReportModel(
            id=uuid4(), project_id=project_id, target_type="planning_artifact",
            target_id=world.id, reviewer_type="derived_input_revision", verdict="pass",
            structured_output=report,
        )
        session.add(review)
        await session.commit()
        report_id = review.id
        lock_version = project.lock_version
    try:
        yield factory, project, {"report_id": report_id, "lock_version": lock_version, "slug": slug}
    finally:
        async with factory() as session:
            await session.execute(delete(ProjectModel).where(ProjectModel.id == project_id))
            await session.commit()
        await engine.dispose()


async def test_apply_is_idempotent_and_preserves_pause(revision_db) -> None:
    factory, _, info = revision_db
    async with factory() as session:
        result = await apply_reviewed_derived_revision(
            session, project_slug=info["slug"], report_id=info["report_id"],
            expected_lock_version=info["lock_version"],
        )
        await session.commit()
        assert result["status"] == "applied"
        applied_version = result["world_artifact_id"]
    async with factory() as session:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == info["slug"]))
        assert project
        assert project.metadata_json["derived_input_revision"]["world_artifact_id"] == applied_version
        assert project.metadata_json["derived_revision_projection_pending"] is True
        second = await apply_reviewed_derived_revision(
            session, project_slug=info["slug"], report_id=info["report_id"],
            expected_lock_version=info["lock_version"],
        )
        assert second["status"] == "already_applied"


async def test_stale_lock_and_tampered_receipt_roll_back(revision_db) -> None:
    factory, _, info = revision_db
    async with factory() as session:
        with pytest.raises(ValueError, match="STALE_LOCK_VERSION"):
            await apply_reviewed_derived_revision(
                session, project_slug=info["slug"], report_id=info["report_id"],
                expected_lock_version=999,
            )
        await session.rollback()
        report = await session.get(ReviewReportModel, info["report_id"])
        assert report
        report.structured_output["editor_receipt"]["model"] = "tampered"
        flag_modified(report, "structured_output")
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError):
            await apply_reviewed_derived_revision(
                session, project_slug=info["slug"], report_id=info["report_id"],
                expected_lock_version=info["lock_version"],
            )
        project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == info["slug"]))
        assert project and "derived_input_revision" not in project.metadata_json


async def test_second_import_failure_rolls_back_all_writes(revision_db, monkeypatch):
    from bestseller.services import derived_revision_apply as service
    factory, original, info = revision_db
    actual_import = service.import_planning_artifact
    count = 0

    async def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("injected second import failure")
        return await actual_import(*args, **kwargs)

    monkeypatch.setattr(service, "import_planning_artifact", fail_second)
    async with factory() as session:
        before = await session.get(ProjectModel, original.id)
        old_meta = deepcopy(before.metadata_json)
        old_ids = set(await session.scalars(select(PlanningArtifactVersionModel.id).where(
            PlanningArtifactVersionModel.project_id == original.id)))
        with pytest.raises(RuntimeError, match="injected"):
            await service.apply_reviewed_derived_revision(
                session, project_slug=info["slug"], report_id=info["report_id"],
                expected_lock_version=info["lock_version"])
        await session.commit()  # even a caller committing after failure is safe
    async with factory() as session:
        after = await session.get(ProjectModel, original.id)
        assert after.metadata_json == old_meta
        assert after.lock_version == info["lock_version"]
        assert set(await session.scalars(select(PlanningArtifactVersionModel.id).where(
            PlanningArtifactVersionModel.project_id == original.id))) == old_ids


async def test_idempotent_replay_rejects_changed_creation_identity(revision_db):
    factory, original, info = revision_db
    async with factory() as session:
        await apply_reviewed_derived_revision(session, project_slug=info["slug"],
            report_id=info["report_id"], expected_lock_version=info["lock_version"])
        await session.commit()
        project = await session.get(ProjectModel, original.id)
        project.title = "changed identity"
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="APPLIED_INPUTS_STALE"):
            await apply_reviewed_derived_revision(session, project_slug=info["slug"],
                report_id=info["report_id"], expected_lock_version=info["lock_version"])
