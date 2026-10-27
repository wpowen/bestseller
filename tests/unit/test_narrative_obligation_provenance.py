"""Planning projection is not adopted history or a mandatory chapter promise."""

import copy
from types import SimpleNamespace

import pytest

from bestseller.services import narrative
from bestseller.services.chekhov_emphasis_gate import evaluate_chekhov_emphasis
from bestseller.services.hook_ledger import build_hook_ledger_view
from bestseller.services.narrative_obligation_migration import (
    apply_graph_snapshot_repair, bind_explicit_obligation, graph_content_hash,
    propose_graph_snapshot_repair,
)

pytestmark = pytest.mark.unit


def _history(first="A later opponent adapts to public information", second="The mechanism continues under pressure"):
    source = {"id": "source-1", "project_id": "project-1", "artifact_type": "volume_plan",
        "status": "approved", "created_at": "2026-01-01T01:00:00+00:00", "content": {"volumes": [{
            "volume_number": 1, "title": "First leg", "foreshadowing_planted": [f"[S1] {first}", second],
            "foreshadowing_paid_off": ["[S1] A first verification is planned"],
        }]}}
    base = {"project_id": "project-1", "plot_arc_id": "arc-1", "metadata": {},
            "created_at": "2026-01-01T02:00:00+00:00", "updated_at": "2026-01-01T02:00:00+00:00"}
    clues = []
    for index, label in enumerate([first, second], 1):
        clues.append({**copy.deepcopy(base), "id": f"c{index}", "clue_code": f"clue-{index:03d}",
            "label": label, "description": label, "clue_type": "foreshadow",
            "planted_in_volume_number": 1, "planted_in_chapter_number": 1,
            "planted_in_scene_number": 1, "expected_payoff_by_volume_number": 1,
            "expected_payoff_by_chapter_number": 1, "expected_payoff_by_scene_number": 2,
            "actual_paid_off_chapter_number": 1 if index == 1 else None,
            "actual_paid_off_scene_number": 2 if index == 1 else None,
            "status": "resolved" if index == 1 else "planted",
            "reveal_guard": "在对应 payoff 前不得完整揭露。"})
    payoff = {**copy.deepcopy(base), "id": "p1", "payoff_code": "payoff-001", "source_clue_id": "c1",
        "label": "A first verification is planned", "description": "A first verification is planned",
        "target_volume_number": 1, "target_chapter_number": 1, "target_scene_number": 2,
        "actual_chapter_number": 1, "actual_scene_number": 2, "status": "paid_off"}
    snapshot = {"clues": clues, "payoffs": [payoff], "plot_arcs": [{"keep": "original arc"}],
                "arc_beats": [{"keep": "original beat"}]}
    windows = [{"volume_number": 1, "first_chapter": 1, "last_chapter": 1,
                "first_scene": 1, "last_scene": 2}]
    return snapshot, source, windows


def _apply(snapshot, review, sources):
    return apply_graph_snapshot_repair(snapshot, review, approved_sources=sources,
        expected_snapshot_hash=review["expected_snapshot_hash"],
        expected_source_hashes=review["proposal"]["source_hashes"],
        actor="offline-operator", evidence={"inspection": "exact producer and source reviewed"})


@pytest.mark.parametrize("first,second", [
    ("更高压力下再次检验机制", "对手依据公开事实改变行动"),
    ("An unanswered message is planned", "A future relationship changes"),
    ("La promesse sera reprise", "Une future pression augmente"),
])
def test_exact_legacy_projections_are_superseded_without_erasing_original_values(first, second):
    snapshot, source, windows = _history(first, second)
    before = copy.deepcopy(snapshot)
    review = propose_graph_snapshot_repair(snapshot, approved_sources=[source], windows=windows)
    assert [item["classification"] for item in review["proposal"]["audit"]] == ["repair_candidate"] * 3
    candidate = _apply(snapshot, review, [source])
    assert snapshot == before
    assert candidate["plot_arcs"] == before["plot_arcs"] and candidate["arc_beats"] == before["arc_beats"]
    for table in ("clues", "payoffs"):
        for old, new in zip(before[table], candidate[table], strict=True):
            assert new["status"] == "reference"
            receipt = new["metadata"]["narrative_obligation"]
            assert receipt["original_row"] == old
            assert receipt["original_row_hash"] == graph_content_hash(old)
            assert {k: v for k, v in new.items() if k not in {"status", "metadata"}} == {
                k: v for k, v in old.items() if k not in {"status", "metadata"}}
    assert _apply(candidate, review, [source]) == candidate
    assert candidate["payoffs"][0]["actual_chapter_number"] == 1  # archived, not scrubbed
    assert build_hook_ledger_view([SimpleNamespace(**r) for r in candidate["clues"]], current_chapter=30) == ()


