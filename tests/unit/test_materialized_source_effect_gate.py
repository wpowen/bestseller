from uuid import UUID

import pytest

from bestseller.services import conception
from bestseller.services.concept_contract import ConceptContractError


def _payload(kind, direction, actor, receiver, action, effect, mechanism):
    modes = {
        "compel_or_execute": "causes_target_action",
        "observe_or_receive": "perceives_information",
    }
    return {
        "applicability": "applicable",
        "causal_mode": modes[kind],
        "mechanism_quote": mechanism,
    }


@pytest.mark.asyncio
async def test_materialized_gate_blocks_operation_to_observation_replacement(monkeypatch):
    async def fake_json(_session, _settings, *, template, **_kwargs):
        if template.endswith("source_effect_contract"):
            return _payload(
                "compel_or_execute",
                "actor_causes_receiver_action",
                "让",
                "死者",
                "完成动作",
                "完成动作",
                "让死者完成动作",
            ), [UUID("00000000-0000-4000-8000-000000000001")]
        if template.endswith("candidate_effect_contract"):
            return _payload(
                "observe_or_receive",
                "actor_observes_or_receives",
                "主角",
                "死者",
                "看到",
                "临终残影与遗言",
                "主角看到死者临终残影与遗言",
            ), [UUID("00000000-0000-4000-8000-000000000002")]
        return {}, []

    async def fake_judge(*_args, **_kwargs):
        return {"passed": True, "hard_conflicts": [], "repair_directives": []}, []

    async def fake_repair(*_args, **_kwargs):
        return {}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_json)
    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", fake_judge)
    monkeypatch.setattr(conception, "_repair_final_result_to_explicit_seed", fake_repair)

    with pytest.raises(ConceptContractError) as exc_info:
        await conception._enforce_materialized_story_seed_fidelity(
            None,
            None,
            concept_seed="让死者完成动作",
            title="测试书",
            premise="主角让死者完成动作",
            synopsis="主角看到死者临终残影与遗言",
            tags=[],
            writing_profile={"character": {}},
            story_spine={},
            world_model={},
            ctx={"language": "zh-CN"},
        )
    assert "FINAL_STORY_ARCHITECTURE_SEED_FIDELITY_UNRESOLVED" in str(exc_info.value)


@pytest.mark.asyncio
async def test_materialized_gate_stops_before_candidate_when_source_effect_is_unknown(monkeypatch):
    calls = []

    async def fake_json(_session, _settings, *, template, **_kwargs):
        calls.append(template)
        if template.endswith("source_effect_contract"):
            return {"applicability": "unknown", "operations": []}, [
                UUID("00000000-0000-4000-8000-000000000001")
            ]
        raise AssertionError("candidate extraction must not run for an unverified source")

    monkeypatch.setattr(conception, "_llm_call_json", fake_json)

    with pytest.raises(ConceptContractError, match="SOURCE_EFFECT_CONTRACT_UNVERIFIED") as caught:
        await conception._enforce_materialized_story_seed_fidelity(
            None,
            None,
            concept_seed="一个无法解析的构思",
            title="测试书",
            premise="主角开始调查",
            synopsis="主角继续调查",
            tags=[],
            writing_profile={"character": {}},
            story_spine={},
            world_model={},
            ctx={"language": "zh-CN"},
        )
    assert calls == ["conception_source_effect_contract"]
    assert len(caught.value.llm_run_ids) == 1
    assert caught.value.materialized_seed_fidelity_diagnostics["repair_attempts"] == []


@pytest.mark.asyncio
async def test_materialized_gate_allows_same_operation_with_additive_effect(monkeypatch):
    async def fake_json(_session, _settings, *, template, **_kwargs):
        if template.endswith("source_effect_contract"):
            return _payload(
                "observe_or_receive",
                "actor_observes_or_receives",
                "主角",
                "青铜镜",
                "触碰",
                "看见残影",
                "主角触碰青铜镜后看见残影",
            ), [UUID("00000000-0000-4000-8000-000000000001")]
        if template.endswith("candidate_effect_contract"):
            return _payload(
                "observe_or_receive",
                "actor_observes_or_receives",
                "主角",
                "青铜镜",
                "触碰",
                "看见残影并听见遗言",
                "主角触碰青铜镜后看见残影并听见遗言",
            ), [UUID("00000000-0000-4000-8000-000000000002")]
        return {}, []

    async def fake_judge(*_args, **_kwargs):
        return {"passed": True, "hard_conflicts": [], "repair_directives": []}, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_json)
    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", fake_judge)
    story, report, _ids = await conception._enforce_materialized_story_seed_fidelity(
        None,
        None,
        concept_seed="主角触碰青铜镜后看见残影",
        title="测试书",
        premise="主角触碰青铜镜后看见残影",
        synopsis="主角触碰青铜镜后看见残影并听见遗言",
        tags=[],
        writing_profile={"character": {}},
        story_spine={},
        world_model={},
        ctx={"language": "zh-CN"},
    )
    assert story["premise"]
    assert report["source_effect_contract"]["status"] == "pass"


