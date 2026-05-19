"""Emotion-driven narrative contracts and pure validation gates.

This module turns the emotion-driven writing methodology into structured,
JSON-compatible contracts that can be stored beside ``StoryDesignKernel`` and
consumed by planner/writer/review layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EndingType = Literal["HE", "BE", "open"]


class EmpathyContract(BaseModel, frozen=True):
    """Character-level empathy chain for a chapter range."""

    model_config = ConfigDict(extra="ignore")

    contract_id: str = ""
    character_key: str = ""
    chapter_range: str = ""
    situation: str = ""
    current_desire: str = ""
    fear_or_loss: str = ""
    flaw_pressure: str = ""
    sensory_entry: str = ""
    judgment_logic: str = ""
    emotional_reaction: str = ""
    reasonable_action: str = ""
    consequence: str = ""


class BombContract(BaseModel, frozen=True):
    """Reader-facing information-gap / danger contract."""

    model_config = ConfigDict(extra="ignore")

    bomb_id: str = ""
    bomb_type: str = ""
    chapter_range: str = ""
    reader_knows: str = ""
    character_blindspot: str = ""
    danger: str = ""
    trigger_condition: str = ""
    countdown: str = ""
    consequence: str = ""
    payoff_window: str = ""
    rational_ignorance: str = ""
    escalation_steps: list[str] = Field(default_factory=list)


class AntagonistMoralContract(BaseModel, frozen=True):
    """Moral complexity contract for memorable antagonists."""

    model_config = ConfigDict(extra="ignore")

    antagonist_key: str = ""
    chapter_range: str = ""
    public_mask: str = ""
    real_good_deeds: list[str] = Field(default_factory=list)
    hidden_desire: str = ""
    fear_of_loss: str = ""
    cracks: list[str] = Field(default_factory=list)
    first_boundary_crossing: str = ""
    self_justification: str = ""
    collapse_wound: str = ""
    target_reader_response: str = ""


class EndingTextureContract(BaseModel, frozen=True):
    """HE/BE ending texture contract."""

    model_config = ConfigDict(extra="ignore")

    ending_type: EndingType = "open"
    core_wish_fulfilled: str = ""
    relationship_settlement: str = ""
    irreversible_cost_retained: str = ""
    theme_answer: str = ""
    future_open: str = ""
    tragic_causality: str = ""
    active_value_choice: str = ""
    aesthetic_callback: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw = str(data.get("ending_type") or "").strip()
        upper = raw.upper()
        if upper.startswith("HE"):
            data["ending_type"] = "HE"
        elif upper.startswith("BE"):
            data["ending_type"] = "BE"
        elif raw:
            data["ending_type"] = "open"
        return data


class EmotionChainBeat(BaseModel, frozen=True):
    """Chapter-range emotion movement plan."""

    model_config = ConfigDict(extra="ignore")

    chapter_range: str = ""
    target_reader_emotion: str = ""
    reader_waiting_for: str = ""
    reader_worry: str = ""
    pressure_source: str = ""
    payoff_or_aftereffect: str = ""
    callback: str = ""


class EmotionDrivenKernel(BaseModel, frozen=True):
    """Project-level emotion design contract."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    reader_emotion_promise: str = ""
    primary_reader_waiting: list[str] = Field(default_factory=list)
    empathy_contracts: list[EmpathyContract] = Field(default_factory=list)
    bomb_contracts: list[BombContract] = Field(default_factory=list)
    antagonist_moral_contracts: list[AntagonistMoralContract] = Field(default_factory=list)
    ending_texture_contract: EndingTextureContract | None = None
    emotion_chain: list[EmotionChainBeat] = Field(default_factory=list)
    callback_motifs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        motifs = data.get("callback_motifs")
        if isinstance(motifs, list):
            normalized: list[str] = []
            for motif in motifs:
                if isinstance(motif, str) and motif.strip():
                    normalized.append(motif.strip())
                elif isinstance(motif, dict):
                    parts = [
                        str(motif.get(key) or "").strip()
                        for key in ("motif_id", "label", "symbol", "meaning", "callback", "payoff")
                        if str(motif.get(key) or "").strip()
                    ]
                    if parts:
                        normalized.append("：".join(parts[:3]))
            data["callback_motifs"] = normalized
        return data


