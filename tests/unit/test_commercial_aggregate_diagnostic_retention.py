"""A failed aggregate binding must retain individual infrastructure diagnostics."""
from uuid import uuid4
import pytest
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services.commercial_source_evidence import commercial_judge_unavailable
from bestseller.settings import load_settings


@pytest.mark.asyncio
async def test_unbound_aggregate_retains_unavailable_sample_diagnostics(monkeypatch):
    diagnostic = '{"sample_error":"source receipt missing before dispatch"}'
    sample = commercial_judge_unavailable(diagnostic='sample unavailable', raw=diagnostic)

    async def unavailable_sample(*args, **kwargs):
        return sample

    async def unbound(*args, **kwargs):
        raise ValueError('commercial_cohort_aggregate_unbound')

    monkeypatch.setattr(judge, 'judge_chapter_commercial_quality', unavailable_sample)
    monkeypatch.setattr(recovery, 'bind_commercial_component_aggregate', unbound)
    result = await judge.judge_chapter_commercial_quality_stable(
        object(), load_settings(env={}), chapter_number=3, content_md='A sealed observatory.',
        samples=1, chapter_id=uuid4(), draft_id=uuid4())
    assert result.infrastructure_failure and not result.passed and result.overall_score == 0
    assert result.raw_excerpt == diagnostic
    assert 'aggregate_unbound' in result.blocking_issues[0].evidence
