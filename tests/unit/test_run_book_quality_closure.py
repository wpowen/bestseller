from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.book_quality_closure import BookClosureReport, LLMPreflightReport

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_book_quality_closure.py"
_spec = importlib.util.spec_from_file_location("closure_runner", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
closure_runner = _module


pytestmark = pytest.mark.unit


def _stub_args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(
        slug="human-nature-game-1779104696",
        all=False,
        platform="framework",
        priority="critical,high",
        round_size=2,
        continuation_size=0,
        max_rounds=5,
        preflight_timeout=2,
        repair_task_timeout=0,
        continuation_timeout=0,
        max_books=0,
        include_verify=False,
        replace_existing=False,
        execute=True,
        dry_run=False,
        json=False,
        **kwargs,
    )


def _make_report(signature_score: float, round_index: int, execution_reason: str = "") -> BookClosureReport:
    return BookClosureReport(
        slug="human-nature-game-1779104696",
        status="repairing",
        next_action="execute_next_repair_round",
        after_acceptance={
            "acceptance": {
                "passed": False,
                "metrics": {
                    "quality_score": signature_score,
                    "chapters_blocked": 0,
                    "repair_task_count": 0,
                    "current_chapters": 57,
                    "missing_chapters": 0,
                    "draftless_chapters": 0,
                },
            },
            "scorecard": {
                "quality_score": signature_score,
                "chapters_blocked": 0,
                "repair_task_count": 4,
                "current_chapters": 57,
                "missing_chapters": 0,
                "draftless_chapters": 0,
            },
            "category": "suspense",
            "target_chapters": 100,
        },
        execution={"reason": execution_reason},
        task_sync={"task_count": 4},
        report_paths={"book_quality_closure": "tmp/book-quality-closure/report.json"},
        model_preflight=LLMPreflightReport(ready=True, provider="deepseek", model_name="deepseek-chat"),
    )


def test_acceptance_gap_repair_specs_turn_scorecard_failures_into_chapter_tasks() -> None:
    specs = closure_runner._acceptance_gap_repair_specs_from_scorecard(
        slug="human-nature-game-1779104692",
        acceptance_payload={
            "acceptance": {
                "passed": False,
                "metrics": {
                    "quality_score": 76.4,
                    "length_cv": 0.15,
                    "hype_missing_chapters": 2,
                },
                "thresholds": {
                    "min_scorecard_quality_score": 80.0,
                    "max_length_cv": 0.10,
                },
            },
            "scorecard": {
                "quality_score": 76.4,
                "length_cv": 0.15,
                "hype_missing_chapters": 2,
                "golden_three_weak": True,
            },
        },
        chapter_rows=[
            {
                "chapter_number": 1,
                "target_word_count": 2200,
                "word_count": 1800,
                "hype_type": "",
            },
            {
                "chapter_number": 2,
                "target_word_count": 2200,
                "word_count": 2950,
                "hype_type": "reversal",
            },
            {
                "chapter_number": 4,
                "target_word_count": 2200,
                "word_count": 2200,
                "hype_type": None,
            },
        ],
    )

    by_chapter = {spec.chapter_number: set(spec.cause_ids) for spec in specs}
    by_audit = {spec.chapter_number: dict(spec.audit_row) for spec in specs}

    assert by_chapter[1] == {
        "GOLDEN_THREE_WEAK",
        "HYPE_ASSIGNMENT_MISSING",
        "LENGTH_STABILITY_BELOW_BAR",
        "SCORECARD_BELOW_ACCEPTANCE_BAR",
    }
    assert by_chapter[2] == {
        "GOLDEN_THREE_WEAK",
        "LENGTH_STABILITY_BELOW_BAR",
        "SCORECARD_BELOW_ACCEPTANCE_BAR",
    }
    assert by_chapter[4] == {
        "HYPE_ASSIGNMENT_MISSING",
        "SCORECARD_BELOW_ACCEPTANCE_BAR",
    }
    assert by_audit[1]["target_word_count"] == "2200"
    assert by_audit[1]["char_count"] == "1800"
    assert by_audit[1]["word_count_reason"] == "underflow"
    assert by_audit[2]["word_count_reason"] == "overflow"


@pytest.mark.asyncio
async def test_run_stalls_after_repeated_metric_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _stub_args()
    model_preflight = LLMPreflightReport(ready=True, provider="deepseek", model_name="deepseek-chat")
    call_index = {"index": 0}

    async def _fake_preflight(*args, **kwargs):
        return model_preflight

    def _fake_load_settings():
        return SimpleNamespace(output=SimpleNamespace(base_dir="/tmp"))

    async def _fake_run_one_book(*_args, **_kwargs):
        call_index["index"] += 1
        return _make_report(70.0, call_index["index"])

    # deterministic loop and no network/provider calls
    monkeypatch.setattr(closure_runner, "load_settings", _fake_load_settings)
    monkeypatch.setattr(closure_runner, "_run_preflight", _fake_preflight)
    monkeypatch.setattr(closure_runner, "_run_one_book", _fake_run_one_book)

    result = await closure_runner._run(args)
    assert result["reports"][0]["loop"]["stop_reason"] == "no_metric_progress"
    assert result["reports"][0]["loop"]["rounds"][-1]["round"] == 4


@pytest.mark.asyncio
async def test_run_stops_when_no_pending_rewrite_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _stub_args()
    args.max_rounds = 4
    model_preflight = LLMPreflightReport(ready=True, provider="deepseek", model_name="deepseek-chat")

    async def _fake_preflight(*args, **kwargs):
        return model_preflight

    def _fake_load_settings():
        return SimpleNamespace(output=SimpleNamespace(base_dir="/tmp"))

    async def _fake_run_one_book(*_args, **_kwargs):
        return _make_report(70.0, 1, execution_reason="no_pending_rewrite_tasks")

    monkeypatch.setattr(closure_runner, "load_settings", _fake_load_settings)
    monkeypatch.setattr(closure_runner, "_run_preflight", _fake_preflight)
    monkeypatch.setattr(closure_runner, "_run_one_book", _fake_run_one_book)

    result = await closure_runner._run(args)
    assert result["reports"][0]["loop"]["stop_reason"] == "no_executable_repair_tasks"
    assert result["reports"][0]["loop"]["rounds"][-1]["round"] == 1


@pytest.mark.asyncio
async def test_filter_task_ids_by_status_only_keeps_pending_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    project = SimpleNamespace(id=uuid4())
    pending_id = uuid4()
    stale_id = uuid4()

    class _Result:
        def __init__(self, payload: list[object]) -> None:
            self._payload = payload

        def scalars(self) -> list[object]:
            return self._payload

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, statement: object) -> _Result:
            return _Result([pending_id])

    class _SessionScope:
        async def __aenter__(self) -> "_Session":
            return _Session()

        async def __aexit__(self, *args: object) -> None:
            return None

    def _fake_session_scope(*args: object, **kwargs: object) -> _SessionScope:
        return _SessionScope()

    async def _fake_get_project_by_slug(_session: object, _slug: str) -> SimpleNamespace:
        return project

    monkeypatch.setattr(closure_runner, "session_scope", _fake_session_scope)
    monkeypatch.setattr(closure_runner, "get_project_by_slug", _fake_get_project_by_slug)

    filtered = await closure_runner._filter_task_ids_by_status(
        SimpleNamespace(output=SimpleNamespace(base_dir="/tmp")),
        "human-nature-game-1779104692",
        [str(pending_id), str(stale_id)],
    )

    assert filtered == [str(pending_id)]
