from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from bestseller.domain.enums import ArtifactType, ProjectType
from bestseller.domain.fanqie_short import FANQIE_SHORT_CONTENT_MODE
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
from bestseller.services.genre_skill_profiles import (
    GENRE_SKILL_PROFILE_METADATA_KEY,
    GENRE_SKILL_PROFILE_VERSION,
)
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
async def test_create_project_persists_genre_skill_profile_snapshot() -> None:
    settings = build_settings()
    session = FakeSession()
    payload = ProjectCreate(
        slug="skill-profile-book",
        title="Skill Profile Book",
        genre="悬疑",
        sub_genre="民俗怪谈",
        target_word_count=120000,
        target_chapters=60,
    )

    project = await project_services.create_project(session, payload, settings)

    profile = project.metadata_json[GENRE_SKILL_PROFILE_METADATA_KEY]
    assert profile["profile_key"] == "suspense-mystery"
    assert profile["prompt_pack_key"] == "suspense-mystery"
    assert "suspense-mystery" in profile["research_skill_keys"]
    assert profile["activation"]["gate_mode"] == "audit_only"


def test_project_delete_tombstone_round_trip(tmp_path: Path) -> None:
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))

    assert project_services.is_project_delete_tombstoned(settings, "book-a") is False

    project_services.mark_project_delete_tombstone(settings, "book-a")

    assert project_services.is_project_delete_tombstoned(settings, "book-a") is True
    assert project_services.is_project_delete_tombstoned(settings, "book-b") is False

    project_services.clear_project_delete_tombstone(settings, "book-a")

    assert project_services.is_project_delete_tombstoned(settings, "book-a") is False


@pytest.mark.asyncio
async def test_delete_missing_project_writes_tombstone(tmp_path: Path) -> None:
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))
    session = FakeSession(scalar_results=[None])

    result = await project_services.delete_project_completely(
        session,
        settings,
        "already-gone",
    )

    assert result["db_deleted"] is False
    assert result["fs_deleted"] is True
    assert result["errors"] == ["project_not_found_in_db"]
    assert project_services.is_project_delete_tombstoned(settings, "already-gone") is True


class _DeleteSession:
    """Fake async session covering the ``delete_project_completely`` path.

    ``get_project_by_slug`` reads through ``scalar``; the delete itself uses
    ``execute`` (timeout pragmas), ``delete``, ``commit`` and ``rollback``.
    """

    def __init__(
        self,
        project: object,
        *,
        commit_failures: int = 0,
        gone_on_refetch: bool = False,
    ) -> None:
        self._project = project
        self._commit_failures = commit_failures
        self._gone_on_refetch = gone_on_refetch
        self.commit_count = 0
        self.rollback_count = 0
        self.delete_count = 0
        self.execute_count = 0
        self._scalar_calls = 0

    async def scalar(self, stmt: object) -> object | None:
        self._scalar_calls += 1
        if self._scalar_calls > 1 and self._gone_on_refetch:
            return None
        return self._project

    async def execute(self, stmt: object) -> None:
        self.execute_count += 1

    async def delete(self, obj: object) -> None:
        self.delete_count += 1

    async def commit(self) -> None:
        if self._commit_failures > 0:
            self._commit_failures -= 1
            raise RuntimeError("canceling statement due to lock_timeout")
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


@pytest.mark.asyncio
async def test_delete_succeeds_and_tombstones(tmp_path: Path) -> None:
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))
    session = _DeleteSession(SimpleNamespace(slug="book-x"))

    result = await project_services.delete_project_completely(session, settings, "book-x")

    assert result["db_deleted"] is True
    assert result["fs_deleted"] is True
    assert result["errors"] == []
    assert session.delete_count == 1
    assert session.commit_count == 1
    assert project_services.is_project_delete_tombstoned(settings, "book-x") is True


@pytest.mark.asyncio
async def test_delete_retries_transient_lock_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_services, "_DB_DELETE_RETRY_DELAY_SECONDS", 0)
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))
    session = _DeleteSession(SimpleNamespace(slug="book-y"), commit_failures=1)

    result = await project_services.delete_project_completely(session, settings, "book-y")

    assert result["db_deleted"] is True
    assert all(not str(e).startswith("db_delete_failed") for e in result["errors"])
    assert session.rollback_count == 1
    assert session.commit_count == 1
    assert project_services.is_project_delete_tombstoned(settings, "book-y") is True


