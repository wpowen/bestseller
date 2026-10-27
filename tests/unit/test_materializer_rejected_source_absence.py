"""Real offline SQLite: explicit revocation is absence, ambiguity is not."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_adopted_knowledge_resume_control_integrity import configured as configured

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
)
from bestseller.services.adopted_chapter_source import (
    _continuity_source_blocked,
    continuity_source_key,
    explicit_continuity_source_rejection,
    select_adopted_chapter_source,
)
from bestseller.services.chapter_scene_contract_materializer import (
    chapter_contract_has_adopted_source,
)
from bestseller.services.reviews import chapter_review_body_fingerprint

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def receipt(draft, passed=False):
    return {
        "draft_id": str(draft.id),
        "body_sha256": chapter_review_body_fingerprint(draft.content_md)["digest"],
        "passed": passed,
        "reason": "source_correction",
        "reports": [{"code": "COUNTDOWN_REGRESSION", "source": "preserved evidence"}],
    }


def bind(chapter, value):
    meta = deepcopy(chapter.metadata_json)
    meta.setdefault("continuity_admission_by_source", {})[continuity_source_key(value)] = deepcopy(
        value
    )
    chapter.metadata_json = meta


@pytest.fixture
async def sources(configured):
    session, _, project, chapter, old = configured
    old.is_current = False
    old.content_md = "The clock crossed midnight.\r\nThe courier recorded the time.\rA seal broke."
    old.promotion_metadata = {"preserve": ["original generation"]}
    candidate = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="The courier corrected the dated entry.",
        promotion_state="eligible",
        is_current=True,
        promotion_metadata={"keep": "candidate"},
    )
    session.add(candidate)
    await session.commit()
    return SimpleNamespace(
        session=session, project=project, chapter=chapter, old=old, candidate=candidate
    )


async def snapshot(session):
    return [
        deepcopy(list((await session.execute(select(model.__table__))).mappings()))
        for model in (
            ProjectModel,
            ChapterModel,
            ChapterDraftVersionModel,
            QualityScoreModel,
            ReviewReportModel,
        )
    ]


async def classify_unchanged(d, expected):
    await d.session.commit()
    before = await snapshot(d.session)
    writes = []

    def inspect_sql(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    engine = d.session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", inspect_sql)
    try:
        if expected == "ambiguous":
            with pytest.raises(ValueError, match=r"^materializer_adoption_ambiguous$"):
                await chapter_contract_has_adopted_source(d.session, chapter=d.chapter)
        else:
            assert (
                await chapter_contract_has_adopted_source(d.session, chapter=d.chapter) is expected
            )
    finally:
        event.remove(engine, "before_cursor_execute", inspect_sql)
    assert not writes
    assert await snapshot(d.session) == before
    assert not d.session.new and not d.session.dirty and not d.session.deleted


@pytest.mark.parametrize("title", ["Harbor Atlas", "山城夜班手记"])
@pytest.mark.parametrize("quality", ["missing", "high", "ineligible"])
async def test_exact_rejected_promotion_is_absence_independent_of_quality(sources, title, quality):
    d = sources
    d.project.title = title
    rejected = receipt(d.old)
    bind(d.chapter, rejected)
    if quality != "missing":
        d.session.add(
            QualityScoreModel(
                project_id=d.project.id,
                target_type="chapter",
                target_id=d.chapter.id,
                chapter_draft_version_id=d.old.id,
                judge_key="offline",
                score_overall=0.99,
                score_goal=0.99,
                score_conflict=0.99,
                score_emotion=0.99,
                score_style=0.99,
                evidence_summary={"hard_gates_passed": quality == "high"},
            )
        )
    await d.session.commit()
    selected, _ = await select_adopted_chapter_source(d.session, chapter=d.chapter, lock=False)
    assert selected is None
    audit = explicit_continuity_source_rejection(chapter=d.chapter, draft=d.old)
    assert audit == {
        "project_id": str(d.project.id),
        "chapter_id": str(d.chapter.id),
        "draft_id": str(d.old.id),
        "body_sha256": rejected["body_sha256"],
        "source_key": continuity_source_key(rejected),
        "receipt": rejected,
    }
    assert _continuity_source_blocked(d.chapter, d.old)
    await classify_unchanged(d, False)
    assert d.old.promotion_state == "promoted" and d.candidate.promotion_state == "eligible"


@pytest.mark.parametrize("passed", [None, 0, "false", [], {}])
async def test_unknown_exact_receipt_is_blocked_but_never_explicit_revocation(sources, passed):
    d = sources
    bind(d.chapter, receipt(d.old, passed))
    await d.session.commit()
    assert _continuity_source_blocked(d.chapter, d.old)
    assert explicit_continuity_source_rejection(chapter=d.chapter, draft=d.old) is None
    await classify_unchanged(d, "ambiguous")


async def add_raw(d, *, rejected=True):
    raw = ChapterDraftVersionModel(
        project_id=d.project.id,
        chapter_id=d.chapter.id,
        version_no=3,
        content_md="A separate retained source.",
        promotion_state="promoted",
        is_current=False,
        promotion_metadata={"preserve": "ambiguous legacy source"},
    )
    d.session.add(raw)
    await d.session.flush()
    if rejected:
        bind(d.chapter, receipt(raw))
    return raw


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "none",
        "malformed",
        "wrong_body",
        "wrong_draft",
        "wrong_key",
        "missing_passed",
        "valid",
        "wrong_body_under_correct_key",
    ],
)
async def test_every_raw_promoted_source_must_be_proven_negative(sources, fault):
    d = sources
    bind(d.chapter, receipt(d.old))
    other = await add_raw(d, rejected=False)
    value = receipt(other)
    key = continuity_source_key(value)
    meta = deepcopy(d.chapter.metadata_json)
    ledger = meta["continuity_admission_by_source"]
    if fault == "none":
        value["passed"] = None
        ledger[key] = value
    elif fault == "malformed":
        ledger[key] = [value]
    elif fault == "wrong_body":
        value["body_sha256"] = "0" * 64
        ledger[continuity_source_key(value)] = value
    elif fault == "wrong_draft":
        value["draft_id"] = str(uuid4())
        ledger[continuity_source_key(value)] = value
    elif fault == "wrong_key":
        ledger["unbound"] = value
    elif fault == "wrong_body_under_correct_key":
        value["body_sha256"] = "0" * 64
        ledger[key] = value
    elif fault == "missing_passed":
        value.pop("passed")
        ledger[key] = value
    elif fault == "valid":
        value["passed"] = True
        ledger[key] = value
    d.chapter.metadata_json = meta
    await classify_unchanged(d, "ambiguous")


async def test_all_ambiguous_raw_sources_explicitly_rejected_is_absence(sources):
    d = sources
    bind(d.chapter, receipt(d.old))
    await add_raw(d)
    await classify_unchanged(d, False)


@pytest.mark.parametrize(
    "marker",
    [
        {},
        {"draft_id": "invalid"},
        {"draft_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
        False,
        "unknown",
    ],
)
async def test_numeric_debt_ambiguity_guard_still_precedes_explicit_absence(sources, marker):
    d = sources
    bind(d.chapter, receipt(d.old))
    d.chapter.metadata_json = {**d.chapter.metadata_json, "numeric_promotion_debt": marker}
    await classify_unchanged(d, "ambiguous")


@pytest.mark.parametrize("admission", ["missing", "valid", "later_valid"])
async def test_canonical_selected_source_remains_adopted(sources, admission):
    d = sources
    if admission != "missing":
        if admission == "later_valid":
            d.chapter.metadata_json = {
                **d.chapter.metadata_json,
                "continuity_admission_history": [receipt(d.old)],
                "continuity_adoption_block": receipt(d.old),
            }
        bind(d.chapter, receipt(d.old, True))
    await classify_unchanged(d, True)


async def test_no_owned_promotions_is_absence(sources):
    d = sources
    d.old.promotion_state = "superseded"
    await classify_unchanged(d, False)


async def test_foreign_promoted_rows_and_receipts_cannot_change_owned_absence(sources):
    d = sources
    bind(d.chapter, receipt(d.old))
    foreign = ProjectModel(
        slug=f"unrelated-{uuid4().hex}",
        title="An unrelated archive",
        genre="test",
        target_word_count=10000,
        target_chapters=1,
    )
    d.session.add(foreign)
    await d.session.flush()
    foreign_chapter = ChapterModel(
        project_id=foreign.id, chapter_number=1, chapter_goal="Independent source", metadata_json={}
    )
    d.session.add(foreign_chapter)
    await d.session.flush()
    raw = ChapterDraftVersionModel(
        project_id=foreign.id,
        chapter_id=foreign_chapter.id,
        version_no=1,
        content_md=d.old.content_md,
        is_current=True,
        promotion_state="promoted",
    )
    d.session.add(raw)
    await d.session.flush()
    bind(d.chapter, receipt(raw))
    assert explicit_continuity_source_rejection(chapter=d.chapter, draft=raw) is None
    await classify_unchanged(d, False)


@pytest.mark.parametrize(
    "mutation", ["wrong_project", "wrong_chapter", "current_false", "current_true"]
)
async def test_shared_helper_ownership_and_current_pointer_parity(sources, mutation):
    d = sources
    bind(d.chapter, receipt(d.old))
    draft = SimpleNamespace(
        id=d.old.id,
        project_id=d.project.id,
        chapter_id=d.chapter.id,
        content_md=d.old.content_md,
        is_current=mutation == "current_true",
    )
    if mutation == "wrong_project":
        draft.project_id = uuid4()
    elif mutation == "wrong_chapter":
        draft.chapter_id = uuid4()
    before = deepcopy(d.chapter.metadata_json)
    audit = explicit_continuity_source_rejection(chapter=d.chapter, draft=draft)
    assert (audit is not None) is mutation.startswith("current_")
    if audit is not None:
        audit["receipt"]["reports"][0]["code"] = "mutated copy"
    assert d.chapter.metadata_json == before


@pytest.mark.parametrize("newlines", ["\n", "\r\n", "\r"])
async def test_shared_helper_uses_canonical_normalized_body_identity(sources, newlines):
    d = sources
    d.old.content_md = newlines.join(["An interval begins.", "An interval ends."])
    bind(d.chapter, receipt(d.old))
    assert _continuity_source_blocked(d.chapter, d.old)
    audit = explicit_continuity_source_rejection(chapter=d.chapter, draft=d.old)
    assert audit["body_sha256"] == chapter_review_body_fingerprint(d.old.content_md)["digest"]
    await classify_unchanged(d, False)
