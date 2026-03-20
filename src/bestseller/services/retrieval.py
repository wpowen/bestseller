from __future__ import annotations

import math
import re
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.retrieval import RetrievedChunk, RetrievalSearchResult
from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterModel,
    ChapterDraftVersionModel,
    CharacterModel,
    ProjectModel,
    RelationshipModel,
    RetrievalChunkModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services.projects import get_project_by_slug
from bestseller.settings import AppSettings


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def tokenize_text(text: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in TOKEN_PATTERN.findall(text or ""):
        token = raw_token.lower()
        tokens.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) <= 4:
                tokens.extend(token[index : index + 2] for index in range(max(0, len(token) - 1)))
            else:
                tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
                tokens.extend(token[index : index + 3] for index in range(len(token) - 2))
    return list(dict.fromkeys(item for item in tokens if item))


def build_hashed_embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    tokens = tokenize_text(text)
    if not tokens:
        return vector
    for token in tokens:
        index = hash(token) % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False))


def build_text_chunks(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if not text.strip():
        return []
    if len(text) <= chunk_size:
        return [text.strip()]
    chunks: list[str] = []
    step = max(1, chunk_size - chunk_overlap)
    for start in range(0, len(text), step):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(text):
            break
    return chunks


def _scene_context_text(
    *,
    chapter_number: int,
    scene: SceneCardModel,
    summary_text: str,
    draft_content: str,
) -> str:
    return (
        f"第{chapter_number}章第{scene.scene_number}场 {scene.title or ''}。"
        f"参与者：{'、'.join(scene.participants)}。"
        f"剧情目的：{scene.purpose.get('story', '')}。"
        f"情绪目的：{scene.purpose.get('emotion', '')}。"
        f"入场状态：{json_safe(scene.entry_state)}。"
        f"出场状态：{json_safe(scene.exit_state)}。"
        f"摘要：{summary_text}。"
        f"正文：{draft_content}"
    )


def _source_structural_score(source_type: str) -> float:
    return {
        "scene_context": 1.0,
        "scene_draft": 0.9,
        "chapter_draft": 0.8,
        "character": 0.7,
        "canon_fact": 0.7,
        "relationship": 0.65,
        "volume": 0.6,
        "world_rule": 0.6,
    }.get(source_type, 0.4)


def _coverage_bonus(matched_count: int, lexical_overlap: float) -> float:
    if matched_count >= 3 or lexical_overlap >= 0.6:
        return 0.15
    if matched_count >= 2 or lexical_overlap >= 0.4:
        return 0.10
    if matched_count >= 1:
        return 0.05
    return 0.0


def _score_chunk(
    *,
    query_text: str,
    query_tokens: set[str],
    query_embedding: list[float],
    chunk: RetrievalChunkModel,
    settings: AppSettings,
) -> tuple[float, float, float]:
    lexical_source = chunk.lexical_document or chunk.chunk_text
    chunk_tokens = set(tokenize_text(lexical_source))
    matched_count = len(query_tokens & chunk_tokens)
    lexical_overlap = matched_count / max(len(query_tokens), 1)
    vector_score = float(cosine_similarity(query_embedding, list(chunk.embedding)))
    semantic_score = max(vector_score, lexical_overlap * 0.85)
    structural_score = _source_structural_score(chunk.source_type)
    score = (
        lexical_overlap * settings.retrieval.hybrid_weights.lexical
        + semantic_score * settings.retrieval.hybrid_weights.vector
        + structural_score * settings.retrieval.hybrid_weights.structural
        + _coverage_bonus(matched_count, lexical_overlap)
    )
    if query_text.strip() and query_text.strip().lower() in chunk.chunk_text.lower():
        score += 0.08
    return min(score, 1.0), lexical_overlap, vector_score


async def _upsert_source_chunks(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    source_type: str,
    source_id: UUID,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> int:
    await session.execute(
        delete(RetrievalChunkModel).where(
            RetrievalChunkModel.project_id == project_id,
            RetrievalChunkModel.source_type == source_type,
            RetrievalChunkModel.source_id == source_id,
        )
    )
    chunks = build_text_chunks(
        text,
        settings.retrieval.chunk_size,
        settings.retrieval.chunk_overlap,
    )
    for chunk_index, chunk_text in enumerate(chunks):
        session.add(
            RetrievalChunkModel(
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
                chunk_index=chunk_index,
                chunk_text=chunk_text,
                embedding_model=settings.retrieval.embedding_model,
                embedding_dim=settings.retrieval.embedding_dimensions,
                embedding=build_hashed_embedding(chunk_text, settings.retrieval.embedding_dimensions),
                lexical_document=" ".join(tokenize_text(chunk_text)),
                metadata_json=metadata or {},
            )
        )
    await session.flush()
    return len(chunks)


async def refresh_story_bible_retrieval_index(
    session: AsyncSession,
    settings: AppSettings,
    project_id: UUID,
) -> int:
    chunk_count = 0
    world_rules = list(
        await session.scalars(select(WorldRuleModel).where(WorldRuleModel.project_id == project_id))
    )
    for world_rule in world_rules:
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project_id,
            source_type="world_rule",
            source_id=world_rule.id,
            text=(
                f"世界规则 {world_rule.rule_code} {world_rule.name}。"
                f"{world_rule.description}。"
                f"冲突后果：{world_rule.story_consequence or '未定义'}。"
            ),
            metadata={"kind": "world_rule", "name": world_rule.name},
        )

    characters = list(
        await session.scalars(select(CharacterModel).where(CharacterModel.project_id == project_id))
    )
    for character in characters:
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project_id,
            source_type="character",
            source_id=character.id,
            text=(
                f"角色 {character.name}，身份 {character.role}。"
                f"目标：{character.goal or '未定义'}。"
                f"恐惧：{character.fear or '未定义'}。"
                f"缺陷：{character.flaw or '未定义'}。"
                f"弧线状态：{character.arc_state or '未定义'}。"
            ),
            metadata={"kind": "character", "name": character.name, "role": character.role},
        )

    relationships = list(
        await session.scalars(select(RelationshipModel).where(RelationshipModel.project_id == project_id))
    )
    for relationship in relationships:
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project_id,
            source_type="relationship",
            source_id=relationship.id,
            text=(
                f"关系类型 {relationship.relationship_type}。"
                f"表面关系：{relationship.public_face or '未定义'}。"
                f"真实关系：{relationship.private_reality or '未定义'}。"
                f"张力：{relationship.tension_summary or '未定义'}。"
            ),
            metadata={"kind": "relationship", "relationship_type": relationship.relationship_type},
        )

    volumes = list(await session.scalars(select(VolumeModel).where(VolumeModel.project_id == project_id)))
    for volume in volumes:
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project_id,
            source_type="volume",
            source_id=volume.id,
            text=(
                f"卷 {volume.volume_number} {volume.title}。"
                f"主题：{volume.theme or '未定义'}。"
                f"目标：{volume.goal or '未定义'}。"
                f"障碍：{volume.obstacle or '未定义'}。"
            ),
            metadata={"kind": "volume", "volume_number": volume.volume_number},
        )
    return chunk_count


