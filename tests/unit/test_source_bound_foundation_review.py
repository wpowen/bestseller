from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.services import planner
from bestseller.services.conception_snapshots import snapshot_hash

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("review_passes", [False, True])
@pytest.mark.parametrize("invalid_identity", [False, True])
async def test_source_bound_cast_repair_acceptance_requires_identity_and_source_review(
    monkeypatch, review_passes, invalid_identity
):
    original = {
        "protagonist": {
            "name": "沈落", "gender": "male", "pronoun_set_zh": "他", "pronoun_set_en": "he/him",
        },
        "antagonist_forces": [],
    }
    repaired = {
        "protagonist": {
            "name": "沈落", "gender": "male", "pronoun_set_zh": "他", "pronoun_set_en": "he/him",
        },
        "antagonist_forces": [{"name": "新势力"}],
    }
    if invalid_identity:
        repaired["supporting_cast"] = [{"name": "未知角色", "gender": "unknown"}]
    project = SimpleNamespace(
        slug="source-bound-test",
        language="zh-CN",
        metadata_json={"source_bound_expansion_review": {"candidate_hash": "approved-baseline"}},
        id=uuid4(),
    )
    content = {"cast_spec": deepcopy(original)}
    source_id, source_hash = str(uuid4()), snapshot_hash(content)
    project.metadata_json.update({
        "conception_approved": True,
        "creation_canonical_snapshot_id": source_id,
        "creation_canonical_snapshot_hash": source_hash,
        "book_design_snapshot_status": "locked",
        "book_design_snapshot": {"snapshot_id": source_id, "source_hash": source_hash,
            "protagonist": deepcopy(original["protagonist"])},
    })
    project._source_bound_planning_canonical = {
        "id": source_id, "content_hash": source_hash, "content": content,
    }
    assert planner._source_bound_cast_enabled(project)
    canon = planner._source_bound_canonical_protagonist(project, "")
    assert all(canon[key] == value for key, value in original["protagonist"].items())
    review = AsyncMock(
        return_value={"decision": "pass", "draft_eligible": True}
        if review_passes else {"reason": "blocked", "draft_eligible": False},
    )
    monkeypatch.setattr(planner, "_review_source_bound_expansion", review)
    if review_passes and not invalid_identity:
        monkeypatch.setattr(planner, "_source_bound_report_is_draft_eligible", lambda report: True)

    repair_run_id, workflow_run_id = uuid4(), uuid4()
    settings = SimpleNamespace()
    book_spec, world_spec = {}, {}
    result, run_id = await planner._source_bound_accept_cast_repair(
        session=None,
        settings=settings,
        project=project,
        book_spec_payload=book_spec,
        world_spec_payload=world_spec,
        baseline_cast=original,
        candidate_cast=repaired,
        repair_run_id=repair_run_id,
        workflow_run_id=workflow_run_id,
        audit_key="source_bound_foundation_repair_review",
    )

    if invalid_identity:
        review.assert_not_awaited()
    else:
        review.assert_awaited_once_with(
            None, settings, project=project, book_spec=book_spec,
            world_baseline=world_spec, world_derived=world_spec,
            cast_baseline=original, cast_derived=repaired,
            workflow_run_id=workflow_run_id,
        )

    if review_passes and not invalid_identity:
        assert result is repaired
        assert run_id == repair_run_id
        assert project.metadata_json["source_bound_expansion_review"]["decision"] == "pass"
        assert "source_bound_expansion_repair_review" not in project.metadata_json
    else:
        assert result is original
        assert run_id is None
        assert (
            project.metadata_json["source_bound_expansion_review"]["candidate_hash"]
            == "approved-baseline"
        )
        if not invalid_identity:
            assert (
                project.metadata_json["source_bound_foundation_repair_review"]["reason"]
                == "blocked"
            )


def test_source_bound_merged_cast_identity_checks_inherited_and_new_roles():
    baseline = {
        "protagonist": {
            "name": "沈落", "gender": "male", "pronoun_set_zh": "他", "pronoun_set_en": "he/him",
        },
        "supporting_cast": [],
    }
    candidate = {
        "supporting_cast": [
            {"name": "新角色", "gender": "unknown"},
        ]
    }
    with pytest.raises(ValueError, match="merged supporting_cast"):
        planner._validate_source_bound_merged_cast_identities(baseline, candidate)