@dataclass(frozen=True)
class EmotionContractIssue:
    code: str
    severity: str
    path: str
    message: str
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmotionContractGateReport:
    passed: bool
    issues: tuple[EmotionContractIssue, ...] = field(default_factory=tuple)

    @property
    def critical_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "critical")


def emotion_driven_kernel_from_dict(data: dict[str, Any]) -> EmotionDrivenKernel:
    """Validate and hydrate an emotion-driven kernel payload."""

    return EmotionDrivenKernel.model_validate(data)


def emotion_driven_kernel_to_dict(kernel: EmotionDrivenKernel) -> dict[str, Any]:
    """Serialize a kernel into JSON-compatible data."""

    return kernel.model_dump(mode="json")


def extract_chapter_emotion_contract(
    kernel: EmotionDrivenKernel | dict[str, Any] | None,
    *,
    chapter_number: int,
) -> dict[str, Any]:
    """Return only contracts whose chapter range covers ``chapter_number``."""

    if kernel is None:
        return _empty_chapter_contract()
    if isinstance(kernel, dict):
        kernel = emotion_driven_kernel_from_dict(kernel)

    return {
        "reader_emotion_promise": kernel.reader_emotion_promise,
        "primary_reader_waiting": list(kernel.primary_reader_waiting),
        "empathy_contracts": [
            item.model_dump(mode="json")
            for item in kernel.empathy_contracts
            if _range_contains(item.chapter_range, chapter_number)
        ],
        "bomb_contracts": [
            item.model_dump(mode="json")
            for item in kernel.bomb_contracts
            if _range_contains(item.chapter_range, chapter_number)
        ],
        "antagonist_moral_contracts": [
            item.model_dump(mode="json")
            for item in kernel.antagonist_moral_contracts
            if _range_contains(item.chapter_range, chapter_number)
        ],
        "ending_texture_contract": (
            kernel.ending_texture_contract.model_dump(mode="json")
            if kernel.ending_texture_contract
            else None
        ),
        "emotion_chain": [
            item.model_dump(mode="json")
            for item in kernel.emotion_chain
            if _range_contains(item.chapter_range, chapter_number)
        ],
        "callback_motifs": list(kernel.callback_motifs),
    }


def render_emotion_driven_kernel_prompt_block(
    kernel: EmotionDrivenKernel | dict[str, Any] | None,
    *,
    chapter_number: int | None = None,
    language: str = "zh-CN",
) -> str:
    """Render a compact writer/planner-facing emotion contract block."""

    if kernel is None:
        return ""
    if isinstance(kernel, dict):
        kernel = emotion_driven_kernel_from_dict(kernel)

    is_en = language.lower().startswith("en")
    if chapter_number is not None:
        scoped = extract_chapter_emotion_contract(kernel, chapter_number=chapter_number)
        empathy = scoped["empathy_contracts"]
        bombs = scoped["bomb_contracts"]
        antagonists = scoped["antagonist_moral_contracts"]
        chain = scoped["emotion_chain"]
    else:
        empathy = [item.model_dump(mode="json") for item in kernel.empathy_contracts[:3]]
        bombs = [item.model_dump(mode="json") for item in kernel.bomb_contracts[:3]]
        antagonists = [
            item.model_dump(mode="json") for item in kernel.antagonist_moral_contracts[:3]
        ]
        chain = [item.model_dump(mode="json") for item in kernel.emotion_chain[:3]]

    lines: list[str] = (
        ["[emotion_driven_core | reader emotion contract]"]
        if is_en
        else ["【emotion_driven_core · 读者情绪合同】"]
    )
    if kernel.reader_emotion_promise:
        lines.append(
            f"- Reader emotion promise: {kernel.reader_emotion_promise}"
            if is_en
            else f"- 读者情绪承诺: {kernel.reader_emotion_promise}"
        )
    if kernel.primary_reader_waiting:
        joined = ", ".join(kernel.primary_reader_waiting[:5])
        lines.append(
            f"- Primary reader waiting: {joined}" if is_en else f"- 读者正在等待: {joined}"
        )

    for item in empathy[:2]:
        lines.append(_render_empathy_item(item, is_en=is_en))
    for item in bombs[:2]:
        lines.append(_render_bomb_item(item, is_en=is_en))
    for item in antagonists[:2]:
        lines.append(_render_antagonist_item(item, is_en=is_en))
    for item in chain[:2]:
        lines.append(_render_chain_item(item, is_en=is_en))

    ending = kernel.ending_texture_contract
    if ending is not None:
        if is_en:
            lines.append(
                "- Ending texture: "
                f"{ending.ending_type}; fulfilled={ending.core_wish_fulfilled}; "
                f"irreversible cost={ending.irreversible_cost_retained}; "
                f"theme={ending.theme_answer}"
            )
        else:
            lines.append(
                "- 结局纹理: "
                f"{ending.ending_type}; 核心愿望={ending.core_wish_fulfilled}; "
                f"不可逆代价={ending.irreversible_cost_retained}; "
                f"主题回答={ending.theme_answer}"
            )

    if kernel.callback_motifs:
        joined = ", ".join(kernel.callback_motifs[:6])
        lines.append(f"- Callback motifs: {joined}" if is_en else f"- 回收意象: {joined}")

    return "\n".join(line for line in lines if line.strip())


