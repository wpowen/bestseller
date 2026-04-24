from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from bestseller.domain.enums import ArtifactType
from bestseller.domain.planning import PlanningArtifactCreate
from bestseller.domain.project import (
    AmazonKdpPublicationProfile,
    CharacterEngineConfig,
    MarketPositioningConfig,
    ProjectCreate,
    PublishingProfilesConfig,
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
                obj.id = uuid4()

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
    assert project.metadata_json["truth_version"] == 1
    assert project.metadata_json["truth_updated_at"] is None
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


@pytest.mark.asyncio
async def test_create_project_persists_amazon_kdp_publication_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> None:
        return None

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession()

    project = await project_services.create_project(
        session,
        ProjectCreate(
            slug="english-launch",
            title="English Launch",
            genre="fantasy",
            language="en-US",
            target_word_count=90000,
            target_chapters=30,
            publishing=PublishingProfilesConfig(
                amazon_kdp=AmazonKdpPublicationProfile(
                    language="en-US",
                    book_title="English Launch",
                    author_display_name="Owen Example",
                    description="A fantasy launch novel.",
                    categories=["Fiction / Fantasy / Epic"],
                    ai_generated_text="assisted",
                    ai_generated_images="none",
                )
            ),
        ),
        build_settings(),
    )

    payload = project.metadata_json["publishing"]["amazon_kdp"]
    assert project.language == "en-US"
    assert payload["book_title"] == "English Launch"
    assert payload["author_display_name"] == "Owen Example"


def test_project_style_guide_relationship_cascades_deletion() -> None:
    """Regression guard for the ``db_delete_failed: Dependency rule on column
    'projects.id' tried to blank-out primary key column 'style_guides.project_id'``
    error that blocked project deletion.

    ``style_guides.project_id`` is both the foreign key **and** the primary key,
    so SQLAlchemy's default cascade (``save-update, merge``) would try to
    orphan the child by nulling its FK on parent delete — which fails because
    a PK cannot be null. The fix is ``cascade="all, delete-orphan"`` with
    ``passive_deletes=True`` so SA defers to the DB-level ``ON DELETE CASCADE``.
    """
    mapper = ProjectModel.__mapper__
    rel = mapper.relationships["style_guide"]
    cascade_flags = rel.cascade
    assert cascade_flags.delete, (
        "ProjectModel.style_guide must cascade delete; otherwise SA will try "
        "to null style_guides.project_id which is a primary key."
    )
    assert cascade_flags.delete_orphan, (
        "ProjectModel.style_guide must use delete-orphan so disassociation "
        "never attempts to leave a style_guide without a project."
    )
    assert rel.passive_deletes is True, (
        "ProjectModel.style_guide must have passive_deletes=True so SA defers "
        "to PostgreSQL's ON DELETE CASCADE on the FK."
    )


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
    assert profile.market.trope_keywords == []
    assert not profile.character.golden_finger


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
    assert project.metadata_json["truth_version"] == 1
    assert project.metadata_json["truth_last_changed_artifact_type"] is None
    assert ArtifactType.BOOK_SPEC.value in project.metadata_json["_truth_artifact_fingerprints"]
    compiled_sql = str(
        session.last_scalar_statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "scope_ref_id IS NULL" in compiled_sql


@pytest.mark.asyncio
async def test_import_planning_artifact_bumps_truth_version_when_core_artifact_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={
            "truth_version": 1,
            "truth_updated_at": None,
            "truth_last_changed_artifact_type": None,
            "_truth_artifact_fingerprints": {
                ArtifactType.BOOK_SPEC.value: "old-hash",
            },
            "_truth_change_log": [],
        },
    )
    project.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[0])

    await project_services.import_planning_artifact(
        session,
        "my-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "A different hero survives."},
        ),
    )

    assert project.metadata_json["truth_version"] == 2
    assert project.metadata_json["truth_last_changed_artifact_type"] == ArtifactType.BOOK_SPEC.value
    assert project.metadata_json["truth_updated_at"] is not None
    assert len(project.metadata_json["_truth_change_log"]) == 1


def test_load_json_file_reads_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"chapter": 1, "title": "Opening"}), encoding="utf-8")

    payload = project_services.load_json_file(payload_path)

    assert payload["chapter"] == 1
    assert payload["title"] == "Opening"


# ---------------------------------------------------------------------------
# Regression: style_guides enum-like columns must be TEXT (migration 0013)
# ---------------------------------------------------------------------------
#
# Background: the LLM conception pipeline writes free-form Chinese descriptions
# into pov_type / sentence_style / info_density / tense (e.g. 51-char
# "短句为主，穿插中等长度复合句构建张力；..."). The Pydantic domain model has
# always allowed ``max_length=4000``, but the DB schema historically capped
# these at VARCHAR(32), causing autowrite to crash in ``create_project`` with
# ``StringDataRightTruncationError``. Migration 0013 widens the columns to
# TEXT — these tests pin both layers so we don't silently regress back.


def test_style_guide_enum_columns_are_text_not_varchar() -> None:
    """pov_type / tense / sentence_style / info_density must be TEXT."""
    from sqlalchemy import String, Text

    for col_name in ("pov_type", "tense", "sentence_style", "info_density"):
        col = StyleGuideModel.__table__.c[col_name]
        # Text is a subclass of String in SA, so order matters: reject String(N)
        # explicitly (where length is set), then confirm Text.
        assert (
            not isinstance(col.type, String) or col.type.length is None
        ), (
            f"style_guides.{col_name} is {col.type!r}; must be TEXT "
            "because the LLM conception pipeline writes long descriptions."
        )
        assert isinstance(col.type, Text), (
            f"style_guides.{col_name} must be sqlalchemy.Text, got {col.type!r}"
        )


def test_style_preference_accepts_long_chinese_sentence_style() -> None:
    """The 51-char Chinese value that originally crashed autowrite must round-trip."""
    long_value = (
        "短句为主，穿插中等长度复合句构建张力；"
        "对话简洁有力，避免冗长内心独白式独白（内心戏通过行为和反应呈现）"
    )
    assert len(long_value) > 32  # guard against the old VARCHAR(32) cap

    cfg = StylePreferenceConfig(sentence_style=long_value)
    assert cfg.sentence_style == long_value


def test_style_preference_accepts_annotated_pov_type() -> None:
    """LLMs frequently append Chinese annotations to pov_type — must be accepted."""
    annotated = "third-limited（跟随主角陆征视角）"
    assert len(annotated) > 16  # much longer than a bare enum code

    cfg = StylePreferenceConfig(pov_type=annotated)
    assert cfg.pov_type == annotated
