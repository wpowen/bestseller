from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.retrieval import RetrievalSearchResult
from bestseller.infra.db.models import (
    CharacterModel,
    ProjectModel,
    RetrievalChunkModel,
    WorldRuleModel,
)
from bestseller.services import retrieval as retrieval_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, *, scalars_results: list[list[object]] | None = None) -> None:
        self.scalars_results = list(scalars_results or [])
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is not None and "id" in table.c and getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalars(self, stmt: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    async def execute(self, stmt: object) -> None:
        self.executed.append(stmt)


def build_settings():
    return load_settings(env={})


def build_project() -> ProjectModel:
    project = ProjectModel(
        slug="my-story",
        title="长夜巡航",
        genre="science-fantasy",
        target_word_count=80000,
        target_chapters=12,
        metadata_json={},
    )
    project.id = uuid4()
    return project


def test_embedding_and_chunking_are_deterministic() -> None:
    chunks = retrieval_services.build_text_chunks("abcdef" * 300, 100, 20)
    embedding = retrieval_services.build_hashed_embedding("长夜巡航", 16)

    assert len(chunks) > 1
    assert len(embedding) == 16
    assert round(sum(value * value for value in embedding), 4) == 1.0


@pytest.mark.asyncio
async def test_refresh_story_bible_retrieval_index_creates_chunks() -> None:
    project = build_project()
    world_rule = WorldRuleModel(
        project_id=project.id,
        rule_code="R001",
        name="记录优先",
        description="官方记录高于个人证词。",
        metadata_json={},
    )
    world_rule.id = uuid4()
    character = CharacterModel(
        project_id=project.id,
        name="沈砚",
        role="protagonist",
        goal="找证据",
        knowledge_state_json={},
        metadata_json={},
    )
    character.id = uuid4()

    session = FakeSession(scalars_results=[[world_rule], [character], [], []])
    chunk_count = await retrieval_services.refresh_story_bible_retrieval_index(
        session,
        build_settings(),
        project.id,
    )

    assert chunk_count >= 2
    assert any(isinstance(item, RetrievalChunkModel) for item in session.added)


@pytest.mark.asyncio
async def test_search_project_retrieval_scores_matching_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = build_project()
    matching = RetrievalChunkModel(
        project_id=project.id,
        source_type="character",
        source_id=uuid4(),
        chunk_index=0,
        chunk_text="角色 沈砚 目标 找证据 并破解帝国阴谋",
        embedding_model="mock",
        embedding_dim=16,
        embedding=retrieval_services.build_hashed_embedding("沈砚 找证据 帝国 阴谋", 16),
        metadata_json={"kind": "character"},
    )
    other = RetrievalChunkModel(
        project_id=project.id,
        source_type="world_rule",
        source_id=uuid4(),
        chunk_index=0,
        chunk_text="无关文本",
        embedding_model="mock",
        embedding_dim=16,
        embedding=retrieval_services.build_hashed_embedding("无关文本", 16),
        metadata_json={"kind": "world_rule"},
    )

    async def fake_get_project_by_slug(session, slug: str):
        return project

    async def fake_ensure_project_retrieval_index(session, settings, project_id):
        return 0

    monkeypatch.setattr(retrieval_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        retrieval_services,
        "ensure_project_retrieval_index",
        fake_ensure_project_retrieval_index,
    )

    session = FakeSession(scalars_results=[[matching, other]])
    settings = build_settings()
    settings.retrieval.embedding_dimensions = 16

    result = await retrieval_services.search_project_retrieval(
        session,
        settings,
        "my-story",
        "沈砚 需要 找证据",
        top_k=1,
    )

    assert isinstance(result, RetrievalSearchResult)
    assert len(result.chunks) == 1
    assert result.chunks[0].source_type == "character"
