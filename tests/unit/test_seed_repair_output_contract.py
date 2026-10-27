import pytest

from bestseller.services import conception


@pytest.mark.asyncio
@pytest.mark.parametrize("world_state", ["legacy", "empty", "absent"])
async def test_source_only_repair_declares_required_shape_without_rejected_content(
    monkeypatch, world_state
):
    captured = {}

    async def fake_call(*args, **kwargs):
        captured.update(kwargs)
        return {}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    rejected = {"premise": "REJECTED_STORY_MARKER"}
    if world_state == "legacy":
        rejected["world_model"] = {"laws": ["REJECTED_WORLD_MARKER"]}
    elif world_state == "empty":
        rejected["world_model"] = {}
    await conception._repair_final_result_to_explicit_seed(
        None, None, concept_seed="纪白让死者完成一个动作。每具一次。",
        final_result=rejected, report={"hard_conflicts": []}, ctx={},
    )
    prompt = captured["user_prompt"]
    assert "必须输出的字段与类型" in prompt
    assert '"writing_profile"' in prompt
    for section in ("market", "character", "world", "style", "serialization"):
        assert section in prompt
    for field in ("who", "wants", "why_now", "against", "stakes", "question"):
        assert field in prompt
    if world_state == "legacy":
        assert "保留现有 legacy world_model 结构" in prompt
    elif world_state == "empty":
        assert "正式 WorldModel 尚未编译" in prompt
        assert "writing_profile.world" in prompt
    else:
        assert "world_model" not in prompt
    assert "输入中若有" not in prompt
    assert "REJECTED_STORY_MARKER" not in prompt
    assert "REJECTED_WORLD_MARKER" not in prompt
