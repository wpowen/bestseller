"""Absent source context retains the exact legacy 3.4 request contract."""

from copy import deepcopy
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_none_source_context_keeps_default_legacy_3_4_request_byte_exact(monkeypatch):
    sent = []

    async def transport(session, settings, request):
        sent.append(request)
        return LLMCompletionResult(
            content='{"patches":[]}',
            model_name="inert-transport",
            provider="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(planner, "complete_text", transport)
    project = ProjectModel(
        id=uuid4(),
        slug="unrelated-telescope",
        title="Tidal observations",
        genre="science-fiction",
        language="en",
        target_chapters=1,
        target_word_count=2000,
        metadata_json={},
    )
    candidate = {"chapters": [{"chapter_number": 1, "goal": "Observe the exact source"}]}
    before = deepcopy(candidate)
    kwargs = {
        "project": project,
        "workflow_run_id": uuid4(),
        "logical_name": "source_revision",
        "outline_payload": candidate,
        "directives": ["Retain the current exact fact."],
    }
    await planner._repair_outline_commercial_findings_surgically(None, load_settings(), **kwargs)
    await planner._repair_outline_commercial_findings_surgically(
        None, load_settings(), **kwargs, source_context=None
    )
    assert len(sent) == 2
    assert sent[0].model_dump(mode="json") == sent[1].model_dump(mode="json")
    assert sent[0].system_prompt == sent[1].system_prompt
    assert sent[0].user_prompt == sent[1].user_prompt
    assert candidate == before
    assert sent[0].prompt_version == sent[1].prompt_version == "3.4-window-complete-repair"
    assert "source_complete_retention" not in sent[0].metadata
