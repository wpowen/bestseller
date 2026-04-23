"""Unit tests for ``bestseller.services.material_library_reference``.

Covers the soft-reference rendering that lets historical projects'
new chapters pull inspiration from the global library without hard
``§slug`` references.

We mock :func:`query_library` at module scope so the tests stay
hermetic (no DB, no pgvector).
"""

from __future__ import annotations

from typing import Any

import pytest

from bestseller.services import material_library_reference as mlr
from bestseller.services.material_library import MaterialEntry

pytestmark = pytest.mark.unit


# ── Helpers ───────────────────────────────────────────────────────────


def _entry(
    *,
    dimension: str,
    slug: str,
    name: str,
    summary: str,
    genre: str | None = "仙侠",
    usage_count: int = 0,
) -> MaterialEntry:
    return MaterialEntry(
        dimension=dimension,
        slug=slug,
        name=name,
        narrative_summary=summary,
        content_json={},
        genre=genre,
        usage_count=usage_count,
    )


class _FakeSession:
    """Records call args — the real session is never touched."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []


# ── Tests ─────────────────────────────────────────────────────────────


class TestRenderLibrarySoftReferenceBlock:
    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_query_library(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            return []

        monkeypatch.setattr(mlr, "query_library", fake_query_library)
        session = _FakeSession()
        out = await mlr.render_library_soft_reference_block(
            session,  # type: ignore[arg-type]
            query="ch1 main scene",
            genre="仙侠",
        )
        assert out == ""

    @pytest.mark.asyncio
    async def test_builds_markdown_block_with_soft_framing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_query_library(
            _session, *, dimension, query, genre, sub_genre, top_k, novelty_filter, include_generic,  # noqa: ARG001
        ):
            if dimension == "scene_templates":
                return [
                    _entry(
                        dimension="scene_templates",
                        slug="face-slap-reversal",
                        name="打脸反转",
                        summary="压迫→假装无辜→终极反杀",
                    )
                ]
            if dimension == "thematic_motifs":
                return [
                    _entry(
                        dimension="thematic_motifs",
                        slug="lonely-moon",
                        name="孤月",
                        summary="月代表观察者视角，角色内心的冷寂",
                    )
                ]
            return []

        monkeypatch.setattr(mlr, "query_library", fake_query_library)
        out = await mlr.render_library_soft_reference_block(
            _FakeSession(),  # type: ignore[arg-type]
            query="ch5 confrontation",
            genre="仙侠",
        )
        # Soft framing present — critical so LLM knows it's inspiration
        # not a hard constraint:
        assert "仅供参考" in out or "不强制" in out
        assert "不得直接套用" in out

        # Header + both picked entries rendered:
        assert "资源库灵感" in out
        assert "打脸反转" in out
        assert "孤月" in out
        # Dimension sections:
        assert "### scene_templates" in out
        assert "### thematic_motifs" in out

    @pytest.mark.asyncio
    async def test_custom_dimensions_override_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen_dims: list[str] = []

        async def fake_query_library(
            _session, *, dimension, **_kwargs,
        ):
            seen_dims.append(dimension)
            return []

        monkeypatch.setattr(mlr, "query_library", fake_query_library)
        await mlr.render_library_soft_reference_block(
            _FakeSession(),  # type: ignore[arg-type]
            query="q",
            genre="仙侠",
            dimensions=["factions", "world_settings"],
        )
        assert seen_dims == ["factions", "world_settings"]

    @pytest.mark.asyncio
    async def test_passes_max_usage_count_to_novelty_filter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured_filters: list[Any] = []

        async def fake_query_library(
            _session, *, dimension, query, genre, sub_genre, top_k, novelty_filter, include_generic,  # noqa: ARG001
        ):
            captured_filters.append(novelty_filter)
            return []

        monkeypatch.setattr(mlr, "query_library", fake_query_library)
        await mlr.render_library_soft_reference_block(
            _FakeSession(),  # type: ignore[arg-type]
            query="q",
            genre="仙侠",
            dimensions=["scene_templates"],
            max_usage_count=3,
        )
        assert captured_filters and captured_filters[0] is not None
        assert captured_filters[0].max_usage_count == 3

    @pytest.mark.asyncio
    async def test_query_exception_is_soft_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def blowup(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("retrieval backend dead")

        monkeypatch.setattr(mlr, "query_library", blowup)
        # Must NOT raise — soft-fail is a contract.
        out = await mlr.render_library_soft_reference_block(
            _FakeSession(),  # type: ignore[arg-type]
            query="q",
            genre="仙侠",
            dimensions=["scene_templates"],
        )
        assert out == ""

    def test_truncate_short_text_unchanged(self) -> None:
        assert mlr._truncate("短文") == "短文"

    def test_truncate_long_text_appends_ellipsis(self) -> None:
        long = "一" * 200
        out = mlr._truncate(long, limit=50)
        assert out.endswith("…")
        assert len(out) <= 51  # limit + ellipsis

    def test_truncate_empty_returns_empty(self) -> None:
        assert mlr._truncate("") == ""
