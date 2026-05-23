from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.domain.chapter_seam_contract import seam_contract_from_mapping
from bestseller.domain.gate_verdict import GateFinding, GateVerdict


def evaluate_chapter_seam_inheritance(
    *,
    previous_chapter_text: str,
    current_chapter_text: str,
    seam_contract: object,
) -> GateVerdict:
    seam = seam_contract_from_mapping(seam_contract)
    findings: list[GateFinding] = []
    if seam is None:
        findings.append(
            GateFinding(
                code="seam_contract_missing",
                severity="critical",
                message="chapter seam contract is missing",
                repair_action="materialize seam_contract before drafting",
            )
        )
    else:
        current = current_chapter_text
        for callback in seam.required_callbacks:
            if callback and callback not in current:
                findings.append(
                    GateFinding(
                        code="required_callback_missing",
                        severity="critical",
                        message=f"required callback not inherited: {callback}",
                        path=f"chapter:{seam.chapter_no}:required_callbacks",
                        repair_action="rewrite opening or first scene to pay the callback",
                    )
                )
        for inherited in seam.inherits_from_prev:
            if _key_phrase(inherited) not in current:
                findings.append(
                    GateFinding(
                        code="prior_state_not_inherited",
                        severity="high",
                        message=f"prior state not visible in current chapter: {inherited}",
                        path=f"chapter:{seam.chapter_no}:inherits_from_prev",
                        repair_action="surface the inherited state in the opening beat",
                    )
                )
    tail_signal = _tail_signal(previous_chapter_text)
    if tail_signal and tail_signal not in current_chapter_text[:500]:
        findings.append(
            GateFinding(
                code="previous_tail_signal_dropped",
                severity="high",
                message="current chapter opening drops the previous chapter tail signal",
                repair_action="carry the prior chapter tail image into the new opening",
            )
        )
    verdict = (
        "blocked"
        if any(f.critical for f in findings)
        else ("warn_only" if findings else "pass")
    )
    return GateVerdict(
        gate_name="chapter_seam_inheritance_gate",
        verdict=verdict,
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={"finding_count": len(findings)},
    )


def seam_contract_from_chapter_contract(payload: Mapping[str, Any]) -> object:
    return payload.get("seam_contract")


def _key_phrase(text: str) -> str:
    return str(text).split()[0][:12]


def _tail_signal(text: str) -> str:
    compact = "".join(str(text or "").split())
    return compact[-12:] if len(compact) >= 12 else compact
