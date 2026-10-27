# ruff: noqa: RUF001
from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete

from bestseller.infra.db.models import ProjectModel
from bestseller.infra.db.session import create_engine, create_session_factory
from bestseller.services.book_packaging_contract import (
    IntroVariant,
    build_book_packaging_contract,
)
from bestseller.services.book_packaging_store import (
    PackagingContractIntegrityError,
    activate_packaging_contract,
    load_active_packaging_contract,
    persist_packaging_contract,
    promote_creation_packaging_contract_if_ready,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def packaging_db() -> AsyncIterator[tuple[object, ProjectModel]]:
    engine = create_engine()
    factory = create_session_factory(engine=engine)
    project = ProjectModel(
        id=uuid4(),
        slug=f"packaging-{uuid4().hex}",
        title="内部工作名",
        genre="异世界",
        target_word_count=80_000,
        target_chapters=30,
        metadata_json={},
    )
    async with factory() as session:
        session.add(project)
        await session.commit()
    try:
        yield factory, project
    finally:
        async with factory() as session:
            await session.execute(delete(ProjectModel).where(ProjectModel.id == project.id))
            await session.commit()
        await engine.dispose()


def _contract(project_id: str, *, revision: int = 1, title: str = "荒城通关簿"):
    intro = {
        "variant_id": "long-v1",
        "text": "商洛接手一座废城，用会报价的界碑和现代通行规则，把万族争道变成一门生意。",
        "promise_refs": ["arch:trade-war", "effect:comedy_engine"],
    }
    intro_hash = IntroVariant.model_validate(intro).content_hash
    return build_book_packaging_contract(
        project_id=project_id,
        revision=revision,
        title=title,
        title_promise="经营废城、重写万族通行规则",
        protagonist="商洛",
        situation="被哥布林占据的废城",
        action="经营关城并建立新秩序",
        result="成为万族交通中枢",
        genre="异世界经营",
        tags=("穿越", "升级流", "金手指", "经营", "喜剧"),
        effect_skills=("comedy_engine",),
        architecture_refs=("arch:trade-war",),
        intro_variants=(intro,),
        surfaces=(
            {
                "target_platform": "fanqie",
                "listing_surface": "long-form",
                "active_intro_variant_id": "long-v1",
                "active_intro_variant_hash": intro_hash,
            },
        ),
    )


@pytest.mark.asyncio
async def test_active_contract_is_the_hash_bound_title_and_intro_authority(packaging_db) -> None:
    factory, detached = packaging_db
    async with factory() as session:
        project = await session.get(ProjectModel, detached.id)
        contract = _contract(str(project.id))
        row = await persist_packaging_contract(
            session,
            project=project,
            contract=contract,
            target_platform="fanqie",
            source_design_snapshot_hash="d" * 64,
        )
        await activate_packaging_contract(session, project=project, row=row)
        await session.commit()

    async with factory() as session:
        project = await session.get(ProjectModel, detached.id)
        payload = await load_active_packaging_contract(
            session,
            project=project,
            target_platform="fanqie",
            listing_surface="long-form",
        )
        assert payload["title"] == "荒城通关簿"
        assert payload["contract_hash"] == project.metadata_json["active_packaging_contract_hash"]
        assert project.metadata_json["ranking_evidence_invalidated_layers"] == [
            "reader",
            "platform_cohort",
            "rank_receipt",
        ]


@pytest.mark.asyncio
async def test_stale_project_packaging_pointer_is_rejected(packaging_db) -> None:
    factory, detached = packaging_db
    async with factory() as session:
        project = await session.get(ProjectModel, detached.id)
        row = await persist_packaging_contract(
            session,
            project=project,
            contract=_contract(str(project.id)),
            target_platform="fanqie",
            source_design_snapshot_hash="d" * 64,
        )
        await activate_packaging_contract(session, project=project, row=row)
        project.metadata_json = {
            **project.metadata_json,
            "active_packaging_contract_hash": "stale",
        }
        await session.commit()

    async with factory() as session:
        project = await session.get(ProjectModel, detached.id)
        with pytest.raises(PackagingContractIntegrityError, match="STALE_PACKAGING_CONSUMER"):
            await load_active_packaging_contract(
                session,
                project=project,
                target_platform="fanqie",
                listing_surface="long-form",
            )


@pytest.mark.asyncio
async def test_promotion_retry_validates_existing_pointer_instead_of_silently_skipping(
    packaging_db,
) -> None:
    factory, detached = packaging_db
    async with factory() as session:
        project = await session.get(ProjectModel, detached.id)
        row = await persist_packaging_contract(
            session,
            project=project,
            contract=_contract(str(project.id)),
            target_platform="fanqie",
            source_design_snapshot_hash="d" * 64,
        )
        await activate_packaging_contract(session, project=project, row=row)
        project.metadata_json = {
            **project.metadata_json,
            "concept_contract_version": "2",
            "conception_artifacts": {
                "story_appeal": {"final_packaging_promotion": {"passed": True}}
            },
            "active_packaging_contract_hash": "stale",
        }
        await session.commit()

    async with factory() as session:
        project = await session.get(ProjectModel, detached.id)
        with pytest.raises(PackagingContractIntegrityError, match="STALE_PACKAGING_CONSUMER"):
            await promote_creation_packaging_contract_if_ready(session, project=project)
