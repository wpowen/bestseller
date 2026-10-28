"""F0-17: native source/index producers, score ties and cache replacement.

Real SQLite ordinary sources only; no model, adoption, grade or raw paid input.
The three distinct relationship sources render identical text, so the real
scorer ties exactly. This exercises selection before top_k without score mocks.
"""

import pytest
from sqlalchemy import select, text

from bestseller.infra.db import models as m
from bestseller.services import retrieval, story_bible_read_views
from bestseller.settings import load_settings

from .f0_ordering_support import uid
from .test_context_native_ordering import source_db as source_db

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def seed_equal_sources(case):
    people = [m.CharacterModel(
        id=uid(4000 + i), project_id=case.project.id, name=f"Person {i}",
        role="support", metadata_json={},
    ) for i in range(1, 5)]
    case.session.add_all(people)
    await case.session.flush()
    # Physical insertion order intentionally disagrees with logical source ID.
    relations = [m.RelationshipModel(
        id=uid(5000 + i), project_id=case.project.id,
        character_a_id=people[0].id, character_b_id=people[i].id,
        relationship_type="ally", public_face="shared purpose",
        private_reality="shared trust", tension_summary="shared pressure",
        strength=0.8, metadata_json={},
    ) for i in (3, 2, 1)]
    case.session.add_all(relations)
    await case.session.commit()
    settings = load_settings(env={})
    await story_bible_read_views.ensure_story_bible_read_views(
        case.session, settings, project=case.project,
    )
    await case.session.commit()
    chunks = list(await case.session.scalars(select(m.RetrievalChunkModel).where(
        m.RetrievalChunkModel.project_id == case.project.id,
        m.RetrievalChunkModel.source_type == "relationship",
    )))
    assert len(chunks) == 3
    assert len({chunk.chunk_text for chunk in chunks}) == 1
    return settings, chunks[0].chunk_text


def identities(result):
    return [(chunk.source_type, chunk.source_id, chunk.chunk_index) for chunk in result.chunks]


@pytest.mark.parametrize("fallback", [False, True])
async def test_f0_17_same_score_cap_plus_one_uses_source_identity(source_db, fallback):
    case = source_db
    settings, query = await seed_equal_sources(case)
    settings.retrieval.min_score = 1.1 if fallback else 0.9
    expected = [("relationship", uid(i), 0) for i in (5001, 5002)]
    results = []
    for reverse in ("OFF", "ON"):
        await case.session.execute(text(f"PRAGMA reverse_unordered_selects={reverse}"))
        result = await retrieval.search_retrieval_for_project(
            case.session, settings, case.project, query, top_k=2,
        )
        assert [chunk.score for chunk in result.chunks] == [1.0, 1.0]
        results.append(identities(result))
    assert results == [expected, expected]


@pytest.mark.parametrize("fallback", [False, True])
async def test_f0_17_native_cache_rebuild_changes_row_ids_not_selected_sources(source_db, fallback):
    case = source_db
    settings, query = await seed_equal_sources(case)
    settings.retrieval.min_score = 1.1 if fallback else 0.9

    async def chunks_by_identity():
        rows = await case.session.scalars(select(m.RetrievalChunkModel).where(
            m.RetrievalChunkModel.project_id == case.project.id,
            m.RetrievalChunkModel.source_type == "relationship",
        ))
        return {(row.source_type, row.source_id, row.chunk_index): row.id for row in rows}

    first = await retrieval.search_retrieval_for_project(
        case.session, settings, case.project, query, top_k=2,
    )
    old_cache_ids = await chunks_by_identity()
    await case.session.execute(text("PRAGMA reverse_unordered_selects=ON"))
    # Actual native producer deletes/recreates cache rows; no fabricated receipt.
    await retrieval.refresh_story_bible_retrieval_index(case.session, settings, case.project.id)
    await case.session.commit()
    second = await retrieval.search_retrieval_for_project(
        case.session, settings, case.project, query, top_k=2,
    )
    new_cache_ids = await chunks_by_identity()
    assert old_cache_ids.keys() == new_cache_ids.keys()
    assert all(old_cache_ids[key] != new_cache_ids[key] for key in old_cache_ids)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert identities(second) == [("relationship", uid(i), 0) for i in (5001, 5002)]


@pytest.mark.parametrize("fallback", [False, True])
async def test_f0_17_same_source_tuple_survives_opposite_cache_pk_order(monkeypatch, fallback):
    """IO component control: never rank by a replaceable cache-row UUID.

    The native rebuild test above proves actual producer behavior; this small
    control makes cache PK reversal deterministic and covers tuple dimensions.
    """
    from types import SimpleNamespace

    from .f0_ordering_support import RowsSession

    project = SimpleNamespace(id=uid(6000))
    settings = load_settings(env={})
    settings.retrieval.min_score = 1.1 if fallback else 0.9
    query = "shared trust pressure"
    keys = [("relationship", uid(11), 10), ("character", uid(99), 0),
            ("relationship", uid(11), 2), ("relationship", uid(10), 0)]

    async def existing_io_index(*args, **kwargs):
        return 0  # This component supplies persisted-cache-shaped IO rows only.

    monkeypatch.setattr(retrieval, "ensure_project_retrieval_index", existing_io_index)
    outputs = []
    for rebuilt in (False, True):
        rows = [m.RetrievalChunkModel(
            id=uid((8000 - i) if rebuilt else (7000 + i)), project_id=project.id,
            source_type=kind, source_id=source, chunk_index=index,
            chunk_text=query, lexical_document=query,
            embedding=retrieval.build_hashed_embedding(query, settings.retrieval.embedding_dimensions),
            embedding_dim=settings.retrieval.embedding_dimensions,
            embedding_model=settings.retrieval.embedding_model, metadata_json={},
        ) for i, (kind, source, index) in enumerate(keys)]
        result = await retrieval.search_retrieval_for_project(
            RowsSession({m.RetrievalChunkModel: list(reversed(rows)) if rebuilt else rows}),
            settings, project, query, top_k=3,
        )
        assert [chunk.score for chunk in result.chunks] == [1.0, 1.0, 1.0]
        outputs.append(identities(result))
    assert outputs == [[("character", uid(99), 0), ("relationship", uid(10), 0),
                        ("relationship", uid(11), 2)]] * 2
