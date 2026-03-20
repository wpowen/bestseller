from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import (
    CharacterEngineConfig,
    MarketPositioningConfig,
    ProjectCreate,
    StylePreferenceConfig,
    WritingProfile,
)
from bestseller.infra.db.models import ProjectModel, StyleGuideModel
from bestseller.services import projects as project_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []
        self.last_scalar_statement = None

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            table = getattr(obj, "__table__", None)
            if table is None or "id" not in table.c:
                continue
            if getattr(obj, "id", None) is None:
                setattr(obj, "id", uuid4())

    async def scalar(self, stmt: object) -> object | None:
        self.last_scalar_statement = stmt
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)


def build_settings() -> object:
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


@pytest.mark.asyncio
async def test_create_project_creates_default_style_guide(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> None:
        return None

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession()

    project = await project_services.create_project(
        session,
        ProjectCreate(
            slug="my-story",
            title="My Story",
            genre="fantasy",
            target_word_count=120000,
            target_chapters=60,
        ),
        build_settings(),
    )

    assert project.id is not None
    assert project.slug == "my-story"
    style_guides = [obj for obj in session.added if isinstance(obj, StyleGuideModel)]
    assert len(style_guides) == 1
    assert style_guides[0].project_id == project.id
    assert "fantasy" in style_guides[0].tone_keywords


@pytest.mark.asyncio
async def test_create_project_applies_writing_profile_to_style_guide_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> None:
        return None

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession()

    project = await project_services.create_project(
        session,
        ProjectCreate(
            slug="doomsday-story",
            title="末日零点仓库",
            genre="末日科幻",
            sub_genre="重生囤货",
            audience="男频连载读者",
            target_word_count=300000,
            target_chapters=120,
            writing_profile=WritingProfile(
                market=MarketPositioningConfig(
                    platform_target="番茄小说",
                    prompt_pack_key="apocalypse-supply-chain",
                    reader_promise="开篇就给重生囤货与末日倒计时，前三章连续抛出资源优势和危机升级。",
                    selling_points=["重生回档", "未来商城", "资源碾压"],
                    trope_keywords=["末日", "囤货", "系统"],
                    pacing_profile="fast",
                ),
                character=CharacterEngineConfig(
                    protagonist_archetype="先知型求生者",
                    golden_finger="未来拼单商城",
                ),
                style=StylePreferenceConfig(
                    pov_type="first-person",
                    tone_keywords=["狠", "快", "压迫感"],
                    prose_style="commercial-web-serial",
                    sentence_style="short-punchy",
                    info_density="lean",
                    dialogue_ratio=0.48,
                    reference_works=["《全球冰封》"],
                    custom_rules=["第一章 800 字内给出明确异变信号。"],
                ),
            ),
        ),
        build_settings(),
    )

    assert project.metadata_json["writing_profile"]["market"]["platform_target"] == "番茄小说"
    assert project.metadata_json["writing_profile"]["market"]["prompt_pack_key"] == "apocalypse-supply-chain"
    assert project.metadata_json["prompt_pack_key"] == "apocalypse-supply-chain"
    assert project.metadata_json["writing_profile"]["character"]["golden_finger"] == "未来拼单商城"
    style_guides = [obj for obj in session.added if isinstance(obj, StyleGuideModel)]
    assert len(style_guides) == 1
    assert style_guides[0].pov_type == "first-person"
    assert style_guides[0].prose_style == "commercial-web-serial"
    assert style_guides[0].sentence_style == "short-punchy"
    assert float(style_guides[0].dialogue_ratio) == pytest.approx(0.48)
    assert "压迫感" in style_guides[0].tone_keywords
    assert style_guides[0].reference_works == ["《全球冰封》"]
    assert "第一章 800 字内给出明确异变信号。" in style_guides[0].custom_rules


def test_resolve_writing_profile_merges_prompt_pack_defaults() -> None:
    from bestseller.services.writing_profile import resolve_writing_profile

    profile = resolve_writing_profile(
        {"market": {"prompt_pack_key": "apocalypse-supply-chain"}},
        genre="末日科幻",
        sub_genre="重生囤货",
        audience="男频连载读者",
    )

    assert profile.market.prompt_pack_key == "apocalypse-supply-chain"
    assert profile.market.platform_target == "番茄小说"
    assert "末日" in profile.market.trope_keywords
    assert profile.character.golden_finger


@pytest.mark.asyncio
async def test_create_project_rejects_duplicate_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> object:
        return object()

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)

    with pytest.raises(ValueError, match="already exists"):
        await project_services.create_project(
            FakeSession(),
            ProjectCreate(
                slug="my-story",
                title="My Story",
                genre="fantasy",
                target_word_count=120000,
                target_chapters=60,
            ),
            build_settings(),
        )


@pytest.mark.asyncio
async def test_import_planning_artifact_uses_null_scope_filter_and_increments_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[2])

    artifact = await project_services.import_planning_artifact(
        session,
        "my-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "A hero survives."},
        ),
    )

    assert artifact.version_no == 3
    assert artifact.project_id == project.id
    compiled_sql = str(
        session.last_scalar_statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "scope_ref_id IS NULL" in compiled_sql


def test_load_json_file_reads_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"chapter": 1, "title": "Opening"}), encoding="utf-8")

    payload = project_services.load_json_file(payload_path)

    assert payload["chapter"] == 1
    assert payload["title"] == "Opening"
