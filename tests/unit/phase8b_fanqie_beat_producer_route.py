"""STAGED phase8B producer routing contract; run by explicit file path.

The future producer is not integrated in phase8A. This file deliberately has no
pytest default filename prefix; it contains no skip/xfail and preserves every
route-registration/conflict assertion. Run explicitly for phase8B integration:
pytest --no-cov tests/unit/phase8b_fanqie_beat_producer_route.py
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_legacy_request_purpose import (
    offline,  # noqa: F401
    project_stub,
    result,
    returned,
    role_settings,
    witnesses,
)

from bestseller.services import commercial_request_purpose as registered
from bestseller.services import fanqie_short_planner as producer
from bestseller.services import llm
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_new_producer_route_is_explicit_registered_and_conflicts_reject(monkeypatch):
    project, captured = project_stub(), []
    project.slug = "unrelated-new-short-story"

    async def get_project(*args, **kwargs):
        return project

    async def gateway(_session, _settings, request):
        captured.append(request)
        return llm.LLMCompletionResult(
            content=request.fallback_response,
            provider="openai",
            model_name=role_settings().model,
        )

    async def importer(*args, **kwargs):
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(producer, "get_project_by_slug", get_project)
    monkeypatch.setattr(producer, "complete_text", gateway)
    monkeypatch.setattr(producer, "import_planning_artifact", importer)
    await producer.generate_fanqie_beat_sheet(
        None,
        load_settings(),
        project.slug,
        "An observer finds a falsified tide.",
        book_spec={},
        cast_spec={},
    )
    assert len(captured) == 1
    request = captured[0]
    assert request.logical_role == "planner"
    assert isinstance(request.prompt_template, str) and request.prompt_template.strip()
    assert isinstance(request.prompt_version, str) and request.prompt_version.strip()
    assert len(request.prompt_version) <= 32
    w = witnesses(request, role_settings())
    route = {
        "logical_role": request.logical_role,
        "prompt_template": request.prompt_template,
        "prompt_version": request.prompt_version,
    }
    w["original_runs"][0].update(route)
    for metadata in (
        result(w)["metadata"],
        returned(w)["metadata"],
        w["original_runs"][0]["metadata_json"],
    ):
        metadata["paid_call_origin"].update(route)
    kwargs = {
        key: w[key]
        for key in ("operation", "actual_attempts", "original_runs", "transport_results")
    }
    before = deepcopy(kwargs)
    assert registered.prove_known_noncommercial_request(**kwargs) is True
    assert kwargs == before
    returned(w)["metadata"]["paid_call_origin"]["prompt_template"] = (
        "chapter_commercial_quality_judge"
    )
    assert registered.prove_known_noncommercial_request(**kwargs) is False
    w = deepcopy(before)
    for meta in (
        w["operation"]["result_json"]["metadata"],
        w["transport_results"][0]["result_json"]["result"]["metadata"],
        w["original_runs"][0]["metadata_json"],
    ):
        meta["paid_call_origin"]["prompt_template"] = request.prompt_template + "_unregistered"
    w["original_runs"][0]["prompt_template"] = request.prompt_template + "_unregistered"
    assert registered.prove_known_noncommercial_request(**w) is False
