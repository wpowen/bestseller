"""Unit tests for Batch 2 material forge modules.

Covers:
  - bestseller.services.material_forge.base
  - bestseller.services.material_forge (forge_all_materials)
  - bestseller.services.material_reference
"""

from __future__ import annotations

import json
import pytest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

from bestseller.services.material_forge.base import (
    BaseForge,
    ProjectMaterial,
    ForgeResult,
    _coerce_emit_args,
    _slug_valid,
    insert_project_material,
)
from bestseller.services.material_forge import forge_all_materials
from bestseller.services.material_reference import (
    render_material_reference_block,
    parse_material_refs,
    list_project_materials,
)

pytestmark = pytest.mark.unit


# ── Stub helpers ────────────────────────────────────────────────────────────


class _StubRow:
    """Minimal ORM-row stub carrying arbitrary attributes."""

    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class _ScalarResult:
    """Mimics sqlalchemy ScalarResult with .scalars().all() chaining."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items

    def scalars(self) -> "_ScalarResult":
        return self


def _make_session_returning(rows: list[Any]) -> AsyncMock:
    """Return an async session whose execute() yields *rows* via .scalars().all()."""
    session = AsyncMock()
    result = _ScalarResult(rows)
    session.execute.return_value = result
    return session


def _make_project_material(
    slug: str = "test-slug",
    name: str = "测试名称",
    material_type: str = "world_settings",
    project_id: str = "proj-123",
) -> ProjectMaterial:
    return ProjectMaterial(
        project_id=project_id,
        material_type=material_type,
        slug=slug,
        name=name,
        narrative_summary="一段简短的概括文字。",
        content_json={"key": "value"},
    )


# ── TestProjectMaterialDataclass ────────────────────────────────────────────


class TestProjectMaterialDataclass:
    """Tests for ProjectMaterial frozen dataclass and ForgeResult."""

    def test_frozen(self) -> None:
        """ProjectMaterial is immutable — attribute assignment raises AttributeError."""
        mat = _make_project_material()
        with pytest.raises(AttributeError):
            mat.slug = "new-slug"  # type: ignore[misc]

    def test_forge_result_emitted_count(self) -> None:
        """ForgeResult.emitted_count equals len(emitted)."""
        mats = tuple(_make_project_material(slug=f"slug-{i}") for i in range(3))
        result = ForgeResult(
            project_id="proj-123",
            dimension="world_settings",
            emitted=mats,
            rounds=2,
            exit_reason="text",
        )
        assert result.emitted_count == 3

    def test_coerce_emit_args_valid(self) -> None:
        """A valid payload produces a ProjectMaterial with all expected fields."""
        raw = {
            "slug": "yunhe-town",
            "name": "云和镇",
            "narrative_summary": "荒山脚下的破败矿镇。",
            "content_json": {"atmosphere": "gloomy"},
            "variation_notes": "与库中风格迥异",
            "source_library_ids": [1, 2, 3],
        }
        mat = _coerce_emit_args(raw, project_id="proj-abc", dimension="locale_templates")
        assert mat.project_id == "proj-abc"
        assert mat.material_type == "locale_templates"
        assert mat.slug == "yunhe-town"
        assert mat.name == "云和镇"
        assert mat.narrative_summary == "荒山脚下的破败矿镇。"
        assert mat.content_json == {"atmosphere": "gloomy"}
        assert mat.source_library_ids == [1, 2, 3]
        assert mat.variation_notes == "与库中风格迥异"


# ── TestCoerceEmitArgs ───────────────────────────────────────────────────────


class TestCoerceEmitArgs:
    """Validation behaviour of _coerce_emit_args."""

    def _base(self) -> dict[str, Any]:
        return {
            "slug": "valid-slug",
            "name": "有效名称",
            "narrative_summary": "有效摘要。",
            "content_json": {"x": 1},
        }

    def test_invalid_slug_uppercase(self) -> None:
        # Note: _coerce_emit_args lower-cases the slug before validation,
        # so we need a slug that is invalid AFTER lowercasing (e.g. has underscore or space)
        raw = self._base()
        raw["slug"] = "has_underscore"
        with pytest.raises(ValueError, match="invalid_slug"):
            _coerce_emit_args(raw, project_id="p", dimension="power_systems")

    def test_invalid_slug_uppercase_before_lower(self) -> None:
        """Slugs with only uppercase chars are lowercased first; test truly invalid slug."""
        raw = self._base()
        raw["slug"] = "bad slug with spaces"
        with pytest.raises(ValueError, match="invalid_slug"):
            _coerce_emit_args(raw, project_id="p", dimension="power_systems")

    def test_invalid_slug_empty(self) -> None:
        raw = self._base()
        raw["slug"] = ""
        with pytest.raises(ValueError, match="invalid_slug"):
            _coerce_emit_args(raw, project_id="p", dimension="world_settings")

    def test_missing_name(self) -> None:
        raw = self._base()
        raw["name"] = ""
        with pytest.raises(ValueError, match="missing_name"):
            _coerce_emit_args(raw, project_id="p", dimension="world_settings")

    def test_missing_summary(self) -> None:
        raw = self._base()
        raw["narrative_summary"] = ""
        with pytest.raises(ValueError, match="missing_narrative_summary"):
            _coerce_emit_args(raw, project_id="p", dimension="world_settings")

    def test_missing_content_json(self) -> None:
        raw = self._base()
        del raw["content_json"]
        with pytest.raises(ValueError, match="missing_content_json"):
            _coerce_emit_args(raw, project_id="p", dimension="world_settings")

    def test_content_json_string_parsed(self) -> None:
        """A JSON-encoded string for content_json is parsed into a dict."""
        raw = self._base()
        raw["content_json"] = json.dumps({"parsed": True})
        mat = _coerce_emit_args(raw, project_id="p", dimension="world_settings")
        assert mat.content_json == {"parsed": True}

    def test_dimension_override(self) -> None:
        """dimension key in raw args overrides the default dimension parameter."""
        raw = self._base()
        raw["dimension"] = "power_systems"
        mat = _coerce_emit_args(raw, project_id="p", dimension="world_settings")
        assert mat.material_type == "power_systems"

    def test_source_ids_coerced(self) -> None:
        """source_library_ids is coerced to list[int]; non-list value becomes []."""
        raw = self._base()
        raw["source_library_ids"] = [1, 2.0, "3"]
        mat = _coerce_emit_args(raw, project_id="p", dimension="world_settings")
        assert mat.source_library_ids == [1, 2, 3]

    def test_source_ids_non_list_becomes_empty(self) -> None:
        """Non-list source_library_ids is silently replaced with []."""
        raw = self._base()
        raw["source_library_ids"] = "not-a-list"
        mat = _coerce_emit_args(raw, project_id="p", dimension="world_settings")
        assert mat.source_library_ids == []


# ── TestSlugValid ────────────────────────────────────────────────────────────


class TestSlugValid:
    """_slug_valid accepts only lowercase kebab-case strings."""

    @pytest.mark.parametrize(
        "slug",
        [
            "simple",
            "kebab-case",
            "with-numbers-123",
            "a",
            "abc123",
            "1starts-with-digit",
        ],
    )
    def test_valid_slugs(self, slug: str) -> None:
        assert _slug_valid(slug) is True

    def test_invalid_uppercase(self) -> None:
        assert _slug_valid("PowerSystem") is False

    def test_invalid_empty(self) -> None:
        assert _slug_valid("") is False

    def test_invalid_spaces(self) -> None:
        assert _slug_valid("has space") is False

    def test_invalid_too_long(self) -> None:
        assert _slug_valid("a" * 161) is False

    def test_valid_max_length(self) -> None:
        assert _slug_valid("a" * 160) is True

    def test_invalid_underscore(self) -> None:
        assert _slug_valid("has_underscore") is False


# ── TestBaseForge ────────────────────────────────────────────────────────────


class TestBaseForge:
    """Tests for BaseForge and its subclasses."""

    def test_dimensions_tuple_nonempty_for_all_subclasses(self) -> None:
        """Every concrete Forge subclass must define a non-empty dimensions tuple."""
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.services.material_forge.power_forge import PowerSystemForge
        from bestseller.services.material_forge.character_forge import CharacterForge
        from bestseller.services.material_forge.plot_forge import PlotForge
        from bestseller.services.material_forge.device_forge import DeviceForge

        for cls in [WorldForge, PowerSystemForge, CharacterForge, PlotForge, DeviceForge]:
            assert isinstance(cls.dimensions, tuple), f"{cls.__name__}.dimensions not a tuple"
            assert len(cls.dimensions) > 0, f"{cls.__name__}.dimensions is empty"

    async def test_run_calls_forge_per_dimension(self) -> None:
        """BaseForge.run() calls _forge_dimension once per dimension."""
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.settings import load_settings

        forge = WorldForge()
        settings = load_settings(env={})
        session = AsyncMock()

        fake_result = ForgeResult(
            project_id="p",
            dimension="world_settings",
            emitted=(),
            rounds=1,
            exit_reason="text",
        )

        with patch.object(forge, "_forge_dimension", new=AsyncMock(return_value=fake_result)) as mock_fd:
            results = await forge.run(session, "proj-x", "仙侠", settings)

        assert mock_fd.call_count == len(WorldForge.dimensions)
        assert len(results) == len(WorldForge.dimensions)

    async def test_forge_dimension_emits_via_tool_loop(self) -> None:
        """_forge_dimension populates outcome_box when emit_material tool is called."""
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.services.llm_tool_runtime import ToolLoopResult
        from bestseller.settings import load_settings

        forge = WorldForge()
        settings = load_settings(env={})
        session = AsyncMock()

        mat = _make_project_material(slug="yunhe-town", material_type="world_settings")

        # The tool loop mock captures the tools argument and returns a plain LoopResult
        captured_tools: list[Any] = []

        async def fake_run_tool_loop(s: Any, cfg: Any, *, base_request: Any, registry: Any, **kw: Any) -> ToolLoopResult:
            captured_tools.extend(registry.names())
            # Simulate calling emit_material tool — invoke the handler directly
            emit_tool = registry.get("emit_material")
            await emit_tool.handler({
                "slug": "yunhe-town",
                "name": "云和镇",
                "narrative_summary": "荒山脚下的矿镇。",
                "content_json": {"atmosphere": "gloomy"},
            })
            return ToolLoopResult(
                final_content="done",
                rounds=1,
                exit_reason="text",
            )

        import bestseller.services.llm as _llm_mod

        # _forge_dimension has a local import:
        #   from bestseller.services.llm import call_llm, LLMCompletionRequest
        # and creates LLMCompletionRequest with kwargs that differ from the real model.
        # Patch both: add a dummy call_llm and replace LLMCompletionRequest with a MagicMock
        # so the construction doesn't raise a validation error.
        mock_request_cls = MagicMock(return_value=MagicMock())

        had_call_llm = hasattr(_llm_mod, "call_llm")
        if not had_call_llm:
            _llm_mod.call_llm = None  # type: ignore[attr-defined]
        try:
            with patch.object(_llm_mod, "LLMCompletionRequest", mock_request_cls), \
                 patch("bestseller.services.material_forge.base.run_tool_loop", new=fake_run_tool_loop), \
                 patch("bestseller.services.material_forge.base.query_library", new=AsyncMock(return_value=[])), \
                 patch("bestseller.services.material_forge.base.insert_project_material", new=AsyncMock(return_value=mat)):
                result = await forge._forge_dimension(
                    session,
                    project_id="proj-abc",
                    dimension="world_settings",
                    genre="仙侠",
                    sub_genre=None,
                    settings=settings,
                    existing_materials={},
                    max_rounds=3,
                )
        finally:
            if not had_call_llm and hasattr(_llm_mod, "call_llm"):
                del _llm_mod.call_llm  # type: ignore[attr-defined]

        assert result.emitted_count == 1
        assert result.emitted[0].slug == "yunhe-town"
        assert result.dimension == "world_settings"
        # registry tool names should include both tools
        assert "emit_material" in captured_tools
        assert "query_library" in captured_tools

    async def test_forge_dimension_query_library_tool_returns_error_for_empty_query(self) -> None:
        """query_library tool handler returns {'error': 'empty_query'} for empty query."""
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.settings import load_settings

        forge = WorldForge()
        settings = load_settings(env={})
        session = AsyncMock()

        ql_tool = forge._build_query_library_tool(
            session,
            dimension="world_settings",
            genre="仙侠",
            sub_genre=None,
        )

        result = await ql_tool.handler({"query": ""})
        assert result == {"error": "empty_query"}

        result_whitespace = await ql_tool.handler({"query": "   "})
        assert result_whitespace == {"error": "empty_query"}

    async def test_emit_tool_handler_returns_ok_on_success(self) -> None:
        """emit_material tool handler returns 'ok' status and remaining count."""
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.settings import load_settings

        forge = WorldForge()
        settings = load_settings(env={})  # enable_novelty_guard defaults to False
        session = AsyncMock()
        outcome_box: list[ProjectMaterial] = []

        mat = _make_project_material(slug="my-slug", material_type="world_settings")

        with patch("bestseller.services.material_forge.base.insert_project_material", new=AsyncMock(return_value=mat)):
            emit_tool = forge._build_emit_tool(
                session,
                project_id="proj-x",
                dimension="world_settings",
                genre="仙侠",
                settings=settings,
                outcome_box=outcome_box,
                target_count=5,
            )
            response = await emit_tool.handler({
                "slug": "my-slug",
                "name": "测试地点",
                "narrative_summary": "简短摘要。",
                "content_json": {"k": "v"},
            })

        assert response["status"] == "ok"
        assert response["slug"] == "my-slug"
        assert response["remaining_to_emit"] == 4
        assert len(outcome_box) == 1

    async def test_emit_tool_handler_returns_validation_error(self) -> None:
        """emit_material tool handler returns error dict on invalid args."""
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.settings import load_settings

        forge = WorldForge()
        settings = load_settings(env={})  # enable_novelty_guard defaults to False
        session = AsyncMock()
        outcome_box: list[ProjectMaterial] = []

        emit_tool = forge._build_emit_tool(
            session,
            project_id="proj-x",
            dimension="world_settings",
            genre="仙侠",
            settings=settings,
            outcome_box=outcome_box,
            target_count=5,
        )
        response = await emit_tool.handler({
            "slug": "INVALID_UPPERCASE",
            "name": "名称",
            "narrative_summary": "摘要。",
            "content_json": {},
        })
        assert "error" in response
        assert "validation" in response["error"]


# ── TestForgeAllMaterials ────────────────────────────────────────────────────


class TestForgeAllMaterials:
    """Tests for the forge_all_materials orchestrator."""

    async def test_runs_all_5_forges(self) -> None:
        """forge_all_materials aggregates results from all 5 forges."""
        from bestseller.settings import load_settings

        session = AsyncMock()
        settings = load_settings(env={})

        # Each forge returns ForgeResults; WorldForge has 3 dims, others 1-2
        def make_forge_results(forge_cls: Any) -> list[ForgeResult]:
            from bestseller.services.material_forge.base import BaseForge
            forge_instance = forge_cls()
            return [
                ForgeResult(
                    project_id="proj-test",
                    dimension=dim,
                    emitted=(),
                    rounds=1,
                    exit_reason="text",
                )
                for dim in forge_instance.dimensions
            ]

        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.services.material_forge.power_forge import PowerSystemForge
        from bestseller.services.material_forge.character_forge import CharacterForge
        from bestseller.services.material_forge.plot_forge import PlotForge
        from bestseller.services.material_forge.device_forge import DeviceForge

        all_forge_classes = [WorldForge, PowerSystemForge, CharacterForge, PlotForge, DeviceForge]
        expected_total = sum(len(cls.dimensions) for cls in all_forge_classes)

        async def mock_run(self_forge: Any, *args: Any, **kwargs: Any) -> list[ForgeResult]:
            return make_forge_results(type(self_forge))

        with patch.object(WorldForge, "run", new=mock_run), \
             patch.object(PowerSystemForge, "run", new=mock_run), \
             patch.object(CharacterForge, "run", new=mock_run), \
             patch.object(PlotForge, "run", new=mock_run), \
             patch.object(DeviceForge, "run", new=mock_run):
            results = await forge_all_materials(session, "proj-test", "仙侠", settings)

        assert len(results) == expected_total

    async def test_accumulates_existing_materials(self) -> None:
        """Later forges receive earlier forges' emitted materials in existing_materials."""
        from bestseller.settings import load_settings
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.services.material_forge.power_forge import PowerSystemForge
        from bestseller.services.material_forge.character_forge import CharacterForge
        from bestseller.services.material_forge.plot_forge import PlotForge
        from bestseller.services.material_forge.device_forge import DeviceForge

        session = AsyncMock()
        settings = load_settings(env={})

        world_mat = _make_project_material(slug="world-a", material_type="world_settings")
        received_existing: list[dict[str, list[ProjectMaterial]]] = []

        async def world_run(self_forge: Any, *args: Any, existing_materials: Any = None, **kwargs: Any) -> list[ForgeResult]:
            return [
                ForgeResult(
                    project_id="p",
                    dimension="world_settings",
                    emitted=(world_mat,),
                    rounds=1,
                    exit_reason="text",
                ),
                ForgeResult(
                    project_id="p",
                    dimension="factions",
                    emitted=(),
                    rounds=1,
                    exit_reason="text",
                ),
                ForgeResult(
                    project_id="p",
                    dimension="locale_templates",
                    emitted=(),
                    rounds=1,
                    exit_reason="text",
                ),
            ]

        async def power_run(self_forge: Any, *args: Any, existing_materials: Any = None, **kwargs: Any) -> list[ForgeResult]:
            received_existing.append(dict(existing_materials or {}))
            return [
                ForgeResult(
                    project_id="p",
                    dimension="power_systems",
                    emitted=(),
                    rounds=1,
                    exit_reason="text",
                )
            ]

        async def stub_run(self_forge: Any, *args: Any, existing_materials: Any = None, **kwargs: Any) -> list[ForgeResult]:
            return [
                ForgeResult(
                    project_id="p",
                    dimension=dim,
                    emitted=(),
                    rounds=1,
                    exit_reason="text",
                )
                for dim in type(self_forge).dimensions
            ]

        with patch.object(WorldForge, "run", new=world_run), \
             patch.object(PowerSystemForge, "run", new=power_run), \
             patch.object(CharacterForge, "run", new=stub_run), \
             patch.object(PlotForge, "run", new=stub_run), \
             patch.object(DeviceForge, "run", new=stub_run):
            await forge_all_materials(session, "p", "仙侠", settings)

        # PowerSystemForge should have received world_settings in existing_materials
        assert len(received_existing) == 1
        assert "world_settings" in received_existing[0]
        assert world_mat in received_existing[0]["world_settings"]

    async def test_forge_failure_skipped(self) -> None:
        """If one forge raises an exception, remaining forges still run."""
        from bestseller.settings import load_settings
        from bestseller.services.material_forge.world_forge import WorldForge
        from bestseller.services.material_forge.power_forge import PowerSystemForge
        from bestseller.services.material_forge.character_forge import CharacterForge
        from bestseller.services.material_forge.plot_forge import PlotForge
        from bestseller.services.material_forge.device_forge import DeviceForge

        session = AsyncMock()
        settings = load_settings(env={})

        async def failing_run(self_forge: Any, *args: Any, **kwargs: Any) -> list[ForgeResult]:
            raise RuntimeError("WorldForge exploded")

        async def ok_run(self_forge: Any, *args: Any, **kwargs: Any) -> list[ForgeResult]:
            return [
                ForgeResult(
                    project_id="p",
                    dimension=dim,
                    emitted=(),
                    rounds=1,
                    exit_reason="text",
                )
                for dim in type(self_forge).dimensions
            ]

        with patch.object(WorldForge, "run", new=failing_run), \
             patch.object(PowerSystemForge, "run", new=ok_run), \
             patch.object(CharacterForge, "run", new=ok_run), \
             patch.object(PlotForge, "run", new=ok_run), \
             patch.object(DeviceForge, "run", new=ok_run):
            results = await forge_all_materials(session, "p", "仙侠", settings)

        # WorldForge (3 dims) failed, remaining 7 dims should still be present
        result_dims = {r.dimension for r in results}
        assert "power_systems" in result_dims
        assert "character_archetypes" in result_dims
        # WorldForge dims must NOT appear
        assert "world_settings" not in result_dims
        assert "factions" not in result_dims
        assert "locale_templates" not in result_dims


