"""The tournament must see the same story boundary as shared-context injection."""

from types import SimpleNamespace

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


def test_automatic_direction_does_not_lock_a_user_unspecified_story():
    block = conception._build_automatic_story_seed({"setting": "矿镇祀坛"})
    assert "可替换人物、场域与能力" in block
    assert "围绕这个起点生成同一本书" not in block


def candidate(**fields):
    return SimpleNamespace(to_dict=lambda: fields)


def check(item, **overrides):
    options = {"ctx": {}, "explicit_seed": "", "is_last_attempt": False}
    options.update(overrides)
    return conception._tournament_candidate_pollution_reasons(item, **options)


def test_unrequested_default_family_is_visible_before_winner_selection():
    item = candidate(
        concept="走阴人替东家入殓后开始追讨旧账。",
        mechanism="命债抵押、人情欠条和旧账清算决定下一次抬棺。",
    )
    assert "UNREQUESTED_DEFAULT_MOTIF_A" in check(item)


def test_boundary_ignores_judge_and_control_metadata():
    item = candidate(
        concept="少年用血脉修成的第二双手把对手的剑接住。",
        mechanism="新招迫使对手改用阵法，主角要学会与队友合击。",
        judge_reason="避免命债抵押、人情欠条和旧账清算。",
        seriality_judge={"reason": "不要靠抬棺还债重复。"},
    )
    assert check(item) == ()


def test_default_family_is_advisory_in_every_attempt():
    item = candidate(concept="替东家入殓后开始追讨旧账。", mechanism="命债抵押与旧账清算。")
    assert "UNREQUESTED_DEFAULT_MOTIF_A" in check(item)
    assert "UNREQUESTED_DEFAULT_MOTIF_A" in check(item, is_last_attempt=True)
    assert conception._tournament_candidate_hard_pollution_reasons(
        item, ctx={}, explicit_seed="", is_last_attempt=False
    ) == ()
    ctx = {"genre_intent_contract": {"tone_preference": "light"}}
    heavy = candidate(concept="尸体旁的尸首散发着阴冷气息。")
    assert conception._tournament_candidate_hard_pollution_reasons(
        heavy, ctx=ctx, explicit_seed="", is_last_attempt=False
    )


def test_finance_theme_can_be_selected_without_default_family_veto(monkeypatch):
    monkeypatch.setattr(conception, "_user_requested_debt", lambda ctx: True)
    item = candidate(concept="金融风控师追查一笔异常贷款。", mechanism="债务重组与现金流博弈。")
    assert "UNREQUESTED_DEFAULT_MOTIF_A" not in check(item)
    assert conception._tournament_candidate_hard_pollution_reasons(
        item, ctx={}, explicit_seed="金融风控", is_last_attempt=False
    ) == ()


def test_live_boiler_investigation_failure_is_advisory_only():
    """The recorded first-round concepts were wrongly rejected as debt-family."""

    item = candidate(
        concept=(
            "锅炉检验员被半夜叫到爆炸废墟前签老化认定书，却发现烧成铁疙瘩的"
            "压力表编号根本不属于死者车间，他当场拒签并抢着在天亮封场前把十年前的调拨单抄走。"
        )
    )
    assert conception._tournament_candidate_hard_pollution_reasons(
        item, ctx={}, explicit_seed="", is_last_attempt=False
    ) == ()


def test_explicit_light_contract_remains_a_hard_boundary():
    item = candidate(concept="尸体旁的尸首散发着阴冷气息，死者被拖入棺材。")
    reasons = conception._tournament_candidate_hard_pollution_reasons(
        item,
        ctx={"genre_intent_contract": {"tone_preference": "light"}},
        explicit_seed="",
        is_last_attempt=False,
    )
    assert any("轻松调性" in reason for reason in reasons)


def test_user_selected_theme_remains_authoritative(monkeypatch):
    monkeypatch.setattr(conception, "_user_requested_debt", lambda ctx: True)
    item = candidate(concept="收债人追讨旧账。", mechanism="命债抵押、人情欠条和旧账清算。")
    assert "UNREQUESTED_DEFAULT_MOTIF_A" not in check(item)


def test_boundary_failures_return_stable_actionable_retry_axes():
    result = SimpleNamespace(
        candidates=[SimpleNamespace(
            rejected_reason="建书选项/反污染门失败: UNREQUESTED_DEFAULT_MOTIF_A；内部裁判自由文本",
            composite=7.0,
        )],
        engine_rejections=[],
    )
    axes = conception._concept_tournament_retry_axis_codes(result)
    assert axes == ["UNREQUESTED_DEFAULT_MOTIF_A", "creation_intent_pollution"]
    feedback = conception._render_concept_retry_axis_feedback(axes)
    assert "内部裁判自由文本" not in feedback
    assert "受限修复说明" in feedback


def test_seedless_retry_allows_original_characters_within_selected_genre():
    feedback = conception._render_concept_retry_axis_feedback(["creation_intent_pollution"])
    assert "未给种子时可创作" in feedback
    assert "有用户种子时保留其明确事实" in feedback
