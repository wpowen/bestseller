from __future__ import annotations

# ruff: noqa: ANN401
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bestseller.domain.chapter_generation_input import ChapterGenerationInputBundle
from bestseller.services.methodology_application_gate import (
    evaluate_methodology_application_contract,
)


_BLOCKING_SEVERITIES = {"critical", "major"}


@dataclass(frozen=True)
class ChapterPreDraftQualityFinding:
    code: str
    severity: str
    message: str
    repair_hint: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "repair_hint": self.repair_hint,
            "path": self.path,
        }


@dataclass(frozen=True)
class ChapterPreDraftQualityReport:
    passed: bool
    findings: tuple[ChapterPreDraftQualityFinding, ...]
    coverage: float
    gate_name: str = "chapter_predraft_quality_gate"

    @property
    def blocked(self) -> bool:
        return any(finding.severity in _BLOCKING_SEVERITIES for finding in self.findings)

    @property
    def blocking_issues(self) -> tuple[ChapterPreDraftQualityFinding, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity in _BLOCKING_SEVERITIES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "blocked": self.blocked,
            "coverage": self.coverage,
            "findings": [finding.to_dict() for finding in self.findings],
            "blocking_codes": [finding.code for finding in self.blocking_issues],
        }


def evaluate_chapter_predraft_quality(
    bundle: ChapterGenerationInputBundle,
) -> ChapterPreDraftQualityReport:
    """Validate whether chapter-first input is rich enough to draft once.

    This gate is intentionally pre-generation. It blocks thin scene cards or
    missing acceptance targets before the writer model spends tokens producing
    prose that the later commercial gate is already likely to reject.
    """

    findings: list[ChapterPreDraftQualityFinding] = []
    _append_missing_context_findings(findings, bundle.missing_context_keys)
    _append_acceptance_contract_findings(findings, bundle)
    _append_scene_gate_findings(findings, bundle)
    _append_front_position_findings(findings, bundle)
    passed = not any(finding.severity in _BLOCKING_SEVERITIES for finding in findings)
    return ChapterPreDraftQualityReport(
        passed=passed,
        findings=tuple(findings),
        coverage=bundle.coverage,
    )


def _append_missing_context_findings(
    findings: list[ChapterPreDraftQualityFinding],
    missing_context_keys: Sequence[str],
) -> None:
    for key in missing_context_keys:
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_CONTEXT_MISSING",
                severity="major",
                message=f"正文输入包缺少必要上下文：{key}",
                repair_hint=(
                    "先修复章纲、场景卡或上下文物料，让正文写手拿到完整目标后再生成。"
                ),
                path=key,
            )
        )


