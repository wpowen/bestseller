"""Hard gate from reader-persona simulation (payoff / abandon rate)."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from bestseller.domain.reader_persona import PersonaSimulationResult

PERSONA_ABANDON_RATE_HIGH: Final[str] = "PERSONA_ABANDON_RATE_HIGH"
PERSONA_WEIGHTED_SCORE_LOW: Final[str] = "PERSONA_WEIGHTED_SCORE_LOW"
PERSONA_PAYOFF_DENSITY_LOW: Final[str] = "PERSONA_PAYOFF_DENSITY_LOW"


#: 逐项互证关系登记：``persona 失败码 -> 能证明**同一具体问题**的独立修复码集合``。
#:
#: 2026-09-11（W5 审查 P1）：先前 ``pipelines`` 的互证判定是
#: ``bool(base_repair_codes)`` —— **任意一个**独立失败都会让**全部** persona 失败
#: 升级为 ``critical`` 并追加**全部** persona 修复码。"开头钩子缺失"既不能自行证明
#: "爽点密度不足"，更不能替一整套模拟偏好背书。把**共现**命名为**互证**是错的。
#:
#: 因此这里必须是**逐项**关系，而不是"有没有独立失败"。登记一条关系意味着：
#: 该独立失败码**本身就是**这同一个具体问题的可定位证据。没有登记 = 没有证据
#: 证明是同一个问题 = 该 persona 项保持 advisory、不追加修复义务。
#:
#: **当前为空是有意为之**：这三个 persona 码都是聚合代理量
#: （``abandon_rate`` / ``weighted_score`` / 平均 ``payoff_density``，底层是
#: hook/payoff/emotion 计数过 sigmoid）。目前没有任何一条"某个具体真实失败
#: == 某个具体 persona 失败"的关系通过了证据验证，因此**不敢替它们登记**。
#: 由此推出的显式策略：**默认配置下 persona 失败只留痕、不取得改写权**。
#: 新增关系必须在注释里写明是哪一条可定位的独立证据证明了同一个具体问题，
#: 不得退回到"只要有失败就算互证"。
PERSONA_CORROBORATION_LINKS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        # PERSONA_PAYOFF_DENSITY_LOW: frozenset({...}),  # 待证据，勿臆造
    }
)


def corroborating_base_codes(persona_code: str, base_repair_codes) -> frozenset[str]:
    """Return the declared independent codes that evidence ``persona_code``.

    Pure helper shared by the merge function and its callers so the receipt can
    record **which** independent failure corroborated **which** persona item,
    rather than dumping the whole base-code list.
    """

    declared = PERSONA_CORROBORATION_LINKS.get(persona_code, frozenset())
    present = set(base_repair_codes or ())
    return frozenset(declared & present)


@dataclass(frozen=True)
class PersonaQualityFinding:
    severity: str
    code: str
    detail: str


@dataclass(frozen=True)
class PersonaQualityReport:
    chapter_position: int
    findings: tuple[PersonaQualityFinding, ...]
    auto_repair_codes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.auto_repair_codes


def _avg_channel(result: PersonaSimulationResult, key: str) -> float:
    scores = [
        float(p.channel_scores.get(key, 0.0))
        for p in result.per_persona
        if p.channel_scores
    ]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def evaluate_persona_quality(
    result: PersonaSimulationResult,
    *,
    min_weighted_score: float = 0.62,
    max_abandon_rate: float = 0.35,
    min_payoff_density: float = 0.22,
    block_on_payoff: bool = True,
) -> PersonaQualityReport:
    """Turn persona simulation into blocking findings for the pipeline."""

    findings: list[PersonaQualityFinding] = []
    auto_repair: list[str] = []

    if result.abandon_rate > max_abandon_rate:
        findings.append(
            PersonaQualityFinding(
                severity="critical",
                code=PERSONA_ABANDON_RATE_HIGH,
                detail=(
                    f"abandon_rate={result.abandon_rate:.2f} > {max_abandon_rate}"
                ),
            )
        )
        auto_repair.append(PERSONA_ABANDON_RATE_HIGH)

    if result.weighted_score < min_weighted_score:
        findings.append(
            PersonaQualityFinding(
                severity="critical",
                code=PERSONA_WEIGHTED_SCORE_LOW,
                detail=(
                    f"weighted_score={result.weighted_score:.2f} < {min_weighted_score}"
                ),
            )
        )
        auto_repair.append(PERSONA_WEIGHTED_SCORE_LOW)

    payoff_avg = _avg_channel(result, "payoff_density")
    if block_on_payoff and payoff_avg < min_payoff_density:
        findings.append(
            PersonaQualityFinding(
                severity="critical",
                code=PERSONA_PAYOFF_DENSITY_LOW,
                detail=(
                    f"avg payoff_density={payoff_avg:.2f} < {min_payoff_density}"
                ),
            )
        )
        auto_repair.append(PERSONA_PAYOFF_DENSITY_LOW)

    return PersonaQualityReport(
        chapter_position=result.chapter_position,
        findings=tuple(findings),
        auto_repair_codes=tuple(dict.fromkeys(auto_repair)),
    )


__all__ = [
    "PERSONA_ABANDON_RATE_HIGH",
    "PERSONA_CORROBORATION_LINKS",
    "PERSONA_PAYOFF_DENSITY_LOW",
    "PERSONA_WEIGHTED_SCORE_LOW",
    "PersonaQualityFinding",
    "PersonaQualityReport",
    "corroborating_base_codes",
    "evaluate_persona_quality",
]
