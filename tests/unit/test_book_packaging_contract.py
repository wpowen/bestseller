# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from bestseller.services.book_packaging_contract import (
    build_book_packaging_contract,
    canonical_hash,
    get_active_contract,
    supersede_contract_revision,
)

pytestmark = pytest.mark.unit


def _contract():
    variants = [
        {
            "variant_id": "fanqie-long",
            "text": "一座荒城，一块会报价的界碑，商洛要让万族先学会排队交费。",
            "promise_refs": ["arch:city-growth", "effect:comedy-engine"],
        }
    ]
    from bestseller.services.book_packaging_contract import IntroVariant

    variant_hash = IntroVariant.model_validate(variants[0]).content_hash
    return build_book_packaging_contract(
        project_id="book-1",
        title="荒城通关簿",
        title_promise="用现代通行管理经营异界荒城，并在万族博弈中建立新秩序",
        protagonist="商洛",
        situation="接手被哥布林占据的废城",
        action="经营关城并重写通行规则",
        result="从废城走向万族交通中枢",
        genre="异世界经营",
        tags=("穿越", "升级流", "金手指", "经营", "喜剧"),
        effect_skills=("comedy_engine",),
        architecture_refs=("arch:city-growth", "arch:trade-war"),
        intro_variants=variants,
        surfaces=(
            {
                "target_platform": "fanqie",
                "listing_surface": "long-form",
                "active_intro_variant_id": "fanqie-long",
                "active_intro_variant_hash": variant_hash,
            },
        ),
    )


def test_active_packaging_payload_is_hash_bound_to_target_surface() -> None:
    contract = _contract()

    payload = get_active_contract(contract, "fanqie", "long-form")

    assert payload["title"] == "荒城通关簿"
    assert payload["active_intro_variant_id"] == "fanqie-long"
    assert payload["contract_hash"] == contract.contract_hash
    assert payload["tags"] == list(contract.tags)
    assert payload["effect_skills"] == ["comedy_engine"]
    assert payload["architecture_refs"] == ["arch:city-growth", "arch:trade-war"]
    assert "logline" not in payload or payload["logline"] is None
    assert payload["active_payload_hash"] == canonical_hash(
        {key: value for key, value in payload.items() if key != "active_payload_hash"}
    )


def test_placeholder_title_and_stale_variant_selector_are_rejected() -> None:
    valid = _contract()
    surface = valid.surfaces[0].model_copy(update={"active_intro_variant_hash": "stale"})

    with pytest.raises(ValueError, match="TITLE_PLACEHOLDER_OR_INTERNAL_NAME"):
        build_book_packaging_contract(
            **{
                **valid.model_dump(
                    mode="python", exclude={"schema_version", "status", "invalidates_evidence"}
                ),
                "title": "新书01",
            }
        )

    stale = valid.model_copy(update={"surfaces": (surface,)})
    with pytest.raises(ValueError, match="PACKAGING_VARIANT_HASH_MISMATCH"):
        get_active_contract(stale, "fanqie", "long-form")


def test_selector_change_creates_new_revision_and_invalidates_old_evidence() -> None:
    previous = _contract()
    revised = supersede_contract_revision(previous, title="万族关城录")

    assert revised.revision == previous.revision + 1
    assert revised.supersedes_contract_hash == previous.contract_hash
    assert revised.invalidates_evidence is True
    assert revised.contract_hash != previous.contract_hash
