"""Methodology application contracts for outline and pre-draft gates.

The methodology decks are useful only when a chapter records where each
method is applied and which gate will measure it. This module makes that
mapping explicit before prose drafting.
"""

from __future__ import annotations

# ruff: noqa: ANN401,RUF001
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.services.methodology_book_selector import (
    BookMethodologySelectionContext,
    select_book_methodology_cards,
)

_BLOCKING_SEVERITIES = {"critical", "major"}

_FRONT_TEN_REQUIRED_CARDS = {
    "plova.mainline.stage_goal_obstacle_result",
    "platform.anchor_character_gene",
    "platform.character_desire_collision",
    "platform.information_gap",
    "platform.hype_four_beat",
}

_GOLDEN_THREE_REQUIRED_CARDS = {
    "plova.opening.anti_pitfall",
    "plova.opening.reader_desire_over_noise",
    "plova.opening.three_chapter_function",
}

_LOW_SIGNAL_EMOTION_MARKERS = (
    "从被动承压转为做出一个带代价的判断",
    "读者能看见害怕、犹豫或信任变化",
    "保持本章压力递进，并把选择、代价或线索推到下一拍",
    "推动剧情",
    "提升紧张感",
    "制造悬念",
)

_LOW_SIGNAL_RELATIONSHIP_MARKERS = (
    "本场必须改变至少一组人物的信任、亏欠或隐瞒状态",
    "改变至少一组人物",
    "形成一笔可回响的人情债",
    "下一场必须改变信任或行动",
    "深化关系",
    "关系变化",
)

_DECISION_PROTOCOL_REQUIRED_FIELDS = (
    "chosen_action",
    "alternatives_rejected",
    "why_this_not_that",
    "constraint",
    "wrong_choice_loss",
)

_RELATIONSHIP_DEBT_REQUIRED_FIELDS = (
    "debtor",
    "creditor",
    "evidence_or_handle",
    "due_condition",
    "breach_consequence",
    "repayment_modes",
)


@dataclass(frozen=True)
class MethodologyApplicationFinding:
    code: str
    severity: str
    message: str
    repair_hint: str
    path: str = ""
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "repair_hint": self.repair_hint,
            "path": self.path,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class MethodologyApplicationReport:
    passed: bool
    findings: tuple[MethodologyApplicationFinding, ...]
    score: float
    application_count: int
    gate_name: str = "methodology_application_gate"

    @property
    def blocked(self) -> bool:
        return any(finding.blocking for finding in self.findings)

    @property
    def blocking_issues(self) -> tuple[MethodologyApplicationFinding, ...]:
        return tuple(finding for finding in self.findings if finding.blocking)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "blocked": self.blocked,
            "score": self.score,
            "application_count": self.application_count,
            "findings": [finding.to_dict() for finding in self.findings],
            "blocking_codes": [finding.code for finding in self.blocking_issues],
        }


