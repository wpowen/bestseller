from __future__ import annotations

import pytest

from bestseller.services.narrative_contracts import (
    IDENTITY_POLICY_VERSION,
    repair_commercial_zh_identity_policy,
    validate_commercial_zh_identity_policy,
)

pytestmark = pytest.mark.unit


def _cast_spec(*, meta: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "protagonist": {
            "name": "陆岑",
            "role": "protagonist",
            "gender": "nonbinary",
            "pronoun_set_zh": "ta",
            "pronoun_set_en": "they/them",
            "description": "规则生存案卷的调查者。",
        },
        "supporting_cast": [],
    }
    if meta is not None:
        payload["_meta"] = meta
    return payload


def test_zh_cast_spec_nonbinary_ta_cannot_pass_policy() -> None:
    report = validate_commercial_zh_identity_policy(_cast_spec(), language="zh-CN")

    codes = {violation.code for violation in report.violations}
    assert report.passed is False
    assert "ZH_PINYIN_TA_PRONOUN_FORBIDDEN" in codes
    assert "ZH_NONBINARY_DEFAULT_FORBIDDEN" in codes


def test_old_cast_spec_without_identity_policy_version_cannot_be_reused() -> None:
    report = validate_commercial_zh_identity_policy(
        {
            **_cast_spec(),
            "protagonist": {
                "name": "陆岑",
                "role": "protagonist",
                "gender": "male",
                "pronoun_set_zh": "他",
                "pronoun_set_en": "he/him",
            },
        },
        language="zh-CN",
        require_policy_version=True,
    )

    assert report.passed is False
    assert {violation.code for violation in report.violations} == {
        "IDENTITY_POLICY_VERSION_MISSING"
    }


def test_repair_zh_cast_spec_normalizes_ta_and_stamps_policy() -> None:
    repaired, repair_count = repair_commercial_zh_identity_policy(
        _cast_spec(),
        language="zh-CN",
    )

    assert repair_count >= 3
    assert isinstance(repaired, dict)
    protagonist = repaired["protagonist"]
    assert isinstance(protagonist, dict)
    assert protagonist["gender"] == "male"
    assert protagonist["pronoun_set_zh"] == "他"
    assert protagonist["pronoun_set_en"] == "he/him"
    assert (
        repaired["_meta"]["policy_versions"]["identity_policy"]
        == IDENTITY_POLICY_VERSION
    )
