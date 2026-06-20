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


# ---------------------------------------------------------------------------
# P1-SC-1 — publish_now must verify the schedule belongs to the path project
# ---------------------------------------------------------------------------
class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _SeqSession:
    """Returns queued scalar results, one per execute() call."""

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)

    async def execute(self, _stmt: object) -> _ScalarResult:
        return _ScalarResult(self._results.pop(0))


async def test_p1_sc1_publish_now_rejects_foreign_schedule() -> None:
    from fastapi import HTTPException

    from bestseller.api.routers import publishing

    project = types.SimpleNamespace(id=uuid4(), slug="proj")
    # 1st execute -> project found; 2nd execute -> schedule NOT owned (None)
    session = _SeqSession([project, None])

    with pytest.raises(HTTPException) as exc_info:
        await publishing.publish_now("proj", uuid4(), session, None, None)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 404


async def test_p1_sc1_publish_now_allows_owned_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    import bestseller.scheduler.jobs as jobs
    from bestseller.api.routers import publishing

    project = types.SimpleNamespace(id=uuid4(), slug="proj")
    schedule = types.SimpleNamespace(id=uuid4(), project_id=project.id)
    session = _SeqSession([project, schedule])

    published: dict[str, object] = {}

    async def _fake_publish_next_chapter(**kwargs: object) -> dict:
        published.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(jobs, "publish_next_chapter", _fake_publish_next_chapter)

    out = await publishing.publish_now("proj", schedule.id, session, object(), None)  # type: ignore[arg-type]
    assert out == {"published": {"ok": True}}
    assert published.get("schedule_id") == schedule.id


# ---------------------------------------------------------------------------
# P0-11 — retention gate must surface a degraded signal when sub-checks crash
# ---------------------------------------------------------------------------
def test_p0_11_retention_degraded_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    from bestseller.services import retention_safety_gate as rsg

    # Lower the systemic threshold to 1 so a single forced crash trips it.
    monkeypatch.setattr(rsg, "_RETENTION_DEGRADED_MIN_ERRORS", 1)

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("forced check failure")

    monkeypatch.setattr(rsg, "check_hook_echo", _boom)

    report = rsg.evaluate_retention_safety(
        chapter_position=5,
        chapter_text="正文内容" * 80,
        prev_chapter_text="上一章内容" * 80,
        skip_signature=True,
        skip_exposition=True,
        skip_cast_compliance=True,
        skip_timeline=True,
        skip_scene_coherence=True,
        skip_character_role=True,
        skip_dialogue_voice=True,
        skip_chapter_length=True,
        skip_word_count_truth=True,
        skip_duplicate_check=True,
        skip_payoff_ledger=True,
    )
    codes = [f.code for f in report.findings]
    assert rsg.RETENTION_GATE_DEGRADED_CODE in codes


# ---------------------------------------------------------------------------
# P1-EH-2 — override-contract signing must roll back the contract if its debt
# fails (no orphaned debt-less contract).
# ---------------------------------------------------------------------------
async def test_p1_eh2_override_savepoint_rolls_back_on_debt_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bestseller.services.regen_loop as regen_loop
    from bestseller.services import drafts

    proposal = types.SimpleNamespace(
        chapter_no=3,
        violation_code="ARC_TIMING_X",
        suggested_rationale_type="ARC_TIMING",
        rationale_text="rationale",
        suggested_payback_plan="payback",
        suggested_due_chapter=10,
    )
    monkeypatch.setattr(
        regen_loop, "propose_overrides_from_report", lambda *_a, **_k: [proposal]
    )

    class _Nested:
        def __init__(self, sess: "_OrphanSession") -> None:
            self._sess = sess

        async def __aenter__(self) -> "_Nested":
            self._sess.nested_entered += 1
            return self

        async def __aexit__(self, exc_type: object, *_rest: object) -> bool:
            if exc_type is not None:
                self._sess.nested_rolled_back += 1
            return False  # propagate

    class _OrphanSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.flush_calls = 0
            self.nested_entered = 0
            self.nested_rolled_back = 0

        def begin_nested(self) -> _Nested:
            return _Nested(self)

        def add(self, obj: object) -> None:
            self.added.append(obj)

        async def flush(self) -> None:
            self.flush_calls += 1
            if self.flush_calls == 2:  # the debt flush fails
                raise RuntimeError("debt flush boom")

    sess = _OrphanSession()
    out = await drafts._auto_sign_override_contracts(
        sess,  # type: ignore[arg-type]
        project_id=uuid4(),
        chapter_number=3,
        blocking_violations=(),
        soft_constraint_codes=frozenset(),
        interest_rate=0.1,
        payback_window=5,
    )
    assert out == 0  # debt failed -> proposal not counted as persisted
    assert sess.nested_entered == 1  # contract+debt wrapped in one savepoint
    assert sess.nested_rolled_back == 1  # savepoint rolled back on the failure
