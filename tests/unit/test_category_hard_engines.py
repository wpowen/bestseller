# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from bestseller.services.category_hard_engines import (
    build_category_engine_fixture,
    evaluate_category_hard_engine,
    load_category_hard_engine_contracts,
    resolve_category_hard_engine_key,
    run_category_engine_fixture_benchmark,
)

pytestmark = pytest.mark.unit


def test_all_contracts_have_required_surfaces() -> None:
    contracts = load_category_hard_engine_contracts()

    assert {
        "action-progression",
        "base-building",
        "eastern-aesthetic",
        "esports-competition",
        "otherworld-cross-system",
        "relationship-driven",
        "strategy-worldbuilding",
        "suspense-mystery",
    }.issubset(contracts)
    for contract in contracts.values():
        assert contract.state_ledger_keys
        assert contract.hard_gate_keys
        assert contract.chapter_update_keys
        assert contract.benchmark_focus


@pytest.mark.parametrize("category_key", sorted(load_category_hard_engine_contracts()))
def test_good_fixture_passes_and_bad_fixture_blocks(category_key: str) -> None:
    good = evaluate_category_hard_engine(
        build_category_engine_fixture(category_key, good=True),
        category_key=category_key,
    )
    bad = evaluate_category_hard_engine(
        build_category_engine_fixture(category_key, good=False),
        category_key=category_key,
    )

    assert good.passed is True
    assert bad.passed is False
    assert {finding.code for finding in bad.findings} == {
        "category_state_ledger_missing",
        "category_hard_gate_missing",
        "category_chapter_update_missing",
    }


def test_otherworld_contract_requires_identity_and_exposure_ledgers() -> None:
    report = evaluate_category_hard_engine(
        {
            "canonical_category": "otherworld-cross-system",
            "premium_state_snapshot": {
                "passed": True,
                "cross_system_mapping": [{"old": "modern knowledge", "local": "partial"}],
            },
            "category_hard_gates": {
                "cross_system_boundary_gate": {"status": "active"},
                "identity_debt_gate": {"status": "active"},
                "exposure_cost_gate": {"status": "active"},
            },
            "chapter_state_updates": {
                "rule_mapping_delta": {"status": "required"},
                "identity_debt_delta": {"status": "required"},
                "exposure_cost_delta": {"status": "required"},
            },
        },
        category_key="otherworld-cross-system",
    )

    assert report.passed is False
    state_finding = next(
        finding for finding in report.findings if finding.code == "category_state_ledger_missing"
    )
    assert "identity_debt_ledger" in state_finding.missing_keys
    assert "exposure_cost_ledger" in state_finding.missing_keys


def test_resolve_category_key_from_genre_text() -> None:
    assert (
        resolve_category_hard_engine_key({}, genre="异界穿越", sub_genre="系统")
        == "otherworld-cross-system"
    )
    assert resolve_category_hard_engine_key({}, genre="基建经营") == "base-building"
    assert resolve_category_hard_engine_key({}, genre="电竞比赛") == "esports-competition"
    assert (
        resolve_category_hard_engine_key({}, genre="惊悚灵异", sub_genre="驱魔探案综合")
        == "suspense-mystery"
    )


def test_fixture_benchmark_covers_good_and_bad_cases() -> None:
    rows = run_category_engine_fixture_benchmark(
        ["otherworld-cross-system", "base-building", "suspense-mystery"]
    )

    assert len(rows) == 3
    assert all(row["good_fixture_passed"] is True for row in rows)
    assert all(row["bad_fixture_blocked"] is True for row in rows)