# ── TestParseMaterialRefs ────────────────────────────────────────────────────


class TestParseMaterialRefs:
    """Tests for parse_material_refs URN extractor."""

    def test_parses_single_ref(self) -> None:
        text = "See §world_settings/proj123/yunhe-town for details."
        refs = parse_material_refs(text)
        assert refs == ["§world_settings/proj123/yunhe-town"]

    def test_parses_multiple_refs(self) -> None:
        text = (
            "§world_settings/p1/yunhe-town and "
            "§power_systems/p1/blood-vein-system and "
            "§character_templates/p1/wang-qingfeng"
        )
        refs = parse_material_refs(text)
        assert len(refs) == 3
        assert "§world_settings/p1/yunhe-town" in refs
        assert "§power_systems/p1/blood-vein-system" in refs
        assert "§character_templates/p1/wang-qingfeng" in refs

    def test_deduplicates(self) -> None:
        text = "§world_settings/p1/yunhe-town repeated §world_settings/p1/yunhe-town"
        refs = parse_material_refs(text)
        assert refs == ["§world_settings/p1/yunhe-town"]
        assert len(refs) == 1

    def test_empty_text(self) -> None:
        assert parse_material_refs("") == []

    def test_preserves_insertion_order(self) -> None:
        text = "§b2/proj/z-entry then §a1/proj/a-entry"
        refs = parse_material_refs(text)
        assert refs[0] == "§b2/proj/z-entry"
        assert refs[1] == "§a1/proj/a-entry"

    def test_no_plain_text_match(self) -> None:
        text = "no URNs here, just plain text"
        assert parse_material_refs(text) == []


