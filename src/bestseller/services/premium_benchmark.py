# ruff: noqa: RUF001
"""Deterministic premium-genre benchmark fixtures.

This module does not claim to benchmark against live ranking books. It provides
repeatable good/bad structural fixtures so the premium gate can prove the
framework can represent and reject core ranking-book mechanics before a live
market benchmark is run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from bestseller.services.category_hard_engines import build_category_engine_fixture
from bestseller.services.premium_book_gate import (
    PremiumBookGateReport,
    evaluate_premium_project_readiness,
    premium_book_gate_report_to_dict,
)


@dataclass(frozen=True, slots=True)
class PremiumBenchmarkCase:
    case_id: str
    title: str
    genre: str
    sub_genre: str | None
    metadata: dict[str, object]
    expected_gate_passed: bool
    expected_blocking_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "genre": self.genre,
            "sub_genre": self.sub_genre,
            "expected_gate_passed": self.expected_gate_passed,
            "expected_blocking_codes": list(self.expected_blocking_codes),
        }


@dataclass(frozen=True, slots=True)
class PremiumBenchmarkCaseResult:
    case_id: str
    title: str
    passed: bool
    gate_passed: bool
    expected_gate_passed: bool
    actual_blocking_codes: tuple[str, ...]
    expected_blocking_codes: tuple[str, ...]
    gate_report: PremiumBookGateReport = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "passed": self.passed,
            "gate_passed": self.gate_passed,
            "expected_gate_passed": self.expected_gate_passed,
            "actual_blocking_codes": list(self.actual_blocking_codes),
            "expected_blocking_codes": list(self.expected_blocking_codes),
            "gate_report": premium_book_gate_report_to_dict(self.gate_report),
        }


@dataclass(frozen=True, slots=True)
class PremiumBenchmarkSuiteResult:
    suite_id: str
    title: str
    passed: bool
    passed_case_count: int
    failed_case_count: int
    case_results: tuple[PremiumBenchmarkCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "title": self.title,
            "passed": self.passed,
            "passed_case_count": self.passed_case_count,
            "failed_case_count": self.failed_case_count,
            "case_results": [case.to_dict() for case in self.case_results],
        }


def _state_snapshot(**overrides: object) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "passed": True,
        "resource_balances": {},
        "rule_state": {},
        "faction_pressure_queue": [],
        "relationship_state": {},
        "open_agency_debts": [],
    }
    snapshot.update(overrides)
    return snapshot


def _valid_ledger_report() -> dict[str, object]:
    return {"passed": True, "findings": []}


def _with_category_engine(
    metadata: dict[str, object],
    category_key: str,
) -> dict[str, object]:
    fixture = build_category_engine_fixture(category_key, good=True)
    merged = dict(metadata)
    merged["canonical_category"] = category_key
    fixture_snapshot = dict(fixture.get("premium_state_snapshot") or {})
    fixture_snapshot.update(dict(metadata.get("premium_state_snapshot") or {}))
    merged["premium_state_snapshot"] = fixture_snapshot
    merged["category_hard_gates"] = fixture["category_hard_gates"]
    merged["chapter_state_updates"] = fixture["chapter_state_updates"]
    return merged


def _good_xianxia_metadata() -> dict[str, object]:
    metadata = {
        "world_spec": {
            "power_system": {
                "name": "青炉修行体系",
                "realms": ["炼气", "筑基", "金丹"],
                "resources": ["筑基丹", "灵石", "外门名额"],
            },
            "factions": [
                {
                    "name": "青炉宗",
                    "goal": "垄断外门筑基资源",
                    "relationship_to_protagonist": "压价观察",
                    "next_pressure": "要求沈砚交出秘境线索",
                }
            ],
        },
        "cast_spec": {
            "protagonist": {
                "name": "沈砚",
                "decision_policy": {
                    "core_rule": "先保命，再用小收益换取下一次机会。",
                    "will_not_do": ["无代价突破", "为面子公开决斗"],
                },
            }
        },
        "premium_state_ledger_report": _valid_ledger_report(),
        "premium_state_snapshot": _state_snapshot(
            resource_balances={"沈砚": {"筑基丹": 1, "灵石": 12}},
            faction_pressure_queue=[
                {
                    "faction": "青炉宗",
                    "trigger": "沈砚取得筑基丹",
                    "reaction": "派外门执事压价收购并试探秘境来源",
                }
            ],
        ),
    }
    return _with_category_engine(metadata, "action-progression")


def _bad_xianxia_metadata() -> dict[str, object]:
    return {
        "cast_spec": {
            "protagonist": {
                "name": "沈砚",
                "decision_policy": {"core_rule": "随机冒险，总能突破。"},
            }
        },
        "premium_state_ledger_report": _valid_ledger_report(),
        "premium_state_snapshot": _state_snapshot(),
    }


def _good_rule_mystery_metadata() -> dict[str, object]:
    metadata = {
        "world_rules": [
            {
                "rule_code": "R-001",
                "name": "否认者先入账",
                "visible_effect": "镜面出现债名",
                "exploitation_potential": "逼当事人承认隐瞒事实",
                "cost": "每次逼供都会让主角被镜局记名",
            }
        ],
        "cast_spec": {
            "protagonist": {
                "name": "林渊",
                "decision_policy": {"core_rule": "先验证规则，再用规则反制。"},
            }
        },
        "premium_state_ledger_report": _valid_ledger_report(),
        "premium_state_snapshot": _state_snapshot(
            rule_state={
                "R-001": {
                    "name": "否认者先入账",
                    "last_visible_effect": "镜面出现债名",
                    "last_cost": "被镜局记名",
                }
            }
        ),
    }
    return _with_category_engine(metadata, "suspense-mystery")


def _bad_rule_mystery_metadata() -> dict[str, object]:
    return {
        "cast_spec": {
            "protagonist": {
                "name": "林渊",
                "decision_policy": {"core_rule": "遇到怪事就硬闯。"},
            }
        },
        "premium_state_ledger_report": _valid_ledger_report(),
        "premium_state_snapshot": _state_snapshot(),
    }


def _good_female_no_cp_metadata() -> dict[str, object]:
    metadata = {
        "cast_spec": {
            "protagonist": {
                "name": "苏棠",
                "decision_policy": {"core_rule": "事业优先，所有关系都服务于自我选择。"},
                "relationships": [
                    {
                        "target": "合伙人周宁",
                        "relationship_type": "事业盟友",
                        "tension_summary": "资源互补但目标节奏冲突",
                    }
                ],
            }
        },
        "interpersonal_promises": [
            {
                "owner": "苏棠",
                "promise": "三章内给周宁一个公开交代",
                "due_window": "3章内",
            }
        ],
        "premium_state_ledger_report": _valid_ledger_report(),
        "premium_state_snapshot": _state_snapshot(
            relationship_state={
                "苏棠 -> 合伙人周宁": {
                    "axes": {"trust": "有限合作"},
                    "last_active_choice": "拒绝暧昧化资源交换",
                }
            },
            open_agency_debts=[
                {
                    "owner": "苏棠",
                    "debt": "公开说明合作边界",
                    "due_window": "3章内",
                }
            ],
        ),
    }
    return _with_category_engine(metadata, "female-growth-ncp")


def _bad_female_no_cp_metadata() -> dict[str, object]:
    return {
        "cast_spec": {
            "protagonist": {
                "name": "苏棠",
                "decision_policy": {"core_rule": "事业优先。"},
            }
        },
        "premium_state_ledger_report": _valid_ledger_report(),
        "premium_state_snapshot": _state_snapshot(),
    }


def builtin_premium_benchmark_cases() -> tuple[PremiumBenchmarkCase, ...]:
    return (
        PremiumBenchmarkCase(
            case_id="xianxia-survival-good",
            title="凡人流修仙好夹具",
            genre="仙侠",
            sub_genre="凡人流",
            metadata=_good_xianxia_metadata(),
            expected_gate_passed=True,
        ),
        PremiumBenchmarkCase(
            case_id="xianxia-survival-bad-empty-upgrade",
            title="凡人流空升级坏夹具",
            genre="仙侠",
            sub_genre="凡人流",
            metadata=_bad_xianxia_metadata(),
            expected_gate_passed=False,
            expected_blocking_codes=(
                "progression_engine_missing",
                "faction_ecology_missing",
                "category_state_ledger_missing",
                "category_hard_gate_missing",
                "category_chapter_update_missing",
            ),
        ),
        PremiumBenchmarkCase(
            case_id="rule-mystery-good",
            title="规则悬疑好夹具",
            genre="民俗悬疑",
            sub_genre="规则怪谈",
            metadata=_good_rule_mystery_metadata(),
            expected_gate_passed=True,
        ),
        PremiumBenchmarkCase(
            case_id="rule-mystery-bad-missing-rules",
            title="规则悬疑缺规则坏夹具",
            genre="民俗悬疑",
            sub_genre="规则怪谈",
            metadata=_bad_rule_mystery_metadata(),
            expected_gate_passed=False,
            expected_blocking_codes=(
                "rule_system_missing",
                "category_state_ledger_missing",
                "category_hard_gate_missing",
                "category_chapter_update_missing",
            ),
        ),
        PremiumBenchmarkCase(
            case_id="female-no-cp-good",
            title="女频无CP关系代理好夹具",
            genre="女频",
            sub_genre="女性成长无CP",
            metadata=_good_female_no_cp_metadata(),
            expected_gate_passed=True,
        ),
        PremiumBenchmarkCase(
            case_id="female-no-cp-bad-missing-agency",
            title="女频无CP缺关系代理坏夹具",
            genre="女频",
            sub_genre="女性成长无CP",
            metadata=_bad_female_no_cp_metadata(),
            expected_gate_passed=False,
            expected_blocking_codes=(
                "relationship_agency_missing",
                "category_state_ledger_missing",
                "category_hard_gate_missing",
                "category_chapter_update_missing",
            ),
        ),
    )


def _case_result(case: PremiumBenchmarkCase) -> PremiumBenchmarkCaseResult:
    report = evaluate_premium_project_readiness(
        case.metadata,
        genre=case.genre,
        sub_genre=case.sub_genre,
    )
    actual_codes = tuple(finding.code for finding in report.blocking_findings)
    expected_codes = set(case.expected_blocking_codes)
    passed = (
        report.passed is case.expected_gate_passed
        and expected_codes.issubset(set(actual_codes))
    )
    return PremiumBenchmarkCaseResult(
        case_id=case.case_id,
        title=case.title,
        passed=passed,
        gate_passed=report.passed,
        expected_gate_passed=case.expected_gate_passed,
        actual_blocking_codes=actual_codes,
        expected_blocking_codes=case.expected_blocking_codes,
        gate_report=report,
    )


def run_premium_benchmark_cases(
    cases: Sequence[PremiumBenchmarkCase] | None = None,
) -> PremiumBenchmarkSuiteResult:
    selected_cases = tuple(cases or builtin_premium_benchmark_cases())
    case_results = tuple(_case_result(case) for case in selected_cases)
    passed_count = sum(1 for result in case_results if result.passed)
    failed_count = len(case_results) - passed_count
    return PremiumBenchmarkSuiteResult(
        suite_id="premium-genre-structure",
        title="Premium Genre Structure Benchmark",
        passed=failed_count == 0,
        passed_case_count=passed_count,
        failed_case_count=failed_count,
        case_results=case_results,
    )


__all__ = [
    "PremiumBenchmarkCase",
    "PremiumBenchmarkCaseResult",
    "PremiumBenchmarkSuiteResult",
    "builtin_premium_benchmark_cases",
    "run_premium_benchmark_cases",
]
