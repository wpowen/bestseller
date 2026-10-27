from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.services.derived_revision_projection import (
    _identity_names,
    _with_projection_meta,
)

pytestmark = pytest.mark.unit


def test_projection_identity_contract_collects_locked_cast_members() -> None:
    assert _identity_names(
        {
            "protagonist": {"name": "纪年"},
            "supporting_cast": [{"canonical_name": "陈瑜"}],
            "antagonist_forces": [{"name": "孙砚"}],
        }
    ) == {"纪年", "陈瑜", "孙砚"}


def test_projection_meta_records_parent_inputs_without_mutating_source() -> None:
    payload = {"protagonist": {"name": "纪年"}}
    book_id = uuid4()
    world_id = uuid4()

    projected = _with_projection_meta(
        payload,
        report_id="report-1",
        parent_book_id=book_id,
        parent_world_id=world_id,
    )

    assert "_meta" not in payload
    assert projected["_meta"] == {
        "derived_revision_projection": True,
        "revision_report_id": "report-1",
        "parent_book_artifact_id": str(book_id),
        "parent_world_artifact_id": str(world_id),
    }