@pytest.mark.asyncio
async def test_delete_gives_up_after_retries_but_tombstone_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_services, "_DB_DELETE_RETRY_DELAY_SECONDS", 0)
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))
    session = _DeleteSession(SimpleNamespace(slug="book-z"), commit_failures=99)

    result = await project_services.delete_project_completely(session, settings, "book-z")

    assert result["db_deleted"] is False
    # Disk is left untouched when the DB delete fails ...
    assert result["fs_deleted"] is False
    assert any(str(e).startswith("db_delete_failed") for e in result["errors"])
    assert session.rollback_count == project_services._DB_DELETE_MAX_ATTEMPTS
    # ... but the tombstone written FIRST keeps the project suppressed so it
    # cannot be resurrected by self-heal — the core durability guarantee.
    assert project_services.is_project_delete_tombstoned(settings, "book-z") is True


@pytest.mark.asyncio
async def test_delete_treats_concurrent_completion_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_services, "_DB_DELETE_RETRY_DELAY_SECONDS", 0)
    settings = SimpleNamespace(output=SimpleNamespace(base_dir=str(tmp_path)))
    session = _DeleteSession(
        SimpleNamespace(slug="book-c"), commit_failures=1, gone_on_refetch=True
    )

    result = await project_services.delete_project_completely(session, settings, "book-c")

    assert result["db_deleted"] is True
    assert all(not str(e).startswith("db_delete_failed") for e in result["errors"])
    assert project_services.is_project_delete_tombstoned(settings, "book-c") is True


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
async def test_create_project_rejects_duplicate_fanqie_short_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> None:
        return None

    duplicate = ProjectModel(
        slug="existing-short",
        title="器语者",
        genre="东方美学幻想",
        target_word_count=10000,
        target_chapters=6,
        project_type=ProjectType.FANQIE_SHORT.value,
        metadata_json={"library_archived": True},
    )
    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[duplicate])

    with pytest.raises(ValueError, match="existing-short"):
        await project_services.create_project(
            session,
            ProjectCreate(
                slug="new-short",
                title="器语者",
                genre="东方美学幻想",
                target_word_count=10000,
                target_chapters=6,
                project_type=ProjectType.FANQIE_SHORT,
                metadata={"content_mode": FANQIE_SHORT_CONTENT_MODE},
            ),
            build_settings(),
        )

    assert session.added == []


@pytest.mark.asyncio
async def test_create_project_initializes_genre_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> None:
        return None

    calls: list[tuple[object, str, str, str]] = []

    async def fake_initialize(
        session: object,
        project: ProjectModel,
    ) -> dict[str, object]:
        calls.append((session, project.slug, project.genre, project.language))
        return {"supported_pack": "category_action_progression_zh"}

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        project_services,
        "initialize_project_genre_capabilities",
        fake_initialize,
    )
    session = FakeSession()

    project = await project_services.create_project(
        session,
        ProjectCreate(
            slug="my-story",
            title="My Story",
            genre="仙侠升级",
            sub_genre="宗门逆袭",
            target_word_count=120000,
            target_chapters=60,
        ),
        build_settings(),
    )

    assert calls == [(session, "my-story", "仙侠升级", project.language)]


@pytest.mark.asyncio
async def test_create_project_seeds_initial_planning_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_project_by_slug(session: object, slug: str) -> None:
        return None

    async def fake_initialize(session: object, project: ProjectModel) -> dict[str, object]:
        return {"supported_pack": "category_action_progression_zh"}

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    monkeypatch.setattr(
        project_services,
        "initialize_project_genre_capabilities",
        fake_initialize,
    )
    session = FakeSession()

    project = await project_services.create_project(
        session,
        ProjectCreate(
            slug="my-story",
            title="My Story",
            genre="仙侠升级",
            sub_genre="宗门逆袭",
            target_word_count=120000,
            target_chapters=60,
            metadata={
                "premise": "外门杂役得到会记账的道种。",
                "story_facets": {
                    "setting": "外门杂役峰与三月秘境",
                    "narrative_drive": "低位反制",
                    "trope_tags": ["凡人流", "宗门生存"],
                },
                "benchmark_works": ["凡人修仙传结构对标"],
            },
        ),
        build_settings(),
    )

    report = project.metadata_json["prewrite_readiness_report"]
    assert project.metadata_json["planning_kernel"]["project_slug"] == "my-story"
    assert report["passed"] is False
    assert any(
        finding["code"] == "progression_engine_missing"
        for finding in report["blocking_findings"]
    )
    assert any(
        "补齐可计量的升级体系" in action
        for action in report["recommended_repair_actions"]
    )
    assert any(
        "资源账" in directive
        for directive in project.metadata_json["prewrite_repair_directives"]
    )


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
    session = FakeSession(scalar_results=[None, 2])

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
    session = FakeSession(scalar_results=[None, 0])

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


