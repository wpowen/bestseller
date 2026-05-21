from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.domain.fanqie_market import FanqieRankingBook, FanqieRankingSnapshot
from bestseller.services.fanqie_market_analyzer import build_market_analysis_bundle
from bestseller.services.fanqie_market_repository import (
    apply_fanqie_seed_profile,
    evaluate_and_persist_fanqie_long_readiness,
    import_fanqie_market_payload,
    inspect_fanqie_market_project,
    persist_market_planning_artifacts,
)

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    async def scalar(self, _stmt: object) -> object | None:
        return None

    def add(self, model: object) -> None:
        self.added.append(model)

    async def flush(self) -> None:
        self.flush_count += 1


def _payload() -> dict[str, object]:
    return {
        "data_date": "2026-05-20",
        "category": "都市脑洞",
        "data": [
            {
                "rank": 1,
                "book_id": "b1",
                "title": "每天六千万, 只能在县城花?",
                "author": "凤失凰",
                "tags": ["系统", "都市"],
                "readers": "92万",
                "intro": "主角每天到账巨额资金, 必须在县城完成消费和反击循环。",
            },
            {
                "rank": 2,
                "book_id": "b2",
                "title": "跳楼未遂, 我靠破案系统征服警花",
                "author": "我也不想的啊",
                "tags": ["破案", "系统"],
                "readers": "61万",
                "intro": "危机开局后获得破案系统, 用证据推动一次次真相曝光。",
            },
        ],
    }


@pytest.mark.asyncio
async def test_import_fanqie_market_payload_persists_snapshot_and_profiles() -> None:
    session = _FakeSession()

    result = await import_fanqie_market_payload(session, _payload(), category="都市脑洞")

    assert len(session.added) == 4
    assert session.flush_count == 3
    assert result.analysis is not None
    assert result.analysis.category_profile.sample_size == 2
    assert result.analysis.craft_profile.hook_rules
    assert result.to_dict()["summary"]["category"] == "都市脑洞"


@pytest.mark.asyncio
async def test_persist_market_planning_artifacts_stores_three_artifact_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = FanqieRankingSnapshot(
        category="都市脑洞",
        board_type="reading",
        data_date=date(2026, 5, 20),
        books=[
            FanqieRankingBook(
                source_book_id="b1",
                title="每天六千万, 只能在县城花?",
                rank=1,
                tags=["系统"],
                intro="每天到账, 只能在县城消费。",
            )
        ],
    )
    analysis = build_market_analysis_bundle(snapshot)
    captured: list[ArtifactType] = []
    project = SimpleNamespace(metadata_json={})

    async def fake_import(_session: object, project_slug: str, payload: object) -> object:
        assert project_slug == "market-project"
        captured.append(payload.artifact_type)
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        "bestseller.services.fanqie_market_repository.import_planning_artifact",
        fake_import,
    )

    async def fake_get_project(_session: object, project_slug: str) -> object:
        assert project_slug == "market-project"
        return project

    monkeypatch.setattr(
        "bestseller.services.fanqie_market_repository.get_project_by_slug",
        fake_get_project,
    )

    artifacts = await persist_market_planning_artifacts(
        object(),
        project_slug="market-project",
        analysis=analysis,
    )

    assert len(artifacts) == 4
    assert captured == [
        ArtifactType.FANQIE_MARKET_SNAPSHOT,
        ArtifactType.FANQIE_MARKET_PROFILE,
        ArtifactType.FANQIE_CATEGORY_PROFILE,
        ArtifactType.FANQIE_CRAFT_PROFILE,
    ]
    assert project.metadata_json["fanqie_craft_profile"]["category"] == "都市脑洞"


@pytest.mark.asyncio
async def test_evaluate_and_persist_fanqie_long_readiness_stores_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_import(_session: object, project_slug: str, payload: object) -> object:
        assert project_slug == "market-project"
        captured.append(payload)
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        "bestseller.services.fanqie_market_repository.import_planning_artifact",
        fake_import,
    )

    artifact = await evaluate_and_persist_fanqie_long_readiness(
        object(),
        project_slug="market-project",
        chapter_texts={
            1: "林澈被主管逼到门口, 否则押金冻结。他抓住证据反击并曝光真相?",
            2: "系统能力解锁, 但每次使用都有冷却代价。他拿到新证据?",
            3: "债主必须夺回证据, 林澈冲上前反制, 赢下筹码并发现父亲线索?",
        },
        protagonist_name="林澈",
    )

    assert artifact.id is not None
    assert captured[0].artifact_type == ArtifactType.FANQIE_LONG_RANKING_READINESS
    assert captured[0].content["project_slug"] == "market-project"
    assert captured[0].content["metrics"]["chapter_count"] == 3


@pytest.mark.asyncio
async def test_apply_fanqie_seed_profile_updates_project_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    project = SimpleNamespace(metadata_json={})

    async def fake_get_project(_session: object, project_slug: str) -> object:
        assert project_slug == "market-project"
        return project

    async def fake_import(_session: object, project_slug: str, payload: object) -> object:
        assert project_slug == "market-project"
        captured.append(payload)
        return SimpleNamespace(id=uuid4())

    session = SimpleNamespace(flush=lambda: None)

    async def fake_flush() -> None:
        return None

    session.flush = fake_flush
    monkeypatch.setattr(
        "bestseller.services.fanqie_market_repository.get_project_by_slug",
        fake_get_project,
    )
    monkeypatch.setattr(
        "bestseller.services.fanqie_market_repository.import_planning_artifact",
        fake_import,
    )

    result = await apply_fanqie_seed_profile(
        session,
        project_slug="market-project",
        profile_key="urban-brain",
    )

    assert result["profile_key"] == "urban-brain"
    assert [payload.artifact_type for payload in captured] == [
        ArtifactType.FANQIE_MARKET_PROFILE,
        ArtifactType.FANQIE_CATEGORY_PROFILE,
        ArtifactType.FANQIE_CRAFT_PROFILE,
    ]
    assert project.metadata_json["fanqie_seed_profile_key"] == "urban-brain"
    assert project.metadata_json["fanqie_craft_profile"]["category"] == "都市脑洞"


@pytest.mark.asyncio
async def test_inspect_fanqie_market_project_returns_latest_artifact_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={
            "fanqie_seed_profile_key": "urban-brain",
            "fanqie_market_summary": {"category": "都市脑洞"},
            "fanqie_craft_profile": {"category": "都市脑洞"},
        },
    )
    artifact = SimpleNamespace(
        id=uuid4(),
        version_no=2,
        created_at=None,
        status="approved",
        notes="latest",
        content={"summary": {"category": "都市脑洞"}},
    )

    async def fake_get_project(_session: object, project_slug: str) -> object:
        assert project_slug == "market-project"
        return project

    class FakeInspectSession:
        async def scalar(self, _stmt: object) -> object | None:
            return artifact

    monkeypatch.setattr(
        "bestseller.services.fanqie_market_repository.get_project_by_slug",
        fake_get_project,
    )

    result = await inspect_fanqie_market_project(
        FakeInspectSession(),
        project_slug="market-project",
    )

    assert result["fanqie_seed_profile_key"] == "urban-brain"
    assert result["metadata_summary"]["category"] == "都市脑洞"
    assert result["artifacts"]["fanqie_market_profile"]["version_no"] == 2
