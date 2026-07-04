"""L1 unit tests for the pairwise story/blurb appeal arena (vs real competitors).

The LLM judge is faked here (covered live in the validation script). These pin
the deterministic arena mechanics: verdict mapping, swap-consistency,
win-rate aggregation, reference resolution, and end-to-end win-rate with a
content-aware fake judge (candidate-always-wins → 1.0; position-biased → 0.5).
"""

from __future__ import annotations

# ruff: noqa: RUF003 — Chinese test fixtures.
import pytest

from bestseller.services.premise_appeal_arena import (
    _swap_consistent,
    load_reference_blurbs,
    parse_appeal_verdict,
    resolve_reference_set,
    run_appeal_arena,
    summarize_appeal,
)

_CANDIDATE_MARK = "★候选★"


@pytest.mark.unit
@pytest.mark.parametrize(
    "winner,candidate_is_a,expected",
    [
        ("甲", True, "win"),
        ("甲", False, "loss"),
        ("乙", True, "loss"),
        ("乙", False, "win"),
        ("持平", True, "tie"),
    ],
)
def test_parse_verdict_maps_token_to_candidate_outcome(winner, candidate_is_a, expected):
    raw = f'{{"winner": "{winner}", "reason": "x"}}'
    assert parse_appeal_verdict(raw, candidate_is_a=candidate_is_a) == expected


@pytest.mark.unit
def test_parse_verdict_unparseable_returns_none():
    assert parse_appeal_verdict("garbage no json", candidate_is_a=True) is None


@pytest.mark.unit
def test_fair_length_truncates_long_candidate_at_sentence_boundary():
    from bestseller.services.premise_appeal_arena import _fair_length

    short = "短简介。"
    assert _fair_length(short, 220) == short  # 不动
    long = "这是第那么一个小句子。" * 40  # 句界遍布、远超 220 字
    out = _fair_length(long, 220)
    assert len(out) <= 220
    assert out.endswith("。")  # 在后半段句界截断，保持完整句


@pytest.mark.unit
def test_swap_consistent_only_consistent_wins_count():
    assert _swap_consistent("win", "win") == "win"
    assert _swap_consistent("loss", "loss") == "loss"
    assert _swap_consistent("win", "loss") == "tie"   # position bias → neutralized
    assert _swap_consistent("win", "tie") == "tie"


@pytest.mark.unit
def test_reference_set_known_genre_and_generic_fallback():
    refs = load_reference_blurbs()
    assert "xuanhuan" in refs and len(refs["xuanhuan"]) >= 2
    # unknown genre → topped up from the generic pool (non-empty)
    fallback = resolve_reference_set("完全没有的题材zzz", None, min_refs=3)
    assert len(fallback) >= 3


# ── 频道感知补池 + 题材错标修复（审计 P1-7）────────────────────────────────
# 此前补池写死男频/中性题材键：女频候选参照不足时被混入斗破苍穹级男频简介，
# 且判官 prompt 把跨题材参照错标成候选题材。


@pytest.mark.unit
def test_reference_set_each_ref_carries_source_genre():
    refs = resolve_reference_set("玄幻", None, min_refs=3)
    assert refs
    assert all(r.get("genre") for r in refs)
    # own-genre refs resolve to the candidate's canonical key
    assert refs[0]["genre"] == "xuanhuan"


@pytest.mark.unit
def test_config_now_has_female_reference_pool():
    refs = load_reference_blurbs()
    female_keys = [
        k for k in ("gu-yan", "xian-yan", "fantasy-romance", "female-growth", "pure-love")
        if refs.get(k)
    ]
    assert len(female_keys) >= 3, "女频参照集缺失：arena 无法给女频候选同频道参照"
    total = sum(len(refs[k]) for k in female_keys)
    assert total >= 4, "女频参照总数须 ≥ arena min_refs(4)"


@pytest.mark.unit
def test_female_candidate_topup_never_mixes_male_channel():
    from bestseller.services.genre_taxonomy import get_genre

    # min_refs 拉高强制深度补池——补进来的每一条都必须非男频。
    got = resolve_reference_set("现代言情", None, min_refs=12)
    assert len(got) >= 4
    for r in got:
        g = get_genre(r["genre"])
        assert g is None or "male" not in g.channel, f"女频候选混入男频参照: {r['genre']}"


