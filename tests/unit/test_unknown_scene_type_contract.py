"""Unknown scene types stay empty across producer, schemas and SQLite readers."""

from copy import deepcopy
import json
import socket
from uuid import uuid4
from types import SimpleNamespace

from pydantic import ValidationError
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as lineage_db  # noqa: F401

from bestseller.domain.context import ChapterSceneContext, ChapterWriterContextPacket
from bestseller.domain.fanqie_short import FanqieShortBeat, FanqieShortBeatSheet
from bestseller.domain.project import SceneCardCreate, SceneStructureRead
from bestseller.domain.workflow import ChapterOutlineBatchInput, SceneOutlineInput
from bestseller.infra.db import models as db
from bestseller.services import context, inspection, llm, projects
from bestseller.services.fanqie_segment_projection import project_segment_outline
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit

SCHEMAS = (ChapterSceneContext, SceneCardCreate, SceneStructureRead, SceneOutlineInput)
BOUNDED_SCHEMAS = (SceneCardCreate, SceneOutlineInput)


class ForbiddenExternalWork(BaseException):
    """External work must fail even inside an optional context probe."""


@pytest.fixture(autouse=True)
def sqlite_only_offline(monkeypatch):
    # The reused lineage fixture supports PG elsewhere; this suite never does.
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")
    assert str(load_settings().database.url).startswith("sqlite+aiosqlite:")
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise ForbiddenExternalWork("Unknown-scene contract tests forbid network/model work")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    yield
    assert attempted == []


def payload_for(schema):
    payload = {"scene_number": 1}
    if schema in (ChapterSceneContext, SceneStructureRead):
        payload["status"] = "planned"
    if schema is SceneStructureRead:
        payload.update(id=uuid4(), target_word_count=700)
    return payload


def projected_outline(project, *, scene_type=None):
    return project_segment_outline(
        project,
        FanqieShortBeatSheet(
            beats=[
                FanqieShortBeat(
                    segment_number=number,
                    beat_role="observation",
                    purpose=f"Record measurement {number} without deciding its cause.",
                    opening_contract={} if scene_type is None else {"scene_type": scene_type},
                )
                for number in range(1, project.target_chapters + 1)
            ]
        ),
    )


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda schema: schema.__name__)
@pytest.mark.parametrize(
    "value", ["", "conflict", "development", "quiet observation", "  交接 / 观察\n", " \t"]
)
def test_scene_type_preserves_exact_string_and_roundtrips(schema, value):
    model = schema.model_validate({**payload_for(schema), "scene_type": value})
    assert model.scene_type == value
    assert model.model_dump()["scene_type"] == value
    assert schema.model_validate(model.model_dump()).scene_type == value
    assert schema.model_validate_json(model.model_dump_json()).scene_type == value


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda schema: schema.__name__)
@pytest.mark.parametrize("value", [None, 7, 1.5, True, [], {}, b"conflict", bytearray(b"setup")])
def test_scene_type_rejects_null_and_non_strings(schema, value):
    payload = {**payload_for(schema), "scene_type": value}
    with pytest.raises(ValidationError) as caught:
        schema.model_validate(payload)
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("scene_type",), "string_type")
    ]
    if not isinstance(value, (bytes, bytearray)):
        with pytest.raises(ValidationError):
            schema.model_validate_json(json.dumps(payload, default=str))


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda schema: schema.__name__)
def test_missing_type_keeps_legacy_outline_parser_and_other_entries_require_a_value(schema):
    payload = payload_for(schema)
    if schema is SceneOutlineInput:
        outline = schema.model_validate(payload)
        assert outline.scene_type == "development"
        assert outline.model_dump()["scene_type"] == "development"
        assert schema.model_validate_json(outline.model_dump_json()).scene_type == "development"
    else:
        with pytest.raises(ValidationError) as caught:
            schema.model_validate(payload)
        assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
            (("scene_type",), "missing")
        ]


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda schema: schema.__name__)
def test_original_scene_type_length_limits_are_preserved(schema):
    assert (
        schema.model_validate({**payload_for(schema), "scene_type": "观" * 4000}).scene_type
        == "观" * 4000
    )
    payload = {**payload_for(schema), "scene_type": "观" * 4001}
    if schema in BOUNDED_SCHEMAS:
        with pytest.raises(ValidationError) as caught:
            schema.model_validate(payload)
        assert caught.value.errors()[0]["type"] == "string_too_long"
        assert caught.value.errors()[0]["ctx"]["max_length"] == 4000
    else:
        assert schema.model_validate(payload).scene_type == payload["scene_type"]