@pytest.mark.parametrize("fault", ["authored", "later_update", "different_text", "unapproved", "future_source", "no_source"])
def test_unsupported_or_authored_rows_stay_unknown(fault):
    snapshot, source, windows = _history()
    if fault == "authored":
        snapshot["clues"][0]["metadata"] = {"author_note": "Actually planted on page"}
    elif fault == "later_update":
        snapshot["clues"][0]["actual_paid_off_chapter_number"] = 4
    elif fault == "different_text":
        snapshot["clues"][0]["description"] += " separately authored history"
    elif fault == "unapproved":
        source["status"] = "needs_review"
    elif fault == "future_source":
        source["created_at"] = "2026-01-01T03:00:00+00:00"
    review = propose_graph_snapshot_repair(snapshot, approved_sources=[] if fault == "no_source" else [source], windows=windows)
    assert review["proposal"]["audit"][0]["classification"] == "unknown"
    assert _apply(snapshot, review, [] if fault == "no_source" else [source])["clues"][0] == snapshot["clues"][0]


@pytest.mark.parametrize("fault", ["source", "row", "proposal", "snapshot"])
def test_stale_source_row_or_candidate_tampering_fails_atomically(fault):
    snapshot, source, windows = _history()
    review = propose_graph_snapshot_repair(snapshot, approved_sources=[source], windows=windows)
    if fault == "source":
        source["content"]["volumes"][0]["title"] = "Changed source bytes"
    elif fault == "row":
        snapshot["clues"][0]["actual_paid_off_chapter_number"] = 3
    elif fault == "proposal":
        review["proposal"]["candidate_rows"][0]["row"]["label"] = "Invented replacement"
    else:
        snapshot["arc_beats"][0]["keep"] = "Changed unrelated state"
    before = copy.deepcopy(snapshot)
    with pytest.raises(ValueError):
        _apply(snapshot, review, [source])
    assert snapshot == before


def test_new_builder_does_not_invent_first_chapter_actuals_or_deadlines():
    chapter = SimpleNamespace(id="ch-1", volume_id="v-1", chapter_number=1, hook_description="A note appears")
    entry = SimpleNamespace(foreshadowing_planted=["A future consequence", "A later countermeasure"],
                            foreshadowing_paid_off=["A first result is planned"])
    clues, payoffs = narrative._build_clues_and_payoffs(arc_ids={},
        volumes=[SimpleNamespace(id="v-1", volume_number=1)], chapters=[chapter],
        scenes_by_chapter={"ch-1": [SimpleNamespace(scene_number=1)]}, volume_entries={1: entry})
    assert len(clues) == 2 and len(payoffs) == 1
    assert all(row["status"] == "reference" for row in clues + payoffs)
    assert all(row["planted_in_chapter_number"] is None and row["expected_payoff_by_chapter_number"] is None
               and row["actual_paid_off_chapter_number"] is None for row in clues)
    assert payoffs[0]["actual_chapter_number"] is None and payoffs[0]["target_chapter_number"] is None


def test_source_bound_promise_still_enforces_its_explicit_due():
    item = {"item": "The sealed letter", "expected_function": "Identify its sender",
            "expected_use_by_chapter": 3, "status": "planted"}
    source = {"id": "approved-contract", "project_id": "project-1", "status": "approved",
              "artifact_type": "chapter_outline_batch", "content": {"items": [item]}}
    bound = {**item, "narrative_obligation": bind_explicit_obligation(source, ["items", 0])}
    report = evaluate_chekhov_emphasis(emphasized_items=[bound], current_chapter=4, mode="strict")
    assert "CHEKHOV_USE_OVERDUE" in {issue.id for issue in report.issues}
    assert not report.issues[0].can_override
    unknown = evaluate_chekhov_emphasis(emphasized_items=[item], current_chapter=4, mode="strict")
    assert unknown.issues == () and unknown.metrics["unknown_authority_item_count"] == 1


def test_postgres_transaction_equal_source_and_graph_timestamps_are_valid():
    snapshot, source, windows = _history()
    source["created_at"] = snapshot["clues"][0]["created_at"]
    snapshot["canon"] = [{"fact": "An adopted event", "aliases": ["original alias"]}]
    snapshot["timeline"] = [{"chapter": 1, "event": "An adopted event"}]
    review = propose_graph_snapshot_repair(snapshot, approved_sources=[source], windows=windows)
    result = _apply(snapshot, review, [source])
    assert all(item["classification"] == "repair_candidate" for item in review["proposal"]["audit"])
    assert result["canon"] == snapshot["canon"] and result["timeline"] == snapshot["timeline"]


def test_changed_source_record_metadata_is_also_hash_guarded():
    snapshot, source, windows = _history()
    review = propose_graph_snapshot_repair(snapshot, approved_sources=[source], windows=windows)
    source["created_at"] = "2026-01-01T00:30:00+00:00"  # same content, different producer evidence
    with pytest.raises(ValueError, match="Source/proposal changed"):
        _apply(snapshot, review, [source])