@pytest.mark.asyncio
async def test_import_planning_artifact_adds_strict_prewrite_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="strict-story",
        title="严格规划书",
        genre="都市修真",
        target_word_count=40000,
        target_chapters=20,
        metadata_json={
            "quality_profile": "commercial_strict_prewrite",
            "methodology_contract_mode": "strict",
        },
    )
    project.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[None, 0])

    artifact = await project_services.import_planning_artifact(
        session,
        "strict-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "主角用现代修真协议打破旧秩序。"},
        ),
    )

    assert artifact.content["_meta"]["quality_profile"] == "commercial_strict_prewrite"
    assert (
        artifact.content["_meta"]["methodology_lineage"]["methodology_contract_mode"]
        == "strict"
    )
    assert artifact.content["_meta"]["repair_attempts"] == []


@pytest.mark.asyncio
async def test_import_planning_artifact_records_genre_skill_profile_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="profile-story",
        title="题材策略书",
        genre="悬疑",
        sub_genre="民俗怪谈",
        target_word_count=40000,
        target_chapters=20,
        metadata_json={
            GENRE_SKILL_PROFILE_METADATA_KEY: {
                "version": GENRE_SKILL_PROFILE_VERSION,
                "profile_key": "suspense-mystery",
                "genre": "悬疑",
                "sub_genre": "民俗怪谈",
                "research_skill_keys": ["base-research-discipline", "suspense-mystery"],
                "prompt_pack_key": "suspense-mystery",
                "review_profile_key": "suspense-mystery",
                "threshold_profile_key": "suspense-mystery",
            }
        },
    )
    project.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[None, 0])

    artifact = await project_services.import_planning_artifact(
        session,
        "profile-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "主角按民俗禁忌追查失踪案。"},
        ),
    )

    lineage = artifact.content["_meta"]["methodology_lineage"]
    assert lineage["genre_skill_profile_key"] == "suspense-mystery"
    assert lineage["genre_skill_profile_version"] == GENRE_SKILL_PROFILE_VERSION


@pytest.mark.asyncio
async def test_import_planning_artifact_reuses_legacy_exact_content_without_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = ProjectModel(
        slug="legacy-story",
        title="Legacy Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()
    reusable = project_services.PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.BOOK_SPEC.value,
        version_no=2,
        status="approved",
        schema_version="1.0",
        content={"logline": "A hero survives."},
    )
    reusable.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[reusable])

    artifact = await project_services.import_planning_artifact(
        session,
        "legacy-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "A hero survives."},
        ),
    )

    assert artifact is reusable
    assert session.added == []


@pytest.mark.asyncio
async def test_import_planning_artifact_reuses_matching_input_hash(
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
    reusable = project_services.PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.BOOK_SPEC.value,
        version_no=4,
        status="approved",
        schema_version="1.0",
        content={"logline": "A hero survives.", "_meta": {"input_hash": "same-input"}},
    )
    reusable.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[reusable])

    artifact = await project_services.import_planning_artifact(
        session,
        "my-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "A hero survives.", "_meta": {"input_hash": "same-input"}},
        ),
    )

    assert artifact is reusable
    assert session.added == []


