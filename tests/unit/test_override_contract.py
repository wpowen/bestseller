"""Phase C2 unit tests for override_contract service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bestseller.services.override_contract import (
    OverrideContract,
    OverrideRejected,
    OverrideStatus,
    OverrideStore,
    RationaleType,
    validate_override_proposal,
)


# ---------------------------------------------------------------------------
# RationaleType / OverrideStatus enums
# ---------------------------------------------------------------------------


class TestRationaleType:
    def test_seven_values(self) -> None:
        assert len(list(RationaleType)) == 7

    def test_string_roundtrip(self) -> None:
        assert RationaleType("ARC_TIMING") is RationaleType.ARC_TIMING
        assert RationaleType.ARC_TIMING.value == "ARC_TIMING"

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            RationaleType("NOT_A_RATIONALE")


class TestOverrideStatus:
    def test_three_values(self) -> None:
        assert {s.value for s in OverrideStatus} == {"active", "resolved", "expired"}


# ---------------------------------------------------------------------------
# OverrideContract dataclass
# ---------------------------------------------------------------------------


def _contract(
    *,
    chapter_no: int = 5,
    due_chapter: int = 10,
    status: OverrideStatus = OverrideStatus.ACTIVE,
    id: int | None = 1,
    violation_code: str = "LINE_GAP_OVER",
) -> OverrideContract:
    return OverrideContract(
        id=id,
        project_id="p1",
        chapter_no=chapter_no,
        violation_code=violation_code,
        rationale_type=RationaleType.ARC_TIMING,
        rationale_text="bridge chapter setup",
        payback_plan="大兑现 at ch10",
        due_chapter=due_chapter,
        status=status,
    )


class TestOverrideContract:
    def test_is_active(self) -> None:
        c = _contract()
        assert c.is_active
        assert not c.is_resolved

    def test_is_resolved(self) -> None:
        c = _contract(status=OverrideStatus.RESOLVED)
        assert c.is_resolved
        assert not c.is_active

    def test_is_overdue_true_when_past_due_and_active(self) -> None:
        c = _contract(chapter_no=5, due_chapter=10)
        assert c.is_overdue(11)
        assert not c.is_overdue(10)
        assert not c.is_overdue(9)

    def test_is_overdue_false_when_resolved(self) -> None:
        c = _contract(chapter_no=5, due_chapter=10, status=OverrideStatus.RESOLVED)
        assert not c.is_overdue(99)

    def test_to_dict_shape(self) -> None:
        c = _contract()
        d = c.to_dict()
        assert d["rationale_type"] == "ARC_TIMING"
        assert d["status"] == "active"
        assert d["due_chapter"] == 10
        assert "created_at" in d
        # isoformat string
        datetime.fromisoformat(d["created_at"])

    def test_frozen(self) -> None:
        c = _contract()
        with pytest.raises(Exception):
            c.status = OverrideStatus.RESOLVED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_override_proposal
# ---------------------------------------------------------------------------


_SOFT_CODES = frozenset({"LINE_GAP_OVER", "LINE_GAP_WARN", "PLEASURE_SETUP_PAYOFF_DEBT"})
_ALLOWED_RATIONALES = (
    "ARC_TIMING",
    "TRANSITIONAL_SETUP",
    "GENRE_CONVENTION",
)


def _valid_kwargs(**overrides):
    base = dict(
        violation_code="LINE_GAP_OVER",
        chapter_no=5,
        due_chapter=10,
        rationale="ARC_TIMING",
        rationale_text="bridge for main arc",
        payback_plan="收束 at ch10",
        soft_constraint_codes=_SOFT_CODES,
        allowed_rationale_types=_ALLOWED_RATIONALES,
        payback_window=10,
    )
    base.update(overrides)
    return base


class TestValidateProposal:
    def test_accepts_valid_proposal(self) -> None:
        rt = validate_override_proposal(**_valid_kwargs())
        assert rt is RationaleType.ARC_TIMING

    def test_accepts_enum_rationale(self) -> None:
        rt = validate_override_proposal(
            **_valid_kwargs(rationale=RationaleType.ARC_TIMING)
        )
        assert rt is RationaleType.ARC_TIMING

    def test_hard_violation_rejected(self) -> None:
        with pytest.raises(OverrideRejected, match="hard"):
            validate_override_proposal(**_valid_kwargs(violation_code="LANG_LEAK_CJK_IN_EN"))

    def test_rationale_not_in_whitelist(self) -> None:
        with pytest.raises(OverrideRejected, match="whitelist"):
            validate_override_proposal(**_valid_kwargs(rationale="EDITORIAL_INTENT"))

    def test_unknown_rationale_string(self) -> None:
        with pytest.raises(OverrideRejected, match="unknown"):
            validate_override_proposal(**_valid_kwargs(rationale="MADE_UP"))

    def test_empty_text_rejected(self) -> None:
        with pytest.raises(OverrideRejected, match="rationale_text"):
            validate_override_proposal(**_valid_kwargs(rationale_text="   "))

    def test_empty_payback_rejected(self) -> None:
        with pytest.raises(OverrideRejected, match="payback_plan"):
            validate_override_proposal(**_valid_kwargs(payback_plan=""))

    def test_due_not_after_chapter(self) -> None:
        with pytest.raises(OverrideRejected, match="due_chapter"):
            validate_override_proposal(**_valid_kwargs(chapter_no=10, due_chapter=10))

    def test_due_before_chapter(self) -> None:
        with pytest.raises(OverrideRejected, match="due_chapter"):
            validate_override_proposal(**_valid_kwargs(chapter_no=10, due_chapter=8))

    def test_exceeds_payback_window(self) -> None:
        with pytest.raises(OverrideRejected, match="payback window"):
            validate_override_proposal(
                **_valid_kwargs(chapter_no=5, due_chapter=20, payback_window=10)
            )

    def test_no_window_supplied_allows_long_span(self) -> None:
        rt = validate_override_proposal(
            **_valid_kwargs(chapter_no=5, due_chapter=50, payback_window=None)
        )
        assert rt is RationaleType.ARC_TIMING


# ---------------------------------------------------------------------------
# OverrideStore lifecycle
# ---------------------------------------------------------------------------


class TestOverrideStore:
    def test_create_assigns_id(self) -> None:
        store = OverrideStore()
        c = store.create(_contract(id=None))
        assert c.id == 1
        second = store.create(_contract(id=None, chapter_no=6))
        assert second.id == 2

    def test_create_preserves_explicit_id(self) -> None:
        store = OverrideStore()
        c = store.create(_contract(id=42))
        assert c.id == 42

    def test_list_active_filters_by_project(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None))
        store.create(
            OverrideContract(
                id=None,
                project_id="other",
                chapter_no=5,
                violation_code="LINE_GAP_OVER",
                rationale_type=RationaleType.ARC_TIMING,
                rationale_text="x",
                payback_plan="y",
                due_chapter=10,
            )
        )
        active = store.list_active("p1")
        assert len(active) == 1
        assert active[0].project_id == "p1"

    def test_list_active_excludes_resolved(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None))
        store.create(_contract(id=None, chapter_no=6))
        resolved = store.resolve(1)
        assert resolved is not None
        active = store.list_active("p1")
        assert len(active) == 1
        assert active[0].chapter_no == 6

    def test_list_overdue(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None, chapter_no=5, due_chapter=10))
        store.create(_contract(id=None, chapter_no=5, due_chapter=20))
        overdue = store.list_overdue("p1", current_chapter=11)
        assert len(overdue) == 1
        assert overdue[0].due_chapter == 10

    def test_resolve_missing_returns_none(self) -> None:
        store = OverrideStore()
        assert store.resolve(999) is None

    def test_resolve_already_resolved_returns_none(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None))
        first = store.resolve(1)
        assert first is not None
        second = store.resolve(1)
        assert second is None

    def test_expire_overdue_flips_status(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None, chapter_no=5, due_chapter=10))
        store.create(_contract(id=None, chapter_no=5, due_chapter=20))
        flipped = store.expire_overdue("p1", current_chapter=11)
        assert flipped == 1
        active = store.list_active("p1")
        assert len(active) == 1
        assert active[0].due_chapter == 20


# ---------------------------------------------------------------------------
# OverrideStore.as_lookup — integration shape for write_gate.OverrideLookup
# ---------------------------------------------------------------------------


class TestAsLookup:
    def test_lookup_matches_active_window(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None, chapter_no=5, due_chapter=10))
        lookup = store.as_lookup("p1")
        # Chapter within [5,10] with matching code → True.
        assert lookup("LINE_GAP_OVER", 7) is True
        assert lookup("LINE_GAP_OVER", 5) is True
        assert lookup("LINE_GAP_OVER", 10) is True

    def test_lookup_outside_window(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None, chapter_no=5, due_chapter=10))
        lookup = store.as_lookup("p1")
        assert lookup("LINE_GAP_OVER", 4) is False
        assert lookup("LINE_GAP_OVER", 11) is False

    def test_lookup_wrong_code(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None))
        lookup = store.as_lookup("p1")
        assert lookup("LANG_LEAK_CJK_IN_EN", 7) is False

    def test_lookup_wrong_project(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None))
        lookup = store.as_lookup("other")
        assert lookup("LINE_GAP_OVER", 7) is False

    def test_lookup_chapter_none_returns_false(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None))
        lookup = store.as_lookup("p1")
        assert lookup("LINE_GAP_OVER", None) is False

    def test_lookup_ignores_resolved(self) -> None:
        store = OverrideStore()
        store.create(_contract(id=None))
        store.resolve(1)
        lookup = store.as_lookup("p1")
        assert lookup("LINE_GAP_OVER", 7) is False


# ---------------------------------------------------------------------------
# write_gate integration — override downgrade block → audit_only
# ---------------------------------------------------------------------------


class TestWriteGateIntegration:
    def test_override_downgrades_block_to_audit_only(self) -> None:
        from bestseller.services.write_gate import DEFAULT_GATE_CONFIG, resolve_mode

        store = OverrideStore()
        store.create(_contract(id=None, chapter_no=15, due_chapter=20))
        lookup = store.as_lookup("p1")
        # Chapter > warmup (10) so LINE_GAP_OVER base mode is block; with the
        # active override covering ch15–20 it should downgrade to audit_only.
        mode = resolve_mode(
            "LINE_GAP_OVER",
            DEFAULT_GATE_CONFIG,
            chapter_no=17,
            override_lookup=lookup,
        )
        assert mode == "audit_only"

    def test_override_does_not_bypass_golden_three(self) -> None:
        from bestseller.services.write_gate import DEFAULT_GATE_CONFIG, resolve_mode

        store = OverrideStore()
        # Craft a contract nominally covering ch1 for ENDING_SENTENCE_WEAK.
        store.create(
            OverrideContract(
                id=None,
                project_id="p1",
                chapter_no=1,
                violation_code="ENDING_SENTENCE_WEAK",
                rationale_type=RationaleType.EDITORIAL_INTENT,
                rationale_text="x",
                payback_plan="y",
                due_chapter=4,
            )
        )
        lookup = store.as_lookup("p1")
        # Golden-three policy must win — resolve_mode returns block even with override.
        mode = resolve_mode(
            "ENDING_SENTENCE_WEAK",
            DEFAULT_GATE_CONFIG,
            chapter_no=2,
            override_lookup=lookup,
        )
        assert mode == "block"
