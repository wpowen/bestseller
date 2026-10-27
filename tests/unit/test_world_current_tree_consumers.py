"""Current world-rule consumers against real, isolated SQLite lineage.

The SQLite lock functions only let the existing PostgreSQL rebuild statement
execute locally; these tests make no PostgreSQL/concurrency or provider claim.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.schema import CreateTable
from test_inspection_retired_world_rules import (
    _apply,
    _overview_tables,
    _row_state,
    _withdraw,
)
from test_inspection_retired_world_rules import (
    db as db,
)
from test_inspection_retired_world_rules import (
    knowledge_db as knowledge_db,
)
from test_inspection_retired_world_rules import (
    offline as offline,
)
from test_inspection_retired_world_rules import (
    retired as retired,
)
from test_inspection_retired_world_rules import (
    seeded as seeded,
)
from test_knowledge_downstream_source_dependencies import _replace_source

from bestseller.infra.db.models import (
    ChapterContractModel,
    NarrativeTreeNodeModel,
    PayoffModel,
    ProjectModel,
    SceneCardModel,
    SceneContractModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services import narrative_tree as tree
from bestseller.services import query_broker
from bestseller.services import story_bible_projection as projection
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def tree_tables(db):
    session = db[0]
    assert session.bind.dialect.name == "sqlite"
    await _overview_tables(session)
    connection = await session.connection()
    for model in (
        NarrativeTreeNodeModel,
        VolumeModel,
        SceneCardModel,
        PayoffModel,
        ChapterContractModel,
        SceneContractModel,
    ):
        ddl = str(
            CreateTable(model.__table__, if_not_exists=True).compile(
                dialect=connection.dialect,
            )
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    raw = await connection.get_raw_connection()
    await raw.driver_connection.create_function("hashtextextended", 2, lambda *_: 0)
    await raw.driver_connection.create_function("pg_advisory_xact_lock", 1, lambda *_: 0)
    # These tests construct retained legacy tree snapshots explicitly below.
    # Ordinary Bible writes now populate live views, so clear only this
    # disposable derived fixture tree before installing that legacy snapshot.
    await session.execute(
        delete(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.project_id == db[1].id)
    )
    await session.commit()


def _node(
    project_id,
    path,
    *,
    source_type="world_rule",
    source_ref_id=None,
    node_type="world_rule",
    chapter=None,
):
    return NarrativeTreeNodeModel(
        project_id=project_id,
        node_path=path,
        parent_path="/world/rules",
        depth=3,
        node_type=node_type,
        title="Brass retained fact",
        summary="Brass retained fact",
        body_md="Brass retained fact opens the door.",
        lexical_document="brass retained fact opens door",
        source_type=source_type,
        source_ref_id=source_ref_id,
        scope_level="chapter" if chapter else "project",
        scope_chapter_number=chapter,
        metadata_json={},
    )


async def _tool(session, project, arguments):
    registry = query_broker.build_story_query_registry(
        session,
        load_settings(),
        project=project,
        chapter_number=1,
        scene_number=1,
    )
    return await registry.get("query_world_rule").handler(arguments)


async def _read(surface, session, project, paths):
    if surface == "overview":
        return (await tree.build_narrative_tree_overview(session, project.slug)).nodes
    if surface == "path":
        nodes = [
            await tree.get_narrative_tree_node_by_path(session, project.slug, path)
            for path in paths
        ]
        return [node for node in nodes if node is not None]
    if surface == "resolve":
        return await tree.resolve_narrative_tree_paths_for_project(session, project, paths)
    return (
        await tree.search_narrative_tree_for_project(
            session,
            project,
            "brass",
            preferred_paths=paths,
            top_k=100,
        )
    ).hits


SURFACES = ["overview", "path", "resolve", "search"]


@pytest.mark.parametrize("surface", SURFACES)
async def test_retained_tree_filters_world_sources_only_and_is_read_only(
    retired,
    tree_tables,
    monkeypatch,
    surface,
):
    book, retired_rule = retired
    session, project, *_ = book
    authored = WorldRuleModel(
        project_id=project.id,
        rule_code="authored",
        name="Brass authored",
        description="Brass authored rule.",
        metadata_json={},
    )
    other_project = ProjectModel(
        slug=f"foreign-test-{uuid4().hex}",
        title="foreign",
        genre="test",
        target_word_count=10000,
        target_chapters=4,
    )
    session.add_all([authored, other_project])
    await session.flush()
    foreign = WorldRuleModel(
        project_id=other_project.id,
        rule_code="foreign",
        name="Brass foreign",
        description="Other project's rule.",
        metadata_json={},
    )
    session.add(foreign)
    await session.flush()
    nodes = [
        _node(project.id, "/world/rules/retired", source_ref_id=retired_rule.id),
        _node(project.id, "/world/rules/current", source_ref_id=authored.id),
        _node(project.id, "/world/rules/orphan", source_ref_id=uuid4()),
        _node(project.id, "/world/rules/null"),
        _node(project.id, "/world/rules/foreign", source_ref_id=foreign.id),
        _node(project.id, "/world/rules", source_type="container", node_type="world_rules_root"),
        _node(
            project.id,
            "/characters/brass",
            source_type="character",
            node_type="character",
            source_ref_id=retired_rule.id,
        ),
    ]
    session.add_all(nodes)
    await session.commit()
    watched = [*book[1:], retired_rule, authored, foreign, *nodes]
    before = [_row_state(row) for row in watched]
    await session.connection()  # Open the fixture savepoint before tracing read SQL.
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Current retained-tree reads must not write or rebuild")

    monkeypatch.setattr(session, "flush", forbidden)
    monkeypatch.setattr(session, "commit", forbidden)
    monkeypatch.setattr(tree, "rebuild_narrative_tree", forbidden)
    event.listen(session.bind.engine.sync_engine, "before_cursor_execute", record)
    try:
        result = await _read(surface, session, project, [node.node_path for node in nodes])
    finally:
        event.remove(session.bind.engine.sync_engine, "before_cursor_execute", record)
    assert {node.node_path for node in result} == {
        "/world/rules/current",
        "/world/rules",
        "/characters/brass",
    }
    assert statements and all(stmt.lstrip().upper().startswith("SELECT") for stmt in statements)
    assert [_row_state(row) for row in watched] == before
    assert await session.get(NarrativeTreeNodeModel, nodes[0].id) is nodes[0]


async def test_rebuild_uses_current_world_rules_and_preserves_authored_rules(retired, tree_tables):
    book, retired_rule = retired
    session, project, *_ = book
    authored = WorldRuleModel(
        project_id=project.id,
        rule_code="authored",
        name="Authored",
        description="Independent authored rule.",
        metadata_json={},
    )
    session.add(authored)
    await session.commit()
    counts = await tree.rebuild_narrative_tree(session, project=project)
    await session.commit()
    nodes = list(
        await session.scalars(
            select(NarrativeTreeNodeModel).where(
                NarrativeTreeNodeModel.project_id == project.id,
                NarrativeTreeNodeModel.source_type == "world_rule",
            )
        )
    )
    assert counts["node_type_counts"]["world_rule"] == 1
    assert [node.source_ref_id for node in nodes] == [authored.id]
    assert await session.get(WorldRuleModel, retired_rule.id) is retired_rule


async def test_withdrawal_hides_existing_tree_then_same_identity_reactivates(
    seeded,
    tree_tables,
    monkeypatch,
):
    book, rule = seeded
    session, project, *_ = book
    await tree.rebuild_narrative_tree(session, project=project)
    await session.commit()
    path = f"/world/rules/{tree.tree_segment(rule.rule_code, 'rule')}"
    assert await tree.get_narrative_tree_node_by_path(session, project.slug, path)
    await _withdraw(book)
    cached = await session.scalar(
        select(NarrativeTreeNodeModel).where(
            NarrativeTreeNodeModel.project_id == project.id,
            NarrativeTreeNodeModel.node_path == path,
        )
    )
    assert cached and cached.source_ref_id == rule.id
    assert "brass" in cached.body_md.lower()
    cached_before = _row_state(cached)
    for surface in SURFACES:
        assert path not in {
            node.node_path
            for node in await _read(
                surface,
                session,
                project,
                [path],
            )
        }
    assert (await _tool(session, project, {}))["rules"] == []
    assert _row_state(cached) == cached_before  # Pure retirement reads preserve retained cache.
    replacement = await _replace_source(book, "A newly adopted key constraint.")
    await _apply(replacement, monkeypatch, "New brass-key constraint.")
    # Ordinary accepted Bible writes now refresh owned projections. Reading
    # remains pure; compare against the state after that legitimate write.
    updated = await session.scalar(
        select(NarrativeTreeNodeModel).where(
            NarrativeTreeNodeModel.project_id == project.id,
            NarrativeTreeNodeModel.node_path == path,
        )
    )
    assert updated is not None and updated.source_ref_id == rule.id
    assert updated.summary == rule.description
    updated_before_read = _row_state(updated)
    assert await tree.get_narrative_tree_node_by_path(session, project.slug, path)
    result = await _tool(session, project, {"rule_codes": [rule.rule_code]})
    assert [item["description"] for item in result["rules"]] == [rule.description]
    assert _row_state(updated) == updated_before_read  # Reading does not repair cached text.


@pytest.mark.parametrize("arguments", [{}, {"query": "brass"}, {"rule_codes": ["retired"]}])
async def test_direct_world_tool_filters_retirement_before_query_matching(retired, arguments):
    book, rule = retired
    session, project, *_ = book
    # Do not mutate the retained identity merely to exercise exact-code matching.
    if arguments.get("rule_codes"):
        arguments = {"rule_codes": [rule.rule_code]}
    authored = WorldRuleModel(
        project_id=project.id,
        rule_code="authored",
        name="Brass authored",
        description="Brass current rule.",
        metadata_json={},
    )
    session.add(authored)
    await session.commit()
    before = [_row_state(item) for item in (rule, authored)]
    result = await _tool(session, project, arguments)
    expected = [] if arguments.get("rule_codes") else [authored.rule_code]
    assert [item["rule_code"] for item in result["rules"]] == expected
    assert [_row_state(item) for item in (rule, authored)] == before


@pytest.mark.parametrize("surface", [*SURFACES, "tool", "rebuild"])
async def test_invalid_retirement_proof_fails_closed(retired, tree_tables, surface):
    book, rule = retired
    session, project, *_ = book
    metadata = deepcopy(rule.metadata_json)
    metadata[projection.BASE]["project_id"] = str(uuid4())
    rule.metadata_json = metadata
    node = _node(project.id, "/world/rules/invalid", source_ref_id=rule.id)
    session.add(node)
    await session.commit()
    await session.refresh(rule)  # Reload the server-updated timestamp explicitly.
    before = _row_state(rule)
    node_before = _row_state(node)
    with pytest.raises(projection.BibleProjectionAttributionError):
        if surface == "tool":
            await _tool(session, project, {})
        elif surface == "rebuild":
            await tree.rebuild_narrative_tree(session, project=project)
        else:
            await _read(surface, session, project, [node.node_path])
    assert _row_state(rule) == before
    assert (
        await session.scalar(
            select(NarrativeTreeNodeModel).where(
                NarrativeTreeNodeModel.id == node.id,
            )
        )
        is node
    )
    assert _row_state(node) == node_before


async def test_position_visibility_and_requested_path_order_remain_intact(db, tree_tables):
    session, project, *_ = db
    nodes = [
        _node(
            project.id,
            f"/chapters/{number}",
            node_type="chapter",
            source_type="chapter",
            source_ref_id=uuid4(),
            chapter=number,
        )
        for number in (1, 2, 3)
    ]
    session.add_all(nodes)
    await session.commit()
    paths = [nodes[2].node_path, nodes[1].node_path, nodes[0].node_path]
    resolved = await tree.resolve_narrative_tree_paths_for_project(
        session,
        project,
        paths,
        current_chapter_number=2,
    )
    assert [node.node_path for node in resolved] == paths[1:]
    hits = await tree.search_narrative_tree_for_project(
        session,
        project,
        "brass",
        preferred_paths=paths,
        current_chapter_number=2,
    )
    assert {hit.node_path for hit in hits.hits} == set(paths[1:])
