"""Native SQLite projections: data-only repair, exact no-op and atomic failure.

Reuse the isolated *_test SQLite fixture and SQLite advisory substitutions.
There are no PostgreSQL, network, provider or deployment claims here.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.schema import CreateTable
from test_world_current_tree_consumers import db as db
from test_world_current_tree_consumers import knowledge_db as knowledge_db
from test_world_current_tree_consumers import offline as offline
from test_world_current_tree_consumers import retired as retired
from test_world_current_tree_consumers import seeded as seeded
from test_world_current_tree_consumers import tree_tables as tree_tables

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    LlmRunModel,
    NarrativeTreeNodeModel,
    ProjectModel,
    QualityScoreModel,
    RelationshipModel,
    RetrievalChunkModel,
    VolumeModel,
    WorkflowRunModel,
    WorldRuleModel,
)
from bestseller.services import narrative_tree, retrieval, story_bible_projection
from bestseller.services.story_bible_read_views import ensure_story_bible_read_views
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def views(db, tree_tables):
    session, project, chapter, draft = db
    assert session.bind.dialect.name == "sqlite"
    assert "test" in session.bind.engine.url.database
    connection = await session.connection()
    for model in (RetrievalChunkModel, WorkflowRunModel, LlmRunModel):
        ddl = str(
            CreateTable(model.__table__, if_not_exists=True).compile(dialect=connection.dialect)
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    await session.commit()
    return session, project, chapter, draft, load_settings(env={})


async def _snapshot(session, models):
    # Raw database rows, including IDs, timestamps, metadata and embeddings.
    return {
        model.__tablename__: [
            tuple(row)
            for row in await session.execute(
                text(f'SELECT * FROM "{model.__tablename__}" ORDER BY id')
            )
        ]
        for model in models
    }


async def _derived(session):
    return await _snapshot(session, (RetrievalChunkModel, NarrativeTreeNodeModel))


def _rule(project, number):
    row = WorldRuleModel(
        id=uuid4(),
        project_id=project.id,
        rule_code=f"航规{number}",
        name=f"潮汐航规{number}",
        description=f"第{number}次落潮后才可起航。",
        story_consequence="提前起航将搁浅。",
        metadata_json={
            "story_bible_update_history": [{"preserved": number}],
        },
    )
    story_bible_projection.capture_bible_baseline(row)
    return row


async def _world_ids(session, project):
    rag = set(
        await session.scalars(
            select(RetrievalChunkModel.source_id).where(
                RetrievalChunkModel.project_id == project.id,
                RetrievalChunkModel.source_type == "world_rule",
            )
        )
    )
    tree = set(
        await session.scalars(
            select(NarrativeTreeNodeModel.source_ref_id).where(
                NarrativeTreeNodeModel.project_id == project.id,
                NarrativeTreeNodeModel.source_type == "world_rule",
            )
        )
    )
    return rag, tree


async def _no_writes(session, call):
    statements = []

    def record(_conn, _cursor, statement, *_args):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            statements.append(statement)

    event.listen(session.bind.engine.sync_engine, "before_cursor_execute", record)
    try:
        result = await call()
    finally:
        event.remove(session.bind.engine.sync_engine, "before_cursor_execute", record)
    assert statements == []
    assert not result["refreshed"]
    return result


async def test_native_data_only_13_to_18_repair_preserves_sources_paid_grades_and_foreign(views):
    session, project, chapter, draft, settings = views
    project.title = "渡船与潮汐"
    project.metadata_json = {"premium_state_ledger": {"retained": True}}
    rules = [_rule(project, number) for number in range(13)]
    foreign = ProjectModel(
        slug=f"foreign-ship-test-{uuid4().hex}",
        title="Other shipping book",
        genre="test",
        target_word_count=10000,
        target_chapters=4,
        metadata_json={},
    )
    paid = LlmRunModel(
        project_id=project.id,
        logical_role="summarizer",
        provider="retained",
        model_name="retained",
        input_tokens=93,
        output_tokens=24,
        metadata_json={"paid_history": [1, 2, 3]},
    )
    grade = QualityScoreModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        chapter_draft_version_id=draft.id,
        score_overall=0.91,
        evidence_summary={"version": "keep"},
    )
    workflow = WorkflowRunModel(
        project_id=project.id,
        workflow_type="chapter_pipeline",
        status="completed",
        metadata_json={"calls": 217, "retained": True},
    )
    session.add_all([*rules, foreign, paid, grade, workflow])
    await session.flush()
    session.add(_rule(foreign, 99))
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=foreign)
    await retrieval.refresh_story_bible_retrieval_index(session, settings, project.id)
    await narrative_tree.rebuild_narrative_tree(session, project=project)
    await session.commit()
    assert _world_count(await _world_ids(session, project)) == (13, 13)
    session.add_all([_rule(project, number) for number in range(13, 18)])
    # A protected non-Bible chunk is independent of source/derived receipts.
    await retrieval._upsert_source_chunks(
        session,
        settings,
        project_id=project.id,
        source_type="chapter_draft",
        source_id=draft.id,
        text=draft.content_md,
        metadata={"protected": True},
    )
    await session.commit()
    protected_models = (
        ProjectModel,
        ChapterModel,
        ChapterDraftVersionModel,
        WorldRuleModel,
        CharacterModel,
        RelationshipModel,
        QualityScoreModel,
        LlmRunModel,
        WorkflowRunModel,
    )
    protected = await _snapshot(session, protected_models)
    previous = await _derived(session)
    result = await ensure_story_bible_read_views(session, settings, project=project)
    assert result["refreshed"] and result["source_counts"]["world_rule"] == 18
    assert result["retrieval_chunk_count"] == 18
    assert _world_count(await _world_ids(session, project)) == (18, 18)
    assert await _snapshot(session, protected_models) == protected
    after = await _derived(session)
    for table, rows in previous.items():
        # Inspect columns instead of assuming a native column order.
        model = RetrievalChunkModel if table == "retrieval_chunks" else NarrativeTreeNodeModel
        index = list(model.__table__.c.keys()).index("project_id")
        protected_rows = [row for row in rows if row[index] == foreign.id.hex]
        assert protected_rows and all(row in after[table] for row in protected_rows)
    chunk = await session.scalar(
        select(RetrievalChunkModel).where(
            RetrievalChunkModel.source_type == "chapter_draft",
        )
    )
    assert chunk.chunk_text == draft.content_md and chunk.metadata_json == {"protected": True}
    await session.commit()
    before = await _derived(session)
    for _ in range(2):
        repeated = await _no_writes(
            session, lambda: ensure_story_bible_read_views(session, settings, project=project)
        )
        assert repeated["source_sha256"] == result["source_sha256"]
        assert await _derived(session) == before


def _world_count(ids):
    return tuple(len(group) for group in ids)


async def test_same_id_text_tree_only_fields_and_relationships_change_without_runtime_churn(views):
    session, project, chapter, _draft, settings = views
    rule = _rule(project, 1)
    chars = [
        CharacterModel(project_id=project.id, name=name, role="supporting", goal="靠岸")
        for name in ("船长", "水手")
    ]
    volume = VolumeModel(project_id=project.id, volume_number=1, title="潮汐", metadata_json={})
    session.add_all([rule, *chars, volume])
    await session.flush()
    relation = RelationshipModel(
        project_id=project.id,
        character_a_id=chars[0].id,
        character_b_id=chars[1].id,
        relationship_type="船员",
        public_face="合作",
        tension_summary="争论航线",
    )
    session.add(relation)
    await session.commit()
    result = await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    source_ids = (rule.id, chars[0].id, relation.id, volume.id)
    for row, field, value, source_type in (
        (rule, "description", "新航规: 日出后出海。", "world_rule"),
        (rule, "exploitation_potential", "只有树使用的新字段", "world_rule"),
        (chars[0], "secret", "只有树使用的船长秘密", "character"),
        (relation, "private_reality", "计划分道扬镳", "relationship"),
        (volume, "obstacle", "风暴封港", "volume"),
    ):
        setattr(row, field, value)
        await session.commit()
        fresh = await ensure_story_bible_read_views(session, settings, project=project)
        assert fresh["refreshed"] and fresh["source_sha256"] != result["source_sha256"]
        model = RetrievalChunkModel if source_type == "relationship" else NarrativeTreeNodeModel
        rendered = " ".join(
            await session.scalars(
                select(
                    model.chunk_text if model is RetrievalChunkModel else model.body_md,
                ).where(model.project_id == project.id, model.source_type == source_type)
            )
        )
        assert value in rendered
        result = fresh
        await session.commit()
    assert source_ids == (rule.id, chars[0].id, relation.id, volume.id)
    project.metadata_json = {**project.metadata_json, "critic_cycle": {"round": 999}}
    chapter.metadata_json = {"story_bible_update_receipts": [{"runtime_only": True}]}
    await session.commit()
    before = await _derived(session)
    repeated = await _no_writes(
        session, lambda: ensure_story_bible_read_views(session, settings, project=project)
    )
    assert repeated["source_sha256"] == result["source_sha256"]
    assert await _derived(session) == before


async def test_native_chinese_and_punctuation_world_path_collisions_are_complete(views):
    session, project, _chapter, _draft, settings = views
    rules = [_rule(project, index) for index in range(6)]
    for rule, code in zip(rules, ("潮汐", "潮汐!", "潮汐?", "___", "---", "rule"), strict=True):
        rule.rule_code = code
    session.add_all(rules)
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    nodes = list(
        await session.scalars(
            select(NarrativeTreeNodeModel).where(
                NarrativeTreeNodeModel.project_id == project.id,
                NarrativeTreeNodeModel.source_type == "world_rule",
            )
        )
    )
    assert len({node.node_path for node in nodes}) == len(rules)
    assert {node.source_ref_id for node in nodes} == {rule.id for rule in rules}
    await session.commit()
    await _no_writes(
        session, lambda: ensure_story_bible_read_views(session, settings, project=project)
    )


async def test_retired_retained_world_and_extra_orphan_chunks_are_removed(views, retired):
    session, project, _chapter, _draft, settings = views
    assert retired[0][0] is session
    retired_rule = retired[1]
    current = _rule(project, 1)
    session.add(current)
    await session.flush()
    for source_id in (retired_rule.id, uuid4()):
        await retrieval._upsert_source_chunks(
            session,
            settings,
            project_id=project.id,
            source_type="world_rule",
            source_id=source_id,
            text="旧航规应退出当前索引。",
        )
    # A marker-free legacy native node remains owned through its retained raw
    # source identity, even though that rule is excluded from the current set.
    session.add(
        NarrativeTreeNodeModel(
            project_id=project.id,
            node_path="/world/rules/retired-legacy",
            parent_path="/world/rules",
            depth=3,
            node_type="world_rule",
            title="Retired rule",
            body_md="Retired legacy view",
            lexical_document="retired legacy view",
            source_type="world_rule",
            source_ref_id=retired_rule.id,
            scope_level="project",
            metadata_json={"rule_code": retired_rule.rule_code},
        )
    )
    await session.commit()
    before = await _snapshot(session, (WorldRuleModel, ChapterModel, ChapterDraftVersionModel))
    result = await ensure_story_bible_read_views(session, settings, project=project)
    assert result["source_counts"]["world_rule"] == 1
    assert await _world_ids(session, project) == ({current.id}, {current.id})
    assert (
        await _snapshot(session, (WorldRuleModel, ChapterModel, ChapterDraftVersionModel)) == before
    )


@pytest.mark.parametrize(
    "corruption", ["rag_text", "rag_metadata", "tree_body", "extra", "missing"]
)
async def test_receipt_checks_actual_derived_rows_not_only_source_ids(views, corruption):
    session, project, _chapter, _draft, settings = views
    rule = _rule(project, 1)
    session.add(rule)
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    chunk = await session.scalar(
        select(RetrievalChunkModel).where(RetrievalChunkModel.project_id == project.id)
    )
    if corruption == "rag_text":
        chunk.chunk_text = "same id stale text"
    elif corruption == "rag_metadata":
        chunk.metadata_json = {"wrong": "same id"}
    elif corruption == "tree_body":
        node = await session.scalar(
            select(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.source_ref_id == rule.id)
        )
        node.body_md = "same id stale body"
    elif corruption == "missing":
        await session.delete(chunk)
    else:
        await retrieval._upsert_source_chunks(
            session,
            settings,
            project_id=project.id,
            source_type="character",
            source_id=uuid4(),
            text="orphan",
        )
    await session.commit()
    result = await ensure_story_bible_read_views(session, settings, project=project)
    assert result["refreshed"]
    assert await _world_ids(session, project) == ({rule.id}, {rule.id})
    actual = await session.scalar(
        select(RetrievalChunkModel).where(RetrievalChunkModel.source_id == rule.id)
    )
    assert (
        rule.description in actual.chunk_text
        and actual.metadata_json["rule_code"] == rule.rule_code
    )
    await session.commit()
    await _no_writes(
        session, lambda: ensure_story_bible_read_views(session, settings, project=project)
    )


async def test_staged_tree_failure_rolls_back_both_views_and_old_receipt(views, monkeypatch):
    session, project, _chapter, _draft, settings = views
    session.add(_rule(project, 1))
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    session.add(_rule(project, 2))
    await session.commit()
    sources = await _snapshot(session, (WorldRuleModel, ProjectModel))
    before = await _derived(session)
    native = narrative_tree.rebuild_narrative_tree

    async def fail_after_native(*args, **kwargs):
        await native(*args, **kwargs)
        raise RuntimeError("staged tree failure")

    with monkeypatch.context() as patch:
        patch.setattr(narrative_tree, "rebuild_narrative_tree", fail_after_native)
        with pytest.raises(RuntimeError, match="staged tree failure"):
            await ensure_story_bible_read_views(session, settings, project=project)
    assert await _derived(session) == before
    assert await _snapshot(session, (WorldRuleModel, ProjectModel)) == sources
    assert (await ensure_story_bible_read_views(session, settings, project=project))["refreshed"]
    # Releasing the helper savepoint never takes ownership of the outer commit.
    await session.rollback()
    assert await _derived(session) == before


async def test_chunk_configuration_change_invalidates_receipt(views):
    session, project, _chapter, _draft, settings = views
    session.add(_rule(project, 1))
    await session.commit()
    first = await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    settings = deepcopy(settings)
    settings.retrieval.chunk_size = 24
    settings.retrieval.chunk_overlap = 4
    fresh = await ensure_story_bible_read_views(session, settings, project=project)
    assert fresh["refreshed"] and fresh["source_sha256"] == first["source_sha256"]
    assert fresh["projection_sha256"] != first["projection_sha256"]
    assert fresh["retrieval_chunk_count"] > first["retrieval_chunk_count"]


async def test_long_lived_reader_reloads_same_id_source_and_actual_derived_rows(views):
    session, project, _chapter, _draft, settings = views
    rule = _rule(project, 1)
    session.add(rule)
    await session.commit()
    first = await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    await session.execute(
        text("UPDATE world_rules SET description = :body WHERE id = :id"),
        {"body": "直接写入的新航规", "id": rule.id.hex},
    )
    await session.commit()
    assert rule.description != "直接写入的新航规"  # Deliberately retained ORM preimage.
    fresh = await ensure_story_bible_read_views(session, settings, project=project)
    assert fresh["refreshed"] and fresh["source_sha256"] != first["source_sha256"]
    assert rule.description == "直接写入的新航规"
    await session.commit()
    chunk = await session.scalar(
        select(RetrievalChunkModel).where(
            RetrievalChunkModel.source_id == rule.id,
        )
    )
    await session.execute(
        text("UPDATE retrieval_chunks SET chunk_text = :body WHERE id = :id"),
        {"body": "stale external row", "id": chunk.id.hex},
    )
    await session.commit()
    assert chunk.chunk_text != "stale external row"
    assert (await ensure_story_bible_read_views(session, settings, project=project))["refreshed"]
    await session.commit()
    await _no_writes(
        session, lambda: ensure_story_bible_read_views(session, settings, project=project)
    )


def _custom_node(project, path, source_type):
    return NarrativeTreeNodeModel(
        project_id=project.id,
        node_path=path,
        parent_path=narrative_tree._parent_path(path),
        depth=narrative_tree._depth(path),
        node_type="world_rule",
        title="User-owned node",
        summary="Retain exactly",
        body_md="Manual body and workflow extension evidence.",
        lexical_document="user owned evidence",
        source_type=source_type,
        source_ref_id=uuid4(),
        scope_level="project",
        metadata_json={"user": {"preserved": [1, 2, 3]}},
    )


@pytest.mark.parametrize("mode", ["bible", "full"])
async def test_same_project_custom_nodes_survive_native_refresh_even_under_native_prefix(
    views, mode
):
    session, project, chapter, _draft, settings = views
    rule = _rule(project, 1)
    session.add(rule)
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    custom = [
        _custom_node(project, "/manual/notes", "manual"),
        _custom_node(project, "/world/rules/user-note", "custom"),
        _custom_node(project, "/characters/workflow-note", "extension"),
        _custom_node(project, "/volumes/review-note", "workflow_extension"),
        _custom_node(project, "/other/consumer", "unknown_owner"),
    ]
    # Even a copied native marker can never grant authority over a custom type.
    custom[1].metadata_json = {
        **custom[1].metadata_json,
        narrative_tree._NATIVE_TREE_MARKER: narrative_tree._native_tree_stamp(custom[1]),
    }
    session.add_all(custom)
    unrelated_native = await session.scalar(
        select(NarrativeTreeNodeModel).where(
            NarrativeTreeNodeModel.project_id == project.id,
            NarrativeTreeNodeModel.source_type == "chapter",
            NarrativeTreeNodeModel.source_ref_id == chapter.id,
        )
    )
    unrelated_native.body_md += "\nRetained chapter view extension."
    unrelated_native.metadata_json = {**unrelated_native.metadata_json, "workflow": ["keep"]}
    rule.description = "更新后的潮汐航规"
    await session.commit()
    protected_ids = {row.id.hex for row in custom}
    if mode == "bible":
        protected_ids.add(unrelated_native.id.hex)
    before = (await _derived(session))["narrative_tree_nodes"]
    id_column = list(NarrativeTreeNodeModel.__table__.c.keys()).index("id")
    protected = [row for row in before if row[id_column] in protected_ids]
    assert len(protected) == len(protected_ids)
    if mode == "bible":
        await ensure_story_bible_read_views(session, settings, project=project)
    else:
        await narrative_tree.rebuild_narrative_tree(session, project=project)
    after = (await _derived(session))["narrative_tree_nodes"]
    assert [row for row in after if row[id_column] in protected_ids] == protected
    native_rule = await session.scalar(
        select(NarrativeTreeNodeModel).where(
            NarrativeTreeNodeModel.project_id == project.id,
            NarrativeTreeNodeModel.source_ref_id == rule.id,
        )
    )
    assert rule.description in native_rule.body_md
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    # Editing an extension must not dirty the Bible-only freshness certificate.
    custom[0].body_md = "Only this manual note changed."
    await session.commit()
    await _no_writes(
        session, lambda: ensure_story_bible_read_views(session, settings, project=project)
    )


@pytest.mark.parametrize("path", ["/book", "/world/rules/航规1"])
@pytest.mark.parametrize("mode", ["bible", "full"])
async def test_unowned_canonical_path_conflict_preserves_whole_views_and_receipt(views, path, mode):
    session, project, _chapter, _draft, settings = views
    rule = _rule(project, 1)
    session.add(rule)
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    occupant = await session.scalar(
        select(NarrativeTreeNodeModel).where(
            NarrativeTreeNodeModel.project_id == project.id,
            NarrativeTreeNodeModel.node_path == path,
        )
    )
    occupant.source_type = "custom"
    occupant.body_md = "Canonical path is explicitly user-owned."
    occupant.metadata_json = {**occupant.metadata_json, "user_owned": True}
    rule.description = "原生视图需要刷新但不可覆盖自定义节点"
    await session.commit()
    before = await _derived(session)
    sources = await _snapshot(session, (WorldRuleModel, ProjectModel))
    with pytest.raises(ValueError, match="narrative_tree_unowned_path_conflict"):
        if mode == "bible":
            await ensure_story_bible_read_views(session, settings, project=project)
        else:
            async with session.begin_nested():
                await narrative_tree.rebuild_narrative_tree(session, project=project)
    assert await _derived(session) == before
    assert await _snapshot(session, (WorldRuleModel, ProjectModel)) == sources


async def test_source_changed_during_native_build_never_receives_closed_fingerprint(
    views, monkeypatch
):
    session, project, _chapter, _draft, settings = views
    rule = _rule(project, 1)
    session.add(rule)
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    session.add(_rule(project, 2))
    await session.commit()
    before = await _derived(session)
    sources = await _snapshot(session, (WorldRuleModel, ProjectModel))
    native = narrative_tree.rebuild_narrative_tree

    async def change_after_build(*args, **kwargs):
        result = await native(*args, **kwargs)
        await session.execute(
            text("UPDATE world_rules SET description = :body WHERE id = :id"),
            {"body": "Changed during projection", "id": rule.id.hex},
        )
        return result

    with monkeypatch.context() as patch:
        patch.setattr(narrative_tree, "rebuild_narrative_tree", change_after_build)
        with pytest.raises(ValueError, match="story_bible_read_views_source_changed"):
            await ensure_story_bible_read_views(session, settings, project=project)
    assert await _derived(session) == before
    assert await _snapshot(session, (WorldRuleModel, ProjectModel)) == sources


async def test_native_entity_refresh_preserves_unrelated_metadata_annotations(views):
    session, project, *_rest, settings = views
    rule = WorldRuleModel(project_id=project.id, rule_code="annotated-rule",
        name="Harbour access", description="Original access condition.", metadata_json={})
    session.add(rule)
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    node = await session.scalar(select(NarrativeTreeNodeModel).where(
        NarrativeTreeNodeModel.project_id == project.id,
        NarrativeTreeNodeModel.source_type == "world_rule",
        NarrativeTreeNodeModel.source_ref_id == rule.id))
    identity = node.id
    annotation = {"reader_note": "Keep this independent annotation", "audit": [1, 2]}
    node.metadata_json = {**node.metadata_json, "manual_annotation": annotation}
    rule.description = "Updated access condition."
    await session.commit()
    result = await ensure_story_bible_read_views(session, settings, project=project)
    assert result["refreshed"]
    await session.commit()
    await session.refresh(node)
    assert node.id == identity and node.summary == rule.description
    assert node.metadata_json["manual_annotation"] == annotation
    assert not (await ensure_story_bible_read_views(session, settings, project=project))["refreshed"]