@pytest.mark.asyncio
async def test_import_planning_artifact_forces_new_version_when_hash_matches_but_content_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3 复用陷阱：内容已改但 _meta.input_hash 未变 → 不得静默返回旧版本。"""

    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()
    stale = project_services.PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.BOOK_SPEC.value,
        version_no=4,
        status="approved",
        schema_version="1.0",
        content={"logline": "OLD cast.", "_meta": {"input_hash": "same-input"}},
    )
    stale.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    # scalar order: input_hash reuse → exact-content match (miss) → max version
    session = FakeSession(scalar_results=[stale, None, 4])

    artifact = await project_services.import_planning_artifact(
        session,
        "my-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "NEW cast.", "_meta": {"input_hash": "same-input"}},
        ),
    )

    assert artifact is not stale
    assert artifact in session.added
    assert artifact.version_no == 5
    assert artifact.content["logline"] == "NEW cast."
    assert "input_hash matched but content differs" in (artifact.notes or "")


@pytest.mark.asyncio
async def test_import_planning_artifact_forced_version_preserves_caller_notes(
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
    stale = project_services.PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.BOOK_SPEC.value,
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={"logline": "OLD.", "_meta": {"input_hash": "h1"}},
    )
    stale.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[stale, None, 1])

    artifact = await project_services.import_planning_artifact(
        session,
        "my-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "NEW.", "_meta": {"input_hash": "h1"}},
            notes="manual cast patch",
        ),
    )

    assert "manual cast patch" in artifact.notes
    assert "input_hash matched but content differs" in artifact.notes


@pytest.mark.asyncio
async def test_import_planning_artifact_still_reuses_when_only_meta_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """剥 _meta 后内容一致 → 复用旧版本（合法短路不受影响）。"""

    project = ProjectModel(
        slug="my-story",
        title="My Story",
        genre="fantasy",
        target_word_count=120000,
        target_chapters=60,
        metadata_json={},
    )
    project.id = uuid4()
    reusable = project_services.PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type=ArtifactType.BOOK_SPEC.value,
        version_no=2,
        status="approved",
        schema_version="1.0",
        content={
            "logline": "Same content.",
            "_meta": {"input_hash": "h2", "extra_stamp": "old-run"},
        },
    )
    reusable.id = uuid4()

    async def fake_get_project_by_slug(session: object, slug: str) -> ProjectModel:
        return project

    monkeypatch.setattr(project_services, "get_project_by_slug", fake_get_project_by_slug)
    session = FakeSession(scalar_results=[reusable])

    artifact = await project_services.import_planning_artifact(
        session,
        "my-story",
        PlanningArtifactCreate(
            artifact_type=ArtifactType.BOOK_SPEC,
            content={"logline": "Same content.", "_meta": {"input_hash": "h2"}},
        ),
    )

    assert artifact is reusable
    assert session.added == []


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


# ── delete_project_completely: Redis cleanup (zombie task-board fix) ──────────


def test_redis_key_belongs_to_slug_matches_whole_segments_only() -> None:
    f = project_services._redis_key_belongs_to_slug
    # Real self-heal key shapes for the deleted slug → match.
    assert f("task:autowrite:heal:fanren-bench-v4:progress", "fanren-bench-v4")
    assert f("task:autowrite:heal:fanren-bench-v4:milestones", "fanren-bench-v4")
    assert f("arq:job:repair:heal:fanren-bench-v4", "fanren-bench-v4")
    # Substring collision must NOT match (v4 must not sweep v40 / v41).
    assert not f("task:autowrite:heal:fanren-bench-v40:progress", "fanren-bench-v4")
    # A different book's key must NOT match.
    assert not f("task:autowrite:heal:shilouyan-bench-v2:progress", "fanren-bench-v4")
    assert not f("arq:job:run_self_heal_task:1781513130123", "fanren-bench-v4")


@pytest.mark.asyncio
async def test_purge_project_redis_keys_scopes_to_slug(monkeypatch) -> None:
    """Only the deleted slug's keys are removed; other books are untouched."""

    store = {
        "task:autowrite:heal:fanren-bench-v4:progress": 1,
        "task:autowrite:heal:fanren-bench-v4:milestones": 1,
        "arq:job:repair:heal:fanren-bench-v4": 1,
        "task:autowrite:heal:fanren-bench-v40:progress": 1,  # collision guard
        "task:autowrite:heal:shilouyan-bench-v2:progress": 1,  # other book
        "arq:job:run_self_heal_task:1781513130123": 1,  # generic scheduler
    }

    class _FakeRedis:
        def __init__(self, data):
            self._data = data

        async def scan_iter(self, match="*"):
            needle = match.strip("*")
            for k in list(self._data):
                if needle in k:
                    yield k.encode()

        async def delete(self, *keys):
            n = 0
            for k in keys:
                if k in self._data:
                    del self._data[k]
                    n += 1
            return n

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "redis.asyncio.from_url", lambda *a, **k: _FakeRedis(store), raising=False
    )
    settings = SimpleNamespace(redis=SimpleNamespace(url="redis://localhost:6379/0"))

    out = await project_services._purge_project_redis_keys(settings, "fanren-bench-v4")

    assert out["error"] is None
    assert out["deleted"] == 3  # only the 3 exact-segment v4 keys
    assert "task:autowrite:heal:fanren-bench-v40:progress" in store  # collision kept
    assert "task:autowrite:heal:shilouyan-bench-v2:progress" in store  # other book kept
    assert "arq:job:run_self_heal_task:1781513130123" in store  # scheduler kept
