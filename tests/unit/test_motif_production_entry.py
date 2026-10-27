"""Production-entry regression coverage for advisory motif/commercial findings.

The replay routes through ``run_concept_tournament`` itself.  It deliberately
does not call the lexical helpers directly: raw ranking, premise review, hook
review, serialization, market collision, and creation-intent enforcement must
remain connected at the production entry point.
"""

from __future__ import annotations

from copy import deepcopy

# ruff: noqa: RUF001 -- Chinese frozen-receipt fixture values are intentional.
import json
from pathlib import Path
from typing import Any

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit

_FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures/framework_motif/b1-production-entry-replay.json"
)
_CARD_PATH = Path(__file__).parents[1] / "fixtures/framework_motif/b1-sweeper-card.json"
def _replay_fixture() -> dict[str, Any]:
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["fixture_contract"]["scores_and_hooks"].startswith("synthetic")
    return fixture


def _complete_engine_verdict(index: int) -> dict[str, Any]:
    # Synthetic evaluator response: every production axis passes.  This replay
    # isolates card lexical policy; it does not claim a live-model score.
    return {
        "index": index,
        "seed_fidelity": 8,
        "freshness": 8,
        "click_seed": 8,
        "action_conflict": 8,
        "reader_promise": 8,
        "character_choice": 8,
        "scene_generation": 8,
        "promise_survival": 8,
        "deformable_loop": 8,
        "post_reveal_engine": 8,
        "genre_fidelity": 8,
        "reason": "synthetic all-axis pass",
    }


def _hook_verdict() -> str:
    return json.dumps(
        {
            "freshness": 8,
            "click": 8,
            "predictable": 2,
            "character_logic": 8,
            "mechanism_causality": 8,
            "genre_fidelity": 8,
            "plain_language": 8,
            "story_motion": 8,
            "protagonist_agency": 8,
            "reason": "synthetic all-axis pass",
        },
        ensure_ascii=False,
    )


def _production_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "candidate_prompt_mode": "engine_first",
        "commercial_gate_mode": "strict",
        "judge_model_key": "frozen",
        "raw_idea_judge_model_key": "frozen",
        "premise_judge_model_key": "frozen",
        "finalist_judge_model_key": "frozen",
        "raw_idea_prompt_arm": "author_pitch",
        "n_candidates": 4,
        "raw_idea_pool_multiplier": 1,
        "raw_idea_pool_topup_calls": 0,
        "raw_idea_rank_batch_size": 4,
        "premise_card_count": 4,
        "engine_judge_floor": 7,
        "story_layer_judge_enabled": False,
        "story_architecture_contract_enabled": False,
        "seriality_advisory_min_chapters": 8,
        "finalist_pool_size": 4,
        "seriality_finalist_count": 4,
        "winner_min": 5.5,
        "cliche_seeds": {"generic": []},
        "judge_weights": {"freshness": 0.4, "click": 0.4, "unpredictability": 0.2},
        "judge_hard_floors": {
            "catastrophe_floor": 2,
            "soft_miss_allowance": 0,
            "freshness": 7,
            "click": 7,
            "predictable_max": 6,
            "character_logic": 7,
            "mechanism_causality": 7,
            "genre_fidelity": 7,
            "plain_language": 7,
            "story_motion": 7,
        },
    }


def _complete_frozen_card(projection: dict[str, Any]) -> dict[str, Any]:
    """Use real P1 card material plus neutral fields required by the card schema.

    The fixture freezes seven material fields from each cited P1 engine-card
    response.  The remaining fields only make the deterministic completeness
    gate executable; they are deliberately neutral and never enter the motif
    finding being tested.
    """

    title = str(projection["protagonist_identity"]).split("，", 1)[0]
    return {
        **{
            key: projection[key]
            for key in (
                "protagonist_identity",
                "core_abnormality",
                "reader_promise",
                "difference_point",
                "deformable_loop",
                "opening_crisis",
                "emotional_promise",
            )
        },
        "protagonist_private_desire": f"{title}要守住自己作出选择的资格",
        "protagonist_flaw": "过分相信熟悉的做法，容易错过新的同盟",
        "current_goal": f"{title}必须在眼前冲突中守住主动权",
        "effective_resistance": "两股各有目标的势力都在逼主角先表态",
        "failure_cost": "主角会失去现有行动入口并暴露关键弱点",
        "success_cost": "主角必须承担一次公开站队带来的后果",
        "irreversible_change": "这次选择会改变主角下一轮能动用的人和资源",
        "scene_seeds": [f"{title}在场面{i}作出不同选择" for i in range(1, 6)],
        "post_reveal_scene_seeds": [f"{title}在揭晓后场面{i}继续兑现承诺" for i in range(1, 4)],
        "expansion_axes": ["关系变化", "资源变化", "对手学习"],
        "opposing_ecology": ["近处对手", "远处对手"],
    }


