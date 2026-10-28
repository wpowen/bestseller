"""Persistent source identity, business priority and author data boundaries."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from bestseller.services.source_ordering import ordered_source_rows, source_row_id

pytestmark = pytest.mark.unit


def test_tie_membership_is_stable_without_sorting_author_arrays():
    rows = [
        SimpleNamespace(id=UUID(int=i), priority=2, authored=["later", "earlier"])
        for i in range(13, 0, -1)
    ]
    original = deepcopy([row.authored for row in rows])
    first = ordered_source_rows(rows, priority=lambda row: (row.priority,))[:12]
    second = ordered_source_rows(reversed(rows), priority=lambda row: (row.priority,))[:12]
    assert (
        [row.id for row in first]
        == [row.id for row in second]
        == [UUID(int=i) for i in range(1, 13)]
    )
    assert [row.authored for row in rows] == original
    assert [row.id.int for row in rows] == list(range(13, 0, -1))


def test_unique_identity_does_not_override_existing_business_priority():
    rows = [SimpleNamespace(id=UUID(int=1), urgency=2), SimpleNamespace(id=UUID(int=99), urgency=0)]
    result = ordered_source_rows(rows, priority=lambda row: (row.urgency,))
    assert [row.id.int for row in result] == [99, 1]
    assert source_row_id(SimpleNamespace(id=str(UUID(int=99)))) == 99


@pytest.mark.parametrize("value", [None, 0, False, "not-a-uuid"])
def test_missing_persistent_identity_never_falls_back_to_position_or_prose(value):
    with pytest.raises(ValueError, match="persistent_identity_required") as caught:
        ordered_source_rows([SimpleNamespace(id=value, name="same content", created_at=0)])
    assert caught.value.differences[0]["field"] == "source_row.id"
    assert caught.value.differences[0]["actual"]["type"] == type(value).__name__