def build_methodology_application_contract(
    *,
    chapter_number: int,
    chapter_title: str | None = None,
    chapter_contract: Mapping[str, Any] | None = None,
    scene_cards: Sequence[Mapping[str, Any] | Any] = (),
) -> dict[str, Any]:
    """Build the explicit card-to-node application map for a chapter."""

    chapter_contract = _mapping(chapter_contract)
    front_ten = chapter_number <= 10
    golden_three = chapter_number <= 3
    applications: list[dict[str, Any]] = []

    def _add(
        *,
        card_id: str,
        profile_id: str,
        scope: str,
        node_path: str,
        required_fields: Sequence[str],
        evidence_fields: Sequence[str],
        gate: str,
        mode: str,
        measurement: Sequence[str],
    ) -> None:
        applications.append(
            {
                "card_id": card_id,
                "profile_id": profile_id,
                "scope": scope,
                "stage": ["planning", "drafting", "review"],
                "node_path": node_path,
                "required_contract_fields": list(required_fields),
                "evidence_fields": list(evidence_fields),
                "gate": gate,
                "mode": mode,
                "measurement": list(measurement),
            }
        )

    if golden_three:
        _add(
            card_id="plova.opening.anti_pitfall",
            profile_id="plova_structured_writing_v1",
            scope="chapter",
            node_path="chapter",
            required_fields=(
                "protagonist_situation",
                "abnormal_pressure",
                "first_question",
                "reader_desire",
            ),
            evidence_fields=(
                "chapter.opening_situation",
                "scenes[0].purpose.story",
                "scenes[0].hook_requirement",
                "acceptance_contract.must_deliver",
            ),
            gate="opening_three_function",
            mode="block",
            measurement=(
                "chapter_outline_readiness_gate",
                "chapter_predraft_quality_gate",
                "outline_llm_commercial_judge",
                "chapter_llm_commercial_judge",
            ),
        )
        _add(
            card_id="plova.opening.reader_desire_over_noise",
            profile_id="plova_structured_writing_v1",
            scope="chapter",
            node_path="chapter",
            required_fields=("stimulus", "reader_question", "protagonist_desire", "next_step"),
            evidence_fields=(
                "chapter.main_conflict",
                "chapter.hook_description",
                "chapter.methodology_contract.loop_position",
            ),
            gate="opening_three_function",
            mode="block",
            measurement=(
                "chapter_predraft_quality_gate",
                "outline_llm_commercial_judge",
            ),
        )
        _add(
            card_id="plova.opening.three_chapter_function",
            profile_id="plova_structured_writing_v1",
            scope="chapter",
            node_path=f"chapter[{chapter_number}]",
            required_fields=(
                "chapter_1_question",
                "chapter_2_cost_proof",
                "chapter_3_long_desire",
                "next_problem",
            ),
            evidence_fields=(
                "chapter.chapter_goal",
                "chapter.information_revealed",
                "chapter.hook_description",
            ),
            gate="opening_three_function",
            mode="block",
            measurement=(
                "outline_llm_commercial_judge",
                "chapter_window_llm_judge",
            ),
        )

    if front_ten:
        _add(
            card_id="plova.mainline.stage_goal_obstacle_result",
            profile_id="plova_structured_writing_v1",
            scope="chapter",
            node_path="chapter.methodology_contract",
            required_fields=("goal", "obstacle", "action", "cost", "result", "next_desire"),
            evidence_fields=(
                "chapter.causal_contract",
                "chapter.event_cycle_contract",
                "scenes[*].entry_state",
                "scenes[*].exit_state",
            ),
            gate="chapter_causality",
            mode="block",
            measurement=(
                "planning_readiness_gate",
                "chapter_outline_readiness_gate",
                "chapter_predraft_quality_gate",
            ),
        )
        _add(
            card_id="platform.anchor_character_gene",
            profile_id="platform_character_debt_v1",
            scope="chapter",
            node_path="chapter.character_agency",
            required_fields=(
                "value_axis",
                "non_negotiable",
                "action_loop",
                "capability_basis",
                "collateral_limit",
            ),
            evidence_fields=(
                "chapter.protagonist_choice",
                "chapter.methodology_contract.visible_action_or_reaction",
                "object_signal_contract.forbidden_shortcut",
            ),
            gate="anchor_agency",
            mode="block",
            measurement=("outline_llm_commercial_judge", "chapter_llm_commercial_judge"),
        )
        _add(
            card_id="platform.character_desire_collision",
            profile_id="platform_character_debt_v1",
            scope="scene",
            node_path="scenes[*].methodology_contract",
            required_fields=("desire", "obstacle", "choice_axis", "cost", "next_pressure"),
            evidence_fields=(
                "scenes[*].purpose.emotion",
                "scenes[*].methodology_contract.conflict_stakes",
                "scenes[*].methodology_contract.pressure_stack",
                "scenes[*].exit_state",
            ),
            gate="chapter_causality",
            mode="block",
            measurement=(
                "chapter_outline_readiness_gate",
                "chapter_predraft_quality_gate",
                "chapter_llm_commercial_judge",
            ),
        )
        _add(
            card_id="platform.character_debt_ledger",
            profile_id="platform_character_debt_v1",
            scope="chapter",
            node_path="chapter.relationship_debts",
            required_fields=(
                "debtor",
                "creditor",
                "evidence_or_handle",
                "due_condition",
                "breach_consequence",
                "repayment_modes",
            ),
            evidence_fields=(
                "chapter.methodology_contract.relationship_debts",
                "scenes[*].methodology_contract.relationship_debts",
                "chapter.hook_description",
            ),
            gate="character_debt",
            # 2026-07-04: block→warn. 强制每章产出 debtor/creditor 六字段台账是
            # 债务同质化的结构性根因（55a3d4c 只改了话术没动结构）——人际张力
            # 保留为建议，不再作为硬合约逼出记账型设定。
            mode="warn",
            measurement=(
                "chapter_predraft_quality_gate",
                "chapter_llm_commercial_judge",
            ),
        )
        _add(
            card_id="platform.information_gap",
            profile_id="platform_character_debt_v1",
            scope="scene",
            node_path="scenes[*].information_control",
            required_fields=(
                "reader_knows",
                "character_does_not_know",
                "wrong_assumption",
                "trigger",
                "payoff_window",
            ),
            evidence_fields=(
                "scenes[*].methodology_contract.reveal_mode",
                "scenes[*].hook_requirement",
                "knowledge_boundary_contract",
            ),
            gate="information_gap",
            mode="warn",
            measurement=(
                "chapter_outline_readiness_gate",
                "outline_llm_commercial_judge",
            ),
        )
        _add(
            card_id="platform.hype_four_beat",
            profile_id="platform_character_debt_v1",
            scope="chapter",
            node_path="chapter.payoff",
            required_fields=(
                "pressure",
                "trump_card_cue",
                "domination_payoff",
                "audience_feedback",
                "tangible_gain",
                "next_seed",
            ),
            evidence_fields=(
                "scenes[*].gate_function",
                "scenes[*].reader_payoff",
                "chapter.hook_description",
            ),
            gate="hype_four_beat",
            mode="warn",
            measurement=(
                "chapter_predraft_quality_gate",
                "chapter_llm_commercial_judge",
            ),
        )

    book_methodology_card_ids: list[str] = []
    try:
        book_selection = select_book_methodology_cards(
            BookMethodologySelectionContext(
                stage="outline_chapter",
                scope="chapter",
                chapter_no=chapter_number,
                max_cards=3,
                token_budget=500,
            )
        )
        scene_selection = select_book_methodology_cards(
            BookMethodologySelectionContext(
                stage="prose_scene",
                scope="scene",
                chapter_no=chapter_number,
                max_cards=3,
                token_budget=500,
            )
        )
    except Exception:
        book_selection = None
        scene_selection = None

    if book_selection is not None:
        for selected in book_selection.cards:
            applications.append(selected.to_application(node_path="chapter.methodology_contract"))
            book_methodology_card_ids.append(selected.card_id)
    if scene_selection is not None:
        for selected in scene_selection.cards:
            applications.append(selected.to_application(node_path="scenes[*].methodology_contract"))
            book_methodology_card_ids.append(selected.card_id)

    profile_ids = [
        "plova_structured_writing_v1",
        "platform_character_debt_v1",
    ] if front_ten else ["plova_structured_writing_v1"]
    if book_methodology_card_ids:
        profile_ids.append("books_core_v1")

    return {
        "schema_version": "methodology-application-contract.v1",
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "profile_ids": profile_ids,
        "book_methodology_lineage": {
            "card_ids": list(dict.fromkeys(book_methodology_card_ids)),
            "priority_order": [
                "platform_required",
                "writing_methodology.yaml",
                "book_core_deck",
                "book_advisory",
            ],
        },
        "applications": applications,
        "measurement_summary": {
            "outline": [
                "planning_readiness_gate",
                "chapter_outline_readiness_gate",
                "outline_llm_commercial_judge",
            ],
            "predraft": ["chapter_predraft_quality_gate"],
            "draft": ["chapter_llm_commercial_judge", "chapter_window_llm_judge"],
        },
        "chapter_contract_digest": {
            key: chapter_contract.get(key)
            for key in (
                "visible_action_or_reaction",
                "conflict_stakes",
                "loop_position",
                "relationship_debts",
                "decision_protocol",
                "character_agency_contract",
            )
            if chapter_contract.get(key) not in (None, "", [], {})
        },
    }


