"""A verified World content rejection gets one narrow revision, never approval by feedback."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_planner_services import build_project, lock_design_snapshot

from bestseller.services import planner as p
from bestseller.services.conception_snapshots import snapshot_hash

pytestmark = pytest.mark.unit


def receipt(project, book, baseline, candidate, decision="revise"):
    authority = p._source_bound_design_snapshot(project)
    sh = p._approved_payload_hash(authority)
    ch = p._approved_payload_hash(
        {"world_spec": p._without_planning_meta(candidate), "cast_spec": {}}
    )
    bh = p._approved_payload_hash(p._source_bound_review_baseline(project,
        book_spec=book,world_baseline=baseline,cast_baseline={},authority=authority))
    run = str(uuid4())
    return {
        "decision": decision,
        "reason": "reviewed",
        "evidence_verified": True,
        "draft_eligible": decision == "pass",
        "core_preserved": True,
        "core_conflicts": ["unsupported history"] if decision == "revise" else [],
        "feedback": "Remove unsupported history from location atmosphere",
        "llm_run_id": run,
        "raw_response_hash": "response-hash",
        "authority_snapshot_hash": sh,
        "authority_snapshot_hash_echo": sh,
        "candidate_hash": ch,
        "candidate_hash_echo": ch,
        "canonical_baseline_hash": bh,
        "changed_fields": ["world.locations"],
        "source_evidence": [{"field": "world.locations", "quote": "approved action"}],
        "candidate_evidence": [{"field": "world.locations", "quote": "Workshop"}],
        "review_attempts": [
            {
                "llm_run_id": run,
                "raw_response_hash": "response-hash",
                "provider": "openai",
                "fallback_used": False,
                "finish_reason": "stop",
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "rejected_again",
        "generation_fallback",
        "wrong_hash",
        "malformed_review",
        "missing_evidence",
        "review_fallback",
        "empty_revision",
        "functional_slots_only",
    ],
)
async def test_one_world_content_revision_requires_new_independent_review(monkeypatch, mode):
    project = build_project()
    lock_design_snapshot(
        project,
        protagonist_name="Mira",
        reader_promise="approved action",
        core_story_engine="approved action",
    )
    book = {"unique_hook": "approved action"}
    baseline = {"rules": [{"description": "approved rule"}], "locations": []}
    candidate = p._rebind_source_bound_world_expansion(
        baseline,
        {
            "locations": [{"name": "Workshop", "atmosphere": "unsupported history"}],
            "factions": [],
        },
    )
    original = copy.deepcopy(candidate)
    reviews, generations, runs = [], [], []

    async def review(*args, **kw):
        reviews.append(copy.deepcopy(kw["world_derived"]))
        result = receipt(
            project,
            book,
            baseline,
            kw["world_derived"],
            "pass" if len(reviews) == 2 and mode != "rejected_again" else "revise",
        )
        if len(reviews) == 1:
            if mode == "wrong_hash":
                result["candidate_hash_echo"] = "wrong"
            elif mode == "malformed_review":
                result["reason"] = "malformed_review_json"
            elif mode == "missing_evidence":
                result["evidence_verified"] = False
            elif mode == "review_fallback":
                result["review_attempts"][0]["fallback_used"] = True
        else:
            assert kw["world_baseline"] == baseline
            assert kw["world_derived"]["rules"] == baseline["rules"]
            assert kw["world_derived"]["locations"][0]["atmosphere"] == "ordinary workbench"
        return result

    async def generate(_session, _settings, request):
        generations.append(request)
        assert "Remove unsupported history" in request.user_prompt
        assert "immutable_world_baseline" in request.user_prompt
        assert request.prompt_template == "planner_source_bound_world_revision"
        assert "Retain at least one concrete derived" in request.user_prompt
        assert (
            "a planned ordinary place need not have appeared in prose already"
            in request.user_prompt
        )
        locations = [{"name": "Workshop", "atmosphere": "ordinary workbench"}]
        if mode == "empty_revision":
            locations = []
        elif mode == "functional_slots_only":
            locations = p._source_bound_locations([])
        return SimpleNamespace(
            content=json.dumps(
                {
                    "locations": locations,
                    "factions": [],
                }
            ),
            provider="openai",
            fallback_used=mode == "generation_fallback",
            finish_reason="stop",
            llm_run_id=uuid4(),
            model_name="fixture",
        )

    monkeypatch.setattr(p, "_review_source_bound_expansion", review)
    monkeypatch.setattr(p, "complete_text", generate)

    async def approve():
        return await p._approve_source_bound_world_expansion(
            None,
            SimpleNamespace(),
            project=project,
            book_spec=book,
            world_baseline=baseline,
            world_derived=candidate,
            workflow_run_id=uuid4(),
            llm_run_ids=runs,
        )

    if mode == "success":
        result = await approve()
        assert result["_meta"]["source_bound_expansion"] == "reviewed"
        trail = result["_meta"]["source_bound_expansion_review_receipt"]["world_revision"]
        assert trail["initial_review"]["decision"] == "revise"
        assert trail["final_review"]["decision"] == "pass"
        assert len(runs) == 3
    else:
        with pytest.raises(p.PlannerFallbackError):
            await approve()
    assert candidate == original
    if mode in {"wrong_hash", "malformed_review", "missing_evidence", "review_fallback"}:
        assert len(generations) == 0
        assert len(reviews) == 1
    else:
        assert len(generations) == 1
        assert len(reviews) == (
            1 if mode in {"generation_fallback", "empty_revision", "functional_slots_only"} else 2
        )
        trail = project.metadata_json["source_bound_expansion_review"]["world_revision"]
        assert trail["initial_review"]["llm_run_id"]
        assert trail["generation_receipt"]["llm_run_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "genre,language,name",
    [
        ("现实百态", "zh-CN", "修理铺"),
        ("fantasy", "en", "River Shrine"),
        ("science-fiction", "en", "Orbital Dock"),
    ],
)
async def test_planning_rules_allow_reviewed_scene_drafts_without_claiming_past_events(
    monkeypatch,
    genre,
    language,
    name,
):
    project = build_project()
    project.genre, project.language = genre, language
    book = {"unique_hook": "approved action", "protagonist": {"name": "Mira"}}
    lock_design_snapshot(project, protagonist_name="Mira", reader_promise="approved action",
        core_story_engine="approved action")
    content = {"book_spec": copy.deepcopy(book)}
    source_id, source_hash = str(uuid4()), snapshot_hash(content)
    project.metadata_json.update({"conception_approved": True,
        "creation_canonical_snapshot_id": source_id,
        "creation_canonical_snapshot_hash": source_hash})
    project._source_bound_planning_canonical = {
        "id": source_id, "content_hash": source_hash, "content": content,
    }
    baseline = p._compile_source_bound_world_spec(project, "approved action", book)
    # No author rules were supplied: the compiler must expose the gaps rather
    # than manufacture SB-R002/SB-R007 as fictional world law.
    assert baseline["rules"] == []
    assert baseline["_meta"]["planning_status"] == "projection_only"
    assert baseline["_meta"]["approved_complete"] is False
    assert "world_name" in baseline["_meta"]["unresolved_fields"]
    assert baseline["_meta"]["source_gaps"] == []
    assert project._source_bound_planning_canonical["content_hash"] == snapshot_hash(content)
    candidate = p._rebind_source_bound_world_expansion(
        baseline,
        {
            "locations": [{"name": name, "story_role": "approved action setting"}],
            "factions": [],
        },
    )
    calls = []

    async def complete(_session, _settings, request):
        calls.append(request)
        assert "SB-R002/SB-R007" in request.system_prompt
        if language == "en":
            assert "without a prior chapter confirming its name" in request.system_prompt
            assert "Backstory not established in a verified source may be proposed" in request.system_prompt
            assert "must not be falsely attributed to an author or to already adopted prose" in request.system_prompt
            assert "Reject contradiction of positively sourced approved history" in request.system_prompt
            assert "Still reject invented hidden history" not in request.system_prompt
        else:
            assert "无需先有章节证明其名称" in request.system_prompt
            assert "未知背景可以作为新的世界构建提案" in request.system_prompt
            assert "不得伪称已被作者授权或已在采用正文中发生" in request.system_prompt
            assert "应拒绝与有来源的已批准历史" in request.system_prompt
            assert "仍须拒绝捏造隐藏前史" not in request.system_prompt
        return SimpleNamespace(
            content=json.dumps(
                {
                    "decision": "pass",
                    "core_preserved": True,
                    "core_conflicts": [],
                    "authority_snapshot_hash": request.metadata["authority_snapshot_hash"],
                    "candidate_hash": request.metadata["candidate_hash"],
                    "source_evidence": [
                        {
                            "field": "world.locations",
                            "quote": "approved action",
                            "source_relation": "source action licenses draft setting",
                        }
                    ],
                    "candidate_evidence": [
                        {
                            "field": "world.locations",
                            "quote": name,
                            "source_relation": "draft scene, not a past event",
                        }
                    ],
                }
            ),
            provider="openai",
            finish_reason="stop",
            llm_run_id=uuid4(),
            fallback_used=False,
        )

    monkeypatch.setattr(p, "complete_text", complete)
    result = await p._review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=project,
        book_spec=book,
        world_baseline=baseline,
        cast_baseline={},
        world_derived=candidate,
        cast_derived={},
        workflow_run_id=uuid4(),
    )
    assert len(calls) == 1
    assert result["review_version"] == "source-bound-expansion-semantic-review.v4.2-stable-catalog"
    assert result["evidence_verified"] is True
    assert p._source_bound_report_is_draft_eligible(result)
