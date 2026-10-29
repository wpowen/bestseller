"""New creations choose the capacity epoch; saved inputs retain their epoch."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from bestseller.services import conception, planner
from bestseller.services import source_bound_planning_content as protocol

pytestmark = pytest.mark.unit
RAW = "  Ordinary family choice.\nOriginal spacing stays.  "
V2 = "reader-first-planning.v2"


def test_new_conception_chooses_bounded_capacity_epoch_before_any_generation():
    context = {"genre": "realistic-life", "chapter_count": 4}
    marker = conception._bind_reader_first_conception(context, original_user_input=RAW)
    assert marker["protocol"] == V2
    assert marker == protocol.new_reader_first_binding(RAW, protocol=V2)
    assert context["reader_first_original_input"] == RAW


def test_saved_v1_conception_binding_is_not_silently_upgraded():
    old = protocol.new_reader_first_binding(RAW)
    context = {"reader_first_planning": deepcopy(old), "reader_first_original_input": RAW}
    assert conception._bind_reader_first_conception(context, original_user_input=RAW) == old
    assert context["reader_first_planning"] == old
    with pytest.raises(ValueError, match="protocol_conflict"):
        conception._bind_reader_first_conception(context, original_user_input=RAW + "changed")


def test_planner_does_not_duplicate_the_bound_v2_instruction():
    binding = protocol.new_reader_first_binding(RAW, protocol=V2)
    project = SimpleNamespace(metadata_json={"reader_first_planning": binding}, language="en")
    instruction = protocol.reader_first_instruction(binding, language="en", stage="planning")
    system, user = planner._reader_first_planner_prompts(
        project, instruction, "Unchanged request.", stage="planning",
    )
    assert system == instruction
    assert user == "Unchanged request."
