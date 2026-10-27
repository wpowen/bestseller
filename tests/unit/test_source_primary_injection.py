import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from bestseller.services import concept_tournament
from bestseller.services.source_effect_contract import (
    normalize_source_effect_contract,
    render_source_effect_generation_block,
    source_effect_contract_matches_source,
)

_ROOT = Path(__file__).parents[2]
_PROBE = _ROOT / "tests/fixtures/engine-source-direction-real-probe.json"


def _probe_contract():
    probe = json.loads(_PROBE.read_text())
    text = probe["source"]["text"]
    payload = {
        "applicability": "applicable",
        "mechanism_quote": probe["source"]["raw"]["mechanism_quote"],
        "causal_mode": probe["source"]["raw"]["causal_mode"],
    }
    return text, normalize_source_effect_contract(
        payload,
        source_text=text,
        origin="source",
        llm_run_id=UUID(probe["source"]["receipt"]),
    )


def test_real_probe_primary_block_matches_recorded_experiment():
    probe = json.loads(_PROBE.read_text())
    text, contract = _probe_contract()
    assert contract["known"] is True
    assert source_effect_contract_matches_source(contract, text)
    expected = probe["expected_generation_block"]
    assert render_source_effect_generation_block(contract, source_text=text) == expected


@pytest.mark.parametrize(
    "mutator",
    [
        lambda c: c.update(text_hash="0" * 64),
        lambda c: c.update(mechanism_quote="不存在的连续原文"),
        lambda c: c.update(origin="candidate"),
        lambda c: c.update(llm_run_id="00000000-0000-0000-0000-000000000000"),
        lambda c: c.update(contract_id="00000000-0000-0000-0000-000000000000"),
    ],
)
def test_primary_source_contract_validation_is_bound_to_text_and_receipts(mutator):
    text, contract = _probe_contract()
    mutator(contract)
    assert not source_effect_contract_matches_source(contract, text)
    assert render_source_effect_generation_block(contract, source_text=text) == ""


def test_unknown_or_not_applicable_contract_does_not_inject():
    text = "她在雨夜离开旧城。"
    unknown = normalize_source_effect_contract(
        {"applicability": "unknown"},
        source_text=text,
        origin="source",
        llm_run_id=UUID("00000000-0000-4000-8000-000000000001"),
    )
    ordinary = normalize_source_effect_contract(
        {
            "applicability": "not_applicable",
            "applicability_reason": "普通现实叙事且没有明确核心能力",
            "causal_mode": "unknown",
            "mechanism_quote": "",
        },
        source_text=text,
        origin="source",
        llm_run_id=UUID("00000000-0000-4000-8000-000000000001"),
    )
    assert not source_effect_contract_matches_source(unknown, text)
    assert source_effect_contract_matches_source(ordinary, text)
    assert render_source_effect_generation_block(unknown, source_text=text) == ""
    assert render_source_effect_generation_block(ordinary, source_text=text) == ""