async def index_scene_retrieval_context(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
    chapter_id: UUID,
    chapter_number: int,
    scene: SceneCardModel,
    draft_content: str,
    summary_text: str,
) -> int:
    return await _upsert_source_chunks(
        session,
        settings,
        project_id=project.id,
        source_type="scene_context",
        source_id=scene.id,
        text=_scene_context_text(
            chapter_number=chapter_number,
            scene=scene,
            summary_text=summary_text,
            draft_content=draft_content,
        ),
        metadata={
            "kind": "scene_context",
            "chapter_id": str(chapter_id),
            "chapter_number": chapter_number,
            "scene_number": scene.scene_number,
        },
    )


async def ensure_project_retrieval_index(
    session: AsyncSession,
    settings: AppSettings,
    project_id: UUID,
) -> int:
    existing = list(
        await session.scalars(
            select(RetrievalChunkModel)
            .where(RetrievalChunkModel.project_id == project_id)
            .limit(1)
        )
    )
    if existing:
        return 0

    return await refresh_story_bible_retrieval_index(session, settings, project_id)


async def refresh_project_retrieval_index(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
) -> int:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")

    await session.execute(delete(RetrievalChunkModel).where(RetrievalChunkModel.project_id == project.id))
    chunk_count = await refresh_story_bible_retrieval_index(session, settings, project.id)

    canon_facts = list(
        await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project.id,
                CanonFactModel.is_current.is_(True),
            )
        )
    )
    for fact in canon_facts:
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project.id,
            source_type="canon_fact",
            source_id=fact.id,
            text=(
                f"{fact.subject_label} 的 {fact.predicate}。"
                f"{json_safe(fact.value_json)}"
            ),
            metadata={"kind": "canon_fact", "subject_label": fact.subject_label},
        )

    chapters = {
        chapter.id: chapter
        for chapter in await session.scalars(
            select(ChapterModel).where(ChapterModel.project_id == project.id)
        )
    }
    scenes = {
        scene.id: scene
        for scene in await session.scalars(
            select(SceneCardModel).where(SceneCardModel.project_id == project.id)
        )
    }

    scene_drafts = list(
        await session.scalars(
            select(SceneDraftVersionModel).where(
                SceneDraftVersionModel.project_id == project.id,
                SceneDraftVersionModel.is_current.is_(True),
            )
        )
    )
    for draft in scene_drafts:
        scene = scenes.get(draft.scene_card_id)
        chapter = chapters.get(scene.chapter_id) if scene is not None else None
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project.id,
            source_type="scene_draft",
            source_id=draft.id,
            text=draft.content_md,
            metadata={
                "kind": "scene_draft",
                "version_no": draft.version_no,
                "chapter_id": str(chapter.id) if chapter is not None else None,
                "chapter_number": chapter.chapter_number if chapter is not None else None,
                "scene_number": scene.scene_number if scene is not None else None,
            },
        )

    chapter_drafts = list(
        await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.project_id == project.id,
                ChapterDraftVersionModel.is_current.is_(True),
            )
        )
    )
    for draft in chapter_drafts:
        chapter = chapters.get(draft.chapter_id)
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project.id,
            source_type="chapter_draft",
            source_id=draft.id,
            text=draft.content_md,
            metadata={
                "kind": "chapter_draft",
                "version_no": draft.version_no,
                "chapter_id": str(chapter.id) if chapter is not None else None,
                "chapter_number": chapter.chapter_number if chapter is not None else None,
            },
        )
    current_scene_drafts = {draft.scene_card_id: draft for draft in scene_drafts}
    for scene in scenes.values():
        chapter = chapters.get(scene.chapter_id)
        if chapter is None:
            continue
        current_draft = current_scene_drafts.get(scene.id)
        summary_text = str(
            scene.metadata_json.get("latest_summary")
            or scene.purpose.get("story")
            or scene.hook_requirement
            or ""
        )
        chunk_count += await _upsert_source_chunks(
            session,
            settings,
            project_id=project.id,
            source_type="scene_context",
            source_id=scene.id,
            text=_scene_context_text(
                chapter_number=chapter.chapter_number,
                scene=scene,
                summary_text=summary_text,
                draft_content=current_draft.content_md if current_draft is not None else "",
            ),
            metadata={
                "kind": "scene_context",
                "chapter_id": str(chapter.id),
                "chapter_number": chapter.chapter_number,
                "scene_number": scene.scene_number,
            },
        )
    return chunk_count


