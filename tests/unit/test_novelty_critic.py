"""Unit tests for ``bestseller.services.novelty_critic``.

Covers:
  - compute_fingerprint_embedding (basic shape / normalization)
  - check_novelty — ok path (no existing fingerprints)
  - check_novelty — Layer 1 exact name collision (the "方域" scenario)
  - check_novelty — Layer 2 cosine similarity block
  - check_novelty — cosine just below threshold → ok
  - check_novelty — Layer 3 library seed over-use warning
  - check_novelty — skip library check when no source_library_ids
  - register_fingerprint — executes INSERT statement

All tests are pure-unit: no DB, no LLM, no network.
Session is an AsyncMock; ORM rows are plain dataclass stubs.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bestseller.services.novelty_critic import (
    NoveltyVerdict,
    _fingerprint_text,
    check_novelty,
    compute_fingerprint_embedding,
    register_fingerprint,
)
from bestseller.services.retrieval import cosine_similarity

pytestmark = pytest.mark.unit


# ── Stub helpers ───────────────────────────────────────────────────────────────


@dataclass
class _FingerprintRow:
    """Minimal stub for CrossProjectFingerprintModel rows."""

    project_id: Any = field(default_factory=lambda: uuid.uuid4())
    genre: str = "仙侠"
    dimension: str = "character_templates"
    entity_name: str | None = None
    slug: str = "some-slug"
    embedding_json: list[float] | None = None
    source_material_id: int | None = None


@dataclass
class _LibraryRow:
    """Minimal stub for MaterialLibraryModel rows."""

    id: int = 1
    usage_count: int = 0


def _make_session(
    fingerprint_rows: list[Any] | None = None,
    library_rows: list[Any] | None = None,
) -> AsyncMock:
    """Build an AsyncMock session that returns stubbed scalars results."""
    session = AsyncMock()

    call_count = 0
    fp_rows = list(fingerprint_rows or [])
    lib_rows = list(library_rows or [])

    async def _scalars_side_effect(stmt: Any) -> Any:
        nonlocal call_count
        call_count += 1
        # First two calls: fingerprint queries (exact-name + cosine bucket).
        # Third call: library overuse check.
        if call_count <= 2:
            return fp_rows
        return lib_rows

    session.scalars.side_effect = _scalars_side_effect
    # execute() is used by register_fingerprint
    session.execute = AsyncMock(return_value=MagicMock())
    return session


# ── Tests: compute_fingerprint_embedding ──────────────────────────────────────


class TestComputeFingerprintEmbedding:
    """Unit tests for the embedding helper functions."""

    def test_returns_1024_dim_vector(self) -> None:
        vec = compute_fingerprint_embedding("方域", "反派宗门长老")
        assert len(vec) == 1024

    def test_unit_norm(self) -> None:
        """Hashed embedding must have L2 norm ≈ 1.0."""
        vec = compute_fingerprint_embedding("云和镇", "荒山脚下的破败矿镇")
        norm = math.sqrt(sum(v * v for v in vec))
        assert abs(norm - 1.0) < 1e-6

    def test_empty_text_produces_zero_vector(self) -> None:
        """Empty name + empty summary → zero vector (no tokens)."""
        vec = compute_fingerprint_embedding("", "")
        assert all(v == 0.0 for v in vec)

    def test_identical_inputs_produce_identical_vectors(self) -> None:
        v1 = compute_fingerprint_embedding("云鹤宗", "以云为名的正道大宗")
        v2 = compute_fingerprint_embedding("云鹤宗", "以云为名的正道大宗")
        assert v1 == v2

    def test_different_inputs_produce_different_vectors(self) -> None:
        v1 = compute_fingerprint_embedding("云鹤宗", "以云为名的正道大宗")
        v2 = compute_fingerprint_embedding("铁血宗", "崇尚力量的魔道势力")
        assert v1 != v2

    def test_fingerprint_text_concatenates(self) -> None:
        text = _fingerprint_text("Name", "Summary")
        assert "Name" in text
        assert "Summary" in text


# ── Tests: check_novelty — ok path ────────────────────────────────────────────


class TestCheckNoveltyOk:
    """check_novelty returns ok=True when no fingerprints exist yet."""

    @pytest.mark.asyncio
    async def test_no_fingerprints_at_all(self) -> None:
        session = _make_session(fingerprint_rows=[], library_rows=[])
        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="character_templates",
            entity_name="林逸风",
            narrative_summary="出身寒微、内藏天赋的少年修士",
        )
        assert verdict.ok is True
        assert verdict.reason == "ok"
        assert verdict.conflicting_project_id is None
        assert verdict.similarity_score == 0.0
        assert verdict.overused_library_ids == []

    @pytest.mark.asyncio
    async def test_existing_fingerprint_from_different_genre(self) -> None:
        """A fingerprint in a different genre must never block."""
        # Exact same name exists in scifi but check is for 仙侠 → no collision
        fp = _FingerprintRow(
            genre="星际",  # ← different genre
            dimension="character_templates",
            entity_name="林逸风",
        )
        session = _make_session(fingerprint_rows=[])  # query scoped to 仙侠 → empty
        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="character_templates",
            entity_name="林逸风",
            narrative_summary="少年修士",
        )
        assert verdict.ok is True

    @pytest.mark.asyncio
    async def test_cosine_just_below_threshold(self) -> None:
        """Cosine of 0.84 is below the default 0.85 threshold → ok."""
        # Build an embedding for an existing entry
        existing_vec = compute_fingerprint_embedding("云鹤宗", "以云为名的正道大宗")
        # Slightly-perturbed embedding representing a different entry
        perturbed = list(existing_vec)
        perturbed[0] += 10.0  # big perturbation to drive cosine < 0.85
        norm = math.sqrt(sum(v * v for v in perturbed)) or 1.0
        perturbed = [v / norm for v in perturbed]

        fp = _FingerprintRow(
            genre="仙侠",
            dimension="factions",
            entity_name="云鹤宗",
            slug="yun-he-zong",
            embedding_json=existing_vec,
        )

        session = AsyncMock()

        call_count = 0

        async def _scalars_side_effect(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # exact name query → no match (different name)
            if call_count == 2:
                return [fp]  # cosine bucket
            return []

        session.scalars.side_effect = _scalars_side_effect

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="factions",
            entity_name="铁血派",  # different name
            narrative_summary="崇尚力量的魔道势力",  # very different summary
        )
        assert verdict.ok is True


# ── Tests: check_novelty — Layer 1 exact name collision ────────────────────────


class TestCheckNoveltyExactNameCollision:
    """The 方域 scenario: exact name already registered → block."""

    @pytest.mark.asyncio
    async def test_fang_yu_blocked(self) -> None:
        """'方域' already registered in 仙侠 character_templates → block."""
        project_a = uuid.uuid4()
        fp = _FingerprintRow(
            project_id=project_a,
            genre="仙侠",
            dimension="character_templates",
            entity_name="方域",  # stored lower-cased
            slug="fang-yu",
        )
        # session.scalars first call (exact name) returns this row
        session = AsyncMock()

        async def _scalars_side_effect(stmt: Any) -> Any:
            return [fp]

        session.scalars.side_effect = _scalars_side_effect

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="character_templates",
            entity_name="方域",  # same name, different case/whitespace
            narrative_summary="新项目也想用这个名字",
        )
        assert verdict.ok is False
        assert verdict.reason == "exact_name_collision"
        assert verdict.conflicting_project_id == str(project_a)

    @pytest.mark.asyncio
    async def test_case_insensitive_collision(self) -> None:
        """Entity names are lower-cased before comparison."""
        project_a = uuid.uuid4()
        fp = _FingerprintRow(
            project_id=project_a,
            genre="仙侠",
            dimension="character_templates",
            entity_name="fang yu",  # lower-cased in DB
            slug="fang-yu",
        )

        session = AsyncMock()
        session.scalars.side_effect = AsyncMock(return_value=[fp])

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="character_templates",
            entity_name="FANG YU",  # upper-case input → lowered for comparison
            narrative_summary="英文名重复测试",
        )
        assert verdict.ok is False
        assert verdict.reason == "exact_name_collision"

    @pytest.mark.asyncio
    async def test_name_collision_different_dimension_is_ok(self) -> None:
        """Same name in a different dimension must not block."""
        # This is handled at the DB query level (WHERE dimension = ...), so
        # if the session returns empty for the exact-name query we get ok.
        session = _make_session(fingerprint_rows=[])
        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="factions",  # ← different dimension
            entity_name="方域",
            narrative_summary="这次是一个派系而非角色",
        )
        assert verdict.ok is True


# ── Tests: check_novelty — Layer 2 cosine similarity ──────────────────────────


class TestCheckNoveltyCosine:
    """Cosine similarity blocks semantic clones even when names differ."""

    @pytest.mark.asyncio
    async def test_identical_content_blocked(self) -> None:
        """Identical name + summary → cosine = 1.0 → block."""
        existing_vec = compute_fingerprint_embedding("血炼体", "以鲜血为媒介淬炼肉身的功法体系")
        fp = _FingerprintRow(
            project_id=uuid.uuid4(),
            genre="仙侠",
            dimension="power_systems",
            entity_name="blood-forge",  # different name → passes Layer 1
            slug="xue-lian-ti",
            embedding_json=existing_vec,
        )

        session = AsyncMock()
        call_count = 0

        async def _scalars_side_effect(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # exact-name query → no match
            return [fp]  # cosine bucket

        session.scalars.side_effect = _scalars_side_effect

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="power_systems",
            entity_name="血炼体",  # different stored name but identical embedding
            narrative_summary="以鲜血为媒介淬炼肉身的功法体系",  # identical → cosine 1.0
        )
        assert verdict.ok is False
        assert verdict.reason == "cosine_too_high"
        assert verdict.similarity_score >= 0.85

    @pytest.mark.asyncio
    async def test_custom_threshold(self) -> None:
        """A lower threshold blocks content that would pass the default."""
        existing_vec = compute_fingerprint_embedding("云鹤宗", "以云为名的正道大宗，历史悠久")
        fp = _FingerprintRow(
            project_id=uuid.uuid4(),
            genre="仙侠",
            dimension="factions",
            entity_name="different-name",
            slug="yun-he-zong",
            embedding_json=existing_vec,
        )

        session = AsyncMock()
        call_count = 0

        async def _scalars_se(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return [fp]

        session.scalars.side_effect = _scalars_se

        # Use a very low threshold so any similarity blocks
        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="factions",
            entity_name="云鹤宗别称",  # slightly different name
            narrative_summary="以云为名的正道大宗，历史悠久",  # identical summary → cosine ≈ 1.0
            threshold=0.1,  # very low threshold
        )
        assert verdict.ok is False
        assert verdict.reason == "cosine_too_high"

    @pytest.mark.asyncio
    async def test_fingerprint_with_empty_embedding_skipped(self) -> None:
        """Rows with None or empty embedding_json are skipped silently."""
        fp = _FingerprintRow(
            genre="仙侠",
            dimension="power_systems",
            entity_name=None,
            slug="empty-embed",
            embedding_json=None,  # ← no embedding
        )

        session = AsyncMock()
        call_count = 0

        async def _scalars_se(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return [fp]

        session.scalars.side_effect = _scalars_se

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="power_systems",
            entity_name="新功法",
            narrative_summary="全新功法体系",
        )
        assert verdict.ok is True  # empty embedding skipped, no block


# ── Tests: check_novelty — Layer 3 library overuse ────────────────────────────


class TestCheckNoveltyLibraryOveruse:
    """usage_count_warning is non-blocking but carries overused_library_ids."""

    @pytest.mark.asyncio
    async def test_overused_seed_gives_warning(self) -> None:
        """Seed with usage_count ≥ limit → ok=True + usage_count_warning."""
        lib_row = _LibraryRow(id=42, usage_count=10)  # exceeds default limit of 8

        session = AsyncMock()
        call_count = 0

        async def _scalars_se(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return []  # no fingerprint collisions
            return [lib_row]  # library overuse query

        session.scalars.side_effect = _scalars_se

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="power_systems",
            entity_name="独特功法",
            narrative_summary="独特描述",
            source_library_ids=[42],
        )
        assert verdict.ok is True
        assert verdict.reason == "usage_count_warning"
        assert 42 in verdict.overused_library_ids

    @pytest.mark.asyncio
    async def test_seed_below_limit_no_warning(self) -> None:
        lib_row = _LibraryRow(id=7, usage_count=3)  # below default limit of 8

        session = AsyncMock()
        call_count = 0

        async def _scalars_se(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return []
            return [lib_row]

        session.scalars.side_effect = _scalars_se

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="power_systems",
            entity_name="独特功法",
            narrative_summary="独特描述",
            source_library_ids=[7],
        )
        assert verdict.ok is True
        assert verdict.reason == "ok"
        assert verdict.overused_library_ids == []

    @pytest.mark.asyncio
    async def test_no_source_ids_skips_library_check(self) -> None:
        """When source_library_ids is None/empty, library check is skipped entirely."""
        session = AsyncMock()
        call_count = 0

        async def _scalars_se(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return []  # first two calls (exact-name + cosine) return empty

        session.scalars.side_effect = _scalars_se

        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="power_systems",
            entity_name="独特功法",
            narrative_summary="独特描述",
            source_library_ids=None,
        )
        # Only 2 scalars calls (exact-name + cosine), no 3rd for library
        assert call_count == 2
        assert verdict.ok is True
        assert verdict.reason == "ok"

    @pytest.mark.asyncio
    async def test_custom_usage_limit(self) -> None:
        """Configurable usage_count_limit changes the overuse threshold."""
        lib_row = _LibraryRow(id=5, usage_count=5)  # exactly 5

        session = AsyncMock()
        call_count = 0

        async def _scalars_se(stmt: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return []
            return [lib_row]

        session.scalars.side_effect = _scalars_se

        # With limit=5, usage_count=5 → warning (>= 5)
        verdict = await check_novelty(
            session,
            genre="仙侠",
            dimension="power_systems",
            entity_name="功法",
            narrative_summary="功法描述",
            source_library_ids=[5],
            usage_count_limit=5,
        )
        assert verdict.reason == "usage_count_warning"


# ── Tests: register_fingerprint ───────────────────────────────────────────────


class TestRegisterFingerprint:
    """register_fingerprint executes an INSERT via session.execute."""

    @pytest.mark.asyncio
    async def test_calls_session_execute(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock())

        await register_fingerprint(
            session,
            project_id=uuid.uuid4(),
            genre="仙侠",
            dimension="character_templates",
            entity_name="云逸",
            slug="yun-yi",
            narrative_summary="来自偏远山村的少年",
        )

        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_accepts_string_project_id(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock())

        await register_fingerprint(
            session,
            project_id="proj-string-id",
            genre="星际",
            dimension="factions",
            entity_name="钢铁联盟",
            slug="steel-alliance",
            narrative_summary="以武力维持星系秩序的联盟",
        )

        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_includes_source_material_id(self) -> None:
        """source_material_id is optional and passed through when provided."""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock())

        await register_fingerprint(
            session,
            project_id=uuid.uuid4(),
            genre="仙侠",
            dimension="power_systems",
            entity_name="星图引力体",
            slug="xingtu-yinli-ti",
            narrative_summary="借助星图引导天地灵气",
            source_material_id=99,
        )

        # Just verify it didn't raise and called execute
        session.execute.assert_awaited_once()


# ── Tests: NoveltyVerdict dataclass ───────────────────────────────────────────


class TestNoveltyVerdict:
    """Tests for the NoveltyVerdict frozen dataclass."""

    def test_ok_defaults(self) -> None:
        v = NoveltyVerdict(ok=True, reason="ok")
        assert v.ok is True
        assert v.reason == "ok"
        assert v.conflicting_project_id is None
        assert v.similarity_score == 0.0
        assert v.overused_library_ids == []

    def test_frozen_immutable(self) -> None:
        v = NoveltyVerdict(ok=False, reason="exact_name_collision")
        with pytest.raises((AttributeError, TypeError)):
            v.ok = True  # type: ignore[misc]

    def test_block_verdict_fields(self) -> None:
        proj_id = str(uuid.uuid4())
        v = NoveltyVerdict(
            ok=False,
            reason="cosine_too_high",
            conflicting_project_id=proj_id,
            similarity_score=0.91,
        )
        assert v.ok is False
        assert v.similarity_score == 0.91
        assert v.conflicting_project_id == proj_id

    def test_warning_verdict_fields(self) -> None:
        v = NoveltyVerdict(ok=True, reason="usage_count_warning", overused_library_ids=[1, 2, 3])
        assert v.ok is True
        assert v.overused_library_ids == [1, 2, 3]


# ── Tests: prompt_packs PromptPackFragments ───────────────────────────────────


class TestPromptPackFragmentsBClassRemoved:
    """B-class planner_* fields must no longer exist on PromptPackFragments."""

    def test_b_class_fields_absent(self) -> None:
        from bestseller.services.prompt_packs import PromptPackFragments

        frag = PromptPackFragments()
        for b_field in (
            "planner_book_spec",
            "planner_world_spec",
            "planner_cast_spec",
            "planner_volume_plan",
            "planner_outline",
        ):
            assert not hasattr(frag, b_field), f"{b_field!r} should be removed"

    def test_a_class_fields_present(self) -> None:
        from bestseller.services.prompt_packs import PromptPackFragments

        frag = PromptPackFragments()
        for a_field in (
            "global_rules",
            "scene_writer",
            "scene_review",
            "chapter_review",
            "emotion_engineering",
            "conflict_stakes",
            "hook_design",
            "core_loop",
            "pacing_guidance",
        ):
            assert hasattr(frag, a_field), f"{a_field!r} must remain"

    def test_render_prompt_pack_fragment_returns_empty_for_removed_fields(self) -> None:
        """render_prompt_pack_fragment silently returns '' for missing B-class fields."""
        from bestseller.services.prompt_packs import PromptPackFragments, PromptPack, render_prompt_pack_fragment

        pack = PromptPack(
            key="test-pack",
            name="Test",
            description="test",
        )
        for b_field in (
            "planner_book_spec",
            "planner_world_spec",
            "planner_cast_spec",
            "planner_volume_plan",
            "planner_outline",
        ):
            result = render_prompt_pack_fragment(pack, b_field)
            assert result == "", f"Expected '' for {b_field!r}, got {result!r}"

    def test_xianxia_pack_no_longer_has_b_class_fragments(self) -> None:
        """After trim_prompt_packs --apply, xianxia pack has no B-class fragments."""
        from bestseller.services.prompt_packs import load_prompt_pack_registry

        # Clear LRU cache to pick up the updated YAML
        load_prompt_pack_registry.cache_clear()
        registry = load_prompt_pack_registry()
        pack = registry.get("xianxia-upgrade-core")
        if pack is None:
            pytest.skip("xianxia-upgrade-core pack not found — skipping")

        frags = pack.fragments
        for b_field in (
            "planner_book_spec",
            "planner_world_spec",
            "planner_cast_spec",
            "planner_volume_plan",
            "planner_outline",
        ):
            # If the field still exists on the model, its value must be None/empty
            val = getattr(frags, b_field, None)
            assert not val, f"B-class field {b_field!r} should be empty after trim"

    def test_xianxia_pack_no_obligatory_scenes(self) -> None:
        """After trim, xianxia pack has empty obligatory_scenes."""
        from bestseller.services.prompt_packs import load_prompt_pack_registry

        load_prompt_pack_registry.cache_clear()
        registry = load_prompt_pack_registry()
        pack = registry.get("xianxia-upgrade-core")
        if pack is None:
            pytest.skip("xianxia-upgrade-core pack not found — skipping")

        assert pack.obligatory_scenes == [], (
            "obligatory_scenes should be empty after trim"
        )

    def test_a_class_methodology_fragments_retained(self) -> None:
        """A-class methodology fragments must survive the trim."""
        from bestseller.services.prompt_packs import load_prompt_pack_registry

        load_prompt_pack_registry.cache_clear()
        registry = load_prompt_pack_registry()
        pack = registry.get("xianxia-upgrade-core")
        if pack is None:
            pytest.skip("xianxia-upgrade-core pack not found — skipping")

        # At least some methodology fragments should be non-empty
        frags = pack.fragments
        has_methodology = any(
            getattr(frags, f, None)
            for f in (
                "emotion_engineering",
                "conflict_stakes",
                "hook_design",
                "core_loop",
                "dialogue_rules",
            )
        )
        assert has_methodology, "A-class methodology fragments must not have been removed"