@pytest.mark.parametrize("value", ["", "  observational / 交接\n"])
def test_outline_type_alias_preserves_value_and_other_fields(value):
    payload = {
        "scene_number": 2,
        "type": value,
        "title": "Original title",
        "participants": ["Ari"],
        "purpose": {"story": "Inspect the signed receipt."},
        "entry_state": {"seal": "intact"},
        "target_word_count": 880,
    }
    original = deepcopy(payload)
    outline = SceneOutlineInput.model_validate(payload)
    assert payload == original
    assert outline.scene_type == value
    assert (
        outline.model_dump()
        == SceneOutlineInput.model_validate(
            {"scene_type" if key == "type" else key: item for key, item in payload.items()}
        ).model_dump()
    )
    assert outline.title == payload["title"]
    assert outline.participants == payload["participants"]
    assert outline.purpose == payload["purpose"]
    assert outline.entry_state == payload["entry_state"]
    assert outline.target_word_count == 880
    assert SceneOutlineInput(scene_number=2, scene_type="", type="conflict").scene_type == ""


@pytest.mark.parametrize(
    "value",
    [None, 9, True, b"development", "x" * 4001],
    ids=["null", "number", "bool", "bytes", "too-long"],
)
def test_outline_alias_cannot_bypass_type_or_length_contract(value):
    with pytest.raises(ValidationError):
        SceneOutlineInput.model_validate({"scene_number": 1, "type": value})


def test_retained_omitted_field_source_keeps_its_original_materialized_projection():
    from bestseller.services.chapter_outline_readiness import _verify_materialized_content
    from bestseller.services.workflows import (
        _sync_chapter_causality_metadata, _sync_chapter_outline_semantics,
        _sync_scene_methodology_metadata,
    )

    raw = {"chapter_number": 1, "chapter_goal": "Observe a weather measurement.",
           "scenes": [{"scene_number": 1}]}
    original = deepcopy(raw)
    typed = ChapterOutlineBatchInput.model_validate({"chapters": [raw]}).chapters[0]
    chapter = SimpleNamespace(**typed.model_dump(exclude={"scenes"}), metadata_json={})
    _sync_chapter_causality_metadata(chapter, typed)
    _sync_chapter_outline_semantics(chapter, typed)
    scene = SimpleNamespace(**typed.scenes[0].model_dump(), metadata_json={})
    _sync_scene_methodology_metadata(scene, typed.scenes[0])
    # This literal is the historical parser's default, not a new story fact.
    scene.scene_type = "development"
    _verify_materialized_content(chapter, [scene], raw)
    assert raw == original and "scene_type" not in raw["scenes"][0]
    assert scene.scene_type == "development"


@pytest.mark.parametrize("genre", ["science-fiction", "everyday-life"])
@pytest.mark.parametrize("scene_type", [None, "", "  authored observation\n"])
def test_actual_producer_roundtrips_through_all_domain_entries(genre, scene_type):
    project = db.ProjectModel(
        slug="literal-source",
        title="Observatory records",
        genre=genre,
        target_chapters=4,
        target_word_count=8000,
        metadata_json={},
    )
    source = projected_outline(project, scene_type=scene_type)
    original = deepcopy(source)
    batch = ChapterOutlineBatchInput.model_validate(source)
    assert source == original
    assert ChapterOutlineBatchInput.model_validate_json(batch.model_dump_json()) == batch
    expected = "" if scene_type is None else scene_type
    for raw_chapter, chapter in zip(source["chapters"], batch.chapters, strict=True):
        raw = raw_chapter["scenes"][0]
        assert raw["scene_type"] == chapter.scenes[0].scene_type == expected
        for schema in SCHEMAS:
            model = schema.model_validate({**raw, **payload_for(schema)})
            assert schema.model_validate_json(model.model_dump_json()).scene_type == expected