def json_safe(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return re.sub(r"\s+", " ", str(payload))


async def search_project_retrieval(
    session: AsyncSession,
    settings: AppSettings,
    project_slug: str,
    query_text: str,
    *,
    top_k: int | None = None,
) -> RetrievalSearchResult:
    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"Project '{project_slug}' was not found.")
    return await search_retrieval_for_project(
        session,
        settings,
        project,
        query_text,
        top_k=top_k,
    )


async def search_retrieval_for_project(
    session: AsyncSession,
    settings: AppSettings,
    project: ProjectModel,
    query_text: str,
    *,
    top_k: int | None = None,
) -> RetrievalSearchResult:
    await ensure_project_retrieval_index(session, settings, project.id)
    chunks = list(
        await session.scalars(select(RetrievalChunkModel).where(RetrievalChunkModel.project_id == project.id))
    )
    query_tokens = set(tokenize_text(query_text))
    query_embedding = build_hashed_embedding(query_text, settings.retrieval.embedding_dimensions)
    scored: list[RetrievedChunk] = []
    fallback_candidates: list[RetrievedChunk] = []
    for chunk in chunks:
        score, lexical_overlap, vector_score = _score_chunk(
            query_text=query_text,
            query_tokens=query_tokens,
            query_embedding=query_embedding,
            chunk=chunk,
            settings=settings,
        )
        candidate = RetrievedChunk(
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            chunk_index=chunk.chunk_index,
            score=round(score, 4),
            chunk_text=chunk.chunk_text,
            metadata=chunk.metadata_json,
        )
        if lexical_overlap > 0 or vector_score >= 0.12:
            fallback_candidates.append(candidate)
        if score < settings.retrieval.min_score:
            continue
        scored.append(candidate)
    scored.sort(key=lambda item: item.score, reverse=True)
    if not scored:
        fallback_candidates.sort(key=lambda item: item.score, reverse=True)
        scored = fallback_candidates
    return RetrievalSearchResult(
        project_id=project.id,
        query_text=query_text,
        chunks=scored[: (top_k or settings.retrieval.top_k)],
    )