# ── TestRenderMaterialReferenceBlock ────────────────────────────────────────


class TestRenderMaterialReferenceBlock:
    """Tests for render_material_reference_block."""

    async def test_empty_when_no_materials(self) -> None:
        """Returns empty string when there are no active materials."""
        session = _make_session_returning([])
        result = await render_material_reference_block(
            session,
            "proj-empty",
            dimensions=["world_settings"],
        )
        assert result == ""

    async def test_renders_block_with_entries(self) -> None:
        """Block contains §URN lines for each returned row."""
        row1 = _StubRow(
            id=1,
            slug="yunhe-town",
            name="云和镇",
            narrative_summary="荒山脚下的矿镇",
            material_type="world_settings",
            content_json={"k": "v"},
            source_library_ids_json=[],
            variation_notes=None,
            status="active",
        )
        row2 = _StubRow(
            id=2,
            slug="iron-sect",
            name="铁宗",
            narrative_summary="以锻造见长的宗派",
            material_type="world_settings",
            content_json={},
            source_library_ids_json=[],
            variation_notes=None,
            status="active",
        )

        # Session always returns the same rows (once per dimension queried)
        session = AsyncMock()
        result_obj = _ScalarResult([row1, row2])
        session.execute.return_value = result_obj

        block = await render_material_reference_block(
            session,
            "proj-abc",
            dimensions=["world_settings"],
        )

        assert "§world_settings/proj-abc/yunhe-town" in block
        assert "§world_settings/proj-abc/iron-sect" in block

    async def test_respects_dimension_filter(self) -> None:
        """When dimensions=['power_systems'], only power_systems rows are queried."""
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([])

        await render_material_reference_block(
            session,
            "proj-x",
            dimensions=["power_systems"],
        )

        # execute should have been called exactly once (for power_systems)
        assert session.execute.call_count == 1

    async def test_block_contains_required_header(self) -> None:
        """Non-empty block starts with the canonical Chinese header."""
        row = _StubRow(
            id=1,
            slug="my-slug",
            name="名称",
            narrative_summary="摘要",
            material_type="world_settings",
            content_json={},
            source_library_ids_json=[],
            variation_notes=None,
            status="active",
        )
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([row])

        block = await render_material_reference_block(
            session,
            "proj-123",
            dimensions=["world_settings"],
        )

        assert block.startswith("## 可引用物料")

    async def test_multiple_dimensions_grouped(self) -> None:
        """Entries from different dimensions appear under separate ### headers."""
        world_row = _StubRow(
            id=1,
            slug="world-a",
            name="世界A",
            narrative_summary="摘要A",
            material_type="world_settings",
            content_json={},
            source_library_ids_json=[],
            variation_notes=None,
            status="active",
        )
        power_row = _StubRow(
            id=2,
            slug="power-b",
            name="功法B",
            narrative_summary="摘要B",
            material_type="power_systems",
            content_json={},
            source_library_ids_json=[],
            variation_notes=None,
            status="active",
        )

        session = AsyncMock()
        # First call returns world_settings row, second returns power_systems row
        session.execute.side_effect = [
            _ScalarResult([world_row]),
            _ScalarResult([power_row]),
        ]

        block = await render_material_reference_block(
            session,
            "proj-multi",
            dimensions=["world_settings", "power_systems"],
        )

        assert "### world_settings" in block
        assert "### power_systems" in block
        assert "§world_settings/proj-multi/world-a" in block
        assert "§power_systems/proj-multi/power-b" in block

    async def test_include_content_preview_flag(self) -> None:
        """When include_content_preview=True the block is still valid (no crash)."""
        row = _StubRow(
            id=1,
            slug="preview-slug",
            name="预览名称",
            narrative_summary="预览摘要",
            material_type="world_settings",
            content_json={"detail": "some content"},
            source_library_ids_json=[],
            variation_notes=None,
            status="active",
        )
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([row])

        block = await render_material_reference_block(
            session,
            "proj-preview",
            dimensions=["world_settings"],
            include_content_preview=True,
        )

        # Should still render without error and contain the URN
        assert "§world_settings/proj-preview/preview-slug" in block


