"""Main-run RED: send refusals carry exact fields and non-disclosing typed hashes."""

from copy import deepcopy
import hashlib
import json

import pytest
from sqlalchemy.orm.attributes import flag_modified

from bestseller.infra.db import models as m
from bestseller.services import chapter_automatic_revision_budget as revision
from bestseller.services.budget import ExecutionBudgetError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def summary(value):
    # Match main's receipt convention; values never appear in the emitted summary.
    encoded = json.dumps(value, sort_keys=True, default=str).encode()
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, list, dict, bytes)) else None,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def diagnostic(error, field, request):
    differences = getattr(error, "differences", [])
    request.node.user_properties.append(("budget_error_code", error.code))
    request.node.user_properties.append((
        "budget_differences", json.dumps(differences, sort_keys=True),
    ))
    match = [item for item in differences if item.get("field") == field]
    assert len(match) == 1, f"missing exact rejection field: {field}"
    item = match[0]
    for side in ("expected", "actual"):
        capsule = item[side]
        assert set(capsule) == {"type", "length", "sha256"}
        assert isinstance(capsule["type"], str)
        assert capsule["length"] is None or type(capsule["length"]) is int
        assert len(capsule["sha256"]) == 64
        assert all(char in "0123456789abcdef" for char in capsule["sha256"])
    cause = error.__cause__
    assert isinstance(cause, revision.AutomaticRevisionBudgetError)
    assert differences == cause.differences
    return item


@pytest.mark.parametrize("case", ["missing", "null", "bad_id"])
async def test_selector_send_error_has_exact_field_and_actual_typed_hash(gateway, case, request):
    metadata = {}
    field = "request.metadata.automatic_revision_source"
    actual = None
    if case == "null":
        metadata[field.removeprefix("request.metadata.")] = None
    elif case == "bad_id":
        actual = "diagnostic-fixture-private-value"
        metadata["automatic_revision_source"] = {**gateway.selector, "source_draft_id": actual}
        field += ".source_draft_id"
    with pytest.raises(ExecutionBudgetError, match="automatic_revision_budget:") as caught:
        await gateway.call(gateway.request(metadata=metadata))
    item = diagnostic(caught.value, field, request)
    assert item["actual"] == summary(actual)
    assert "diagnostic-fixture-private-value" not in json.dumps(caught.value.differences)
    assert not gateway.wires and not gateway.sdk_calls


@pytest.mark.parametrize("case,field", [
    ("control", "book_production_controls.desired_state"),
    ("owner", "execution_operations.owner"),
    ("input_hash", "execution_operations.input_hash"),
    ("source", "chapter_draft_versions.content_md.sha256"),
    ("bool_round", "chapter_automatic_revision_budget.history[0].round_no"),
])
async def test_native_send_guards_hash_compared_operands_without_exposing_them(
    gateway, case, field, request,
):
    operands = {}

    async def mutate_after_reservation(call):
        async with gateway.sessions.begin() as session:
            if case == "control":
                row = await session.get(m.BookProductionControlModel, gateway.project_id)
                operands.update(expected=row.desired_state, actual="pause")
                row.desired_state = "pause"
            elif case in {"owner", "input_hash"}:
                row = await session.get(m.ExecutionOperationModel, call.claim.key)
                actual = "private-owner-fixture" if case == "owner" else "b" * 64
                operands.update(expected=getattr(row, case), actual=actual)
                setattr(row, case, actual)
            elif case == "source":
                row = await session.get(m.ChapterDraftVersionModel, gateway.source_id)
                before = hashlib.sha256(row.content_md.encode()).hexdigest()
                row.content_md += "\n并发正文，不得进入错误回执。"
                after = hashlib.sha256(row.content_md.encode()).hexdigest()
                operands.update(expected=before, actual=after)
            else:
                row = await session.get(m.ChapterModel, gateway.chapter_id)
                metadata = deepcopy(row.metadata_json)
                metadata[revision.JOURNAL_KEY]["history"][0]["round_no"] = True
                row.metadata_json = metadata
                flag_modified(row, "metadata_json")
                operands.update(expected=1, actual=True)

    gateway.before_wire = mutate_after_reservation
    with pytest.raises(ExecutionBudgetError) as caught:
        await gateway.call(gateway.request())
    item = diagnostic(caught.value, field, request)
    assert item["expected"] == summary(operands["expected"])
    assert item["actual"] == summary(operands["actual"])
    encoded = json.dumps(caught.value.differences, ensure_ascii=False)
    assert gateway.body not in encoded
    assert "private-owner-fixture" not in encoded
    assert "并发正文，不得进入错误回执。" not in encoded
    assert gateway.wires == []
    assert len((await gateway.journal())["history"]) == 1
