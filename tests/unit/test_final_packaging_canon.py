from __future__ import annotations

from bestseller.services.final_packaging_canon import (
    build_canonical_arbitration_messages,
    build_canonical_review_messages,
    build_canonical_quote_recovery_messages,
    canonical_source_catalog,
)


def test_canonical_review_requires_claim_level_fact_evidence() -> None:
    system, user = build_canonical_review_messages(
        canonical_body=(
            "核心案件性质：旧城供电事故；受害主体：维修站居民；"
            "角色：维修员林野与站长周岚；目标：今晚保住线路；机制：读取故障记录。"
        ),
        title="旧城最后一班车",
        synopsis="一桩少女失踪案把维修员拖进旧城。",
    )

    assert "canonical body 是唯一故事事实依据" in system
    assert "少女失踪案" in user
    assert "candidate_quote" in system
    assert "canonical_quote" in system
    assert "core_case|character|relationship|goal|mechanism|other" in system
    assert "unsupported 或 contradicted" in system
    assert "没有矛盾" in system
    assert "待审 synopsis 永远不是 canonical body" in system


def test_canonical_arbitration_cannot_use_old_seed_prohibitions_to_clear_claim() -> None:
    system, user = build_canonical_arbitration_messages(
        canonical_body="核心案件性质：锅炉事故调查；角色：检验员林野；目标：查明事故原因。",
        title="事故之后",
        synopsis="商人陶蕙与少女失踪案成为全书主线。",
        challenged_report={"passed": True, "hard_conflicts": []},
    )

    assert "仲裁官" in system
    assert "不能用旧 seed/prohibitions 逻辑替代事实核对" in system
    assert "可以推翻其 passed 结论" in system
    assert "商人陶蕙与少女失踪案" in user
    assert "初审报告（仅供逐项复核，不是事实来源）" in user


def test_canonical_review_allows_omission_but_forbids_new_core_facts() -> None:
    system, _ = build_canonical_review_messages(
        canonical_body="角色：维修员林野；目标：保住线路；核心机制：读取故障记录。",
        title="断电之前",
        synopsis="林野在暴雨里赶往维修站。",
    )

    assert "omitted 只表示简介合法省略" in system
    assert "泛化事件或题材氛围不能支撑一个新增的核心案件属性" in system
    assert "不要为修复而新增故事事实" in system


def test_canonical_effect_feedback_is_limited_to_selected_effects() -> None:
    system, _ = build_canonical_review_messages(
        canonical_body="角色：林野；目标：保住线路。",
        title="断电之前",
        synopsis="林野赶往维修站。",
        selected_effects=("comedy_engine",),
    )
    assert "comedy_engine" in system
    assert "effect_realization" in system
    assert "hype_satisfaction_engine" not in system

    arbitration, _ = build_canonical_arbitration_messages(
        canonical_body="角色：林野；目标：保住线路。",
        title="断电之前",
        synopsis="林野赶往维修站。",
        selected_effects=(),
    )
    assert "effect_realization" not in arbitration


def test_canonical_source_catalog_contains_only_exactly_verifiable_leaf_values() -> None:
    catalog = canonical_source_catalog(
        "【终稿资产：premise】\n"
        '{"premise":"封锁线收紧","noise":"不存在的片段"}'
    )
    assert "封锁线收紧" in catalog.values()
    assert "不存在的片段" in catalog.values()

    system, user = build_canonical_quote_recovery_messages(
        canonical_body='【终稿资产：premise】\n{"premise":"封锁线收紧"}',
        title="封锁线", synopsis="封锁线收紧",
        claims=[{"candidate_quote": "封锁线收紧"}], invalid_claim_indexes=[0],
    )
    assert "canonical_source_ids" in system
    assert "source_1" in user


def test_canonical_unsupported_claim_is_not_acquitted_as_source_omission():
    from bestseller.services.final_packaging_canon import normalize_canonical_report
    report = normalize_canonical_report(
        {"passed": True, "claims": [{"scope": "synopsis", "candidate_quote": "失踪的是药商",
            "canonical_quote": "一桩未破案件", "claim_type": "core_case", "status": "unsupported",
            "reason": "事实基线未提及受害者职业，不能在包装中新增。"}]},
        title="旧案", synopsis="失踪的是药商", canonical_body="一桩未破案件",
    )
    assert report["passed"] is False
    assert report["hard_conflicts"][0]["generated"] == "失踪的是药商"


def test_canonical_supported_compression_passes_but_invented_quotes_do_not():
    from bestseller.services.final_packaging_canon import normalize_canonical_report
    claim = {"scope": "synopsis", "candidate_quote": "追查旧案", "canonical_quote": "追查旧案",
             "claim_type": "goal", "status": "supported"}
    assert normalize_canonical_report({"claims": [claim]}, title="旧案", synopsis="他追查旧案",
                                      canonical_body="他要追查旧案")['passed']
    claim['canonical_quote'] = '从未提供的事实'
    report = normalize_canonical_report({"claims": [claim]}, title="旧案", synopsis="他追查旧案",
                                        canonical_body="他要追查旧案")
    assert report['passed']
    assert report['exact_quote_recoveries'] == [0]


