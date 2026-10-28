"""F0-18: real native tree producer, natural score ties and stable path identity.

SQLite/dialect IO evidence only, not PostgreSQL advisory-lock/E21 acceptance.
Sources are ordinary characters; no grade, adoption or paid-result fixture is
constructed. Main executes RED/GREEN; the authoring worker does not run tests.
"""

import pytest
from sqlalchemy import delete, select, text

from bestseller.infra.db import models as m
from bestseller.services import narrative_tree as tree

from .f0_ordering_support import RowsSession, ScalarRows, uid
from .test_context_native_ordering import source_db as source_db  # noqa: F401 -- shared SQLite IO fixture

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
QUERY = "shared witness oath"
PATHS = ["/characters/ari-witness", "/characters/mira-witness", "/characters/zed-witness"]


async def seed_native_tree(case):
    # Arrival/source-ID order is deliberately opposite to node_path order.
    people = [m.CharacterModel(
        id=uid(7101 + i), project_id=case.project.id, name=name,
        role="support", goal=QUERY, metadata_json={},
    ) for i, name in enumerate(("Zed witness", "Mira witness", "Ari witness"))]
    case.session.add_all(people)
    await case.session.commit()
    await tree.rebuild_narrative_tree(case.session, project=case.project)
    await case.session.commit()
    nodes = list(await case.session.scalars(select(m.NarrativeTreeNodeModel).where(
        m.NarrativeTreeNodeModel.project_id == case.project.id,
        m.NarrativeTreeNodeModel.source_type == "character",
    )))
    assert sorted(node.node_path for node in nodes) == PATHS
    assert {node.source_ref_id for node in nodes} == {person.id for person in people}
    assert all(QUERY in node.body_md for node in nodes)
    return nodes


def paths(result):
    return [hit.node_path for hit in result.hits]


async def test_f0_18_native_same_score_cap_plus_one_is_stable_in_both_sql_scans(source_db):
    case = source_db
    await seed_native_tree(case)
    outputs = []
    for mode in ("OFF", "ON"):
        await case.session.execute(text(f"PRAGMA reverse_unordered_selects={mode}"))
        result = await tree.search_narrative_tree_for_project(
            case.session, case.project, QUERY, top_k=2,
        )
        assert [hit.score for hit in result.hits] == [0.7825, 0.7825]
        outputs.append(paths(result))
    assert outputs == [PATHS[:2], PATHS[:2]]


async def test_f0_18_sql_arrival_itself_has_the_same_unique_final_key(source_db, monkeypatch):
    """Observe actual query values before Python ranking; do not inspect SQL text."""
    case = source_db
    await seed_native_tree(case)
    original = case.session.scalars
    arrivals = []

    async def observe(statement, *args, **kwargs):
        rows = await original(statement, *args, **kwargs)
        descriptions = statement.column_descriptions
        model = descriptions[0].get("entity") if descriptions else None
        if model is m.NarrativeTreeNodeModel:
            values = list(rows)
            arrivals.append([node.node_path for node in values if node.source_type == "character"])
            return ScalarRows(values)
        return rows

    with monkeypatch.context() as guard:
        guard.setattr(case.session, "scalars", observe)
        for mode in ("OFF", "ON"):
            await case.session.execute(text(f"PRAGMA reverse_unordered_selects={mode}"))
            await tree.search_narrative_tree_for_project(case.session, case.project, QUERY, top_k=2)
    assert arrivals == [PATHS, PATHS]


class ReversedDriverArrival(RowsSession):
    """IO component only: reverse real native cache rows after SQL ordering.

    The real-SQL case above checks SQL separately. This adversarial arrival
    checks that Python's top-k tie decision independently uses the same key.
    Source fields and the scoring implementation are never mocked or changed.
    """

    async def scalars(self, statement):
        rows = await super().scalars(statement)
        descriptions = statement.column_descriptions
        model = descriptions[0].get("entity") if descriptions else None
        if model is m.NarrativeTreeNodeModel:
            return ScalarRows(reversed(rows))
        return rows


async def test_f0_18_python_ties_use_path_even_with_reversed_driver_arrival(source_db):
    case = source_db
    nodes = await seed_native_tree(case)
    results = []
    for arrival in (nodes, list(reversed(nodes))):
        result = await tree.search_narrative_tree_for_project(
            ReversedDriverArrival({m.NarrativeTreeNodeModel: arrival}),
            case.project, QUERY, top_k=2,
        )
        assert [hit.score for hit in result.hits] == [0.7825, 0.7825]
        results.append(paths(result))
    assert results == [PATHS[:2], PATHS[:2]]


@pytest.mark.parametrize("replace_cache", [False, True])
async def test_f0_18_rebuild_preserves_selected_native_paths_and_writer_visible_bytes(source_db, replace_cache):
    case = source_db
    await seed_native_tree(case)

    async def cache_ids():
        rows = await case.session.scalars(select(m.NarrativeTreeNodeModel).where(
            m.NarrativeTreeNodeModel.project_id == case.project.id))
        return {row.node_path: row.id for row in rows}

    await case.session.execute(text("PRAGMA reverse_unordered_selects=OFF"))
    first = await tree.search_narrative_tree_for_project(case.session, case.project, QUERY, top_k=2)
    old_ids = await cache_ids()
    await case.session.execute(text("PRAGMA reverse_unordered_selects=ON"))
    if replace_cache:
        # Derived-cache replacement only. All ordinary source identities stay.
        await case.session.execute(delete(m.NarrativeTreeNodeModel).where(
            m.NarrativeTreeNodeModel.project_id == case.project.id))
        await case.session.commit()
    await tree.rebuild_narrative_tree(case.session, project=case.project)
    await case.session.commit()
    second = await tree.search_narrative_tree_for_project(case.session, case.project, QUERY, top_k=2)
    new_ids = await cache_ids()
    assert old_ids.keys() == new_ids.keys()
    if replace_cache:
        assert all(old_ids[path] != new_ids[path] for path in old_ids)
    else:
        assert old_ids == new_ids
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert paths(second) == PATHS[:2]


async def test_f0_18_existing_affinity_priority_and_score_threshold_are_unchanged(source_db):
    case = source_db
    await seed_native_tree(case)
    preferred = PATHS[-1]
    for mode in ("OFF", "ON"):
        await case.session.execute(text(f"PRAGMA reverse_unordered_selects={mode}"))
        result = await tree.search_narrative_tree_for_project(
            case.session, case.project, QUERY, preferred_paths=[preferred], top_k=2,
        )
        assert paths(result) == [preferred, PATHS[0]]
        assert [hit.score for hit in result.hits] == [1.0, 0.7825]
        absent = await tree.search_narrative_tree_for_project(
            case.session, case.project, "zxqvnevermatchingtoken", top_k=2,
        )
        assert absent.hits == []