def evaluate_methodology_application(
    *,
    chapter_number: int,
    chapter_metadata: Mapping[str, Any] | None = None,
    scene_cards: Sequence[Mapping[str, Any] | Any] = (),
    front_chapter_limit: int = 10,
) -> MethodologyApplicationReport:
    metadata = _mapping(chapter_metadata)
    return evaluate_methodology_application_contract(
        chapter_number=chapter_number,
        contract=_mapping(metadata.get("methodology_application_contract")),
        scene_cards=scene_cards,
        front_chapter_limit=front_chapter_limit,
    )


def evaluate_methodology_application_contract(
    *,
    chapter_number: int,
    contract: Mapping[str, Any] | None,
    scene_cards: Sequence[Mapping[str, Any] | Any] = (),
    front_chapter_limit: int = 10,
) -> MethodologyApplicationReport:
    findings: list[MethodologyApplicationFinding] = []
    contract = _mapping(contract)
    front_chapter = chapter_number <= front_chapter_limit
    applications = _sequence_of_mappings(contract.get("applications"))

    if front_chapter and not contract:
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_APPLICATION_CONTRACT_MISSING",
                severity="critical",
                message="前十章缺少方法论应用记录，无法确认哪些方法论真正进入细纲与正文输入。",
                repair_hint=(
                    "生成正文前写入 methodology_application_contract：列出卡片、节点、"
                    "证据字段和衡量 gate。"
                ),
                path="chapter.metadata.methodology_application_contract",
            )
        )
    elif front_chapter:
        _append_contract_payload_findings(
            findings,
            chapter_number=chapter_number,
            applications=applications,
            contract=contract,
        )

    if front_chapter:
        _append_scene_repetition_findings(findings, scene_cards)

    blocking = any(finding.blocking for finding in findings)
    penalty = min(0.95, len(findings) * 0.12)
    return MethodologyApplicationReport(
        passed=not blocking,
        findings=tuple(findings),
        score=round(max(0.0, 1.0 - penalty), 3),
        application_count=len(applications),
    )


