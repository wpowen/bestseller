"""Effect routes cannot manufacture author obligations or erase real constraints."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.drafts import (
    audit_persisted_story_effect_realization,
    reconcile_persisted_effect_requirement,
)
from bestseller.services.story_effect_realization import (
    audit_story_effect_realization,
    extract_story_effect_evidence,
)


def payload(selection=None, **extra):
    return {
        "selected_effect_skills": selection or {"primary": "comedy_engine"},
        "prose": "渡船停靠。",
        **extra,
    }


def explicit_creation(*, strict=False, effects=("comedy_engine",)):
    creation = {
        "field_sources": {"story_enhancers": "explicit"},
        "story_enhancers": {"effect_skills": list(effects)},
    }
    if strict:
        creation["field_sources"]["hook_spec"] = "explicit"
        creation["hook_spec"] = {
            "effect_realization_contracts": {
                "comedy_engine": {
                    "required_stages": [
                        "setup",
                        "reversal_or_misalignment",
                        "character_reaction",
                        "situation_change",
                    ]
                }
            }
        }
    return creation


@pytest.mark.parametrize("source", ["ai", "ai_suggestion", "ai_recommendation", "planner", "model"])
@pytest.mark.parametrize("approved", [None, False, True])
def test_recommendation_or_approval_boolean_alone_does_not_authorize(source, approved):
    result = audit_story_effect_realization(
        payload({"primary": "comedy_engine", "source": source, "approved": approved})
    )
    assert result["passed"] and result["selected_effects"] == []
    assert result["requirement_provenance"]["advisory_effects"] == ["comedy_engine"]


@pytest.mark.parametrize(
    "container", ["story_enhancers", "creation_intent_contract", "genre_intent_contract"]
)
def test_author_palette_requires_routed_effect_but_not_every_chapter(container):
    choice = {"effect_skills": ["comedy_engine"]}
    if container == "creation_intent_contract":
        choice = {"story_enhancers": choice}
    elif container == "genre_intent_contract":
        choice = {"explicit_enhancers": choice}
    project = {container: choice}
    legacy = audit_story_effect_realization(payload(_effect_project_metadata=project))
    assert legacy["passed"] and not legacy["selected_effects"]
    assert legacy["requirement_provenance"]["advisory_effects"] == ["comedy_engine"]
    project["creation_intent_contract"] = explicit_creation()
    selected = audit_story_effect_realization(payload(_effect_project_metadata=project))
    assert selected["passed"] is True
    assert selected["requirement_provenance"]["required_effects"] == []
    assert selected["editorial_debt"] == ["comedy_realization_unverified"]
    assert selected["missing"] == [
        "setup",
        "reversal_or_misalignment",
        "character_reaction",
        "situation_change",
        "evidence_spans",
    ]
    unrouted = audit_story_effect_realization(
        {"_effect_project_metadata": project, "prose": "无关章节"}
    )
    assert unrouted["passed"] and not unrouted["selected_effects"]


def test_author_labels_and_approval_without_creation_entry_remain_advisory():
    for selection in (
        {"primary": "comedy_engine", "source": "author"},
        {"primary": {"skill_key": "comedy_engine", "source": "user"}},
        {
            "primary": "comedy_engine",
            "source": "ai_suggestion",
            "approved": True,
            "approved_by": "author",
        },
    ):
        report = audit_story_effect_realization(
            payload(selection, _effect_project_metadata={"selected_effect_skills": selection})
        )
        assert report["passed"] and report["selected_effects"] == []
        assert report["requirement_provenance"]["required_effects"] == []


def test_planner_rejection_cannot_cancel_independent_author_effect_requirement():
    result = audit_story_effect_realization(
        payload(
            {
                "primary": {
                    "skill_key": "comedy_engine",
                    "source": "ai_suggestion",
                    "approved": False,
                }
            },
            _effect_project_metadata={"creation_intent_contract": explicit_creation()},
        )
    )
    assert result["passed"] and result["selected_effects"] == ["comedy_engine"]
    assert result["editorial_debt"] == ["comedy_realization_unverified"]


def test_unrelated_palette_does_not_authorize_comedy():
    result = audit_story_effect_realization(
        payload(
            _effect_project_metadata={
                "creation_intent_contract": explicit_creation(effects=("danger_action_engine",))
            }
        )
    )
    assert result["passed"] and result["selected_effects"] == []


def records(*, authorized=False):
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={
            "genre_intent_contract": {
                "explicit_enhancers": {"effect_skills": ["comedy_engine"] if authorized else []}
            },
            "override_contracts": [{"id": "preserved-debt"}],
        },
    )
    chapter = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        production_state="blocked",
        metadata_json={
            "selected_effect_skills": {"primary": "comedy_engine"},
            "effect_realization_blocked": True,
            "story_effect_realization_block_codes": ["STORY_EFFECT_REALIZATION_UNREALIZED"],
            "effect_realization_gate": {"passed": False, "selected_effects": ["comedy_engine"]},
            "other_gate": "blocked",
        },
    )
    draft = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        chapter_id=chapter.id,
        content_md="港口收到一封信。",
        is_current=True,
        promotion_state="candidate",
        promotion_metadata={"quality_gate_outcome": "blocked"},
    )
    if authorized:
        project.metadata_json["creation_intent_contract"] = explicit_creation(strict=True)
    return project, chapter, draft


def test_historical_invalid_obligation_supersession_is_bound_idempotent_and_narrow():
    project, chapter, draft = records()
    before_project, before_draft = deepcopy(vars(project)), deepcopy(vars(draft))
    result = reconcile_persisted_effect_requirement(project, chapter, draft)
    assert result["changed"]
    assert chapter.metadata_json["effect_realization_blocked"] is False
    assert "story_effect_realization_block_codes" not in chapter.metadata_json
    receipt = chapter.metadata_json["effect_requirement_supersessions"][0]
    assert receipt["previous_report"]["passed"] is False
    assert chapter.metadata_json["effect_realization_gate"]["source_draft_id"] == str(draft.id)
    assert len(chapter.metadata_json["effect_realization_gate"]["source_sha256"]) == 64
    assert not reconcile_persisted_effect_requirement(project, chapter, draft)["changed"]
    assert len(chapter.metadata_json["effect_requirement_supersessions"]) == 1
    assert (
        chapter.production_state == "blocked" and chapter.metadata_json["other_gate"] == "blocked"
    )
    assert vars(project) == before_project and vars(draft) == before_draft


def test_real_author_requirement_and_existing_debt_are_not_superseded():
    project, chapter, draft = records(authorized=True)
    before = deepcopy(chapter.metadata_json)
    assert not reconcile_persisted_effect_requirement(project, chapter, draft)["changed"]
    assert chapter.metadata_json == before
    assert not audit_persisted_story_effect_realization(project, chapter, draft.content_md)[
        "passed"
    ]


@pytest.mark.parametrize("field", ["project_id", "chapter_id"])
def test_supersession_rejects_foreign_source(field):
    project, chapter, draft = records()
    setattr(draft, field, uuid4())
    before = deepcopy(chapter.metadata_json)
    with pytest.raises(ValueError, match="ownership"):
        reconcile_persisted_effect_requirement(project, chapter, draft)
    assert chapter.metadata_json == before


@pytest.mark.asyncio
async def test_advisory_effect_never_requests_paid_evidence(monkeypatch):
    from bestseller.services import llm

    async def forbidden(*_a, **_kw):
        pytest.fail("Advisory routes must not call the model gateway")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    result = await extract_story_effect_evidence(
        None,
        None,
        chapter_payload=payload(),
        prose="雾散了。",
        project_id=uuid4(),
        chapter_id=uuid4(),
        chapter_number=8,
        workflow_run_id=None,
    )
    assert result == {"status": "not_selected", "contract": None, "llm_run_id": None}


def test_planner_cannot_forge_author_approval_or_reserved_project_authority():
    project, chapter, draft = records()
    chapter.metadata_json["selected_effect_skills"].update(source="author", approved=True)
    chapter.metadata_json["_effect_project_metadata"] = {
        "creation_intent_contract": explicit_creation(strict=True)
    }
    chapter.metadata_json["creation_intent_contract"] = explicit_creation(strict=True)
    chapter.metadata_json["story_enhancers"] = {"effect_skills": ["comedy_engine"]}
    result = audit_persisted_story_effect_realization(project, chapter, draft.content_md)
    assert result["passed"] and not result["selected_effects"]


def test_supersession_removes_only_exact_effect_codes_from_live_containers():
    project, chapter, draft = records()
    code = "STORY_EFFECT_REALIZATION_UNREALIZED"
    for key in (
        "quality_gate_block_codes",
        "quality_bundle_blocking_codes",
        "auto_repair_last_block_codes",
    ):
        chapter.metadata_json[key] = ["UNEXPLAINED_BODY_STATE", code]
    chapter.metadata_json["production_block_code"] = code
    reconcile_persisted_effect_requirement(project, chapter, draft)
    for key in (
        "quality_gate_block_codes",
        "quality_bundle_blocking_codes",
        "auto_repair_last_block_codes",
    ):
        assert chapter.metadata_json[key] == ["UNEXPLAINED_BODY_STATE"]
    assert "production_block_code" not in chapter.metadata_json
    history = chapter.metadata_json["effect_requirement_supersessions"][0]
    assert history["source_draft_id"] == str(draft.id)
    assert history["previous_quality_codes"]["quality_gate_block_codes"] == [
        "UNEXPLAINED_BODY_STATE",
        code,
    ]


def test_ai_project_palette_does_not_authorize_but_does_not_mask_real_creation_choice():
    project = {"story_enhancers": {"effect_skills": ["comedy_engine"], "source": "ai_suggestion"}}
    assert audit_story_effect_realization(payload(_effect_project_metadata=project))["passed"]
    project["genre_intent_contract"] = {"explicit_enhancers": {"effect_skills": ["comedy_engine"]}}
    legacy = audit_story_effect_realization(payload(_effect_project_metadata=project))
    assert legacy["passed"] and not legacy["selected_effects"]
    project["creation_intent_contract"] = explicit_creation()
    assert audit_story_effect_realization(payload(_effect_project_metadata=project))[
        "selected_effects"
    ] == ["comedy_engine"]


def test_historical_old_draft_cannot_replace_current_gate_receipt():
    project, chapter, draft = records()
    draft.is_current = False
    before = deepcopy(chapter.metadata_json)
    with pytest.raises(ValueError, match="current source"):
        reconcile_persisted_effect_requirement(project, chapter, draft)
    assert chapter.metadata_json == before


def test_nested_advisory_metadata_and_project_only_selection_do_not_force_chapter():
    project = {"story_enhancers": {"effect_skills": ["comedy_engine"]}}
    result = audit_story_effect_realization(
        {
            "_effect_project_metadata": project,
            "enhancers": {"effect_skills": ["comedy_engine"], "source": "ai_suggestion"},
            "prose": "一艘船入港。",
        }
    )
    assert result["passed"] and not result["selected_effects"]
    from bestseller.services.drafts import _effect_realization_payload

    project, chapter, draft = records()
    project.metadata_json = {
        "selected_effect_skills": {"primary": "comedy_engine", "source": "author"}
    }
    chapter.metadata_json = {}
    assert not audit_story_effect_realization(
        _effect_realization_payload(project, chapter, draft.content_md)
    )["selected_effects"]


@pytest.mark.parametrize(
    "selection",
    [
        {"primary": "comedy_engine", "source": "planner"},
        {"primary": "comedy_engine", "source": "ai_suggestion", "approved": False},
    ],
)
def test_ai_labels_never_cancel_explicit_author_requirement(selection):
    project, chapter, draft = records(authorized=True)
    chapter.metadata_json["selected_effect_skills"] = selection
    result = reconcile_persisted_effect_requirement(project, chapter, draft)
    assert not result["changed"] and not result["report"]["passed"]
    assert chapter.metadata_json["effect_realization_blocked"] is True


def test_book_palette_only_does_not_become_a_chapter_route_in_adapter():
    project, chapter, draft = records(authorized=True)
    project.metadata_json["story_enhancers"] = {"effect_skills": ["comedy_engine"]}
    chapter.metadata_json = {}
    report = audit_persisted_story_effect_realization(project, chapter, draft.content_md)
    assert report["passed"] and not report["selected_effects"]