def _append_acceptance_contract_findings(
    findings: list[ChapterPreDraftQualityFinding],
    bundle: ChapterGenerationInputBundle,
) -> None:
    acceptance = _mapping(bundle.acceptance_contract)
    must_deliver = _sequence_of_mappings(acceptance.get("must_deliver"))
    scene_gate_targets = _sequence_of_mappings(acceptance.get("scene_gate_targets"))
    if not acceptance:
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_ACCEPTANCE_CONTRACT_MISSING",
                severity="critical",
                message="正文生成缺少章节验收契约。",
                repair_hint="生成正文前必须先形成 must_deliver、scene_gate_targets 和门禁阈值。",
                path="acceptance_contract",
            )
        )
        return
    if not must_deliver:
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_MUST_DELIVER_EMPTY",
                severity="critical",
                message="章节验收契约没有必须显性兑现的交付项。",
                repair_hint="补齐章节目标、核心冲突、信息释放和章末钩子后再写正文。",
                path="acceptance_contract.must_deliver",
            )
        )
    if not scene_gate_targets:
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_SCENE_GATE_TARGETS_EMPTY",
                severity="critical",
                message="章节验收契约没有场景级门禁目标。",
                repair_hint="每个场景必须明确 gate_function、visible_progress、reader_payoff 和出场钩子。",
                path="acceptance_contract.scene_gate_targets",
            )
        )

    deliver_by_label = {
        str(item.get("label") or ""): str(item.get("value") or "").strip()
        for item in must_deliver
    }
    for label in ("chapter_goal", "main_conflict", "closing_hook"):
        if not deliver_by_label.get(label):
            findings.append(
                ChapterPreDraftQualityFinding(
                    code="PREDRAFT_REQUIRED_DELIVERY_MISSING",
                    severity="critical" if label == "closing_hook" else "major",
                    message=f"章节验收契约缺少 {label} 的可写交付内容。",
                    repair_hint="把章纲里的目标、冲突和章末钩子具体化为读者能看到的动作/物件/选择。",
                    path=f"acceptance_contract.must_deliver.{label}",
                )
            )
    chapter_number = int(acceptance.get("chapter_number") or 0)
    if chapter_number <= 10 and not deliver_by_label.get("information_release"):
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_INFORMATION_RELEASE_MISSING",
                severity="major",
                message="前十章缺少本章明确的信息释放目标。",
                repair_hint="在章节契约中指定本章只释放哪一个新事实，避免术语堆叠或空转。",
                path="acceptance_contract.must_deliver.information_release",
            )
        )
    front_rules = _mapping(acceptance.get("front_position_rules"))
    knowledge_contract = _mapping(acceptance.get("knowledge_boundary_contract"))
    if chapter_number <= 10 and not knowledge_contract:
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_KNOWLEDGE_BOUNDARY_CONTRACT_MISSING",
                severity="major",
                message="正文生成缺少角色认知边界契约。",
                repair_hint=(
                    "写作前必须明确哪些角色能理解规则术语，哪些角色只能描述异常现象。"
                ),
                path="acceptance_contract.knowledge_boundary_contract",
            )
        )
    elif (
        chapter_number <= 10
        and bool(knowledge_contract.get("allowed_explainers"))
        and not knowledge_contract.get("specialist_rule_terms")
    ):
        # Only nudge when the book has specialist explainers but no rule terms — genres
        # without specialist/supernatural mechanics legitimately have none, so we must
        # not force a detective-style rule glossary onto every book.
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_KNOWLEDGE_BOUNDARY_TERMS_MISSING",
                severity="major",
                message="角色认知边界契约缺少需要管控的规则术语。",
                repair_hint=(
                    "若本书设定包含专业 / 超自然规则，补齐这些术语的可见知识边界"
                    "（用本书自己的设定术语，不要套用其它题材）。"
                ),
                path="acceptance_contract.knowledge_boundary_contract.specialist_rule_terms",
            )
        )
    object_signal_contract = _mapping(acceptance.get("object_signal_contract"))
    if chapter_number <= 10 and not object_signal_contract:
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_OBJECT_SIGNAL_CONTRACT_MISSING",
                severity="major",
                message="正文生成缺少物件信号边界契约。",
                repair_hint=(
                    "明确本书关键物件 / 能力的每种异常分别代表什么、触发条件与不能做什么"
                    "（用本书自己的设定，不要套用其它题材的器物）。"
                ),
                path="acceptance_contract.object_signal_contract",
            )
        )
    methodology_application_contract = _mapping(
        acceptance.get("methodology_application_contract")
    )
    if chapter_number <= 10:
        methodology_report = evaluate_methodology_application_contract(
            chapter_number=chapter_number,
            contract=methodology_application_contract,
            scene_cards=bundle.scenes,
        )
        for issue in methodology_report.blocking_issues:
            findings.append(
                ChapterPreDraftQualityFinding(
                    code=f"PREDRAFT_{issue.code}",
                    severity=issue.severity,
                    message=issue.message,
                    repair_hint=issue.repair_hint,
                    path=f"acceptance_contract.{issue.path}" if issue.path else "",
                )
            )
    if chapter_number <= 10 and not front_rules.get(
        "real_world_evidence_must_be_plausible_or_marked_impossible"
    ):
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_REAL_WORLD_PLAUSIBILITY_RULE_MISSING",
                severity="major",
                message="前十章验收契约没有要求现实证据符合常识或明确标记为不可能证据。",
                repair_hint="补齐现实流程常识校验：快递、门禁、监控、警方、医院等证据不能无解释硬塞。",
                path="acceptance_contract.front_position_rules",
            )
        )


def _append_scene_gate_findings(
    findings: list[ChapterPreDraftQualityFinding],
    bundle: ChapterGenerationInputBundle,
) -> None:
    acceptance = _mapping(bundle.acceptance_contract)
    chapter_number = int(acceptance.get("chapter_number") or 0)
    for index, scene in enumerate(bundle.scenes, start=1):
        scene_path = f"scenes[{index - 1}]"
        required_fields = {
            "gate_function": "场景在商业门禁中的职责",
            "visible_progress": "读者可见的剧情推进",
            "reader_payoff": "本场景交付的爽点/情绪回报",
            "ending_hook_payload": "场景出口钩子或压力",
        }
        for field, description in required_fields.items():
            if not str(scene.get(field) or "").strip():
                findings.append(
                    ChapterPreDraftQualityFinding(
                        code="PREDRAFT_SCENE_GATE_FIELD_MISSING",
                        severity="major",
                        message=f"场景 {scene.get('scene_number') or index} 缺少{description}。",
                        repair_hint=(
                            "补充场景卡：每场必须说明进入后读者会看到什么变化、得到什么回报、"
                            "为什么会继续读下一场。"
                        ),
                        path=f"{scene_path}.{field}",
                    )
                )
        if not scene.get("methodology_contract"):
            findings.append(
                ChapterPreDraftQualityFinding(
                    code="PREDRAFT_SCENE_METHODOLOGY_CONTRACT_MISSING",
                    severity="major",
                    message=f"场景 {scene.get('scene_number') or index} 未接入方法论契约。",
                    repair_hint="先让场景卡承载 reader hook、signature image、cut point 等方法论字段。",
                    path=f"{scene_path}.methodology_contract",
                )
            )
        elif chapter_number <= 10:
            missing_methodology = _missing_front_scene_methodology_fields(scene)
            if missing_methodology:
                findings.append(
                    ChapterPreDraftQualityFinding(
                        code="PREDRAFT_SCENE_METHODOLOGY_CONTRACT_INCOMPLETE",
                        severity="major",
                        message=(
                            f"场景 {scene.get('scene_number') or index} 方法论契约不完整："
                            + "、".join(missing_methodology)
                        ),
                        repair_hint=(
                            "前十章场景卡必须补齐 stakes/pressure_stack/focus_character/"
                            "reveal_mode/signature_image/breakpoint，先控节奏再写正文。"
                        ),
                        path=f"{scene_path}.methodology_contract",
                    )
                )