@pytest.mark.unit
def test_male_candidate_topup_never_mixes_female_channel():
    from bestseller.services.genre_taxonomy import get_genre

    got = resolve_reference_set("玄幻", None, min_refs=12)
    assert len(got) >= 4
    for r in got:
        g = get_genre(r["genre"])
        assert g is None or "female" not in g.channel, f"男频候选混入女频参照: {r['genre']}"


@pytest.mark.unit
def test_cross_genre_prompt_does_not_assert_candidate_genre():
    from bestseller.services.premise_appeal_arena import build_appeal_user_prompt

    same = build_appeal_user_prompt("a", "b", genre="玄幻")
    assert "玄幻" in same
    cross = build_appeal_user_prompt("a", "b", genre="玄幻", cross_genre=True)
    assert "玄幻" not in cross  # 跨题材参照不得被错标成候选题材
    assert "不限" in cross


@pytest.mark.unit
async def test_arena_cross_genre_pairs_use_unlabeled_prompt():
    prompts: list[str] = []

    async def _judge(system, user):
        prompts.append(user)
        return '{"winner": "持平"}'

    # 纯爱自有参照少 → 必然发生同频道补池；补池对局的 prompt 不得写「题材：纯爱」。
    s = await run_appeal_arena(
        candidate_blurb="她在旧书店捡到一封没寄出的信。", genre="纯爱",
        judge=_judge, min_refs=6, max_refs=6,
    )
    assert s.pairs >= 3
    cross_prompts = [p for p in prompts if "不限" in p]
    assert cross_prompts, "补池跨题材对局必须用『题材：不限』的判官 prompt"


@pytest.mark.unit
async def test_arena_details_carry_reference_genre():
    async def _judge(system, user):
        return '{"winner": "持平"}'

    s = await run_appeal_arena(
        candidate_blurb="x", genre="玄幻", judge=_judge, min_refs=3, max_refs=4,
    )
    assert all("ref_genre" in d for d in s.details)


@pytest.mark.unit
def test_summarize_winrate_math():
    from bestseller.services.premise_appeal_arena import AppealArenaPair, AppealMatchResult

    def _r(outcome):
        p = AppealArenaPair(pair_id="x", candidate_blurb="c", reference_blurb="r", genre="玄幻")
        return AppealMatchResult(pair=p, outcome=outcome)

    summary = summarize_appeal([_r("win"), _r("win"), _r("tie"), _r("loss")], genre="玄幻")
    assert summary.pairs == 4
    assert summary.wins == 2 and summary.losses == 1 and summary.ties == 1
    # (2 + 0.5*1) / 4 = 0.625
    assert summary.win_rate == pytest.approx(0.625)


async def _candidate_wins_judge(system, user):
    # The candidate (marked) always wins, whichever slot it is in.
    a_section = user.split("【简介·乙】")[0]
    cand_in_a = _CANDIDATE_MARK in a_section
    return '{"winner": "甲"}' if cand_in_a else '{"winner": "乙"}'


async def _candidate_loses_judge(system, user):
    a_section = user.split("【简介·乙】")[0]
    cand_in_a = _CANDIDATE_MARK in a_section
    return '{"winner": "乙"}' if cand_in_a else '{"winner": "甲"}'


async def _position_biased_judge(system, user):
    return '{"winner": "甲"}'  # always slot A → should neutralize to ties


@pytest.mark.unit
async def test_arena_candidate_always_wins_gives_winrate_1():
    s = await run_appeal_arena(
        candidate_blurb=f"{_CANDIDATE_MARK}一个极强的钩子简介", genre="玄幻",
        judge=_candidate_wins_judge, min_refs=3, max_refs=4,
    )
    assert s.pairs >= 3
    assert s.win_rate == pytest.approx(1.0)


@pytest.mark.unit
async def test_arena_candidate_always_loses_gives_winrate_0():
    s = await run_appeal_arena(
        candidate_blurb=f"{_CANDIDATE_MARK}很弱的简介", genre="玄幻",
        judge=_candidate_loses_judge, min_refs=3, max_refs=4,
    )
    assert s.win_rate == pytest.approx(0.0)


@pytest.mark.unit
async def test_arena_position_biased_judge_neutralized_to_half():
    s = await run_appeal_arena(
        candidate_blurb=f"{_CANDIDATE_MARK}简介", genre="玄幻",
        judge=_position_biased_judge, min_refs=3, max_refs=4,
    )
    # every pair: forward win + backward loss → tie → win_rate 0.5
    assert s.win_rate == pytest.approx(0.5)
