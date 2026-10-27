"""Body-bound evidence for a rewrite stage; no provider or transaction ownership."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.rewrite_patch import text_hash


def blocking_deterministic_violations(report, *, band, word_count, chapter_number, config, translate):
    """Resolve craft actions without demoting factual or body-integrity failures."""
    from bestseller.services.deterministic_audit_actions import effective_deterministic_audit_blockers
    findings = translate(report, word_count=word_count, band=band)
    return effective_deterministic_audit_blockers(
        {'findings': findings}, settings=config, chapter_number=chapter_number,
    )


@dataclass(frozen=True)
class CandidateEvidence:
    content_md: str
    stage: str
    word_count: int
    gate_receipt: Any
    outcome: str | None
    duplicates: tuple
    violations: tuple
    audit_report: Any
    audit_band: Any
    audit_blockers: tuple
    retrofit: tuple = ()

    def receipt(self):
        return {
            'stage': self.stage, 'body_sha256': text_hash(self.content_md),
            'content_md': self.content_md, 'word_count': self.word_count,
            'gate_evaluation_id': self.gate_receipt.evaluation_id,
            'gate_report_id': str(self.gate_receipt.report_id) if self.gate_receipt.report_id else None,
            'gate_outcome': self.outcome,
            'duplicate_findings': [dict(vars(item)) for item in self.duplicates],
            'quality_gate_violations': deepcopy(list(self.violations)),
            'deterministic_audit': self.audit_report.to_dict(),
            'deterministic_blockers': deepcopy(list(self.audit_blockers)),
            'quality_retrofit_findings': deepcopy(list(self.retrofit)),
        }


async def evaluate_rewrite_candidate_evidence(content_md, stage, *, evaluate_gate,
        collect_duplicates, audit_candidate, classify_audit, count_words, evaluate_retrofit=None):
    """Build a complete replacement before the caller activates any of it.

    Exceptions propagate: a caller may retain its previous complete candidate,
    but must never bind partially evaluated new evidence to the old body.
    """
    report, band = audit_candidate(content_md)
    if report is None or not isinstance(getattr(report, 'passed', None), bool):
        raise ValueError('chapter_rewrite_candidate_audit_unavailable')
    if not report.passed and not getattr(report, 'findings', ()):
        raise ValueError('chapter_rewrite_candidate_audit_unexplained')
    word_count = count_words(content_md)
    blockers = classify_audit(report, band, word_count)
    duplicates = tuple(await collect_duplicates(content_md))
    gate = await evaluate_gate(content_md)
    if gate.graded != graded_text_fingerprint(content_md):
        raise ValueError('chapter_rewrite_candidate_gate_body_mismatch')
    outcome = gate.outcome
    violations = list(gate.violations) if outcome == 'blocked' or duplicates or blockers else []
    existing = {item.get('code') for item in violations}
    violations.extend(item for item in blockers if item.get('code') not in existing)
    if duplicates or blockers:
        outcome = 'blocked'
    retrofit = tuple(evaluate_retrofit(content_md)) if evaluate_retrofit and outcome != 'blocked' else ()
    return CandidateEvidence(content_md, stage, word_count, gate, outcome,
        duplicates, tuple(deepcopy(violations)), report, band, blockers, retrofit)
