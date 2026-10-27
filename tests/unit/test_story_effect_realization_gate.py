from bestseller.services.drafts import (
    _persist_effect_contract_context,
    _stamp_effect_realization_report,
    audit_persisted_story_effect_realization,
)
from bestseller.services.story_effect_realization import (
    audit_story_effect_realization,
    build_comedy_contract_from_observation,
    compare_effect_contracts,
)
from bestseller.services.reviews import challenger_takes_current


_STRICT_CONTRACT = {
    "comedy_engine": {
        "source": "author",
        "required_stages": [
            "setup",
            "reversal_or_misalignment",
            "character_reaction",
            "situation_change",
        ],
    }
}


_STRICT_CREATION = {"field_sources": {"hook_spec": "explicit", "story_enhancers": "explicit"}, "hook_spec": {"effect_realization_contracts": _STRICT_CONTRACT}, "story_enhancers": {"effect_skills": ["comedy_engine"]}}

class _Record:
    def __init__(self, **values):
        self.__dict__.update(values)


def _chapter(**overrides):
    chapter = {
        "chapter_number": 1,
        "creation_intent_contract": _STRICT_CREATION,
        "story_enhancers": {"effect_skills": ["comedy_engine"]},
        "selected_effect_skills": {"primary": "comedy_engine"},
        "prose": "商队进城, 主角照章办事。",
    }
    chapter.update(overrides)
    return chapter


def test_comedy_requires_observable_four_part_contract():
    result = audit_story_effect_realization(_chapter())

    assert result["passed"] is False
    assert result["issues"] == ["comedy_contract_unrealized"]
    assert result["missing"] == [
        "setup",
        "reversal_or_misalignment",
        "character_reaction",
        "situation_change",
        "evidence_spans",
    ]


def test_comedy_contract_passes_without_keyword_matching():
    prose = (
        "守门人坚持按废城旧章收取物资。狮人把罚单当成贵族通行券, "
        "当场追加购买。领队先端着架子, 发现误会后改口催手下搬货。"
        "商队留下物资并替城门带来下一批客商。"
    )
    result = audit_story_effect_realization(
        _chapter(
            comedy_contract={
                "setup": "守门人坚持按废城旧章收取通行物资",
                "reversal_or_misalignment": "狮人把罚单当成贵族通行券, 当场追加购买",
                "character_reaction": "领队先端着架子, 发现误会后改口催手下搬货",
                "situation_change": "商队留下物资并替城门带来下一批客商",
                "evidence_spans": {
                    "setup": "守门人坚持按废城旧章收取物资",
                    "reversal_or_misalignment": "狮人把罚单当成贵族通行券, 当场追加购买",
                    "character_reaction": "领队先端着架子, 发现误会后改口催手下搬货",
                    "situation_change": "商队留下物资并替城门带来下一批客商",
                },
            },
            prose=prose,
        )
    )

    assert result["passed"] is True
    assert result["issues"] == []


def test_post_write_comedy_observation_requires_ordered_verbatim_evidence() -> None:
    prose = (
        "裴淮安把三十年草案推到沈渡面前。"
        "沈渡却把草案逐页折成扇面，边折边念官样条款。"
        "门边守卒先憋红了脸，随后笑出了声。"
        "沈渡借着笑声封住二十银的日租价，裴淮安只能当厅念价。"
    )
    observation = {
        "setup": {
            "description": "特使递交长期包销草案",
            "quote": "裴淮安把三十年草案推到沈渡面前。",
        },
        "reversal_or_misalignment": {
            "description": "主角把正式草案当扇子折",
            "quote": "沈渡却把草案逐页折成扇面，边折边念官样条款。",
        },
        "character_reaction": {
            "description": "守卒被错位动作逗笑",
            "quote": "门边守卒先憋红了脸，随后笑出了声。",
        },
        "situation_change": {
            "description": "笑声成为改价筹码并改变交易",
            "quote": "沈渡借着笑声封住二十银的日租价，裴淮安只能当厅念价。",
        },
    }

    contract = build_comedy_contract_from_observation(
        observation,
        prose,
        planned_contract={"concrete_beat": "折草案成扇并借笑声改价"},
    )

    assert contract is not None
    assert contract["concrete_beat"] == "折草案成扇并借笑声改价"
    assert contract["evidence_spans"]["situation_change"] in prose