def test_canonical_quotes_accept_transport_layout_but_not_changed_story_words():
    from bestseller.services.final_packaging_canon import normalize_canonical_report
    import json
    body = json.dumps({'premise': '他问“为何离开”，她没有回答。'}, ensure_ascii=False)
    claim = {'candidate_quote': '他问“为何离开”', 'canonical_quote': '他问“为何离开”',
             'scope': 'synopsis', 'claim_type': 'relationship', 'status': 'supported'}
    assert normalize_canonical_report({'claims': [claim]}, title='追问',
        synopsis='他问“为何离开”\n她没有回答。', canonical_body=body)['passed']
    claim['canonical_quote'] = '他问“为何留下”'
    report = normalize_canonical_report({'claims': [claim]}, title='追问',
        synopsis='他问“为何离开”\n她没有回答。', canonical_body=body)
    assert report['passed']
    assert report['exact_quote_recoveries'] == [0]


def test_canonical_normalizer_recovers_broken_source_quote_from_exact_candidate():
    from bestseller.services.final_packaging_canon import normalize_canonical_report

    claim = {
        "scope": "synopsis",
        "candidate_quote": "防线一格格收死",
        "canonical_quote": "防线一格格……收死",
        "claim_type": "mechanism",
        "status": "supported",
        "reason": "原文支持该机制",
    }
    report = normalize_canonical_report(
        {"passed": False, "claims": [claim]},
        title="防线",
        synopsis="防线一格格收死。",
        canonical_body="核心机制：防线一格格收死。",
    )

    assert report["passed"] is True
    assert report["invalid_claim_indexes"] == []
    assert report["exact_quote_recoveries"] == [0]
    assert report["claims"][0]["canonical_quote"] == "防线一格格收死"
    assert report["claims"][0]["status"] == "supported"


def test_canonical_normalizer_does_not_recover_candidate_absent_from_canonical():
    from bestseller.services.final_packaging_canon import normalize_canonical_report

    report = normalize_canonical_report(
        {
            "claims": [
                {
                    "scope": "synopsis",
                    "candidate_quote": "防线自动识别敌人",
                    "canonical_quote": "防线一格格……收死",
                    "claim_type": "mechanism",
                    "status": "supported",
                    "reason": "模型声称支持",
                }
            ]
        },
        title="防线",
        synopsis="防线自动识别敌人。",
        canonical_body="核心机制：防线一格格收死。",
    )

    assert report["passed"] is False
    assert report["invalid_claim_indexes"] == [0]
    assert report["exact_quote_recoveries"] == []


def test_canonical_normalizer_accepts_multiple_contiguous_evidence_segments():
    from bestseller.services.final_packaging_canon import normalize_canonical_report

    claim = {
        "scope": "synopsis", "candidate_quote": "封锁线收紧，粮仓断供",
        "canonical_quotes": ["封锁线收紧", "粮仓断供"],
        "claim_type": "mechanism", "status": "supported",
    }
    report = normalize_canonical_report(
        {"claims": [claim]}, title="封锁线", synopsis="封锁线收紧，粮仓断供",
        canonical_body="先是封锁线收紧。随后粮仓断供。",
    )
    assert report["passed"] is True
    assert report["invalid_claim_indexes"] == []


def test_canonical_normalizer_rejects_one_fabricated_segment_in_multiple_evidence():
    from bestseller.services.final_packaging_canon import normalize_canonical_report

    report = normalize_canonical_report(
        {"claims": [{
            "scope": "synopsis", "candidate_quote": "封锁线收紧，粮仓断供",
            "canonical_quotes": ["封锁线收紧", "粮仓自动恢复"],
            "claim_type": "mechanism", "status": "supported",
        }]}, title="封锁线", synopsis="封锁线收紧，粮仓断供",
        canonical_body="先是封锁线收紧。随后粮仓断供。",
    )
    assert report["passed"] is False
    assert report["invalid_claim_indexes"] == [0]


def test_unsupported_claim_can_have_no_source_evidence():
    from bestseller.services.final_packaging_canon import normalize_canonical_report
    report = normalize_canonical_report(
        {'claims': [{'candidate_quote': '第二颗妖丹', 'canonical_quote': '', 'canonical_quotes': [],
                     'claim_type': 'core_case', 'status': 'unsupported', 'scope': 'synopsis',
                     'reason': '基线只写下一件物品，未指定妖丹'}]},
        title='鉴物', synopsis='他拿起第二颗妖丹', canonical_body='下一件物品送到门前',
    )
    assert report['passed'] is False
    assert report['invalid_claim_indexes'] == []
    assert 'evaluator_error' not in report
    assert report['hard_conflicts'][0]['generated'] == '第二颗妖丹'
    assert report['repair_directives']


def test_supported_claim_still_requires_source_when_list_is_empty():
    from bestseller.services.final_packaging_canon import normalize_canonical_report
    report = normalize_canonical_report(
        {'claims': [{'candidate_quote': '第二颗妖丹', 'canonical_quote': '', 'canonical_quotes': [],
                     'claim_type': 'core_case', 'status': 'supported', 'scope': 'synopsis'}]},
        title='鉴物', synopsis='他拿起第二颗妖丹', canonical_body='下一件物品送到门前',
    )
    assert report['passed'] is False
    assert report['evaluator_error'] == 'canonical_claim_evidence_incomplete'
