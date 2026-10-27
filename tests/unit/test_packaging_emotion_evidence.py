from __future__ import annotations

# ruff: noqa: RUF001 — Chinese evidence fixtures are intentional.
import hashlib
from uuid import UUID, uuid4

import pytest

from bestseller.services.packaging_emotion_evidence import (
    corroborate_emotion_evidence,
    render_emotion_evidence_messages,
    validate_emotion_evidence,
)


def test_render_prompt_defines_four_categories_without_score_or_pass() -> None:
    system, user = render_emotion_evidence_messages("纪白触碰尸体，尸体竟侧身让路。")

    assert "两难" in user and "切肤" in user and "迫近" in user and "骇异" in user
    assert "只输出JSON" in system
    assert "分数" not in system
    assert "通过" not in user


def test_validate_accepts_literal_scene_evidence_and_scores_front_loaded_categories() -> None:
    synopsis = (
        "“烧了它，否则你连尸都收不成。”追兵刀锋抵喉。"
        "纪白垂眼，指尖触到亡者衣角——那具尸体竟侧身，为身后孩童让出半步。"
    )
    report = validate_emotion_evidence(
        {
            "claims": [
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "烧了它，否则你连尸都收不成。”追兵刀锋抵喉",
                    "elements": {
                        "threat_action_quote": "追兵刀锋抵喉",
                        "target_or_deadline_quote": "烧了它",
                    },
                    "reason": "威胁已落实到眼前的烧铺行动",
                },
                {
                    "category": "骇异",
                    "situation": "violated_expectation",
                    "evidence_quote": "那具尸体竟侧身，为身后孩童让出半步",
                    "elements": {"abnormal_action_quote": "尸体竟侧身", "baseline_quote": "尸体"},
                    "reason": "已死尸体出现可见的反常动作",
                },
            ]
        },
        synopsis,
    )

    assert report["valid"] is True
    assert report["categories"] == ["迫近", "骇异"]
    assert report["front_loaded"] is True
    assert report["score"] == pytest.approx(3.8)


def test_validate_rejects_category_situation_mismatch() -> None:
    synopsis = "危机、绝境、生死、倒计时，情况非常危险。"
    report = validate_emotion_evidence(
        {
            "claims": [
                {
                    "category": "迫近",
                    "situation": "choice_and_loss",
                    "evidence_quote": synopsis,
                    "elements": {
                        "choice_quote": "烧掉自己的铺子，还是",
                        "personal_loss_quote": "孩子和证据一起死去",
                    },
                    "reason": "有很多危险词",
                }
            ]
        },
        synopsis,
    )

    assert report["valid"] is False
    assert report["score"] == 1.5
    assert report["rejected"][0]["reason"] == "invalid_situation_for_category"


def test_validate_rejects_unknown_category() -> None:
    synopsis = "他是收尸人，每天整理尸体，按时收工。"
    report = validate_emotion_evidence(
        {
            "claims": [
                {
                    "category": "职业感",
                    "situation": "active_threat",
                    "evidence_quote": "他是收尸人，每天整理尸体",
                    "reason": "身份很特别",
                }
            ]
        },
        synopsis,
    )

    assert report["valid"] is False
    assert report["categories"] == []


def test_validate_requires_category_specific_literal_elements() -> None:
    synopsis = "天亮前追兵会到，纪白必须带孩子穿过巷口。"
    report = validate_emotion_evidence(
        {
            "claims": [
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": synopsis,
                    "elements": {
                        "threat_action_quote": "追兵会到",
                        "target_or_deadline_quote": "不存在的期限",
                    },
                    "reason": "缺少逐字目标证据",
                }
            ]
        },
        synopsis,
    )

    assert report["valid"] is False
    assert report["rejected"][0]["reason"] == "invalid_category_elements"


def test_validate_accepts_choice_and_personal_irreversible_loss() -> None:
    synopsis = "纪白只能烧掉自己的铺子，还是让孩子和证据一起死去？"
    report = validate_emotion_evidence(
        {
            "claims": [
                {
                    "category": "两难",
                    "situation": "choice_and_loss",
                    "evidence_quote": synopsis,
                    "elements": {
                        "choice_quote": "烧掉自己的铺子，还是",
                        "personal_loss_quote": "孩子和证据一起死去",
                    },
                    "reason": "两个选择对应铺子与孩子、证据的不可逆损失",
                }
            ]
        },
        synopsis,
    )

    assert report["valid"] is True
    assert report["categories"] == ["两难"]