def test_post_write_comedy_observation_rejects_invented_or_out_of_order_quotes() -> None:
    prose = "先改变了交易。后来众人才笑。"
    observation = {
        "setup": {"description": "建立场面", "quote": "后来众人才笑。"},
        "reversal_or_misalignment": {"description": "发生错位", "quote": "先改变了交易。"},
        "character_reaction": {"description": "人物发笑", "quote": "并不存在的原句"},
        "situation_change": {"description": "交易改变", "quote": "先改变了交易。"},
    }

    assert build_comedy_contract_from_observation(observation, prose) is None


def test_metadata_or_route_without_a_cashed_beat_is_unrealized():
    result = audit_story_effect_realization(
        {
            "chapter_number": 2,
            "creation_intent_contract": _STRICT_CREATION,
            "story_effect_skill_selection": {"effect_skills": ["comedy_engine"]},
            "story_enhancers": {"effect_skills": ["comedy_engine"]},
            "prose": "双方完成谈判, 局势保持原样。",
        }
    )

    assert result["passed"] is False
    assert result["issues"] == ["comedy_contract_unrealized"]


def test_rewrite_cannot_delete_an_already_realized_effect():
    prose = (
        "守门人按章收取物资。对方误把罚单当成特权凭证。"
        "领队改变口风并催促成交。商队决定入城, 带来新的交易压力。"
    )
    before = _chapter(
        comedy_contract={
            "setup": "守门人按章收取物资",
            "reversal_or_misalignment": "对方误把罚单当成特权凭证",
            "character_reaction": "领队改变口风并催促成交",
            "situation_change": "商队决定入城, 带来新的交易压力",
            "evidence_quotes": {
                "setup": "守门人按章收取物资",
                "reversal_or_misalignment": "对方误把罚单当成特权凭证",
                "character_reaction": "领队改变口风并催促成交",
                "situation_change": "商队决定入城, 带来新的交易压力",
            },
        },
        prose=prose,
    )
    after = _chapter(prose="商队顺利进城。")

    result = compare_effect_contracts(before, after)

    assert result["passed"] is False
    assert result["issues"] == ["protected_effect_deleted"]
    assert result["deleted_effects"] == ["comedy_engine"]


def test_unrealized_effect_rewrite_is_never_allowed_to_take_current() -> None:
    assert (
        challenger_takes_current(
            challenger_blocked=True,
            incumbent_gate_outcome="blocked",
            has_duplicate_findings=False,
            deterministic_audit_failed=False,
            violation_codes=("STORY_EFFECT_REALIZATION_UNREALIZED",),
            incumbent_audit_failed=True,
        )
        is False
    )


def test_rewrite_is_blocked_when_contract_remains_but_prose_evidence_is_deleted():
    before = _chapter(
        comedy_contract={
            "setup": "甲方坚持旧章",
            "reversal_or_misalignment": "乙方误读凭证",
            "character_reaction": "乙方改口成交",
            "situation_change": "交易条件发生变化",
            "evidence_spans": {
                "setup": "甲方坚持旧章",
                "reversal_or_misalignment": "乙方误读凭证",
                "character_reaction": "乙方改口成交",
                "situation_change": "交易条件发生变化",
            },
        },
        prose="甲方坚持旧章。乙方误读凭证。乙方改口成交。交易条件发生变化。",
    )
    after = dict(before, prose="双方完成谈判。", comedy_contract=before["comedy_contract"])

    result = compare_effect_contracts(before, after)

    assert result["passed"] is False
    assert result["issues"] == ["protected_effect_deleted"]
    assert result["deleted_effects"] == ["comedy_engine"]


