"""Unit tests for the ambient progress emitter (services.progress_context)."""

from __future__ import annotations

import pytest

from bestseller.services.progress_context import (
    TIER_ACTIVITY,
    TIER_KEY,
    TIER_MILESTONE,
    bind_progress,
    current_emitter,
    emit_activity,
    emit_gate_result,
    emit_milestone,
    set_ambient,
)


def test_emit_is_noop_when_unbound() -> None:
    # No emitter bound → helpers must be safe no-ops, never raising.
    assert current_emitter() is None
    emit_activity("anything", {"a": 1})
    emit_milestone("anything")
    assert current_emitter() is None


def test_bind_captures_events_and_tags_tier() -> None:
    seen: list[tuple[str, dict]] = []
    with bind_progress(lambda stage, payload: seen.append((stage, payload))):
        emit_activity("heat_search_started", {"genre": "g"})
        emit_milestone("methodology_selected", {"framework": "F"})

    assert [s for s, _ in seen] == ["heat_search_started", "methodology_selected"]
    assert seen[0][1][TIER_KEY] == TIER_ACTIVITY
    assert seen[1][1][TIER_KEY] == TIER_MILESTONE
    # Original payload values are preserved alongside the tier hint.
    assert seen[0][1]["genre"] == "g"
    assert seen[1][1]["framework"] == "F"


def test_bind_resets_binding_on_exit() -> None:
    assert current_emitter() is None
    with bind_progress(lambda *_: None):
        assert current_emitter() is not None
    assert current_emitter() is None


def test_nested_bindings_are_isolated() -> None:
    outer: list[str] = []
    inner: list[str] = []
    with bind_progress(lambda s, _p: outer.append(s)):
        emit_activity("outer_before")
        with bind_progress(lambda s, _p: inner.append(s)):
            emit_activity("inner_only")
        emit_activity("outer_after")

    assert inner == ["inner_only"]
    assert outer == ["outer_before", "outer_after"]


def test_none_binding_is_noop() -> None:
    # Binding ``None`` (e.g. no progress callback available) must not raise and
    # leaves helpers as no-ops within the block.
    with bind_progress(None):
        assert current_emitter() is None
        emit_activity("z")
        emit_milestone("z")


def test_emit_never_raises_when_callback_throws() -> None:
    def boom(_stage: str, _payload: dict | None) -> None:
        raise RuntimeError("sink exploded")

    # A throwing sink must not break the pipeline — emit swallows the error.
    with bind_progress(boom):
        emit_activity("safe_activity")
        emit_milestone("safe_milestone")


def test_does_not_mutate_caller_payload() -> None:
    captured: dict[str, dict] = {}
    original = {"k": "v"}
    with bind_progress(lambda s, p: captured.__setitem__(s, p)):
        emit_activity("ev", original)

    # The tier hint goes onto a copy, not the caller's dict.
    assert TIER_KEY not in original
    assert captured["ev"][TIER_KEY] == TIER_ACTIVITY


def test_emit_gate_result_blocked_is_milestone() -> None:
    seen: list[tuple[str, dict]] = []
    with bind_progress(lambda s, p: seen.append((s, p))):
        emit_gate_result(
            "anti_meta_gate",
            verdict="blocked",
            severity="critical",
            score=50,
            reasons=["a", "b", "c", "d", "e"],
            chapter=7,
        )
    assert len(seen) == 1
    stage, payload = seen[0]
    assert stage == "anti_meta_gate_evaluated"
    assert payload[TIER_KEY] == TIER_MILESTONE  # blocked → durable milestone axis
    assert payload["blocked"] is True
    assert payload["verdict"] == "blocked"
    assert payload["severity"] == "critical"
    assert payload["score"] == 50.0
    assert payload["target"] == "ch:7"
    assert payload["chapter_number"] == 7
    # reasons capped at 3 and coerced to strings
    assert payload["reasons"] == ["a", "b", "c"]


def test_emit_gate_result_pass_is_activity() -> None:
    seen: list[tuple[str, dict]] = []
    with bind_progress(lambda s, p: seen.append((s, p))):
        emit_gate_result("hook_echo_gate", verdict="pass", score=88.0, chapter=2)
    stage, payload = seen[0]
    assert stage == "hook_echo_gate_evaluated"
    assert payload[TIER_KEY] == TIER_ACTIVITY  # passing → high-frequency activity
    assert payload["blocked"] is False


def test_emit_gate_result_rewrite_counts_as_blocked() -> None:
    seen: list[tuple[str, dict]] = []
    with bind_progress(lambda s, p: seen.append((s, p))):
        emit_gate_result("chapter_review", verdict="rewrite", chapter=3)
    assert seen[0][1]["blocked"] is True
    assert seen[0][1][TIER_KEY] == TIER_MILESTONE


def test_emit_gate_result_noop_when_unbound() -> None:
    # Must not raise when no emitter is bound.
    emit_gate_result("x_gate", verdict="blocked", chapter=1)


def test_set_ambient_binds_without_reset() -> None:
    seen: list[str] = []
    try:
        set_ambient(lambda s, _p: seen.append(s))
        assert current_emitter() is not None
        emit_activity("after_set_ambient")
        assert seen == ["after_set_ambient"]
    finally:
        set_ambient(None)  # clean up the unscoped binding for test isolation
    assert current_emitter() is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
