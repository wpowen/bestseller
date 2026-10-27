"""Unavailable or invalid judges must not certify a repair as verified."""
from types import SimpleNamespace

import pytest

from bestseller.services import blurb_coherence_judge as judge
from bestseller.services import llm

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("content,fallback,verified", [
    ('{"contradictions": []}', True, False),
    ('not json', False, False),
    ('{}', False, False),
    ('{"contradictions": "none"}', False, False),
    ('{"contradictions": []}', False, True),
    ('```json\n{"contradictions": []}\n```', False, True),
])
async def test_judge_requires_real_structured_response(monkeypatch, content, fallback, verified):
    async def complete(*args):
        return SimpleNamespace(content=content, fallback_used=fallback)

    monkeypatch.setattr(llm, "complete_text", complete)
    report = await judge.verify_blurb_coherence(
        None, None, premise="A traveler arrives.", synopsis="She opens a shop.",
        advisory_axes=False,
    )
    assert report.llm_used is verified
