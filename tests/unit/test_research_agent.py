"""Unit tests for ``bestseller.services.research_agent``.

These are **not** integration tests — they do not hit an LLM, the web, or
Postgres.  We:

* Mock ``run_tool_loop`` with a stub that invokes the registered handlers
  directly (so we exercise the real ``emit_entry`` / ``search_library`` /
  ``search_web`` handlers).
* Mock the DB session (``FakeAsyncSession``) and ``insert_entry`` to
  capture persisted entries.
* Assert:
    * Emitted entries land in the outcome + session insert list.
    * Taboo patterns trigger rejection + a usable error payload.
    * Invalid payloads are rejected at the handler boundary.
    * Search tools delegate correctly to the injected clients.
    * Skills auto-loading is plumbed through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from bestseller.services.llm_tool_runtime import (
    ToolCallRecord,
    ToolLoopResult,
    ToolRegistry,
)
from bestseller.services.material_library import MaterialEntry
from bestseller.services.research_agent import (
    ResearchOutcome,
    _collect_taboos,
    _coerce_emit_arguments,
    _entry_hits_taboo,
    _slug_looks_valid,
    run_research,
)
from bestseller.services.search_client import (
    NoopSearchClient,
    SearchHit,
    SearchResponse,
)
from bestseller.services.skills_loader import ResearchSkill

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────


@dataclass
class _StubSession:
    """Session stand-in that records :func:`insert_entry` upserts."""

    inserted: list[MaterialEntry] = field(default_factory=list)


@dataclass
class _StubSettings:
    """Minimal AppSettings stand-in — not actually used by our stubs."""


def _fake_loop_factory(
    handler_calls: list[tuple[str, dict[str, Any]]],
    tool_invocations: list[tuple[str, dict[str, Any]]],
    final_text: str = "done",
):
    """Build a replacement for :func:`run_tool_loop`.

    Each entry in ``tool_invocations`` is ``(tool_name, arguments)``; the
    fake loop dispatches it through the real :class:`ToolRegistry`, which
    in turn runs the agent's own handlers.
    """

    async def _fake_run_tool_loop(
        session: Any,
        settings: Any,
        *,
        base_request: Any,
        registry: ToolRegistry,
        max_rounds: int = 5,
        tool_choice: Any = "auto",
    ) -> ToolLoopResult:
        trace: list[ToolCallRecord] = []
        for tool_name, arguments in tool_invocations:
            handler_calls.append((tool_name, dict(arguments)))
            spec = registry.get(tool_name)
            if spec is None:
                trace.append(
                    ToolCallRecord(
                        round_index=1,
                        tool_name=tool_name,
                        arguments=arguments,
                        result={"error": "unknown_tool"},
                        error="unknown_tool",
                    )
                )
                continue
            result = await spec.handler(arguments)
            trace.append(
                ToolCallRecord(
                    round_index=1,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                )
            )
        return ToolLoopResult(
            final_content=final_text,
            final_tool_results={},
            rounds=1,
            exit_reason="text",
            trace=trace,
            last_completion=None,
        )

    return _fake_run_tool_loop


async def _patched_insert_entry(
    session: _StubSession, entry: MaterialEntry, *, compute_embedding: bool = True
) -> MaterialEntry:
    """Pretend to persist: echo back the entry with a synthetic id."""
    assigned_id = len(session.inserted) + 1
    persisted = MaterialEntry(
        dimension=entry.dimension,
        slug=entry.slug,
        name=entry.name,
        narrative_summary=entry.narrative_summary,
        content_json=entry.content_json,
        genre=entry.genre,
        sub_genre=entry.sub_genre,
        tags=entry.tags,
        source_type=entry.source_type,
        source_citations=entry.source_citations,
        confidence=entry.confidence,
        coverage_score=entry.coverage_score,
        status=entry.status,
        embedding=entry.embedding,
        id=assigned_id,
        usage_count=0,
    )
    session.inserted.append(persisted)
    return persisted


async def _patched_query_library(
    session: _StubSession, *, dimension: str, **kwargs: Any
) -> list[MaterialEntry]:
    """Return whatever the session has previously had inserted."""
    return [e for e in session.inserted if e.dimension == dimension]


# ── Pure helpers ───────────────────────────────────────────────────────


class TestPureHelpers:
    def test_slug_validation(self) -> None:
        assert _slug_looks_valid("qingluo-sect")
        assert _slug_looks_valid("a1b2")
        assert _slug_looks_valid("x")
        assert not _slug_looks_valid("")
        assert not _slug_looks_valid("UPPER")
        assert not _slug_looks_valid("with space")
        assert not _slug_looks_valid("-dash-start")
        assert not _slug_looks_valid("dash-end-")

    def test_collect_taboos_deduplicates(self) -> None:
        s1 = ResearchSkill(key="a", name="A", taboo_patterns=["方域", "废灵根"])
        s2 = ResearchSkill(key="b", name="B", taboo_patterns=["方域", "青萝剑"])
        assert _collect_taboos([s1, s2]) == ("方域", "废灵根", "青萝剑")

    def test_entry_hits_taboo_detects_name(self) -> None:
        entry = MaterialEntry(
            dimension="factions",
            slug="fangyu-sect",
            name="方域宗",
            narrative_summary="-",
            content_json={},
        )
        assert _entry_hits_taboo(entry, ["方域"]) == "方域"

    def test_entry_hits_taboo_returns_none_when_safe(self) -> None:
        entry = MaterialEntry(
            dimension="factions",
            slug="heyan-sect",
            name="合衍宗",
            narrative_summary="-",
            content_json={},
        )
        assert _entry_hits_taboo(entry, ["方域"]) is None

    def test_coerce_emit_arguments_happy_path(self) -> None:
        entry = _coerce_emit_arguments(
            {
                "slug": "qingyun-sect",
                "name": "青云宗",
                "narrative_summary": "一个擅长御剑的仙侠宗门",
                "content_json": {"location": "九天山脉"},
                "tags": ["仙侠", "宗门"],
                "source_citations": [
                    {"url": "https://example.test/1", "title": "t"},
                    "https://example.test/2",
                ],
                "confidence": 0.8,
            },
            dimension="factions",
            default_genre="仙侠",
            default_sub_genre="upgrade",
        )
        assert entry.slug == "qingyun-sect"
        assert entry.name == "青云宗"
        assert entry.dimension == "factions"
        assert entry.genre == "仙侠"
        assert entry.tags == ["仙侠", "宗门"]
        assert entry.confidence == 0.8
        assert entry.source_citations[0]["url"] == "https://example.test/1"
        assert entry.source_citations[1]["url"] == "https://example.test/2"

    def test_coerce_emit_rejects_invalid_slug(self) -> None:
        with pytest.raises(ValueError) as exc:
            _coerce_emit_arguments(
                {
                    "slug": "INVALID SLUG",
                    "name": "N",
                    "narrative_summary": "S",
                    "content_json": {},
                },
                dimension="d",
                default_genre=None,
                default_sub_genre=None,
            )
        assert "invalid_slug" in str(exc.value)

    def test_coerce_emit_parses_content_json_string(self) -> None:
        entry = _coerce_emit_arguments(
            {
                "slug": "abc",
                "name": "N",
                "narrative_summary": "S",
                "content_json": '{"x": 1}',
            },
            dimension="d",
            default_genre=None,
            default_sub_genre=None,
        )
        assert entry.content_json == {"x": 1}

    def test_coerce_emit_clamps_confidence(self) -> None:
        entry = _coerce_emit_arguments(
            {
                "slug": "abc",
                "name": "N",
                "narrative_summary": "S",
                "content_json": {},
                "confidence": 2.5,
            },
            dimension="d",
            default_genre=None,
            default_sub_genre=None,
        )
        assert entry.confidence == 1.0


# ── run_research behaviour ─────────────────────────────────────────────


class TestRunResearch:
    async def test_emits_entries_and_records_in_outcome(
        self, tmp_path: Path
    ) -> None:
        session = _StubSession()
        settings = _StubSettings()
        handler_calls: list[tuple[str, dict[str, Any]]] = []
        tool_plan = [
            (
                "emit_entry",
                {
                    "slug": "qingyun-sect",
                    "name": "青云宗",
                    "narrative_summary": "御剑宗门",
                    "content_json": {"loc": "山脉"},
                    "source_citations": [{"url": "https://example.test"}],
                    "confidence": 0.7,
                },
            ),
            (
                "emit_entry",
                {
                    "slug": "five-qi-law",
                    "name": "五气归元诀",
                    "narrative_summary": "入门心法",
                    "content_json": {"stages": ["入门", "登堂", "入室"]},
                    "source_citations": [{"url": "https://example.test/2"}],
                },
            ),
        ]
        fake_loop = _fake_loop_factory(handler_calls, tool_plan)
        with patch(
            "bestseller.services.research_agent.run_tool_loop",
            new=AsyncMock(side_effect=fake_loop),
        ), patch(
            "bestseller.services.research_agent.insert_entry",
            new=_patched_insert_entry,
        ), patch(
            "bestseller.services.research_agent.query_library",
            new=_patched_query_library,
        ):
            outcome = await run_research(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                dimension="factions",
                genre="仙侠",
                sub_genre="upgrade",
                target_count=2,
                skills=[
                    ResearchSkill(
                        key="k1",
                        name="仙侠升级",
                        matches_genres=["仙侠"],
                        taboo_patterns=["方域"],
                    )
                ],
            )

        assert isinstance(outcome, ResearchOutcome)
        assert outcome.emitted_count == 2
        assert {e.slug for e in outcome.emitted} == {
            "qingyun-sect",
            "five-qi-law",
        }
        assert all(e.dimension == "factions" for e in outcome.emitted)
        assert outcome.exit_reason == "text"
        assert outcome.rejected_taboos == ()
        assert len(session.inserted) == 2
        assert handler_calls == [(name, args) for name, args in tool_plan]

    async def test_taboo_hit_rejects_entry(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        handler_calls: list[tuple[str, dict[str, Any]]] = []
        tool_plan = [
            (
                "emit_entry",
                {
                    "slug": "fangyu-sect",
                    "name": "方域宗",  # hits taboo
                    "narrative_summary": "反派宗门",
                    "content_json": {},
                    "source_citations": [{"url": "https://example.test"}],
                },
            ),
            (
                "emit_entry",
                {
                    "slug": "heyan-sect",
                    "name": "合衍宗",
                    "narrative_summary": "换个名字的反派宗门",
                    "content_json": {},
                    "source_citations": [{"url": "https://example.test"}],
                },
            ),
        ]
        fake_loop = _fake_loop_factory(handler_calls, tool_plan)
        with patch(
            "bestseller.services.research_agent.run_tool_loop",
            new=AsyncMock(side_effect=fake_loop),
        ), patch(
            "bestseller.services.research_agent.insert_entry",
            new=_patched_insert_entry,
        ), patch(
            "bestseller.services.research_agent.query_library",
            new=_patched_query_library,
        ):
            outcome = await run_research(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                dimension="factions",
                genre="仙侠",
                target_count=2,
                skills=[
                    ResearchSkill(
                        key="k1",
                        name="仙侠",
                        matches_genres=["仙侠"],
                        taboo_patterns=["方域"],
                    )
                ],
            )

        assert outcome.emitted_count == 1
        assert outcome.emitted[0].slug == "heyan-sect"
        assert outcome.rejected_taboos == (("fangyu-sect", "方域"),)

    async def test_invalid_slug_reports_error_not_emitted(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        tool_plan = [
            (
                "emit_entry",
                {
                    # Contains space + Chinese — neither survive lowercasing
                    # into the kebab-case slug regex.
                    "slug": "bad slug 中文",
                    "name": "N",
                    "narrative_summary": "S",
                    "content_json": {},
                },
            ),
        ]
        handler_calls: list[tuple[str, dict[str, Any]]] = []
        with patch(
            "bestseller.services.research_agent.run_tool_loop",
            new=AsyncMock(side_effect=_fake_loop_factory(handler_calls, tool_plan)),
        ), patch(
            "bestseller.services.research_agent.insert_entry",
            new=_patched_insert_entry,
        ), patch(
            "bestseller.services.research_agent.query_library",
            new=_patched_query_library,
        ):
            outcome = await run_research(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                dimension="d",
                genre=None,
                target_count=1,
                skills=[],
            )

        assert outcome.emitted_count == 0
        # Trace retained the validation error.
        assert any(
            rec.result.get("error", "").startswith("validation:")
            for rec in outcome.tool_trace
        )

    async def test_search_web_uses_injected_client(self) -> None:
        class Spy:
            provider = "spy"

            def __init__(self) -> None:
                self.calls: list[tuple[str, int | None]] = []

            async def search(
                self, query: str, *, max_results: int | None = None
            ) -> SearchResponse:
                self.calls.append((query, max_results))
                return SearchResponse(
                    query=query,
                    hits=(
                        SearchHit(title="T", url="https://u", snippet="S"),
                    ),
                    provider=self.provider,
                )

            async def close(self) -> None:
                return None

        spy = Spy()
        session = _StubSession()
        settings = _StubSettings()
        tool_plan = [("search_web", {"query": "道教 九境", "max_results": 3})]
        handler_calls: list[tuple[str, dict[str, Any]]] = []
        with patch(
            "bestseller.services.research_agent.run_tool_loop",
            new=AsyncMock(side_effect=_fake_loop_factory(handler_calls, tool_plan)),
        ), patch(
            "bestseller.services.research_agent.insert_entry",
            new=_patched_insert_entry,
        ), patch(
            "bestseller.services.research_agent.query_library",
            new=_patched_query_library,
        ):
            outcome = await run_research(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                dimension="power_systems",
                genre="仙侠",
                target_count=0,
                skills=[],
                search_client=spy,  # type: ignore[arg-type]
            )

        assert spy.calls == [("道教 九境", 3)]
        assert outcome.emitted_count == 0
        assert outcome.tool_trace[0].tool_name == "search_web"
        assert outcome.tool_trace[0].result["hits"][0]["url"] == "https://u"

    async def test_search_library_delegates_to_query_library(self) -> None:
        session = _StubSession()
        # Pre-populate so query_library returns something.
        session.inserted.append(
            MaterialEntry(
                id=99,
                dimension="power_systems",
                slug="nine-levels",
                name="九境",
                narrative_summary="九境体系",
                content_json={},
            )
        )
        settings = _StubSettings()
        tool_plan = [("search_library", {"query": "境界"})]
        handler_calls: list[tuple[str, dict[str, Any]]] = []
        with patch(
            "bestseller.services.research_agent.run_tool_loop",
            new=AsyncMock(side_effect=_fake_loop_factory(handler_calls, tool_plan)),
        ), patch(
            "bestseller.services.research_agent.insert_entry",
            new=_patched_insert_entry,
        ), patch(
            "bestseller.services.research_agent.query_library",
            new=_patched_query_library,
        ):
            outcome = await run_research(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                dimension="power_systems",
                genre="仙侠",
                target_count=0,
                skills=[],
            )

        record = outcome.tool_trace[0]
        assert record.tool_name == "search_library"
        assert record.result["count"] == 1
        assert record.result["entries"][0]["slug"] == "nine-levels"

    async def test_defaults_to_noop_search_client(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        tool_plan = [("search_web", {"query": "x"})]
        handler_calls: list[tuple[str, dict[str, Any]]] = []
        with patch(
            "bestseller.services.research_agent.run_tool_loop",
            new=AsyncMock(side_effect=_fake_loop_factory(handler_calls, tool_plan)),
        ), patch(
            "bestseller.services.research_agent.insert_entry",
            new=_patched_insert_entry,
        ), patch(
            "bestseller.services.research_agent.query_library",
            new=_patched_query_library,
        ):
            outcome = await run_research(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                dimension="d",
                genre=None,
                target_count=0,
                skills=[],
                search_client=None,
            )
        # Noop returns empty hits.
        assert outcome.tool_trace[0].result["hits"] == []

    async def test_auto_loads_skills_when_none_given(self, tmp_path: Path) -> None:
        # Create a temp skills directory with one is_common skill so the
        # research agent can find something even without calls.
        (tmp_path / "common.skill.md").write_text(
            "---\nkey: common\nname: Common\nis_common: true\ntaboo_patterns:\n- 禁词\n---\n",
            encoding="utf-8",
        )
        session = _StubSession()
        settings = _StubSettings()
        tool_plan: list[tuple[str, dict[str, Any]]] = []
        handler_calls: list[tuple[str, dict[str, Any]]] = []

        # Spy on load_skills_for_genre by pointing it at the tmp dir.
        import bestseller.services.research_agent as ra

        with patch(
            "bestseller.services.research_agent.run_tool_loop",
            new=AsyncMock(side_effect=_fake_loop_factory(handler_calls, tool_plan)),
        ), patch(
            "bestseller.services.research_agent.load_skills_for_genre",
            side_effect=lambda *a, **kw: [
                ResearchSkill(
                    key="common",
                    name="Common",
                    is_common=True,
                    taboo_patterns=["禁词"],
                )
            ],
        ) as spy_load:
            outcome = await run_research(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                dimension="d",
                genre="仙侠",
                sub_genre="upgrade",
                target_count=0,
                search_client=NoopSearchClient(),
            )

        spy_load.assert_called_once()
        assert outcome.emitted_count == 0
