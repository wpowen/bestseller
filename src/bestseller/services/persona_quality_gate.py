"""Hard gate from reader-persona simulation (payoff / abandon rate)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from bestseller.domain.reader_persona import PersonaSimulationResult

PERSONA_ABANDON_RATE_HIGH: Final[str] = "PERSONA_ABANDON_RATE_HIGH"
PERSONA_WEIGHTED_SCORE_LOW: Final[str] = "PERSONA_WEIGHTED_SCORE_LOW"
PERSONA_PAYOFF_DENSITY_LOW: Final[str] = "PERSONA_PAYOFF_DENSITY_LOW"


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
    "PERSONA_PAYOFF_DENSITY_LOW",
    "PERSONA_WEIGHTED_SCORE_LOW",
    "PersonaQualityFinding",
    "PersonaQualityReport",
    "evaluate_persona_quality",
]
