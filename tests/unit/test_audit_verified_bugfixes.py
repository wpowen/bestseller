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


# ---------------------------------------------------------------------------
# P1-SC-4 — concurrent pipeline starts must be serialized by a reservation
# ---------------------------------------------------------------------------
class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, key: str, val: str, nx: bool = False, ex: int | None = None
    ) -> object:
        if nx and key in self.store:
            return None
        self.store[key] = val
        return True

    async def get(self, key: str) -> object:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _BrokenRedis:
    async def set(self, *_a: object, **_k: object) -> object:
        raise RuntimeError("redis down")


async def test_p1_sc4_pipeline_start_reservation_serializes() -> None:
    from fastapi import HTTPException

    from bestseller.api.routers import pipelines as pr

    redis = _FakeRedis()
    project_id = uuid4()

    token = await pr._reserve_pipeline_start(redis, project_id)
    assert token is not None

    # Second concurrent start is rejected while the first is in flight.
    with pytest.raises(HTTPException) as exc_info:
        await pr._reserve_pipeline_start(redis, project_id)
    assert exc_info.value.status_code == 409

    # Releasing (e.g. enqueue failed) lets a retry through.
    await pr._release_pipeline_start(redis, project_id, token)
    assert await pr._reserve_pipeline_start(redis, project_id) is not None


async def test_p1_sc4_reservation_degrades_when_redis_unavailable() -> None:
    from bestseller.api.routers import pipelines as pr

    # Redis failure must not block legitimate work — fall through to DB guard.
    assert await pr._reserve_pipeline_start(_BrokenRedis(), uuid4()) is None


# ---------------------------------------------------------------------------
# P0-6 — defensive clamp: target_chapters=0 must not collapse to 1/volume
# ---------------------------------------------------------------------------
def test_p0_6_zero_target_uses_sane_default() -> None:
    from bestseller.services.world_expansion import (
        _DEFAULT_CHAPTERS_PER_VOLUME,
        _estimate_volume_chapter_targets,
    )

    project = types.SimpleNamespace(target_chapters=0)
    volumes = [
        types.SimpleNamespace(volume_number=i, target_chapter_count=None)
        for i in (1, 2, 3)
    ]
    targets = _estimate_volume_chapter_targets(project, volumes)
    # Old behaviour gave every volume exactly 1 chapter; now each gets a sane
    # default instead.
    assert all(count >= _DEFAULT_CHAPTERS_PER_VOLUME - 1 for count in targets.values())
    assert sum(targets.values()) == 3 * _DEFAULT_CHAPTERS_PER_VOLUME


# ---------------------------------------------------------------------------
# P0-13 — Web Studio auth gate (open when unset, enforced when configured)
# ---------------------------------------------------------------------------
def test_p0_13_web_auth_logic() -> None:
    from bestseller.web.server import _provided_web_token, _web_auth_ok

    # No token configured -> open (backward-compatible local default).
    assert _web_auth_ok("", "anything") is True
    assert _web_auth_ok("", "") is True

    # Configured token must match exactly.
    assert _web_auth_ok("s3cret", "s3cret") is True
    assert _web_auth_ok("s3cret", "wrong") is False
    assert _web_auth_ok("s3cret", "") is False

    def header_getter(d: dict[str, str]):
        return lambda key: d.get(key)

    assert _provided_web_token(header_getter({"X-Web-Token": "abc"})) == "abc"
    assert _provided_web_token(header_getter({"Authorization": "Bearer xyz"})) == "xyz"
    assert _provided_web_token(header_getter({})) == ""


# ---------------------------------------------------------------------------
# P0-3 — greenfield alembic bootstrap (0001 renders live metadata)
# ---------------------------------------------------------------------------
class _ScalarRes:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value


class _FakeMigrationConn:
    def __init__(self, dialect: str, results: list[object]) -> None:
        self.dialect = types.SimpleNamespace(name=dialect)
        self._results = list(results)
        self.executed: list[tuple[str, object]] = []

    def execute(self, stmt: object, params: object = None) -> _ScalarRes:
        self.executed.append((str(stmt), params))
        return _ScalarRes(self._results.pop(0) if self._results else None)


def test_p0_3_greenfield_detection() -> None:
    from bestseller.infra.db.migration_bootstrap import database_is_greenfield

    # fresh PG: no projects table, no stamped revision
    assert database_is_greenfield(_FakeMigrationConn("postgresql", [None, None])) is True
    # existing application schema -> use the migration chain
    assert database_is_greenfield(_FakeMigrationConn("postgresql", [1])) is False
    # already stamped (projects absent but a revision present) -> not greenfield
    assert database_is_greenfield(_FakeMigrationConn("postgresql", [None, "0031_x"])) is False
    # non-postgres backend never takes the greenfield path
    assert database_is_greenfield(_FakeMigrationConn("sqlite", [])) is False


def test_p0_3_baseline_creates_schema_and_stamps_head() -> None:
    from bestseller.infra.db.migration_bootstrap import baseline_to_head

    conn = _FakeMigrationConn("postgresql", [])
    baseline_to_head(conn, head_revision="0031_fanqie_market_profiles")

    sqls = [sql for sql, _params in conn.executed]
    assert any("CREATE EXTENSION" in sql for sql in sqls)  # extensions emitted
    assert any("CREATE TABLE" in sql and "projects" in sql for sql in sqls)  # tables emitted
    # head stamped last with the revision bound as a param
    last_sql, last_params = conn.executed[-1]
    assert "alembic_version" in last_sql
    assert last_params == {"rev": "0031_fanqie_market_profiles"}