@pytest.mark.asyncio
async def test_repaired_ordinary_story_allows_not_applicable_and_keeps_effect_report(monkeypatch):
    calls = {"judge": 0}

    async def fake_json(_session, _settings, *, template, **_kwargs):
        if template.endswith("effect_contract"):
            return {
                "applicability": "not_applicable",
                "applicability_reason": "现实叙事没有明确的核心能力或机制。",
                "operations": [],
            }, [
                UUID("00000000-0000-4000-8000-000000000001")
                if template.endswith("source_effect_contract")
                else UUID("00000000-0000-4000-8000-000000000002")
            ]
        return {}, []

    async def fake_judge(*_args, **_kwargs):
        assert _kwargs["fidelity_stage"] == "preproject_materialized"
        calls["judge"] += 1
        if calls["judge"] == 1:
            return {
                "passed": False,
                "hard_conflicts": [],
                "repair_directives": ["保留人物关系"],
            }, []
        return {"passed": True, "hard_conflicts": [], "repair_directives": []}, []

    async def fake_arbitration(*_args, **_kwargs):
        assert _kwargs["fidelity_stage"] == "preproject_materialized"
        return {"passed": False, "hard_conflicts": [], "repair_directives": ["保留人物关系"]}, []

    async def fake_repair(*_args, **_kwargs):
        return {
            "title": "修复后",
            "premise": "两人决定一起面对现实",
            "synopsis": "两人共同解决生活问题",
            "tags": ["现实"],
            "writing_profile": {
                "market": {"reader_promise": "见证两人修复关系"},
                "character": {"protagonist_core_drive": "修复误会"},
                "world": {"setting_tags": ["现代城市"]},
                "style": {"pov_type": "third-limited"},
                "serialization": {"opening_mandate": "先呈现误会后果"},
            },
            "story_spine": {
                "who": "被误会拆散的两个人",
                "wants": "在期限前澄清误会",
                "why_now": "工作变动迫使两人重逢",
                "against": "未解决的旧误会",
                "stakes": "关系与工作机会同时失去",
                "question": "他们能否在期限前重新信任彼此？",
            },
            "world_model": {"setting": "城市"},
        }, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_json)
    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", fake_judge)
    monkeypatch.setattr(conception, "_arbitrate_explicit_seed_fidelity_report", fake_arbitration)
    monkeypatch.setattr(conception, "_repair_final_result_to_explicit_seed", fake_repair)

    story, report, _ids = await conception._enforce_materialized_story_seed_fidelity(
        None,
        None,
        concept_seed="两人因为工作误会重新认识",
        title="原书",
        premise="两人因为工作误会重新认识",
        synopsis="两人共同解决生活问题",
        tags=["现实"],
        writing_profile={"character": {"protagonist": "甲"}},
        story_spine={"hook": "共同面对"},
        world_model={"setting": "城市"},
        ctx={"language": "zh-CN"},
    )

    assert story["title"] == "修复后"
    assert report["materialized_repaired"] is True
    assert report["source_effect_contract"]["status"] == "not_applicable"


@pytest.mark.asyncio
async def test_materialized_gate_failure_preserves_repair_candidates_reports_and_receipts(
    monkeypatch,
):
    async def fake_json(_session, _settings, *, template, **_kwargs):
        if template.endswith("source_effect_contract"):
            return _payload(
                "compel_or_execute",
                "actor_causes_receiver_action",
                "让",
                "死者",
                "完成动作",
                "完成动作",
                "让死者完成动作",
            ), [UUID("00000000-0000-4000-8000-000000000021")]
        if template.endswith("candidate_effect_contract"):
            return _payload(
                "observe_or_receive",
                "actor_observes_or_receives",
                "主角",
                "死者",
                "看到",
                "临终残影",
                "主角看到死者临终残影",
            ), [UUID("00000000-0000-4000-8000-000000000022")]
        return {}, []

    async def fake_judge(*_args, **_kwargs):
        return {
            "passed": False,
            "hard_conflicts": [
                {
                    "source": "source_effect_contract",
                    "reason": "能力方向被改写",
                }
            ],
            "repair_directives": ["保留能力方向"],
        }, [UUID("00000000-0000-4000-8000-000000000023")]

    repair_calls = 0

    async def fake_repair(*_args, **_kwargs):
        nonlocal repair_calls
        repair_calls += 1
        return {}, [UUID(f"00000000-0000-4000-8000-{30 + repair_calls:012d}")]

    monkeypatch.setattr(conception, "_llm_call_json", fake_json)
    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", fake_judge)
    monkeypatch.setattr(conception, "_repair_final_result_to_explicit_seed", fake_repair)

    with pytest.raises(ConceptContractError) as exc_info:
        await conception._enforce_materialized_story_seed_fidelity(
            None,
            None,
            concept_seed="让死者完成动作",
            title="测试书",
            premise="主角让死者完成动作",
            synopsis="主角看到死者临终残影",
            tags=[],
            writing_profile={"character": {}},
            story_spine={},
            world_model={},
            ctx={"language": "zh-CN"},
        )

    error = exc_info.value
    diagnostics = error.materialized_seed_fidelity_diagnostics
    assert error.violations
    assert diagnostics["initial_materialized_story"]["premise"] == "主角让死者完成动作"
    assert len(diagnostics["repair_attempts"]) == 2
    assert all(item["status"] == "incomplete_structure" for item in diagnostics["repair_attempts"])
    assert all(item["structure_errors"] for item in diagnostics["repair_attempts"])
    assert all(item["candidate"] == {} for item in diagnostics["repair_attempts"])
    assert diagnostics["repair_attempts"][0]["repair_ids"]
    assert str(UUID("00000000-0000-4000-8000-000000000021")) in diagnostics["llm_run_ids"]
    assert str(UUID("00000000-0000-4000-8000-000000000023")) in diagnostics["llm_run_ids"]
    assert str(UUID("00000000-0000-4000-8000-000000000031")) in diagnostics["llm_run_ids"]
    assert diagnostics["source_effect_contract"]["status"] == "conflict"


def test_materialized_gate_caller_attaches_diagnostics_to_conception_log():
    import inspect

    source = inspect.getsource(conception._review_conception_candidate)
    assert "isinstance(exc, ConceptContractError)" in source
    assert "list(exc.violations)" in source
    assert "materialized_seed_fidelity_diagnostics" in source
    assert '"status": "failed"' in source