def test_validate_accepts_specific_relation_loss_for_flesh_wound() -> None:
    synopsis = "孩子落入追兵手里就会死，纪白只能护住他。"
    report = validate_emotion_evidence(
        {
            "claims": [
                {
                    "category": "切肤",
                    "situation": "relationship_stakes",
                    "evidence_quote": synopsis,
                    "elements": {"relationship_quote": "孩子", "stakes_quote": "就会死"},
                    "reason": "具体孩子的死亡风险迫使主角承担保护选择",
                }
            ]
        },
        synopsis,
    )

    assert report["valid"] is True
    assert report["categories"] == ["切肤"]


def test_validate_rejects_noncontinuous_long_quote_and_cross_category_reuse() -> None:
    synopsis = "那具尸体竟侧身，为孩子让路。追兵逼近，纪白烧铺逃走。"
    report = validate_emotion_evidence(
        {
            "claims": [
                {
                    "category": "骇异",
                    "situation": "violated_expectation",
                    "evidence_quote": "那具尸体竟侧身，为孩子让路",
                    "elements": {"abnormal_action_quote": "尸体竟侧身", "baseline_quote": "尸体"},
                    "reason": "尸体出现可见异常动作",
                },
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "那具尸体竟侧身，为孩子让路",
                    "elements": {
                        "threat_action_quote": "尸体竟侧身",
                        "target_or_deadline_quote": "为孩子让路",
                    },
                    "reason": "错误复用同一段原文",
                },
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "那具尸体竟侧身，为孩子让路。追兵逼近，纪白烧铺逃走。" * 3,
                    "elements": {
                        "threat_action_quote": "追兵逼近",
                        "target_or_deadline_quote": "烧铺逃走",
                    },
                    "reason": "跨全文拼接",
                },
            ]
        },
        synopsis,
    )

    assert report["categories"] == ["骇异"]
    assert {item["reason"] for item in report["rejected"]} >= {
        "duplicate_category_or_quote",
        "quote_not_continuous_or_too_long",
    }


def test_corroborate_requires_two_distinct_matching_receipts_and_overlapping_quotes() -> None:
    synopsis = "追兵刀锋抵喉，纪白烧掉铺子带孩子逃走。"
    first_id, second_id = uuid4(), uuid4()
    candidate_hash = hashlib.sha256(synopsis.encode("utf-8")).hexdigest()
    reviews = [
        {
            "llm_run_id": str(first_id),
            "candidate_hash": candidate_hash,
            "claims": [
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "追兵刀锋抵喉，纪白烧掉铺子",
                    "elements": {
                        "threat_action_quote": "追兵刀锋抵喉",
                        "target_or_deadline_quote": "烧掉铺子",
                    },
                    "reason": "具体追兵威胁迫使烧铺行动",
                }
            ],
        },
        {
            "llm_run_id": str(second_id),
            "candidate_hash": candidate_hash,
            "claims": [
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "刀锋抵喉，纪白烧掉铺子",
                    "elements": {
                        "threat_action_quote": "刀锋抵喉",
                        "target_or_deadline_quote": "烧掉铺子",
                    },
                    "reason": "同一威胁与行动被独立复核",
                }
            ],
        },
    ]
    report = corroborate_emotion_evidence(reviews, synopsis, [first_id, second_id])

    assert report["valid"] is True
    assert report["corroborated"] is True
    assert report["categories"] == ["迫近"]
    assert report["score"] == pytest.approx(2.95)
    assert report["receipt_ids"] == [str(first_id), str(second_id)]
    assert report["candidate_hash"] == candidate_hash


