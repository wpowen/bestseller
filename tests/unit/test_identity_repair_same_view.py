"""身份锁修复器与验收器同视图（2026-08-19《摔下山三次》奠基失败定罪）。

真机链路：cast_spec 的配角表里出现「名字作键」条目
（{"净云宗少主·霍惊檐": {...}}），验收器经 parse_cast_spec_input 归一后
解包出该角色并要求 gender/pronouns 锁；修复器却在原始 dict 上迭代，
character.get("name") 为空直接跳过——永远修不到验收器看得到的条目，
FOUNDATION_IDENTITY_GENDER/PRONOUN_MISSING 毙掉整个奠基。
「声明带括号别名」匹配盲区同族：两把尺子不同视图。

修：修复器先走验收器同一套归一（fail-open），再做锁回填。
"""

from __future__ import annotations

import pytest

from bestseller.services.narrative_contracts import (
    repair_legacy_foundation_identity_locks,
    validate_foundation_identity_contract,
)

pytestmark = pytest.mark.unit


def _cast_with_name_keyed_supporting() -> dict:
    return {
        "protagonist": {
            "name": "洛残山",
            "role": "protagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
        "antagonist": {
            "name": "霍执事",
            "role": "antagonist",
            "gender": "male",
            "pronoun_set_zh": "他",
            "pronoun_set_en": "he/him",
        },
        "supporting_cast": [
            # 真机形状：名字作键、锁字段全缺
            {"净云宗少主·霍惊檐": {"role": "rival", "description": "围山执事之子"}},
            {
                "name": "赶山派头目·柳压枝",
                "role": "supporting",
                # gender/pronouns 全缺
            },
        ],
    }


def test_repair_fills_locks_on_validator_view():
    repaired, count = repair_legacy_foundation_identity_locks(
        _cast_with_name_keyed_supporting(),
        allow_unreliable_defaults=True,
    )
    assert count > 0, "名字作键与缺锁条目都必须被回填"
    report = validate_foundation_identity_contract(repaired)
    blocking = [
        v
        for v in report.violations
        if v.code
        in ("FOUNDATION_IDENTITY_GENDER_MISSING", "FOUNDATION_IDENTITY_PRONOUN_MISSING")
    ]
    assert blocking == [], f"修复后不得再有身份锁缺失：{[v.location for v in blocking]}"


def test_unparseable_payload_fails_open():
    # 解析失败必须回退原始形状继续（legacy resume 场景），不许 raise
    repaired, _ = repair_legacy_foundation_identity_locks(
        {"supporting_cast": "not-a-list"},
        allow_unreliable_defaults=True,
    )
    assert repaired is not None
