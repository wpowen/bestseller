"""Stored reference projections require the current project-bound receipt.

These checks exercise direct consumers as well as persistence. They are not
PostgreSQL concurrency, model, or literary acceptance checks.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import book_lifecycle_evidence_repair as lifecycle
from bestseller.services import reviews
from bestseller.services.book_creation_readiness_gate import evaluate_book_creation_readiness
from bestseller.services.character_intelligence.optimizer import optimize_project_character_profiles
from bestseller.services.character_intelligence.strategy import (
    character_strategy_from_project_metadata,
)
from bestseller.services.planning_kernel import build_project_planning_kernel
from bestseller.services.prompt_source_policy import stamp_reference_cache, valid_reference_value

pytestmark = pytest.mark.unit
KEYS = ("distilled_strategy_card", "character_strategy", "distilled_strategy_expected")


def card():
    return {
        "aggregate_key": "urban-contemporary",
        "genre_profile_key": "urban-contemporary",
        "source_count": 3,
        "maturity_score": 0.85,
        "maturity_status": "review",
        "provenance_status": "anonymous_aggregate",
        "privacy_status": "redacted",
        "selected_mechanisms": [],
        "required_state_variables": ["ADOPTED_STATE_MARKER"],
        "reader_reward_mix": ["Visible author-selected cooperation"],
    }


def book(**metadata):
    return SimpleNamespace(
        id=uuid4(),
        slug="independent-projection-test",
        title="Shared garden",
        genre="现实生活",
        sub_genre=None,
        language="zh-CN",
        audience="general",
        reader_contract_json={},
        dramatic_question="",
        theme_statement="",
        target_chapters=12,
        target_word_count=30000,
        metadata_json={"premise": "Neighbors restore a garden", **metadata},
    )


def adopted_book():
    project = book(
        distilled_strategy_card=card(),
        distilled_strategy_expected=True,
        character_strategy={
            "source": "author_selected",
            "required_axes": ["agency"],
            "agency_policy": {"choice_with_cost_required": False},
        },
    )
    stamp_reference_cache(project, KEYS, producer="strategy_compiler")
    return project


@pytest.mark.parametrize(
    "key,value",
    [
        (
            "character_strategy",
            {"source": "explicit", "agency_policy": {"must_act_within_chapters": 1}},
        ),
        ("distilled_strategy_card", card()),
    ],
)
def test_metadata_only_does_not_promote_stored_reference(key, value):
    assert character_strategy_from_project_metadata({key: value}) == {}


def test_valid_author_strategy_is_preserved_only_with_project_context():
    project = adopted_book()
    assert character_strategy_from_project_metadata(project.metadata_json) == {}
    result = character_strategy_from_project_metadata(project.metadata_json, project=project)
    assert result["source"] == "author_selected"
    assert result["agency_policy"]["choice_with_cost_required"] is False


@pytest.mark.parametrize(
    "mutation", ["missing", "wrong_project", "edited_value", "changed_intent", "forged_producer"]
)
def test_strategy_rejects_unbound_or_stale_projections(mutation):
    project = adopted_book()
    if mutation == "missing":
        project.metadata_json.pop("prompt_reference_receipts")
    elif mutation == "wrong_project":
        project.id = uuid4()
    elif mutation == "edited_value":
        project.metadata_json["character_strategy"]["agency_policy"]["must_act_within_chapters"] = 1
        project.metadata_json["distilled_strategy_card"]["required_state_variables"] = ["OLD_STATE"]
    elif mutation == "changed_intent":
        project.reader_contract_json = {"core_fantasy": "A new author promise"}
    else:
        for receipt in project.metadata_json["prompt_reference_receipts"].values():
            receipt["producer"] = "model_claimed_author"
    assert character_strategy_from_project_metadata(project.metadata_json, project=project) == {}


def test_verified_card_can_derive_strategy_without_consuming_unsigned_override():
    project = book(
        distilled_strategy_card=card(), character_strategy={"agency_policy": {"old": True}}
    )
    stamp_reference_cache(project, ("distilled_strategy_card",), producer="strategy_compiler")
    result = character_strategy_from_project_metadata(project.metadata_json, project=project)
    assert result["reader_reward_contracts"] == card()["reader_reward_mix"]
    assert "old" not in result["agency_policy"]


def test_metadata_overlay_cannot_reuse_a_different_effective_input_receipt():
    project = adopted_book()
    overlay = {**project.metadata_json, "premise": "A different book intention"}
    assert character_strategy_from_project_metadata(overlay, project=project) == {}


@pytest.mark.parametrize("project_context", [False, True])
@pytest.mark.parametrize(
    "key,value", [("distilled_strategy_card", card()), ("distilled_strategy_expected", True)]
)
def test_kernel_ignores_unadopted_card_and_expectation(project_context, key, value):
    project = book(**{key: value})
    kernel = build_project_planning_kernel(
        project if project_context else None, project_metadata=project.metadata_json
    )
    assert kernel["distilled_strategy"] == {"present": False}


def test_kernel_preserves_current_adopted_reference():
    project = adopted_book()
    kernel = build_project_planning_kernel(project)
    assert kernel["distilled_strategy"]["required_state_variables"] == ["ADOPTED_STATE_MARKER"]


@pytest.mark.parametrize("context", [False, True])
def test_readiness_does_not_turn_legacy_flags_into_new_repair_obligations(context):
    project = book(
        distilled_strategy_expected=True,
        distilled_strategy_card={**card(), "maturity_status": "unsafe"},
    )
    kwargs = {"project": project} if context else {}
    result = evaluate_book_creation_readiness(
        project_slug=project.slug,
        project_metadata=project.metadata_json,
        target_chapters=12,
        planned_chapters=12,
        **kwargs,
    )
    assert not any(f.code.startswith("distilled_strategy_") for f in result.findings)
    assert result.domain_statuses[0].metrics["distilled_strategy_expected"] is False


def test_readiness_checks_a_verified_selected_card():
    project = adopted_book()
    project.metadata_json["distilled_strategy_card"]["maturity_status"] = "unsafe"
    stamp_reference_cache(project, KEYS, producer="strategy_compiler")
    from bestseller.services.character_intelligence.strategy import adopt_reference_projection

    adopt_reference_projection(
        project, "distilled_strategy_card", adopted_by="test-author", required=True
    )
    result = evaluate_book_creation_readiness(
        project_slug=project.slug,
        project_metadata=project.metadata_json,
        target_chapters=12,
        planned_chapters=12,
        project=project,
    )
    assert any(f.code == "distilled_strategy_card_unsafe" for f in result.findings)


@pytest.mark.parametrize("scope", ["scene", "chapter"])
@pytest.mark.parametrize("verified", [False, True])
def test_review_prompts_do_not_reintroduce_unsigned_strategy(scope, verified, monkeypatch):
    project = adopted_book()
    if scope == "chapter":
        # Current chapter review reads explicit creator leaves, independently
        # of optional strategy-cache adoption or the retired 6.0 craft recipe.
        project.metadata_json["creation_intent_contract"] = {
            "creative_direction": "EXPLICIT_CREATOR_PRIORITY_MARKER",
            "field_sources": {"creative_direction": "explicit"},
        }
        stamp_reference_cache(project, KEYS, producer="strategy_compiler")
    if verified:
        from bestseller.services.character_intelligence.strategy import adopt_reference_projection

        adopt_reference_projection(
            project, "distilled_strategy_card", adopted_by="test-author", required=True
        )
    if not verified:
        project.metadata_json.pop("prompt_reference_receipts")
    # Isolate context rendering; use the real strategy gate and critic renderer.
    monkeypatch.setattr(reviews, "_render_chapter_context_section", lambda *a, **kw: "")
    chapter = SimpleNamespace(chapter_number=4, title="Garden", chapter_goal="Cooperate")
    draft = SimpleNamespace(content_md="The neighbors repaired the gate together.")
    if scope == "scene":
        scores = reviews.SceneReviewScores(
            **{k: 0.8 for k, v in reviews.SceneReviewScores.model_fields.items() if v.is_required()}
        )
        result = reviews.SceneReviewResult(
            verdict="pass", severity_max="info", scores=scores, findings=[], evidence_summary={}
        )
        prompts = reviews.build_scene_review_prompts(
            project,
            chapter,
            SimpleNamespace(scene_number=1, title="Gate", purpose={}),
            draft,
            result,
        )
    else:
        scores = reviews.ChapterReviewScores(
            **{
                k: 0.8
                for k, v in reviews.ChapterReviewScores.model_fields.items()
                if v.is_required()
            }
        )
        result = reviews.ChapterReviewResult(
            verdict="pass", severity_max="info", scores=scores, findings=[], evidence_summary={}
        )
        prompts = reviews.build_chapter_review_prompts(
            project, chapter, draft, SimpleNamespace(), result
        )
    assert ("ADOPTED_STATE_MARKER" in "\n".join(prompts)) is (verified and scope == "scene")
    if scope == "chapter":
        assert "EXPLICIT_CREATOR_PRIORITY_MARKER" in prompts[1]
        assert "ADOPTED_STATE_MARKER" not in prompts[0]
        assert "来源优先级：创作者明确约束、已发生的 Canon/Timeline 事实优先" in prompts[0]
        assert "编辑意图，不能当作已经发生的事实" in prompts[0]
        assert "默认模板、方法论数字、既有评分和旧审校建议不能产生创作者义务" in prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_optimizer_does_not_stamp_old_inputs_and_retains_verified_choices(verified):
    project = adopted_book()
    if verified:
        from bestseller.services.character_intelligence.strategy import adopt_reference_projection

        adopt_reference_projection(project, "character_strategy", adopted_by="test-author")
    if not verified:
        project.metadata_json.pop("prompt_reference_receipts")
    before = deepcopy(project.metadata_json)

    class Session:
        async def scalars(self, _stmt):
            return []

        async def flush(self):
            pass

    result = await optimize_project_character_profiles(Session(), project)
    assert (result["profiles_without_strategy"] == 1) is (not verified)
    assert project.metadata_json == before


def test_lifecycle_does_not_rebadge_legacy_card(monkeypatch):
    project = book(distilled_strategy_card=card())
    monkeypatch.setattr(lifecycle, "compile_distilled_strategy_card", lambda **kw: None)
    assert (
        lifecycle._distilled_strategy_payload(
            project,
            project.metadata_json,
            category_key="urban-contemporary",
            protagonist_name="Independent lead",
        )
        is None
    )


def test_lifecycle_preserves_valid_author_card():
    project = adopted_book()
    assert (
        lifecycle._distilled_strategy_payload(
            project,
            project.metadata_json,
            category_key="urban-contemporary",
            protagonist_name="Independent lead",
        )
        == card()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_lifecycle_new_card_uses_registered_receipt_without_dry_run_mutation(
    monkeypatch, dry_run
):
    project = book(category_key="urban-contemporary")
    before = deepcopy(project.metadata_json)

    async def rows(*_args):
        return [], [], [], [], [], []

    monkeypatch.setattr(lifecycle, "_load_rows", rows)
    from bestseller.services.distilled_strategy_compiler import DistilledStrategyCard

    monkeypatch.setattr(
        lifecycle,
        "compile_distilled_strategy_card",
        lambda **kw: DistilledStrategyCard.model_validate(card()),
    )

    class Session:
        def add(self, _row):
            pass

        async def flush(self):
            pass

    await lifecycle.repair_book_lifecycle_evidence(Session(), project, dry_run=dry_run)
    if dry_run:
        assert project.metadata_json == before
    else:
        assert (
            valid_reference_value(project, "distilled_strategy_card")["required_state_variables"]
            == card()["required_state_variables"]
        )
        assert valid_reference_value(project, "distilled_strategy_expected") is True


@pytest.mark.parametrize("disabled", [False, True])
def test_verified_optional_reference_never_becomes_required(disabled, monkeypatch):
    from bestseller.services.planning_kernel import (
        build_prewrite_repair_directives,
        evaluate_prewrite_readiness,
    )

    project = adopted_book()
    if disabled:
        monkeypatch.setenv("BESTSELLER__PIPELINE__ENABLE_DISTILLED_DESIGN_REFERENCE", "false")
    kernel = build_project_planning_kernel(project)
    report = evaluate_prewrite_readiness(kernel)
    assert not any(f.code.startswith("distilled_") for f in report.blocking_findings)
    assert not any(f.code.startswith("distilled_") for f in report.warnings)
    baseline = evaluate_prewrite_readiness(
        build_project_planning_kernel(
            project,
            project_metadata={
                "distilled_strategy_card": None,
                "distilled_strategy_expected": False,
            },
        )
    )
    assert report.score == baseline.score
    assert build_prewrite_repair_directives(report) == build_prewrite_repair_directives(baseline)
    ready = evaluate_book_creation_readiness(
        project_slug=project.slug,
        project_metadata=project.metadata_json,
        target_chapters=12,
        planned_chapters=12,
        project=project,
    )
    assert not any(f.code.startswith("distilled_strategy_") for f in ready.findings)
    assert ready.domain_statuses[0].metrics["distilled_strategy_expected"] is False


@pytest.mark.parametrize("failure", ["changed_intent", "missing_card", "disabled"])
def test_author_required_source_failure_is_reported_instead_of_recompiled(monkeypatch, failure):
    from bestseller.services.character_intelligence import strategy

    project = adopted_book()
    strategy.adopt_reference_projection(
        project, "distilled_strategy_card", adopted_by="test-author", required=True
    )
    if failure == "changed_intent":
        project.reader_contract_json = {"core_fantasy": "Changed author intention"}
    elif failure == "missing_card":
        project.metadata_json.pop("distilled_strategy_card")
    else:
        monkeypatch.setenv("BESTSELLER__PIPELINE__ENABLE_DISTILLED_DESIGN_REFERENCE", "false")
        from bestseller.settings import reset_settings_cache

        reset_settings_cache()
    ready = evaluate_book_creation_readiness(
        project_slug=project.slug,
        project_metadata=project.metadata_json,
        target_chapters=12,
        planned_chapters=12,
        project=project,
    )
    assert any(
        f.code == "adopted_reference_unavailable" and f.severity == "critical"
        for f in ready.findings
    )
    kernel = build_project_planning_kernel(project)
    assert "ADOPTED_REFERENCE_UNAVAILABLE" in kernel["distilled_strategy"]["issue_codes"]
    with pytest.raises(strategy.AdoptedReferenceUnavailable):
        character_strategy_from_project_metadata(project.metadata_json, project=project)
    with pytest.raises(strategy.AdoptedReferenceUnavailable):
        lifecycle._distilled_strategy_payload(
            project,
            project.metadata_json,
            category_key="urban-contemporary",
            protagonist_name="Independent lead",
        )


@pytest.mark.asyncio
async def test_fresh_optional_card_does_not_force_story_state_and_preserves_legacy_archive(
    monkeypatch,
):
    project = book(category_key="urban-contemporary", distilled_strategy_card={"old": "ARCHIVE_ME"})

    async def rows(*args):
        return [], [], [], [], [], []

    monkeypatch.setattr(lifecycle, "_load_rows", rows)
    from bestseller.services.distilled_strategy_compiler import DistilledStrategyCard

    monkeypatch.setattr(
        lifecycle,
        "compile_distilled_strategy_card",
        lambda **kw: DistilledStrategyCard.model_validate(card()),
    )

    class Session:
        def add(self, _row):
            pass

        async def flush(self):
            pass

    await lifecycle.repair_book_lifecycle_evidence(Session(), project)
    assert "ADOPTED_STATE_MARKER" not in str(project.metadata_json["story_design_kernel"])
    assert "ARCHIVE_ME" in str(project.metadata_json.get("superseded_prompt_sources"))


@pytest.fixture
async def projection_db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import Session
    from sqlalchemy.schema import CreateTable

    from bestseller.infra.db.models import (
        CharacterModel,
        CharacterStateSnapshotModel,
        ProjectModel,
        RelationshipModel,
        WorldRuleModel,
    )

    @compiles(JSONB, "sqlite")
    def sqlite_jsonb(_type, _compiler, **kw):
        return "JSON"

    path = tmp_path / "reference_projection_test.sqlite3"
    assert "test" in path.name
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for model in (
            ProjectModel,
            CharacterModel,
            CharacterStateSnapshotModel,
            RelationshipModel,
            WorldRuleModel,
        ):
            # Portable test DDL only; production types and migrations are untouched.
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect)).replace(
                "::jsonb", ""
            )
            connection.exec_driver_sql(ddl)

    class SQLiteSession:
        def __init__(self):
            self.orm = Session(engine, expire_on_commit=False)

        @property
        def no_autoflush(self):
            return self.orm.no_autoflush

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.orm.close()

        def add(self, row):
            self.orm.add(row)

        def add_all(self, rows):
            self.orm.add_all(rows)

        async def get(self, model, key):
            return self.orm.get(model, key)

        async def scalars(self, stmt):
            return self.orm.scalars(stmt)

        async def scalar(self, stmt):
            return self.orm.scalar(stmt)

        async def flush(self):
            self.orm.flush()

        async def commit(self):
            self.orm.commit()

    yield SQLiteSession
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["character_strategy", "distilled_strategy_card"])
async def test_sqlite_author_choice_roundtrip_derivation_and_idempotence(projection_db, source):
    from bestseller.infra.db.models import CharacterModel, ProjectModel
    from bestseller.services.character_intelligence.strategy import adopt_reference_projection

    p = adopted_book()
    p.metadata_json.pop("prompt_reference_receipts")
    if source == "distilled_strategy_card":
        p.metadata_json["character_strategy"] = {"agency_policy": {"old_unknown": True}}
    row = ProjectModel(
        id=p.id,
        slug=p.slug,
        title=p.title,
        genre=p.genre,
        sub_genre=p.sub_genre,
        language=p.language,
        audience=p.audience,
        reader_contract_json=p.reader_contract_json,
        dramatic_question=p.dramatic_question,
        theme_statement=p.theme_statement,
        target_chapters=p.target_chapters,
        target_word_count=p.target_word_count,
        metadata_json=p.metadata_json,
    )
    stamp_reference_cache(row, (source,), producer="strategy_compiler")
    adopt_reference_projection(row, source, adopted_by="fixture-author")
    actor = CharacterModel(
        id=uuid4(),
        project_id=row.id,
        name="Unrelated author name",
        role="protagonist",
        goal="Cooperate",
        voice_profile_json={"signature_words": ["AUTHOR_VOICE"]},
        metadata_json={
            "character_engine_profile": {"source": "author", "display_name": "AUTHOR_IDENTITY"}
        },
    )
    async with projection_db() as session:
        session.add_all([row, actor])
        await session.commit()
    async with projection_db() as session:
        project = await session.get(ProjectModel, row.id)
        result = await optimize_project_character_profiles(session, project)
        assert result["profiles_optimized"] == 1
        assert valid_reference_value(project, "character_strategy")
        await session.commit()
    async with projection_db() as session:
        project = await session.get(ProjectModel, row.id)
        restored_actor = await session.get(CharacterModel, actor.id)
        before = deepcopy(restored_actor.metadata_json)
        assert before["character_engine_profile_legacy"]["display_name"] == "AUTHOR_IDENTITY"
        assert "AUTHOR_VOICE" in str(restored_actor.voice_profile_json)
        result = await optimize_project_character_profiles(session, project)
        assert result["projects_skipped_current"] == 1
        assert restored_actor.metadata_json == before
        if source == "distilled_strategy_card":
            assert "old_unknown" in str(project.metadata_json["superseded_prompt_sources"])


@pytest.mark.asyncio
async def test_new_author_choice_rebuilds_profiles_instead_of_skipping_old_version(projection_db):
    from bestseller.infra.db.models import CharacterModel, ProjectModel
    from bestseller.services.character_intelligence.strategy import adopt_reference_projection

    p = adopted_book()
    project = ProjectModel(
        id=p.id,
        slug=p.slug,
        title=p.title,
        genre=p.genre,
        language=p.language,
        audience=p.audience,
        reader_contract_json={},
        dramatic_question="",
        theme_statement="",
        target_chapters=12,
        target_word_count=30000,
        metadata_json=p.metadata_json,
    )
    stamp_reference_cache(project, KEYS, producer="strategy_compiler")
    adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    async with projection_db() as session:
        session.add(project)
        actor = CharacterModel(
            id=uuid4(),
            project_id=project.id,
            name="A different name",
            role="protagonist",
            metadata_json={},
        )
        session.add(actor)
        await session.commit()
        assert (await optimize_project_character_profiles(session, project))[
            "profiles_optimized"
        ] == 1
        old_profile = deepcopy(actor.metadata_json["character_engine_profile"])
        updated = deepcopy(project.metadata_json)
        updated["character_strategy"]["agency_policy"]["choice_with_cost_required"] = True
        project.metadata_json = updated
        stamp_reference_cache(project, ("character_strategy",), producer="strategy_compiler")
        adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
        result = await optimize_project_character_profiles(session, project)
        assert result["profiles_optimized"] == 1
        assert actor.metadata_json["character_engine_profile"] != old_profile


def test_required_adoption_without_project_requests_context_without_authorizing_content():
    from bestseller.services.character_intelligence import strategy

    project = adopted_book()
    strategy.adopt_reference_projection(
        project,
        "distilled_strategy_card",
        adopted_by="fixture-author",
        required=True,
    )
    result = evaluate_book_creation_readiness(
        project_slug=project.slug,
        project_metadata=project.metadata_json,
        target_chapters=12,
        planned_chapters=12,
    )
    assert any(f.code == "reference_context_required" for f in result.findings)
    kernel = build_project_planning_kernel(project_metadata=project.metadata_json)
    assert "REFERENCE_CONTEXT_REQUIRED" in kernel["distilled_strategy"]["issue_codes"]
    with pytest.raises(strategy.ReferenceContextRequiredError):
        character_strategy_from_project_metadata(project.metadata_json)


@pytest.mark.asyncio
async def test_old_adoption_cannot_authorize_a_new_different_optional_projection():
    from bestseller.services.character_intelligence import strategy

    project = adopted_book()
    strategy.adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    project.metadata_json["character_strategy"] = {"agency_policy": {"new_generated_event": True}}
    stamp_reference_cache(project, ("character_strategy",), producer="strategy_compiler")

    class Session:
        async def scalars(self, _stmt):
            return []

        async def flush(self):
            pass

    result = await optimize_project_character_profiles(Session(), project)
    assert result["profiles_without_strategy"] == 1


@pytest.mark.asyncio
async def test_adopted_card_does_not_authorize_unrelated_registered_character_strategy():
    from bestseller.services.character_intelligence import strategy

    project = adopted_book()
    strategy.adopt_reference_projection(
        project, "distilled_strategy_card", adopted_by="fixture-author"
    )
    project.metadata_json["character_strategy"] = {"agency_policy": {"unselected_event": True}}
    stamp_reference_cache(project, ("character_strategy",), producer="strategy_compiler")

    class Session:
        async def scalars(self, _stmt):
            return []

        async def flush(self):
            pass

    await optimize_project_character_profiles(Session(), project)
    value = valid_reference_value(project, "character_strategy")
    assert "unselected_event" not in str(value)
    assert value["reader_reward_contracts"] == card()["reader_reward_mix"]


@pytest.mark.asyncio
async def test_lifecycle_recompiling_optional_card_preserves_independent_author_strategy(
    monkeypatch,
):
    from bestseller.services.character_intelligence import strategy
    from bestseller.services.distilled_strategy_compiler import DistilledStrategyCard

    project = adopted_book()
    project.metadata_json.pop("distilled_strategy_card")
    strategy.adopt_reference_projection(
        project, "character_strategy", adopted_by="fixture-author", required=True
    )
    expected = deepcopy(project.metadata_json["character_strategy"])

    async def rows(*args):
        return [], [], [], [], [], []

    monkeypatch.setattr(lifecycle, "_load_rows", rows)
    monkeypatch.setattr(
        lifecycle,
        "compile_distilled_strategy_card",
        lambda **kw: DistilledStrategyCard.model_validate(card()),
    )
    build_book = lifecycle.build_lifecycle_book_spec

    def unchanged_hook(*args, **kwargs):
        result = build_book(*args, **kwargs)
        result["unique_hook"] = None
        return result

    monkeypatch.setattr(lifecycle, "build_lifecycle_book_spec", unchanged_hook)

    class Session:
        def add(self, _row):
            pass

        async def flush(self):
            pass

    await lifecycle.repair_book_lifecycle_evidence(Session(), project)
    assert valid_reference_value(project, "character_strategy") == expected


def profile_actor(project):
    from bestseller.infra.db.models import CharacterModel

    return CharacterModel(
        id=uuid4(),
        project_id=project.id,
        name="Independent gardener",
        role="protagonist",
        goal="Restore the garden",
        fear="Losing cooperation",
        metadata_json={},
        voice_profile_json={"verbal_tics": ["AUTHOR_PROFILE_VOICE"]},
    )


def test_legacy_profile_cannot_enter_writer_without_project_receipt():
    from bestseller.services.drafts import _render_story_bible_section

    context = {
        "participants": [
            {
                "name": "Independent gardener",
                "character_engine_profile": {
                    "display_name": "Independent gardener",
                    "voice_dna": {"signature_words": ["LEGACY_PROFILE_MARKER"]},
                },
            }
        ]
    }
    assert "LEGACY_PROFILE_MARKER" not in _render_story_bible_section(context, language="zh-CN")


@pytest.mark.parametrize(
    "mutation", [None, "profile", "project", "cast", "strategy", "receipt", "character_id"]
)
def test_profile_receipt_binds_character_body_and_current_sources(mutation):
    from bestseller.services.character_intelligence import optimizer, strategy

    project = adopted_book()
    strategy.adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    actor = profile_actor(project)
    chosen = strategy.character_strategy_from_project_metadata(
        project.metadata_json, project=project
    )
    profile = optimizer.build_optimized_character_profile(actor, character_strategy=chosen)
    optimizer.record_character_engine_profile(project, actor, profile, strategy=chosen)
    character_id = actor.id
    if mutation == "profile":
        profile = {**profile, "display_name": "Changed without adoption"}
    elif mutation == "project":
        project.id = uuid4()
    elif mutation == "cast":
        project.metadata_json["cast_spec"] = {"protagonist": {"name": "Different author"}}
    elif mutation == "strategy":
        project.metadata_json["character_strategy"] = {
            "agency_policy": {"new_unadopted_event": True}
        }
        stamp_reference_cache(project, ("character_strategy",), producer="strategy_compiler")
    elif mutation == "receipt":
        project.metadata_json["prompt_reference_receipts"].pop("character_engine_profiles")
    elif mutation == "character_id":
        character_id = uuid4()
    value = optimizer.validated_character_engine_profile(project, character_id, profile)
    assert (value is not None) is (mutation is None)


def test_authorized_profile_reaches_writer_and_optional_raw_profile_does_not():
    from bestseller.services.character_intelligence import optimizer, strategy
    from bestseller.services.drafts import _render_story_bible_section

    project = adopted_book()
    strategy.adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    actor = profile_actor(project)
    chosen = strategy.character_strategy_from_project_metadata(
        project.metadata_json, project=project
    )
    profile = optimizer.build_optimized_character_profile(actor, character_strategy=chosen)
    optimizer.record_character_engine_profile(project, actor, profile, strategy=chosen)
    context = {
        "participants": [
            {"name": actor.name, "character_id": str(actor.id), "character_engine_profile": profile}
        ]
    }
    assert "AUTHOR_PROFILE_VOICE" in _render_story_bible_section(
        context, language="zh-CN", project=project
    )
    assert "AUTHOR_PROFILE_VOICE" not in _render_story_bible_section(context, language="zh-CN")


@pytest.mark.asyncio
async def test_optimizer_preserves_old_profile_and_emits_current_profile_receipt():
    from bestseller.services.character_intelligence import optimizer, strategy

    project = adopted_book()
    strategy.adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    actor = profile_actor(project)
    actor.metadata_json = {"character_engine_profile": {"old": "UNVERIFIED_PROFILE"}}

    class Session:
        async def scalars(self, _stmt):
            return [actor]

        async def flush(self):
            pass

    await optimizer.optimize_project_character_profiles(Session(), project)
    profile = actor.metadata_json["character_engine_profile"]
    assert optimizer.validated_character_engine_profile(project, actor.id, profile) == profile
    assert "UNVERIFIED_PROFILE" in str(actor.metadata_json["superseded_character_engine_profiles"])
    assert "AUTHOR_PROFILE_VOICE" in str(profile)


@pytest.mark.asyncio
@pytest.mark.parametrize("adopted", [False, True])
async def test_sqlite_cast_profile_reader_writer_roundtrip(projection_db, monkeypatch, adopted):
    from bestseller.infra.db.models import CharacterModel, ProjectModel
    from bestseller.services import (
        character_evolution,
        interpersonal_promises,
        memory_recall,
        story_bible,
    )
    from bestseller.services.character_intelligence import optimizer, strategy
    from bestseller.services.drafts import _render_story_bible_section

    p = adopted_book()
    project = ProjectModel(
        id=p.id,
        slug=p.slug,
        title=p.title,
        genre=p.genre,
        language=p.language,
        audience=p.audience,
        reader_contract_json={},
        dramatic_question="",
        theme_statement="",
        target_chapters=12,
        target_word_count=30000,
        metadata_json=p.metadata_json,
    )
    stamp_reference_cache(project, KEYS, producer="strategy_compiler")
    if adopted:
        strategy.adopt_reference_projection(
            project, "character_strategy", adopted_by="fixture-author"
        )

    async def no_novelty(*args, **kwargs):
        return 0

    async def no_world(*args, **kwargs):
        return {}

    async def no_ancillary(*args, **kwargs):
        return []

    # Only unrelated world/novelty/optional diagnostics are stubbed. Cast writes,
    # snapshots, source receipts, reader queries and writer assembly remain real.
    monkeypatch.setattr(story_bible, "_run_character_novelty_check", no_novelty)
    monkeypatch.setattr(story_bible, "load_world_expansion_context", no_world)
    monkeypatch.setattr(character_evolution, "detect_character_stagnation", no_ancillary)
    monkeypatch.setattr(interpersonal_promises, "active_promises_for_chapter", no_ancillary)
    monkeypatch.setattr(memory_recall, "compute_memory_recall_cues", no_ancillary)
    cast = {
        "protagonist": {
            "name": "Garden keeper",
            "goal": "AUTHOR_GOAL",
            "fear": "Lost cooperation",
            "flaw": "Avoiding disagreement",
            "strength": "Patience",
            "background": "A local gardener",
            "secret": "Keeps old seeds",
            "arc_trajectory": "Learn to ask for help",
            "voice_profile": {"verbal_tics": ["AUTHOR_ROUNDTRIP_VOICE"]},
        }
    }
    actor_id = story_bible.stable_character_id(project.id, "Garden keeper")
    async with projection_db() as session:
        session.add(project)
        await session.commit()
        first = await story_bible.upsert_cast_spec(session, project, cast)
        assert first["state_snapshots_created"] == 1
        await session.commit()
    async with projection_db() as session:
        project = await session.get(ProjectModel, p.id)
        actor = await session.get(CharacterModel, actor_id)
        profile = deepcopy(actor.metadata_json["character_engine_profile"])
        assert (
            optimizer.validated_character_engine_profile(
                project, actor.id, profile, character=actor
            )
            == profile
        )
        assert bool(profile.get("strategy_source")) is adopted
        catalog = deepcopy(project.metadata_json["character_engine_profiles"])
        second = await story_bible.upsert_cast_spec(session, project, cast)
        assert second["state_snapshots_created"] == 0
        assert project.metadata_json["character_engine_profiles"] == catalog
        assert actor.metadata_json["character_engine_profile"] == profile
        await session.commit()
    async with projection_db() as session:
        project = await session.get(ProjectModel, p.id)
        chapter = SimpleNamespace(volume_id=None, chapter_number=1)
        scene = SimpleNamespace(scene_number=1, participants=["Garden keeper"])
        context = await story_bible.load_scene_story_bible_context(
            session, project=project, chapter=chapter, scene=scene
        )
        assert context["participants"][0]["character_engine_profile"] == profile
        prompt = _render_story_bible_section(context, language="zh-CN", project=project)
        assert "AUTHOR_ROUNDTRIP_VOICE" in prompt
        # Historical packet and row data lose authority when their receipt goes
        # stale; author goal/voice remain available through their own fields.
        metadata = deepcopy(project.metadata_json)
        metadata["prompt_reference_receipts"].pop("character_engine_profiles")
        project.metadata_json = metadata
        assert "AUTHOR_ROUNDTRIP_VOICE" not in _render_story_bible_section(
            {
                "participants": [
                    {
                        "name": actor.name,
                        "character_id": str(actor.id),
                        "character_engine_profile": profile,
                    }
                ]
            },
            language="zh-CN",
            project=project,
        )
        context = await story_bible.load_scene_story_bible_context(
            session, project=project, chapter=chapter, scene=scene
        )
        assert context["participants"][0]["character_engine_profile"] is None
        assert context["participants"][0]["goal"] == "AUTHOR_GOAL"
        assert "AUTHOR_ROUNDTRIP_VOICE" in _render_story_bible_section(
            context, language="zh-CN", project=project
        )


@pytest.mark.asyncio
async def test_current_optimizer_marker_cannot_bless_missing_profile_receipt():
    from bestseller.services.character_intelligence import optimizer, strategy

    project = adopted_book()
    strategy.adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    actor = profile_actor(project)

    class Session:
        async def scalars(self, _stmt):
            return [actor]

        async def flush(self):
            pass

    session = Session()
    await optimizer.optimize_project_character_profiles(session, project)
    project.metadata_json["prompt_reference_receipts"].pop("character_engine_profiles")
    result = await optimizer.optimize_project_character_profiles(session, project)
    assert result["profiles_optimized"] == 1
    assert result["projects_skipped_current"] == 0
    assert optimizer.validated_character_engine_profile(
        project, actor.id, actor.metadata_json["character_engine_profile"]
    )


def test_disabled_profiles_and_changed_character_input_are_optional(monkeypatch):
    from bestseller.services.character_intelligence import optimizer, strategy
    from bestseller.settings import get_settings

    project = adopted_book()
    strategy.adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    actor = profile_actor(project)
    actor.metadata_json = {
        "cast_entry": {"goal": actor.goal, "voice_profile": deepcopy(actor.voice_profile_json)}
    }
    chosen = strategy.adopted_character_strategy(project)
    profile = optimizer.build_optimized_character_profile(actor, character_strategy=chosen)
    optimizer.record_character_engine_profile(project, actor, profile, strategy=chosen)
    actor.voice_profile_json = {"verbal_tics": ["NEW_AUTHOR_VOICE"]}
    assert (
        optimizer.validated_character_engine_profile(project, actor.id, profile, character=actor)
        is None
    )
    monkeypatch.setattr(get_settings().pipeline, "enable_distilled_design_reference", False)
    assert optimizer.validated_character_engine_profile(project, actor.id, profile) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cleared", [False, True])
async def test_current_character_facts_override_stale_cast_entry_during_rebuild(cleared):
    from bestseller.services.character_intelligence import optimizer, strategy

    project = adopted_book()
    strategy.adopt_reference_projection(project, "character_strategy", adopted_by="fixture-author")
    actor = profile_actor(project)
    actor.goal = "OLD_PROFILE_GOAL"
    actor.voice_profile_json = {"verbal_tics": ["OLD_PROFILE_VOICE"]}
    actor.metadata_json = {
        "cast_entry": {
            "goal": actor.goal,
            "voice_profile": deepcopy(actor.voice_profile_json),
        }
    }

    class Session:
        async def scalars(self, _stmt):
            return [actor]

        async def flush(self):
            pass

    session = Session()
    await optimizer.optimize_project_character_profiles(session, project)
    previous = deepcopy(actor.metadata_json["character_engine_profile"])
    actor.goal = None if cleared else "CURRENT_AUTHOR_GOAL"
    actor.voice_profile_json = {} if cleared else {"verbal_tics": ["CURRENT_AUTHOR_VOICE"]}
    assert (
        optimizer.validated_character_engine_profile(project, actor.id, previous, character=actor)
        is None
    )
    assert (await optimizer.optimize_project_character_profiles(session, project))[
        "profiles_optimized"
    ] == 1
    profile = actor.metadata_json["character_engine_profile"]
    assert "OLD_PROFILE_GOAL" not in str(profile)
    assert "OLD_PROFILE_VOICE" not in str(profile)
    if not cleared:
        assert "CURRENT_AUTHOR_GOAL" in str(profile)
        assert "CURRENT_AUTHOR_VOICE" in str(profile)
    assert (
        optimizer.validated_character_engine_profile(project, actor.id, profile, character=actor)
        == profile
    )
    assert previous in actor.metadata_json["superseded_character_engine_profiles"].values()
    assert (await optimizer.optimize_project_character_profiles(session, project))[
        "projects_skipped_current"
    ] == 1