def test_corroborate_rejects_same_receipt_invalid_receipt_and_nonoverlap() -> None:
    synopsis = "追兵刀锋抵喉，纪白烧掉铺子带孩子逃走。"
    first_id, second_id = uuid4(), uuid4()
    candidate_hash = hashlib.sha256(synopsis.encode("utf-8")).hexdigest()
    base = {
        "category": "迫近",
        "situation": "active_threat",
        "reason": "威胁迫使行动",
        "elements": {"threat_action_quote": "追兵刀锋", "target_or_deadline_quote": "抵喉"},
        "candidate_hash": candidate_hash,
    }
    reviews = [
        {
            "llm_run_id": str(first_id),
            "candidate_hash": candidate_hash,
            "claims": [{**base, "evidence_quote": "追兵刀锋抵喉"}],
        },
        {
            "llm_run_id": str(second_id),
            "candidate_hash": candidate_hash,
            "claims": [{**base, "evidence_quote": "烧掉铺子带孩子逃走"}],
        },
    ]

    assert corroborate_emotion_evidence(reviews, synopsis, [first_id, first_id])["valid"] is False
    assert (
        corroborate_emotion_evidence(reviews, synopsis, [UUID(int=0), second_id])["valid"] is False
    )
    report = corroborate_emotion_evidence(reviews, synopsis, [first_id, second_id])
    assert report["valid"] is False
    assert report["score"] == 1.5


def test_corroborate_does_not_treat_one_review_as_global_pass() -> None:
    synopsis = "追兵刀锋抵喉，纪白烧掉铺子带孩子逃走。"
    review = {
        "llm_run_id": str(uuid4()),
        "candidate_hash": hashlib.sha256(synopsis.encode("utf-8")).hexdigest(),
        "claims": [
            {
                "category": "迫近",
                "situation": "active_threat",
                "evidence_quote": "追兵刀锋抵喉，纪白烧掉铺子",
                "elements": {
                    "threat_action_quote": "追兵刀锋抵喉",
                    "target_or_deadline_quote": "烧掉铺子",
                },
                "reason": "威胁迫使行动",
            }
        ],
    }

    report = corroborate_emotion_evidence([review], synopsis, [review["llm_run_id"]])
    assert report["valid"] is False
    assert report["score"] == 1.5


def test_corroborate_rejects_candidate_hash_mismatch_and_tiny_overlap() -> None:
    synopsis = "追兵刀锋抵喉，纪白烧掉铺子带孩子逃走。"
    first_id, second_id = uuid4(), uuid4()
    candidate_hash = hashlib.sha256(synopsis.encode("utf-8")).hexdigest()
    reviews = [
        {
            "llm_run_id": str(first_id),
            "candidate_hash": "0" * 64,
            "claims": [
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "追兵",
                    "elements": {"threat_action_quote": "追兵", "target_or_deadline_quote": "追兵"},
                    "reason": "威胁片段",
                }
            ],
        },
        {
            "llm_run_id": str(second_id),
            "candidate_hash": candidate_hash,
            "claims": [
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "兵刀",
                    "elements": {"threat_action_quote": "兵刀", "target_or_deadline_quote": "兵刀"},
                    "reason": "相邻威胁片段",
                }
            ],
        },
    ]
    assert corroborate_emotion_evidence(reviews, synopsis, [first_id, second_id])["valid"] is False

    reviews[0]["candidate_hash"] = candidate_hash
    report = corroborate_emotion_evidence(reviews, synopsis, [first_id, second_id])
    assert report["valid"] is False
    assert report["rejected"][0]["reason"] == "no_overlapping_same_category_claim"


def test_corroborate_rejects_merged_overlap_across_different_categories() -> None:
    synopsis = "尸体竟侧身让路，追兵刀锋抵喉，纪白烧铺逃走。"
    first_id, second_id = uuid4(), uuid4()
    candidate_hash = hashlib.sha256(synopsis.encode("utf-8")).hexdigest()
    reviews = [
        {
            "llm_run_id": str(first_id),
            "candidate_hash": candidate_hash,
            "claims": [
                {
                    "category": "骇异",
                    "situation": "violated_expectation",
                    "evidence_quote": "尸体竟侧身让路，追兵",
                    "elements": {"abnormal_action_quote": "尸体竟侧身", "baseline_quote": "尸体"},
                    "reason": "异常动作片段",
                }
            ],
        },
        {
            "llm_run_id": str(second_id),
            "candidate_hash": candidate_hash,
            "claims": [
                {
                    "category": "迫近",
                    "situation": "active_threat",
                    "evidence_quote": "路，追兵刀锋抵喉",
                    "elements": {
                        "threat_action_quote": "追兵刀锋",
                        "target_or_deadline_quote": "抵喉",
                    },
                    "reason": "威胁片段",
                }
            ],
        },
    ]
    report = corroborate_emotion_evidence(reviews, synopsis, [first_id, second_id])
    assert report["valid"] is False
    assert report["rejected"][0]["reason"] == "no_overlapping_same_category_claim"
