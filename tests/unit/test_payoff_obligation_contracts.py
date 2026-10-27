"""Payoff obligations come from structured contracts, never prose or defaults."""

from copy import deepcopy
from dataclasses import dataclass, field
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from bestseller.domain.narrative import ChapterContractRead
from bestseller.infra.db.models import ClueModel, PayoffModel
from bestseller.services.payoff_ledger import PayoffStatus, run_payoff_ledger_audit
from bestseller.services.payoff_ledger_runtime import (
    _payoff_ledger_rewrite_instructions,
    compute_payoff_ledger_audit_for_review,
    merge_payoff_ledger_audit_into_chapter_review,
    payoff_ledger_audit_to_dict,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class Payoff:
    payoff_code: str = "instant"
    label: str = ""
    description: str = ""
    source_clue_id: Any | None = None
    target_chapter_number: int | None = 1
    actual_chapter_number: int | None = 1
    status: str = "planned"
    metadata_json: dict[str, Any] = field(default_factory=lambda: {"source_planted_chapter": 1})


def contract(distance: int) -> dict[str, Any]:
    return {
        "source_planted_chapter": 1,
        "obligation_contract": {
            "source": {"artifact_id": "approved-plan", "content_hash": "exported-hash"},
            "min_setup_distance": distance,
        },
    }


@pytest.mark.parametrize("distance", [None, 0])
def test_chapter_one_instant_payoff_has_no_default_distance_obligation(distance):
    row = Payoff()
    audit = run_payoff_ledger_audit([row], current_chapter=1, min_setup_distance=distance)
    assert audit.all_findings == ()
    assert audit.closure_rate == 1.0
    entry = audit.view[0]
    assert (entry.source_planted_chapter, entry.target_chapter, entry.actual_chapter) == (1, 1, 1)
    assert entry.setup_distance_chapters == 0
    assert entry.is_resolved and entry.is_obligation and not entry.is_reference


@pytest.mark.parametrize("global_distance", [None, 0, 1, 2, 3])
def test_source_distance_is_enforced_and_global_argument_cannot_relax_it(global_distance):
    row = Payoff(metadata_json=contract(2))
    original = deepcopy(row)
    audit = run_payoff_ledger_audit([row], current_chapter=1, min_setup_distance=global_distance)
    (finding,) = audit.setup_distance_findings
    assert finding.code == "PAYOFF_SETUP_TOO_SHORT"
    assert finding.evidence["required_setup_distances"] == {"instant": max(2, global_distance or 0)}
    assert finding.evidence["setup_distances"] == [0]
    assert row == original


def test_explicit_legacy_api_distance_still_works_without_metadata_contract():
    audit = run_payoff_ledger_audit([Payoff()], current_chapter=1, min_setup_distance=2)
    assert audit.setup_distance_findings[0].evidence["min_setup_distance"] == 2


@pytest.mark.parametrize("bad", [True, -1, 1.5, "2"])
def test_invalid_explicit_api_requirement_is_not_silently_coerced(bad):
    with pytest.raises(ValueError, match="min_setup_distance"):
        run_payoff_ledger_audit([Payoff()], current_chapter=1, min_setup_distance=bad)


@pytest.mark.parametrize(
    "metadata",
    [
        {"min_setup_distance": 2},
        {"obligation_contract": {"min_setup_distance": 2}},
        {"obligation_contract": {"min_setup_distance": 2, "source": {}}},
        {"obligation_contract": {"min_setup_distance": 2, "source": {"artifact_id": None}}},
        {"obligation_contract": {"min_setup_distance": 2, "source": "not-an-export"}},
        {"obligation_contract": {"min_setup_distance": True, "source": {"artifact_id": "a"}}},
    ],
)
def test_unbound_or_invalid_metadata_does_not_create_a_setup_obligation(metadata):
    row = Payoff(metadata_json={"source_planted_chapter": 1, **metadata})
    audit = run_payoff_ledger_audit([row], current_chapter=1)
    assert audit.all_findings == ()
    assert audit.view[0].min_setup_distance is None


@pytest.mark.parametrize("kind", ["obligation_contract", "narrative_obligation"])
def test_guarded_top_level_distance_is_supported(kind):
    metadata = {
        "source_planted_chapter": 1,
        "min_setup_distance": 2,
        kind: {"source": {"artifact_id": "plan"}, "kind": "commitment"},
    }
    assert run_payoff_ledger_audit(
        [Payoff(metadata_json=metadata)], current_chapter=1
    ).setup_distance_findings


def test_distance_boundary_and_unknown_setup_are_not_fabricated():
    sufficient = Payoff(target_chapter_number=3, actual_chapter_number=3, metadata_json=contract(2))
    assert run_payoff_ledger_audit([sufficient], current_chapter=3).all_findings == ()
    missing = contract(2)
    missing.pop("source_planted_chapter")
    audit = run_payoff_ledger_audit([Payoff(metadata_json=missing)], current_chapter=1)
    assert audit.view[0].setup_distance_chapters is None
    assert audit.setup_distance_findings == ()


def test_reference_and_superseded_rows_preserve_facts_without_closure_debt():
    rows = [
        Payoff("active", target_chapter_number=3, actual_chapter_number=3),
        Payoff("future", target_chapter_number=5, actual_chapter_number=None),
        Payoff(
            "reference",
            actual_chapter_number=None,
            metadata_json={
                "narrative_obligation": {"kind": "reference", "source": {"artifact_id": "a"}}
            },
        ),
        Payoff("superseded", actual_chapter_number=None, status="superseded"),
        Payoff(
            "paid-reference",
            metadata_json={
                **contract(2),
                "narrative_obligation": {"kind": "reference"},
            },
        ),
    ]
    audit = run_payoff_ledger_audit(rows, current_chapter=3, min_setup_distance=2)
    assert audit.all_findings == ()
    assert audit.closure_rate == 0.5
    assert audit.due_count == audit.overdue_count == 0
    assert audit.resolved_current_chapter_count == 1
    payload = payoff_ledger_audit_to_dict(audit)
    reference = payload["entries"][2]
    assert reference["status"] == "overdue"  # Historical schedule remains visible.
    assert reference["target_chapter"] == 1 and reference["actual_chapter"] is None
    assert reference["is_reference"] is True and reference["is_obligation"] is False
    assert payload["entries"][3]["status"] == "superseded"
    assert payload["entries"][4]["actual_chapter"] == 1
    json.dumps(payload)


@pytest.mark.parametrize("status", ["planned", "reference", "superseded"])
def test_reference_only_or_superseded_only_ledger_has_full_closure(status):
    metadata = {"narrative_obligation": {"kind": "reference"}} if status == "planned" else {}
    audit = run_payoff_ledger_audit(
        [Payoff(status=status, actual_chapter_number=None, metadata_json=metadata)],
        current_chapter=8,
    )
    assert audit.closure_rate == 1.0 and audit.all_findings == ()


def test_shared_status_reference_without_metadata_preserves_historical_actual_and_due():
    row = Payoff(status="reference", metadata_json=contract(2))
    audit = run_payoff_ledger_audit([row], current_chapter=1, min_setup_distance=2)
    (entry,) = audit.view
    assert entry.is_reference and entry.payoff_status == PayoffStatus.REFERENCE
    assert entry.target_chapter == entry.actual_chapter == entry.source_planted_chapter == 1
    assert audit.all_findings == () and audit.closure_rate == 1.0
    assert audit.resolved_current_chapter_count == 0


def test_labels_and_source_mentions_do_not_erase_explicit_due_facts():
    row = Payoff(
        label="reference only; superseded",
        description="只是引用，不必兑现",  # noqa: RUF001
        actual_chapter_number=None,
        metadata_json={
            "narrative_obligation": {
                "kind": "commitment",
                "source": {"artifact_id": "plan"},
                "due_authority": "explicit",
            }
        },
    )
    due = run_payoff_ledger_audit([row], current_chapter=1)
    overdue = run_payoff_ledger_audit([row], current_chapter=2)
    assert due.due_count == 1 and overdue.overdue_count == 1
    assert overdue.all_findings[0].severity == "block"
    resolved = run_payoff_ledger_audit([Payoff()], current_chapter=1)
    assert resolved.view[0].payoff_status == PayoffStatus.RESOLVED


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_rewrite_prompt_only_includes_distance_when_required_and_excludes_references(language):
    audit = run_payoff_ledger_audit(
        [
            Payoff("due", actual_chapter_number=None),
            Payoff(
                "reference",
                actual_chapter_number=None,
                metadata_json={"narrative_obligation": {"kind": "reference"}},
            ),
        ],
        current_chapter=1,
    )
    prompt = _payoff_ledger_rewrite_instructions(
        audit,
        1,
        language=language,
        evidence_paths=[{"payoff_code": "reference", "scene_ref": "DO_NOT_CASH_REFERENCE"}],
    )
    assert "DO_NOT_CASH_REFERENCE" not in prompt
    assert "setup-distance requirements" not in prompt and "按显式合同核查铺垫距离" not in prompt
    required = run_payoff_ledger_audit([Payoff(metadata_json=contract(2))], current_chapter=1)
    prompt = _payoff_ledger_rewrite_instructions(required, 1, language=language)
    assert "instant: 2" in prompt


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


class SQLiteSession:
    """Execute runtime ORM reads against a real isolated SQLite database."""

    def __init__(self, session):
        self.session = session

    async def scalars(self, statement):
        return self.session.scalars(statement)


@pytest.mark.parametrize("required_distance", [None, 2])
async def test_runtime_sqlite_roundtrip_preserves_instant_reference_and_superseded_facts(
    tmp_path, monkeypatch, required_distance
):
    monkeypatch.setenv("BESTSELLER_METHODOLOGY_V2", "1")
    path = tmp_path / "payoff_obligation_test.sqlite3"
    assert "test" in path.name
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            for model in (ClueModel, PayoffModel):
                connection.execute(CreateTable(model.__table__))
        with Session(engine, expire_on_commit=False) as session:
            project_id, clue_id = uuid4(), uuid4()
            session.add(
                ClueModel(
                    id=clue_id,
                    project_id=project_id,
                    clue_code="setup",
                    label="setup",
                    description="First-scene setup",
                    planted_in_chapter_number=1,
                )
            )
            metadata = contract(required_distance) if required_distance is not None else {}
            session.add_all(
                [
                    PayoffModel(
                        id=uuid4(),
                        project_id=project_id,
                        payoff_code="status-reference",
                        label="status reference",
                        description="Migrated legacy row retains its original actual and due",
                        source_clue_id=clue_id,
                        target_chapter_number=1,
                        actual_chapter_number=1,
                        status="reference",
                        metadata_json={},
                    ),
                    PayoffModel(
                        id=uuid4(),
                        project_id=project_id,
                        payoff_code="instant",
                        label="instant",
                        description="Same-chapter outcome",
                        source_clue_id=clue_id,
                        target_chapter_number=1,
                        actual_chapter_number=1,
                        metadata_json=metadata,
                    ),
                    PayoffModel(
                        id=uuid4(),
                        project_id=project_id,
                        payoff_code="reference",
                        label="reference",
                        description="Historical reference",
                        target_chapter_number=1,
                        metadata_json={
                            "narrative_obligation": {
                                "kind": "reference",
                                "source": {"artifact_id": "prior-plan"},
                            }
                        },
                    ),
                    PayoffModel(
                        id=uuid4(),
                        project_id=project_id,
                        payoff_code="old",
                        label="old",
                        description="Superseded plan",
                        target_chapter_number=1,
                        status="superseded",
                    ),
                    PayoffModel(
                        id=uuid4(),
                        project_id=uuid4(),
                        payoff_code="foreign",
                        label="foreign",
                        description="Other project",
                        target_chapter_number=1,
                    ),
                ]
            )
            session.commit()
            before = [
                (
                    p.payoff_code,
                    p.target_chapter_number,
                    p.actual_chapter_number,
                    p.status,
                    deepcopy(p.metadata_json),
                )
                for p in session.scalars(select(PayoffModel))
            ]
            audit = await compute_payoff_ledger_audit_for_review(
                session=SQLiteSession(session),
                project=SimpleNamespace(id=project_id),
                chapter=SimpleNamespace(chapter_number=1),
                chapter_contract=ChapterContractRead(
                    id=uuid4(),
                    chapter_id=uuid4(),
                    chapter_number=1,
                    contract_summary="Approved chapter outcome",
                    due_payoff_codes=["reference", "old"],
                ),
            )
            assert audit is not None and len(audit.view) == 4
            assert audit.due_count == audit.overdue_count == 0 and audit.closure_rate == 1.0
            assert bool(audit.setup_distance_findings) == (required_distance is not None)
            # Use a real review schema; only ledger findings may change its verdict.
            from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores

            scores = ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.9))
            review = ChapterReviewResult(
                verdict="pass", severity_max="info", scores=scores, findings=[], evidence_summary={}
            )
            merged = merge_payoff_ledger_audit_into_chapter_review(review, audit, chapter_number=1)
            assert merged.verdict == ("rewrite" if required_distance else "pass")
            session.expire_all()
            after = [
                (
                    p.payoff_code,
                    p.target_chapter_number,
                    p.actual_chapter_number,
                    p.status,
                    p.metadata_json,
                )
                for p in session.scalars(select(PayoffModel))
            ]
            assert after == before
    finally:
        engine.dispose()
