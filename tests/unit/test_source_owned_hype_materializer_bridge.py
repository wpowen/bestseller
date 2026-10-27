"""Actual full materializer -> stamp -> persisted source projection bridge.

Only the existing upstream admission fixture and diversity storage are inert;
materialization, classification, source verification and SQLite readback are real.
"""

from copy import deepcopy
from hashlib import sha256

import pytest
from test_adopted_knowledge_lineage import db as db
from test_full_outline_reconciliation_materializer import full_case as full_case
from test_full_outline_reconciliation_materializer import run
from test_selected_prefix_hype_observation import HIT, KEY
from test_selected_prefix_hype_observation import budget_io as budget_io
from test_unadopted_outline_reconciliation import _raw
from test_unadopted_outline_reconciliation import migration as migration

from bestseller.services.chapter_outline_readiness import (
    _verify_materialized_content,
    outline_prefix_projection_hash,
)
from bestseller.services.drafts import stamp_chapter_hype
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rewrite_outline_binding import validate_source_plan_binding

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def sqlite_only(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")


async def test_actual_full_materializer_marker_still_matches_after_native_stamp_and_readback(
    full_case, budget_io
):
    case, proof, _calls = full_case
    assert case.session.bind.dialect.name == "sqlite"
    await run(case)
    await case.session.commit()
    await case.session.refresh(case.chapter)
    for scene in case.scenes:
        await case.session.refresh(scene)
    original_source = _raw(case.source)
    bodies = [_raw(draft) for draft in [*case.drafts, *case.scene_drafts]]
    before_metadata = deepcopy(case.chapter.metadata_json)
    marker = deepcopy(before_metadata["outline_source_admission"])
    assert "outline_prefix_readiness" not in before_metadata
    assert all(getattr(case.chapter, key) is None for key in (
        "hype_type", "hype_intensity", "hype_recipe_key"
    ))
    for _ in range(2):
        await stamp_chapter_hype(
            case.session, chapter=case.chapter, chapter_number=case.chapter.chapter_number,
            content_md=HIT, project=case.project,
            refresh=case.chapter.hype_type is not None,
        )
        await case.session.commit()
        await case.session.refresh(case.chapter)
        _verify_materialized_content(
            case.chapter, case.scenes, case.source.content["chapters"][0]
        )
        assert validate_source_plan_binding(
            case.chapter.metadata_json["outline_source_admission"]
        ) == {
            "artifact_id": str(case.source.id),
            "workflow_run_id": proof["workflow_run_id"],
            "source_content_sha256": content_hash(case.source.content),
            "projection_hash": outline_prefix_projection_hash(case.chapter, case.scenes),
        } == marker
    await case.session.refresh(case.source)
    for draft in [*case.drafts, *case.scene_drafts]:
        await case.session.refresh(draft)
    assert _raw(case.source) == original_source
    assert [_raw(draft) for draft in [*case.drafts, *case.scene_drafts]] == bodies
    assert {
        key: value for key, value in case.chapter.metadata_json.items() if key != KEY
    } == before_metadata
    observation = case.chapter.metadata_json[KEY]
    assert observation["body_sha256"] == sha256(HIT.encode()).hexdigest()
    assert observation["adoption_authorized"] is False
    assert len(observation["history"]) == 1
    assert budget_io[1] == [case.project.id]
