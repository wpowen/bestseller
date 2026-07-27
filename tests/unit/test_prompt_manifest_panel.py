"""B3「生效片段」manifest loader + panel contract.

The panel is the plan's answer to "用户选项弱耦合 / 用户以为生效, writer 未必收到"
(§2.4) — it is the only surface that shows what the writer prompt ACTUALLY
contained, rather than what the static config implies. §11.5 P0-P makes it the
first item precisely so lean/full claims stop being guesses.

Covered here: the DB→payload loader (previously untested) and the panel's
red-flag requirement for dropped blocks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from bestseller.services.prompt_manifest import load_chapter_prompt_manifest


pytestmark = pytest.mark.unit


class _FakeProject:
    def __init__(self, slug: str) -> None:
        self.id = uuid4()
        self.slug = slug


class _FakeRun:
    def __init__(self, metadata: dict[str, Any] | None) -> None:
        self.id = uuid4()
        self.metadata_json = metadata
        self.created_at = None


class _Result:
    def __init__(self, value: Any, *, many: bool = False) -> None:
        self._value = value
        self._many = many

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> Any:
        return self

    def all(self) -> Any:
        return self._value if self._many else []


class _FakeSession:
    """Returns the project for the first execute(), then the llm_run rows."""

    def __init__(self, project: Any, runs: list[Any]) -> None:
        self._project = project
        self._runs = runs
        self._calls = 0

    async def execute(self, stmt: Any) -> Any:
        self._calls += 1
        if self._calls == 1:
            return _Result(self._project)
        return _Result(self._runs, many=True)


def _report_run(chapter: int, **report: Any) -> _FakeRun:
    return _FakeRun(
        {
            "chapter_number": chapter,
            "prompt_mode": "legacy",
            "generation_mode": "chapter",
            "prompt_compiler_report": {
                "kept": report.get("kept", ["canon", "beats"]),
                "dropped": report.get("dropped", ["acceptance_contract"]),
                "drop_reasons": report.get(
                    "drop_reasons", {"acceptance_contract": "lean profile"}
                ),
                "block_sizes": report.get("block_sizes", {"canon": 420}),
                "final_hash": report.get("final_hash", "abc123def456789012"),
            },
        }
    )


@pytest.mark.asyncio
async def test_loader_returns_report_for_matching_chapter() -> None:
    session = _FakeSession(_FakeProject("book-a"), [_report_run(7)])

    payload = await load_chapter_prompt_manifest(
        session, project_slug="book-a", chapter_number=7
    )

    assert payload["found"] is True
    assert payload["chapter_number"] == 7
    assert payload["kept"] == ["canon", "beats"]
    assert payload["dropped"] == ["acceptance_contract"]
    assert payload["drop_reasons"]["acceptance_contract"] == "lean profile"
    assert payload["prompt_mode"] == "legacy"


@pytest.mark.asyncio
async def test_loader_reports_not_found_rather_than_raising() -> None:
    """A chapter that has not been written yet must render an empty panel, not
    a 500 — the panel is polled while the book is still being generated."""

    session = _FakeSession(_FakeProject("book-a"), [_report_run(3)])

    payload = await load_chapter_prompt_manifest(
        session, project_slug="book-a", chapter_number=9
    )

    assert payload["ok"] is True
    assert payload["found"] is False
    assert payload["kept"] == []
    assert payload["dropped"] == []


@pytest.mark.asyncio
async def test_loader_skips_runs_without_a_compiler_report() -> None:
    """Writer runs predating provenance-aware compilation carry no report;
    they must not shadow a later run that does."""

    stale = _FakeRun({"chapter_number": 7})
    session = _FakeSession(_FakeProject("book-a"), [stale, _report_run(7)])

    payload = await load_chapter_prompt_manifest(
        session, project_slug="book-a", chapter_number=7
    )

    assert payload["found"] is True
    assert payload["kept"] == ["canon", "beats"]


@pytest.mark.asyncio
async def test_loader_rejects_unknown_project() -> None:
    session = _FakeSession(None, [])

    with pytest.raises(ValueError, match="Project not found"):
        await load_chapter_prompt_manifest(
            session, project_slug="nope", chapter_number=1
        )


def _panel_source() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "bestseller"
        / "web"
        / "novel_quickstart.html"
    )
    return path.read_text(encoding="utf-8")


def test_panel_is_wired_into_the_progress_view() -> None:
    source = _panel_source()
    assert 'id="pvManifestSec"' in source
    assert "async function loadPromptManifest(" in source
    # Defining the renderer without calling it was the actual half-built state.
    assert source.count("refreshPromptManifestSelector(") >= 2


def test_panel_flags_dropped_blocks_in_red() -> None:
    """Plan §5.2 B3: "用户选项映射到可见 prompt 切片；未注入标红".

    Plain-text kept/dropped lists satisfy neither half — a dropped block is the
    one thing an operator is scanning for, so it must be visually distinct.
    """

    source = _panel_source()
    start = source.index("async function loadPromptManifest(")
    body = source[start : start + 4000]

    assert "pm-dropped" in body, "dropped blocks need their own styled class"
    assert ".pm-dropped" in source, "the pm-dropped class needs a CSS rule"
    assert "innerHTML" in body, "styling requires markup, not textContent"
