"""RED contracts for main to run before authorizing native length wiring.

Only zero-adoption-prefix positives. No fabricated grade/current/promoted
authorization; native multi-chapter adoption proof belongs to main. The AST
cases supplement the behavioral cases with entrypoint/order coverage only.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.domain.context import ChapterWriterContextPacket
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    SceneCardModel,
)
from bestseller.services import deslop_revise, drafts, llm, pipelines, projects, reviews
from bestseller.services import full_flow_length as length
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit
FLAG = {"protocol": "book-length-control-v1", "counting": "export-body-words-v1"}
_SERVICES = Path(drafts.__file__).parent


@compiles(JSONB, "sqlite")
def _jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
async def wiring_case(tmp_path, monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("No real model call is permitted in length wiring tests")

    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(deslop_revise, "complete_text", denied)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'length_wiring_test.sqlite3'}")
    try:
        async with engine.begin() as connection:
            for model in (ProjectModel, ChapterModel, ChapterDraftVersionModel):
                ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
                await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            project = ProjectModel(
                slug=f"length-wiring-{uuid4().hex}",
                title="Fixture",
                genre="quiet travel",
                language="zh-CN",
                target_word_count=8000,
                target_chapters=4,
                metadata_json={"book_length_control": deepcopy(FLAG)},
            )
            session.add(project)
            await session.flush()
            chapters = [
                ChapterModel(
                    project_id=project.id,
                    chapter_number=number,
                    chapter_goal="Return the key",
                    title="Door",
                    target_word_count=2000,
                    metadata_json={},
                    information_revealed=[],
                    information_withheld=[],
                    foreshadowing_actions={},
                )
                for number in range(1, 5)
            ]
            session.add_all(chapters)
            await session.flush()
            settings = load_settings(
                env={
                    "BESTSELLER__GENERATION__WORDS_PER_CHAPTER__MIN": "1800",
                    "BESTSELLER__GENERATION__WORDS_PER_CHAPTER__TARGET": "2000",
                    "BESTSELLER__GENERATION__WORDS_PER_CHAPTER__MAX": "3500",
                }
            )
            monkeypatch.setattr(drafts, "load_settings", lambda: settings)
            monkeypatch.setattr(reviews, "get_settings", lambda: settings)
            chapter = chapters[0]
            context = ChapterWriterContextPacket(
                project_id=project.id,
                project_slug=project.slug,
                chapter_id=chapter.id,
                chapter_number=1,
                query_text="Write the selected chapter",
                chapter_goal=chapter.chapter_goal,
            )
            yield SimpleNamespace(
                session=session,
                project=project,
                chapter=chapter,
                chapters=chapters,
                settings=settings,
                context=context,
            )
            await session.rollback()
    finally:
        await engine.dispose()


async def _request(case, stage="draft"):
    parent = None
    if stage != "draft":
        parent = ChapterDraftVersionModel(
            project_id=case.project.id,
            chapter_id=case.chapter.id,
            version_no=1,
            content_md="字" * 1900,
            word_count=9999,
            is_current=False,
            promotion_state="candidate",
            promotion_metadata={},
            assembled_from_scene_draft_ids=[],
        )
        case.session.add(parent)
        await case.session.flush()
    request = await length.freeze_request(
        case.session,
        case.settings,
        project=case.project,
        chapter=case.chapter,
        stage=stage,
        parent_draft=parent,
    )
    assert request["feasible"] is True
    return request, parent


def _prompt_text(prepared):
    if hasattr(prepared, "system"):
        return prepared.system + "\n" + prepared.user
    return "\n".join(prepared)


def _assert_frozen_band(text, request):
    band = request["window"]
    assert f"{band['min_words']}-{band['max_words']}" in text
    assert str(band["target_words"]) in text
    assert request["protocol"] in text
    assert request["counting"] in text
    assert request["digest"] in text
    assert "1800-3500" not in text


@pytest.mark.parametrize("language,enabled", [("zh-CN", True), ("en-US", False)])
async def test_create_project_defaults_v1_only_for_new_chinese_projects(
    monkeypatch, language, enabled
):
    # Exercise real create_project metadata construction; isolate unrelated
    # filesystem/capability setup, without creating grade/adoption evidence.
    from bestseller.services import book_packaging_store, planning_kernel

    added = []

    def add(row):
        if isinstance(row, ProjectModel):
            row.id = uuid4()
        added.append(row)

    session = SimpleNamespace(add=add, flush=AsyncMock())
    monkeypatch.setattr(projects, "get_project_by_slug", AsyncMock(return_value=None))
    monkeypatch.setattr(
        projects, "_find_duplicate_fanqie_short_project", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(projects, "clear_project_delete_tombstone", lambda *_: None)
    monkeypatch.setattr(projects, "initialize_project_genre_capabilities", AsyncMock())
    monkeypatch.setattr(planning_kernel, "persist_project_planning_kernel", lambda *_, **__: None)
    monkeypatch.setattr(
        book_packaging_store, "promote_creation_packaging_contract_if_ready", AsyncMock()
    )
    payload = ProjectCreate(
        slug=f"length-new-{uuid4().hex[:10]}",
        title="Fixture",
        genre="quiet travel",
        language=language,
        target_word_count=8000,
        target_chapters=4,
        metadata={"user_marker": "preserve"},
    )
    project = await projects.create_project(session, payload, load_settings(env={}))
    assert project in added and project.metadata_json["user_marker"] == "preserve"
    assert (project.metadata_json.get("book_length_control") == FLAG) is enabled
    assert "book_length_control" not in payload.metadata


@pytest.mark.parametrize("profile", ["lean", "full"])
@pytest.mark.parametrize("mode", ["legacy", "compiled"])
async def test_writer_prompt_uses_frozen_window_in_every_profile(wiring_case, profile, mode):
    case = wiring_case
    request, _ = await _request(case)
    scene = SceneCardModel(
        id=uuid4(),
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        scene_number=1,
        scene_type="confrontation",
        title="Door",
        time_label="now",
        participants=[],
        purpose={},
        entry_state={},
        exit_state={},
        key_dialogue_beats=[],
        sensory_anchors={},
        forbidden_actions=[],
        metadata_json={},
        target_word_count=2000,
    )
    prepared = drafts.build_chapter_first_draft_prompts(
        case.project,
        case.chapter,
        [scene],
        None,
        case.context,
        target_word_count=2000,
        prompt_mode=mode,
        prose_prompt_profile=profile,
        length_request=request,
    )
    _assert_frozen_band(_prompt_text(prepared), request)
    assert case.chapter.target_word_count == 2000


async def test_rewrite_prompt_uses_frozen_window_and_preserves_complete_parent(wiring_case):
    case = wiring_case
    request, parent = await _request(case, "rewrite")
    task = SimpleNamespace(
        instructions="Fix the supported pronoun", rewrite_strategy="targeted", metadata_json={}
    )
    prepared = reviews.build_chapter_rewrite_prompts(
        case.project,
        case.chapter,
        parent,
        task,
        case.context,
        length_request=request,
    )
    text = _prompt_text(prepared)
    _assert_frozen_band(text, request)
    assert parent.content_md in text
    assert case.chapter.target_word_count == 2000


class _StopBeforeModel(BaseException):
    """Escape broad model-error handlers without sending any model request."""


async def test_deslop_prompt_uses_actual_parent_cap_before_model(wiring_case, monkeypatch):
    case = wiring_case
    request, parent = await _request(case, "deslop")
    captured = []

    async def capture(_session, _settings, model_request):
        captured.append(model_request)
        raise _StopBeforeModel

    monkeypatch.setattr(deslop_revise, "_findings_text", lambda *_: ("fixture issue", 1.0, 1))
    monkeypatch.setattr(deslop_revise, "complete_text", capture)
    with pytest.raises(_StopBeforeModel):
        await deslop_revise.revise_prose_deslop(
            case.session,
            case.settings,
            content=parent.content_md,
            project_id=case.project.id,
            chapter_number=1,
            rounds=1,
            target_chars=2000,
            length_request=request,
        )
    assert len(captured) == 1
    assert request["window"]["max_words"] == 1995
    _assert_frozen_band(captured[0].system_prompt + "\n" + captured[0].user_prompt, request)


@pytest.mark.parametrize("words", [1995, 1996, 2301])
async def test_deslop_candidate_persists_request_and_non_authoritative_preview(wiring_case, words):
    case = wiring_case
    request, parent = await _request(case, "deslop")
    candidate = await pipelines._persist_deslop_candidate(
        case.session,
        chapter=case.chapter,
        parent_draft=parent,
        content="字" * words,
        llm_run_id=None,
        round_no=1,
        length_request=request,
    )
    assert candidate.promotion_metadata["book_length_control"] == request
    preview = candidate.promotion_metadata["book_length_control_candidate"]
    assert preview["request_digest"] == request["digest"]
    assert preview["length_passed"] is (words <= 1995)
    assert all(preview[key] is False for key in ("grade", "adoption", "credit", "dispatch"))
    assert candidate.is_current is False and candidate.promotion_state == "candidate"
    assert case.chapter.target_word_count == 2000


async def test_deslop_selection_rejects_v1_over_cap_after_flag_removal(wiring_case):
    case = wiring_case
    request, parent = await _request(case, "deslop")
    candidate = ChapterDraftVersionModel(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        version_no=2,
        content_md="字" * 1996,
        word_count=1900,
        promotion_state="candidate",
        is_current=False,
        assembled_from_scene_draft_ids=[],
        promotion_metadata={"book_length_control": request, "parent_draft_id": str(parent.id)},
    )
    case.session.add(candidate)
    await case.session.flush()
    await case.session.execute(
        update(ProjectModel).where(ProjectModel.id == case.project.id).values(metadata_json={})
    )
    with pytest.raises(length.LengthControlError):
        await pipelines._select_deslop_candidate(
            case.session,
            chapter=case.chapter,
            candidate=candidate,
            selected_reason="cleaner",
        )
    assert candidate.is_current is False and candidate.promotion_state == "candidate"


async def test_deslop_selection_revalidates_parent_sha_not_cached_count(wiring_case):
    case = wiring_case
    request, parent = await _request(case, "deslop")
    candidate = ChapterDraftVersionModel(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        version_no=2,
        content_md="字" * 1900,
        word_count=1900,
        promotion_state="candidate",
        is_current=False,
        assembled_from_scene_draft_ids=[],
        promotion_metadata={"book_length_control": request, "parent_draft_id": str(parent.id)},
    )
    case.session.add(candidate)
    await case.session.flush()
    await case.session.execute(
        update(ChapterDraftVersionModel)
        .where(
            ChapterDraftVersionModel.id == parent.id,
        )
        .values(content_md="文" * 1900)
        .execution_options(synchronize_session=False)
    )
    with pytest.raises(length.LengthControlError):
        await pipelines._select_deslop_candidate(
            case.session,
            chapter=case.chapter,
            candidate=candidate,
            selected_reason="cleaner",
        )
    assert candidate.is_current is False


async def test_reviewed_promotion_cannot_bypass_v1_length_via_stub_shortcut(wiring_case):
    case = wiring_case
    request, _ = await _request(case)
    candidate = ChapterDraftVersionModel(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        version_no=1,
        content_md="字" * 2301,
        promotion_state="candidate",
        is_current=False,
        promotion_metadata={"book_length_control": request},
    )
    case.session.add(candidate)
    await case.session.flush()
    # Negative only: the pre-existing quality stub shortcut grants no positive
    # grade or adoption fixture here. A v1 over-band body must not return True.
    try:
        promoted, reason = await pipelines._promote_reviewed_chapter_draft(
            case.session,
            settings=case.settings,
            project=case.project,
            chapter=case.chapter,
            draft=candidate,
            quality=object(),
            workflow_run_id=None,
        )
    except length.LengthControlError:
        promoted, reason = False, "LENGTH_CONTROL"
    assert promoted is False and "LENGTH_CONTROL" in reason.upper()
    assert candidate.promotion_state == "candidate"


def _function(filename, name):
    tree = ast.parse((_SERVICES / filename).read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _calls(node, name):
    return sorted(
        call.lineno
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and (
            (isinstance(call.func, ast.Name) and call.func.id == name)
            or (isinstance(call.func, ast.Attribute) and call.func.attr == name)
        )
    )


@pytest.mark.parametrize(
    "filename,entrypoint",
    [
        ("drafts.py", "generate_chapter_draft_once"),
        ("reviews.py", "rewrite_chapter_from_task"),
    ],
)
def test_native_generation_freezes_before_model_dispatch(filename, entrypoint):
    node = _function(filename, entrypoint)
    freezes, dispatches = _calls(node, "freeze_request"), _calls(node, "complete_text")
    assert freezes, f"{entrypoint} has no native request freeze"
    assert dispatches, f"Audit helper needs updating for {entrypoint}'s model entrypoint"
    assert min(freezes) < min(dispatches)
    assert _calls(node, "evaluate_candidate"), f"{entrypoint} omits candidate length preview"
    assert "book_length_control" in ast.unparse(node), (
        f"{entrypoint} omits persistent request binding"
    )


def test_native_assembly_evaluates_and_persists_the_same_contract():
    node = _function("drafts.py", "assemble_chapter_draft")
    assert _calls(node, "freeze_request"), "scene assembly has no native contract"
    assert _calls(node, "evaluate_candidate"), "assembled prose bypasses candidate length check"
    assert "book_length_control" in ast.unparse(node)


def test_native_promotion_revalidates_before_adoption_and_refreshes_after():
    node = _function("draft_promotion.py", "promote_best_draft")
    rechecks = _calls(node, "revalidate_request")
    adoptions = _calls(node, "_advance_to_promoted")
    refreshes = _calls(node, "refresh_after_adoption")
    assert rechecks, "native adoption lacks the actual-source/old-field CAS length fence"
    assert adoptions, "Audit helper needs updating for the native adoption mutation"
    assert refreshes, "native adoption never recounts the durable adopted prefix"
    assert min(rechecks) < min(adoptions) < max(refreshes)
    assert _calls(node, "evaluate_candidate"), (
        "native promotion trusts cached preview instead of body"
    )


def test_pipeline_freezes_deslop_contract_before_deslop_dispatch():
    node = _function("pipelines.py", "run_chapter_pipeline")
    dispatches = _calls(node, "revise_prose_deslop")
    freezes = _calls(node, "freeze_request")
    assert dispatches, "Audit helper needs updating for the pipeline deslop entrypoint"
    assert freezes and min(freezes) < min(dispatches)
    deslop_calls = [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and (
            (isinstance(call.func, ast.Name) and call.func.id == "revise_prose_deslop")
            or (isinstance(call.func, ast.Attribute) and call.func.attr == "revise_prose_deslop")
        )
    ]
    assert all(
        any(keyword.arg == "length_request" for keyword in call.keywords) for call in deslop_calls
    ), "deslop dispatch loses its pre-model frozen request"
