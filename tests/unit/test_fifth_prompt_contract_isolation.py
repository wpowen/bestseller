# ruff: noqa: RUF001
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit

AXES = (
    "seed_fidelity",
    "freshness",
    "click_seed",
    "action_conflict",
    "reader_promise",
    "character_choice",
    "scene_generation",
    "promise_survival",
    "deformable_loop",
    "post_reveal_engine",
    "genre_fidelity",
)
SEED = (
    "边城矿奴陆衡能看见废矿里的灵脉走向；宗门要封矿活埋工友，他当众烧毁卖身契，"
    "带人抢先凿穿唯一生路，却挖出了各宗争夺的古战场入口。"
)
CARD = {
    "protagonist_identity": "陆衡，边城赤阳矿场矿奴，天生能看到废矿中未成形灵脉的走向。",
    "core_abnormality": (
        "能看见废矿中尚未成形的灵脉走向，"
        "从而判断出哪里可能挖出灵石或通往隐秘空间。"
    ),
}


def test_batch_and_single_share_source_fidelity_contract_and_keep_eleven_axes():
    _single_system, single = ct._build_engine_judge_messages(
        kernel=CARD,
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        seed_concept=SEED,
        authoritative_seed_concept=SEED,
    )
    _batch_system, batch = ct._build_engine_batch_judge_messages(
        cards=[("lane", SEED, CARD)],
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        authoritative_seed_concept=SEED,
    )
    contract = ct._render_engine_source_fidelity_contract(SEED)
    assert contract in single
    assert contract in batch
    assert "supplement（补足）" in single and "replacement（替换）" in single
    assert "supplement（补足）" in batch and "replacement（替换）" in batch
    for prompt in (single, batch):
        assert "任一核心事实" in prompt
        assert "seed_fidelity不得超过4" in prompt
        assert "用户原文未命名时必须为null" in prompt
        assert "无法核对时为unknown" in prompt
    for axis in AXES:
        assert f'"{axis}"' in single
        assert f'"{axis}"' in batch
    assert '"source_change_type":"supplement|replacement|none|unknown"' in single
    assert '"source_change_type":"supplement|replacement|none|unknown"' in batch


def test_disagreement_is_audit_evidence_and_never_a_confirmed_defect():
    initial = dict.fromkeys(AXES, 8)
    recheck = dict(initial)
    recheck.update({"seed_fidelity": 4, "freshness": 6, "promise_survival": 4})
    record = ct._engine_review_disagreement(
        initial,
        recheck,
        engine_axes=AXES,
        engine_floor=7,
        initial_review_mode="batch",
        recheck_review_mode="single",
    )
    assert record["kind"] == "review_context_disagreement"
    assert record["context_changed"] is True
    assert set(record["changed_axes"]) == {"seed_fidelity", "freshness", "promise_survival"}
    assert record["confirmed_defect"] is False
    assert record["gate_decision"] == "existing_single_axis_evidence_gate"
    assert record["initial_failed_axes"] == []
    assert set(record["recheck_failed_axes"]) == {"seed_fidelity", "freshness", "promise_survival"}


def test_fifth_fixture_locks_same_card_and_batch_single_context_difference():
    fixture = Path(__file__).parents[1] / "fixtures" / "fifth-narrow-recheck.json"
    data = json.loads(fixture.read_text())
    narrow = data["narrow_revision"]
    assert narrow["original_premise_review"]["review_mode"] == "batch"
    assert narrow["recheck_run_id"]
    assert narrow["recheck_input_hash"] == narrow["original_card_hash"]
    assert narrow["recheck_failed_axes"] == [
        "seed_fidelity",
        "freshness",
        "action_conflict",
        "reader_promise",
        "scene_generation",
        "promise_survival",
        "deformable_loop",
        "post_reveal_engine",
    ]
    assert "editor_run_id" not in narrow
    assert "rejudge_run_id" not in narrow
    raw = json.loads(
        narrow["recheck_raw"].strip().removeprefix("```json").removesuffix("```").strip()
    )
    assert raw["seed_fidelity"] == 4
    assert narrow["original_premise_review"]["seed_fidelity"] == 9


@pytest.mark.asyncio
async def test_disagreement_does_not_bypass_single_axis_repair_gate():
    card = {"difference_point": "机制只在矿道中显现"}
    initial = dict.fromkeys(AXES, 8)
    initial["freshness"] = 6
    recheck = dict.fromkeys(AXES, 6)

    async def judge(system, user):
        assert "单卡商业复核" in system
        return json.dumps(recheck, ensure_ascii=False), "00000000-0000-4000-8000-000000000901"

    async def editor(system, user):  # pragma: no cover - must never be called
        raise AssertionError("disagreement must not bypass the gate")

    revised, status = await ct._attempt_narrow_commercial_repair(
        card=card,
        dimension="lane",
        premise_seed=SEED,
        initial_verdict=initial,
        failed_axes=["freshness"],
        authoritative_seed_concept="",
        genre="玄幻",
        sub_genre="东方玄幻",
        chapter_count=20,
        intent_axes=(),
        engine_axes=AXES,
        engine_floor=7,
        premise_judge_fn=judge,
        gen_fn=editor,
        result=ct.ConceptTournamentResult(),
        initial_review_mode="batch",
    )
    assert revised is None
    assert status["disagreement"]["confirmed_defect"] is False
    assert status["disagreement"]["changed_axes"]
    assert status["reason"] == "multiple_failed_axes"
