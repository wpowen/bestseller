from __future__ import annotations

import pytest

from bestseller.services.genre_intent_contract import (
    build_genre_intent_contract,
    contract_from_payload,
    contract_from_selection,
    detect_genre_native_ontology_violations,
)
from bestseller.services.genre_taxonomy import resolve_selection
from bestseller.services.story_enhancers import StoryEnhancerSelection

pytestmark = pytest.mark.unit


def test_plain_xianxia_contract_owns_native_prompt_pack() -> None:
    contract = contract_from_selection(
        {"channel": "male", "genre": "xianxia", "sub_genre": None, "tags": []}
    )

    assert contract.genre_key == "xianxia"
    assert contract.sub_genre_key is None
    assert contract.prompt_pack_key == "xianxia-upgrade-core"
    assert contract.allowed_modernity == "genre_native"
    assert contract.explicit_enhancers.is_default()


def test_urban_cultivation_contract_explicitly_allows_modern_elements() -> None:
    contract = contract_from_selection(
        {
            "channel": "male",
            "genre": "xianxia",
            "sub_genre": "urban-cultivation",
            "tags": [],
        }
    )

    assert contract.sub_genre_key == "urban-cultivation"
    assert contract.prompt_pack_key == "urban-cultivation-2.0"
    assert contract.allowed_modernity == "modern"


def test_contract_hash_is_stable_and_changes_when_user_intent_changes() -> None:
    plain = contract_from_selection({"genre": "xianxia"})
    urban = contract_from_selection(
        {"genre": "xianxia", "sub_genre": "urban-cultivation"}
    )

    assert plain.contract_hash() == plain.contract_hash()
    assert plain.contract_hash() != urban.contract_hash()


def test_native_contract_detects_high_signal_modern_ontology_leakage() -> None:
    contract = contract_from_selection({"genre": "xianxia", "sub_genre": "xianxia"})
    assert detect_genre_native_ontology_violations("他拿出手机，走进写字楼。", contract)
    assert detect_genre_native_ontology_violations("他把尸体送进殡仪馆，再安排入殓。", contract)
    assert not detect_genre_native_ontology_violations("他在宗门广场拔剑。", contract)


def test_explicit_enhancers_are_preserved_but_not_inferred() -> None:
    selection = StoryEnhancerSelection(brainhole=True, effect_skills=("twist_reversal_engine",))
    contract = build_genre_intent_contract(
        resolve_selection(None, "xianxia", None, []),
        enhancers=selection,
    )

    assert contract.explicit_enhancers.brainhole is True
    assert contract.explicit_enhancers.effect_skills == ("twist_reversal_engine",)


# ── allowed_modernity 数据驱动回归网 (2026-07-15) ───────────────────────────
# 曾是 2 行硬编码,只覆盖 都市/urban-cultivation ⇒ 其余 19 个题材全 genre_native:
# 悬疑推理书被禁用自己的核心词汇(法医/尸检),现代言情/现实(行业职场)/游戏竞技/末世
# 书一写 手机/写字楼/职场 就被构思期最终绊线 raise 打死。


@pytest.mark.unit
def test_contemporary_genres_may_use_contemporary_vocabulary() -> None:
    """当代题材必须能写当代词——否则用户选了就必死。"""

    modern_premise = "林岚在写字楼的职场里被上司打压，她用一部手机录下证据。"
    for genre in ("行业职场", "职场情感", "都市", "游戏竞技"):
        contract = contract_from_selection(
            {"channel": "general", "genre": genre, "sub_genre": None, "tags": []}
        )
        assert contract.allowed_modernity in ("modern", "hybrid"), genre
        assert detect_genre_native_ontology_violations(modern_premise, contract) == (), genre


@pytest.mark.unit
def test_detective_genre_may_use_its_own_forensic_vocabulary() -> None:
    """悬疑推理的 法医/尸检/停尸房 是题材核心词,不是"现代漂移"。"""

    contract = contract_from_selection(
        {"channel": "male", "genre": "悬疑推理", "sub_genre": None, "tags": []}
    )
    forensic = "法医在停尸房完成尸检，确认死者身份。"
    assert detect_genre_native_ontology_violations(forensic, contract) == ()


@pytest.mark.unit
def test_contract_from_payload_round_trips_valid_contract() -> None:
    contract = contract_from_selection(
        {"channel": "male", "genre": "玄幻", "sub_genre": "东方玄幻", "tags": []},
        audience_orientation="male",
        tone_preference="light",
        enhancers=StoryEnhancerSelection(brainhole=True, cost_style="minimal"),
    )
    reloaded = contract_from_payload({"genre_intent_contract": contract.model_dump(mode="json")})
    assert reloaded is not None
    assert reloaded.audience_orientation == "male"
    assert reloaded.tone_preference == "light"
    assert reloaded.explicit_enhancers.brainhole is True
    assert reloaded.explicit_enhancers.cost_style == "minimal"


@pytest.mark.unit
def test_contract_from_payload_invalid_contract_logs_and_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    """A corrupt persisted contract must be observable (P11): log a warning,
    never silently drop the whole creation intent with no trace."""
    import logging

    with caplog.at_level(logging.WARNING, logger="bestseller.services.genre_intent_contract"):
        result = contract_from_payload({"genre_intent_contract": {"genre_key": 42}})
    assert result is None
    assert any(
        "genre_intent_contract present but invalid" in r.message for r in caplog.records
    )


@pytest.mark.unit
def test_contract_from_payload_absent_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    """No contract in the payload is a legitimate state (legacy/self-heal) and
    must NOT log as corruption."""
    import logging

    with caplog.at_level(logging.WARNING, logger="bestseller.services.genre_intent_contract"):
        assert contract_from_payload({}) is None
    assert not caplog.records


@pytest.mark.unit
def test_native_genre_still_blocks_genuine_modern_drift() -> None:
    """放宽当代题材的同时,原生题材(玄幻/仙侠/古言)的漂移必须照样拦住。"""

    for genre in ("玄幻", "仙侠", "古代言情"):
        contract = contract_from_selection(
            {"channel": None, "genre": genre, "sub_genre": None, "tags": []}
        )
        assert contract.allowed_modernity == "genre_native", genre
        hits = detect_genre_native_ontology_violations(
            "他掏出手机，在写字楼的职场会议上发微信。", contract
        )
        assert hits, f"{genre} 应拦住现代漂移"