def _append_contract_payload_findings(
    findings: list[MethodologyApplicationFinding],
    *,
    chapter_number: int,
    applications: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> None:
    card_ids = {str(item.get("card_id") or "").strip() for item in applications}
    required = set(_FRONT_TEN_REQUIRED_CARDS)
    if chapter_number <= 3:
        required.update(_GOLDEN_THREE_REQUIRED_CARDS)
    missing = sorted(card_id for card_id in required if card_id not in card_ids)
    if missing:
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_REQUIRED_CARD_NOT_APPLIED",
                severity="critical",
                message="章节缺少必需方法论卡片应用：" + "、".join(missing),
                repair_hint="补齐方法论应用记录，前十章必须覆盖开篇、主线、主角能动性、人物关系张力、信息差和爽点兑现。",
                path="methodology_application_contract.applications",
            )
        )

    for index, item in enumerate(applications):
        missing_fields = [
            field
            for field in (
                "card_id",
                "profile_id",
                "scope",
                "node_path",
                "required_contract_fields",
                "evidence_fields",
                "gate",
                "mode",
                "measurement",
            )
            if not item.get(field)
        ]
        if missing_fields:
            findings.append(
                MethodologyApplicationFinding(
                    code="METHODOLOGY_APPLICATION_ENTRY_INCOMPLETE",
                    severity="major",
                    message=(
                        f"方法论应用条目 {index + 1} 缺少字段："
                        + "、".join(missing_fields)
                    ),
                    repair_hint="每个方法论条目必须写清应用节点、证据字段、门禁和衡量方式。",
                    path=f"methodology_application_contract.applications[{index}]",
                )
            )

    digest = _mapping(contract.get("chapter_contract_digest"))
    decision_protocol = _mapping(digest.get("decision_protocol"))
    missing_decision_fields = [
        field for field in _DECISION_PROTOCOL_REQUIRED_FIELDS if not decision_protocol.get(field)
    ]
    if missing_decision_fields:
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_PROTAGONIST_DECISION_PROTOCOL_MISSING",
                severity="critical",
                message=(
                    "前十章主角选择缺少 why-this-not-that 决策记录："
                    + "、".join(missing_decision_fields)
                ),
                repair_hint=(
                    "为章节补齐 chosen_action、alternatives_rejected、why_this_not_that、"
                    "constraint、wrong_choice_loss，避免主角像按算法执行规则。"
                ),
                path="methodology_application_contract.chapter_contract_digest.decision_protocol",
            )
        )


