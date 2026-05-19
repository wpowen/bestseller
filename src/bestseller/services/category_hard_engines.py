# ruff: noqa: RUF001
"""Category-specific hard-engine contracts for premium serial fiction.

The contracts are repo-safe abstractions: they name the state ledgers, gates,
and chapter-update channels a category needs, but they do not store source
titles, prose, authors, or recoverable sample links.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CategoryHardEngineContract:
    category_key: str
    display_name: str
    state_ledger_keys: tuple[str, ...]
    hard_gate_keys: tuple[str, ...]
    chapter_update_keys: tuple[str, ...]
    benchmark_focus: tuple[str, ...]
    keyword_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_key": self.category_key,
            "display_name": self.display_name,
            "state_ledger_keys": list(self.state_ledger_keys),
            "hard_gate_keys": list(self.hard_gate_keys),
            "chapter_update_keys": list(self.chapter_update_keys),
            "benchmark_focus": list(self.benchmark_focus),
            "keyword_signals": list(self.keyword_signals),
        }


@dataclass(frozen=True, slots=True)
class CategoryHardEngineFinding:
    code: str
    severity: str
    message: str
    path: str
    missing_keys: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "missing_keys": list(self.missing_keys),
        }


@dataclass(frozen=True, slots=True)
class CategoryHardEngineReport:
    category_key: str
    passed: bool
    present_state_ledgers: tuple[str, ...]
    present_hard_gates: tuple[str, ...]
    present_chapter_updates: tuple[str, ...]
    findings: tuple[CategoryHardEngineFinding, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_key": self.category_key,
            "passed": self.passed,
            "present_state_ledgers": list(self.present_state_ledgers),
            "present_hard_gates": list(self.present_hard_gates),
            "present_chapter_updates": list(self.present_chapter_updates),
            "findings": [finding.to_dict() for finding in self.findings],
        }


_CONTRACTS: dict[str, CategoryHardEngineContract] = {
    "action-progression": CategoryHardEngineContract(
        category_key="action-progression",
        display_name="升级流 / Action Progression",
        state_ledger_keys=(
            "power_tier_state",
            "resource_balances",
            "opportunity_map",
            "faction_pressure_queue",
        ),
        hard_gate_keys=(
            "progression_causality_gate",
            "resource_cost_gate",
            "faction_reaction_gate",
        ),
        chapter_update_keys=(
            "power_tier_delta",
            "resource_delta",
            "opportunity_delta",
            "faction_reaction_delta",
        ),
        benchmark_focus=("升级有因果", "收益有代价", "派系有后续反应"),
        keyword_signals=("升级", "仙侠", "修仙", "玄幻", "凡人", "progression", "cultivation"),
    ),
    "strategy-worldbuilding": CategoryHardEngineContract(
        category_key="strategy-worldbuilding",
        display_name="权谋历史与世界构建 / Strategy Worldbuilding",
        state_ledger_keys=(
            "faction_pressure_queue",
            "institutional_agenda",
            "logistics_ledger",
            "treasury_state",
            "battlefront_state",
        ),
        hard_gate_keys=(
            "strategy_consequence_gate",
            "institutional_pressure_gate",
            "logistics_plausibility_gate",
        ),
        chapter_update_keys=(
            "faction_move_delta",
            "institutional_agenda_delta",
            "resource_logistics_delta",
        ),
        benchmark_focus=("计谋产生制度后果", "派系按利益行动", "战役与财政物流自洽"),
        keyword_signals=("权谋", "历史", "争霸", "strategy", "court"),
    ),
    "suspense-mystery": CategoryHardEngineContract(
        category_key="suspense-mystery",
        display_name="悬疑规则 / Suspense Mystery",
        state_ledger_keys=(
            "rule_lattice",
            "clue_chain",
            "evidence_ledger",
            "suspect_timeline",
            "red_herring_ledger",
        ),
        hard_gate_keys=(
            "fair_clue_gate",
            "evidence_legality_gate",
            "timeline_consistency_gate",
        ),
        chapter_update_keys=(
            "clue_delta",
            "suspect_state_delta",
            "rule_reveal_delta",
            "misdirection_delta",
        ),
        benchmark_focus=("线索公平", "规则可验证", "误导不作弊"),
        keyword_signals=(
            "悬疑",
            "推理",
            "怪谈",
            "灵异",
            "驱魔",
            "探案",
            "民俗",
            "阴阳",
            "mystery",
            "thriller",
        ),
    ),
    "relationship-driven": CategoryHardEngineContract(
        category_key="relationship-driven",
        display_name="关系驱动 / Relationship Driven",
        state_ledger_keys=(
            "relationship_state",
            "intimacy_boundaries",
            "misunderstanding_graph",
            "promise_debt_ledger",
        ),
        hard_gate_keys=(
            "relationship_distance_gate",
            "agency_choice_gate",
            "promise_payoff_gate",
        ),
        chapter_update_keys=(
            "relationship_distance_delta",
            "boundary_delta",
            "promise_debt_delta",
        ),
        benchmark_focus=("关系距离真实变化", "承诺有兑现或延期", "人物主动选择不漂移"),
        keyword_signals=("言情", "关系", "女性", "romance", "relationship"),
    ),
    "base-building": CategoryHardEngineContract(
        category_key="base-building",
        display_name="基建经营 / Base Building",
        state_ledger_keys=(
            "settlement_inventory",
            "logistics_ledger",
            "population_state",
            "build_queue",
            "external_demand_pressure",
        ),
        hard_gate_keys=(
            "resource_conservation_gate",
            "build_queue_gate",
            "stakeholder_pressure_gate",
        ),
        chapter_update_keys=(
            "inventory_delta",
            "build_queue_delta",
            "population_delta",
            "demand_pressure_delta",
        ),
        benchmark_focus=("建设成果可见", "资源守恒", "每次扩张带来新瓶颈"),
        keyword_signals=("基建", "经营", "种田", "base-building", "settlement"),
    ),
    "esports-competition": CategoryHardEngineContract(
        category_key="esports-competition",
        display_name="电竞游戏 / Esports Competition",
        state_ledger_keys=(
            "match_state",
            "draft_bp_state",
            "patch_meta",
            "team_tactics",
            "tournament_pressure",
        ),
        hard_gate_keys=(
            "match_state_gate",
            "bp_logic_gate",
            "tactical_payoff_gate",
        ),
        chapter_update_keys=(
            "match_state_delta",
            "team_tactic_delta",
            "opponent_adaptation_delta",
        ),
        benchmark_focus=("比赛状态连续", "BP 和版本有逻辑", "战术收益可复盘"),
        keyword_signals=("电竞", "游戏", "esports", "match", "tournament"),
    ),
    "otherworld-cross-system": CategoryHardEngineContract(
        category_key="otherworld-cross-system",
        display_name="异界跨体系 / Otherworld Cross-System",
        state_ledger_keys=(
            "cross_system_mapping",
            "identity_debt_ledger",
            "exposure_cost_ledger",
            "local_rule_audit",
        ),
        hard_gate_keys=(
            "cross_system_boundary_gate",
            "identity_debt_gate",
            "exposure_cost_gate",
        ),
        chapter_update_keys=(
            "rule_mapping_delta",
            "identity_debt_delta",
            "exposure_cost_delta",
        ),
        benchmark_focus=("旧知识有边界", "身份债持续推进", "套利必然留下暴露代价"),
        keyword_signals=("异界", "异世", "穿越", "系统", "otherworld", "isekai"),
    ),
    "female-growth-ncp": CategoryHardEngineContract(
        category_key="female-growth-ncp",
        display_name="女性成长无CP / Female Growth NCP",
        state_ledger_keys=(
            "career_ladder",
            "agency_debt_ledger",
            "social_pressure_state",
            "competence_growth_ledger",
        ),
        hard_gate_keys=(
            "agency_preservation_gate",
            "hidden_romance_drift_gate",
            "career_progression_gate",
        ),
        chapter_update_keys=(
            "career_delta",
            "agency_debt_delta",
            "social_pressure_delta",
        ),
        benchmark_focus=("事业线真实进阶", "主动权不被恋爱线吞掉", "社会压力有反作用"),
        keyword_signals=("大女主", "女强", "无CP", "female growth"),
    ),
    "eastern-aesthetic": CategoryHardEngineContract(
        category_key="eastern-aesthetic",
        display_name="东方美学 / Eastern Aesthetic",
        state_ledger_keys=(
            "image_meaning_chain",
            "ritual_order_pressure",
            "poetic_object_ledger",
            "atmosphere_turn_ledger",
        ),
        hard_gate_keys=(
            "image_plot_function_gate",
            "ritual_pressure_gate",
            "poetic_payoff_gate",
        ),
        chapter_update_keys=(
            "image_meaning_delta",
            "ritual_pressure_delta",
            "poetic_object_delta",
        ),
        benchmark_focus=("意象改变剧情", "礼法秩序制造选择压力", "审美余韵有状态变化"),
        keyword_signals=("东方美学", "国风", "水墨", "志怪", "eastern aesthetic"),
    ),
}


def load_category_hard_engine_contracts() -> dict[str, CategoryHardEngineContract]:
    return dict(_CONTRACTS)


def get_category_hard_engine_contract(
    category_key: str | None,
) -> CategoryHardEngineContract | None:
    if not category_key:
        return None
    return _CONTRACTS.get(category_key)


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _non_empty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | Sequence) and not isinstance(value, str | bytes):
        return bool(value)
    return True


def _metadata_containers(metadata: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    snapshot = _as_mapping(metadata.get("premium_state_snapshot"))
    containers: list[Mapping[str, object]] = [
        metadata,
        snapshot,
        _as_mapping(metadata.get("book_spec")),
        _as_mapping(metadata.get("world_spec")),
        _as_mapping(metadata.get("cast_spec")),
        _as_mapping(metadata.get("story_bible_context")),
    ]
    for key in ("category_state_engine", "category_hard_gates", "chapter_state_updates"):
        containers.append(_as_mapping(metadata.get(key)))
    return tuple(containers)


def _key_present(metadata: Mapping[str, object], key: str) -> bool:
    for container in _metadata_containers(metadata):
        if _non_empty(container.get(key)):
            return True
    for collection_key in ("category_hard_gates", "chapter_state_updates"):
        raw = metadata.get(collection_key)
        if isinstance(raw, list | tuple | set) and key in {_text(item) for item in raw}:
            return True
    return False


def _present_keys(metadata: Mapping[str, object], keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(key for key in keys if _key_present(metadata, key))


def _missing_keys(expected: Sequence[str], present: Sequence[str]) -> tuple[str, ...]:
    present_set = set(present)
    return tuple(key for key in expected if key not in present_set)


def evaluate_category_hard_engine(
    metadata: Mapping[str, object],
    *,
    category_key: str,
) -> CategoryHardEngineReport:
    contract = get_category_hard_engine_contract(category_key)
    if contract is None:
        return CategoryHardEngineReport(
            category_key=category_key,
            passed=False,
            present_state_ledgers=(),
            present_hard_gates=(),
            present_chapter_updates=(),
            findings=(
                CategoryHardEngineFinding(
                    code="category_hard_engine_contract_missing",
                    severity="warning",
                    message=f"No hard-engine contract exists for category {category_key}.",
                    path="category_hard_engine_contracts",
                ),
            ),
        )

    present_state = _present_keys(metadata, contract.state_ledger_keys)
    present_gates = _present_keys(metadata, contract.hard_gate_keys)
    present_updates = _present_keys(metadata, contract.chapter_update_keys)
    findings: list[CategoryHardEngineFinding] = []
    missing_state = _missing_keys(contract.state_ledger_keys, present_state)
    missing_gates = _missing_keys(contract.hard_gate_keys, present_gates)
    missing_updates = _missing_keys(contract.chapter_update_keys, present_updates)
    if missing_state:
        findings.append(
            CategoryHardEngineFinding(
                code="category_state_ledger_missing",
                severity="critical",
                message=f"{category_key} lacks required category state ledgers.",
                path="premium_state_snapshot",
                missing_keys=missing_state,
            )
        )
    if missing_gates:
        findings.append(
            CategoryHardEngineFinding(
                code="category_hard_gate_missing",
                severity="high",
                message=f"{category_key} lacks required category hard gates.",
                path="category_hard_gates",
                missing_keys=missing_gates,
            )
        )
    if missing_updates:
        findings.append(
            CategoryHardEngineFinding(
                code="category_chapter_update_missing",
                severity="high",
                message=f"{category_key} lacks required chapter state update channels.",
                path="chapter_state_updates",
                missing_keys=missing_updates,
            )
        )
    return CategoryHardEngineReport(
        category_key=category_key,
        passed=not findings,
        present_state_ledgers=present_state,
        present_hard_gates=present_gates,
        present_chapter_updates=present_updates,
        findings=tuple(findings),
    )


def resolve_category_hard_engine_key(
    metadata: Mapping[str, object],
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
) -> str | None:
    for key in ("canonical_category", "category_key", "category"):
        value = _text(metadata.get(key))
        if value in _CONTRACTS:
            return value
    haystack = " ".join(
        part
        for part in (
            _text(genre),
            _text(sub_genre),
            _text(metadata.get("genre")),
            _text(metadata.get("sub_genre")),
            _text(_as_mapping(metadata.get("book_spec")).get("genre")),
            _text(_as_mapping(metadata.get("book_spec")).get("sub_genre")),
        )
        if part
    ).lower()
    matches: list[tuple[int, str]] = []
    for contract in _CONTRACTS.values():
        for signal in contract.keyword_signals:
            if signal.lower() in haystack:
                matches.append((len(signal), contract.category_key))
    if matches:
        return max(matches)[1]
    return None


def build_category_engine_fixture(
    category_key: str,
    *,
    good: bool,
) -> dict[str, object]:
    contract = get_category_hard_engine_contract(category_key)
    if contract is None:
        raise KeyError(category_key)
    metadata: dict[str, object] = {
        "canonical_category": category_key,
        "genre": contract.display_name,
        "decision_policy": {"core_rule": "preserve category-specific causality"},
        "premium_state_ledger_report": {"passed": True, "findings": []},
        "premium_state_snapshot": {"passed": True},
    }
    if not good:
        return metadata
    snapshot = _as_mapping(metadata["premium_state_snapshot"])
    for key in contract.state_ledger_keys:
        snapshot[key] = [{"status": "fixture-present", "key": key}]
    metadata["premium_state_snapshot"] = snapshot
    metadata["category_hard_gates"] = {
        key: {"status": "active", "fixture": True}
        for key in contract.hard_gate_keys
    }
    metadata["chapter_state_updates"] = {
        key: {"status": "required", "fixture": True}
        for key in contract.chapter_update_keys
    }
    return metadata


def run_category_engine_fixture_benchmark(
    categories: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    selected = tuple(categories or sorted(_CONTRACTS))
    rows: list[dict[str, Any]] = []
    for category_key in selected:
        good_report = evaluate_category_hard_engine(
            build_category_engine_fixture(category_key, good=True),
            category_key=category_key,
        )
        bad_report = evaluate_category_hard_engine(
            build_category_engine_fixture(category_key, good=False),
            category_key=category_key,
        )
        rows.append(
            {
                "category_key": category_key,
                "good_fixture_passed": good_report.passed,
                "bad_fixture_blocked": not bad_report.passed,
                "bad_fixture_codes": [
                    finding.code for finding in bad_report.findings
                ],
            }
        )
    return rows


__all__ = [
    "CategoryHardEngineContract",
    "CategoryHardEngineFinding",
    "CategoryHardEngineReport",
    "build_category_engine_fixture",
    "evaluate_category_hard_engine",
    "get_category_hard_engine_contract",
    "load_category_hard_engine_contracts",
    "resolve_category_hard_engine_key",
    "run_category_engine_fixture_benchmark",
]
