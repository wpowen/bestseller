"""Continuation v4 advertises fields required by the real integrity boundary."""

from copy import deepcopy
import json

import pytest
from test_chapter_outline_readiness import _approve, rig  # noqa: F401 -- Reuse inert request rig.
from test_source_bound_outline_integrity import source, validate

from bestseller.services import chapter_outline_readiness as ready
from bestseller.services import planner
from bestseller.services.prompt_source_policy import content_hash

pytestmark = pytest.mark.unit


async def test_actual_continuation_request_advertises_all_three_required_fields(
    rig,  # noqa: F811 -- Pytest injects the imported fixture.
    monkeypatch,
):
    from bestseller.services import planning_continuity_context

    async def continuity(*args, **kwargs):
        return {"context_hash": "retained-observatory", "before_chapter": 2}

    monkeypatch.setattr(
        planning_continuity_context, "build_planning_continuity_context", continuity
    )
    original = deepcopy(rig.project.metadata_json["outline_commercial_judge_checkpoints"])
    result = await _approve(rig, from_chapter=2, through_chapter=2)
    assert result["draft_eligible"] and len(rig.rebase_inputs) == 1
    request = rig.rebase_inputs[0]
    assert request.prompt_template == "continuation_outline_rebase"
    assert request.prompt_version == "source-aware-continuation.v5"
    assert request.project_id == rig.project.id and request.workflow_run_id is not None
    supplied = json.loads(request.user_prompt)
    contract = supplied["continuation_input"]["output_contract"]
    assert contract == ready._CONTINUATION_OUTPUT
    chapter = contract["chapters"][0]
    assert isinstance(chapter["title"], str) and chapter["title"]
    for key in ("scene_type", "time_label"):
        assert isinstance(chapter["scenes"][0][key], str) and chapter["scenes"][0][key]
    assert rig.project.metadata_json["outline_commercial_judge_checkpoints"] == original


def test_full_source_bound_title_and_scene_fields_are_preserved_without_fallback():
    raw = source()
    raw["chapters"][1]["title"] = "  The chart — keep this complete title and its final clause  "
    raw["chapters"][1]["scenes"][0]["time_label"] = "After the second tide; before midnight"
    before = deepcopy(raw)
    assert validate(raw) == before == raw


@pytest.mark.parametrize("field", ["title", "scene_type", "time_label"])
def test_missing_advertised_field_raises_real_typed_report_without_source_repair(field):
    raw = source()
    target = raw["chapters"][1]
    path = "/chapters/1"
    if field != "title":
        target = target["scenes"][0]
        path += "/scenes/0"
    del target[field]
    before = deepcopy(raw)
    with pytest.raises(planner.OutlineContractValidationError) as error:
        validate(raw)
    assert any(item["location"] == path + "/" + field for item in error.value.report["violations"])
    assert raw == before and field not in target


def test_v4_policy_differs_from_exact_retained_v3_contract_hash():
    old_output = deepcopy(ready._CONTINUATION_OUTPUT)
    chapter = old_output["chapters"][0]
    del chapter["title"]
    del chapter["scenes"][0]["scene_type"]
    del chapter["scenes"][0]["time_label"]
    old_policy = content_hash(
        {
            "version": "source-aware-continuation.v3",
            "system": ready._CONTINUATION_SYSTEM_V4,
            "output": old_output,
            "json_policy": ready._PREFIX_JSON_POLICY,
        }
    )
    assert old_policy == "c97e8c5fc2d6f3ebf2b0255d4ac47e94705a49ca55deea9b264a7eb54fe6a138"
    assert ready.CONTINUATION_PRODUCER_POLICY != old_policy


def test_v5_is_explicit_compact_transport_policy_without_changing_story_contract():
    assert ready._CONTINUATION_SYSTEM.startswith(ready._CONTINUATION_SYSTEM_V4)
    assert '不能复述完整前章' in ready._CONTINUATION_SYSTEM
    assert '身份、官阶、物件状态必须继承其末状态' in ready._CONTINUATION_SYSTEM
    old=content_hash({'version':'source-aware-continuation.v4','system':ready._CONTINUATION_SYSTEM_V4,'output':ready._CONTINUATION_OUTPUT,'json_policy':ready._PREFIX_JSON_POLICY})
    assert old != ready.CONTINUATION_PRODUCER_POLICY
