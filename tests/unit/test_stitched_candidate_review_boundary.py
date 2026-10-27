"""Context candidates survive review without authorizing deletion or a rewrite."""
from copy import deepcopy
from bestseller.domain.review import ChapterReviewResult,ChapterReviewScores,ChapterReviewFinding
from bestseller.services.reviews import _merge_stitched_draft_into_review


def _accepted():
    return ChapterReviewResult(verdict='accept',severity_max='info',
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields,0.9)),
        findings=[],evidence_summary={'original':'retained'})


def test_signature_candidate_is_diagnostic_not_a_prose_edit():
    review=_accepted();before=review.model_dump(mode='json')
    evidence={'stitched_draft_pairs':[{'evidence_kind':'context_candidate','hard_gate':False,
        'requires_semantic_confirmation':True,'a_idx':1,'b_idx':2,'similarity':0.89}],
        'stitched_draft_repair_prompt':''}
    result=_merge_stitched_draft_into_review(review,[],deepcopy(evidence))
    assert result.verdict=='accept' and result.severity_max=='info'
    assert result.scores==review.scores and not result.rewrite_instructions
    assert result.evidence_summary['stitched_draft_pairs']==evidence['stitched_draft_pairs']
    assert review.model_dump(mode='json')==before


def test_confirmed_verbatim_repeat_remains_a_real_rewrite_finding():
    review=_accepted();finding=ChapterReviewFinding(severity='critical',category='stitched_draft',
        message='An identical complete 200-character block occurs twice.')
    result=_merge_stitched_draft_into_review(review,[finding],{
        'stitched_draft_pairs':[{'evidence_kind':'verbatim_repeated_block','hard_gate':True}],
        'stitched_draft_repair_prompt':'Remove the accidental verbatim duplicate.'},language='en')
    assert result.verdict=='rewrite' and result.severity_max=='critical'
    assert finding in result.findings
