"""Current editable-plan packets retain every field except one opaque audit leaf."""
from copy import deepcopy
import json
import pytest
from test_retained_review_input_proof import _audit_pair
from test_adopted_source_correction_sqlite import db as db

from bestseller.infra.db.models import ChapterModel, ProjectModel
from bestseller.services import editable_plan_source as editable
from bestseller.services import retained_review_input_proof as proof

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def pair(db, *, selected=False, pending=False):
    old, new = _audit_pair()
    async with db.sessions() as session:
        project = await session.get(ProjectModel, db.book[0])
        chapter = await session.get(ChapterModel, db.book[1])
        plan = await editable.build_editable_plan_source(
            session, project=project, chapter=chapter, scenes=[])
    if selected:
        plan.pop("reason")
        plan.update(status="available", binding={"plan_sha256": "a"*64},
                    plan={"goal": "Cross the river after repairing the ferry"})
    for source in (old, new):
        source["schema"] = editable.REVIEW_SOURCE_SCHEMA
        source["editable_plan"] = deepcopy(plan)
        if pending:
            source["draft_manuscript_continuity"] = source.pop("adopted_continuity")
    return old, new


def bridge(old, new):
    prompt = json.dumps(old, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return proof._audit_source_compatibility(prompt,
        {"source_complete": True, "source_sha256": proof._hash(old)}, new)


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("pending", [False, True])
async def test_actual_schema_allows_only_the_existing_opaque_audit_edge(db, selected, pending):
    old, new = await pair(db, selected=selected, pending=pending)
    receipt = bridge(old, new)
    assert receipt["source_schema"] == editable.REVIEW_SOURCE_SCHEMA
    assert receipt["changed_pointer"] == proof._AUDIT_POINTER
    assert receipt["original_source_verified"] is True
    assert receipt["raw_bundle_equivalence_asserted"] is False
    assert receipt["paid_request_bytes_equivalence_asserted"] is False
    assert receipt["selected_semantic_sha256"]


@pytest.mark.parametrize("mutation", ["fact", "prior", "plan", "binding", "authority", "schema", "audit_policy", "extra", "audit_hash"])
async def test_current_schema_other_source_changes_still_reject(db, mutation):
    old, new = await pair(db, selected=True)
    if mutation == "fact":new["current_source_channels"]["established_facts"]["facts"][0] = "Different event"
    elif mutation == "prior":new["adopted_continuity"]["facts"].append("Future event")
    elif mutation == "plan":new["editable_plan"]["plan"]["goal"] = "Leave the ferry broken"
    elif mutation == "binding":new["editable_plan"]["binding"]["plan_sha256"] = "b"*64
    elif mutation == "authority":new["editable_plan"]["authority"] = "author_hardlaw"
    elif mutation == "schema":new["schema"] = "unverified-future-schema"
    elif mutation == "audit_policy":new["current_source_channels"]["source_selection_receipt"]["policy"] += "changed"
    elif mutation == "extra":new["unknown"] = "new source"
    else:new["current_source_channels"]["source_selection_receipt"]["original_source_sha256"] = "bad"
    assert bridge(old, new) is None


async def test_current_schema_still_requires_exact_canonical_paid_packet(db):
    old, new = await pair(db, selected=True)
    receipt = {"source_complete": True, "source_sha256": proof._hash(old)}
    assert proof._audit_source_compatibility(json.dumps(old, indent=2), receipt, new) is None
    assert proof._audit_source_compatibility("unavailable", receipt, new) is None
    prompt = json.dumps(old, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert proof._audit_source_compatibility(prompt, {**receipt, "source_sha256": "f"*64}, new) is None
