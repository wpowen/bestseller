"""Capture the real semantic-review request without a DB or provider.

Inert completions test transport/source guards; their decisions are fixture
proposals, never production grades or evidence of literary correctness.
"""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services import source_bound_planning_content as content_service
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.creation_contract_authority import select_creation_contracts

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def setup_review(*, language="zh-CN", explicit_cost=False):
    creation = {
        "pov": "third", "creative_direction": "保留合作探索的多种可能",
        "story_enhancers": {"cost_style": "minimal", "effect_skills": []},
        "field_sources": {"pov": "explicit", "creative_direction": "explicit",
                          "story_enhancers": "explicit" if explicit_cost else "default"},
        "hook_spec": {"_story_enhancer_input_sources": {
            "cost_style": "explicit" if explicit_cost else "default",
            "effect_skills": "explicit" if explicit_cost else "default",
        }},
    }
    canonical = {
        "synopsis": "FULL_CANONICAL_OUTSIDE_COMPACT_SNAPSHOT:潮汐站允许光学和声学并行探索。",
        "world_spec": {"history_key_events": [{"name": "建站", "description": "两地共同建站"}],
                       "mechanisms": ["光学观测", "声学测量"]},
        "creation_intent": {"cost_style": "minimal", "field_source": "default"},
    }
    project = ProjectModel(
        id=uuid4(), slug=uuid4().hex, title="潮汐观测记", genre="science-fiction",
        language=language, target_word_count=78000, target_chapters=30,
        metadata_json={
            "creation_intent_contract": creation,
            "story_enhancers": {"cost_style": "external", "effect_skills": ["comedy_engine"]},
            "book_design_snapshot_status": "locked",
            "book_design_snapshot": {
                "snapshot_id": "compact-view", "source_hash": "a" * 64,
                "protagonist": {"name": "温遥"},
                "approved_story_design": {"hook_card": {"one_liner": "共同观察潮汐信号"}},
            },
        },
    )
    project._source_bound_planning_canonical = {
        "id": str(uuid4()), "content_hash": snapshot_hash(canonical), "content": canonical,
    }
    return SimpleNamespace(
        project=project, book={"logline": "共同观察潮汐信号"},
        world_base={"world_premise": "共同观察潮汐信号", "locations": []},
        cast_base={"protagonist": {"name": "温遥"}, "supporting_cast": []},
        world={"world_premise": "共同观察潮汐信号",
               "locations": [{"name": "潮汐台", "function": "规划中的联合测量地点"}]},
        cast={"protagonist": {"name": "温遥"},
              "supporting_cast": [{"name": "江澄", "role": "规划中的观测伙伴"}]},
        workflow_id=uuid4(), requests=[], capacity_checks=[],
    )


def prompt_value(request, name):
    # raw_decode accepts one complete JSON value, independent of subsequent
    # named fields or appended retry instructions.
    text = request.user_prompt.split("\n" + name + "=", 1)[1]
    return json.JSONDecoder().raw_decode(text)[0]


def review_payload(request):
    fields = prompt_value(request, "changed_fields")
    source = prompt_value(request, "source_quote_options")[0]
    options = prompt_value(request, "candidate_quote_options")
    return {
        "decision": "pass", "core_preserved": True, "core_conflicts": [],
        "quality_findings": [], "feedback": "Fixture evidence only",
        "authority_snapshot_hash": request.metadata["authority_snapshot_hash"],
        "candidate_hash": request.metadata["candidate_hash"],
        "source_evidence": [{"field": f, "quote_id": source["quote_id"],
                             "source_relation": "derive present planning from approved signal"}
                            for f in fields],
        "candidate_evidence": [{"field": f,
                                "quote_id": next(o["quote_id"] for o in options if o["field"] == f),
                                "source_relation": "new proposal, not occurred history"}
                               for f in fields],
    }


def install_transport(monkeypatch, rig, *, mutate=None, provider="minimax", length_once=False):
    original_capacity = content_service.assert_source_bound_planning_prompt_capacity

    def capacity(system, user):
        result = original_capacity(system, user)
        rig.capacity_checks.append((system, user))
        return result

    monkeypatch.setattr(content_service, "assert_source_bound_planning_prompt_capacity", capacity)

    async def complete(session, settings, request):
        assert session is None
        rig.requests.append(request)
        if request.prompt_template != "planner_source_bound_expansion_semantic_review":
            # An invalid evidence tuple may trigger the existing bounded repair.
            # This fixture refuses it rather than manufacturing repaired proof.
            return SimpleNamespace(content="{}", provider="fallback", finish_reason="stop",
                                   llm_run_id=uuid4(), fallback_used=True)
        assert rig.capacity_checks[-1] == (request.system_prompt, request.user_prompt)
        payload = review_payload(request)
        if mutate:
            mutate(payload)
        return SimpleNamespace(
            content=json.dumps(payload, ensure_ascii=False), provider=provider,
            finish_reason="length" if length_once and len(rig.requests) == 1 else "stop",
            llm_run_id=uuid4(), fallback_used=False,
        )

    monkeypatch.setattr(planner, "complete_text", complete)


