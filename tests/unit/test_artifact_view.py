"""L1 unit tests for per-type artifact view-models."""

from __future__ import annotations

# ruff: noqa: E501, RUF001 — CJK fixtures are wide by display width.
import pytest

from bestseller.services.artifact_view import build_artifact_view


def _kinds(view: dict) -> list[str]:
    return [s["kind"] for s in view["sections"]]


def _labels(view: dict) -> list[str]:
    return [s["label"] for s in view["sections"]]


@pytest.mark.unit
def test_world_spec_yields_ladder_and_rule_cards() -> None:
    content = {
        "_meta": {"input_hash": "x"},
        "world_premise": "末法时代，诡道横行。",
        "power_system": {"name": "诡道体系", "tiers": ["扎纸", "走阴", "司天"],
                         "protagonist_starting_tier": "扎纸", "acquisition_method": "解谜拓印"},
        "rules": [
            {"rule_name": "诡道留门", "description": "留一线生机", "story_consequence": "被误解为软弱"},
            {"rule_name": "认知污染", "description": "接触遗物积累污染"},
        ],
        "factions": [{"name": "镇诡司", "description": "官方机构"}],
    }
    view = build_artifact_view("world_spec", content)
    assert view["has_spec"] is True
    assert view["title"] == "世界观"
    assert view["hint"]
    ladder = next(s for s in view["sections"] if s["kind"] == "ladder")
    assert [st["name"] for st in ladder["steps"]] == ["扎纸", "走阴", "司天"]
    assert ladder["steps"][0]["tag"] == "起点"
    rules = next(s for s in view["sections"] if s["label"] == "世界规则")
    assert rules["kind"] == "cards"
    assert rules["cards"][0]["title"] == "诡道留门"
    assert any(i["label"] == "剧情后果" for i in rules["cards"][0]["items"])


@pytest.mark.unit
def test_cast_spec_curates_protagonist_and_hides_noise() -> None:
    content = {
        "protagonist": {
            "name": "路无咎", "archetype": "破局游方者", "golden_finger": "扎纸通灵",
            "core_motivation": "护送阴镖", "fatal_flaw": "麻木旁观", "fear": "再害死人",
            # noise fields that must NOT surface as curated items:
            "pronoun_set_en": "he/him", "voice_profile": {"x": 1}, "social_network": [1, 2],
            "background": "很长的背景…", "psych_profile": {"y": 2},
        },
        "antagonist": {"name": "晏无休", "goal": "清除证据", "flaw": "秩序高于人"},
        "supporting_cast": [{"name": "陈枯", "role": "mentor", "goal": "找传人", "flaw": "记忆残缺"}],
    }
    view = build_artifact_view("cast_spec", content)
    prot = next(s for s in view["sections"] if s["label"] == "主角")
    card = prot["cards"][0]
    assert card["title"] == "路无咎"
    labels = [i["label"] for i in card["items"]]
    assert "金手指" in labels and "致命缺陷" in labels
    # curated set only — voice_profile / pronoun / background are NOT shown
    assert "background" not in str(card["items"])
    assert len(card["items"]) <= 9


@pytest.mark.unit
def test_book_spec_callout_and_fields() -> None:
    content = {
        "logline": "一句话钩子",
        "dramatic_question": "他能否在崩坏前留住退路？",
        "series_engine": {"core_loop": "解谜升级", "selling_points": ["卖点A", "卖点B"]},
        "protagonist": {"golden_finger": "扎纸", "core_wound": "妹妹纸化"},
    }
    view = build_artifact_view("book_spec", content)
    assert any(s["kind"] == "callout" and "戏剧问题" in s["label"] for s in view["sections"])
    assert any(s["kind"] == "chips" and s["label"] == "卖点" for s in view["sections"])


