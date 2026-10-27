from types import SimpleNamespace

import pytest

from bestseller.services.pipelines import _scorecard_score_for_premium_gate


@pytest.mark.parametrize(
    'missing,unknown,expected',
    [(2, (), (None, 'project_in_progress_missing_chapters')),
     (0, (1, 3), (None, 'current_draft_quality_reports_unverified')),
     (0, (), (92.0, None))],
)
def test_premium_score_requires_complete_current_draft_evidence(missing, unknown, expected):
    card = SimpleNamespace(
        missing_chapters=missing,
        quality_report_unknown_chapters=unknown,
        quality_score=92.0,
    )
    assert _scorecard_score_for_premium_gate(card) == expected