def evaluate_emotion_contracts(
    kernel: EmotionDrivenKernel | dict[str, Any] | None,
) -> EmotionContractGateReport:
    """Run deterministic contract completeness checks."""

    if kernel is None:
        return EmotionContractGateReport(
            passed=False,
            issues=(
                EmotionContractIssue(
                    code="EMOTION_KERNEL_MISSING",
                    severity="critical",
                    path="emotion_driven_kernel",
                    message="EmotionDrivenKernel is missing.",
                ),
            ),
        )
    if isinstance(kernel, dict):
        kernel = emotion_driven_kernel_from_dict(kernel)

    issues: list[EmotionContractIssue] = []
    if not _present(kernel.reader_emotion_promise):
        issues.append(
            EmotionContractIssue(
                code="EMOTION_PROMISE_MISSING",
                severity="major",
                path="reader_emotion_promise",
                message="Reader emotion promise is missing.",
            )
        )

    if not kernel.empathy_contracts:
        issues.append(
            EmotionContractIssue(
                code="EMPATHY_CONTRACT_MISSING",
                severity="critical",
                path="empathy_contracts",
                message="At least one empathy contract is required.",
            )
        )
    for index, item in enumerate(kernel.empathy_contracts):
        missing = _missing_fields(
            item,
            (
                "situation",
                "current_desire",
                "fear_or_loss",
                "sensory_entry",
                "judgment_logic",
                "reasonable_action",
                "consequence",
            ),
        )
        if missing:
            issues.append(
                EmotionContractIssue(
                    code="EMPATHY_CHAIN_MISSING",
                    severity="critical",
                    path=f"empathy_contracts[{index}]",
                    message=(
                        "Empathy chain must include situation, desire, sensory "
                        "entry, judgment, action, and consequence."
                    ),
                    missing_fields=missing,
                )
            )

    for index, item in enumerate(kernel.bomb_contracts):
        missing = _missing_fields(
            item,
            (
                "reader_knows",
                "character_blindspot",
                "danger",
                "trigger_condition",
                "countdown",
                "consequence",
                "payoff_window",
                "rational_ignorance",
            ),
        )
        if missing:
            code = (
                "BOMB_TRIGGER_MISSING"
                if "trigger_condition" in missing
                else "BOMB_CONTRACT_INCOMPLETE"
            )
            issues.append(
                EmotionContractIssue(
                    code=code,
                    severity="critical",
                    path=f"bomb_contracts[{index}]",
                    message=(
                        "Bomb contract must define information gap, "
                        "trigger/countdown, consequence, payoff window, and "
                        "rational ignorance."
                    ),
                    missing_fields=missing,
                )
            )

    for index, item in enumerate(kernel.antagonist_moral_contracts):
        missing = _missing_fields(
            item,
            (
                "public_mask",
                "hidden_desire",
                "fear_of_loss",
                "first_boundary_crossing",
                "self_justification",
                "collapse_wound",
            ),
        )
        if not item.real_good_deeds:
            missing = (*missing, "real_good_deeds")
        if not item.cracks:
            missing = (*missing, "cracks")
        if missing:
            issues.append(
                EmotionContractIssue(
                    code="ANTAGONIST_MASK_FLAT",
                    severity="major",
                    path=f"antagonist_moral_contracts[{index}]",
                    message=(
                        "Antagonist contract needs real good, hidden desire, "
                        "cracks, rationalization, and collapse wound."
                    ),
                    missing_fields=missing,
                )
            )

    issues.extend(_evaluate_ending(kernel.ending_texture_contract))

    return EmotionContractGateReport(
        passed=not any(issue.severity in {"critical", "major"} for issue in issues),
        issues=tuple(issues),
    )