# ── TestInsertProjectMaterial ─────────────────────────────────────────────────


class TestInsertProjectMaterial:
    """Tests for insert_project_material DB helper.

    The function uses a PostgreSQL-specific ``on_conflict_do_update`` clause
    via ``sqlalchemy.insert``.  We mock the ``insert`` function inside
    ``bestseller.infra.db.models`` (imported locally inside the function) to
    return a chainable mock so the test doesn't require a live PG connection.
    """

    def _make_mock_insert(self) -> MagicMock:
        """Build a mock insert() that supports .values(...).on_conflict_do_update(...)."""
        stmt_mock = MagicMock()
        stmt_mock.values.return_value = stmt_mock
        stmt_mock.on_conflict_do_update.return_value = stmt_mock
        insert_mock = MagicMock(return_value=stmt_mock)
        return insert_mock

    async def test_returns_same_material(self) -> None:
        """insert_project_material persists and returns the passed ProjectMaterial."""
        session = AsyncMock()
        mat = _make_project_material(slug="persist-me", material_type="world_settings")
        insert_mock = self._make_mock_insert()

        # The function does `from sqlalchemy import insert` locally — patch at sqlalchemy level
        import sqlalchemy as _sa
        with patch.object(_sa, "insert", insert_mock):
            returned = await insert_project_material(session, mat)

        assert returned is mat
        session.execute.assert_called_once()

    async def test_session_execute_called_with_statement(self) -> None:
        """Session.execute is invoked (not session.add)."""
        session = AsyncMock()
        mat = _make_project_material()
        insert_mock = self._make_mock_insert()

        import sqlalchemy as _sa
        with patch.object(_sa, "insert", insert_mock):
            await insert_project_material(session, mat)

        assert session.execute.called
        assert not session.add.called


