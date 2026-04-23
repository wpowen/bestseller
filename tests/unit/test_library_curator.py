"""Unit tests for ``bestseller.services.library_curator``.

These are pure unit tests — no DB, no LLM, no MCP subprocess.  We mock:

* ``ensure_coverage`` to synthesise :class:`CoverageReport` values and
  drive the audit into gap / satisfied branches.
* ``run_research`` to return deterministic :class:`ResearchOutcome`
  payloads so ``fill_gap`` can exercise its search-client lifecycle and
  report shape without hitting the network.

Coverage goals:

* Default coverage plan enumerates all 14 dimensions × (None + 3 genres).
* ``coverage_audit`` routes gap vs satisfied correctly and honours
  ``ttl_days``.
* ``fill_gap`` computes the right ``target_count``, owns + closes
  factory-built search clients, and forwards MCP extra tools.
* ``run_curation`` caps gaps, skips fills when ``fill=False``, keeps the
  sweep alive when one ``fill_gap`` raises.
* ``scheduled_weekly_audit`` short-circuits when the env flag is off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from bestseller.services.library_curator import (
    CURATOR_COVERAGE_PLAN,
    AuditResult,
    CoverageGap,
    CoverageTarget,
    CurationReport,
    FillOutcome,
    _MATERIAL_DIMENSIONS,
    _default_coverage_plan,
    coverage_audit,
    fill_gap,
    run_curation,
    scheduled_weekly_audit,
)
from bestseller.services.material_library import CoverageReport, MaterialEntry
from bestseller.services.research_agent import ResearchOutcome
from bestseller.services.search_client import NoopSearchClient, WebSearchClient

pytestmark = pytest.mark.unit


# ── Fakes ──────────────────────────────────────────────────────────────


@dataclass
class _StubSession:
    """Minimal stand-in for :class:`AsyncSession`."""


@dataclass
class _StubSettings:
    """AppSettings stand-in — never dereferenced by the curator directly."""


@dataclass
class _SpyEnsureCoverage:
    """Callable that records invocations and returns scripted reports."""

    scripted: dict[tuple[str, str | None, str | None], CoverageReport] = field(
        default_factory=dict
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self,
        session: Any,
        *,
        dimension: str,
        genre: str | None,
        sub_genre: str | None = None,
        min_entries: int = 10,
        ttl_days: int | None = None,
    ) -> CoverageReport:
        self.calls.append(
            {
                "dimension": dimension,
                "genre": genre,
                "sub_genre": sub_genre,
                "min_entries": min_entries,
                "ttl_days": ttl_days,
            }
        )
        key = (dimension, genre, sub_genre)
        if key in self.scripted:
            return self.scripted[key]
        # Default: satisfy the bucket
        return CoverageReport(
            dimension=dimension,
            genre=genre,
            sub_genre=sub_genre,
            active_count=min_entries,
            min_required=min_entries,
            is_satisfied=True,
            stale_ids=(),
        )


@dataclass
class _SpyRunResearch:
    """Stand-in for :func:`run_research` that captures args + echoes a result."""

    emitted: tuple[MaterialEntry, ...] = ()
    rejected_taboos: tuple[tuple[str, str], ...] = ()
    exit_reason: str = "text"
    raise_on_call: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self,
        session: Any,
        settings: Any,
        *,
        dimension: str,
        genre: str | None,
        sub_genre: str | None = None,
        target_count: int = 10,
        skills: Any = None,
        search_client: WebSearchClient | None = None,
        extra_tools: Any = None,
        max_rounds: int = 5,
        tool_choice: Any = "auto",
        logical_role: str = "planner",
    ) -> ResearchOutcome:
        self.calls.append(
            {
                "dimension": dimension,
                "genre": genre,
                "sub_genre": sub_genre,
                "target_count": target_count,
                "skills": skills,
                "search_client": search_client,
                "extra_tools": tuple(extra_tools) if extra_tools else (),
                "max_rounds": max_rounds,
            }
        )
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return ResearchOutcome(
            dimension=dimension,
            genre=genre,
            sub_genre=sub_genre,
            target_count=target_count,
            emitted=self.emitted,
            rejected_taboos=self.rejected_taboos,
            tool_trace=(),
            rounds=1,
            exit_reason=self.exit_reason,
        )


def _entry(
    dimension: str = "factions",
    slug: str = "sample-entry",
    name: str = "Sample",
    **overrides: Any,
) -> MaterialEntry:
    return MaterialEntry(
        dimension=dimension,
        slug=slug,
        name=name,
        narrative_summary=overrides.pop("narrative_summary", "-"),
        content_json=overrides.pop("content_json", {}),
        genre=overrides.pop("genre", None),
        sub_genre=overrides.pop("sub_genre", None),
        id=overrides.pop("id", 1),
    )


def _report(
    dimension: str,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    active_count: int,
    min_required: int,
    stale_ids: tuple[int, ...] = (),
) -> CoverageReport:
    return CoverageReport(
        dimension=dimension,
        genre=genre,
        sub_genre=sub_genre,
        active_count=active_count,
        min_required=min_required,
        is_satisfied=active_count >= min_required,
        stale_ids=stale_ids,
    )


# ── Plan ───────────────────────────────────────────────────────────────


class TestCoveragePlan:
    def test_default_plan_covers_every_dimension(self) -> None:
        plan = _default_coverage_plan(min_entries=5)
        # 14 baseline + 3 genres × 14 = 56 targets
        assert len(plan) == len(_MATERIAL_DIMENSIONS) * 4
        # Every dimension appears at least once with genre=None
        baseline = [t for t in plan if t.genre is None]
        assert {t.dimension for t in baseline} == set(_MATERIAL_DIMENSIONS)
        # Each of the 3 seed genres covers every dimension.
        for genre in ("仙侠", "都市修仙", "科幻"):
            genre_dims = {t.dimension for t in plan if t.genre == genre}
            assert genre_dims == set(_MATERIAL_DIMENSIONS)
        # min_entries propagated
        assert all(t.min_entries == 5 for t in plan)

    def test_module_level_plan_uses_default_min_entries(self) -> None:
        assert CURATOR_COVERAGE_PLAN[0].min_entries == 10

    def test_coverage_gap_exposes_target_shortcuts(self) -> None:
        target = CoverageTarget(
            dimension="factions", genre="仙侠", sub_genre="upgrade", min_entries=4
        )
        report = _report(
            "factions",
            genre="仙侠",
            sub_genre="upgrade",
            active_count=1,
            min_required=4,
        )
        gap = CoverageGap(target=target, report=report)
        assert gap.dimension == "factions"
        assert gap.genre == "仙侠"
        assert gap.sub_genre == "upgrade"


# ── Audit ──────────────────────────────────────────────────────────────


class TestCoverageAudit:
    async def test_splits_gaps_and_satisfied(self) -> None:
        session = _StubSession()
        plan = (
            CoverageTarget(dimension="factions", genre="仙侠", min_entries=10),
            CoverageTarget(dimension="power_systems", genre="仙侠", min_entries=10),
        )
        spy = _SpyEnsureCoverage(
            scripted={
                ("factions", "仙侠", None): _report(
                    "factions",
                    genre="仙侠",
                    active_count=3,
                    min_required=10,
                ),
                ("power_systems", "仙侠", None): _report(
                    "power_systems",
                    genre="仙侠",
                    active_count=12,
                    min_required=10,
                ),
            }
        )
        with patch(
            "bestseller.services.library_curator.ensure_coverage",
            new=spy,
        ):
            result = await coverage_audit(session, plan=plan)  # type: ignore[arg-type]
        assert isinstance(result, AuditResult)
        assert result.targets_checked == 2
        assert len(result.gaps) == 1
        assert result.gaps[0].dimension == "factions"
        assert len(result.satisfied) == 1
        assert result.satisfied[0].dimension == "power_systems"

    async def test_forwards_ttl_days_to_ensure_coverage(self) -> None:
        session = _StubSession()
        spy = _SpyEnsureCoverage()
        plan = (CoverageTarget(dimension="factions", genre=None),)
        with patch(
            "bestseller.services.library_curator.ensure_coverage",
            new=spy,
        ):
            await coverage_audit(session, plan=plan, ttl_days=30)  # type: ignore[arg-type]
        assert spy.calls[0]["ttl_days"] == 30

    async def test_uses_module_default_plan_when_none(self) -> None:
        session = _StubSession()
        spy = _SpyEnsureCoverage()
        with patch(
            "bestseller.services.library_curator.ensure_coverage",
            new=spy,
        ):
            result = await coverage_audit(session)  # type: ignore[arg-type]
        assert result.targets_checked == len(CURATOR_COVERAGE_PLAN)
        assert len(spy.calls) == len(CURATOR_COVERAGE_PLAN)


# ── Fill ───────────────────────────────────────────────────────────────


def _make_gap(
    dimension: str = "factions",
    genre: str | None = "仙侠",
    sub_genre: str | None = None,
    *,
    active: int = 2,
    min_required: int = 10,
) -> CoverageGap:
    target = CoverageTarget(
        dimension=dimension,
        genre=genre,
        sub_genre=sub_genre,
        min_entries=min_required,
    )
    return CoverageGap(
        target=target,
        report=_report(
            dimension,
            genre=genre,
            sub_genre=sub_genre,
            active_count=active,
            min_required=min_required,
        ),
    )


class TestFillGap:
    async def test_computes_target_count_from_remaining(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap(active=4, min_required=10)
        spy = _SpyRunResearch(emitted=(_entry(),))
        client = NoopSearchClient()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ):
            outcome = await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=client,
            )
        assert len(spy.calls) == 1
        # 10 - 4 = 6 entries to fill.
        assert spy.calls[0]["target_count"] == 6
        assert spy.calls[0]["search_client"] is client
        assert outcome.emitted_count == 1

    async def test_clamps_target_count_by_max_fills_per_run(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap(active=0, min_required=10)
        spy = _SpyRunResearch()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ):
            await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=NoopSearchClient(),
                max_fills_per_run=3,
            )
        assert spy.calls[0]["target_count"] == 3

    async def test_always_requests_at_least_one(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        # Edge: already over target (deliberately malformed gap)
        gap = _make_gap(active=10, min_required=10)
        spy = _SpyRunResearch()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ):
            await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=NoopSearchClient(),
            )
        assert spy.calls[0]["target_count"] == 1

    async def test_builds_and_closes_default_search_client(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap()
        spy = _SpyRunResearch()

        # Build a real object (not Mock) so isinstance(..., NoopSearchClient)
        # is meaningful.  We still fake build_search_client so we can assert
        # close() was called.
        class _ClosableClient:
            provider = "tracking"

            def __init__(self) -> None:
                self.closed = False

            async def search(self, query: str, *, max_results: int | None = None):  # noqa: ARG002
                return None  # not used

            async def close(self) -> None:
                self.closed = True

        tracker = _ClosableClient()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ), patch(
            "bestseller.services.library_curator.build_search_client",
            return_value=tracker,
        ):
            await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=None,
            )
        assert tracker.closed is True

    async def test_does_not_close_caller_supplied_client(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap()
        spy = _SpyRunResearch()

        class _Tracker:
            provider = "keep-alive"

            def __init__(self) -> None:
                self.closed = False

            async def search(self, query: str, *, max_results: int | None = None):  # noqa: ARG002
                return None

            async def close(self) -> None:
                self.closed = True

        tracker = _Tracker()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ):
            await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=tracker,  # type: ignore[arg-type]
            )
        assert tracker.closed is False

    async def test_logs_but_survives_close_raising(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap()
        spy = _SpyRunResearch()

        class _BadCloser:
            provider = "bad-close"

            async def search(self, query: str, *, max_results: int | None = None):  # noqa: ARG002
                return None

            async def close(self) -> None:
                raise RuntimeError("network wedged")

        tracker = _BadCloser()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ), patch(
            "bestseller.services.library_curator.build_search_client",
            return_value=tracker,
        ):
            # Must not raise — the close() error is swallowed + logged.
            outcome = await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=None,
            )
        assert outcome.exit_reason == "text"

    async def test_does_not_close_noop_client_built_by_factory(self) -> None:
        # Even when the factory defaults to Noop (no API keys), we don't
        # call close() — not strictly required since Noop.close() is a
        # no-op, but covers the conditional in fill_gap.
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap()
        spy = _SpyRunResearch()
        noop = NoopSearchClient()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ), patch(
            "bestseller.services.library_curator.build_search_client",
            return_value=noop,
        ):
            outcome = await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=None,
            )
        # Noop.close() is a no-op but the curator's skip branch is exercised.
        assert outcome.exit_reason == "text"

    async def test_passes_mcp_extra_tools_to_research(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap()
        spy = _SpyRunResearch()

        class _FakePool:
            def as_tool_specs(self, consumer: str | None = None):
                # Return something non-empty so we can assert pass-through.
                return [("fake-tool-spec",)]  # content doesn't matter

        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ):
            await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=NoopSearchClient(),
                mcp_pool=_FakePool(),  # type: ignore[arg-type]
            )
        assert spy.calls[0]["extra_tools"] == (("fake-tool-spec",),)

    async def test_autoloads_skills_when_none_given(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap(genre="仙侠", sub_genre="upgrade")
        spy = _SpyRunResearch()
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=["SENTINEL"],
        ) as spy_load:
            await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=NoopSearchClient(),
            )
        spy_load.assert_called_once_with("仙侠", "upgrade")
        assert spy.calls[0]["skills"] == ["SENTINEL"]

    async def test_propagates_outcome_payload(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        gap = _make_gap()
        spy = _SpyRunResearch(
            emitted=(_entry(slug="a"), _entry(slug="b")),
            rejected_taboos=(("fangyu-sect", "方域"),),
            exit_reason="max_rounds",
        )
        with patch(
            "bestseller.services.library_curator.run_research",
            new=spy,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ):
            outcome = await fill_gap(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                gap,
                search_client=NoopSearchClient(),
            )
        assert isinstance(outcome, FillOutcome)
        assert outcome.emitted_count == 2
        assert outcome.rejected_taboos == (("fangyu-sect", "方域"),)
        assert outcome.exit_reason == "max_rounds"


# ── Orchestration ──────────────────────────────────────────────────────


class TestRunCuration:
    async def test_audit_only_when_fill_false(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        plan = (CoverageTarget(dimension="factions", genre=None),)
        spy_ensure = _SpyEnsureCoverage(
            scripted={
                ("factions", None, None): _report(
                    "factions", active_count=0, min_required=10
                )
            }
        )
        spy_research = _SpyRunResearch()
        with patch(
            "bestseller.services.library_curator.ensure_coverage",
            new=spy_ensure,
        ), patch(
            "bestseller.services.library_curator.run_research",
            new=spy_research,
        ):
            report = await run_curation(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                plan=plan,
                fill=False,
            )
        assert isinstance(report, CurationReport)
        assert len(report.audit.gaps) == 1
        assert report.fills == ()
        assert spy_research.calls == []

    async def test_skips_fill_when_no_gaps(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        plan = (CoverageTarget(dimension="factions", genre=None),)
        spy_ensure = _SpyEnsureCoverage()  # defaults to satisfied
        spy_research = _SpyRunResearch()
        with patch(
            "bestseller.services.library_curator.ensure_coverage",
            new=spy_ensure,
        ), patch(
            "bestseller.services.library_curator.run_research",
            new=spy_research,
        ):
            report = await run_curation(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                plan=plan,
            )
        assert report.audit.gaps == ()
        assert report.fills == ()
        assert spy_research.calls == []

    async def test_fills_gaps_up_to_max_gaps(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        plan = (
            CoverageTarget(dimension="factions", genre=None),
            CoverageTarget(dimension="power_systems", genre=None),
            CoverageTarget(dimension="character_templates", genre=None),
        )
        spy_ensure = _SpyEnsureCoverage(
            scripted={
                ("factions", None, None): _report(
                    "factions", active_count=0, min_required=10
                ),
                ("power_systems", None, None): _report(
                    "power_systems", active_count=2, min_required=10
                ),
                ("character_templates", None, None): _report(
                    "character_templates", active_count=1, min_required=10
                ),
            }
        )
        spy_research = _SpyRunResearch(emitted=(_entry(),))
        with patch(
            "bestseller.services.library_curator.ensure_coverage",
            new=spy_ensure,
        ), patch(
            "bestseller.services.library_curator.run_research",
            new=spy_research,
        ), patch(
            "bestseller.services.library_curator.load_skills_for_genre",
            return_value=[],
        ):
            report = await run_curation(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                plan=plan,
                search_client=NoopSearchClient(),
                max_gaps=2,
                max_fills_per_run=3,
            )
        # All 3 audits; only 2 fills respected max_gaps.
        assert report.audit.targets_checked == 3
        assert len(report.audit.gaps) == 3
        assert len(report.fills) == 2
        assert report.total_emitted == 2

    async def test_continues_sweep_when_single_fill_raises(self) -> None:
        session = _StubSession()
        settings = _StubSettings()
        plan = (
            CoverageTarget(dimension="factions", genre=None),
            CoverageTarget(dimension="power_systems", genre=None),
        )
        spy_ensure = _SpyEnsureCoverage(
            scripted={
                ("factions", None, None): _report(
                    "factions", active_count=0, min_required=10
                ),
                ("power_systems", None, None): _report(
                    "power_systems", active_count=0, min_required=10
                ),
            }
        )

        failures = {"factions"}

        async def flaky_fill_gap(
            session: Any,
            settings: Any,
            gap: CoverageGap,
            *,
            search_client: Any = None,
            mcp_pool: Any = None,
            max_fills_per_run: int | None = None,
        ) -> FillOutcome:
            if gap.dimension in failures:
                raise RuntimeError("boom")
            return FillOutcome(
                gap=gap,
                emitted=(_entry(dimension=gap.dimension),),
                rejected_taboos=(),
                exit_reason="text",
            )

        with patch(
            "bestseller.services.library_curator.ensure_coverage",
            new=spy_ensure,
        ), patch(
            "bestseller.services.library_curator.fill_gap",
            new=flaky_fill_gap,
        ):
            report = await run_curation(
                session,  # type: ignore[arg-type]
                settings,  # type: ignore[arg-type]
                plan=plan,
                search_client=NoopSearchClient(),
            )
        # 1 successful fill + 1 failed: sweep continued.
        assert len(report.audit.gaps) == 2
        assert len(report.fills) == 1
        assert report.fills[0].gap.dimension == "power_systems"


# ── Scheduled entry point ──────────────────────────────────────────────


class TestScheduledWeeklyAudit:
    async def test_no_op_when_flag_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BESTSELLER_ENABLE_MATERIAL_LIBRARY", raising=False)

        # Isolate from any developer .env that may have flipped the typed
        # flag on — the function must be a no-op when *both* gates are off.
        @dataclass
        class _PipelineOff:
            enable_material_library: bool = False
            curator_max_gaps_per_run: int = 6
            curator_max_fills_per_run: int = 5

        @dataclass
        class _SettingsOff:
            pipeline: _PipelineOff = field(default_factory=_PipelineOff)

        with patch(
            "bestseller.settings.get_settings",
            return_value=_SettingsOff(),
        ):
            # Should not attempt to open a session when flag is off.
            await scheduled_weekly_audit()

    async def test_runs_when_flag_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BESTSELLER_ENABLE_MATERIAL_LIBRARY", "1")

        # Fake get_server_session -> AsyncCM yielding a stub session.
        class _FakeCM:
            def __init__(self) -> None:
                self.commits = 0
                self.entered = False
                self.exited = False

            async def __aenter__(self) -> _StubSession:
                self.entered = True
                self.session = _StubSession()
                self.session.commits = 0  # type: ignore[attr-defined]

                async def _commit() -> None:
                    self.session.commits += 1  # type: ignore[attr-defined]
                    self.commits += 1

                self.session.commit = _commit  # type: ignore[attr-defined]
                return self.session

            async def __aexit__(self, exc_type, exc, tb) -> None:
                self.exited = True

        cm = _FakeCM()

        # Stub settings with a pipeline attribute so the new typed path
        # still gets the values it expects.
        @dataclass
        class _PipelineStub:
            enable_material_library: bool = True
            curator_max_gaps_per_run: int = 6
            curator_max_fills_per_run: int = 5

        @dataclass
        class _SettingsWithPipeline:
            pipeline: _PipelineStub = field(default_factory=_PipelineStub)

        async def fake_run_curation(
            session: Any,
            settings: Any,
            *,
            fill: bool = True,
            max_gaps: int | None = None,
            max_fills_per_run: int | None = None,
            **_: Any,
        ) -> CurationReport:
            assert fill is True
            assert max_gaps == 6
            assert max_fills_per_run == 5
            return CurationReport(
                audit=AuditResult(targets_checked=0, gaps=(), satisfied=()),
                fills=(),
            )

        with patch(
            "bestseller.infra.db.session.get_server_session",
            return_value=cm,
        ), patch(
            "bestseller.settings.get_settings",
            return_value=_SettingsWithPipeline(),
        ), patch(
            "bestseller.services.library_curator.run_curation",
            new=AsyncMock(side_effect=fake_run_curation),
        ):
            await scheduled_weekly_audit()
        assert cm.entered and cm.exited
        assert cm.commits == 1

    async def test_runs_when_pipeline_flag_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Typed settings path: pipeline.enable_material_library=True
        drives execution even without the env var override."""
        monkeypatch.delenv("BESTSELLER_ENABLE_MATERIAL_LIBRARY", raising=False)

        class _FakeCM:
            async def __aenter__(self) -> _StubSession:
                self.session = _StubSession()

                async def _commit() -> None:
                    return None

                self.session.commit = _commit  # type: ignore[attr-defined]
                return self.session

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

        @dataclass
        class _PipelineStub:
            enable_material_library: bool = True
            curator_max_gaps_per_run: int = 2
            curator_max_fills_per_run: int = 3

        @dataclass
        class _SettingsWithPipeline:
            pipeline: _PipelineStub = field(default_factory=_PipelineStub)

        async def fake_run_curation(
            session: Any,
            settings: Any,
            *,
            fill: bool = True,
            max_gaps: int | None = None,
            max_fills_per_run: int | None = None,
            **_: Any,
        ) -> CurationReport:
            # Values should come from settings.pipeline, not env.
            assert max_gaps == 2
            assert max_fills_per_run == 3
            return CurationReport(
                audit=AuditResult(targets_checked=0, gaps=(), satisfied=()),
                fills=(),
            )

        with patch(
            "bestseller.infra.db.session.get_server_session",
            return_value=_FakeCM(),
        ), patch(
            "bestseller.settings.get_settings",
            return_value=_SettingsWithPipeline(),
        ), patch(
            "bestseller.services.library_curator.run_curation",
            new=AsyncMock(side_effect=fake_run_curation),
        ):
            await scheduled_weekly_audit()
