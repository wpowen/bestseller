"""Explicit creation POV is a read projection, never a legacy profile rewrite."""

from copy import deepcopy
import json
import socket
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb, _uuid_as_text  # noqa: F401

from bestseller.infra.db.models import ProjectModel, StyleGuideModel
from bestseller.services.content_origin_policy import content_hash, origin_record
from bestseller.services.creation_intent_contract import build_creation_intent_contract
from bestseller.services.genre_intent_contract import contract_from_selection
from bestseller.services.writing_profile import (
    get_project_runtime_writing_profile,
    get_project_writing_profile,
    render_writer_voice_data,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("POV tests forbid PG/provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def project_case(pov="first_person", *, raw_profile=True):
    contract = build_creation_intent_contract(
        contract_from_selection({"channel": "male", "genre": "xianxia"}),
        pov=pov, chapter_count=3, concept_seed="Map the winter sky.",
        field_sources={"pov": "explicit"},
    )
    raw = contract.model_dump(mode="json")
    metadata = {
        "creation_intent_contract": raw,
        "creation_intent_contract_hash": contract.contract_hash(),
        "creation_input_receipt": {
            "schema_version": contract.schema_version,
            "contract_hash": contract.contract_hash(),
            "field_sources": deepcopy(raw["field_sources"]),
        },
        "writing_profile_origins": {},
        "unrelated_history": [{"raw": None, "status": "old", "time": "unchanged"}],
    }
    if raw_profile:
        metadata["writing_profile"] = {
            "style": {"pov_type": "third-limited", "tense": "past", "tone_keywords": ["quiet"]},
        }
        metadata["writing_profile_origins"]["style.pov_type"] = origin_record(
            "third-limited", kind="unknown", producer="build_project_metadata",
        )
    project = ProjectModel(
        id=uuid4(), slug="winter-atlas-" + uuid4().hex[:8], title="Winter atlas",
        genre="玄幻", language="en", target_chapters=3, target_word_count=7000,
        metadata_json=metadata,
    )
    style = StyleGuideModel(
        project_id=project.id, pov_type="third-limited", tense="past",
        tone_keywords=["quiet"], prose_style="observational", sentence_style="mixed",
        info_density="medium", dialogue_ratio=0.4, taboo_words=[], taboo_topics=[],
        reference_works=[], custom_rules=[],
    )
    return project, style


@pytest.mark.parametrize("raw_profile", [True, False])
@pytest.mark.parametrize(
    ("selected", "expected"),
    [("first_person", "first-person"), ("first-person", "first-person"),
     ("first", "first-person"), ("close_third", "third-limited"),
     ("third_limited", "third-limited"), ("second_person", "second"),
     ("omniscient", "omniscient")],
)
def test_selected_pov_projects_both_profile_paths_without_mutating_raw(
    raw_profile, selected, expected
):
    project, style = project_case(selected, raw_profile=raw_profile)
    before = deepcopy(project.metadata_json)
    for reader in (get_project_writing_profile, get_project_runtime_writing_profile):
        result = reader(project, style)
        assert result.style.pov_type == expected
        assert result.style.tense == "past" and result.style.tone_keywords == ["quiet"]
        origin = result._content_origins["style.pov_type"]
        assert origin["adopted"] is False
        assert origin["content_hash"] == content_hash(expected)
        assert origin["creation_intent_hash"] == before["creation_intent_contract_hash"]
    assert project.metadata_json == before and style.pov_type == "third-limited"


@pytest.mark.parametrize(
    "missing", ["contract", "sources", "default", "legacy", "unknown", "pov", "hash"]
)
def test_missing_or_nonexplicit_evidence_never_selects_an_author_pov(missing):
    project, style = project_case()
    metadata = project.metadata_json
    if missing == "contract":
        metadata.pop("creation_intent_contract")
    elif missing == "sources":
        metadata["creation_intent_contract"].pop("field_sources")
    elif missing in {"default", "legacy", "unknown"}:
        metadata["creation_intent_contract"]["field_sources"]["pov"] = missing
    elif missing == "pov":
        metadata["creation_intent_contract"].pop("pov")
    else:
        metadata.pop("creation_intent_contract_hash")
    before = deepcopy(metadata)
    result = get_project_writing_profile(project, style)
    assert result.style.pov_type == "third-limited"
    assert "creation_intent_hash" not in result._content_origins["style.pov_type"]
    assert project.metadata_json == before


@pytest.mark.parametrize("fault", ["hash", "typed", "coercion", "receipt", "receipt_sources"])
def test_present_invalid_selected_proof_fails_closed(fault):
    project, style = project_case()
    metadata = project.metadata_json
    if fault == "hash":
        metadata["creation_intent_contract_hash"] = "0" * 64
    elif fault in {"typed", "coercion"}:
        raw = metadata["creation_intent_contract"]
        if fault == "typed":
            raw["unrecognized_input"] = "not a typed contract field"
        else:
            raw["chapter_count"] = True
        metadata["creation_intent_contract_hash"] = content_hash(raw)
        metadata.pop("creation_input_receipt")
    elif fault == "receipt":
        metadata["creation_input_receipt"]["contract_hash"] = "0" * 64
    else:
        metadata["creation_input_receipt"]["field_sources"]["pov"] = "default"
    before = deepcopy(metadata)
    with pytest.raises(ValueError, match="explicit_author_pov"):
        get_project_writing_profile(project, style)
    assert project.metadata_json == before


@pytest.mark.parametrize("stale_hash", [False, True])
def test_a_conflicting_or_unverifiable_adopted_choice_is_not_silently_overridden(stale_hash):
    project, style = project_case()
    evidence = origin_record("third-limited", kind="explicit", adopted=True)
    if stale_hash:
        evidence["content_hash"] = "0" * 64
    project.metadata_json["writing_profile_origins"]["style.pov_type"] = evidence
    before = deepcopy(project.metadata_json)
    with pytest.raises(ValueError, match="explicit_author_pov_adopted"):
        get_project_writing_profile(project, style)
    assert project.metadata_json == before


def test_equivalent_adopted_alias_retains_its_original_evidence():
    project, style = project_case()
    project.metadata_json["writing_profile"]["style"]["pov_type"] = "first"
    original = origin_record("first", kind="explicit", adopted=True)
    project.metadata_json["writing_profile_origins"]["style.pov_type"] = original
    before = deepcopy(project.metadata_json)
    result = get_project_writing_profile(project, style)
    assert result.style.pov_type == "first-person"
    origin = result._content_origins["style.pov_type"]
    assert origin["adopted"] is True
    assert origin["previous_origin"] == original and origin["previous_value"] == "first"
    assert project.metadata_json == before


@pytest.mark.parametrize("pov", ["first_person", "second_person"])
def test_existing_interactive_second_person_contract_is_not_silently_overridden(pov):
    project, style = project_case(pov)
    project.metadata_json["writing_profile"]["interactive_fiction"] = {
        "enabled": True, "if_genre": "mystery-survival",
    }
    before = deepcopy(project.metadata_json)
    if pov == "first_person":
        with pytest.raises(ValueError, match="explicit_author_pov_interactive_mode_conflict"):
            get_project_writing_profile(project, style)
    else:
        assert get_project_writing_profile(project, style).style.pov_type == "second"
    assert project.metadata_json == before


def test_actual_whole_chapter_prompt_keeps_all_pov_copies_consistent():
    from test_prose_prompt_profile import _prompts

    project, _ = project_case()
    before = deepcopy(project.metadata_json)
    system, user = _prompts(metadata=project.metadata_json)
    assert "叙述层全程第一人称" in system + user
    assert "叙述层全程第三人称" not in system + user
    assert "third-limited" not in system + user
    assert '"pov_type":"first-person"' in system + user
    assert project.metadata_json == before


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_profile", [True, False])
async def test_sqlite_writer_editor_and_style_readers_share_pov_with_zero_writes(
    raw_profile, tmp_path
):
    from bestseller.services import drafts, reviews
    from bestseller.services.editor_writing_voice_source import attach_current_editor_voice

    path = tmp_path / "explicit_author_pov_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.dialect.name == "sqlite"
    async with engine.begin() as connection:
        for model in (ProjectModel, StyleGuideModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            project, style = project_case(raw_profile=raw_profile)
            before = deepcopy(project.metadata_json)
            session.add_all([project, style])
            await session.commit()
            pid = project.id
        statements = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
                statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", observe)
        try:
            async with factory() as session:
                project = await session.get(ProjectModel, pid)
                style = await session.get(StyleGuideModel, pid)
                for reader in (get_project_writing_profile, get_project_runtime_writing_profile,
                               drafts._resolve_project_writing_profile,
                               reviews._resolve_project_writing_profile):
                    profile = reader(project, style)
                    assert profile.style.pov_type == "first-person"
                    voice = json.loads(render_writer_voice_data(profile).split("\n", 1)[1])
                    assert voice["style"]["pov_type"] == "first-person"
                    assert "FIRST person" in drafts._pov_hard_constraint(
                        profile.style.pov_type, language="en",
                    )
                source = {"old_grade": {"score": 66}, "raw": None, "draft": "Old prose."}
                attached = attach_current_editor_voice(source, project, style)
                assert attached["current_writing_voice"]["data"]["style"]["pov_type"] == (
                    "first-person"
                )
                assert source == {"old_grade": {"score": 66}, "raw": None, "draft": "Old prose."}
                assert project.metadata_json == before and style.pov_type == "third-limited"
                assert not session.new and not session.dirty and not session.deleted
                await session.commit()
            async with factory() as observer_session:
                project = await observer_session.get(ProjectModel, pid)
                style = await observer_session.get(StyleGuideModel, pid)
                assert project.metadata_json == before and style.pov_type == "third-limited"
            assert statements == []
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe)
    finally:
        await engine.dispose()