# ── TestListProjectMaterials ──────────────────────────────────────────────────


class TestListProjectMaterials:
    """Tests for list_project_materials."""

    async def test_returns_list_of_dicts(self) -> None:
        """list_project_materials returns a list of dicts with expected keys."""
        row = _StubRow(
            id=42,
            material_type="world_settings",
            slug="test-slug",
            name="测试",
            narrative_summary="摘要",
            source_library_ids_json=[1, 2],
            variation_notes="差异点",
            status="active",
        )
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([row])

        result = await list_project_materials(session, "proj-x")

        assert len(result) == 1
        entry = result[0]
        assert entry["id"] == 42
        assert entry["material_type"] == "world_settings"
        assert entry["slug"] == "test-slug"
        assert entry["name"] == "测试"
        assert entry["source_library_ids"] == [1, 2]

    async def test_empty_when_no_rows(self) -> None:
        session = _make_session_returning([])
        result = await list_project_materials(session, "proj-empty")
        assert result == []

    async def test_material_type_filter_applied(self) -> None:
        """When material_type is provided, execute is still called (filter is passed to query)."""
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([])

        await list_project_materials(session, "proj-x", material_type="power_systems")

        assert session.execute.called

    async def test_source_library_ids_none_becomes_empty_list(self) -> None:
        """source_library_ids_json=None is normalised to [] in the output dict."""
        row = _StubRow(
            id=1,
            material_type="world_settings",
            slug="slug",
            name="名称",
            narrative_summary="摘要",
            source_library_ids_json=None,
            variation_notes=None,
            status="active",
        )
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([row])

        result = await list_project_materials(session, "proj-x")

        assert result[0]["source_library_ids"] == []
