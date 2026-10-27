from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_final_packaging_judge_sees_title_and_explicit_prohibition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_judge(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        captured["fallback"] = kwargs["fallback"]
        return (
            {
                "passed": False,
                "hard_conflicts": [
                    {
                        "fact": "禁止把纯收费作为核心卖点",
                        "generated": "我把商路抽三成",
                        "reason": "标题把被禁止的替代方案提升为整本书身份",
                    }
                ],
                "repair_directives": ["标题改写为交通网络与废城经营承诺"],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_judge)

    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(),
        object(),
        concept_seed=("主角重组道路、市场与契约的流通规则。禁止沿用收费站城主和纯粹收过路费设定。"),
        title="我把商路抽三成",
        premise="主角修复废城生存循环，并争夺大陆交通主权。",
        synopsis="他重组商路规则，让废城重新接入大陆。",
        ctx={"language": "zh-CN", "genre": "异世大陆"},
    )

    assert report["passed"] is False
    assert report["hard_conflicts"]
    assert "我把商路抽三成" in captured["prompt"]
    assert "禁止沿用收费站城主" in captured["prompt"]
    assert "标题把禁止项包装成核心卖点" in captured["system"]
    assert "premise 仅供" in captured["system"]
    assert "scope 只能是title 或 synopsis".replace(" ", "") in captured["prompt"].replace(" ", "")
    assert '"passed": false' in captured["fallback"].lower()


@pytest.mark.asyncio
async def test_final_packaging_canonical_body_is_independent_of_pending_synopsis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_judge(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        return ({"claims": [{"candidate_quote": "少女失踪案", "canonical_quote": "", "claim_type": "core_case", "scope": "synopsis", "status": "unsupported", "reason": "包装新增核心案件"}]}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_judge)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(),
        object(),
        concept_seed="",
        title="旧城最后一班车",
        premise="维修员守住即将断电的旧城交通线。",
        synopsis="待审简介新增一桩 canonical body 没有的少女失踪案。",
        canonical_body={
            "premise": "维修员守住即将断电的旧城交通线。",
            "writing_profile": {"character": {"golden_finger": "读懂故障记录"}},
            "story_spine": {"who": "维修员林野", "why_now": "今晚断电"},
            "story_architecture": {"causal_units": [{"id": "u1", "goal": "保住线路"}]},
        },
        ctx={"language": "zh-CN", "genre": "现实题材"},
    )

    assert report["passed"] is False
    assert report["hard_conflicts"][0]["generated"] == "少女失踪案"
    assert "维修员林野" in captured["prompt"]
    canonical_section = captured["prompt"].split("已批准 canonical body", 1)[1].split(
        "最终书名：", 1
    )[0]
    assert "待审简介新增一桩" not in canonical_section
    assert "canonical body 是唯一故事事实依据" in captured["system"]


@pytest.mark.asyncio
async def test_canonical_effect_feedback_is_independent_of_fact_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_judge(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        return ({
            "claims": [{
                "scope": "synopsis", "candidate_quote": "保住线路",
                "canonical_quote": "保住线路", "claim_type": "goal",
                "status": "supported", "reason": "目标原文支持",
            }],
            "effect_realization": {"comedy_engine": {
                "passed": False, "evidence_quote": "", "expectation": "",
                "misalignment": "", "reaction": "", "situation_change": "",
            }},
        }, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_judge)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(), object(), concept_seed="", title="断电之前",
        premise="维修员保住线路", synopsis="他必须保住线路。",
        canonical_body={"premise": "维修员保住线路"},
        ctx={"language": "zh-CN", "genre_intent_contract": {
            "explicit_enhancers": {"effect_skills": ["comedy_engine"]},
        }},
    )
    assert report["passed"] is True
    assert report["effect_realization"]["comedy_engine"]["passed"] is False
    assert "comedy_engine" in captured["system"]


@pytest.mark.asyncio
async def test_canonical_arbitration_prompt_receives_only_selected_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_arbitrator(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        return ({"claims": [{
            "scope": "synopsis", "candidate_quote": "保住线路",
            "canonical_quote": "保住线路", "claim_type": "goal",
            "status": "supported", "reason": "目标原文支持",
        }], "effect_realization": {}}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_arbitrator)
    report, _ = await conception._arbitrate_final_packaging_seed_fidelity_report(
        object(), object(), concept_seed="", title="断电之前",
        premise="维修员保住线路", synopsis="他必须保住线路。",
        challenged_report={"claims": []},
        canonical_body={"premise": "维修员保住线路"},
        ctx={"language": "zh-CN", "genre_intent_contract": {
            "explicit_enhancers": {"effect_skills": ["twist_reversal_engine"]},
        }},
    )
    assert report["passed"] is True
    assert "twist_reversal_engine" in captured["system"]
    assert "comedy_engine" not in captured["system"]


@pytest.mark.asyncio
async def test_canonical_quote_recovery_is_one_bounded_quote_only_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_call(*_args, **kwargs):
        calls.append(kwargs["stage"])
        if kwargs["stage"].endswith("judge"):
            return ({"claims": [{
                "scope": "synopsis", "candidate_quote": "封锁线收紧",
                "canonical_quote": "封锁线……收紧", "claim_type": "mechanism",
                "status": "supported", "reason": "原文支持",
            }]}, [])
        return ({"corrections": [{
            "index": 0, "candidate_quote": "封锁线层层收紧",
            "canonical_quote": "封锁线层层收紧",
        }]}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(), object(), concept_seed="", title="封锁线",
        premise="封锁线层层收紧", synopsis="封锁线收紧，封锁线层层收紧",
        canonical_body={"premise": "封锁线层层收紧"}, ctx={"language": "zh-CN"},
    )
    assert calls == [
        "conception.final_packaging_seed_fidelity_judge",
        "conception.final_packaging_canonical_quote_recovery",
    ]
    assert report["passed"] is True
    assert report["quote_recovery_indexes"] == [0]
    assert report["claims"][0]["candidate_quote"] == "封锁线收紧"


@pytest.mark.asyncio
async def test_canonical_quote_recovery_cannot_promote_unsupported_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(*_args, **kwargs):
        if kwargs["stage"].endswith("judge"):
            return ({"claims": [{
                "scope": "synopsis", "candidate_quote": "失踪的是药商",
                "canonical_quote": "未破案件……", "claim_type": "core_case",
                "status": "unsupported", "reason": "包装新增核心案件",
            }]}, [])
        return ({"corrections": [{
            "index": 0, "candidate_quote": "失踪的是药商",
            "canonical_quote": "未破案件",
        }]}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(), object(), concept_seed="", title="旧案",
        premise="未破案件", synopsis="失踪的是药商",
        canonical_body={"premise": "未破案件"}, ctx={"language": "zh-CN"},
    )
    assert report["passed"] is False
    assert report["hard_conflicts"][0]["claim_status"] == "unsupported"


@pytest.mark.asyncio
async def test_canonical_quote_recovery_can_fix_invalid_candidate_layout_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(*_args, **kwargs):
        if kwargs["stage"].endswith("judge"):
            return ({"claims": [{
                "scope": "synopsis", "candidate_quote": "封锁线层层收紧",
                "canonical_quote": "封锁线……收紧", "claim_type": "mechanism",
                "status": "supported", "reason": "原文支持",
            }]}, [])
        return ({"corrections": [{
            "index": 0, "candidate_quote": "封锁线收紧",
            "canonical_quote": "封锁线收紧",
        }]}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(), object(), concept_seed="", title="封锁线",
        premise="封锁线收紧", synopsis="封锁线收紧",
        canonical_body={"premise": "封锁线收紧"}, ctx={"language": "zh-CN"},
    )
    assert report["passed"] is True
    assert report["claims"][0]["candidate_quote"] == "封锁线收紧"


@pytest.mark.asyncio
async def test_canonical_quote_recovery_ignores_semantic_field_overwrites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(*_args, **kwargs):
        if kwargs["stage"].endswith("judge"):
            return ({"claims": [{
                "scope": "synopsis", "candidate_quote": "封锁线收紧",
                "canonical_quote": "封锁线……收紧", "claim_type": "mechanism",
                "status": "unsupported", "reason": "保留原始理由",
            }]}, [])
        return ({"corrections": [{
            "index": 0, "candidate_quote": "封锁线层层收紧",
            "canonical_quote": "封锁线层层收紧", "status": "supported",
            "reason": "恶意覆盖", "claim_type": "other", "scope": "title",
        }]}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(), object(), concept_seed="", title="封锁线",
        premise="封锁线层层收紧", synopsis="封锁线层层收紧",
        canonical_body={"premise": "封锁线层层收紧"}, ctx={"language": "zh-CN"},
    )
    claim = report["claims"][0]
    assert claim["status"] == "unsupported"
    assert claim["reason"] == "保留原始理由"
    assert claim["claim_type"] == "mechanism"
    assert claim["scope"] == "synopsis"
    assert report["passed"] is False


@pytest.mark.asyncio
async def test_canonical_quote_recovery_accepts_bounded_multiple_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(*_args, **kwargs):
        if kwargs["stage"].endswith("judge"):
            return ({"claims": [{
                "scope": "synopsis", "candidate_quote": "封锁线收紧，粮仓断供",
                "canonical_quote": "封锁线……粮仓断供", "claim_type": "mechanism",
                "status": "supported", "reason": "复合机制",
            }]}, [])
        return ({"corrections": [{
            "index": 0, "candidate_quote": "封锁线收紧，粮仓断供",
            "canonical_source_ids": ["source_1", "source_2"],
        }]}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(), object(), concept_seed="", title="封锁线",
        premise="封锁线收紧，粮仓断供", synopsis="封锁线收紧，粮仓断供",
        canonical_body={"premise": {"first": "封锁线收紧", "second": "粮仓断供"}},
        ctx={"language": "zh-CN"},
    )
    assert report["passed"] is True
    assert report["claims"][0]["canonical_quotes"] == ["封锁线收紧", "粮仓断供"]


@pytest.mark.asyncio
async def test_canonical_quote_recovery_unknown_source_id_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(*_args, **kwargs):
        if kwargs["stage"].endswith("judge"):
            return ({"claims": [{
                "scope": "synopsis", "candidate_quote": "封锁线收紧",
                "canonical_quote": "错误引文", "claim_type": "mechanism",
                "status": "supported", "reason": "原文支持",
            }]}, [])
        return ({"corrections": [{
            "index": 0, "candidate_quote": "封锁线层层收紧",
            "canonical_source_ids": [{"unexpected": "object"}],
            "canonical_quote": "封锁线收紧",
        }]}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    report, _ = await conception._judge_final_packaging_seed_fidelity(
        object(), object(), concept_seed="", title="封锁线",
        premise="封锁线收紧", synopsis="封锁线层层收紧",
        canonical_body={"premise": "封锁线收紧"}, ctx={"language": "zh-CN"},
    )
    assert report["passed"] is False
    assert report["evaluator_error"] == "canonical_claim_evidence_incomplete"


@pytest.mark.asyncio
async def test_final_packaging_arbitration_preserves_required_trade_route_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_arbitrator(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                "passed": True,
                "positive_requirements": ["重组道路、市场与契约的流通规则", "争夺大陆交通主权"],
                "prohibitions": ["收费站城主", "界碑自动报价", "纯粹收过路费"],
                "hard_conflicts": [],
                "compatible_extensions": [
                    {
                        "fact": "争夺大陆交通主权",
                        "generated": "让废城成为大陆通衢",
                        "reason": "这是用户要求的终局承诺，不是纯收费循环",
                    }
                ],
                "preserved_facts": ["主要对手建立竞争网络"],
                "repair_directives": [],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_arbitrator)

    report, _ = await conception._arbitrate_final_packaging_seed_fidelity_report(
        object(),
        object(),
        concept_seed=(
            "主角重组道路、市场和契约中的流通规则，最终争夺大陆交通主权。"
            "禁止沿用收费站城主、界碑自动报价和纯粹收过路费设定。"
        ),
        title="废城通衢",
        premise="主角修复废城生存循环，并争夺大陆交通主权。",
        synopsis="他重组道路与市场，让废城接入大陆交通网络。",
        challenged_report={
            "passed": False,
            "hard_conflicts": [
                {
                    "fact": "禁止纯收费",
                    "generated": "商路与大陆通衢",
                    "reason": "与过路费处于同一题材领域",
                }
            ],
        },
        ctx={"language": "zh-CN", "genre": "异世大陆"},
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert report["compatible_extensions"][0]["classification"] == "compatible_extension"
    assert "语义邻近不等于冲突" in captured["system"]
    assert "positive_requirements 与 prohibitions 必须分别列出" in captured["prompt"]
    assert "禁止沿用收费站城主" in captured["prompt"]


@pytest.mark.asyncio
async def test_final_packaging_arbitration_keeps_actual_toll_promise_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_arbitrator(*_args, **_kwargs):
        return (
            {
                "passed": True,
                "positive_requirements": ["争夺大陆交通主权"],
                "prohibitions": ["纯粹收过路费"],
                "hard_conflicts": [
                    {
                        "fact": "禁止纯粹收过路费",
                        "generated": "我把商路抽三成；每辆车都按货值交过路费",
                        "reason": "标题与主要经营循环直接恢复了禁设",
                    }
                ],
                "compatible_extensions": [],
                "repair_directives": ["删除抽成与过路费主循环"],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_arbitrator)

    report, _ = await conception._arbitrate_final_packaging_seed_fidelity_report(
        object(),
        object(),
        concept_seed="争夺大陆交通主权；禁止纯粹收过路费。",
        title="我把商路抽三成",
        premise="主角修复废城。",
        synopsis="每辆车都按货值交过路费，主角靠抽成扩城。",
        challenged_report={"passed": False, "hard_conflicts": [{"fact": "禁止纯收费"}]},
        ctx={"language": "zh-CN"},
    )

    assert report["passed"] is False
    assert report["hard_conflicts"][0]["classification"] == "hard_conflict"
    assert "抽三成" in report["hard_conflicts"][0]["generated"]


@pytest.mark.asyncio
async def test_final_packaging_arbitration_does_not_turn_acquittals_into_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_arbitrator(*_args, **_kwargs):
        return (
            {
                "passed": False,
                "positive_requirements": ["争夺大陆交通主权"],
                "prohibitions": ["收费站城主", "纯粹收过路费"],
                "hard_conflicts": [
                    {
                        "classification": "hard_conflict",
                        "scope": "synopsis",
                        "fact": "禁止纯收费",
                        "generated": "修路分包、分账与水井契约竞争",
                        "reason": "初审错误扩张了禁设范围；没有设卡收费，不能据此判为硬冲突",
                    },
                    {
                        "classification": "compatible_extension",
                        "scope": "title",
                        "fact": "争夺大陆交通主权",
                        "generated": "废城通衢",
                        "reason": "交通主权与收费站并非同一实现",
                    },
                    {
                        "classification": "hard_conflict",
                        "scope": "premise_context",
                        "fact": "每次改写都要付代价",
                        "generated": "premise 未单列代价",
                        "reason": "需要由上游故事一致性门禁复核",
                    },
                ],
                "compatible_extensions": [],
                "upstream_consistency_warnings": [],
                "repair_directives": [],
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_arbitrator)

    report, _ = await conception._arbitrate_final_packaging_seed_fidelity_report(
        object(),
        object(),
        concept_seed=(
            "重组道路、市场与契约，争夺大陆交通主权；禁止收费站城主、界碑自动报价和纯粹收过路费。"
        ),
        title="废城通衢",
        premise="主角让规则改写越来越熟练。",
        synopsis="每次改写都会把代价转移到城内真实环节。",
        challenged_report={"passed": False, "hard_conflicts": [{"fact": "禁止纯收费"}]},
        ctx={"language": "zh-CN"},
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert len(report["compatible_extensions"]) == 2
    assert report["upstream_consistency_warnings"][0]["classification"] == (
        "upstream_consistency_warning"
    )


def test_final_packaging_normalizer_handles_real_acquittal_language_and_keeps_polish() -> None:
    report = conception._normalize_final_packaging_semantic_report(
        {
            "passed": False,
            "hard_conflicts": [
                {
                    "classification": "hard_conflict",
                    "scope": "title",
                    "fact": "禁止收费站城主、界碑自动报价和纯粹收过路费",
                    "generated": "穿越废城他一路改规则称王",
                    "reason": (
                        "书名未直接还原收费站、界碑或过路费，因此不构成对该禁设的"
                        "直接复活。初审错误地把非冲突表达判为硬冲突。"
                    ),
                },
                {
                    "classification": "hard_conflict",
                    "scope": "synopsis",
                    "fact": "禁止纯粹收过路费",
                    "generated": "哪条路、哪道关、哪张契他说了算",
                    "reason": (
                        "这是用户要求的流通规则重组，初审超出了禁设的实际射程，"
                        "构成错误升格；霸权措辞需微调，但不等于直接恢复禁止机制。"
                    ),
                },
            ],
            "repair_directives": ["把『掐住大陆脖子』改成受到竞争网络和盟约约束的表达。"],
        }
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert len(report["compatible_extensions"]) == 2
    assert report["non_blocking_repair_directives"] == report["repair_directives"]
    assert not conception._final_packaging_requires_repair(report, non_blocking_polish_attempted=False)
    assert not conception._final_packaging_requires_repair(
        report, non_blocking_polish_attempted=True
    )


def test_final_packaging_normalizer_keeps_actual_prohibition_revival_blocked() -> None:
    report = conception._normalize_final_packaging_semantic_report(
        {
            "passed": True,
            "hard_conflicts": [
                {
                    "classification": "hard_conflict",
                    "scope": "synopsis",
                    "fact": "禁止纯粹收过路费",
                    "generated": "每辆车按货值交过路费，主角靠抽成扩城",
                    "reason": "简介把过路费恢复为主要经营循环与核心卖点。",
                }
            ],
            "repair_directives": ["删除过路费主循环"],
        }
    )

    assert report["passed"] is False
    assert len(report["hard_conflicts"]) == 1
    assert report["non_blocking_repair_directives"] == []


def test_final_packaging_normalizer_demotes_omission_inference_to_warning() -> None:
    report = conception._normalize_final_packaging_semantic_report(
        {
            "passed": False,
            "hard_conflicts": [
                {
                    "classification": "hard_conflict",
                    "scope": "synopsis",
                    "fact": "金手指改写规则时必须承担真实代价",
                    "generated": "简介只写主角修通商路并击退同行",
                    "reason": (
                        "简介省略了代价转移与两难选择，因此把更宽的故事压缩成了单纯经营循环。"
                    ),
                }
            ],
            "repair_directives": ["在正文设定中继续保留代价机制。"],
        }
    )

    assert report["passed"] is True
    assert report["hard_conflicts"] == []
    assert report["upstream_consistency_warnings"][0]["classification"] == (
        "upstream_consistency_warning"
    )


def test_final_packaging_synopsis_selector_keeps_best_in_band_candidate() -> None:
    stronger = (
        "三条商路都绕开这座废城，他接手时粮仓只够撑七天。\n\n"
        "他改掉一条旧规矩，先让粮车进城，又逼囤粮商当众吐出仓库。\n\n"
        "可同行学会了他的办法，转头建起新城抢货抢人；他只能一路夺回商路，"
        "直到掀翻旧帝国留下的封锁。"
    )
    regressed = (
        "他是一名现代运营负责人，穿越后开始研究道路、关隘、市场与契约中的"
        "流通规则，并在效率、民生与盟约之间进行复杂取舍。"
    ) * 3

    selected = conception._select_final_packaging_synopsis_candidate(
        (stronger, regressed),
        title="我把废城改活了",
        premise="主角穿越废城，改写规则并承担代价，最终夺回大陆商路。",
        tags=("穿越", "升级流", "经营", "喜剧"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        language="zh-CN",
        target_platform="fanqie",
        config=None,
    )

    assert selected == stronger


def test_final_packaging_synopsis_selector_preserves_complete_supplied_proposal() -> None:
    overlong = (
        "三条商路都绕开这座废城，他接手时粮仓只够撑七天。\n\n"
        "他改掉一条旧规矩，先让粮车进城，又逼囤粮商当众吐出仓库。\n\n"
        "同行很快学会他的办法，在邻城抢货抢人，他只能一路夺回商路。\n\n"
        "更远处还有一笔旧帝国留下的百年烂账，正等着整座城去偿还。\n\n"
        "城外的旧贵族也在集结，他们要夺回仓库、截断水源，再把所有居民赶走。"
    )

    selected = conception._select_final_packaging_synopsis_candidate(
        (overlong,),
        title="我把废城改活了",
        premise="主角穿越废城，改写规则并承担代价，最终夺回大陆商路。",
        tags=("穿越", "升级流", "经营", "喜剧"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        language="zh-CN",
        target_platform="fanqie",
        config=None,
    )

    assert selected.endswith(("。", "！", "？", ".", "!", "?"))
    assert selected == overlong
    assert "截断水源" in selected


def test_final_packaging_title_selector_prefers_hard_floor_passing_grounded_candidate() -> None:
    selected = conception._select_final_packaging_title_candidate(
        ("我把废城拍成最贵关卡", "从烂木门到北线咽喉"),
        incumbent="破城关卡",
        title_profile={
            "authoritative_concept_seed": (
                "城门是绑铁丝的烂木板，商洛拍下旧界碑，要把废城建成北线商路咽喉。"
            ),
            # Reproduces a stale pre-repair contract. The selector may bypass
            # it only when independent hard floors and story grounding pass.
            "title_anchor_groups": {
                "identity": ["挂名城主"],
                "action": ["无显性系统"],
                "object": ["面板"],
            },
        },
        premise="商洛接手废城，守住北线商路。",
        synopsis="他从烂木门起步，把废城建成北线咽喉。",
        tags=("异世大陆", "穿越", "升级流", "金手指", "经营"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        target_platform="fanqie",
        config=None,
    )

    assert selected == "从烂木门到北线咽喉"


def test_final_packaging_synopsis_selector_prefers_more_hard_floors_over_total() -> None:
    flat_high_total = (
        "哥布林搬空废城，我接手只剩七个老矿工。\n\n"
        "狮人商队嘲笑破城，我拍下界碑让他咬牙成交。\n\n"
        "探子在墙头不敢抬头，商洛继续记下下一位。"
    )
    stronger_open_loop = (
        "天亮前，我必须守住只剩七人的废城。\n\n"
        "狮人刚嘲笑我，界碑却让绕道据点报出更高价，他只能咬牙成交。\n\n"
        "可下一支穿越北线的车队更大、更横、还带刀，这次谁会先低头？"
    )

    selected = conception._select_final_packaging_synopsis_candidate(
        (flat_high_total, stronger_open_loop),
        title="从烂木门到北线咽喉",
        premise="收费站班长穿越异世，拍界碑守废城并扩大北线商路。",
        tags=("异世大陆", "穿越", "升级流", "金手指", "经营"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        language="zh-CN",
        target_platform="fanqie",
        config=None,
    )

    assert selected == stronger_open_loop


def test_ninth_packaging_receipt_explains_in_band_candidates_rejected() -> None:
    """Replay the ninth-round short receipts without calling an LLM."""
    fixture_path = Path(__file__).parents[1] / "fixtures/ninth-packaging-short-candidates.json"
    fixture = json.loads(fixture_path.read_text())
    short_candidates = tuple(fixture["candidates"])
    diagnostics: list[dict[str, object]] = []
    selected = conception._select_final_packaging_synopsis_candidate(
        short_candidates,
        title="引路尸",
        premise=(
            "收尸人纪白能让死者完成临死时被打断的一个动作，每具尸体只能触发一次。"
            "仙门屠城后，他借验尸发现，满城死者最后的动作竟都在为一个孩子让路。"
        ),
        tags=("收尸人", "验尸通灵", "仙门屠城", "孩童救赎", "逃亡之路", "东方玄幻"),
        genre="东方玄幻",
        sub_genre="东方玄幻",
        language="zh-CN",
        target_platform="番茄小说",
        config=None,
        selection_diagnostics=diagnostics,
    )

    assert selected in short_candidates
    assert len(selected) in {101, 103}
    assert diagnostics
    full_candidates = [item for item in diagnostics if item["candidate"] in short_candidates]
    assert {item["candidate"] for item in diagnostics} >= set(short_candidates)
    assert full_candidates
    assert all(item["in_band"] is True for item in full_candidates)
    assert all(item["hard_floor_passed"] is False for item in full_candidates)
    assert all("emotion_charge" in item["failed_axes"] for item in full_candidates)
    assert sum(bool(item.get("selected")) for item in diagnostics) == 1


@pytest.mark.asyncio
async def test_final_packaging_repair_is_field_scoped_and_seed_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_repair(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                "title_candidates": ["废城通衢录", "万族商路重启时"],
                "synopsis": "他重组道路、市场与契约，让废城重新接入大陆。",
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_repair)

    package, _ = await conception._repair_final_packaging_to_explicit_seed(
        object(),
        object(),
        concept_seed="主角修复废城流通循环；禁止把纯收费写成核心。",
        title="我把商路抽三成",
        premise="主角争夺大陆交通主权。",
        synopsis="他靠抽成把城救活。",
        report={
            "hard_conflicts": [{"fact": "禁止纯收费", "generated": "抽三成"}],
            "repair_directives": ["删除抽成与过路费主循环"],
        },
        ctx={"language": "zh-CN", "genre": "异世大陆"},
        attempt=4,
        quality_feedback={
            "title_score": 77.0,
            "title_min": 80.0,
            "title_suggestions": ["加入主角动作或明确结果"],
            "persona_feedback": "书名太虚，看不出主角要做什么。",
            "ranking_candidate": {
                "findings": [
                    {
                        "code": "TITLE_HOOK_POWER_BELOW_FLOOR",
                        "metric": "title.hook_power",
                        "actual": 1.5,
                        "required": 3.5,
                        "message": "标题钩子未过硬门",
                    }
                ]
            },
        },
        target_platform="fanqie",
        tags=("异世大陆", "穿越", "经营", "喜剧"),
        candidate_feedback={
            "title_selection": [
                {
                    "candidate": "旧标题",
                    "reason": "title_hard_floor_or_platform_reject",
                    "dimensions": {"hook_power": 1.5},
                    "required": {"hook_power": 3.5},
                    "unknown": "must not enter prompt",
                }
            ],
            "synopsis_selection": [
                {
                    "candidate": "上一轮简介候选文本",
                    "char_count": 203,
                    "in_band": False,
                    "hard_floor_passed": False,
                    "failed_axes": ["emotion_charge"],
                    "dimensions": {"emotion_charge": 2.35},
                    "required": {"emotion_charge": 3.0},
                    "band": [80, 130],
                    "unknown": "must not enter prompt",
                }
            ],
        },
    )

    assert package["title_candidates"] == ["废城通衢录", "万族商路重启时"]
    assert package["synopsis"].startswith("他重组道路")
    assert "只修书名与简介" in captured["system"]
    assert "用户明确故事创意（唯一上位事实源）" in captured["prompt"]
    assert "主角修复废城流通循环" in captured["prompt"]
    assert "加入主角动作或明确结果" in captured["prompt"]
    assert "删除抽成与过路费主循环" in captured["prompt"]
    assert "修复轮次：4/4" in captured["prompt"]
    assert "严格控制在 80-130 个中文字符" in captured["prompt"]
    assert "任何单句不得超过45字" in captured["prompt"]
    assert "六种候选族" in captured["prompt"]
    assert "TITLE_HOOK_POWER_BELOW_FLOOR" in captured["prompt"]
    assert '"actual": 1.5' in captured["prompt"]
    assert '"required": 3.5' in captured["prompt"]
    assert "上一轮简介候选文本" in captured["prompt"]
    assert '"emotion_charge": 2.35' in captured["prompt"]
    assert '"emotion_charge": 3.0' in captured["prompt"]
    assert '"char_count": 203' in captured["prompt"]
    assert "must not enter prompt" not in captured["prompt"]
    assert "异世大陆" in captured["prompt"]
    assert "其他书的卖点" in captured["prompt"]
    assert "巨兽载城" not in captured["prompt"]
    assert "writing_profile" not in captured["prompt"]


@pytest.mark.asyncio
async def test_final_packaging_repair_keeps_multiple_synopsis_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_repair(*_args, **_kwargs):
        return (
            {
                "title_candidates": ["巨兽背上的七日城"],
                "synopsis_candidates": ["候选甲。", "候选乙。", "候选甲。"],
                "synopsis": "候选甲。",
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_repair)
    package, _ = await conception._repair_final_packaging_to_explicit_seed(
        object(),
        object(),
        concept_seed="林策经营七日一迁的移动驿城。",
        title="移动驿城",
        premise="林策必须在兽潮前迁城。",
        synopsis="旧简介。",
        report={"hard_conflicts": []},
        ctx={"language": "zh-CN", "genre": "异世大陆"},
        attempt=1,
        target_platform="fanqie",
    )

    assert package["synopsis_candidates"] == ["候选甲。", "候选乙。"]


@pytest.mark.asyncio
async def test_final_packaging_repair_has_structure_contract_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_repair(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return ({"title_candidates": [], "synopsis_candidates": []}, [])

    monkeypatch.setattr(conception, "_llm_call_json", fake_repair)
    await conception._repair_final_packaging_to_explicit_seed(
        object(),
        object(),
        concept_seed="收尸人纪白让死者完成最后一个动作。",
        title="引路尸",
        premise="仙门屠城后，纪白带孩子逃亡。",
        synopsis="旧简介。",
        report={"hard_conflicts": []},
        ctx={"language": "zh-CN", "genre": "东方玄幻"},
        attempt=1,
        max_attempts=4,
        target_platform="番茄小说",
    )

    assert "结构化修复要求" in captured["prompt"]
    assert "每个简介候选必须恰好用三步" in captured["prompt"]


def test_final_packaging_audit_rank_prevents_comedy_and_semantic_regression() -> None:
    incumbent_report = {"passed": False, "hard_conflicts": []}
    incumbent_quality = {
        "ranking_candidate": {
            "blocking_codes": ["BLURB_HOOK_STRENGTH_BELOW_FLOOR"],
            "metrics": {
                "title_total": 91.0,
                "blurb_total": 72.0,
                "blurb_dimensions": {"hook_strength": 2.7, "emotion_charge": 3.2},
            },
        }
    }
    regressed_report = {
        "passed": False,
        "hard_conflicts": [{"classification": "hard_conflict", "reason": "标题复活禁用卖点"}],
    }
    regressed_quality = {
        "ranking_candidate": {
            "blocking_codes": ["COMEDY_PACKAGING_REALIZATION_MISSING"],
            "metrics": {
                "title_total": 95.0,
                "blurb_total": 80.0,
                "blurb_dimensions": {"hook_strength": 4.5, "emotion_charge": 4.0},
            },
        }
    }

    assert conception._final_packaging_audit_rank(
        incumbent_report, incumbent_quality, {"avg_score": 8.0}
    ) > conception._final_packaging_audit_rank(
        regressed_report, regressed_quality, {"avg_score": 9.0}
    )


@pytest.mark.asyncio
async def test_final_packaging_audit_rechecks_quality_and_persona_after_late_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_seed_judge(*_args, **_kwargs):
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    async def fake_persona(*_args, **_kwargs):
        return (
            {
                "advisory_pass": False,
                "clicks": 0,
                "samples": 3,
                "avg_score": 3.0,
                "reasons": ["书名像设定集，看不出主角行动"],
            },
            "【模拟读者不点】书名像设定集，看不出主角行动。",
        )

    monkeypatch.setattr(conception, "_judge_final_packaging_seed_fidelity", fake_seed_judge)
    monkeypatch.setattr(conception, "_persona_click_advisory", fake_persona)

    report, quality, persona, feedback, _ = await conception._audit_final_packaging_candidate(
        object(),
        object(),
        concept_seed="主角重组流通规则，修复废城；禁止纯收费。",
        title="废城通衢录",
        premise="主角修复废城生存循环并争夺大陆交通主权。",
        synopsis=(
            "三条商路都绕着这座废城走。主角先修粮仓和道路，让居民活过第一个冬天，"
            "再联合异族商队对抗旧帝国的封锁网络。每次改写规则都把代价压回城内，"
            "逼他在效率、民生与盟约之间做选择。"
        ),
        tags=("穿越", "升级流", "经营", "基建"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        language="zh-CN",
        target_platform="fanqie",
        config={
            "meets_bar": {"title_min": 80, "blurb_min": 0},
            "persona_judge": {"block_below": True, "click_rate_min": 0.34},
        },
        ctx={"language": "zh-CN"},
        genre_intent_contract=None,
        writing_profile={},
        story_spine={},
    )

    assert report["passed"] is True
    assert report["quality_passed"] is False
    assert report["hard_conflicts"] == []
    assert quality["title_score"] < quality["title_min"]
    assert persona and persona["advisory_pass"] is False
    assert "书名像设定集" in feedback["persona_feedback"]
    sources = {item.get("source") for item in report["quality_issues"]}
    assert "deterministic_packaging_quality" in sources
    assert "persona_packaging_recheck" in sources


@pytest.mark.unit
async def test_final_packaging_audit_routes_explicit_comedy_to_ranking_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_seed_judge(*_args, **_kwargs):
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    async def fake_persona(*_args, **_kwargs):
        return ({"llm_used": True, "clicks": 2, "samples": 3, "avg_score": 7.0}, "")

    monkeypatch.setattr(conception, "_judge_final_packaging_seed_fidelity", fake_seed_judge)
    monkeypatch.setattr(conception, "_persona_click_advisory", fake_persona)

    report, quality, *_ = await conception._audit_final_packaging_candidate(
        object(),
        object(),
        concept_seed="异世大陆经营废城，显式加入喜剧引擎。",
        title="废城通衢录",
        premise="主角经营废城并争夺商路。",
        synopsis="废城濒临崩溃，主角接手后重开商路，让各族在危机中重新交易。",
        tags=("异世大陆", "经营", "喜剧"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        language="zh-CN",
        target_platform="fanqie",
        config={
            "ranking_candidate_packaging": {"enabled": True},
            "meets_bar": {"title_min": 0, "blurb_min": 0},
        },
        ctx={"language": "zh-CN"},
        genre_intent_contract=SimpleNamespace(
            explicit_enhancers=SimpleNamespace(effect_skills=("comedy_engine",)),
            allowed_modernity="genre_native",
            audience_orientation="male",
            channel_key="male",
        ),
        writing_profile={},
        story_spine={},
    )

    gate = quality["ranking_candidate"]
    assert gate["passed"] is False
    assert "PERSONA_EVIDENCE_UNAVAILABLE" not in gate["blocking_codes"]
    assert "COMEDY_PACKAGING_REALIZATION_MISSING" in gate["blocking_codes"]


@pytest.mark.unit
def test_production_story_appeal_config_enables_ranking_packaging_gate() -> None:
    from bestseller.services.story_appeal import load_story_appeal_config

    config = load_story_appeal_config()
    gate = config.get("ranking_candidate_packaging")

    assert isinstance(gate, dict)
    assert gate["enabled"] is True
    assert gate["persona_min_samples"] == 3
    assert gate["persona_min_clicks"] == 2


@pytest.mark.asyncio
async def test_final_packaging_ontology_scan_excludes_immutable_story_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services.genre_intent_contract import contract_from_selection

    async def fake_seed_judge(*_args, **_kwargs):
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    async def fake_persona(*_args, **_kwargs):
        return (
            {
                "advisory_pass": True,
                "clicks": 2,
                "samples": 3,
                "click_rate": 0.667,
                "llm_used": True,
                "avg_score": 7.0,
            },
            "",
        )

    monkeypatch.setattr(conception, "_judge_final_packaging_seed_fidelity", fake_seed_judge)
    monkeypatch.setattr(conception, "_persona_click_advisory", fake_persona)

    report, quality, *_ = await conception._audit_final_packaging_candidate(
        object(),
        object(),
        concept_seed="现代应急物流与城市运营负责人穿越异世大陆，重建边境废城。",
        title="废城重回大陆商路",
        premise="现代都市应急物流指挥中心负责人穿越后重建废城。",
        synopsis=(
            "三条商路都绕开这座废城，粮仓只剩七天口粮。他先让粮食进城，再让工坊开门，"
            "当众逼旧势力交出被扣的水源；可每赢一次，城里就多一个利益受损的敌人。"
            "等对手学会他的办法，另一座新城已经开始抢货、抢人、抢盟友。"
        ),
        tags=("穿越", "经营"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        language="zh-CN",
        target_platform="fanqie",
        config={
            "meets_bar": {"title_min": 0, "blurb_min": 0},
            # This regression isolates ontology scope. Packaging promotion has
            # independent coverage above and is explicitly disabled here.
            "ranking_candidate_packaging": {"enabled": False},
        },
        ctx={"language": "zh-CN"},
        genre_intent_contract=contract_from_selection(
            {"channel": "male", "genre": "xuanhuan", "sub_genre": "otherworld-continent"}
        ),
        writing_profile={"character": {"protagonist_archetype": "现代都市应急物流指挥中心负责人"}},
        story_spine={"who": "现代都市应急物流指挥中心负责人沈渡舟"},
    )

    assert report["passed"] is True
    assert quality["ontology_violations"] == []


@pytest.mark.asyncio
async def test_final_packaging_audit_records_overlong_blurb_without_blocking_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_seed_judge(*_args, **_kwargs):
        return ({"passed": True, "hard_conflicts": [], "repair_directives": []}, [])

    async def fake_persona(*_args, **_kwargs):
        return ({"advisory_pass": True, "llm_used": True}, "")

    monkeypatch.setattr(conception, "_judge_final_packaging_seed_fidelity", fake_seed_judge)
    monkeypatch.setattr(conception, "_persona_click_advisory", fake_persona)

    report, quality, *_ = await conception._audit_final_packaging_candidate(
        object(),
        object(),
        concept_seed="主角穿越废城并重建生存循环。",
        title="废城重回大陆商路",
        premise="主角重建废城。",
        synopsis="他先让粮食进城，再让工坊开门。" * 30,
        tags=("穿越", "经营"),
        genre="xuanhuan",
        sub_genre="otherworld-continent",
        language="zh-CN",
        target_platform="fanqie",
        config={"meets_bar": {"title_min": 0, "blurb_min": 0}},
        ctx={"language": "zh-CN"},
        genre_intent_contract=None,
        writing_profile={},
        story_spine={},
    )

    assert report["passed"] is True
    assert report["quality_passed"] is False
    assert report["hard_conflicts"] == []
    assert quality["blurb_length"] > quality["blurb_length_band"][1]
    assert "deterministic_packaging_length" in {
        item.get("source") for item in report["quality_issues"]
    }


def test_final_packaging_fidelity_gate_runs_after_all_late_mutations() -> None:
    entry = inspect.getsource(conception.run_conception_pipeline)
    assert "_complete_conception_candidate(" in entry
    assert "_review_conception_candidate(" in inspect.getsource(conception._complete_conception_candidate)
    source = inspect.getsource(conception._review_conception_candidate)
    ontology_repair = source.index("_repair_final_ontology_drift(")
    final_gate = source.index("_audit_final_packaging_candidate(", ontology_repair)
    result_return = source.index("return ConceptionResult(")

    assert ontology_repair < final_gate < result_return
    assert "FINAL_PACKAGING_SEED_FIDELITY_UNRESOLVED" in source[final_gate:result_return]
    final_region = source[final_gate:result_return]
    assert final_region.count("_audit_final_packaging_candidate(") >= 2
    assert "_arbitrate_final_packaging_seed_fidelity_report(" in inspect.getsource(
        conception._audit_final_packaging_candidate
    )
    assert "_package_exc.conception_log = list(conception_log)" in final_region
    assert 'story_appeal_report["persona_judge"]' in final_region


def test_draft_delivery_does_not_retry_nonblocking_packaging_polish() -> None:
    assert conception._final_packaging_requires_repair(
        {"passed": True, "non_blocking_repair_directives": ["让简介更吸引人"]},
        non_blocking_polish_attempted=False,
    ) is False
    assert conception._final_packaging_requires_repair(
        {"passed": False, "hard_conflicts": [{"reason": "核心能力被替换"}]},
        non_blocking_polish_attempted=False,
    ) is True


def test_final_packaging_approval_is_bound_to_the_reviewed_text_version() -> None:
    report = {
        "passed": True,
        "audit_identity": conception._final_packaging_audit_identity(
            title="已审标题",
            synopsis="已审简介。",
        ),
    }
    assert conception._final_packaging_audit_matches_text(
        report, title="已审标题", synopsis="已审简介。"
    )
    assert not conception._final_packaging_audit_matches_text(
        report, title="重试标题", synopsis="已审简介。"
    )
    assert conception._final_packaging_requires_repair(
        report,
        non_blocking_polish_attempted=False,
        title="重试标题",
        synopsis="已审简介。",
    )


def test_missing_final_packaging_receipt_is_unreviewed_when_current_text_is_supplied() -> None:
    assert conception._final_packaging_requires_repair(
        {"passed": True},
        non_blocking_polish_attempted=False,
        title="当前标题",
        synopsis="当前简介。",
    )


def test_evaluator_evidence_failure_does_not_trigger_story_rewrite():
    report = {"passed": False, "evaluator_error": "canonical_claim_evidence_incomplete",
              "hard_conflicts": [], "invalid_claim_indexes": [1]}
    assert not conception._final_packaging_requires_repair(
        report, non_blocking_polish_attempted=False,
    )
    # An unfinished evidence check never approves the package.
    assert report["passed"] is False


@pytest.mark.asyncio
async def test_incomplete_canonical_evidence_uses_existing_arbitration(monkeypatch):
    calls = []
    async def initial(*args, **kwargs):
        return {"passed": False, "hard_conflicts": [], "claims": [{"status": "supported"}],
                "evaluator_error": "canonical_claim_evidence_incomplete"}, []
    async def arbitration(*args, **kwargs):
        calls.append(kwargs["challenged_report"])
        return {"passed": True, "hard_conflicts": []}, []
    async def persona(*args, **kwargs):
        return None, ""
    monkeypatch.setattr(conception, "_judge_final_packaging_seed_fidelity", initial)
    monkeypatch.setattr(conception, "_arbitrate_final_packaging_seed_fidelity_report", arbitration)
    monkeypatch.setattr(conception, "_persona_click_advisory", persona)
    report, *_ = await conception._audit_final_packaging_candidate(
        object(), object(), concept_seed="", title="暴雨前", premise="工人保住线路",
        synopsis="暴雨将至，维修工赶往旧站抢修线路。", tags=(), genre="现实", sub_genre="现实",
        language="zh-CN", target_platform=None, config={}, ctx={}, genre_intent_contract=None,
        writing_profile={}, story_spine={}, canonical_body={"premise": "工人保住线路"},
    )
    assert len(calls) == 1
    assert report["arbitration_applied"] is True
    assert report["passed"] is True


@pytest.mark.parametrize("reason",[
    "这会将周夏写成男性，破坏父女关系结构。这是最终简介中实际出现的直接改写，不是对设定的有意省略。",
    "这并非普通省略，而是把女儿改成儿子的直接冲突。",
    "属于直接反转事实，而非仅仅未提及背景。",
    "不能认为不构成硬冲突，简介把女儿直接写成儿子。",
])
def test_negated_omission_or_acquittal_never_erases_confirmed_direct_conflict(reason):
    report=conception._normalize_final_packaging_semantic_report({"passed":False,"hard_conflicts":[
        {"classification":"hard_conflict","scope":"synopsis","fact":"周夏是女儿","generated":"父子修机","reason":reason}]})
    assert report['passed'] is False and len(report['hard_conflicts'])==1
    assert report['hard_conflicts'][0]['generated']=='父子修机'


def test_affirmative_omission_in_its_own_clause_still_remains_a_warning():
    report=conception._normalize_final_packaging_semantic_report({"passed":False,"hard_conflicts":[
        {"classification":"hard_conflict","scope":"synopsis","reason":"这里不是直接改写事实；只是省略了背景细节。"}]})
    assert report['passed'] is True and not report['hard_conflicts']
    assert report['upstream_consistency_warnings']
