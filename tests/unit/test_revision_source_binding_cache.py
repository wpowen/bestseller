"""The same story is not the same judge request when policy or assets change."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from bestseller.services import outline_llm_judge as judge
from bestseller.services.judge_rubrics import get_judge_rubric
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rubric", "schema", "model", "asset", "genre"])
async def test_effective_judge_policy_invalidates_cache(monkeypatch, change):
    project = SimpleNamespace(metadata_json={})

    class Session:
        async def scalar(self, stmt):
            return project

    session = Session()
    settings = load_settings(env={})
    rubric = get_judge_rubric("commercial_planning")
    policy = {"rubric": rubric, "asset": "asset version 1", "genre": "context version 1"}
    monkeypatch.setattr(judge, "get_judge_rubric", lambda name: policy["rubric"])
    monkeypatch.setattr(judge, "_render_judge_methodology_reference", lambda pack: policy["asset"])
    original_render = judge._render_planning_readiness_system_prompt
    monkeypatch.setattr(
        judge,
        "_render_planning_readiness_system_prompt",
        lambda **kw: original_render(**kw) + policy["genre"],
    )
    requests = []

    async def completion(session, settings, request):
        requests.append(request)
        return SimpleNamespace(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.89,
                    "dimension_scores": {},
                    "blocking_issues": [],
                    "audit_issues": [],
                    "rewrite_plan": {},
                }
            ),
            llm_run_id=None,
            provider="unit-test",
            model_name="unit-test",
        )

    monkeypatch.setattr(judge, "complete_text", completion)
    kwargs = dict(
        chapters_payload=[{"chapter_number": 1, "chapter_goal": "Choose a route"}],
        project_brief={"slug": "revision-test", "genre": "custom"},
        sample_slot=0,
    )
    await judge.judge_commercial_planning_readiness(session, settings, **kwargs)
    await judge.judge_commercial_planning_readiness(session, settings, **kwargs)
    assert len(requests) == 1
    if change == "rubric":
        policy["rubric"] = replace(
            rubric, system_prompt=rubric.system_prompt + "\nRevised criterion."
        )
    elif change == "schema":
        policy["rubric"] = replace(rubric, output_schema={"revision": 2})
    elif change == "model":
        settings.llm.critic.model += "-changed"
    elif change == "asset":
        policy["asset"] = "asset version 2"
    else:
        policy["genre"] = "context version 2"
    await judge.judge_commercial_planning_readiness(session, settings, **kwargs)
    assert len(requests) == 2
    history = project.metadata_json["llm_judge_verdict_cache_superseded"]
    assert len(history) == 1
    historical_hash = next(iter(history))
    await judge.judge_commercial_planning_readiness(session, settings, **kwargs)
    assert len(requests) == 2
    assert historical_hash in project.metadata_json["llm_judge_verdict_cache_superseded"]
    assert len(project.metadata_json["llm_judge_verdict_cache_superseded"]) == 1
    await judge.judge_commercial_planning_readiness(
        session, settings, **{**kwargs, "sample_slot": 1}
    )
    assert len(requests) == 3  # Independent samples remain independent.


@pytest.mark.asyncio
async def test_unavailable_judge_does_not_invent_passing_percentage_or_repairs(monkeypatch):
    async def fallback(session, settings, request):
        return SimpleNamespace(
            content=request.fallback_response,
            llm_run_id=None,
            provider="fallback",
            fallback_used=True,
        )

    monkeypatch.setattr(judge, "complete_text", fallback)
    result = await judge.judge_commercial_planning_readiness(
        None, load_settings(env={}), chapters_payload=[], project_brief={}
    )
    assert result.passed is False
    assert result.infrastructure_failure is True
    assert not result.quality_rejected
    assert judge.build_outline_repair_directives(result) == []
