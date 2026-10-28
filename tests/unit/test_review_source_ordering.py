"""Source epoch classification; these synthetic inputs grant no paid authority."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import review_source_ordering as epoch

pytestmark = pytest.mark.unit


def primary_source(marker=None):
    receipt = {"policy": "typed-world-and-relevant-current-state.v1"}
    if marker is not None:
        receipt["source_ordering_policy"] = marker
    return {
        "current_source_channels": {
            "source_selection_receipt": receipt,
            "story_bible": {"relationships": [{"type": "second"}, {"type": "first"}]},
        }
    }


def view(source):
    return SimpleNamespace(calls=(SimpleNamespace(source=source),))


def test_pre_f0_returns_nonreuse_without_changing_recorded_order():
    source = primary_source()
    before = deepcopy(source)
    result = epoch._source_epoch(view(source))
    assert result["status"] == "not_reusable"
    assert result["code"] == "pre_f0_source_unprovable"
    assert source == before


def test_post_f0_requires_marker_in_paid_source_not_caller_cycle():
    assert (
        epoch._source_epoch(view(primary_source(epoch.SOURCE_ORDERING_POLICY)))["status"]
        == "recorded_post_f0"
    )
    assert epoch._source_epoch(view(primary_source()))["status"] == "not_reusable"


@pytest.mark.parametrize("marker", [True, 1, {}, "future-unknown-policy"])
def test_unknown_or_forged_source_epoch_has_detailed_failure(marker):
    with pytest.raises(epoch.ReviewSourceOrderingError) as caught:
        epoch._source_epoch(view(primary_source(marker)))
    assert caught.value.differences
    assert all(set(d) == {"field", "expected", "actual"} for d in caught.value.differences)


def test_mixed_primary_and_commercial_epochs_cannot_claim_post_f0():
    mixed = SimpleNamespace(
        calls=(
            SimpleNamespace(source=primary_source(epoch.SOURCE_ORDERING_POLICY)),
            SimpleNamespace(source=primary_source()),
        )
    )
    with pytest.raises(epoch.ReviewSourceOrderingError, match="mixed_source_epochs"):
        epoch._source_epoch(mixed)


def test_public_decision_accepts_native_ids_only():
    with pytest.raises(TypeError):
        epoch.read_review_reuse_decision(
            None,
            None,
            project_id=uuid4(),
            chapter_id=uuid4(),
            draft_id=uuid4(),
            source=primary_source(),
            approved=True,
        )


def test_successor_identity_does_not_depend_on_version_or_actor():
    ids = dict(
        project_id=uuid4(),
        chapter_id=uuid4(),
        draft_id=uuid4(),
        body_sha256="b" * 64,
        source_cycle_id="c" * 64,
    )
    assert epoch._successor_id(**ids) == epoch._successor_id(**ids)
    assert epoch._successor_id(**ids) != epoch._successor_id(**{**ids, "source_cycle_id": "d" * 64})


def test_supersession_appends_without_rewriting_cycles_once_unknown_or_counters():
    metadata = {
        "chapter_critic_cycles": {
            "old": {
                "state": "completed",
                "attempts": [{"outcome": "unknown"}],
                "infrastructure_resumptions": [{"once": True}],
            }
        },
        "chapter_review_attempts_active": 7,
        "chapter_review_active_cycle": {"cycle_id": "old", "attempts_base": 2},
        "unrelated": [3, 1, 2],
    }
    receipt = {
        "source_cycle_id": "old",
        "successor_cycle_id": "new",
        "draft_id": "d",
        "body_sha256": "b" * 64,
        "receipt_sha256": "r" * 64,
    }
    before = deepcopy(metadata)
    revised = epoch._append_supersession(metadata, receipt)
    assert metadata == before
    assert revised["chapter_critic_cycles"] == before["chapter_critic_cycles"]
    assert revised["chapter_review_attempts_active"] == 7
    assert revised["chapter_review_active_cycle"]["attempts_base"] == 2
    assert revised[epoch.HISTORY_KEY] == [receipt]
    assert (
        revised[epoch.POINTER_HISTORY_KEY][receipt["receipt_sha256"]]
        == before["chapter_review_active_cycle"]
    )
    assert epoch._append_supersession(revised, receipt) == revised
    # A later ordinary body/cycle cannot be redirected by replaying old closure.
    later = deepcopy(revised)
    later["chapter_review_active_cycle"]["cycle_id"] = "later"
    assert epoch._append_supersession(later, receipt) == later


def test_supersession_cannot_allocate_second_successor_for_same_old_cycle():
    receipt = {"source_cycle_id": "old", "successor_cycle_id": "one"}
    with pytest.raises(epoch.ReviewSourceOrderingError) as caught:
        epoch._append_supersession(
            {epoch.HISTORY_KEY: [receipt]}, {**receipt, "successor_cycle_id": "two"}
        )
    assert caught.value.differences[0]["field"] == "supersession.once"


def test_legacy_v1_successor_identity_is_exact_and_not_reissued_as_v2():
    ids=dict(project_id=uuid4(),chapter_id=uuid4(),draft_id=uuid4(),
             body_sha256="a"*64,source_cycle_id="b"*64)
    expected=epoch.recorded._hash({"schema":"pre-f0-review-disposition.v1",
        **{k:str(v) for k,v in ids.items()},"source_ordering_policy":epoch.SOURCE_ORDERING_POLICY})
    assert epoch._successor_id(**ids,receipt_schema=epoch.LEGACY_SCHEMA)==expected
    assert epoch._successor_id(**ids)!=expected
