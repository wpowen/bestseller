"""Lossless, provisional source memory. No model calls and no formal Canon writes.

For this bounded book use the formal model's validated request envelope rather
than repeatedly summarize to fit an obsolete global 65k constant. Full prior
prose stays in safety requests; the index only binds versions and offsets.
"""

from copy import deepcopy
from uuid import uuid5, NAMESPACE_URL
from bestseller.infra.db import models as m
from bestseller.services import draft_continuity as dc
from bestseller.services.prompt_source_policy import content_hash


async def build(session, settings, *, project, draft, allow_generate=True):
    body = draft.content_md
    data = {
        "schema": "body-bound-prose-index.v1",
        "project_id": str(project.id),
        "draft_id": str(draft.id),
        "body_sha256": dc.body_hash(body),
        "anchors": [[a["id"], a["start"], a["end"]] for a in dc.build_critic_body_anchors(body)],
        "authority": "Provisional qualified manuscript only; not formal Canon.",
        "formal_canon": False,
        "new_model_calls": 0,
    }
    identity = uuid5(NAMESPACE_URL, "book-prose-index:" + content_hash(data))
    row = await session.get(m.WorkflowRunModel, identity, populate_existing=True)
    if row is None:
        if not allow_generate:
            raise ValueError("book_index_existing_receipt_required")
        row = m.WorkflowRunModel(
            id=identity,
            project_id=project.id,
            workflow_type="book_memory_reference",
            scope_type="chapter",
            scope_id=draft.chapter_id,
            status="completed",
            metadata_json=data,
        )
        session.add(row)
        await session.flush()
    elif row.status != "completed" or row.metadata_json != data:
        raise ValueError("book_index_source_changed")
    return {**data, "evidence": {"workflow_id": str(identity), "index_sha256": content_hash(data)}}


async def projected_sources(session, settings, *, project, chapter, draft, selected, indexes):
    parent = await dc.current_sources(session, settings, project=project, chapter=chapter)
    for row in parent["prior_manuscript_continuity"]["sources"]:
        expected = selected[str(row["chapter_number"])]
        if (row["draft_id"], row["body_sha256"]) != (expected["draft_id"], expected["body_sha256"]):
            raise ValueError("book_capacity_selected_source_changed")
        # All full prose is retained. The address index need not be regenerated
        # or semantically summarized to construct this source view.
    return (
        parent,
        deepcopy(parent),
        {
            "schema": "book-full-source-view.v1",
            "parent_sha256": content_hash(parent),
            "projected_sha256": content_hash(parent),
            "omissions": [],
            "retrieved": "all_selected_prior_full_prose",
            "excluded_full_text_assessed": False,
            "formal_canon_created": False,
            "new_model_calls": 0,
        },
    )