async def invoke(rig):
    return await planner._review_source_bound_expansion(
        None, SimpleNamespace(llm=SimpleNamespace(mock=False)), project=rig.project,
        book_spec=rig.book, world_baseline=rig.world_base, cast_baseline=rig.cast_base,
        world_derived=rig.world, cast_derived=rig.cast, workflow_run_id=rig.workflow_id,
    )


@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("explicit_cost", [False, True])
async def test_actual_review_carries_full_canon_and_only_explicit_controls(
    monkeypatch, language, explicit_cost,
):
    rig = setup_review(language=language, explicit_cost=explicit_cost)
    before = deepcopy((rig.project.metadata_json, rig.world, rig.cast,
                       rig.project._source_bound_planning_canonical))
    install_transport(monkeypatch, rig)
    report = await invoke(rig)
    assert len(rig.requests) == len(rig.capacity_checks) == 1
    request = rig.requests[0]
    baseline = prompt_value(request, "authority_and_baseline")
    assert baseline["canonical_source"] == rig.project._source_bound_planning_canonical
    expected_controls = select_creation_contracts(rig.project.metadata_json, source_bound=True)
    assert baseline["explicit_author_controls"] == expected_controls
    assert prompt_value(request, "explicit_author_controls") == expected_controls
    assert expected_controls["creation_intent_contract"]["pov"] == "third"
    if explicit_cost:
        assert expected_controls["story_enhancers"]["cost_style"] == "minimal"
        assert expected_controls["story_enhancers"]["effect_skills"] == []
    else:
        assert "story_enhancers" not in expected_controls
        assert "cost_style" not in expected_controls["creation_intent_contract"]
    assert "\ncost_style=" not in request.user_prompt
    assert "Source silence does not forbid additional abilities" in request.user_prompt
    assert ("not be falsely attributed" in request.system_prompt
            or "不得伪称" in request.system_prompt)
    assert "second ability" not in request.system_prompt + request.user_prompt
    assert request.prompt_version == "4.2-stable-catalog"
    assert request.project_id == rig.project.id and request.workflow_run_id == rig.workflow_id
    assert report["evidence_verified"] is True and report["draft_eligible"] is True
    assert (rig.project.metadata_json, rig.world, rig.cast,
            rig.project._source_bound_planning_canonical) == before


@pytest.mark.parametrize("fault", ["source_hash", "candidate_hash", "source_quote", "wrong_field"])
async def test_new_authority_prompt_does_not_weaken_hash_or_exact_quote_guards(monkeypatch, fault):
    rig = setup_review()

    def mutate(payload):
        if fault == "source_hash":
            payload["authority_snapshot_hash"] = "f" * 64
        elif fault == "candidate_hash":
            payload["candidate_hash"] = "f" * 64
        elif fault == "source_quote":
            payload["source_evidence"][0]["quote_id"] = "s_missing"
        else:
            payload["candidate_evidence"][0]["quote_id"] = (
                payload["candidate_evidence"][1]["quote_id"]
            )

    install_transport(monkeypatch, rig, mutate=mutate)
    report = await invoke(rig)
    assert not report["draft_eligible"] and report["decision"] != "pass"
    if fault.endswith("hash"):
        assert report["reason"] == "review_hash_mismatch"
        assert len(rig.requests) == 1
    else:
        assert report["reason"] == "evidence_not_verifiable"


async def test_full_canonical_capacity_is_checked_before_any_completion(monkeypatch):
    rig = setup_review()
    rig.project._source_bound_planning_canonical["content"]["full_appendix"] = "完整历史" * 40000
    binding = rig.project._source_bound_planning_canonical
    binding["content_hash"] = snapshot_hash(binding["content"])
    install_transport(monkeypatch, rig)
    report = await invoke(rig)
    assert rig.requests == []
    assert report["reason"] == "review_call_unavailable" and not report["draft_eligible"]
    assert "SourceBoundPlanningCapacityError" in report["feedback"]


async def test_bounded_length_retry_rechecks_exact_rendered_source_and_candidate(monkeypatch):
    rig = setup_review()
    install_transport(monkeypatch, rig, length_once=True)
    report = await invoke(rig)
    assert len(rig.requests) == len(rig.capacity_checks) == 2
    first, second = rig.requests
    assert prompt_value(first, "authority_and_baseline") == prompt_value(
        second, "authority_and_baseline"
    )
    assert prompt_value(first, "derived_candidates") == prompt_value(second, "derived_candidates")
    assert first.metadata["candidate_hash"] == second.metadata["candidate_hash"]
    assert report["evidence_verified"] and report["draft_eligible"]


async def test_fallback_review_still_cannot_approve_new_proposals(monkeypatch):
    rig = setup_review()
    install_transport(monkeypatch, rig, provider="fallback")
    report = await invoke(rig)
    assert report["reason"] == "provider_receipt_rejected"
    assert not report["draft_eligible"]
