"""Complete source projections, with inert judge boundaries and real codecs."""

# ruff: noqa: RUF001 -- Unrelated Chinese source fixtures.

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest

from bestseller.domain.book_design_snapshot import build_book_design_snapshot
from bestseller.infra.db.models import ProjectModel
from bestseller.services import outline_llm_judge as judge
from bestseller.services import planner
from bestseller.services.book_design import ensure_project_book_design_snapshot
from bestseller.services.llm import LLMCompletionResult, _effective_input_hash
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.source_bound_planning_content import (
    MAX_PROMPT_CHARS,
    SourceBoundPlanningCapacityError,
    assert_source_bound_planning_prompt_capacity,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _case(language="en", repeated=1):
    name = "林萤" if language == "zh-CN" else "Inez"
    prerequisite = (
        "她曾在潮汐站学习解读光谱底片，须先校准再读取气压。"
        if language == "zh-CN"
        else (
            "She learned spectral-plate reading at the tide station; "
            "calibration precedes pressure reading."
        )
    )
    project = ProjectModel(
        id=uuid4(),
        slug=uuid4().hex,
        title="潮汐光谱" if language == "zh-CN" else "Tidal spectra",
        genre="science-fiction",
        sub_genre="",
        language=language,
        target_word_count=78000,
        target_chapters=30,
        reader_contract_json={
            "preferences": "Retain the quiet investigation. " * 260,
            "late_requirement": "Keep every uncertain measurement visible.",
        },
        hype_scheme_json={"notes": ["An editorial preference, not a new ability."]},
        metadata_json={
            "premise": name + " calibrates a tide-station plate.",
            "creation_protagonist_name": name,
            "creation_protagonist_source": "user",
        },
    )
    initial = ensure_project_book_design_snapshot(project)
    snapshot = build_book_design_snapshot(
        creation_intent=initial.creation_intent,
        protagonist=initial.protagonist,
        tone=initial.tone,
        target_words=78000,
        chapter_count=30,
        approved_story_design={
            "hook_card": {"abnormality": prerequisite},
            "unknown_late_prerequisite": {
                "full": prerequisite * repeated,
                "literal_ref": {"$ref": "/authored/value"},
            },
        },
    ).model_dump(mode="json")
    snapshot["unknown_root"] = {"types": [False, 1, 1.0, None], "late": prerequisite}
    project.metadata_json = {
        **project.metadata_json,
        "book_design_snapshot": snapshot,
        "book_design_snapshot_id": snapshot["snapshot_id"],
        "book_design_snapshot_hash": snapshot["source_hash"],
        "book_design_snapshot_status": "locked",
        "concept_contract": {"hook_card": {"abnormality": "STALE_CONCEPT_MUST_NOT_RETURN"}},
        "story_effect_skill_selection": {"chapter_outline": {"primary": "UNSELECTED_CACHE"}},
        "creation_intent_contract": {
            "field_sources": {"creative_direction": "explicit"},
            "creative_direction": "Use the complete late author clause. " * 220
            + "LAST_AUTHOR_CLAUSE",
        },
    }
    world = {
        "world_name": "A tidal observatory",
        "world_premise": "Pressure needs calibration.",
        "power_system": {
            "name": "Plate reading",
            "acquisition_method": prerequisite,
            "hard_limits": "No reading before calibration.",
            "protagonist_starting_tier": "Apprentice",
            "tiers": ["Apprentice", "Navigator"],
            "tier_progression": [{"tier": "Navigator", "requires": "A future verified exam"}],
        },
        "rules": [{"rule_id": "CALIBRATION", "description": "Calibration precedes every reading."}],
        "history_key_events": [
            {"event": "The station taught plate reading.", "relevance": prerequisite}
        ],
        "late_unknown": {"full": prerequisite * repeated, "nested_meta": {"must_keep": True}},
        "_meta": {"claim": "PRODUCER_ONLY_DO_NOT_PROMOTE", "unknown_producer_field": [1, 2]},
    }
    return project, world


def _brief(project, world):
    return planner._outline_judge_project_brief(
        project, metadata=project.metadata_json, semantic_candidates=[], world_spec=world
    )


def _section(text, name):
    return text.split("[" + name + "]\n", 1)[1].split("\n[/" + name + "]", 1)[0]


@pytest.mark.parametrize("language", ["zh-CN", "en"])
def test_complete_sources_editorial_clauses_unknowns_and_hashes_survive_summary_limit(language):
    project, world = _case(language)
    before = deepcopy((project.metadata_json, project.reader_contract_json, world))
    brief = _brief(project, world)
    brief["optional"] = "Discardable summary. " * 1000
    brief["editorial_requirements"] = {"full": "Retain this preference. " * 400 + "LAST_EDITORIAL"}
    rendered = judge.render_outline_source_brief(brief, max_summary_chars=20)
    authority = json.loads(_section(rendered, "SOURCE AUTHORITY"))
    assert authority["book_design_snapshot"] == before[0]["book_design_snapshot"]
    assert (
        authority["approved_creation_contracts"]["creation_intent_contract"]["creative_direction"]
        == before[0]["creation_intent_contract"]["creative_direction"]
    )
    current = authority["approved_world_authority"]
    expected = deepcopy(world)
    del expected["_meta"]
    assert current["full_story_source"] == expected
    assert current["source_proof"]["source_payload_sha256"] == planner._approved_payload_hash(world)
    assert current["source_proof"]["story_payload_sha256"] == planner._approved_payload_hash(
        expected
    )
    assert current["source_proof"]["excluded_root_paths"] == ["/_meta"]
    assert "tiers" not in current["power_system"]
    assert current["progression_reference_not_current_authorization"]["tiers"] == [
        "Apprentice",
        "Navigator",
    ]
    assert "not a claim about all later chapters" in current["capability_boundary"]
    editorial = json.loads(_section(rendered, "EDITORIAL CONTEXT"))
    assert editorial["source_role"] == "advisory_editorial_context_not_canonical_author"
    assert editorial["supplied_requirements"]["reader_contract"] == before[1]
    assert (
        editorial["supplied_requirements"]["editorial_requirements"]
        == brief["editorial_requirements"]
    )
    assert "OPTIONAL_SUMMARY_TRUNCATED" in rendered
    assert all(
        s not in rendered
        for s in [
            "STALE_CONCEPT_MUST_NOT_RETURN",
            "UNSELECTED_CACHE",
            "PRODUCER_ONLY_DO_NOT_PROMOTE",
        ]
    )
    brief["book_design_snapshot"]["unknown_root"]["types"].append("detached")
    current["full_story_source"]["late_unknown"]["nested_meta"]["must_keep"] = False
    assert (project.metadata_json, project.reader_contract_json, world) == before


@pytest.mark.parametrize("versioned", [False, True])
def test_metadata_world_never_becomes_current_and_unlocked_book_keeps_legacy_view(versioned):
    project, world = _case()
    project.metadata_json["book_design_snapshot_status"] = "draft"
    if versioned:
        world["_meta"].update(artifact_id=str(uuid4()), version_no=9, authority_status="current")
    project.metadata_json["world_spec"] = world
    brief = _brief(project, None)
    assert set(brief["book_design_snapshot"]) == {"protagonist", "identity"}
    assert "book_design_source_proof" not in brief
    assert brief["approved_world_authority"]["authority_status"] == "unverified"
    rendered = judge.render_outline_source_brief(brief)
    authority = json.loads(_section(rendered, "SOURCE AUTHORITY"))
    assert "approved_world_authority" not in authority
    assert "legacy_world_reference" in rendered


def test_explicit_empty_world_does_not_recover_stale_metadata():
    project, world = _case()
    project.metadata_json["world_spec"] = world
    assert _brief(project, {})["approved_world_authority"] == {}


async def _capture_judges(monkeypatch, project, world):
    requests = []

    async def completion(_session, settings, request):
        # Exercise the real final strings. This is an inert boundary assertion,
        # not a claim that the production caller already checks final capacity.
        assert_source_bound_planning_prompt_capacity(request.system_prompt, request.user_prompt)
        requests.append(request)
        if len(requests) == 1:
            result = {
                "pass": False,
                "overall_score": 0.4,
                "dimension_scores": {},
                "blocking_issues": [
                    {
                        "code": "FIXTURE_ISSUE",
                        "severity": "critical",
                        "evidence": "Fixture only",
                        "required_fix": "Verify source",
                    }
                ],
            }
        else:
            result = {
                "adjudication_complete": True,
                "decisions": [
                    {
                        "index": 1,
                        "decision": "CONFIRM",
                        "reason": "Fixture only",
                        "required_fix": "Verify source",
                    }
                ],
            }
        return LLMCompletionResult(
            content=json.dumps(result), provider="mock", model_name="fixture", llm_run_id=uuid4()
        )

    monkeypatch.setattr(judge, "complete_text", completion)
    await judge.judge_outline_commercial_readiness(
        None,
        load_settings(),
        outline_payload={"chapters": [{"chapter_number": 1, "title": "A plate"}]},
        project_brief=_brief(project, world),
        workflow_run_id=uuid4(),
        project_id=project.id,
    )
    return requests


@pytest.mark.parametrize("language", ["zh-CN", "en"])
async def test_initial_and_consistency_requests_share_full_source_bytes_hash_and_scope(
    monkeypatch, language
):
    project, world = _case(language)
    before = deepcopy((project.metadata_json, world))
    requests = await _capture_judges(monkeypatch, project, world)
    assert len(requests) == 2
    assert requests[0].project_id == requests[1].project_id == project.id
    assert requests[0].workflow_run_id == requests[1].workflow_run_id
    shared = []
    for request in requests:
        data = json.loads(_section(request.user_prompt, "COMPLETE REVIEW DATA"))
        shared.append(data['project_brief'])
        assert request.prompt_version == 'v3-lossless-source-authority'
        assert data['source_roles']['canonical_author'] == '/project_brief/approved_creation_contracts'
        assert '/project_brief/reader_contract' in data['source_roles']['advisory_editorial_not_canon']
    assert shared[0] == shared[1]
    assert requests[0].metadata['review_context_sha256'] == requests[1].metadata['review_context_sha256']
    actual = shared[0]
    assert actual['book_design_snapshot'] == project.metadata_json['book_design_snapshot']
    assert actual['approved_world_authority']['full_story_source']['history_key_events'] == world['history_key_events']
    assert (project.metadata_json, world) == before


async def test_late_prerequisite_changes_both_effective_request_hashes(monkeypatch):
    project, world = _case()
    original = await _capture_judges(monkeypatch, project, world)
    project.metadata_json["book_design_snapshot"]["unknown_root"]["late"] += " Changed late clause."
    changed = await _capture_judges(monkeypatch, project, world)
    role = load_settings().llm.critic
    for old, new in zip(original, changed, strict=True):
        # Hold the scope identities constant; only source content changed.
        new = new.model_copy(
            update={"project_id": old.project_id, "workflow_run_id": old.workflow_run_id}
        )
        assert _effective_input_hash(old, role) != _effective_input_hash(new, role)


def test_large_shared_source_uses_existing_lossless_codec_with_complete_values():
    project, world = _case(repeated=700)
    brief = _brief(project, world)
    original = deepcopy(brief)
    rendered = judge.render_outline_source_brief(brief)
    assert "source_review_data=" in rendered
    encoded = json.loads(rendered.split("source_review_data=", 1)[1])
    restored = decode_lossless_source(encoded)
    authority = restored["source_authority"]
    assert authority["book_design_snapshot"] == project.metadata_json["book_design_snapshot"]
    assert (
        authority["approved_world_authority"]["full_story_source"]["late_unknown"]
        == world["late_unknown"]
    )
    assert_source_bound_planning_prompt_capacity("", rendered)
    assert brief == original


async def test_uncompressible_source_fails_before_any_inert_completion(monkeypatch):
    project, world = _case()
    project.metadata_json["book_design_snapshot"]["unknown_root"]["late"] = "".join(
        hashlib.sha256(str(i).encode()).hexdigest() for i in range(MAX_PROMPT_CHARS // 40)
    )
    sent = []

    async def forbidden(*args, **kwargs):
        sent.append(True)
        pytest.fail("Source capacity failure must precede the completion boundary")

    monkeypatch.setattr(judge, "complete_text", forbidden)
    with pytest.raises(SourceBoundPlanningCapacityError):
        await judge.judge_outline_commercial_readiness(
            None,
            load_settings(),
            outline_payload={"chapters": []},
            project_brief=_brief(project, world),
            workflow_run_id=uuid4(),
            project_id=project.id,
        )
    assert sent == []
