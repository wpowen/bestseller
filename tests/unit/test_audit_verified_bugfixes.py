"""Regression tests for bugs confirmed by the IMPROVEMENT_ANALYSIS audit.

Each test reproduces a defect that was verified against the source and would
fail on the pre-fix code. Grouped by the audit id (P0-x / P1-xx).
"""

from __future__ import annotations

import types
import zlib
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# P0-7 — embedding hash must be deterministic across processes
# ---------------------------------------------------------------------------
def test_p0_7_hashed_embedding_is_process_stable() -> None:
    from bestseller.services.retrieval import build_hashed_embedding

    dims = 16
    emb = build_hashed_embedding("剑修丹田", dims)
    # Same input -> same vector (would drift under builtin salted hash()).
    assert build_hashed_embedding("剑修丹田", dims) == emb
    # Bucket placement is the stable crc32, not the salted builtin hash.
    expected_bucket = zlib.crc32("剑".encode("utf-8")) % dims
    single = build_hashed_embedding("剑", dims)
    assert single[expected_bucket] > 0.0


# ---------------------------------------------------------------------------
# P1-EH-13 — Chinese-numeral countdown extraction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3天", 3.0),
        ("七日之期", 7.0),
        ("还有三天", 3.0),
        ("十天", 10.0),
        ("二十天", 20.0),
        ("两百零三年", 203.0),
        ("没有数字", None),
    ],
)
def test_p1_eh13_extract_numeric_handles_chinese(value: str, expected: float | None) -> None:
    from bestseller.services.continuity import _extract_numeric

    assert _extract_numeric(value) == expected


# ---------------------------------------------------------------------------
# P1-EH-9 — None/garbage JSON payloads must not crash snapshot persistence
# ---------------------------------------------------------------------------
def test_p1_eh9_coerce_mapping_and_sequence() -> None:
    from bestseller.services.knowledge import _coerce_mapping, _coerce_sequence

    # Explicit null in scene exit_state must not raise (old code: dict(None)).
    assert _coerce_mapping(None) == {}
    assert _coerce_mapping(None, {"a": 1}) == {"a": 1}
    assert _coerce_mapping({"b": 2}) == {"b": 2}
    assert _coerce_mapping("garbage") == {}

    assert _coerce_sequence(None) == []
    assert _coerce_sequence(None, [1, 2]) == [1, 2]
    assert _coerce_sequence([3, 4]) == [3, 4]
    assert _coerce_sequence("garbage") == []


# ---------------------------------------------------------------------------
# P1-EH-6 — empty rejection_cause_map must not raise UnboundLocalError
# ---------------------------------------------------------------------------
def test_p1_eh6_regeneration_contract_empty_cause_map(monkeypatch: pytest.MonkeyPatch) -> None:
    from bestseller.services import methodology

    fake_contract = types.SimpleNamespace(
        target_platform="七猫",
        non_negotiables=["重建签约口径"],
        regeneration_decision_order=["立项", "开篇"],
        rejection_cause_map={},  # empty -> old code left `mapped` unbound
    )
    monkeypatch.setattr(methodology, "get_qimao_regeneration_contract", lambda: fake_contract)

    out = methodology.render_qimao_regeneration_contract(
        platform_target="qimao",
        language="zh-CN",
        rejection_reasons="字数不足",
    )
    assert isinstance(out, str)
    assert "已知拒稿原因" in out
    # With an empty cause map, the mapping line is simply omitted (no crash).
    assert "拒稿原因映射" not in out


# ---------------------------------------------------------------------------
# P1-IC-8 — parser default for block_below_target_length matches dataclass
# ---------------------------------------------------------------------------
def test_p1_ic8_block_below_target_length_default_is_false() -> None:
    from bestseller.services.quality_gates_config import (
        ReaderQualityGateConfig,
        _build_reader_quality_gate,
    )

    # dataclass-documented intent
    assert ReaderQualityGateConfig().block_below_target_length is False
    # parser fallback for a missing key now matches that intent
    assert _build_reader_quality_gate({}).block_below_target_length is False


# ---------------------------------------------------------------------------
# P0-5 — timeline contradiction query must not reference nonexistent columns
# ---------------------------------------------------------------------------
def test_p0_5_timeline_model_has_no_phantom_columns() -> None:
    from bestseller.infra.db.models import TimelineEventModel

    # The audited bug referenced these nonexistent attributes; guard against
    # anyone re-introducing them (which raised AttributeError at query build).
    assert not hasattr(TimelineEventModel, "chapter_number")
    assert not hasattr(TimelineEventModel, "description")


class _FakeResult:
    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[object]:
        return []


class _FakeSession:
    async def execute(self, _stmt: object) -> _FakeResult:
        # Reaching here means the SELECT statement compiled without an
        # AttributeError on a phantom column.
        return _FakeResult()


async def test_p0_5_numerical_contradiction_builds_and_runs() -> None:
    from bestseller.services.contradiction import _check_numerical_contradiction

    scene = types.SimpleNamespace(content_md="他32年前离开了宗门")
    violations, warnings = await _check_numerical_contradiction(
        _FakeSession(),  # type: ignore[arg-type]
        uuid4(),
        5,
        ["沈砚"],
        "zh-CN",
        scene=scene,
    )
    assert violations == []
    assert warnings == []