async def _capture_engine_calls(monkeypatch, *, authoritative: bool):
    captured = []
    original_engine = concept_tournament._build_engine_kernel_messages
    original_repair = concept_tournament._build_engine_kernel_repair_messages

    def capture_engine(**kwargs):
        captured.append(("engine", kwargs))
        return original_engine(**kwargs)

    def capture_repair(**kwargs):
        captured.append(("repair", kwargs))
        return original_repair(**kwargs)

    monkeypatch.setattr(concept_tournament, "_build_engine_kernel_messages", capture_engine)
    monkeypatch.setattr(concept_tournament, "_build_engine_kernel_repair_messages", capture_repair)
    monkeypatch.setattr(
        concept_tournament,
        "_parse_complete_axis_scores",
        lambda _verdict, axes: (dict.fromkeys(axes, 9.0), []),
    )
    monkeypatch.setattr(
        concept_tournament,
        "_validate_engine_seed_fidelity_evidence",
        lambda *_args, **_kwargs: {"status": "verified"},
    )
    calls = 0

    async def fake_generator(_system, user):
        nonlocal calls
        calls += 1
        if calls <= 3:
            return (
                json.dumps(
                    {
                        "ideas": [
                            {
                                "lane": "人际困局",
                                "seed": "主角带孩子逃亡",
                                "graft": "未审提炼",
                                "opening": "未审开场",
                                "why_it_keeps_moving": "未审持续",
                                "future_situations": ["未审未来"],
                            },
                            {
                                "lane": "世界规则",
                                "seed": "主角带孩子逃亡2",
                                "graft": "未审提炼2",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                UUID(f"00000000-0000-4000-8000-{calls:012d}"),
            )
        # Malformed card forces the real repair builder to be called; raising
        # afterwards keeps the test bounded and avoids pretending a card passed.
        return "{}", UUID(f"00000000-0000-4000-8000-{calls:012d}")

    async def fake_premise_judge(_system, _user):
        return "{}", UUID("00000000-0000-4000-8000-000000000099")

    result = await concept_tournament.run_concept_tournament(
        None,
        None,
        genre="东方玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        config={
            "enabled": True,
            "candidate_prompt_mode": "engine_first",
            "n_candidates": 2,
            "raw_idea_pool_multiplier": 1,
            "raw_idea_generation_batch_size": 2,
            "raw_idea_rank_enabled": False,
        },
        generator=fake_generator,
        premise_judge=fake_premise_judge,
        seed_concept=(
            "收尸人纪白能让死者完成临死时被打断的一个动作" if authoritative else "探索种子"
        ),
        authoritative_seed=authoritative,
        primary_effect_contract=_probe_contract()[1] if authoritative else None,
    )
    return result, captured


def test_authoritative_run_drops_unreviewed_support_from_engine_and_repair(monkeypatch):
    _result, captured = __import__("asyncio").run(
        _capture_engine_calls(monkeypatch, authoritative=True)
    )
    assert {kind for kind, _kwargs in captured} >= {"engine", "repair"}
    assert all(kwargs["seed_support"] is None for _kind, kwargs in captured)


def test_non_authoritative_run_keeps_exploration_support(monkeypatch):
    _result, captured = __import__("asyncio").run(
        _capture_engine_calls(monkeypatch, authoritative=False)
    )
    assert captured, _result.to_dict()
    assert {kind for kind, _kwargs in captured} >= {"engine", "repair"}
    assert any(kwargs["seed_support"] for _kind, kwargs in captured)


async def _materialized_gate_with_cached_source(monkeypatch, cached):
    from bestseller.services import conception

    calls = []

    async def fake_llm(_session, _settings, **kwargs):
        calls.append(kwargs["stage"])
        return (
            {
                "applicability": "applicable",
                "mechanism_quote": "核心动作",
                "causal_mode": "causes_target_action",
            },
            [UUID(f"00000000-0000-4000-8000-{20 + len(calls):012d}")],
        )

    async def fake_judge(*_args, **_kwargs):
        return {"passed": True}, [UUID("00000000-0000-4000-8000-000000000013")]

    monkeypatch.setattr(conception, "_llm_call_json", fake_llm)
    monkeypatch.setattr(conception, "_judge_explicit_concept_seed_fidelity", fake_judge)
    await conception._enforce_materialized_story_seed_fidelity(
        None,
        None,
        concept_seed="主角能完成核心动作",
        title="标题",
        premise="主角能完成核心动作",
        synopsis="主角能完成核心动作",
        tags=[],
        writing_profile={},
        story_spine={},
        world_model={},
        ctx={},
        authoritative_source_effect_contract=cached,
    )
    return calls


@pytest.mark.asyncio
async def test_late_gate_reuses_only_same_source_hash(monkeypatch):
    text = "主角能完成核心动作"
    cached = normalize_source_effect_contract(
        {
            "applicability": "applicable",
            "mechanism_quote": "核心动作",
            "causal_mode": "causes_target_action",
        },
        source_text=text,
        origin="source",
        llm_run_id=UUID("00000000-0000-4000-8000-000000000011"),
    )
    calls = await _materialized_gate_with_cached_source(monkeypatch, cached)
    assert "conception.source_effect_contract" not in calls


@pytest.mark.asyncio
async def test_late_gate_reextracts_when_cached_source_hash_mismatches(monkeypatch):
    cached = normalize_source_effect_contract(
        {
            "applicability": "applicable",
            "mechanism_quote": "核心动作",
            "causal_mode": "causes_target_action",
        },
        source_text="主角能完成核心动作",
        origin="source",
        llm_run_id=UUID("00000000-0000-4000-8000-000000000011"),
    )
    cached["text_hash"] = hashlib.sha256("不同原文".encode()).hexdigest()
    calls = await _materialized_gate_with_cached_source(monkeypatch, cached)
    assert "conception.source_effect_contract" in calls