@pytest.fixture
async def persisted_scene(lineage_db):  # noqa: F811
    """Extend the existing context/knowledge SQLite fixture; keep services real."""
    session, project, chapter, _draft = lineage_db
    connection = await session.connection()
    assert connection.dialect.name == "sqlite"
    # Reuse the fixture's JSONB/opaque UUID adapters and SQLite cast removal.
    # Context, RAG and the narrative tree read several optional model tables.
    for table in db.Base.metadata.tables.values():
        ddl = str(CreateTable(table, if_not_exists=True).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    volume = db.VolumeModel(project_id=project.id, volume_number=1, title="Measurements")
    session.add(volume)
    await session.flush()
    chapter.volume_id = volume.id
    raw = projected_outline(project)["chapters"][0]["scenes"][0]
    scene = db.SceneCardModel(
        project_id=project.id,
        chapter_id=chapter.id,
        metadata_json={},
        **{key: value for key, value in raw.items() if key in SceneCardCreate.model_fields},
    )
    session.add(scene)
    # As in the existing narrative-tree context fixtures, supply a persisted
    # visible node. This exercises reads without the PG-only tree rebuild lock.
    session.add(
        db.NarrativeTreeNodeModel(
            project_id=project.id,
            node_path="/chapters/001",
            parent_path="/chapters",
            depth=2,
            node_type="chapter",
            title="Measurements",
            summary=chapter.chapter_goal,
            body_md=chapter.chapter_goal,
            source_type="chapter",
            source_ref_id=chapter.id,
            scope_level="chapter",
            scope_chapter_number=1,
            lexical_document=chapter.chapter_goal,
            metadata_json={},
        )
    )
    await session.commit()
    # A new session proves the read paths consume the persisted empty value.
    factory = async_sessionmaker(
        connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as reader:
        yield reader, project.slug, chapter.chapter_number, scene.id


@pytest.mark.asyncio
async def test_sqlite_actual_structure_read_preserves_unknown(persisted_scene):
    session, slug, _number, scene_id = persisted_scene
    stored = await session.scalar(select(db.SceneCardModel).where(db.SceneCardModel.id == scene_id))
    assert stored.scene_type == ""
    structure = await inspection.build_project_structure(session, slug)
    assert structure.volumes[0].chapters[0].scenes[0].scene_type == ""
    assert structure.volumes[0].chapters[0].scenes[0].id == scene_id


@pytest.mark.asyncio
async def test_sqlite_actual_chapter_writer_context_preserves_unknown(persisted_scene):
    session, slug, chapter_number, scene_id = persisted_scene
    settings = load_settings()
    packet = await context.build_chapter_writer_context(session, settings, slug, chapter_number)
    assert packet.chapter_scenes[0].scene_type == ""
    assert packet.tree_context_nodes[0].node_path == "/chapters/001"
    assert (
        ChapterWriterContextPacket.model_validate_json(packet.model_dump_json())
        .chapter_scenes[0]
        .scene_type
        == ""
    )
    assert (
        await session.scalar(
            select(db.SceneCardModel.scene_type).where(db.SceneCardModel.id == scene_id)
        )
        == ""
    )


@pytest.mark.asyncio
async def test_sqlite_actual_create_entry_preserves_unknown(persisted_scene):
    session, slug, chapter_number, _scene_id = persisted_scene
    scene = await projects.create_scene_card(
        session,
        slug,
        chapter_number,
        SceneCardCreate(scene_number=2, scene_type=""),
    )
    await session.flush()
    await session.refresh(scene)
    assert scene.scene_type == ""
    structure = await inspection.build_project_structure(session, slug)
    assert [item.scene_type for item in structure.volumes[0].chapters[0].scenes] == ["", ""]