def test_unrelated_contract_changes_are_not_treated_as_effect_deletion():
    before = _chapter(
        comedy_contract={
            "setup": "甲",
            "reversal_or_misalignment": "乙",
            "character_reaction": "丙",
            "situation_change": "丁",
        }
    )
    after = _chapter(
        comedy_contract={
            "setup": "甲",
            "reversal_or_misalignment": "乙",
            "character_reaction": "丙",
            "situation_change": "丁",
        },
        prose="新增了另一条行动线。",
    )

    result = compare_effect_contracts(before, after)

    assert result["passed"] is True
    assert result["issues"] == []


def test_persisted_chapter_selection_without_prose_evidence_blocks_promotion():
    project = _Record(
        metadata_json={
            "story_enhancers": {"effect_skills": ["comedy_engine"]},
            "creation_intent_contract": _STRICT_CREATION,
        }
    )
    chapter = _Record(
        metadata_json={
            "selected_effect_skills": {"primary": "comedy_engine"},
            "comedy_contract": {
                "setup": "守门人展示旧章",
                "reversal_or_misalignment": "商队误读凭证",
                "character_reaction": "领队改口",
                "situation_change": "交易条件改变",
                "evidence_spans": {
                    "setup": "守门人展示旧章",
                    "reversal_or_misalignment": "商队误读凭证",
                    "character_reaction": "领队改口",
                    "situation_change": "交易条件改变",
                },
            },
        }
    )

    report = audit_persisted_story_effect_realization(
        project,
        chapter,
        "双方平静地完成交易。",
    )

    assert report["passed"] is False
    assert report["issues"] == ["comedy_contract_unrealized"]


def test_legacy_persisted_chapter_without_selected_effect_is_a_noop():
    report = audit_persisted_story_effect_realization(
        _Record(metadata_json={}),
        _Record(metadata_json={}),
        "双方完成交易。",
    )

    assert report["passed"] is True
    assert report["selected_effects"] == []


def test_scene_only_effect_contract_is_pinned_for_later_promotion_audit():
    contract = {
        "setup": "守门人展示旧章",
        "reversal_or_misalignment": "商队误读凭证",
        "character_reaction": "领队改口",
        "situation_change": "交易条件改变",
        "evidence_spans": {
            "setup": "守门人展示旧章",
            "reversal_or_misalignment": "商队误读凭证",
            "character_reaction": "领队改口",
            "situation_change": "交易条件改变",
        },
    }
    project = _Record(
        metadata_json={
            "story_enhancers": {"effect_skills": ["comedy_engine"]},
            "creation_intent_contract": _STRICT_CREATION,
        }
    )
    chapter = _Record(metadata_json={})
    scene = _Record(
        id="scene-1",
        metadata_json={
            "selected_effect_skills": {"primary": "comedy_engine"},
            "comedy_contract": contract,
        },
    )

    _persist_effect_contract_context(project, chapter, scenes=[scene])

    assert chapter.metadata_json["selected_effect_skills"] == {"primary": "comedy_engine"}
    assert chapter.metadata_json["comedy_contract"] == contract
    report = audit_persisted_story_effect_realization(
        project,
        chapter,
        "守门人展示旧章。商队误读凭证。领队改口。交易条件改变。",
    )
    assert report["passed"] is True


def test_repaired_effect_report_clears_stale_chapter_block():
    chapter = _Record(metadata_json={"effect_realization_blocked": True})

    _stamp_effect_realization_report(
        chapter,
        {
            "passed": True,
            "selected_effects": ["comedy_engine"],
            "realized_effects": ["comedy_engine"],
        },
    )

    assert chapter.metadata_json["effect_realization_blocked"] is False
