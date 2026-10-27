"""Default trope references cannot acquire authority over creation choices."""

# ruff: noqa: RUF001 — Chinese creation inputs are intentional.

from copy import deepcopy
import json
import random

import pytest
from test_concept_tournament import _CFG, GOOD_PAYLOAD, _finite_payload, _gen_from, _judge_scoring

from bestseller.services import concept_tournament as ct

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    from bestseller.services import llm

    calls = []

    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    yield
    assert not calls


async def tournament(*, references, mode="advisory", boundary=None, authoritative=False):
    prompts = []
    data = _finite_payload({**GOOD_PAYLOAD, "concept": "退婚打脸后，" + GOOD_PAYLOAD["concept"]})
    generator = _gen_from([data])

    async def retain_prompt(system, user):
        prompts.append((system, user))
        return await generator(system, user)

    cfg = {
        **deepcopy(_CFG),
        "cliche_seeds": {"generic": list(references)},
        "cliche_mode": mode,
        "cliche_penalty": 10000,
    }
    before = deepcopy(cfg)
    result = await ct.run_concept_tournament(
        None,
        None,
        genre="玄幻",
        sub_genre="玄幻",
        chapter_count=20,
        config=cfg,
        generator=retain_prompt,
        judge=_judge_scoring({"退婚打脸后": (9, 8, 2)}),
        rng=random.Random(7),
        seed_concept=data["concept"] if authoritative else "",
        authoritative_seed=authoritative,
        candidate_boundary_check=boundary,
    )
    assert cfg == before
    return result, prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["advisory", "eliminate", "penalize"])
async def test_actual_tournament_reference_hits_never_change_score_order_or_eligibility(mode):
    baseline, _ = await tournament(references=[])
    observed, prompts = await tournament(references=["退婚打脸"], mode=mode)
    assert baseline.winner is not None and observed.winner is not None
    assert [c.to_dict() for c in observed.candidates] == [c.to_dict() for c in baseline.candidates]
    assert observed.winner.to_dict() == baseline.winner.to_dict()
    assert observed.banned_cliches == ()
    assert observed.default_cliche_references == ("退婚打脸",)
    audits = [
        f for f in observed.quality_findings if f.get("finding") == "default_cliche_reference_match"
    ]
    assert audits
    for audit in audits:
        assert audit["matches"] == ["退婚打脸"]
        assert audit["authority"] == "default_reference" and audit["action"] == "advisory"
        assert not any(audit[k] for k in ("affects_scores", "affects_order", "affects_eligibility"))
    assert all("原创开局约束" not in s + u and "在世主角" not in s + u for s, u in prompts)


@pytest.mark.asyncio
async def test_explicit_native_motif_remains_eligible_against_default_library():
    result, prompts = await tournament(
        references=["退婚打脸"], mode="eliminate", authoritative=True
    )
    assert result.winner is not None and result.winner.rejected_reason is None
    assert result.winner.concept.startswith("退婚打脸后")
    assert any("退婚打脸后" in u for _, u in prompts)


@pytest.mark.asyncio
async def test_existing_explicit_boundary_still_rejects_default_library_match():
    calls = []

    def boundary(candidate):
        calls.append(candidate.concept)
        return ("USER_EXPLICIT_AVOID",)

    result, _ = await tournament(references=["退婚打脸"], boundary=boundary)
    assert calls and result.winner is None
    assert result.candidates
    assert all("USER_EXPLICIT_AVOID" in (c.rejected_reason or "") for c in result.candidates)


@pytest.mark.asyncio
async def test_existing_cross_book_boundary_is_not_weakened_by_reference_authority():
    result, _ = await tournament(
        references=[], boundary=lambda candidate: ("CROSS_BOOK_PROSE_COPY",)
    )
    assert result.winner is None
    assert all("CROSS_BOOK_PROSE_COPY" in (c.rejected_reason or "") for c in result.candidates)


def test_future_snapshot_retains_full_inventory_without_ban_label_or_mutating_old_data():
    old = {"banned_cliches": ["旧参考"], "candidates": [{"composite": 8.2}], "raw": "exact bytes"}
    frozen = deepcopy(old)
    references = ct.resolve_banned_cliches("玄幻", "玄幻")
    result = ct.ConceptTournamentResult(default_cliche_references=references)
    snapshot = result.to_dict()
    assert "banned_cliches" not in snapshot
    receipt = snapshot["default_cliche_references"]
    assert receipt["entries"] == list(references) and len(receipt["entries"]) == 21
    assert receipt["authority"] == "default_reference" and receipt["action"] == "advisory"
    assert old == frozen
    assert json.loads(json.dumps(snapshot, ensure_ascii=False)) == snapshot


def test_legacy_python_constructor_keeps_inventory_as_reference_without_prompt_authority():
    result = ct.ConceptTournamentResult(banned_cliches=("旧参考",))
    assert result.to_dict()["default_cliche_references"]["entries"] == ["旧参考"]
    assert ct.render_cliche_avoidance_block(result.banned_cliches) == ""
    result.winner = ct.ConceptCandidate(
        dimension="native", concept="原生母题", mechanism="选择的机制", hook_question="当下选择？"
    )
    block = ct.render_high_concept_block(result)
    assert "原生母题" in block and "选择的机制" in block
    assert "禁止回归题材默认套路" not in block and "禁用" not in block and "旧参考" not in block


def test_generator_and_hook_preserve_selected_material_without_reference_opening_constraints():
    _, engine = ct._build_engine_kernel_messages(
        genre="玄幻",
        sub_genre="玄幻",
        lane="成长道路",
        chapter_count=30,
        authoritative_seed_concept="作者选择签到变强。",
        banned=("签到变强",),
        creation_intent_block="明确用户要求：不得以调查账册作为主线。",
    )
    _, hook = ct._build_hook_from_engine_messages(
        genre="玄幻",
        sub_genre="玄幻",
        kernel={"protagonist_identity": "少年"},
        seed_concept="作者选择签到变强。",
        banned=("签到变强",),
    )
    for prompt in (engine, hook):
        assert "作者选择签到变强。" in prompt
        assert "原创开局约束" not in prompt and "俗套库将在生成后" not in prompt
        assert "在世主角" not in prompt
    assert "不得以调查账册作为主线" in engine


def test_config_inventory_unchanged_and_both_modes_are_advisory():
    cfg = ct.load_concept_tournament_config()
    assert cfg["cliche_mode"] == cfg["wild_mode"]["cliche_mode"] == "advisory"
    assert "cliche_penalty" not in cfg and "cliche_penalty" not in cfg["wild_mode"]
    assert "退婚打脸" in cfg["cliche_seeds"]["generic"]
    assert "扮猪吃虎打脸各路天骄" in cfg["cliche_seeds"]["xuanhuan"]
