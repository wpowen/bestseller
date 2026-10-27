"""An accepted numeric debt cannot waive hard terminal failures or text changes."""

from types import SimpleNamespace

import pytest

from bestseller.services import book_closure, pipelines

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("debt", [(), (1,)])
@pytest.mark.parametrize(
    "issue",
    [
        "empty_content",
        "canon_conflict",
        "source_mismatch",
        "anti_meta:failure",
        "unrecognized_hard_failure",
    ],
)
def test_terminal_failure_is_not_waived(monkeypatch, debt, issue):
    result = SimpleNamespace(passed=False, issues=[issue], errors=[], patched_text=None)
    monkeypatch.setattr(pipelines, "run_final_quality_gates", lambda **kw: result)
    conceded = []
    actual = book_closure._closure_quality_gate(debt, conceded)(chapter_number=1, content_md="正文")
    assert actual is result and not actual.passed
    assert conceded == []


@pytest.mark.parametrize("debt", [(), (1,)])
def test_evaluator_errors_and_patched_bytes_survive_the_adapter(monkeypatch, debt):
    result = SimpleNamespace(
        passed=False, issues=[], errors=["evaluator_error:offline"], patched_text="未经采用的正文"
    )
    calls = []

    def gate(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(pipelines, "run_final_quality_gates", gate)
    metadata = {"reader_judge": {"source": "bound"}}
    actual = book_closure._closure_quality_gate(debt, [])(
        chapter_number=1,
        content_md="正文",
        chapter_metadata=metadata,
    )
    assert actual is result and calls[0]["chapter_metadata"] is metadata


def test_unexpected_evaluator_exception_is_not_hidden(monkeypatch):
    def gate(**kwargs):
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(pipelines, "run_final_quality_gates", gate)
    with pytest.raises(RuntimeError, match="unavailable"):
        book_closure._closure_quality_gate((1,), [])(chapter_number=1)
