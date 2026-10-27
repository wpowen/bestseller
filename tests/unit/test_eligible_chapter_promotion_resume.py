"""An eligible retained body resumes adoption without an illegal state rewind."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_exact_chapter_adoption import candidates as candidates, admit
from test_adopted_knowledge_resume_control_integrity import configured as configured

from bestseller.infra.db.models import QualityScoreModel
from bestseller.services import pipelines
from bestseller.services.draft_promotion import DraftQualityIneligibleError
from bestseller.services.chapter_review_reconciliation import _normalized_module

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("eligible_score", [True, False])
async def test_real_promotion_entry_revalidates_eligible_without_reentering_review(candidates, monkeypatch, eligible_score):
    session, settings, project, chapter, _, draft = candidates
    await admit(candidates, monkeypatch)
    draft.promotion_state = "eligible"
    draft.assembled_from_scene_draft_ids = ["chapter_first_scene:offline"]
    score = await session.scalar(select(QualityScoreModel).where(
        QualityScoreModel.chapter_draft_version_id == draft.id))
    if not eligible_score:
        score.score_goal = 0.1
    await session.commit()

    async def no_story_engine(*args, **kwargs):
        return SimpleNamespace(effective_mode="off")

    monkeypatch.setattr(pipelines, "resolve_story_engine_rollout_decision_from_db", no_story_engine)
    # No story effects are selected by this fixture. Source/continuity, quality
    # revalidation, exact promotion and database transitions remain real.
    monkeypatch.setattr(pipelines, "audit_persisted_story_effect_realization",
                        lambda *a, **k: {"selected_effects": []})
    if eligible_score:
        promoted, mode = await pipelines._promote_reviewed_chapter_draft(
            session, settings=settings, project=project, chapter=chapter, draft=draft,
            quality=score, workflow_run_id=None)
        assert promoted and mode == "chapter_first"
        assert draft.promotion_state == "promoted"
    else:
        with pytest.raises(DraftQualityIneligibleError):
            await pipelines._promote_reviewed_chapter_draft(
                session, settings=settings, project=project, chapter=chapter, draft=draft,
                quality=score, workflow_run_id=None)
        assert draft.promotion_state == "eligible"


@pytest.mark.parametrize("mutation", ["exact", "wrong_state", "wrong_source", "no_eligibility", "no_continuity"])
async def test_only_the_exact_resume_guard_is_policy_compatible(mutation):
    call = ('await mark_candidate_under_review(\n'
            '    session, project_id=project.id, draft_kind="chapter",\n'
            '    draft_id=draft.id, workflow_run_id=workflow_run_id)')
    import textwrap
    before = ('async def _promote_reviewed_chapter_draft(session, draft):\n'
              '    await existing_continuity()\n' + textwrap.indent(call, '    ') + '\n'
              '    await mark_draft_eligible()\n')
    guarded = 'if draft.promotion_state != DraftPromotionState.ELIGIBLE.value:\n' + textwrap.indent(call, '    ')
    current = before.replace(textwrap.indent(call, '    '), textwrap.indent(guarded, '    '))
    if mutation == "wrong_state":current = current.replace('ELIGIBLE.value', 'PROMOTED.value')
    elif mutation == "wrong_source":current = current.replace('draft_id=draft.id', 'draft_id=other.id')
    elif mutation == "no_eligibility":current = current.replace('    await mark_draft_eligible()\n', '')
    elif mutation == "no_continuity":current = current.replace('    await existing_continuity()\n', '')
    assert (_normalized_module(before) == _normalized_module(current)) == (mutation == "exact")
