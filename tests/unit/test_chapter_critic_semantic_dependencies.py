"""Real SQLite source/config changes invalidate a retained critic cycle, not its budget."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt

from bestseller.infra.db.models import (
    ChapterContractModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    SceneCardModel,
    WorldRuleModel,
)
from bestseller.services import pipelines
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def semantic_db(db):
    session, project, _prior, prior_draft = db
    await make_accepted_debt(db)
    conn = await session.connection()
    for model in (
        SceneCardModel,
        PlanningArtifactVersionModel,
        WorldRuleModel,
        ChapterContractModel,
    ):
        ddl = str(CreateTable(model.__table__, if_not_exists=True).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=2,
        chapter_goal="Continue the result",
        target_word_count=2600,
        metadata_json={},
    )
    session.add(chapter)
    await session.flush()
    scene = SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        scene_number=1,
        scene_type="development",
        purpose={"story": "Use the prior result"},
        participants=[],
        entry_state={"door": "closed"},
        exit_state={"door": "open"},
        metadata_json={},
    )
    design = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="world_spec",
        version_no=1,
        status="approved",
        schema_version="fixture.v1",
        content={"rules": ["One key"]},
    )
    session.add_all([scene, design])
    await session.flush()
    return session, project, chapter, scene, design, prior_draft


@pytest.mark.parametrize(
    "changed",
    [
        "prior_prose",
        "design",
        "scene",
        "model",
        "threshold",
        "corpus",
        "model_env",
        "prefix",
        "canonical_pointer",
        "technical_invariants",
    ],
)
async def test_real_semantic_dependencies_change_on_sources_or_evaluation_config(
    semantic_db, monkeypatch, changed
):
    session, project, chapter, scene, design, prior_draft = semantic_db
    settings = load_settings(env={})
    before = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    if changed == "prior_prose":
        # Same current chapter body, but an adopted predecessor's bytes no longer
        # match its immutable adoption receipt: source loading must reject it.
        prior_draft.content_md += "未授权改变的前文事件。"
        with pytest.raises(ValueError):
            await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
        return
    if changed == "design":
        design.content = {"rules": ["Two keys"]}
    elif changed == "scene":
        scene.exit_state = {"door": "still closed"}
    elif changed == "model":
        settings.llm.critic.model = "different-fixture-model"
    elif changed == "model_env":
        monkeypatch.setenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", "fixture-new-critic")
    elif changed == "threshold":
        settings.quality.thresholds.scene_min_score += 0.01
    elif changed == "technical_invariants":
        project.invariants_json = {"length_envelope": {"min_chars": 1700}}
    elif changed == "prefix":
        chapter.metadata_json = {
            "outline_prefix_readiness": {"artifact_id": str(uuid4()), "projection_hash": "changed"}
        }
    elif changed == "canonical_pointer":
        project.metadata_json = {
            **(project.metadata_json or {}),
            "creation_canonical_snapshot_id": str(uuid4()),
        }
    elif changed == "corpus":
        original = Path.read_bytes

        def changed_bytes(path):
            data = original(path)
            return (
                data + b"\n# new calibration fixture"
                if path.parent.name == "reference_corpora"
                else data
            )

        monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    assert (
        await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter) != before
    )


async def test_runtime_receipts_and_cycle_markers_do_not_self_invalidate(semantic_db):
    session, project, chapter, scene, design, prior_draft = semantic_db
    settings = load_settings(env={})
    before = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    chapter.metadata_json = {
        "chapter_critic_cycles": {"id": {"state": "running"}},
        "chapter_review_active_cycle": {"state": "prepared"},
        "commercial_component_cohorts": {"body": {"workflow_run_id": str(uuid4()), "binding_sha256": "a" * 64}},
        "reader_judge": {"pass": False},
        "auto_repair_last_block_codes": ["historical"],
    }
    project.metadata_json = {
        **(project.metadata_json or {}),
        "production_control": {"command_serial": 99},
    }
    scene.metadata_json = {"auto_repair_attempt": 5, "auto_repair_hint": "diagnostic only"}
    design.content = {**design.content, "_meta": {"audit": "new receipt", "attempt": 5}}
    assert (
        await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter) == before
    )
    assert prior_draft.content_md and design.content["rules"] == ["One key"]


@pytest.mark.parametrize('content', [[{'chapter_number': 3, 'goal': 'Another result'}], [], {}, None, '', 0, False])
async def test_approved_json_artifact_shapes_are_preserved_and_invalidate_on_change(semantic_db, content):
    session, project, chapter, _scene, _design, _prior = semantic_db
    outline = PlanningArtifactVersionModel(
        project_id=project.id, artifact_type='volume_chapter_outline', version_no=1,
        status='approved', schema_version='legacy-json.v1', content=deepcopy(content),
    )
    session.add(outline)
    await session.flush()
    settings = load_settings(env={})
    before = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    assert outline.content == content and type(outline.content) is type(content)
    outline.content = {'updated': ['A distinct planning value']}
    after = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    assert after != before
    outline.content = deepcopy(content)
    assert await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter) == before


async def test_empty_list_and_empty_object_are_distinct_approved_dependencies(semantic_db):
    session, project, chapter, _scene, _design, _prior = semantic_db
    outline = PlanningArtifactVersionModel(
        project_id=project.id, artifact_type='volume_chapter_outline', version_no=1,
        status='approved', schema_version='legacy-json.v1', content=[],
    )
    session.add(outline)
    settings = load_settings(env={})
    before = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    outline.content = {}
    assert await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter) != before


async def test_real_changed_design_blocks_completed_report_without_starting_more_rounds(
    semantic_db, monkeypatch
):
    from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
    from bestseller.infra.db.models import (
        ChapterDraftVersionModel,
        QualityScoreModel,
        ReviewReportModel,
        WorkflowRunModel,
    )

    session, project, chapter, _scene, design, _prior_draft = semantic_db
    conn = await session.connection()
    ddl = str(CreateTable(WorkflowRunModel.__table__).compile(dialect=conn.dialect))
    await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="同一份当前章正文。",
        is_current=True,
        promotion_state="candidate",
    )
    session.add(draft)
    await session.flush()
    calls = []

    async def reviewer(*args, **kwargs):
        calls.append(kwargs)
        result = ChapterReviewResult(
            verdict="pass",
            severity_max="low",
            scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.9)),
        )
        report = ReviewReportModel(
            project_id=project.id,
            target_type="chapter",
            target_id=chapter.id,
            reviewer_type="synthetic",
            verdict="pass",
            severity_max="low",
            structured_output={
                "draft_id": str(draft.id),
                "reviewed_body_fingerprint": pipelines.chapter_review_body_fingerprint(
                    draft.content_md
                ),
                "critic_evidence": {"status": "verified"},
            },
        )
        session.add(report)
        await session.flush()
        quality = QualityScoreModel(
            project_id=project.id,
            target_type="chapter",
            target_id=chapter.id,
            chapter_draft_version_id=draft.id,
            review_report_id=report.id,
            is_current=False,
            **dict.fromkeys(
                (
                    "score_overall",
                    "score_goal",
                    "score_conflict",
                    "score_emotion",
                    "score_dialogue",
                    "score_style",
                    "score_hook",
                ),
                0.9,
            ),
        )
        session.add(quality)
        await session.flush()
        return result, report, quality, None

    monkeypatch.setattr(pipelines, "review_chapter_draft", reviewer)
    settings = load_settings(env={})

    async def run():
        workflow = WorkflowRunModel(
            id=uuid4(),
            project_id=project.id,
            scope_type="chapter",
            scope_id=chapter.id,
            workflow_type="chapter_pipeline",
            status="running",
            metadata_json={},
        )
        session.add(workflow)
        await session.commit()
        return await pipelines._review_chapter_with_evidence_retry(
            session, settings, project.slug, 2, workflow=workflow, draft=draft
        )

    await run()
    cycles = deepcopy(chapter.metadata_json["chapter_critic_cycles"])
    design.content = {"rules": ["Actual design changed; same chapter text"]}
    with pytest.raises(ValueError, match="semantic_dependencies_changed"):
        await run()
    assert len(calls) == 1 and chapter.metadata_json["chapter_critic_cycles"] == cycles
