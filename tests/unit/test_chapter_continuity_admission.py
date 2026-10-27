"""Candidate hard checks never publish knowledge; adopted extraction reuses payment."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_adopted_knowledge_resume_control_integrity import configured as configured
from test_fanqie_segment_resume import forbid

from bestseller.infra.db.models import ChapterStateSnapshotModel, QualityScoreModel
from bestseller.services import chapter_continuity_admission as admission
from bestseller.services import continuity, pipelines
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def response(content='{"facts": [], "time_anchor": null}', fallback=False):
    return SimpleNamespace(content=content, fallback_used=fallback, model_name="offline-fixture",
                           llm_run_id=uuid4(), provider="offline")


@pytest.fixture
async def candidate(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    draft.promotion_state = "under_review"
    await session.commit()
    monkeypatch.setattr(pipelines, "get_quality_gates_config", lambda: SimpleNamespace(
        phase_d=SimpleNamespace(enabled=True, countdown_arithmetic_enabled=True, regression_check_enabled=True),
    ))
    return configured


async def test_candidate_preview_not_authority_and_post_adoption_snapshot_reuses_paid_bytes(candidate, monkeypatch):
    session, settings, project, chapter, draft = candidate
    calls = []

    async def complete(session, settings, request):
        assert not session.in_nested_transaction()
        calls.append(request)
        return response()

    monkeypatch.setattr(admission, "complete_text", complete)
    await pipelines._ensure_chapter_continuity_before_adoption(
        session, settings, project_id=project.id, chapter=chapter, draft=draft, workflow_run_id=None,
    )
    assert chapter.metadata_json["continuity_admission"]["passed"]
    assert not list(await session.scalars(select(ChapterStateSnapshotModel)))
    assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False))[0] is None
    await pipelines._ensure_chapter_continuity_before_adoption(
        session, settings, project_id=project.id, chapter=chapter, draft=draft, workflow_run_id=None,
    )
    assert len(calls) == 1
    # Stand in for the independently tested review/promotion decision.
    draft.promotion_state = "promoted"
    await session.commit()
    monkeypatch.setattr(continuity, "complete_text", forbid)
    snapshot = await continuity.extract_chapter_state_snapshot(
        session, settings, project_id=project.id, chapter=chapter, chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id,
    )
    assert snapshot.extraction_status == "ok_promoted"
    assert snapshot.facts["snapshot_contract"]["pre_adoption_preview_reused"]
    assert len(calls) == 1


@pytest.mark.parametrize("gate", ["hard_failure", "unavailable"])
async def test_real_promotion_entry_stops_before_any_promotion_or_knowledge(candidate, monkeypatch, gate):
    session, settings, project, chapter, draft = candidate

    async def complete(*args, **kwargs):
        return response()

    async def checker(**kwargs):
        assert kwargs["strict"]
        if gate == "unavailable":
            raise RuntimeError("validator unavailable")
        return [SimpleNamespace(blocks_write=True, agent="time", chapter=1,
                                summary="time regressed", issues=[])]

    monkeypatch.setattr(admission, "complete_text", complete)
    monkeypatch.setattr(pipelines, "_collect_phase_d_reports", checker)
    monkeypatch.setattr(pipelines, "_chapter_source_mode_is_promotable", forbid)
    monkeypatch.setattr(pipelines, "promote_chapter_draft", forbid)
    quality = QualityScoreModel(id=uuid4(), project_id=project.id, target_type="chapter", target_id=chapter.id)
    with pytest.raises(ValueError, match="chapter_continuity_" + gate):
        await pipelines._promote_reviewed_chapter_draft(
            session, settings=settings, project=project, chapter=chapter, draft=draft,
            quality=quality, workflow_run_id=None,
        )
    assert draft.promotion_state == "under_review"
    assert chapter.production_state == "blocked"
    assert not list(await session.scalars(select(ChapterStateSnapshotModel)))
    assert draft.promotion_metadata[admission.PREVIEW_KEY]["status"] == "extracted"


@pytest.mark.parametrize("raw,fallback", [('not json', False), ('{"facts": []}', True)])
async def test_unknown_extraction_cannot_become_pass(candidate, monkeypatch, raw, fallback):
    session, settings, project, chapter, draft = candidate

    async def complete(*args, **kwargs):
        return response(raw, fallback)

    monkeypatch.setattr(admission, "complete_text", complete)
    with pytest.raises(ValueError, match="chapter_continuity_unavailable"):
        await pipelines._ensure_chapter_continuity_before_adoption(
            session, settings, project_id=project.id, chapter=chapter, draft=draft, workflow_run_id=None,
        )
    assert not list(await session.scalars(select(ChapterStateSnapshotModel)))
    assert draft.promotion_metadata[admission.PREVIEW_KEY]["raw"] == raw


async def test_retained_preview_does_not_follow_changed_body(candidate, monkeypatch):
    session, settings, project, chapter, draft = candidate
    calls = []

    async def complete(*args):
        calls.append(args[-1])
        return response()

    monkeypatch.setattr(admission, "complete_text", complete)
    await admission.extract_continuity_admission_preview(
        session, settings, project_id=project.id, chapter=chapter, draft=draft, workflow_run_id=None,
    )
    first = deepcopy(draft.promotion_metadata[admission.PREVIEW_KEY])
    draft.content_md += "窗外开始下雨。"
    await session.commit()
    await admission.extract_continuity_admission_preview(
        session, settings, project_id=project.id, chapter=chapter, draft=draft, workflow_run_id=None,
    )
    assert len(calls) == 2 and calls[0].operation_id != calls[1].operation_id
    assert draft.promotion_metadata[admission.PREVIEW_KEY + "_history"] == [first]


async def test_failed_revalidation_excludes_old_promoted_body_without_erasing_history(configured, monkeypatch):
    session, settings, project, chapter, draft = configured
    from bestseller.services.reviews import chapter_review_body_fingerprint
    chapter.metadata_json = {"continuity_adoption_block": {
        "draft_id": str(draft.id), "body_sha256": chapter_review_body_fingerprint(draft.content_md)["digest"],
        "passed": False, "status": "hard_failure",
    }}
    await session.commit()
    assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False))[0] is None
    assert draft.promotion_state == "promoted" and draft.content_md
    assert chapter.metadata_json["continuity_adoption_block"]["status"] == "hard_failure"


@pytest.mark.parametrize("second_passes", [False, True])
async def test_other_candidate_cannot_clear_old_source_block(candidate, monkeypatch, second_passes):
    from bestseller.infra.db.models import ChapterDraftVersionModel
    from bestseller.services.adopted_chapter_source import continuity_admissions_by_source

    session, settings, project, chapter, first = candidate
    first.promotion_state = "promoted"
    await session.commit()

    async def complete(*args, **kwargs):
        return response()

    should_pass = False

    async def checker(**kwargs):
        return [] if should_pass else [SimpleNamespace(
            blocks_write=True, agent="time", chapter=1, summary="time regressed", issues=[],
        )]

    monkeypatch.setattr(admission, "complete_text", complete)
    monkeypatch.setattr(pipelines, "_collect_phase_d_reports", checker)
    async def check(draft):
        await pipelines._ensure_chapter_continuity_before_adoption(
            session, settings, project_id=project.id, chapter=chapter, draft=draft, workflow_run_id=None,
        )

    with pytest.raises(ValueError, match="chapter_continuity_hard_failure"):
        await check(first)
    original_receipt = deepcopy(chapter.metadata_json["continuity_adoption_block"])
    first.is_current = False
    await session.flush()
    second = ChapterDraftVersionModel(
        project_id=project.id, chapter_id=chapter.id, version_no=2,
        content_md="次日，阿青回店取走钥匙。", promotion_state="under_review",
        is_current=True, promotion_metadata={},
    )
    session.add(second)
    await session.commit()
    should_pass = second_passes
    if should_pass:
        await check(second)
    else:
        with pytest.raises(ValueError, match="chapter_continuity_hard_failure"):
            await check(second)
    await session.commit()
    assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False))[0] is None
    assert original_receipt in continuity_admissions_by_source(chapter.metadata_json).values()
    assert first.promotion_state == "promoted" and first.content_md
    # Only explicit successful revalidation of the same source resolves it.
    second.is_current = False
    await session.flush()
    first.is_current = True
    await session.commit()
    should_pass = True
    await check(first)
    await session.commit()
    assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False))[0].id == first.id
    assert original_receipt in chapter.metadata_json["continuity_admission_history"]