def _append_front_position_findings(
    findings: list[ChapterPreDraftQualityFinding],
    bundle: ChapterGenerationInputBundle,
) -> None:
    acceptance = _mapping(bundle.acceptance_contract)
    front_rules = _mapping(acceptance.get("front_position_rules"))
    chapter_number = int(acceptance.get("chapter_number") or 0)
    scenes = tuple(bundle.scenes)
    if chapter_number <= 3:
        first_scene = scenes[0] if scenes else {}
        first_gate = str(first_scene.get("gate_function") or "").lower()
        if "opening" not in first_gate and "pressure" not in first_gate:
            findings.append(
                ChapterPreDraftQualityFinding(
                    code="PREDRAFT_GOLDEN_THREE_OPENING_WEAK",
                    severity="critical",
                    message="黄金三章第一场没有明确开篇强牵引职责。",
                    repair_hint="第一场必须以异常、威胁、倒计时、死亡证据或选择压力开局。",
                    path="scenes[0].gate_function",
                )
            )
        if not front_rules.get("opening_must_start_with_pressure"):
            findings.append(
                ChapterPreDraftQualityFinding(
                    code="PREDRAFT_GOLDEN_THREE_PRESSURE_RULE_MISSING",
                    severity="major",
                    message="黄金三章验收契约没有写入开篇压力硬规则。",
                    repair_hint="补齐 front_position_rules.opening_must_start_with_pressure。",
                    path="acceptance_contract.front_position_rules",
                )
            )
    if chapter_number <= 10 and not front_rules.get("ending_hook_must_add_new_information"):
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_FRONT_TEN_HOOK_RULE_MISSING",
                severity="major",
                message="前十章验收契约没有要求章末钩子新增信息。",
                repair_hint="补齐章末钩子必须新增危险、证据、选择或视觉物件的规则。",
                path="acceptance_contract.front_position_rules",
            )
        )
    if not front_rules.get("ending_must_land_on_completed_scene_frame"):
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_ENDING_FRAME_RULE_MISSING",
                severity="major",
                message="章节验收契约没有要求章末落在完成画面帧。",
                repair_hint="补齐章末必须以现场动作、物件变化、完成画面或人物选择收束的规则。",
                path="acceptance_contract.front_position_rules",
            )
        )
    if chapter_number <= 10 and not front_rules.get("object_signal_meaning_must_be_stable"):
        findings.append(
            ChapterPreDraftQualityFinding(
                code="PREDRAFT_OBJECT_SIGNAL_RULE_MISSING",
                severity="major",
                message="前十章验收契约没有要求物件信号含义稳定。",
                repair_hint="补齐 object_signal_meaning_must_be_stable，避免一遇事就发烫。",
                path="acceptance_contract.front_position_rules",
            )
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _missing_front_scene_methodology_fields(scene: Mapping[str, Any]) -> tuple[str, ...]:
    contract = _mapping(scene.get("methodology_contract"))

    def _has_any(*keys: str) -> bool:
        return any(str(contract.get(key) or scene.get(key) or "").strip() for key in keys)

    required_aliases = {
        "stakes": ("stakes", "conflict_stakes"),
        "pressure_stack": (
            "pressure_stack",
            "conflict_buffs",
            "pressure_buffs",
            "pressure",
        ),
        "focus_character": ("focus_character", "spotlight_character", "pov_character"),
        "reveal_mode": ("reveal_mode", "information_control_mode"),
        "signature_image": ("signature_image",),
        "breakpoint": ("breakpoint", "cut_point"),
    }
    return tuple(
        field for field, aliases in required_aliases.items() if not _has_any(*aliases)
    )