def _evaluate_ending(ending: EndingTextureContract | None) -> tuple[EmotionContractIssue, ...]:
    if ending is None:
        return (
            EmotionContractIssue(
                code="ENDING_TEXTURE_MISSING",
                severity="major",
                path="ending_texture_contract",
                message="Ending texture contract is missing.",
            ),
        )

    issues: list[EmotionContractIssue] = []
    if ending.ending_type == "HE":
        missing = _missing_fields(
            ending,
            (
                "core_wish_fulfilled",
                "relationship_settlement",
                "irreversible_cost_retained",
                "theme_answer",
                "future_open",
            ),
        )
        if missing:
            code = (
                "ENDING_COST_ERASED"
                if "irreversible_cost_retained" in missing
                else "HE_TEXTURE_INCOMPLETE"
            )
            issues.append(
                EmotionContractIssue(
                    code=code,
                    severity="major",
                    path="ending_texture_contract",
                    message=(
                        "HE must fulfill happiness while retaining "
                        "irreversible cost and answering theme."
                    ),
                    missing_fields=missing,
                )
            )
    elif ending.ending_type == "BE":
        missing = _missing_fields(
            ending,
            (
                "core_wish_fulfilled",
                "tragic_causality",
                "active_value_choice",
                "irreversible_cost_retained",
                "aesthetic_callback",
            ),
        )
        if "tragic_causality" in missing:
            issues.append(
                EmotionContractIssue(
                    code="TRAGEDY_CAUSALITY_WEAK",
                    severity="critical",
                    path="ending_texture_contract",
                    message="BE must have unavoidable causality, not arbitrary misery.",
                    missing_fields=("tragic_causality",),
                )
            )
        if "active_value_choice" in missing:
            issues.append(
                EmotionContractIssue(
                    code="TRAGEDY_CHOICE_MISSING",
                    severity="critical",
                    path="ending_texture_contract",
                    message="BE must make the character actively choose the tragic value tradeoff.",
                    missing_fields=("active_value_choice",),
                )
            )
        if "aesthetic_callback" in missing:
            issues.append(
                EmotionContractIssue(
                    code="ENDING_CALLBACK_MISSING",
                    severity="major",
                    path="ending_texture_contract",
                    message=(
                        "BE must use a callback motif, line, object, or "
                        "scene for emotional aftertaste."
                    ),
                    missing_fields=("aesthetic_callback",),
                )
            )
    return tuple(issues)


def _render_empathy_item(item: dict[str, Any], *, is_en: bool) -> str:
    if is_en:
        return (
            "- Empathy: "
            f"situation={item.get('situation', '')}; desire={item.get('current_desire', '')}; "
            f"sensory={item.get('sensory_entry', '')}; judgment={item.get('judgment_logic', '')}; "
            f"action={item.get('reasonable_action', '')}; consequence={item.get('consequence', '')}"
        )
    return (
        "- 代入链: "
        f"处境={item.get('situation', '')}; 主角当前欲望={item.get('current_desire', '')}; "
        f"感官入口={item.get('sensory_entry', '')}; 判断逻辑={item.get('judgment_logic', '')}; "
        f"合理行动={item.get('reasonable_action', '')}; 后果={item.get('consequence', '')}"
    )