def _legacy_p1_demote(
    approved: list[tuple[str, str]],
    *,
    prebuilt_kernels: dict[tuple[str, str], dict[str, Any]],
    seed_concept: str,
    result: ct.ConceptTournamentResult,
) -> list[tuple[str, str]]:
    """Frozen pre-fix reducer from HEAD, isolated as a baseline control."""

    if not approved or ct.default_debt_family_hits(str(seed_concept or "")):
        return approved
    dominated: list[tuple[str, str]] = []
    clean: list[tuple[str, str]] = []
    for key in approved:
        kernel = prebuilt_kernels.get(key)
        is_dominated = isinstance(kernel, dict) and ct.is_debt_dominated(
            ct._flatten_kernel_text(kernel)
        )
        (dominated if is_dominated else clean).append(key)
    if not dominated or not clean:
        return approved
    result.engine_rejections.extend(
        {
            "dimension": dimension,
            "scores": {},
            "reason": "legacy default-family demotion baseline",
            "failed_axes": ["default_family_after_expansion"],
        }
        for dimension, _seed in dominated
    )
    return clean


async def _run_frozen_production_entry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    market_competitors: tuple[dict[str, Any], ...] = (),
    tone_preference: str = "",
    raw_pitches: list[dict[str, Any]] | None = None,
    use_legacy_demoter: bool = False,
) -> ct.ConceptTournamentResult:
    fixture = _replay_fixture()
    pitches = deepcopy(raw_pitches or fixture["raw_pitches"])
    card_projections = deepcopy(fixture["card_projections"])
    assert [item["attempt"] for item in card_projections] == [
        "000020",
        "000025",
        "000030",
        "000035",
    ]
    frozen_cards = [_complete_frozen_card(item) for item in card_projections]
    frozen_cards[2] = json.loads(_CARD_PATH.read_text(encoding="utf-8"))
    assert [
        ct.is_debt_dominated(ct._flatten_kernel_text(card)) for card in frozen_cards
    ] == [False, True, True, False]
    kernel_calls = 0

    def _ranked_payload() -> str:
        ranked = []
        for index, pitch in enumerate(pitches):
            seed = pitch["seed"]
            ranked.append(
                {
                    "index": index,
                    "freshness": 8,
                    "click_seed": 8,
                    "character_logic": 8,
                    "action_seed": 8,
                    "promise_survival": 8,
                    "genre_fidelity": 8,
                    "ai_assembly": 0,
                    "dumb_cost": False,
                    "domain": f"域{index}",
                    "after_opening_promise": pitch["why_it_keeps_moving"],
                    "action_families": ["行动甲", "行动乙", "行动丙"],
                    "growth_surface": "可验证的关系与资源积累",
                    "causal_evidence": [
                        {
                            "source_quote": seed,
                            "assumed_link": "主角会介入冲突",
                            "simpler_alternative": "不介入会失去行动入口",
                            "motivation_justification": "开篇压力要求主角选择",
                            "verified": True,
                        }
                    ],
                }
            )
        return json.dumps({"ranked": ranked}, ensure_ascii=False)

    async def _default_generator(*_args: Any, template: str, **_kwargs: Any):
        nonlocal kernel_calls

        async def _call(_system: str, user: str) -> tuple[str, str]:
            nonlocal kernel_calls
            if template == "concept_tournament_raw_idea_pool":
                return json.dumps({"ideas": pitches}, ensure_ascii=False), "frozen-raw-pool"
            if template == "concept_tournament_raw_idea_rank":
                return _ranked_payload(), "frozen-raw-rank"
            if template == "concept_tournament_engine_kernel":
                assert "PREMISE_CARD" in user
                card = frozen_cards[kernel_calls]
                kernel_calls += 1
                return json.dumps(card, ensure_ascii=False), f"frozen-card-{kernel_calls}"
            if template == "concept_tournament_candidate":
                return json.dumps(
                    {
                        "hooks": [
                            {
                                "angle": "promise",
                                "concept": (
                                    "孟秋用扫地换来剑修筹码，却必须躲过门派对异常剑感的追查。"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                ), "frozen-hook"
            if template == "concept_tournament_premise_judge":
                assert "候选=" in user
                return json.dumps(
                    {
                        "verdicts": [
                            _complete_engine_verdict(index) for index in range(len(pitches))
                        ]
                    },
                    ensure_ascii=False,
                ), "frozen-premise-review"
            if template in {"concept_tournament_judge", "concept_tournament_finalist_judge"}:
                return _hook_verdict(), f"frozen-{template}"
            return "{}", f"unused-{template}"

        return _call

    monkeypatch.setattr(ct, "_default_generator", _default_generator)
    if use_legacy_demoter:
        monkeypatch.setattr(ct, "_demote_default_family_cards", _legacy_p1_demote)
    result = await ct.run_concept_tournament(
        None,
        None,
        genre="古典仙侠",
        sub_genre="古典仙侠",
        chapter_count=1,
        config=_production_config(),
        market_competitors=market_competitors,
        tone_preference=tone_preference,
    )
    return result


@pytest.mark.asyncio
async def test_frozen_p1_replay_keeps_all_four_cards_and_serializes_lexical_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_frozen_production_entry(monkeypatch)

    assert len(result.premise_cards) == 4
    assert len({card["dimension"] for card in result.premise_cards}) == 4
    assert not result.engine_rejections
    assert any(
        finding["stage"] == "premise_card"
        and finding["finding"] == "default_family_lexical_signal"
        and finding["policy"] == "advisory_only"
        for finding in result.quality_findings
    )
    serialized = result.to_dict()
    assert serialized["quality_findings"] == result.quality_findings
    assert len(serialized["quality_findings"]) == 2


@pytest.mark.asyncio
async def test_legacy_card_demoter_would_have_sent_only_two_cards_downstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_frozen_production_entry(monkeypatch, use_legacy_demoter=True)

    # The old reducer ran after all four cards were built, so premise_cards is
    # still four.  The regression is the next production stage: only the two
    # clean card dimensions reach hook generation and judging.
    assert len(result.premise_cards) == 4
    downstream_dimensions = {
        candidate.dimension.split(":", 1)[0] for candidate in result.candidates
    }
    assert len(downstream_dimensions) == 2
    assert sum(
        rejection["failed_axes"] == ["default_family_after_expansion"]
        for rejection in result.engine_rejections
    ) == 2


@pytest.mark.asyncio
async def test_production_entry_still_records_real_market_collision_without_dropping_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _replay_fixture()
    first_pitch = fixture["raw_pitches"][0]
    result = await _run_frozen_production_entry(
        monkeypatch,
        market_competitors=(
            {
                "title": "已在榜的修真快递",
                "intro": " ".join(
                    [
                        first_pitch["seed"],
                        first_pitch["graft"],
                        first_pitch["opening"],
                        first_pitch["why_it_keeps_moving"],
                    ]
                ),
                "tags": ["修真", "快递"],
            },
        ),
    )

    assert len(result.premise_cards) == 4
    assert any(
        rejection["failed_axes"] == ["market_collision"]
        for rejection in result.engine_rejections
    )


@pytest.mark.asyncio
async def test_production_entry_keeps_creation_intent_as_a_hard_raw_pool_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _replay_fixture()
    coercive = deepcopy(fixture["raw_pitches"])
    coercive[-1]["graft"] = "敌人扣住妹妹做人质，逼他今夜交出剑谱，否则天亮沉河。"
    coercive[-1]["opening"] = "妹妹被扣做人质，敌人说天亮前不交剑谱就沉河。"

    result = await _run_frozen_production_entry(
        monkeypatch,
        tone_preference="light",
        raw_pitches=coercive,
    )

    assert result.premise_cards == []
    assert any(
        rejection["failed_axes"] == ["creation_intent_pollution"]
        and "轻松调性" in rejection["reason"]
        for rejection in result.engine_rejections
    ), result.engine_rejections
