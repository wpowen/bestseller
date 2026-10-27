"""Literal style projection on a fresh native project; no paid dispatch."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_chapter_review_admission_sqlite import admission_db as admission_db  # noqa: F401

from bestseller.infra.db import models as m
from bestseller.services.project_invariant_initialization import ensure_missing_project_invariants
from bestseller.services.project_style_invariant_boundary import (
    canonical_creation_tense,
    prepare_creation_style_invariant_boundary,
)

pytestmark = pytest.mark.unit
RAW = "过去时叙述，场景内保持当下感。"


@pytest.mark.parametrize("is_en", [False, True])
@pytest.mark.parametrize("repair", [False, True])
def test_new_finalization_prompt_requests_canonical_tense_and_retains_narration_rules(
    is_en, repair
):
    from bestseller.services import conception

    ctx = {
        "genre": "现实生活",
        "sub_genre": "日常",
        "chapter_count": 4,
        "user_hints": {},
        "description": "厨师必须在开席前解决眼前的困难。",
    }
    if repair:
        prompt = conception._pollution_retry_finalize_prompt(ctx, genre_profile=None, is_en=is_en)
    else:
        builder = conception._finalize_user_prompt_en if is_en else conception._finalize_user_prompt
        prompt = builder(ctx, {}, {}, {}, {})
    assert '"tense": "past"' in prompt and '"custom_rules": ["..."]' in prompt
    assert "style.tense" in prompt and "style.custom_rules" in prompt and "present" in prompt
    assert ctx["description"] in prompt


@pytest.mark.parametrize(
    "raw,expected",
    [
        (RAW, "past"),
        ("过去时", "past"),
        ("过去式", "past"),
        ("过去时叙述", "past"),
        ("现在时叙述", "present"),
        ("现在式", "present"),
        ("past", "past"),
        ("PAST", "past"),
        ("past tense", "past"),
        ("present tense", "present"),
        (" present ", "present"),
    ],
)
def test_literal_unambiguous_aliases(raw, expected):
    assert canonical_creation_tense(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        False,
        {},
        "",
        "future",
        "过去",
        "不要用过去时",
        "过去时与现在时交替",
        "past perfect",
        "past and present",
        "pastoral",
        "现在时叙述，但改用过去时",
        "过去时叙述，但不要使用这种时态。",
        "过去时？",
        "不知道采用过去时还是现在时。",
        "过去时已经不用。",
        "过去时叙述，仅限回忆段落；主线使用现在进行式。",
        "过去时叙述，正文实际采用将来时。",
        "过去时叙述，主线不按此规则。",
        "现在时叙述，仅限对白；叙述使用过去完成体。",
    ],
)
def test_unknown_conflicting_or_negative_prose_never_defaults_to_past(raw):
    assert canonical_creation_tense(raw) is None


@pytest.fixture
async def fresh(admission_db):
    d = admission_db
    project = m.ProjectModel(
        id=uuid4(),
        slug="synthetic-fresh-style-" + uuid4().hex[:8],
        title="Synthetic fresh chef story",
        language="zh-CN",
        genre="现实生活",
        status="planning",
        target_word_count=8000,
        target_chapters=4,
        metadata_json={
            "creation_intent_contract": {
                "field_sources": {"pov": "explicit"},
                "pov": "first_person",
            },
            "writing_profile_overrides": {"style": {"tense": RAW}},
            "retained_native_creation": "synthetic fixture",
        },
    )
    style = m.StyleGuideModel(
        project_id=project.id,
        pov_type="first_person",
        tense=RAW,
        custom_rules=["保留厨房具体行动。"],
    )
    d.session.add(project)
    await d.session.flush()
    d.session.add(style)
    await d.session.commit()
    d.fresh_project, d.fresh_style = project, style
    return d


@pytest.mark.asyncio
async def test_actual_description_kept_and_frozen_native_initializer_now_succeeds(fresh):
    d = fresh
    p, style = d.fresh_project, d.fresh_style
    metadata, old_review = deepcopy(p.metadata_json), deepcopy(d.report.structured_output)
    old_budget = (d.budget.calls_used, d.budget.tokens_charged, deepcopy(d.budget.metadata_json))
    receipt = await prepare_creation_style_invariant_boundary(
        d.session, project_id=p.id, requested_by="fixture-web"
    )
    assert receipt["changed"] and receipt["canonical_tense"] == "past"
    assert (
        receipt["body_gate_pass_asserted"]
        is receipt["credit_released"]
        is receipt["author_text_removed"]
        is False
    )
    await d.session.commit()
    assert style.tense == "past" and style.pov_type == "first_person"
    assert style.custom_rules == ["保留厨房具体行动。", RAW] and p.metadata_json == metadata
    wf = m.WorkflowRunModel(
        project_id=p.id,
        workflow_type="project_invariant_initialization",
        status="running",
        scope_type="project",
        scope_id=p.id,
        metadata_json={},
    )
    d.session.add(wf)
    await d.session.commit()
    actual = await ensure_missing_project_invariants(
        d.session, d.settings, project_id=p.id, workflow_id=wf.id
    )
    assert actual["status"] == "initialized" and p.invariants_json["tense"] == "past"
    assert p.invariants_json["pov"] == "first"
    await d.session.commit()
    again = await prepare_creation_style_invariant_boundary(d.session, project_id=p.id)
    assert again["changed"] is False and again["status"] == "existing_contract_preserved"
    assert style.custom_rules.count(RAW) == 1
    assert d.report.structured_output == old_review
    assert (d.budget.calls_used, d.budget.tokens_charged, d.budget.metadata_json) == old_budget
    async with d.factory() as reader:
        audits = list(
            await reader.scalars(
                select(m.WorkflowRunModel).where(
                    m.WorkflowRunModel.project_id == p.id,
                    m.WorkflowRunModel.workflow_type == "project_style_invariant_boundary",
                )
            )
        )
        assert len(audits) == 1 and audits[0].metadata_json["normalization"] == receipt
        stored = await reader.get(m.StyleGuideModel, p.id)
        assert stored.tense == "past" and RAW in stored.custom_rules


@pytest.mark.asyncio
async def test_rollback_restores_raw_style_and_discards_audit(fresh):
    d = fresh
    p = d.fresh_project
    receipt = await prepare_creation_style_invariant_boundary(d.session, project_id=p.id)
    assert receipt["changed"]
    pid = p.id
    await d.session.rollback()
    async with d.factory() as reader:
        stored = await reader.get(m.StyleGuideModel, pid)
        assert stored.tense == RAW and stored.custom_rules == ["保留厨房具体行动。"]
        assert not list(
            await reader.scalars(
                select(m.WorkflowRunModel.id).where(
                    m.WorkflowRunModel.project_id == pid,
                    m.WorkflowRunModel.workflow_type == "project_style_invariant_boundary",
                )
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "ambiguous",
        "stop",
        "pause",
        "existing_chapter",
        "existing_review",
        "active_workflow",
        "not_planning",
    ],
)
async def test_unproven_or_used_project_cannot_be_normalized(fresh, fault):
    d = fresh
    p, style = d.fresh_project, d.fresh_style
    if fault == "ambiguous":
        style.tense = "过去时与现在时交替"
    elif fault in {"stop", "pause"}:
        d.session.add(m.BookProductionControlModel(project_id=p.id, desired_state=fault))
    elif fault == "existing_chapter":
        d.session.add(m.ChapterModel(project_id=p.id, chapter_number=1, chapter_goal="existing"))
    elif fault == "existing_review":
        d.session.add(
            m.ReviewReportModel(
                project_id=p.id,
                target_type="project",
                target_id=p.id,
                reviewer_type="fixture",
                verdict="hold",
                structured_output={},
            )
        )
    elif fault == "active_workflow":
        d.session.add(
            m.WorkflowRunModel(
                project_id=p.id,
                workflow_type="foundation_plan",
                status="running",
                scope_type="project",
                scope_id=p.id,
                metadata_json={},
            )
        )
    else:
        p.status = "drafting"
    await d.session.commit()
    before = (style.tense, deepcopy(style.custom_rules), deepcopy(p.metadata_json))
    with pytest.raises(ValueError, match="creation_style_boundary"):
        await prepare_creation_style_invariant_boundary(d.session, project_id=p.id)
    assert (style.tense, style.custom_rules, p.metadata_json) == before
    assert not list(
        await d.session.scalars(
            select(m.WorkflowRunModel.id).where(
                m.WorkflowRunModel.project_id == p.id,
                m.WorkflowRunModel.workflow_type == "project_style_invariant_boundary",
            )
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["writing_profile", "writing_profile_overrides"])
@pytest.mark.parametrize(
    "profile",
    [
        None,
        [],
        {"style": None},
        {"style": {"tense": "present"}},
        {"style": {"tense": "回忆与现场交错"}},
    ],
)
async def test_unknown_or_opposing_current_metadata_profile_is_preserved_and_denied(
    fresh, key, profile
):
    d = fresh
    p, style = d.fresh_project, d.fresh_style
    p.metadata_json = {**p.metadata_json, key: deepcopy(profile)}
    await d.session.commit()
    before = deepcopy(p.metadata_json)
    with pytest.raises(ValueError, match="creation_style_boundary_profile"):
        await prepare_creation_style_invariant_boundary(d.session, project_id=p.id)
    assert p.metadata_json == before and style.tense == RAW
    assert style.custom_rules == ["保留厨房具体行动。"]


@pytest.mark.asyncio
async def test_canonical_existing_style_fast_path_has_no_row_lock_or_write(fresh):
    from sqlalchemy import event

    d = fresh
    p, style = d.fresh_project, d.fresh_style
    style.tense = "past"
    await d.session.commit()
    statements = []

    def only_read(execution):
        statements.append(execution.statement)
        assert execution.is_select and getattr(execution.statement, "_for_update_arg", None) is None

    event.listen(d.session.sync_session, "do_orm_execute", only_read)
    try:
        receipt = await prepare_creation_style_invariant_boundary(d.session, project_id=p.id)
    finally:
        event.remove(d.session.sync_session, "do_orm_execute", only_read)
    assert receipt["changed"] is False and statements and not d.session.dirty and not d.session.new


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [{}, {"style": {}}, {"style": {"pov_type": "first_person"}}])
async def test_partial_authoritative_profile_cannot_supply_an_opposing_runtime_default(
    fresh, profile
):
    from bestseller.services.writing_profile import get_project_writing_profile

    d = fresh
    p, style = d.fresh_project, d.fresh_style
    p.metadata_json = {**p.metadata_json, "writing_profile": deepcopy(profile)}
    await d.session.commit()
    assert get_project_writing_profile(p, style).style.tense == "present"
    with pytest.raises(ValueError, match="creation_style_boundary_profile_tense_missing"):
        await prepare_creation_style_invariant_boundary(d.session, project_id=p.id)
    assert style.tense == RAW and p.metadata_json["writing_profile"] == profile
