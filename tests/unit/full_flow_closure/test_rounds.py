"""Durable round decisions never create budget, grades or adoption."""

from copy import deepcopy
import importlib.util

import pytest


def service():
    assert importlib.util.find_spec("bestseller.services.fanqie_whole_repair") is not None, (
        "missing bounded whole-book native driver"
    )
    from bestseller.services import fanqie_whole_repair

    return fanqie_whole_repair


def begin(state=None, **kw):
    args = dict(
        source_sha256="a" * 64,
        task_ids=["task1"],
        scope_id="native-scope",
        calls_used=20,
        native_max_calls=400,
    )
    args.update(kw)
    return service().begin_round(state, **args)


def test_running_round_reentry_keeps_ordinal_and_absolute_ceiling():
    first = begin()
    before = deepcopy(first)
    resumed = begin(first, calls_used=25)
    assert first == before and resumed["round"] == 1 and resumed["absolute_call_ceiling"] == 100
    assert resumed["calls_at_start"] == 20


def test_finished_unchanged_assembly_stops_without_spending_or_new_round():
    first = begin()
    first["status"] = "completed"
    with pytest.raises(ValueError, match="whole_repair_stagnant"):
        begin(first, calls_used=25)


def test_three_round_cap_cannot_reset_across_changed_sources_or_new_scope():
    state = begin()
    for i in range(2):
        state["status"] = "completed"
        state = begin(state, source_sha256=str(i + 1) * 64, calls_used=25 + i)
    assert state["round"] == 3
    state["status"] = "completed"
    with pytest.raises(ValueError, match="whole_repair_round_limit"):
        begin(state, source_sha256="4" * 64, calls_used=28)
    with pytest.raises(ValueError, match="whole_repair_scope_changed"):
        begin(state, scope_id="different")


@pytest.mark.parametrize("fault", ["hash", "count", "status", "keys"])
def test_corrupt_history_never_reinitializes(fault):
    state = begin()
    if fault == "hash":
        state["source_sha256"] = "bad"
    elif fault == "count":
        state["round"] = 0
    elif fault == "status":
        state["status"] = "approved"
    else:
        state["forged"] = True
    with pytest.raises(ValueError, match="whole_repair_state_invalid"):
        begin(state)


def test_a_b_a_cannot_extend_repair_life_with_wrapped_task_ids():
    first = begin()
    first["status"] = "completed"
    second = begin(first, source_sha256="b" * 64, calls_used=25, task_ids=["different-wrapper"])
    second["status"] = "completed"
    with pytest.raises(ValueError, match="whole_repair_stagnant"):
        begin(second, source_sha256="a" * 64, calls_used=30, task_ids=["new-wrapper"])