def _append_scene_repetition_findings(
    findings: list[MethodologyApplicationFinding],
    scene_cards: Sequence[Mapping[str, Any] | Any],
) -> None:
    hooks = [
        _normalize_repetition_key(_scene_text(scene, "hook_requirement"))
        for scene in scene_cards
    ]
    duplicate_hooks = _duplicates(hooks, min_length=8)
    if duplicate_hooks:
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_SCENE_HOOK_REPEATED",
                severity="critical",
                message="同章场景出口钩子重复，细纲会把正文写成模板循环。",
                repair_hint="每场出口必须新增不同的危险、证据、选择或物件变化；不要用同一句钩子收两场。",
                path="scene_cards[*].hook_requirement",
            )
        )

    signatures = [
        _normalize_repetition_key(
            _methodology(scene).get("signature_image") or _scene_text(scene, "signature_image")
        )
        for scene in scene_cards
    ]
    duplicate_signatures = _duplicates(signatures, min_length=8)
    if duplicate_signatures:
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_SIGNATURE_IMAGE_REPEATED",
                severity="major",
                message="同章场景记忆画面重复，方法论没有形成可区分的读者记忆点。",
                repair_hint="为每场设计不同的物件、身体变化、空间异常或现实证据画面。",
                path="scene_cards[*].metadata.methodology_contract.signature_image",
            )
        )

    emotions = [
        _normalize_repetition_key(_purpose(scene).get("emotion"))
        for scene in scene_cards
    ]
    duplicate_emotions = _duplicates(emotions, min_length=12)
    low_signal_count = sum(
        1
        for value in emotions
        if value and any(marker in value for marker in _LOW_SIGNAL_EMOTION_MARKERS)
    )
    if duplicate_emotions or (scene_cards and low_signal_count >= max(2, len(scene_cards) // 2)):
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_SCENE_EMOTION_TEMPLATE_REPEATED",
                severity="major",
                message="场景情绪任务模板化，无法驱动人物欲望碰撞或人物关系变化。",
                repair_hint="把每场 emotion 改成人物当场的具体恐惧、隐瞒、让步、错判或信任变化。",
                path="scene_cards[*].purpose.emotion",
            )
        )

    relationship_debts = [
        _methodology(scene).get("relationship_debts")
        for scene in scene_cards
    ]
    missing_structured_debts = [
        index + 1
        for index, value in enumerate(relationship_debts)
        if not _has_structured_relationship_debt(value)
    ]
    if missing_structured_debts:
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_RELATIONSHIP_DEBT_STRUCTURED_MISSING",
                # 2026-07-04: critical→minor。每场强制六字段 debtor/creditor 结构
                # 是债务同质化根因，降为建议不再阻断。
                severity="minor",
                message=(
                    "场景人际张力缺少结构化记录：第"
                    + "、".join(str(item) for item in missing_structured_debts[:8])
                    + "场"
                ),
                repair_hint=(
                    "每场 relationship_debts 至少一条包含 debtor、creditor、"
                    "evidence_or_handle、due_condition、breach_consequence、repayment_modes。"
                    "这些字段记录的是人际亏欠/承诺/把柄（谁对谁负有什么、何时必须兑现、失约后果），"
                    "不是金钱债务；严禁因此在剧情里加入记账、欠条、账本、债契类设定。"
                ),
                path="scene_cards[*].metadata.methodology_contract.relationship_debts",
            )
        )

    relationship_debt_texts = [
        _normalize_repetition_key(value)
        for value in relationship_debts
    ]
    placeholder_debts = [
        value
        for value in relationship_debt_texts
        if value and any(marker in value for marker in _LOW_SIGNAL_RELATIONSHIP_MARKERS)
    ]
    if placeholder_debts:
        findings.append(
            MethodologyApplicationFinding(
                code="METHODOLOGY_RELATIONSHIP_DEBT_PLACEHOLDER",
                # 2026-07-04: 与 STRUCTURED_MISSING 同步降级——字段本身已是建议级。
                severity="minor",
                message="场景人际张力仍是占位模板，方法论没有落到具体关系变化。",
                repair_hint=(
                    "把 relationship_debts 改成具体的人际亏欠或承诺：谁对谁负有什么、"
                    "因为什么、下一场如何回响；不得写成金钱债务或记账设定。"
                ),
                path="scene_cards[*].metadata.methodology_contract.relationship_debts",
            )
        )


def _has_structured_relationship_debt(value: Any) -> bool:
    items = _sequence_of_mappings(value)
    if isinstance(value, Mapping):
        items = (value,)
    return any(
        all(_text(item.get(field)) for field in _RELATIONSHIP_DEBT_REQUIRED_FIELDS)
        for item in items
    )


def _duplicates(values: Sequence[str], *, min_length: int) -> list[str]:
    filtered = [value for value in values if len(value) >= min_length]
    counter = Counter(filtered)
    return sorted(value for value, count in counter.items() if count > 1)


def _scene_text(scene: Mapping[str, Any] | Any, key: str) -> str:
    value = _get(scene, key)
    if value is None:
        metadata = _mapping(_get(scene, "metadata_json") or _get(scene, "metadata"))
        value = metadata.get(key)
    return _text(value)


def _purpose(scene: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    return _mapping(_get(scene, "purpose"))


def _methodology(scene: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    direct = _mapping(_get(scene, "methodology_contract"))
    if direct:
        return direct
    metadata = _mapping(_get(scene, "metadata_json") or _get(scene, "metadata"))
    return _mapping(metadata.get("methodology_contract"))


def _get(value: Mapping[str, Any] | Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return "；".join(_text(item) for item in value.values() if _text(item))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "；".join(_text(item) for item in value if _text(item))
    return str(value).strip()


def _normalize_repetition_key(value: Any) -> str:
    text = _text(value)
    return "".join(text.split())


__all__ = [
    "MethodologyApplicationFinding",
    "MethodologyApplicationReport",
    "build_methodology_application_contract",
    "evaluate_methodology_application",
    "evaluate_methodology_application_contract",
]
