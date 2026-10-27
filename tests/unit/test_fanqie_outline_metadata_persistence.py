"""The public persistence boundary retains producer custody metadata."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import fanqie_short_planner as planner

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_persistence_keeps_exact_custody_beside_validated_body(monkeypatch):
    seen = []
    identity = uuid4()

    async def capture(session, slug, payload):
        seen.append(deepcopy(payload.content))
        return SimpleNamespace(id=identity)

    monkeypatch.setattr(planner, "import_planning_artifact", capture)
    meta = {"segment_projection": {"policy": "fanqie-segment-projection.v1"},
        "outline_contract_completion": {"workflow_run_id": str(uuid4()),
            "grade_asserted": False, "parts": [{"part": 0}]}}
    payload = {"batch_name": "four-segments", "chapters": [], "_meta": meta}
    original = deepcopy(payload)
    result = await planner.persist_fanqie_chapter_outline(object(), "same-book", payload)
    assert result == identity and payload == original
    assert seen == [original]
    assert seen[0]["_meta"] is not meta


async def test_persistence_rejects_malformed_metadata_before_import(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid custody must not be imported")

    monkeypatch.setattr(planner, "import_planning_artifact", forbidden)
    with pytest.raises(ValueError, match="metadata_must_be_object"):
        await planner.persist_fanqie_chapter_outline(
            object(), "same-book", {"chapters": [], "_meta": "invalid"})