@pytest.mark.unit
def test_volume_plan_list_content_renders_cards() -> None:
    content = [
        {"volume_title": "第一卷", "volume_theme": "入局", "volume_goal": "接下阴镖", "volume_climax": "夜遇厉鬼"},
        {"volume_title": "第二卷", "volume_theme": "升维", "volume_goal": "集齐遗物"},
    ]
    view = build_artifact_view("volume_plan", content)
    cards = view["sections"][0]
    assert cards["kind"] == "cards"
    assert cards["cards"][0]["title"] == "第一卷"
    assert cards["cards"][0]["subtitle"] == "入局"


@pytest.mark.unit
def test_unknown_type_uses_curated_fallback() -> None:
    content = {"_meta": {"hash": "x"}, "some_field": "可见值",
               "items": [{"name": "条目一", "detail": "d"}]}
    view = build_artifact_view("mystery_artifact", content)
    assert view["has_spec"] is False
    assert view["sections"]  # not empty
    # noise hidden, scalar surfaced
    assert any(s["kind"] == "fields" for s in view["sections"])
    assert "_meta" not in str(view["sections"])


@pytest.mark.unit
def test_empty_content_is_stable_and_empty() -> None:
    a = build_artifact_view("world_spec", {})
    b = build_artifact_view("world_spec", {})
    assert a == b
    assert a["sections"] == []
    assert a["has_spec"] is True


@pytest.mark.unit
def test_premise_scalar_or_dict() -> None:
    assert build_artifact_view("premise", {"premise": "一句话"})["sections"][0]["text"] == "一句话"


@pytest.mark.unit
def test_story_appeal_renders_scorecards() -> None:
    content = {
        "overall_grade": "consider",
        "meets_bar": True,
        "blurb": {
            "grade": "recommend", "total": 83.3,
            "dimensions": [
                {"key": "selling_triad", "label": "卖点三要素", "score": 4.0, "weight": 16.0,
                 "evidence": {"missing": ["身份"]}, "rationale": "缺要素：身份"},
                {"key": "hook_strength", "label": "钩子强度", "score": 3.0, "weight": 15.0, "rationale": "首句9字"},
            ],
        },
        "premise": {"grade": "consider", "total": 72.0, "dimensions": [{"label": "概念强度", "score": 4.0, "weight": 16.0}],
                    "suggestions": ["强化冲突具象化"]},
    }
    view = build_artifact_view("story_appeal", content)
    assert view["has_spec"] is True
    sc = next(s for s in view["sections"] if s["kind"] == "scorecard" and s["label"] == "简介点击力")
    assert sc["grade"] == "推荐"
    assert sc["total"] == 83.3
    assert sc["rows"][0]["label"] == "卖点三要素"
    assert sc["rows"][0]["score"] == 4.0
    # evidence/key are noise — not surfaced as rows fields
    assert "evidence" not in str(sc["rows"])
    summary = next(s for s in view["sections"] if s["label"] == "综合")
    assert {"label": "是否达标", "value": "达标"} in summary["items"]
    assert any(s["kind"] == "chips" and s["label"] == "立意改进建议" for s in view["sections"])


@pytest.mark.unit
def test_hook_candidates_list_renders_cards() -> None:
    content = [
        {"spec": {"mechanism_key": "revenge_then_what", "hook_type": "information_gap",
                  "reversal": "复仇成功则人设崩", "one_liner": "原来主角不是来复仇成功的"},
         "score": {"verdict": "expand"}, "combined_rank": 0.8132},
    ]
    view = build_artifact_view("hook_candidates", content)
    card = view["sections"][0]["cards"][0]
    assert card["title"] == "revenge_then_what"
    assert card["badge"] == "expand"
    assert card["lines"] == ["原来主角不是来复仇成功的"]


@pytest.mark.unit
def test_commercial_brief_fields_and_chips() -> None:
    content = {"reader_promise": "解谜即升级", "selling_points": ["卖点A"],
               "taboo_words": ["邪教"], "taboo_topics": ["涉政"], "pacing_profile": "fast"}
    view = build_artifact_view("commercial_brief", content)
    assert any(s["kind"] == "callout" for s in view["sections"])
    taboo = next(s for s in view["sections"] if s["label"] == "敏感词 / 禁忌")
    assert set(taboo["chips"]) == {"邪教", "涉政"}
