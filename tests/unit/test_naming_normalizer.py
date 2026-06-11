from __future__ import annotations

import pytest

from bestseller.services.naming_normalizer import (
    MAX_DISTINCT_ROGUE_NAMES,
    normalize_out_of_pool_names,
)

pytestmark = pytest.mark.unit

POOL = ["陆沉", "苏瑶", "裴寂", "钱保民"]


def test_pool_variant_typo_is_mapped_back_to_canonical_name() -> None:
    text = "陆尘推开值班室的门。陆尘的工牌还挂在胸口。"
    result = normalize_out_of_pool_names(
        text,
        rogue_names={"陆尘": 2},
        allowed_names=POOL,
    )

    assert result is not None
    assert result.substitutions == {"陆尘": "陆沉"}
    assert "陆尘" not in result.text
    assert result.text.count("陆沉") == 2


def test_invented_walkon_becomes_generic_referent() -> None:
    text = "云诀拦在门口。云诀冷笑一声，云诀伸手要工牌。"
    result = normalize_out_of_pool_names(
        text,
        rogue_names={"云诀": 3},
        allowed_names=POOL,
    )

    assert result is not None
    assert "云诀" not in result.text
    referent = result.substitutions["云诀"]
    assert referent
    assert result.text.count(referent) >= 3


def test_two_walkons_get_distinct_referents() -> None:
    text = "云诀和林奚对视一眼。云诀先开口，林奚跟着冷笑。"
    result = normalize_out_of_pool_names(
        text,
        rogue_names={"云诀": 2, "林奚": 2},
        allowed_names=POOL,
    )

    assert result is not None
    assert result.substitutions["云诀"] != result.substitutions["林奚"]
    assert "云诀" not in result.text
    assert "林奚" not in result.text


def test_ambiguous_pool_variant_falls_to_generic_referent() -> None:
    # Both 陆沉 and 陆沉 variants: build a pool with two same-surname,
    # same-length candidates so the variant match is ambiguous.
    pool = ["陆沉", "陆波", "苏瑶"]
    text = "陆海站起身。陆海拍了拍灰。"
    result = normalize_out_of_pool_names(
        text,
        rogue_names={"陆海": 2},
        allowed_names=pool,
    )

    assert result is not None
    # Ambiguous (shares surname+length with 陆沉 and 陆波) → generic referent.
    assert result.substitutions["陆海"] not in pool


def test_too_many_rogue_names_refuses_normalization() -> None:
    rogue = {f"云{ch}": 2 for ch in "一二三四"[: MAX_DISTINCT_ROGUE_NAMES + 1]}
    result = normalize_out_of_pool_names(
        "x",
        rogue_names=rogue,
        allowed_names=POOL,
    )

    assert result is None


def test_non_chinese_language_is_skipped() -> None:
    result = normalize_out_of_pool_names(
        "John walked in.",
        rogue_names={"John": 2},
        allowed_names=["Jane"],
        language="en-US",
    )

    assert result is None


def test_longer_rogue_name_substituted_before_prefix() -> None:
    pool = ["陆沉"]
    text = "云诀儿看着云诀儿的影子。"
    result = normalize_out_of_pool_names(
        text,
        rogue_names={"云诀儿": 2},
        allowed_names=pool,
    )

    assert result is not None
    assert "云诀" not in result.text