def test_reapply_preserves_database_updated_at_and_original_archive():
    snapshot, source, windows = _history()
    review = propose_graph_snapshot_repair(snapshot, approved_sources=[source], windows=windows)
    applied = _apply(snapshot, review, [source])
    for table in ("clues", "payoffs"):
        for row in applied[table]:
            row["updated_at"] = "2026-01-02T03:04:05.123456+00:00"
    before = copy.deepcopy(applied)
    assert _apply(applied, review, [source]) == before
    assert applied == before
    assert applied["clues"][0]["metadata"]["narrative_obligation"]["original_row"] == snapshot["clues"][0]


@pytest.mark.parametrize("fault", [
    "first_apply_timestamp", "unknown_timestamp", "missing_receipt", "missing_operator",
    "invalid_operator", "extra_operator_field", "source", "archive", "status", "label",
    "actual", "created_at", "new_field", "invalid_timestamp", "missing_timestamp",
])
def test_timestamp_exception_never_masks_other_changes(fault):
    snapshot, source, windows = _history()
    if fault == "unknown_timestamp":
        snapshot["clues"][0]["description"] += " authored"
    review = propose_graph_snapshot_repair(snapshot, approved_sources=[source], windows=windows)
    current = snapshot if fault == "first_apply_timestamp" else _apply(snapshot, review, [source])
    row = current["clues"][0]
    row["updated_at"] = "2026-01-02T03:04:05+00:00"
    marker = row["metadata"].get("narrative_obligation", {})
    if fault == "missing_receipt":
        row["metadata"] = {}
    elif fault == "missing_operator":
        marker.pop("operator")
    elif fault == "invalid_operator":
        marker["operator"]["evidence"] = {}
    elif fault == "extra_operator_field":
        marker["operator"]["invented"] = True
    elif fault == "source":
        marker["source"]["content_hash"] = "changed"
    elif fault == "archive":
        marker["original_row"]["updated_at"] = row["updated_at"]
    elif fault == "status":
        row["status"] = "resolved"
    elif fault == "label":
        row["label"] += " modified"
    elif fault == "actual":
        row["actual_paid_off_chapter_number"] = 9
    elif fault == "created_at":
        row["created_at"] = row["updated_at"]
    elif fault == "new_field":
        row["extra"] = "changed"
    elif fault == "invalid_timestamp":
        row["updated_at"] = "not a database timestamp"
    elif fault == "missing_timestamp":
        row.pop("updated_at")
    before = copy.deepcopy(current)
    with pytest.raises(ValueError, match="Graph rows changed"):
        _apply(current, review, [source])
    assert current == before


def test_sqlite_timestamp_mixin_readback_reapply_is_noop():
    from datetime import datetime

    from sqlalchemy import JSON, String, create_engine, event, update
    from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

    from bestseller.infra.db.base import TimestampMixin

    class Base(DeclarativeBase):
        pass

    class StoredRow(TimestampMixin, Base):
        __tablename__ = "test_graph_timestamp_roundtrip"
        id: Mapped[str] = mapped_column(String, primary_key=True)
        status: Mapped[str] = mapped_column(String)
        metadata_json: Mapped[dict] = mapped_column(JSON)
        payload: Mapped[dict] = mapped_column(JSON)

    engine = create_engine("sqlite:///:memory:")
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, sql, params, ctx, many: statements.append(sql))
    try:
        Base.metadata.create_all(engine)
        snapshot, source, windows = _history()
        with Session(engine) as session:
            for table in ("clues", "payoffs"):
                for row in snapshot[table]:
                    session.add(StoredRow(id=row["id"], status=row["status"], metadata_json=row["metadata"],
                        payload={k: v for k, v in row.items() if k not in {"status", "metadata", "created_at", "updated_at"}},
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"])))
            session.commit()

            def readback():
                result = copy.deepcopy(snapshot)
                for table in ("clues", "payoffs"):
                    result[table] = []
                    for original in snapshot[table]:
                        stored = session.get(StoredRow, original["id"])
                        result[table].append({**stored.payload, "status": stored.status,
                            "metadata": copy.deepcopy(stored.metadata_json),
                            "created_at": stored.created_at.isoformat() + "+00:00",
                            "updated_at": stored.updated_at.isoformat()})
                return result

            original = readback()
            review = propose_graph_snapshot_repair(original, approved_sources=[source], windows=windows)
            candidate = _apply(original, review, [source])
            assert all(a["classification"] == "repair_candidate" for a in review["proposal"]["audit"])
            for table in ("clues", "payoffs"):
                for row in candidate[table]:
                    session.execute(update(StoredRow).where(StoredRow.id == row["id"]).values(
                        status=row["status"], metadata_json=row["metadata"]))
            session.commit()
            session.expire_all()
            fresh = readback()
            for table in ("clues", "payoffs"):
                for row in fresh[table]:
                    assert row["updated_at"] != original[table][0]["updated_at"]
            assert any("updated_at=CURRENT_TIMESTAMP" in sql for sql in statements)
            statements.clear()
            assert _apply(fresh, review, [source]) == fresh
            assert not any(sql.lstrip().upper().startswith("UPDATE") for sql in statements)
    finally:
        engine.dispose()