def _render_bomb_item(item: dict[str, Any], *, is_en: bool) -> str:
    if is_en:
        return (
            "- Bomb: "
            f"reader knows={item.get('reader_knows', '')}; "
            f"blindspot={item.get('character_blindspot', '')}; "
            f"trigger={item.get('trigger_condition', '')}; countdown={item.get('countdown', '')}; "
            f"consequence={item.get('consequence', '')}; "
            f"payoff window={item.get('payoff_window', '')}"
        )
    return (
        "- 桌下炸弹: "
        f"读者知道={item.get('reader_knows', '')}; 主角盲区={item.get('character_blindspot', '')}; "
        f"触发条件={item.get('trigger_condition', '')}; 倒计时={item.get('countdown', '')}; "
        f"爆炸后果={item.get('consequence', '')}; 兑现窗口={item.get('payoff_window', '')}"
    )


def _render_antagonist_item(item: dict[str, Any], *, is_en: bool) -> str:
    goods = ", ".join(str(value) for value in item.get("real_good_deeds", [])[:3])
    cracks = ", ".join(str(value) for value in item.get("cracks", [])[:3])
    if is_en:
        return (
            "- Antagonist moral mask: "
            f"public mask={item.get('public_mask', '')}; real good={goods}; "
            f"hidden desire={item.get('hidden_desire', '')}; cracks={cracks}; "
            f"collapse wound={item.get('collapse_wound', '')}"
        )
    return (
        "- 反派道德面具: "
        f"表面可信={item.get('public_mask', '')}; 真实善行={goods}; "
        f"隐秘欲望={item.get('hidden_desire', '')}; 裂缝={cracks}; "
        f"崩塌伤口={item.get('collapse_wound', '')}"
    )


def _render_chain_item(item: dict[str, Any], *, is_en: bool) -> str:
    if is_en:
        return (
            "- Emotion chain: "
            f"emotion={item.get('target_reader_emotion', '')}; "
            f"waiting={item.get('reader_waiting_for', '')}; "
            f"worry={item.get('reader_worry', '')}; "
            f"payoff/aftereffect={item.get('payoff_or_aftereffect', '')}"
        )
    return (
        "- 情绪链: "
        f"目标情绪={item.get('target_reader_emotion', '')}; "
        f"读者等待={item.get('reader_waiting_for', '')}; "
        f"读者担心={item.get('reader_worry', '')}; "
        f"兑现/后效={item.get('payoff_or_aftereffect', '')}"
    )


def _empty_chapter_contract() -> dict[str, Any]:
    return {
        "reader_emotion_promise": "",
        "primary_reader_waiting": [],
        "empathy_contracts": [],
        "bomb_contracts": [],
        "antagonist_moral_contracts": [],
        "ending_texture_contract": None,
        "emotion_chain": [],
        "callback_motifs": [],
    }


def _range_contains(chapter_range: str, chapter_number: int) -> bool:
    if not chapter_range:
        return True
    numbers = [int(value) for value in re.findall(r"\d+", chapter_range)]
    if not numbers:
        return True
    if len(numbers) == 1:
        return chapter_number == numbers[0]
    start, end = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
    return start <= chapter_number <= end


def _present(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _missing_fields(model: BaseModel, fields: tuple[str, ...]) -> tuple[str, ...]:
    missing: list[str] = []
    for field_name in fields:
        if not _present(getattr(model, field_name, None)):
            missing.append(field_name)
    return tuple(missing)


__all__ = [
    "AntagonistMoralContract",
    "BombContract",
    "EmotionChainBeat",
    "EmotionContractGateReport",
    "EmotionContractIssue",
    "EmotionDrivenKernel",
    "EmpathyContract",
    "EndingTextureContract",
    "emotion_driven_kernel_from_dict",
    "emotion_driven_kernel_to_dict",
    "evaluate_emotion_contracts",
    "extract_chapter_emotion_contract",
    "render_emotion_driven_kernel_prompt_block",
]
